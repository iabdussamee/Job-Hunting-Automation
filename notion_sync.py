"""
notion_sync.py — Sync jobs to a Notion dashboard.

Layout:
  - One Notion database = all jobs (sortable, filterable, Kanban by Status)
  - One page per job — properties in header, full analysis in page body
  - Sections added incrementally: job description on create,
    analysis / cover letter / email draft appended after enrichment
"""
import json
import time
from typing import Optional

from notion_client import Client
from notion_client.errors import APIResponseError

import database as db
from config import config

# ── Client ────────────────────────────────────────────────────────────────────

_notion = Client(auth=config.NOTION_TOKEN)
_SLEEP = 0.4   # seconds between Notion API calls (max 3 req/s)


# ── Database schema ───────────────────────────────────────────────────────────

DB_PROPERTIES = {
    "Job Title": {"title": {}},
    "Company":   {"rich_text": {}},
    "Location":  {"rich_text": {}},
    "Match Score": {"number": {"format": "number"}},
    "Recommendation": {
        "select": {
            "options": [
                {"name": "✅ Apply",  "color": "green"},
                {"name": "🟡 Maybe",  "color": "yellow"},
                {"name": "❌ Skip",   "color": "red"},
                {"name": "⏳ Pending","color": "gray"},
            ]
        }
    },
    "Status": {
        "select": {
            "options": [
                {"name": "🆕 New",         "color": "blue"},
                {"name": "📤 Applied",      "color": "purple"},
                {"name": "📞 Phone Screen", "color": "orange"},
                {"name": "💼 Interview",    "color": "yellow"},
                {"name": "🎉 Offer",        "color": "green"},
                {"name": "❌ Rejected",     "color": "red"},
            ]
        }
    },
    "Source":   {"select": {
        "options": [
            {"name": "LinkedIn",    "color": "blue"},
            {"name": "Indeed",      "color": "purple"},
            {"name": "Glassdoor",   "color": "green"},
            {"name": "ZipRecruiter","color": "orange"},
            {"name": "Google",      "color": "red"},
        ]
    }},
    "Country":        {"select": {"options": [
                            {"name": "India",         "color": "orange"},
                            {"name": "UAE",           "color": "green"},
                            {"name": "United States", "color": "blue"},
                            {"name": "United Kingdom","color": "purple"},
                            {"name": "Canada",        "color": "red"},
                            {"name": "Australia",     "color": "yellow"},
                            {"name": "Singapore",     "color": "pink"},
                            {"name": "Germany",       "color": "gray"},
                            {"name": "Remote",        "color": "brown"},
                        ]}},
    "Job URL":        {"url": {}},
    "Recruiter Email":{"email": {}},
    "Date Posted":    {"date": {}},
    "Date Added":     {"date": {}},
    "Salary":         {"rich_text": {}},
    "Missing Skills": {"rich_text": {}},
    "Job ID":         {"number": {"format": "number"}},   # Internal SQLite ID
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sleep():
    time.sleep(_SLEEP)


def _rich(text: str) -> list:
    """Single Notion rich_text element."""
    return [{"type": "text", "text": {"content": str(text)[:2000]}}]


def _para(text: str) -> dict:
    """Paragraph block."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich(text)},
    }


def _h2(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": _rich(text)},
    }


def _h3(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": _rich(text)},
    }


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _callout(text: str, emoji: str = "💡") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich(text),
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def _bullet(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich(text)},
    }


def _long_text_blocks(text: str, chunk_size: int = 1900) -> list[dict]:
    """Split long text into multiple paragraph blocks (Notion 2000-char limit)."""
    if not text:
        return []
    return [_para(text[i: i + chunk_size]) for i in range(0, len(text), chunk_size)]


def _rec_label(rec: str) -> str:
    mapping = {"apply": "✅ Apply", "maybe": "🟡 Maybe", "skip": "❌ Skip"}
    return mapping.get((rec or "").lower(), "⏳ Pending")


def _safe_date(date_str: str) -> Optional[dict]:
    """Return Notion date property or None if invalid."""
    if not date_str or date_str in ("None", "nan", ""):
        return None
    try:
        # Notion requires ISO 8601; simplify to YYYY-MM-DD
        clean = date_str.strip()[:10]
        if len(clean) == 10 and clean[4] == "-" and clean[7] == "-":
            return {"start": clean}
    except Exception:
        pass
    return None


def _json_list(json_str) -> list:
    if isinstance(json_str, list):
        return json_str
    try:
        return json.loads(json_str or "[]")
    except Exception:
        return []


def _extract_country(location: str) -> str:
    """
    Pull the country from a location string.
    'Calgary, Alberta, Canada' → 'Canada'
    'Hyderabad, India'         → 'India'
    'Remote'                   → 'Remote'
    """
    if not location:
        return "Unknown"
    parts = [p.strip() for p in location.split(",")]
    country = parts[-1]
    # Normalise common variants
    aliases = {
        "uae": "UAE",
        "united arab emirates": "UAE",
        "usa": "United States",
        "us": "United States",
        "united states of america": "United States",
        "uk": "United Kingdom",
        "great britain": "United Kingdom",
    }
    return aliases.get(country.lower(), country)


# ── Database management ───────────────────────────────────────────────────────

def ensure_database() -> str:
    """
    Return the Notion database ID.
    Uses NOTION_DB_ID if set; otherwise creates a new database
    under NOTION_PARENT_PAGE_ID and prints the ID for .env.
    """
    if config.NOTION_DB_ID:
        return config.NOTION_DB_ID

    if not config.NOTION_PARENT_PAGE_ID:
        raise ValueError(
            "Set NOTION_PARENT_PAGE_ID in .env to auto-create the database, "
            "or set NOTION_DB_ID to use an existing one."
        )

    parent_id = config.NOTION_PARENT_PAGE_ID.replace("-", "")

    result = _notion.databases.create(
        parent={"type": "page_id", "page_id": parent_id},
        title=[{"type": "text", "text": {"content": "🎯 Job Hunt Dashboard"}}],
        properties=DB_PROPERTIES,
    )
    _sleep()

    db_id = result["id"]
    print(f"\n✅ Created Notion database: {db_id}")
    print(f"   Add this to your .env:  NOTION_DB_ID={db_id}\n")
    return db_id


# ── Page creation ─────────────────────────────────────────────────────────────

def _build_properties(job: dict) -> dict:
    """Build Notion properties dict for a job row."""
    props: dict = {}

    props["Job Title"] = {"title": _rich(job.get("title", "Untitled")[:100])}

    if job.get("company"):
        props["Company"] = {"rich_text": _rich(job["company"][:100])}

    if job.get("location"):
        props["Location"] = {"rich_text": _rich(job["location"][:100])}
        props["Country"] = {"select": {"name": _extract_country(job["location"])}}

    score = job.get("match_score") or job.get("score")
    if score is not None:
        props["Match Score"] = {"number": int(score)}

    rec = job.get("recommendation", "pending")
    props["Recommendation"] = {"select": {"name": _rec_label(rec)}}

    props["Status"] = {"select": {"name": "🆕 New"}}

    if job.get("source"):
        source = str(job["source"]).capitalize()
        props["Source"] = {"select": {"name": source}}

    if job.get("url"):
        props["Job URL"] = {"url": job["url"][:2000]}

    if job.get("recruiter_email"):
        props["Recruiter Email"] = {"email": job["recruiter_email"]}

    date = _safe_date(str(job.get("date_posted", "")))
    if date:
        props["Date Posted"] = {"date": date}

    from datetime import date as dt_date
    props["Date Added"] = {"date": {"start": dt_date.today().isoformat()}}

    if job.get("salary"):
        props["Salary"] = {"rich_text": _rich(str(job["salary"])[:200])}

    missing = _json_list(job.get("missing_skills", "[]"))
    if missing:
        props["Missing Skills"] = {"rich_text": _rich(", ".join(missing[:10]))}

    if job.get("id"):
        props["Job ID"] = {"number": int(job["id"])}

    return props


def _build_description_blocks(job: dict) -> list[dict]:
    """Page content blocks — basic job info and full description."""
    blocks = []
    blocks.append(_h2("📋 Job Details"))

    meta_lines = []
    if job.get("company"):
        meta_lines.append(f"🏢 Company: {job['company']}")
    if job.get("location"):
        meta_lines.append(f"📍 Location: {job['location']}")
    if job.get("salary"):
        meta_lines.append(f"💰 Salary: {job['salary']}")
    if job.get("source"):
        meta_lines.append(f"🔗 Source: {job['source']}")
    if job.get("url"):
        meta_lines.append(f"🌐 URL: {job['url']}")

    for line in meta_lines:
        blocks.append(_bullet(line))

    description = (job.get("description") or "").strip()
    if description:
        blocks.append(_divider())
        blocks.append(_h2("📝 Job Description"))
        blocks.extend(_long_text_blocks(description))

    return blocks


def _build_analysis_blocks(job: dict) -> list[dict]:
    """ATS analysis section blocks."""
    blocks = []
    blocks.append(_divider())
    blocks.append(_h2("🎯 ATS Analysis"))

    score = job.get("match_score", 0)
    rec = job.get("recommendation", "pending")
    summary = job.get("summary", "")

    score_bar = "█" * (score // 10) + "░" * (10 - score // 10)
    callout_text = f"Score: {score}/100  [{score_bar}]  →  {_rec_label(rec)}"
    if summary:
        callout_text += f"\n{summary}"
    blocks.append(_callout(callout_text, "🎯"))

    matched = _json_list(job.get("matched_skills", "[]"))
    if matched:
        blocks.append(_h3("✅ Matched Skills"))
        blocks.append(_para(", ".join(matched)))

    missing = _json_list(job.get("missing_skills", "[]"))
    if missing:
        blocks.append(_h3("❌ Missing Skills"))
        blocks.append(_para(", ".join(missing)))

    ats_kw = _json_list(job.get("ats_keywords", "[]"))
    if ats_kw:
        blocks.append(_h3("⚡ Add to Resume (ATS Keywords)"))
        blocks.append(_para(", ".join(ats_kw)))

    if job.get("strongest_match"):
        blocks.append(_h3("💪 Strongest Match"))
        blocks.append(_para(job["strongest_match"]))

    if job.get("biggest_gap"):
        blocks.append(_h3("⚠️ Biggest Gap"))
        blocks.append(_para(job["biggest_gap"]))

    suggestions = _json_list(job.get("resume_suggestions", "[]"))
    if suggestions:
        blocks.append(_h3("📝 Resume Suggestions"))
        for s in suggestions[:5]:
            if isinstance(s, dict):
                section = s.get("section", "")
                issue = s.get("issue", "")
                fix = s.get("fix", "")
                blocks.append(_bullet(f"[{section}] {issue} → {fix}"))

    return blocks


def _build_cover_letter_blocks(cover_letter: str) -> list[dict]:
    blocks = []
    blocks.append(_divider())
    blocks.append(_h2("✉️ Cover Letter"))
    blocks.extend(_long_text_blocks(cover_letter))
    return blocks


def _build_email_blocks(job: dict) -> list[dict]:
    blocks = []
    blocks.append(_divider())
    blocks.append(_h2("📧 Recruiter Outreach"))
    if job.get("recruiter_name"):
        blocks.append(_bullet(f"Name: {job['recruiter_name']}"))
    if job.get("recruiter_email"):
        blocks.append(_bullet(f"Email: {job['recruiter_email']}"))
    if job.get("email_draft"):
        blocks.append(_h3("Draft Email"))
        blocks.extend(_long_text_blocks(job["email_draft"]))
    return blocks


# ── Notion operations ─────────────────────────────────────────────────────────

def _create_page(database_id: str, job: dict) -> str:
    """Create a new Notion page for a job. Returns the page ID."""
    properties = _build_properties(job)
    children = _build_description_blocks(job)

    # If enrichment already exists, add analysis in one shot
    if job.get("match_score"):
        children.extend(_build_analysis_blocks(job))
    if job.get("cover_letter"):
        children.extend(_build_cover_letter_blocks(job["cover_letter"]))
    if job.get("recruiter_email") or job.get("email_draft"):
        children.extend(_build_email_blocks(job))

    # Notion limits page creation to 100 blocks at a time
    BLOCK_LIMIT = 90
    first_batch = children[:BLOCK_LIMIT]
    rest = children[BLOCK_LIMIT:]

    result = _notion.pages.create(
        parent={"database_id": database_id},
        properties=properties,
        children=first_batch,
    )
    _sleep()
    page_id = result["id"]

    # Append remaining blocks if any
    if rest:
        _notion.blocks.children.append(block_id=page_id, children=rest)
        _sleep()

    return page_id


def _update_page(page_id: str, job: dict) -> None:
    """Update properties on an existing page."""
    properties = _build_properties(job)
    # Don't reset Status — user manages that in Notion
    properties.pop("Status", None)
    _notion.pages.update(page_id=page_id, properties=properties)
    _sleep()


def _append_blocks(page_id: str, blocks: list[dict]) -> None:
    """Append blocks to an existing page in batches of 90."""
    BATCH = 90
    for i in range(0, len(blocks), BATCH):
        _notion.blocks.children.append(
            block_id=page_id, children=blocks[i: i + BATCH]
        )
        _sleep()


# ── Public sync API ───────────────────────────────────────────────────────────

def sync_all(verbose: bool = True) -> dict:
    """
    Sync jobs to Notion. Only sends jobs with match_score >= config.MIN_NOTION_SCORE.
    Creates new pages, updates existing ones, appends new sections.

    Returns {"created": N, "updated": N, "sections_added": N, "errors": N}
    """
    database_id = ensure_database()
    jobs = db.get_jobs_for_notion_sync(min_score=config.MIN_NOTION_SCORE)
    if verbose:
        print(f"  Filter: match_score >= {config.MIN_NOTION_SCORE}  ({len(jobs)} jobs qualify)")

    created = 0
    updated = 0
    sections = 0
    errors = 0

    for job in jobs:
        try:
            job_id = job["id"]
            page_id = job.get("notion_page_id")

            if not page_id:
                # First time — create the page
                page_id = _create_page(database_id, job)
                db.update_notion_page_id(job_id, page_id)
                # Mark sections as synced if they were included
                if job.get("match_score"):
                    db.mark_notion_synced(job_id, "analysis")
                if job.get("cover_letter"):
                    db.mark_notion_synced(job_id, "cover")
                if job.get("recruiter_email") or job.get("email_draft"):
                    db.mark_notion_synced(job_id, "email")
                created += 1
                if verbose:
                    print(f"  ✅ Created: {job.get('title', '?')} @ {job.get('company', '?')}")
            else:
                # Update existing page properties
                _update_page(page_id, job)
                updated += 1

                # Append new sections that haven't been synced yet
                if job.get("match_score") and not job.get("notion_synced_analysis"):
                    _append_blocks(page_id, _build_analysis_blocks(job))
                    db.mark_notion_synced(job_id, "analysis")
                    sections += 1

                if job.get("cover_letter") and not job.get("notion_synced_cover"):
                    _append_blocks(page_id, _build_cover_letter_blocks(job["cover_letter"]))
                    db.mark_notion_synced(job_id, "cover")
                    sections += 1

                if (job.get("email_draft") or job.get("recruiter_email")) and not job.get("notion_synced_email"):
                    _append_blocks(page_id, _build_email_blocks(job))
                    db.mark_notion_synced(job_id, "email")
                    sections += 1

        except APIResponseError as e:
            errors += 1
            if verbose:
                print(f"  ⚠️  Notion error for job {job.get('id')}: {e.code} — {str(e)[:120]}")
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  ⚠️  Error syncing job {job.get('id')}: {e}")

    return {
        "created": created,
        "updated": updated,
        "sections_added": sections,
        "errors": errors,
    }
