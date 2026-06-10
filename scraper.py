"""
scraper.py — Multi-location job scraping via JobSpy with deduplication.
"""
import hashlib
import time
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

import database as db
from config import config

console = Console()

# ── Country mapping for Indeed (required by JobSpy) ───────────────────────────
INDEED_COUNTRY_MAP = {
    "india": "India",
    "uae": "united arab emirates",
    "dubai": "united arab emirates",
    "abu dhabi": "united arab emirates",
    "sharjah": "united arab emirates",
    "united arab emirates": "united arab emirates",
    "uk": "UK",
    "united kingdom": "UK",
    "usa": "USA",
    "united states": "USA",
    "canada": "Canada",
    "australia": "Australia",
    "singapore": "Singapore",
    "germany": "Germany",
    "netherlands": "Netherlands",
    "remote": "USA",
}


def _get_indeed_country(location: str) -> str:
    loc_lower = location.lower()
    for key, country in INDEED_COUNTRY_MAP.items():
        if key in loc_lower:
            return country
    return "USA"


def _make_external_id(title: str, company: str, location: str) -> str:
    """Stable dedup key: MD5 of normalized title+company+location."""
    key = f"{(title or '').lower().strip()}|{(company or '').lower().strip()}|{(location or '').lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()[:20]


def _get_sites_for_location(location: str, configured_sites: list[str]) -> list[str]:
    """
    LinkedIn works globally; Indeed/Glassdoor have region limitations.
    Always include linkedin for international searches.
    """
    loc_lower = location.lower()
    international = any(
        k in loc_lower
        for k in [
            "uae",
            "dubai",
            "abu dhabi",
            "sharjah",
            "united arab emirates",
            "germany",
            "netherlands",
            "singapore",
        ]
    )
    if international:
        # Prefer LinkedIn + Glassdoor for international; Indeed less reliable
        sites = [s for s in configured_sites if s in ("linkedin", "glassdoor")]
        if not sites:
            sites = ["linkedin"]
        return sites
    return configured_sites


def _parse_salary(row) -> str:
    """Build a human-readable salary string from JobSpy row."""
    try:
        mn = row.get("min_amount")
        mx = row.get("max_amount")
        interval = row.get("interval", "")
        currency = row.get("currency", "")
        if mn and mx:
            return f"{currency}{int(mn):,} - {currency}{int(mx):,} {interval}".strip()
        if mn:
            return f"{currency}{int(mn):,}+ {interval}".strip()
    except Exception:
        pass
    return ""


def scrape_all(show_progress: bool = True) -> dict:
    """
    Scrape jobs from all configured locations × search terms.
    Returns dict with counts: {"scraped": N, "new": N, "skipped": N}
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        raise ImportError("Run: pip install python-jobspy")

    total_scraped = 0
    total_new = 0
    total_skipped = 0
    combos = [
        (term, location)
        for term in config.SEARCH_TERMS
        for location in config.LOCATIONS
    ]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        disable=not show_progress,
    ) as progress:
        task = progress.add_task("Scraping...", total=len(combos))

        for term, location in combos:
            progress.update(
                task, description=f"[cyan]Scraping[/] '{term}' in {location}"
            )
            sites = _get_sites_for_location(location, config.JOB_SITES)
            indeed_country = _get_indeed_country(location)

            try:
                jobs_df = scrape_jobs(
                    site_name=sites,
                    search_term=term,
                    location=location,
                    results_wanted=config.RESULTS_PER_LOCATION,
                    hours_old=config.HOURS_OLD,
                    country_indeed=indeed_country,
                    verbose=0,
                )
            except Exception as e:
                console.print(f"  [yellow]⚠ Scrape failed for {location}/{term}: {e}[/]")
                progress.advance(task)
                time.sleep(2)
                continue

            if jobs_df is None or jobs_df.empty:
                progress.advance(task)
                continue

            for _, row in jobs_df.iterrows():
                title = str(row.get("title", "") or "").strip()
                company = str(row.get("company", "") or "").strip()
                job_location = str(row.get("location", "") or location).strip()
                description = str(row.get("description", "") or "").strip()
                url = str(row.get("job_url", "") or "").strip()
                source = str(row.get("site", "") or "").strip()

                if not title or not company:
                    continue

                external_id = _make_external_id(title, company, job_location)

                date_posted = row.get("date_posted")
                date_str = str(date_posted) if date_posted else ""

                job_data = {
                    "external_id": external_id,
                    "title": title,
                    "company": company,
                    "location": job_location,
                    "description": description,
                    "url": url,
                    "source": source.capitalize(),
                    "salary": _parse_salary(row),
                    "date_posted": date_str,
                }

                total_scraped += 1
                if db.insert_job(job_data):
                    total_new += 1
                else:
                    total_skipped += 1

            progress.advance(task)
            # Brief pause between requests to be polite
            time.sleep(3)

    return {
        "scraped": total_scraped,
        "new": total_new,
        "skipped": total_skipped,
    }
