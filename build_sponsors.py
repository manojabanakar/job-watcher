"""
Build sponsors.txt from the official USCIS H-1B Employer Data Hub export.

1. Go to https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
2. Download the data file(s) for the most recent 2-3 fiscal years (CSV).
3. Put the CSV file(s) in a folder named uscis_data/ next to this script.
4. Run:  python build_sponsors.py
         python build_sponsors.py --min-approvals 2   (stricter)

The script keeps employers with at least N approvals for NEW employment
(first-time H-1B, not just renewals), merges them with the starter list
already in sponsors.txt, and rewrites sponsors.txt.
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
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0


def find_column(headers, *needles):
    """First header containing all needle words (case-insensitive)."""
    for h in headers:
        low = h.lower()
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
    display = {}
    for path in files:
        rows = read_csv_any_encoding(path)
        if not rows:
            continue
        headers = list(rows[0].keys())
        name_col = find_column(headers, "employer") or find_column(headers, "petitioner", "name")
        # Older files: "Initial Approval"; newer files: "New Employment Approval"
        new_col = (find_column(headers, "initial", "approval")
                   or find_column(headers, "new employment", "approval"))
        if not name_col or not new_col:
            print(f"Skipping {path.name}: columns not recognized. Headers: {headers}")
            continue
        print(f"{path.name}: {len(rows)} rows (name='{name_col}', approvals='{new_col}')")
        for row in rows:
            name = (row.get(name_col) or "").strip()
            norm = normalize_company(name)
            if not norm:
                continue
            totals[norm] = totals.get(norm, 0) + to_int(row.get(new_col))
            display.setdefault(norm, name)

    keep = {n for n, total in totals.items() if total >= args.min_approvals}
    print(f"Employers with >= {args.min_approvals} new-employment approvals: {len(keep)}")

    # Keep the hand-written starter list too
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
