"""
Job Watcher
-----------
Checks Indeed, LinkedIn and Google Jobs for newly posted Data Analyst /
Business Analyst roles in the USA, filters for 0-3 years of experience and
visa sponsorship, and sends a push notification to your phone via ntfy.

Runs every 15 minutes on GitHub Actions (see .github/workflows/watch.yml).

Local usage:
    python watcher.py                 # normal run
    python watcher.py --dry-run       # print matches, send nothing, save nothing
    python watcher.py --test-notify   # send one test push to your phone
"""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# =============================================================================
# SETTINGS (edit these)
# =============================================================================

SEARCH_TERMS = [
    "data analyst",
    "business analyst",
    "business intelligence analyst",
]

LOCATION = "United States"

# Sites to check. Each site runs separately; if one fails or is blocked,
# the error is logged and the others still run.
# Note: zip_recruiter and glassdoor round "hours old" up to 1 day, so a job
# first seen there may be up to ~24 hours old.
SITES = ["indeed", "linkedin", "google", "zip_recruiter", "glassdoor"]

# Only look at jobs posted within this many hours. Runs every 15 min, so 2
# hours gives overlap if a run is late. Already-alerted jobs are skipped.
HOURS_OLD = 2

# How many results to pull per site per search term.
RESULTS_PER_SEARCH = 50

# Fetch full LinkedIn descriptions (needed for sponsorship/experience checks).
# Slower and more likely to be rate limited. Set False if LinkedIn keeps failing.
FETCH_LINKEDIN_DESCRIPTION = True

# Title must match at least one of these (case-insensitive regex).
TITLE_INCLUDE = [
    r"\bdata analyst\b",
    r"\bbusiness analyst\b",
    r"\bbusiness intelligence analyst\b",
    r"\bbi analyst\b",
    r"\banalytics analyst\b",
    r"\breporting analyst\b",
    r"\binsights analyst\b",
    r"\bdata\s*(and|&)\s*analytics analyst\b",
    r"\bproduct analyst\b",
    r"\boperations analyst\b",
]

# Title must NOT match any of these. "Senior" is allowed on purpose; the
# experience check below decides if a Senior role is within reach.
TITLE_EXCLUDE = [
    r"\bprincipal\b",
    r"\bstaff\b",
    r"\blead\b",
    r"\bmanager\b",
    r"\bdirector\b",
    r"\bhead of\b",
    r"\bvice president\b",
    r"\bvp\b",
    r"\bchief\b",
    r"\barchitect\b",
    r"\bintern(ship)?\b",
    r"\bIII\b",
    r"\bIV\b",
    r"\bclearance\b",
    r"\bTS/SCI\b",
]

# Company names matching these are dropped. Empty by default: companies with
# "Federal" or "Defense" in the name (e.g. Navy Federal Credit Union) can
# still sponsor. Roles that need citizenship or clearance are already dropped
# by NO_SPONSORSHIP_PATTERNS and TITLE_EXCLUDE. Add regexes here to skip
# specific companies, e.g. r"\bacme federal\b".
COMPANY_EXCLUDE = []

# Skip jobs whose lowest stated experience requirement is above this.
MAX_YEARS_EXPERIENCE = 3

# Description phrases that mean "no sponsorship" or "not for you". Job is dropped.
NO_SPONSORSHIP_PATTERNS = [
    r"no (visa )?sponsorship",
    r"not (be )?(able|eligible|in a position) to (provide |offer )?sponsor",
    r"(will|can|does|do) not (provide |offer )?(visa )?sponsor",
    r"unable to (provide |offer )?(visa )?sponsor",
    r"sponsorship (is )?not (available|provided|offered)",
    r"without (the need for )?(current or future |now or in the future )?(visa |employer )?sponsorship",
    r"not (currently )?(offering|providing) (visa )?sponsorship",
    r"must be (a )?u\.?s\.? citizen",
    r"u\.?s\.? citizenship (is )?required",
    r"only u\.?s\.? citizens",
    r"green card holders? only",
    r"(active|current|secret|top secret|ts/sci|public trust) (security )?clearance",
    r"security clearance (is )?required",
    r"ability to obtain (a )?(security )?clearance",
]

# Description phrases that mean sponsorship is offered. Job is tagged HIGH.
SPONSORSHIP_POSITIVE_PATTERNS = [
    r"visa sponsorship (is )?available",
    r"sponsorship (is )?available",
    r"(will|can|able to) sponsor",
    r"h-?1b (visa )?(sponsorship|transfer)",
    r"open to sponsor",
    r"\bstem opt\b",
    r"\bf-?1 (opt|visa)\b",
    r"\bopt (candidates|students|eligible)\b",
]

# If True, also alert on jobs from companies NOT in sponsors.txt when the
# posting says nothing about sponsorship (tagged "Unknown"). More alerts,
# more noise. Recommended: False after you build the full sponsors.txt.
ALERT_UNKNOWN_SPONSOR = False

# Push notification limits per run (avoids a flood on busy runs).
MAX_ALERTS_PER_RUN = 10

# Forget alerted jobs after this many days (keeps seen.json small).
SEEN_RETENTION_DAYS = 21

# =============================================================================
# FILES
# =============================================================================

ROOT = Path(__file__).resolve().parent
SEEN_FILE = ROOT / "seen.json"
SPONSORS_FILE = ROOT / "sponsors.txt"

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

# =============================================================================
# HELPERS
# =============================================================================

COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp",
    "corporation", "co", "company", "plc", "group", "holdings", "holding",
    "na", "usa", "us", "the", "pc", "pllc", "sa", "ag", "gmbh",
}


def normalize_company(name: str) -> str:
    """'Capital One, N.A.' -> 'capital one'; 'Amazon.com Services LLC' -> 'amazon com services'."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", "", text)  # 'n.a.' -> 'na', 'u.s.' -> 'us'
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = text.split()
    # Strip legal suffixes only from the END ('U.S. Bank' keeps 'us'),
    # and a leading 'the'.
    while words and (words[-1] in COMPANY_SUFFIXES or words[-1] == "and"):
        words.pop()
    if words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def load_sponsors(path: Path = SPONSORS_FILE) -> set:
    sponsors = set()
    if not path.exists():
        return sponsors
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        norm = normalize_company(line)
        if norm:
            sponsors.add(norm)
    return sponsors


def is_known_sponsor(company: str, sponsors: set) -> bool:
    """Exact match, or sponsor name matches the start of the company name on
    a word boundary ('amazon' matches 'amazon web services', not 'amazonia')."""
    norm = normalize_company(company)
    if not norm:
        return False
    if norm in sponsors:
        return True
    words = norm.split()
    for i in range(len(words) - 1, 0, -1):
        if " ".join(words[:i]) in sponsors:
            return True
    return False


def any_match(patterns, text: str) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*)?years?",
    re.IGNORECASE,
)


def min_years_required(description: str):
    """Lowest 'X years' mentioned near the word 'experience'. None if not stated."""
    if not description:
        return None
    found = []
    for m in YEARS_RE.finditer(description):
        window = description[max(0, m.start() - 80): m.end() + 80].lower()
        if "experience" not in window and "exp." not in window:
            continue
        low = int(m.group(1))
        if 0 <= low <= 20:
            found.append(low)
    return min(found) if found else None


def title_ok(title: str) -> bool:
    if not title:
        return False
    return any_match(TITLE_INCLUDE, title) and not any_match(TITLE_EXCLUDE, title)


def sponsorship_tag(company: str, description: str, sponsors: set):
    """Returns 'HIGH', 'KNOWN', 'UNKNOWN', or None (drop: says no sponsorship)."""
    text = description or ""
    if any_match(NO_SPONSORSHIP_PATTERNS, text):
        return None
    if any_match(SPONSORSHIP_POSITIVE_PATTERNS, text):
        return "HIGH"
    if is_known_sponsor(company, sponsors):
        return "KNOWN"
    return "UNKNOWN"


def job_key(company: str, title: str, location: str) -> str:
    """Same job on Indeed and LinkedIn should only alert once."""
    city = (location or "").split(",")[0].strip().lower()
    raw = f"{normalize_company(company)}|{(title or '').strip().lower()}|{city}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def clean(value) -> str:
    """Pandas NaN / None -> ''."""
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except Exception:
        pass
    return str(value).strip()


def header_safe(text: str) -> str:
    """HTTP headers must be ASCII."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()[:250]


# =============================================================================
# STATE
# =============================================================================

def load_seen() -> dict:
    if not SEEN_FILE.exists():
        return {}
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("WARNING: seen.json unreadable, starting fresh")
        return {}


def save_seen(seen: dict) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)
    kept = {}
    for key, stamp in seen.items():
        try:
            if datetime.fromisoformat(stamp) >= cutoff:
                kept[key] = stamp
        except ValueError:
            continue
    SEEN_FILE.write_text(json.dumps(kept, indent=0, sort_keys=True), encoding="utf-8")


# =============================================================================
# SCRAPING
# =============================================================================

def scrape_all():
    """Returns list of job dicts. Each site runs separately so one failure
    does not stop the others."""
    from jobspy import scrape_jobs  # imported here so tests run without it

    jobs = []
    for term in SEARCH_TERMS:
        for site in SITES:
            kwargs = dict(
                site_name=[site],
                search_term=term,
                location=LOCATION,
                results_wanted=RESULTS_PER_SEARCH,
                hours_old=HOURS_OLD,
                country_indeed="USA",
                verbose=0,
            )
            if site == "google":
                kwargs["google_search_term"] = f"{term} jobs near United States since yesterday"
            if site == "linkedin" and FETCH_LINKEDIN_DESCRIPTION:
                kwargs["fetch_description"] = True
            try:
                df = scrape_jobs(**kwargs)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{site}] '{term}': FAILED ({type(exc).__name__}: {exc})")
                continue
            errors = df.attrs.get("errors") or {}
            if errors:
                print(f"  [{site}] '{term}': error {errors}")
            print(f"  [{site}] '{term}': {len(df)} results")
            for row in df.to_dict(orient="records"):
                jobs.append({
                    "site": clean(row.get("site")) or site,
                    "title": clean(row.get("title")),
                    "company": clean(row.get("company")),
                    "location": clean(row.get("location")),
                    "url": clean(row.get("job_url_direct")) or clean(row.get("job_url")),
                    "board_url": clean(row.get("job_url")),
                    "description": clean(row.get("description")),
                    "date_posted": clean(row.get("date_posted")),
                    "is_remote": bool(row.get("is_remote")) if clean(row.get("is_remote")) else False,
                })
    return jobs


# =============================================================================
# FILTERING
# =============================================================================

def evaluate(job: dict, sponsors: set):
    """Returns (keep: bool, reason: str, tag: str|None, years: int|None)."""
    if not title_ok(job["title"]):
        return False, "title", None, None
    if not job["company"]:
        return False, "no company", None, None
    if any_match(COMPANY_EXCLUDE, job["company"]):
        return False, "company excluded", None, None
    years = min_years_required(job["description"])
    if years is not None and years > MAX_YEARS_EXPERIENCE:
        return False, f"{years}+ yrs", None, years
    tag = sponsorship_tag(job["company"], job["description"], sponsors)
    if tag is None:
        return False, "no sponsorship / clearance", None, years
    if tag == "UNKNOWN" and not ALERT_UNKNOWN_SPONSOR:
        return False, "sponsor unknown", tag, years
    return True, "match", tag, years


# =============================================================================
# NOTIFY
# =============================================================================

TAG_LABEL = {
    "HIGH": "Posting mentions sponsorship",
    "KNOWN": "Known H-1B sponsor",
    "UNKNOWN": "Sponsorship unknown",
}


def send_push(title: str, message: str, click_url: str = "", priority: str = "default",
              tags: str = "briefcase") -> bool:
    if not NTFY_TOPIC:
        print("  (NTFY_TOPIC not set, skipping push)")
        return False
    headers = {"Title": header_safe(title), "Priority": priority, "Tags": tags}
    if click_url:
        headers["Click"] = header_safe(click_url)
    try:
        resp = requests.post(f"{NTFY_SERVER}/{NTFY_TOPIC}",
                             data=message.encode("utf-8"), headers=headers, timeout=20)
        if resp.status_code >= 300:
            print(f"  push failed: HTTP {resp.status_code} {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as exc:
        print(f"  push failed: {exc}")
        return False


def notify_job(job: dict, tag: str, years) -> bool:
    exp = f"{years}+ yrs exp" if years is not None else "exp not stated"
    remote = " | Remote" if job.get("is_remote") else ""
    message = (
        f"{job['company']}\n"
        f"{job['location'] or 'Location not listed'}{remote}\n"
        f"{TAG_LABEL.get(tag, tag)} | {exp}\n"
        f"via {job['site']}"
    )
    priority = "high" if tag == "HIGH" else "default"
    return send_push(job["title"], message, job["url"], priority=priority)


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print matches only")
    parser.add_argument("--test-notify", action="store_true", help="send one test push")
    args = parser.parse_args()

    if args.test_notify:
        ok = send_push("Job Watcher test", "If you see this, alerts are working.",
                       priority="high", tags="white_check_mark")
        print("Test push sent." if ok else "Test push FAILED.")
        return 0 if ok else 1

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sponsors = load_sponsors()
    seen = load_seen()
    first_run = len(seen) == 0
    print(f"Run at {now} | sponsors loaded: {len(sponsors)} | seen: {len(seen)}")

    jobs = scrape_all()
    print(f"Scraped {len(jobs)} postings")

    reasons = {}
    matches = []
    keys_this_run = set()
    for job in jobs:
        key = job_key(job["company"], job["title"], job["location"])
        if key in seen or key in keys_this_run:
            continue
        keep, reason, tag, years = evaluate(job, sponsors)
        reasons[reason] = reasons.get(reason, 0) + 1
        if keep:
            keys_this_run.add(key)
            matches.append((key, job, tag, years))

    print("Filter results: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    print(f"New matches: {len(matches)}")
    # HIGH first, then KNOWN, then UNKNOWN
    order = {"HIGH": 0, "KNOWN": 1, "UNKNOWN": 2}
    matches.sort(key=lambda m: order.get(m[2], 9))
    for _, job, tag, years in matches:
        print(f"  [{tag}] {job['title']} | {job['company']} | {job['location']} | "
              f"yrs={years} | {job['url']}")

    if args.dry_run:
        print("Dry run: nothing sent, nothing saved.")
        return 0

    if first_run:
        # Do not flood the phone on the very first run: mark current jobs as
        # seen and send one "started" message.
        for key, *_ in matches:
            seen[key] = now
        send_push("Job Watcher is running",
                  f"Watching {', '.join(SEARCH_TERMS)} on {', '.join(SITES)}. "
                  f"{len(matches)} current matches saved; you will be alerted on new ones.",
                  tags="rocket")
        save_seen(seen)
        return 0

    sent = 0
    for key, job, tag, years in matches:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        if notify_job(job, tag, years):
            sent += 1
        seen[key] = now  # mark seen even if push failed, to avoid repeats

    extra = len(matches) - sent
    if extra > 0:
        for key, *_ in matches[sent:]:
            seen[key] = now
        lines = [f"- {j['title']} | {j['company']}" for _, j, _, _ in matches[sent:sent + 15]]
        send_push(f"+{extra} more new analyst jobs", "\n".join(lines), tags="clipboard")

    save_seen(seen)
    print(f"Sent {sent} alerts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
