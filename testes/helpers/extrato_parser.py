"""T001 - Extrato CSV parser for ML/MP account statement files.

CSV format:
  Line 1: INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE  (summary header)
  Line 2: <values in BR number format>                  (summary values)
  Line 3: <blank>
  Line 4: RELEASE_DATE;TRANSACTION_TYPE;...             (data header)
  Line 5+: data rows
"""
from __future__ import annotations

from pathlib import Path


def parse_br_number(s: str) -> float:
    """Convert Brazilian number string to float.

    Examples:
        "1.234,56" -> 1234.56
        "-350,00"  -> -350.0
        "0"        -> 0.0
    """
    s = s.strip()
    # Remove thousands separator (dot), replace decimal separator (comma) with period
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def parse_extrato_csv(filepath: str | Path) -> list[dict]:
    """Parse an ML/MP account statement CSV into a list of transaction dicts.

    Skips the first 2 summary lines and any blank lines before the header row
    (the row that starts with "RELEASE_DATE").

    Args:
        filepath: Absolute or relative path to the CSV file.

    Returns:
        List of dicts, one per data row, with keys:
            - date (str): transaction date as DD-MM-YYYY
            - transaction_type (str): human-readable description
            - reference_id (str): ML reference ID (may be empty)
            - amount (float): net transaction amount (negative = debit)
            - balance (float): running partial balance after this line
    """
    filepath = Path(filepath)
    rows: list[dict] = []

    with filepath.open(encoding="utf-8-sig", newline="") as fh:
        lines = fh.readlines()

    header_found = False
    for line in lines:
        stripped = line.rstrip("\r\n")

        if not header_found:
            if stripped.startswith("RELEASE_DATE"):
                header_found = True
            continue

        # Skip blank lines after header
        if not stripped:
            continue

        parts = stripped.split(";")
        if len(parts) < 5:
            continue

        rows.append(
            {
                "date": parts[0].strip(),
                "transaction_type": parts[1].strip(),
                "reference_id": parts[2].strip(),
                "amount": parse_br_number(parts[3]),
                "balance": parse_br_number(parts[4]),
            }
        )

    return rows
