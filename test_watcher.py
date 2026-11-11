"""Offline tests. Run: python -m pytest -q  (or: python test_watcher.py)"""

import json

import watcher as w

w.load_sponsors_orig = w.load_sponsors

SPONSORS = {w.normalize_company(x) for x in ["Capital One", "Amazon", "Johnson & Johnson", "Citi"]}


def test_normalize_company():
    assert w.normalize_company("Capital One, N.A.") == "capital one"
    assert w.normalize_company("Amazon.com Services LLC") == "amazon com services"
    assert w.normalize_company("Johnson & Johnson") == "johnson and johnson"


def test_known_sponsor_prefix_match():
    assert w.is_known_sponsor("Amazon Web Services, Inc.", SPONSORS)
    assert w.is_known_sponsor("Amazon.com Services LLC", SPONSORS)
    assert w.is_known_sponsor("Capital One", SPONSORS)
    assert not w.is_known_sponsor("Amazonia Foods", SPONSORS)
    assert not w.is_known_sponsor("Citizens Bank", SPONSORS)  # 'citi' must not match 'citizens'


def test_titles():
    assert w.title_ok("Data Analyst")
    assert w.title_ok("Senior Data Analyst - US Card")
    assert w.title_ok("Business Intelligence Analyst II")
    assert not w.title_ok("Principal Data Analyst")
    assert not w.title_ok("Data Analyst III")
    assert not w.title_ok("Data Analytics Manager")
    assert not w.title_ok("Data Analyst Intern")
    assert not w.title_ok("Software Engineer")
    assert not w.title_ok("Lead Business Analyst")


def test_years():
    assert w.min_years_required("Requires 2+ years of experience in SQL.") == 2
    assert w.min_years_required("3-5 years of relevant experience") == 3
    assert w.min_years_required("Experience: 5 years minimum. Also 1 year of Tableau experience") == 1
    assert w.min_years_required("We have been in business for 30 years.") is None
    assert w.min_years_required("") is None


def test_sponsorship_tags():
    assert w.sponsorship_tag("Acme", "We are unable to sponsor visas.", SPONSORS) is None
    assert w.sponsorship_tag("Acme", "Must be authorized to work in the US without sponsorship.", SPONSORS) is None
    assert w.sponsorship_tag("Acme", "Applicants must be a U.S. citizen.", SPONSORS) is None
    assert w.sponsorship_tag("Acme", "Active Secret clearance required.", SPONSORS) is None
    assert w.sponsorship_tag("Capital One", "No sponsorship available for this role.", SPONSORS) is None
    assert w.sponsorship_tag("Acme", "Visa sponsorship is available.", SPONSORS) == "HIGH"
    assert w.sponsorship_tag("Acme", "STEM OPT candidates welcome.", SPONSORS) == "HIGH"
    assert w.sponsorship_tag("Capital One", "Great benefits.", SPONSORS) == "KNOWN"
    assert w.sponsorship_tag("Acme", "Great benefits.", SPONSORS) == "UNKNOWN"
    # words containing 'opt' must not count as sponsorship
    assert w.sponsorship_tag("Acme", "Optimize and adopt optional tools.", SPONSORS) == "UNKNOWN"


def test_job_key_dedupes_across_sites():
    a = w.job_key("Capital One, N.A.", "Data Analyst", "McLean, VA")
    b = w.job_key("Capital One", "data analyst ", "McLean, Virginia, US")
    assert a == b


def make_job(title, company, desc, location="McLean, VA", site="indeed"):
    return {"site": site, "title": title, "company": company, "location": location,
            "url": f"https://example.com/{abs(hash(title + company))}", "board_url": "",
            "description": desc, "date_posted": "2026-10-27", "is_remote": False}


def test_full_run(tmp_path, monkeypatch):
    """First run seeds silently; second run alerts only new matches."""
    monkeypatch.setattr(w, "SEEN_FILE", tmp_path / "seen.json")
    sponsors_file = tmp_path / "sponsors.txt"
    sponsors_file.write_text("Capital One\nAmazon\n")
    monkeypatch.setattr(w, "load_sponsors", lambda: w.__dict__["load_sponsors_orig"](sponsors_file))
    pushes = []
    monkeypatch.setattr(w, "send_push", lambda title, msg, click_url="", priority="default", tags="": pushes.append((title, msg, click_url)) or True)

    batch1 = [make_job("Data Analyst", "Capital One", "2+ years experience")]
    monkeypatch.setattr(w, "scrape_all", lambda: batch1)
    monkeypatch.setattr("sys.argv", ["watcher.py"])
    assert w.main() == 0
    assert len(pushes) == 1 and pushes[0][0] == "Job Watcher is running"
    assert len(json.loads((tmp_path / "seen.json").read_text())) == 1

    pushes.clear()
    batch2 = batch1 + [
        make_job("Senior Data Analyst", "Amazon Web Services, Inc.", "1+ years of experience", site="linkedin"),
        make_job("Business Analyst", "Acme Corp", "Visa sponsorship is available. 2 years experience"),
        make_job("Data Analyst", "Defense Co", "Active Secret clearance required"),
        make_job("Data Analyst", "Big Bank", "5+ years of experience required"),
        make_job("Principal Data Analyst", "Capital One", ""),
        make_job("Data Analyst", "Unknown LLC", "Nice team"),
    ]
    monkeypatch.setattr(w, "scrape_all", lambda: batch2)
    assert w.main() == 0
    titles = sorted(p[0] for p in pushes)
    assert titles == ["Business Analyst", "Senior Data Analyst"], titles
    # HIGH (sponsorship in posting) is sent before KNOWN
    assert pushes[0][0] == "Business Analyst"

    pushes.clear()
    assert w.main() == 0
    assert pushes == []  # nothing new on third run




def test_federal_named_company_kept_but_clearance_dropped():
    job = make_job("Data Analyst", "Navy Federal Credit Union", "2 years experience")
    keep, _, tag, _ = w.evaluate(job, SPONSORS | {"navy federal credit union"})
    assert keep and tag == "KNOWN"
    job = make_job("Data Analyst", "Some Federal Co", "Active Secret clearance required")
    keep, reason, _, _ = w.evaluate(job, SPONSORS)
    assert not keep and reason == "no sponsorship / clearance"


def test_build_sponsors_long_and_wide(tmp_path, monkeypatch):
    import build_sponsors as b
    data = tmp_path / "uscis_data"
    data.mkdir()
    (data / "long.csv").write_text(
        "Fiscal Year ,Employer (Petitioner) Name,Measure Names,Measure Values\n"
        "2026,BETA DATA CORP,New Employment Approval,\"1,204\"\n"
        "2026,BETA DATA CORP,Continuation Approval,10\n"
        "2026,RENEW ONLY LLC,Continuation Approval,7\n"
        "2026,RENEW ONLY LLC,New Employment Approval,0\n")
    (data / "wide.csv").write_text(
        "Fiscal Year,Employer (Petitioner) Name,Initial Approval,Continuing Approval\n"
        "2024,ACME ANALYTICS INC,3,5\n")
    sponsors = tmp_path / "sponsors.txt"
    sponsors.write_text("# starter\nCapital One\n")
    monkeypatch.setattr(b, "DATA_DIR", data)
    monkeypatch.setattr(b, "SPONSORS_FILE", sponsors)
    monkeypatch.setattr("sys.argv", ["build_sponsors.py"])
    assert b.main() == 0
    names = [l for l in sponsors.read_text().splitlines() if l and not l.startswith("#")]
    assert names == ["acme analytics", "beta data", "capital one"], names


def test_titles_for_all_five_resumes():
    for t in ["GIS Analyst", "Geospatial Data Analyst", "Remote Sensing Analyst I",
              "Data Quality Analyst", "Data Governance Analyst", "Data Steward",
              "Power BI Analyst", "Reporting Analyst", "Business Operations Analyst"]:
        assert w.title_ok(t), t
    for t in ["GIS Manager", "Lead Data Quality Analyst", "Principal Geospatial Analyst"]:
        assert not w.title_ok(t), t


def test_pick_resume():
    long_gis = "Use ArcGIS, QGIS and Python with satellite imagery and raster data. " * 5
    assert w.pick_resume("GIS Analyst", long_gis)[0] == "Geospatial Analyst"
    assert w.pick_resume("Geospatial Data Analyst", long_gis)[0] == "Geospatial Analyst"
    assert w.pick_resume("Data Quality Analyst", "data profiling and data lineage")[0] == "Data Quality : Governance"
    assert w.pick_resume("BI Analyst", "Power BI and DAX dashboards")[0] == "Business Intelligence Analyst"
    assert w.pick_resume("Business Analyst", "gather requirements from stakeholder groups")[0] == "Business Analyst"
    assert w.pick_resume("Data Analyst", "SQL and Python")[0] == "Data Analyst"
    # Generic title: description decides
    name, skills, title_hit = w.pick_resume("Operations Analyst", "Build Power BI dashboards with DAX, Power Query and Tableau. KPI reporting.")
    assert name == "Business Intelligence Analyst" and not title_hit
    # Generic Business Analyst title with a business-analysis description stays BA
    assert w.pick_resume("Business Analyst", "Work with stakeholder teams on requirements, SQL and Excel")[0] == "Business Analyst"
    # 'SQL' must not match inside 'NoSQL'; 'Git' not inside 'digital'
    assert "SQL" not in w.matched_skills("Data Analyst", "NoSQL stores")
    assert "Git" not in w.matched_skills("Data Analyst", "digital products")


def test_low_skill_match_dropped():
    desc = ("We need someone for warehouse picking and forklift operations. " * 10) + "2 years experience"
    job = make_job("Operations Analyst", "Capital One", desc)
    keep, reason, _, _ = w.evaluate(job, SPONSORS)
    assert not keep and reason == "low skill match"
    desc2 = ("Analyze data with SQL, Python and Tableau. " * 10) + "2 years experience"
    job2 = make_job("Data Analyst", "Capital One", desc2)
    keep, _, _, _ = w.evaluate(job2, SPONSORS)
    assert keep and job2["resume"] == "Data Analyst" and len(job2["skills"]) >= 3


def test_alert_message_names_resume(monkeypatch):
    sent = []
    monkeypatch.setattr(w, "send_push", lambda title, msg, click_url="", priority="default", tags="": sent.append(msg) or True)
    job = make_job("GIS Analyst", "Capital One", "ArcGIS QGIS Python")
    job["resume"], job["skills"] = "Geospatial Analyst", ["ArcGIS", "QGIS", "Python"]
    w.notify_job(job, "KNOWN", 2)
    assert sent[0].startswith("Use: Geospatial Analyst resume")
    assert "Skills matched (3): ArcGIS, QGIS, Python" in sent[0]


def test_linkedin_429_retry(monkeypatch):
    import sys, types
    import pandas as pd
    calls = []

    def fake_scrape(**kw):
        calls.append(kw["site_name"][0])
        df = pd.DataFrame([{"site": kw["site_name"][0], "title": "Data Analyst", "company": "Acme",
                            "location": "VA", "job_url": "u", "description": "d"}])
        if kw["site_name"][0] == "linkedin" and calls.count("linkedin") == 1:
            df = df.iloc[0:0]
            df.attrs["errors"] = {"linkedin": "response status code 429 (blocked: use a proxy)"}
        return df

    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=fake_scrape))
    monkeypatch.setattr(w, "SEARCH_TERMS", ["data analyst", "business analyst"])
    monkeypatch.setattr(w, "SITES", ["indeed", "linkedin"])
    sleeps = []
    import time
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    jobs = w.scrape_all()
    assert calls == ["indeed", "linkedin", "linkedin", "indeed", "linkedin"]
    assert sleeps == [w.LINKEDIN_RETRY_WAIT, w.LINKEDIN_PAUSE_SECONDS]
    assert len(jobs) == 4


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
