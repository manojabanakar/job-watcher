"""
Build sponsors.txt from the official USCIS H-1B Employer Data Hub.

Automatic (recommended): the GitHub workflow "Update Sponsors" downloads the
USCIS data into uscis_data/ and runs this script once a month. You can also
start it any time from the Actions tab.

Manual:
1. Download CSV(s) from https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
2. Put them in a folder named uscis_data/ next to this script.
3. Run:  python build_sponsors.py
         python build_sponsors.py --min-approvals 2   (stricter)

Handles both USCIS formats:
- Old "wide" files: one row per employer with columns like
  "Initial Approval" / "New Employment Approval".
- New "long" Tableau export: one row per employer per measure, with a
  measure-name column and a measure-value column.

Keeps employers with at least N approvals for NEW employment (first-time
H-1B, not just renewals), merges them with the names already in
sponsors.txt, and rewrites sponsors.txt.
"""

import argparse
import csv
import io
import sys
from pathlib import Path

from watcher import SPONSORS_FILE, normalize_company

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "uscis_data"


def to_int(value) -> int:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def find_column(headers, *needles):
    """First header containing all needle words (case-insensitive, spaces trimmed)."""
    for h in headers:
        low = " ".join(h.lower().split())
        if all(n in low for n in needles):
            return h
    return None


def read_csv_any_encoding(path: Path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        sample = text[:5000]
        delim = "\t" if sample.count("\t") > sample.count(",") else ","
        return list(csv.DictReader(io.StringIO(text), delimiter=delim))
    raise ValueError(f"Could not decode {path}")


def is_new_approval_measure(name: str) -> bool:
    low = " ".join((name or "").lower().split())
    return "approval" in low and ("initial" in low or "new employment" in low)


def parse_file(path: Path, totals: dict) -> bool:
    """Adds new-employment approvals per employer into totals. Returns True if parsed."""
    rows = read_csv_any_encoding(path)
    if not rows:
        print(f"{path.name}: empty")
        return False
    headers = list(rows[0].keys())
    print(f"{path.name}: {len(rows)} rows")
    print(f"  headers: {headers}")
    for sample in rows[:2]:
        print(f"  sample: {dict(list(sample.items())[:8])}")

    name_col = (find_column(headers, "employer")
                or find_column(headers, "petitioner", "name"))
    if not name_col:
        print("  SKIPPED: no employer column found")
        return False

    # Format 1: wide (one approval column)
    wide_col = (find_column(headers, "initial", "approval")
                or find_column(headers, "new employment", "approval"))
    if wide_col:
        print(f"  format: wide (name='{name_col}', approvals='{wide_col}')")
        for row in rows:
            norm = normalize_company(row.get(name_col))
            if norm:
                totals[norm] = totals.get(norm, 0) + to_int(row.get(wide_col))
        return True

    # Format 2: long (measure name + measure value)
    measure_col = (find_column(headers, "measure names")
                   or find_column(headers, "measure", "name")
                   or find_column(headers, "measure"))
    value_col = (find_column(headers, "measure values")
                 or find_column(headers, "value"))
    if measure_col and value_col and measure_col != value_col:
        measures = sorted({(r.get(measure_col) or "").strip() for r in rows})
        print(f"  format: long (name='{name_col}', measure='{measure_col}', value='{value_col}')")
        print(f"  measures found: {measures[:20]}")
        used = 0
        for row in rows:
            if not is_new_approval_measure(row.get(measure_col)):
                continue
            norm = normalize_company(row.get(name_col))
            if norm:
                totals[norm] = totals.get(norm, 0) + to_int(row.get(value_col))
                used += 1
        if used:
            return True
        print("  no 'initial/new employment approval' measure; counting any approval measure")
        for row in rows:
            if "approval" not in (row.get(measure_col) or "").lower():
                continue
            norm = normalize_company(row.get(name_col))
            if norm:
                totals[norm] = totals.get(norm, 0) + to_int(row.get(value_col))
        return True

    # Format 3: unknown layout, sum every column that mentions approval
    approval_cols = [h for h in headers if "approval" in h.lower()]
    if approval_cols:
        print(f"  format: other (summing {approval_cols})")
        for row in rows:
            norm = normalize_company(row.get(name_col))
            if norm:
                totals[norm] = totals.get(norm, 0) + sum(to_int(row.get(c)) for c in approval_cols)
        return True

    print("  SKIPPED: could not find approval numbers")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-approvals", type=int, default=1,
                        help="minimum new-employment approvals to count as a sponsor")
    args = parser.parse_args()

    files = sorted(DATA_DIR.glob("*.csv"))
    if not files:
        print(f"No CSV files found in {DATA_DIR}. See instructions at top of this file.")
        return 1

    totals = {}
    parsed = 0
    for path in files:
        try:
            if parse_file(path, totals):
                parsed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"{path.name}: FAILED ({type(exc).__name__}: {exc})")

    if not parsed:
        print("No files could be parsed. sponsors.txt left unchanged.")
        return 1

    keep = {n for n, total in totals.items() if total >= args.min_approvals}
    print(f"Employers with >= {args.min_approvals} new-employment approvals: {len(keep)}")

    existing = []
    if SPONSORS_FILE.exists():
        for line in SPONSORS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                existing.append(line)
    merged = {normalize_company(x) for x in existing} | keep
    merged.discard("")

    header = (
        "# H-1B sponsor list used by watcher.py (one company per line).\n"
        "# Built from the USCIS H-1B Employer Data Hub plus a starter list.\n"
        "# Lines starting with # are ignored. Add companies by hand anytime.\n"
    )
    SPONSORS_FILE.write_text(header + "\n".join(sorted(merged)) + "\n", encoding="utf-8")
    print(f"Wrote {len(merged)} sponsors to {SPONSORS_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
