"""
Bridge to reuse legacy report processing inside API V2.

Goal:
- Keep V2 for sales sync/dashboard.
- Reuse legacy CSV reconciliation logic for MP movements when needed.
"""
import io
import importlib.util
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import UploadFile

logger = logging.getLogger(__name__)

_LEGACY_MODULE: Any | None = None


def _legacy_api_path() -> Path:
    # Prefer an internalized legacy engine file bundled in API V2.
    local_path = Path(__file__).resolve().parent / "legacy_engine.py"
    if local_path.is_file():
        return local_path

    # Backward-compatible fallback for old workspace layout.
    root = Path(__file__).resolve().parents[3]
    return root / "legado.py" / "apiconciliador" / "api.py"


def _load_legacy_module() -> Any:
    global _LEGACY_MODULE
    if _LEGACY_MODULE is not None:
        return _LEGACY_MODULE

    legacy_path = _legacy_api_path()
    if not legacy_path.is_file():
        raise FileNotFoundError(f"Legacy API file not found: {legacy_path}")

    spec = importlib.util.spec_from_file_location("legacy_conciliador_api", legacy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load legacy module spec from {legacy_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _LEGACY_MODULE = module
    logger.info("Legacy module loaded from %s", legacy_path)
    return module


def _detect_sep(header_line: str) -> str:
    return ";" if header_line.count(";") > header_line.count(",") else ","


async def _read_csv(upload_file: UploadFile | None, key: str, clean_json: bool = False) -> pd.DataFrame:
    if upload_file is None:
        return pd.DataFrame()

    content = await upload_file.read()
    if not content:
        return pd.DataFrame()

    legacy = _load_legacy_module()
    if legacy.is_zip_file(content):
        logger.info("legacy-bridge: %s detected as ZIP", key)
        return legacy.extrair_csvs_do_zip(content, skip_rows=0, clean_json=clean_json)

    content_str = content.decode("utf-8")
    if clean_json:
        content_str = re.sub(r'"\{[^}]*(?:\{[^}]*\}[^}]*)*\}"', '""', content_str)

    lines = content_str.split("\n")
    header_line = lines[0] if lines else ""
    sep = _detect_sep(header_line)
    return pd.read_csv(
        io.StringIO(content_str),
        sep=sep,
        on_bad_lines="skip",
        index_col=False,
    )


async def _read_extrato(upload_file: UploadFile) -> tuple[pd.DataFrame, float]:
    content = await upload_file.read()
    if not content:
        raise ValueError("Arquivo 'extrato' vazio")

    legacy = _load_legacy_module()
    if legacy.is_zip_file(content):
        logger.info("legacy-bridge: extrato detected as ZIP")
        return legacy.extrair_csvs_do_zip(content, skip_rows=3, clean_json=False), 0.0

    content_str = content.decode("utf-8")
    lines = content_str.split("\n")
    if len(lines) < 4:
        raise ValueError("Arquivo de extrato invalido - menos de 4 linhas")

    saldo_inicial = 0.0
    try:
        valores_resumo = lines[1].strip().split(";")
        if valores_resumo:
            saldo_str = valores_resumo[0].replace(".", "").replace(",", ".")
            saldo_inicial = float(saldo_str)
    except (ValueError, IndexError):
        logger.warning("legacy-bridge: could not parse extrato initial balance")

    header = lines[3].strip().split(";")
    expected_cols = 5
    data_rows = []

    for line in lines[4:]:
        line = line.strip()
        if not line:
            continue

        campos = line.split(";")
        if len(campos) == expected_cols:
            data_rows.append(campos)
            continue

        if len(campos) > expected_cols:
            extra_count = len(campos) - expected_cols
            transaction_type_parts = campos[1:2 + extra_count]
            transaction_type = " ".join(transaction_type_parts)

            fixed_row = [
                campos[0],
                transaction_type,
                campos[-3],
                campos[-2],
                campos[-1],
            ]
            data_rows.append(fixed_row)

    df = pd.DataFrame(data_rows, columns=header[:expected_cols])
    return df, saldo_inicial


async def run_legacy_reconciliation(
    *,
    extrato: UploadFile,
    dinheiro: UploadFile | None = None,
    vendas: UploadFile | None = None,
    pos_venda: UploadFile | None = None,
    liberacoes: UploadFile | None = None,
    centro_custo: str = "NETAIR",
) -> dict:
    """
    Execute legacy processar_conciliacao() from API V2.
    Optional reports can be omitted; the legacy processor will fallback.
    """
    if extrato is None:
        raise ValueError("Arquivo 'extrato' e obrigatorio")

    legacy = _load_legacy_module()

    arquivos = {
        "dinheiro": await _read_csv(dinheiro, "dinheiro", clean_json=True),
        "vendas": await _read_csv(vendas, "vendas"),
        "pos_venda": await _read_csv(pos_venda, "pos_venda"),
        "liberacoes": await _read_csv(liberacoes, "liberacoes", clean_json=True),
        "retirada": pd.DataFrame(),
    }
    arquivos["extrato"], saldo_inicial_extrato = await _read_extrato(extrato)

    resultado = legacy.processar_conciliacao(arquivos, centro_custo=centro_custo)
    resultado["saldo_inicial_extrato"] = saldo_inicial_extrato
    return resultado


def build_legacy_expenses_zip(resultado: dict) -> tuple[io.BytesIO, dict]:
    """
    Build a ZIP with the legacy expense outputs (pagamentos/transferencias).
    """
    legacy = _load_legacy_module()

    pagamentos = resultado.get("pagamentos") or []
    transferencias = resultado.get("transferencias") or []

    with tempfile.TemporaryDirectory() as temp_dir:
        generated: dict[str, str] = {}

        pag_xlsx = Path(temp_dir) / "PAGAMENTO_CONTAS.xlsx"
        if legacy.gerar_xlsx_completo(pagamentos, str(pag_xlsx)):
            generated["Conta Azul/PAGAMENTO_CONTAS.xlsx"] = str(pag_xlsx)

        pag_resumo = Path(temp_dir) / "PAGAMENTO_CONTAS_RESUMO.xlsx"
        if legacy.gerar_xlsx_resumo(pagamentos, str(pag_resumo)):
            generated["Resumo/PAGAMENTO_CONTAS_RESUMO.xlsx"] = str(pag_resumo)

        pag_csv = Path(temp_dir) / "PAGAMENTO_CONTAS.csv"
        if legacy.gerar_csv_conta_azul(pagamentos, str(pag_csv)):
            generated["Outros/PAGAMENTO_CONTAS.csv"] = str(pag_csv)

        trf_xlsx = Path(temp_dir) / "TRANSFERENCIAS.xlsx"
        if legacy.gerar_xlsx_completo(transferencias, str(trf_xlsx)):
            generated["Conta Azul/TRANSFERENCIAS.xlsx"] = str(trf_xlsx)

        trf_resumo = Path(temp_dir) / "TRANSFERENCIAS_RESUMO.xlsx"
        if legacy.gerar_xlsx_resumo(transferencias, str(trf_resumo)):
            generated["Resumo/TRANSFERENCIAS_RESUMO.xlsx"] = str(trf_resumo)

        trf_csv = Path(temp_dir) / "TRANSFERENCIAS.csv"
        if legacy.gerar_csv_conta_azul(transferencias, str(trf_csv)):
            generated["Outros/TRANSFERENCIAS.csv"] = str(trf_csv)

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if generated:
                for zip_path, local_path in generated.items():
                    zf.write(local_path, zip_path)
            else:
                zf.writestr(
                    "README.txt",
                    "Nenhum lancamento de PAGAMENTO_CONTAS/TRANSFERENCIAS encontrado no periodo.\n",
                )
        zip_buf.seek(0)

    summary = {
        "pagamentos_rows": len(pagamentos),
        "transferencias_rows": len(transferencias),
        "files": sorted(generated.keys()) if generated else ["README.txt"],
    }
    return zip_buf, summary
