#!/usr/bin/env python3
"""Check specific refs in mp_expenses view + payment_events table."""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db

db = get_db()
refs = ["145339424266", "148040507234", "144861257539", "145420947126"]

print("=== mp_expenses (view) ===")
for ref in refs:
    rows = db.table("mp_expenses").select("*").eq(
        "seller_slug", "141air"
    ).or_(f"payment_id.eq.{ref},payment_id.like.{ref}:*").execute().data or []
    print(f"{ref}: {len(rows)} rows")
    for r in rows:
        print(f"  date={r.get('date_approved')} type={r.get('expense_type')} "
              f"dir={r.get('expense_direction')} amt={r.get('amount')} "
              f"pid={r.get('payment_id')} ext={r.get('external_reference')}")

print("\n=== payment_events (raw) ===")
for ref in refs:
    rows = db.table("payment_events").select(
        "id, event_type, reference_id, ml_payment_id, signed_amount, competencia_date, event_date, metadata"
    ).eq("seller_slug", "141air").or_(
        f"ml_payment_id.eq.{ref},reference_id.eq.{ref},reference_id.like.{ref}:*"
    ).execute().data or []
    print(f"{ref}: {len(rows)} rows")
    for r in rows:
        md = r.get('metadata') or {}
        print(f"  type={r['event_type']:<22} ref={r['reference_id']:<24} "
              f"ml_pid={r['ml_payment_id']} amt={r['signed_amount']} "
              f"comp={r.get('competencia_date')} ev_date={r.get('event_date','')[:10]} "
              f"expense_type={md.get('expense_type')}")
