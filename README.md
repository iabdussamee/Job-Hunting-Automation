# 🎯 Job Hunter — Automated Job Search with AI + Notion

A fully local, zero-cost job hunting automation system.  
Scrapes jobs from multiple locations, scores them against your resume with AI, 
generates cover letters, finds recruiter emails, and keeps a live Notion dashboard.

---

## What It Does

| Feature | Tool | Cost |
|---|---|---|
| Job scraping (LinkedIn, Indeed, Glassdoor) | python-jobspy | Free |
| ATS scoring + skill gap analysis | Gemini 3.1 Flash Lite API |
| Cover letter generation | Gemini 3.1 Flash Lite API | Free |
| Resume improvement suggestions | Gemini 3.1 Flash Lite API | Free |
| Recruiter email lookup | Hunter.io + Apollo.io | Free tiers |
| Dashboard (Kanban + filters) | Notion API | Free |
| Local storage | SQLite | Free |

**Total cost: $0** — runs on any laptop, no local GPU needed.

---

## Prerequisites

- Python 3.11+
- A resume file (.pdf, .docx, or .txt)
- Free accounts for: Google AI Studio, Notion, Hunter.io (optional), Apollo.io (optional)

---

## Installation

## Installation

### Linux / Mac

```bash
# 1. Enter the project folder
cd job_hunter

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the config template
cp .env.example .env
```

### Windows (Command Prompt)

```cmd
:: 1. Enter the project folder
cd job_hunter

:: 2. Create virtual environment
python -m venv venv

:: 3. Activate it
venv\Scripts\activate

:: 4. Install dependencies
pip install -r requirements.txt

:: 5. Copy the config template
copy .env.example .env
```

### Windows (PowerShell)

```powershell
# If you get a scripts execution error, run this once first:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 1. Enter the project folder
cd job_hunter

# 2. Create and activate virtual environment
python -m venv venv
venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the config template
Copy-Item .env.example .env
```

> **Always activate the venv before running any commands.**  
> You'll know it's active when you see `(venv)` at the start of your prompt.

---

## Configuration

Edit `.env` with your credentials:

### 1. Gemini API Key (Required — Free)

1. Go to **https://aistudio.google.com/app/apikey**
2. Click **Create API Key**
3. Paste into `.env`:
   ```
   GEMINI_API_KEY=AIzaSy...
   ```

### 2. Notion Integration (Required — Free)

**Step A — Create an integration:**
1. Go to **https://www.notion.so/my-integrations**
2. Click **+ New integration**
3. Name it "Job Hunter", select your workspace
4. Copy the **Internal Integration Token**
5. Paste into `.env`:
   ```
   NOTION_TOKEN=secret_xxxx...
   ```

**Step B — Create a parent page:**
1. In Notion, create a new blank page (e.g., "Job Search")
2. Click the `···` menu → **Add connections** → select "Job Hunter"
3. Copy the page URL:  
   `https://notion.so/My-Page-Title-**abc123def456...**`
4. The last 32 hex characters = your page ID. Paste into `.env`:
   ```
   NOTION_PARENT_PAGE_ID=abc123def456...
   ```
   *(The script auto-creates the database under this page on first run)*
5. Create properties for the database:

| Property Name | Notion Type |
| :--- | :--- |
| `Company` | Text |
| `Location` | Text |
| `Match Score` | Number |
| `Recommendation` | Select |
| `Status` | Select |
| `Source` | Select |
| `Job URL` | URL |
| `Recruiter Email` | Email |
| `Date Posted` | Date |
| `Date Added` | Date |
| `Salary` | Text |
| `Missing Skills` | Text |
| `Job ID` | Number |

### 3. Hunter.io (Optional — 25 free searches/month)

1. Sign up at **https://hunter.io**
2. Go to **API** → copy your key
3. Add to `.env`: `HUNTER_API_KEY=your_key`

### 4. Apollo.io (Optional — 50 free credits/month)

1. Sign up at **https://apollo.io**
2. Go to **Settings → API Keys**
3. Add to `.env`: `APOLLO_API_KEY=your_key`

### 5. Configure Your Search

```env
# Your resume
RESUME_PATH=resume.pdf

# Job titles to search for (comma-separated)
SEARCH_TERMS=software engineer,python developer,backend engineer

# Locations (pipe-separated — commas are part of location names!)
LOCATIONS=Hyderabad, India|Dubai, UAE|Bangalore, India|Remote

# Which job sites to scrape
JOB_SITES=linkedin,indeed,glassdoor

# How many results per location+term combo
RESULTS_PER_LOCATION=15

# Only show jobs posted in the last N hours
HOURS_OLD=72
```

---

## Usage

### Run the full pipeline (recommended)
```bash
python main.py run
```
This does everything in one shot: scrape → AI score → cover letters → recruiter emails → sync to Notion.

#### Primary Pipeline Commands
* **`python main.py run`**
  Runs the full end-to-end automation pipeline.
  * `--skip-scrape` : Skip scraping new jobs.
  * `--skip-emails` : Skip looking up recruiter emails.
  * `--skip-covers` : Skip generating cover letters.
  * `--skip-sync`   : Skip syncing data to Notion.
  * `--enrich-limit <int>` : Maximum number of jobs to enrich in this run *(Default: 50)*.
  * `--cover-limit <int>`  : Maximum number of cover letters to generate *(Default: 10)*.

* **`python main.py scrape`**
  Scrapes new job listings from your configured locations and sites.

* **`python main.py enrich`**
  Uses AI to evaluate and score jobs against your resume.
  * `--limit <int>` : Maximum number of jobs to process *(0 = all unenriched jobs)*.
  * `--rescore`     : Forces re-scoring of **all** jobs, including those already processed.

* **`python main.py covers`**
  Batch-generates cover letters for high-matching positions.
  * `--min-score <int>` : Minimum match score threshold required to generate a letter *(Default: 65)*.
  * `--limit <int>`     : Maximum number of cover letters to generate *(Default: 10)*.

* **`python main.py emails`**
  Searches for recruiter emails associated with your top job matches.
  * `--limit <int>` : Maximum number of jobs to attempt lookup for *(Default: 20)*.

* **`python main.py sync`**
  Pushes all local database jobs, scores, cover letters, and email drafts to your Notion dashboard.

---

#### Utility & Management Commands
* **`python main.py list`**
  Displays your tracked jobs in a formatted terminal table.
  * `--filter [apply|maybe|skip]` : Filter results by AI recommendation category.
  * `--min-score <int>`            : Filter results by a minimum match score.
  * `--limit <int>`                : Maximum number of rows to display *(Default: 30)*.

* **`python main.py stats`**
  Displays pipeline metrics, including total jobs scraped, average match scores, and current top matches.

* **`python main.py schedule`**
  Starts the background daily automation scheduler (runs automatically at 07:00 everyday). *Note: This blocks the terminal; use `nohup` or `screen` to keep it running.*

* **`python main.py cover <job_id>`**
  Generates and saves a cover letter for a single, specific job using its local database ID.

* **`python main.py clean-notion`**
  Wipes all pages from your Notion database and resets local sync flags so you can perform a fresh resync.
  * `--yes` : Skips the terminal confirmation prompt.

### Run automatically every day

### Linux / Mac — background process
```bash
nohup python main.py schedule > scheduler.log 2>&1 &
```

Check if it's running:
```bash
tail -f scheduler.log
```

### Linux / Mac — cron (recommended)
```bash
crontab -e
```
Add this line:
```
0 7 * * * cd /path/to/job_hunter && /path/to/venv/bin/python main.py run >> cron.log 2>&1
```

### Windows — Task Scheduler

1. Open **Task Scheduler** (search in Start menu)
2. Click **Create Basic Task**
3. Name: `Job Hunter`
4. Trigger: **Daily** at 7:00 AM
5. Action: **Start a program**
6. Program: `C:\path\to\job_hunter\venv\Scripts\python.exe`
7. Arguments: `main.py run`
8. Start in: `C:\path\to\job_hunter`
9. Click **Finish**

### Windows — run in background manually
```cmd
:: Command Prompt
start /b python main.py schedule > scheduler.log 2>&1

:: PowerShell
Start-Process python -ArgumentList "main.py schedule" -RedirectStandardOutput scheduler.log -WindowStyle Hidden
```

---

## Notion Dashboard

After your first `sync`, open Notion to see the **🎯 Job Hunt Dashboard** database.

**Suggested views to create in Notion:**

1. **Kanban board** — Group by `Status` → drag cards through:  
   🆕 New → 📤 Applied → 📞 Phone Screen → 💼 Interview → 🎉 Offer

2. **Priority filter** — Filter by `Recommendation = ✅ Apply`, sort by `Match Score` descending

3. **Gallery view** — Shows company name + score at a glance

**Each job page contains:**
- 📋 Job details (company, location, salary, source link)
- 🎯 ATS analysis (score, matched/missing skills, keywords to add)
- 📝 Resume suggestions (specific rewrites for this job)
- ✉️ Cover letter (generated on request)
- 📧 Recruiter outreach (email + draft message)

---

## Workflow Tips

1. **Run `python main.py run` each morning** — new jobs land in Notion automatically
2. **Filter Notion by `✅ Apply` + sort by `Match Score`** — your daily priority list
3. **Check `⚡ Add to Resume` keywords** before applying — tweak your resume per job
4. **Update Status in Notion** as you apply — keeps your pipeline organized
5. **Generate one-off cover letters** with `python main.py cover <id>` for any job

---

## Troubleshooting

**`GEMINI_API_KEY` errors** — Get a free key at aistudio.google.com/app/apikey  
**Notion 401 errors** — Make sure you added the integration to your Notion page (Step B above)  
**No jobs scraped** — Try increasing `HOURS_OLD` to 168 (1 week); some sites block frequent scraping  
**JobSpy install issues** — Try `pip install python-jobspy --upgrade`  
**PDF parsing fails** — Convert resume to .docx or .txt; some PDFs use non-standard encoding
