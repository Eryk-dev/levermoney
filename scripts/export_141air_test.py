"""
Test export for 141air — generates the ZIP locally without marking as exported.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import io
import zipfile
from datetime import datetime, timedelta, timezone
from collections import Counter

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.routers.expenses._deps import (
    _to_brt_date_str, _get_centro_custo_name, _sanitize_path_component,
    _signed_amount, _date_range_label,
    MP_CONTATO, MP_CNPJ, ML_CONTATO, ML_CNPJ,
)
from app.routers.expenses.export import _build_xlsx

BRT = timezone(timedelta(hours=-3))

def main():
    seller_slug = "141air"
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        print(f"Seller {seller_slug} not found")
        return

    # Fetch all non-exported/non-imported rows
    q = (
        db.table("mp_expenses")
        .select("*")
        .eq("seller_slug", seller_slug)
        .not_.in_("status", ["exported", "imported"])
        .order("date_created", desc=False)
    )
    result = q.execute()
    rows = result.data or []

    print(f"Total rows: {len(rows)}")

    # Stats
    directions = Counter(r.get("expense_direction") for r in rows)
    statuses = Counter(r.get("status") for r in rows)
    types = Counter(r.get("expense_type") for r in rows)
    total_amount = sum(float(r.get("amount") or 0) for r in rows)

    print(f"\nBy direction: {dict(directions)}")
    print(f"By status: {dict(statuses)}")
    print(f"By type: {dict(types)}")
    print(f"Total amount: R$ {total_amount:,.2f}")

    # Split
    payment_rows = [r for r in rows if r.get("expense_direction") in ("expense", "income")]
    transfer_rows = [r for r in rows if r.get("expense_direction") == "transfer"]

    print(f"\nPAGAMENTO_CONTAS rows: {len(payment_rows)}")
    print(f"TRANSFERENCIAS rows: {len(transfer_rows)}")

    # Build ZIP
    empresa_nome = seller.get("dashboard_empresa") or seller_slug
    empresa_base = _sanitize_path_component(empresa_nome.upper())
    range_label = _date_range_label(rows)
    empresa_dir = f"{empresa_base}_{range_label}" if range_label != "sem-data" else empresa_base

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
            zf.writestr(f"{empresa_dir}/README.txt", "Nenhuma linha.\n")

    zip_buf.seek(0)

    # Save to disk
    out_path = Path(__file__).resolve().parent / f"despesas_{empresa_dir}.zip"
    out_path.write_bytes(zip_buf.getvalue())
    print(f"\nZIP saved: {out_path}")
    print(f"  Folder: {empresa_dir}/")

    # List ZIP contents
    with zipfile.ZipFile(io.BytesIO(zip_buf.getvalue()), "r") as zf:
        print(f"\nZIP contents:")
        for info in zf.infolist():
            print(f"  {info.filename} ({info.file_size:,} bytes)")

    print(f"\nNO rows were marked as exported.")


if __name__ == "__main__":
    main()
