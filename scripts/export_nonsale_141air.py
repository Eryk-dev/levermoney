"""
Export non-sale expenses for 141air in the exact same format as the platform,
WITHOUT marking rows as exported.

Usage: python scripts/export_nonsale_141air.py
"""
import io
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from supabase import create_client
from openpyxl import Workbook

# ── Constants (same as _deps.py) ─────────────────────────────
BRT = timezone(timedelta(hours=-3))
MP_CONTATO = "MERCADO PAGO"
MP_CNPJ = "10573521000191"
ML_CONTATO = "MERCADO LIVRE"
ML_CNPJ = "03007331000141"
SELLER_SLUG = "141air"

# ── Helpers (copied from _deps.py) ───────────────────────────

def _to_brt_date_str(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone(BRT).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso_str[:10] if iso_str else ""

def _sanitize_path_component(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    return cleaned.strip("._") or "SEM_NOME"

def _signed_amount(row):
    amount = float(row.get("amount") or 0)
    direction = row.get("expense_direction", "expense")
    if direction == "income":
        return abs(amount)
    return -abs(amount)

def _get_centro_custo_name(seller):
    return (seller.get("dashboard_empresa") or seller.get("slug") or "").upper()

def _date_range_label(rows, date_from=None, date_to=None):
    if date_from and date_to:
        d1 = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d.%m.%Y")
        d2 = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d.%m.%Y")
        return f"{d1}_{d2}"
    dates = []
    for r in rows:
        iso = r.get("date_approved") or r.get("date_created")
        if iso:
            try:
                dates.append(datetime.fromisoformat(iso).astimezone(BRT))
            except (ValueError, TypeError):
                pass
    if not dates:
        return "sem-data"
    d1 = min(dates).strftime("%d.%m.%Y")
    d2 = max(dates).strftime("%d.%m.%Y")
    return f"{d1}_{d2}"


# ── XLSX builder (same as export.py) ─────────────────────────

def _build_xlsx(rows, seller, sheet_name):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    headers = [
        "Data de Competencia",
        "Data de Vencimento",
        "Data de Pagamento",
        "Valor",
        "Categoria",
        "Descricao",
        "Cliente/Fornecedor",
        "CNPJ/CPF Cliente/Fornecedor",
        "Centro de Custo",
        "Observacoes",
    ]
    ws.append(headers)

    centro_custo = _get_centro_custo_name(seller)

    for r in rows:
        date_str = _to_brt_date_str(r.get("date_approved") or r.get("date_created"))
        direction = r.get("expense_direction", "expense")
        amount = float(r.get("amount") or 0)

        if direction == "income":
            valor = abs(amount)
            contato = ML_CONTATO
            cnpj = ML_CNPJ
        else:
            valor = -abs(amount)
            contato = MP_CONTATO
            cnpj = MP_CNPJ

        obs_parts = []
        if r.get("payment_id"):
            obs_parts.append(f"Payment {r['payment_id']}")
        if r.get("external_reference"):
            obs_parts.append(f"Ref: {r['external_reference'][:40]}")
        if r.get("notes"):
            obs_parts.append(r["notes"])
        if r.get("auto_categorized"):
            obs_parts.append("(auto)")
        observacoes = " | ".join(obs_parts)

        ws.append([
            date_str,
            date_str,
            date_str,
            valor,
            r.get("ca_category") or "",
            r.get("description") or "",
            contato,
            cnpj,
            centro_custo,
            observacoes,
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ── Main ──────────────────────────────────────────────────────

def main():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    db = create_client(url, key)

    # Get seller config
    seller_result = db.table("sellers").select("*").eq("slug", SELLER_SLUG).single().execute()
    seller = seller_result.data
    if not seller:
        print(f"ERROR: Seller {SELLER_SLUG} not found")
        sys.exit(1)

    print(f"Seller: {seller.get('dashboard_empresa', SELLER_SLUG)}")

    # Query non-exported expenses (same default filter as platform)
    q = db.table("mp_expenses").select("*").eq("seller_slug", SELLER_SLUG)
    q = q.not_.in_("status", ["exported", "imported"])
    q = q.order("date_created", desc=False)
    result = q.execute()
    rows = result.data or []

    print(f"Found {len(rows)} non-exported expenses")
    if not rows:
        print("Nothing to export.")
        return

    # Stats
    by_direction = {}
    by_status = {}
    total_amount = 0.0
    for r in rows:
        d = r.get("expense_direction", "?")
        by_direction[d] = by_direction.get(d, 0) + 1
        s = r.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
        total_amount += _signed_amount(r)

    print(f"\nBy direction: {by_direction}")
    print(f"By status: {by_status}")
    print(f"Total signed amount: R$ {total_amount:,.2f}")

    # Build flat ZIP (new structure)
    empresa_nome = seller.get("dashboard_empresa") or SELLER_SLUG
    empresa_base = _sanitize_path_component(empresa_nome.upper())
    range_label = _date_range_label(rows)
    empresa_dir = f"{empresa_base}_{range_label}" if range_label != "sem-data" else empresa_base

    payment_rows = [r for r in rows if r.get("expense_direction") in ("expense", "income")]
    transfer_rows = [r for r in rows if r.get("expense_direction") == "transfer"]

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if payment_rows:
            zf.writestr(
                f"{empresa_dir}/PAGAMENTO_CONTAS.xlsx",
                _build_xlsx(payment_rows, seller, "PAGAMENTO_CONTAS").getvalue(),
            )
        if transfer_rows:
            zf.writestr(
                f"{empresa_dir}/TRANSFERENCIAS.xlsx",
                _build_xlsx(transfer_rows, seller, "TRANSFERENCIAS").getvalue(),
            )
        if not payment_rows and not transfer_rows:
            zf.writestr(f"{empresa_dir}/README.txt", "Nenhuma linha encontrada.\n")

    zip_buf.seek(0)

    # Save to disk
    output_dir = Path(__file__).resolve().parent.parent / "exports"
    output_dir.mkdir(exist_ok=True)
    filename = f"despesas_{empresa_dir}.zip"
    output_path = output_dir / filename

    with open(output_path, "wb") as f:
        f.write(zip_buf.getvalue())

    # List ZIP contents
    zip_buf.seek(0)
    with zipfile.ZipFile(zip_buf, "r") as zf:
        print(f"\nZIP contents:")
        for name in zf.namelist():
            print(f"  {name}")

    print(f"\n{'='*60}")
    print(f"ZIP saved to: {output_path}")
    print(f"Folder: {empresa_dir}/")
    print(f"  PAGAMENTO_CONTAS.xlsx: {len(payment_rows)} rows")
    print(f"  TRANSFERENCIAS.xlsx: {len(transfer_rows)} rows")
    print(f"Total rows: {len(rows)}")
    print(f"\nNOTE: No rows were marked as exported in the database.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
