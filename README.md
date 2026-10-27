# Job Watcher

Sends a push notification to your phone within about 15 to 30 minutes of a new
**Data Analyst / Business Analyst** job being posted in the USA, filtered for
0 to 3 years of experience and visa sponsorship. Runs free on GitHub Actions,
so your laptop can be off.

## How it works

| Step | What happens |
|---|---|
| 1 | Every 15 minutes GitHub runs `watcher.py` |
| 2 | It pulls jobs posted in the last 2 hours from Indeed, LinkedIn, Google Jobs, ZipRecruiter and Glassdoor |
| 3 | Keeps analyst titles; drops Principal, Lead, Manager, Intern, III/IV, clearance |
| 4 | Drops jobs asking for more than 3 years of experience |
| 5 | Drops postings that say "no sponsorship", "US citizen only", "clearance required" |
| 6 | Keeps postings that mention sponsorship (HIGH) or come from a known H-1B sponsor in `sponsors.txt` (KNOWN) |
| 7 | Skips anything already alerted (`seen.json`), even if the same job appears on two sites |
| 8 | Sends a push to your phone via the free ntfy app; tapping it opens the job |

The very first run does not alert. It saves current jobs and sends one
"Job Watcher is running" message, so your phone is not flooded.

## Setup checklist (about 15 minutes)

### A. Phone app

1. Install **ntfy** on your phone (App Store or Google Play, free).
2. Pick a secret topic name nobody can guess, for example `manoj-jobs-7f3k9q2x`.
   Anyone who knows the topic name can read it, so do not use something simple.
3. In the app, tap **+**, enter the topic name, server `ntfy.sh`, and subscribe.

### B. GitHub repo

1. Go to https://github.com/new
2. Name it `job-watcher`. Choose **Public**.
   Public repos get unlimited free Actions minutes. A private repo running every
   15 minutes would use more than the 2,000 free minutes per month.
   Your topic name stays private because it is stored as a secret, not in code.
3. Upload all files from this folder, including the hidden `.github` folder
   (drag the whole folder contents into "uploading an existing file", or use git):

   ```bash
   cd job-watcher
   git init
   git add .
   git commit -m "Job watcher"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/job-watcher.git
   git push -u origin main
   ```

### C. Secret

1. In the repo: **Settings > Secrets and variables > Actions > New repository secret**
2. Name: `NTFY_TOPIC`  Value: your topic name from step A2.

### D. Allow the workflow to save progress

1. **Settings > Actions > General > Workflow permissions**
2. Select **Read and write permissions**, then Save.

### E. Test

1. **Actions** tab > if asked, click "I understand my workflows, go ahead and enable them".
2. Click **Job Watcher** > **Run workflow** > mode `test` > Run.
   Your phone should get "Job Watcher test" within a minute.
3. Run again with mode `dry` and open the log. You should see results per site
   and a list of matches. Nothing is sent in dry mode.
4. Run again with mode `normal`. You get "Job Watcher is running".
   From now on it runs by itself every 15 minutes.

## Sponsor list (automatic)

The workflow **Update Sponsors** downloads the official USCIS H-1B Employer
Data Hub export and rebuilds `sponsors.txt` on the 1st of every month.
Run it once by hand after setup: **Actions > Update Sponsors > Run workflow**.
The log shows how many sponsors were written.

Manual alternative: download CSVs from
https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub into a
`uscis_data` folder, run `python build_sponsors.py`, then commit `sponsors.txt`.

## Settings you may want to change (top of `watcher.py`)

| Setting | Default | Meaning |
|---|---|---|
| `SEARCH_TERMS` | data analyst, business analyst, BI analyst | What to search |
| `SITES` | indeed, linkedin, google, zip_recruiter, glassdoor | Where to search. Blocked sites are logged and skipped |
| `COMPANY_EXCLUDE` | empty | Company name regexes to skip |
| `MAX_YEARS_EXPERIENCE` | 3 | Drop jobs asking for more |
| `ALERT_UNKNOWN_SPONSOR` | False | True = also alert when company is not a known sponsor and the posting says nothing either way |
| `TITLE_INCLUDE` / `TITLE_EXCLUDE` | see file | Title filters |
| `MAX_ALERTS_PER_RUN` | 10 | Extra matches go into one summary push |

## Things to know

| Topic | Detail |
|---|---|
| Speed | GitHub can start scheduled runs a few minutes late. Expect alerts 15 to 30 minutes after posting |
| LinkedIn | Often blocks cloud servers. If logs show LinkedIn errors every run, set `FETCH_LINKEDIN_DESCRIPTION = False` or remove `linkedin` from `SITES`. Indeed is the most reliable |
| Sponsorship | KNOWN means the company has sponsored before, not a promise for this job. Confirm with the recruiter |
| Pausing | **Actions > Job Watcher > ... > Disable workflow** |
| GitHub auto-disable | GitHub disables scheduled workflows after 60 days with no repo activity. Saving `seen.json` counts as activity |

## Local commands

```bash
pip install -r requirements.txt
python watcher.py --dry-run                 # see matches, send nothing
NTFY_TOPIC=your-topic python watcher.py --test-notify
python -m pytest -q                          # run tests
```
