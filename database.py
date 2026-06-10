"""
database.py — SQLite storage layer.

Schema:
  jobs         — raw scraped listings
  enrichments  — AI analysis, cover letters, recruiter info
  status_log   — application pipeline history
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from config import config


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id     TEXT    UNIQUE NOT NULL,   -- dedup hash: title|company|location
    title           TEXT,
    company         TEXT,
    location        TEXT,
    description     TEXT,
    url             TEXT,
    source          TEXT,
    salary          TEXT,
    date_posted     TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    notion_page_id  TEXT                       -- set after Notion sync
);

CREATE TABLE IF NOT EXISTS enrichments (
    job_id                  INTEGER PRIMARY KEY REFERENCES jobs(id),
    match_score             INTEGER DEFAULT 0,
    recommendation          TEXT    DEFAULT 'pending',   -- apply | maybe | skip
    summary                 TEXT,
    matched_skills          TEXT    DEFAULT '[]',        -- JSON array
    missing_skills          TEXT    DEFAULT '[]',        -- JSON array
    ats_keywords            TEXT    DEFAULT '[]',        -- JSON array
    strongest_match         TEXT,
    biggest_gap             TEXT,
    experience_gap          TEXT    DEFAULT 'none',      -- none | junior | senior
    cover_letter            TEXT,
    resume_suggestions      TEXT    DEFAULT '[]',        -- JSON array
    recruiter_email         TEXT,
    recruiter_name          TEXT,
    email_draft             TEXT,
    enriched_at             TEXT,
    cover_letter_at         TEXT,
    email_found_at          TEXT,
    notion_synced_analysis  INTEGER DEFAULT 0,           -- 0/1 flag
    notion_synced_cover     INTEGER DEFAULT 0,
    notion_synced_email     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS status_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER REFERENCES jobs(id),
    status      TEXT    NOT NULL,   -- new | applied | phone_screen | interview | offer | rejected
    notes       TEXT,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_external_id ON jobs(external_id);
CREATE INDEX IF NOT EXISTS idx_enrichments_score ON enrichments(match_score);
"""


# ── Connection helper ─────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ── Jobs ──────────────────────────────────────────────────────────────────────

def insert_job(job: dict) -> bool:
    """
    Insert a job. Returns True if new, False if duplicate.
    job dict keys: external_id, title, company, location,
                   description, url, source, salary, date_posted
    """
    sql = """
        INSERT OR IGNORE INTO jobs
            (external_id, title, company, location, description,
             url, source, salary, date_posted)
        VALUES
            (:external_id, :title, :company, :location, :description,
             :url, :source, :salary, :date_posted)
    """
    with get_conn() as conn:
        cur = conn.execute(sql, job)
        return cur.rowcount > 0


def get_job(job_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def get_unenriched_jobs(rescore: bool = False) -> list[dict]:
    """
    Return jobs to enrich.
    rescore=False  — only jobs with no score yet (default)
    rescore=True   — all jobs, including already-scored ones
    """
    if rescore:
        sql = """
            SELECT j.*
            FROM jobs j
            WHERE j.description IS NOT NULL
              AND length(j.description) > 100
            ORDER BY j.created_at DESC
        """
    else:
        sql = """
            SELECT j.*
            FROM jobs j
            LEFT JOIN enrichments e ON j.id = e.job_id
            WHERE e.job_id IS NULL
              AND j.description IS NOT NULL
              AND length(j.description) > 100
            ORDER BY j.created_at DESC
        """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def get_jobs_without_cover_letter() -> list[dict]:
    """Jobs that are scored ≥ 60 but don't have a cover letter yet."""
    sql = """
        SELECT j.*, e.match_score, e.recommendation
        FROM jobs j
        JOIN enrichments e ON j.id = e.job_id
        WHERE e.cover_letter IS NULL
          AND e.match_score >= 60
        ORDER BY e.match_score DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def get_jobs_without_recruiter_email() -> list[dict]:
    """Jobs with no recruiter email found yet (only for apply/maybe)."""
    sql = """
        SELECT j.*, e.match_score, e.recommendation
        FROM jobs j
        JOIN enrichments e ON j.id = e.job_id
        WHERE e.recruiter_email IS NULL
          AND e.recommendation IN ('apply', 'maybe')
        ORDER BY e.match_score DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def get_jobs_for_notion_sync(min_score: int = 0) -> list[dict]:
    """
    Return jobs for Notion sync with match_score >= min_score.
    Unenriched jobs (score = NULL) are included only when min_score == 0.
    """
    sql = """
        SELECT
            j.*,
            e.match_score, e.recommendation, e.summary,
            e.matched_skills, e.missing_skills, e.ats_keywords,
            e.strongest_match, e.biggest_gap, e.experience_gap,
            e.cover_letter, e.resume_suggestions,
            e.recruiter_email, e.recruiter_name, e.email_draft,
            e.enriched_at, e.cover_letter_at,
            e.notion_synced_analysis, e.notion_synced_cover, e.notion_synced_email
        FROM jobs j
        LEFT JOIN enrichments e ON j.id = e.job_id
        WHERE COALESCE(e.match_score, 0) >= :min_score
        ORDER BY COALESCE(e.match_score, 0) DESC, j.created_at DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, {"min_score": min_score}).fetchall()]


def get_job_with_enrichment(job_id: int) -> Optional[dict]:
    sql = """
        SELECT j.*, e.*
        FROM jobs j
        LEFT JOIN enrichments e ON j.id = e.job_id
        WHERE j.id = ?
    """
    with get_conn() as conn:
        row = conn.execute(sql, (job_id,)).fetchone()
        return dict(row) if row else None


# ── Enrichments ───────────────────────────────────────────────────────────────

def save_enrichment(job_id: int, data: dict) -> None:
    """Upsert AI analysis results for a job."""
    sql = """
        INSERT INTO enrichments
            (job_id, match_score, recommendation, summary,
             matched_skills, missing_skills, ats_keywords,
             strongest_match, biggest_gap, experience_gap,
             resume_suggestions, enriched_at)
        VALUES
            (:job_id, :match_score, :recommendation, :summary,
             :matched_skills, :missing_skills, :ats_keywords,
             :strongest_match, :biggest_gap, :experience_gap,
             :resume_suggestions, datetime('now'))
        ON CONFLICT(job_id) DO UPDATE SET
            match_score        = excluded.match_score,
            recommendation     = excluded.recommendation,
            summary            = excluded.summary,
            matched_skills     = excluded.matched_skills,
            missing_skills     = excluded.missing_skills,
            ats_keywords       = excluded.ats_keywords,
            strongest_match    = excluded.strongest_match,
            biggest_gap        = excluded.biggest_gap,
            experience_gap     = excluded.experience_gap,
            resume_suggestions = excluded.resume_suggestions,
            enriched_at        = datetime('now')
    """
    row = {
        "job_id": job_id,
        "match_score": data.get("score", 0),
        "recommendation": data.get("recommendation", "maybe"),
        "summary": data.get("summary", ""),
        "matched_skills": json.dumps(data.get("matched_skills", [])),
        "missing_skills": json.dumps(data.get("missing_skills", [])),
        "ats_keywords": json.dumps(data.get("ats_keywords", [])),
        "strongest_match": data.get("strongest_match", ""),
        "biggest_gap": data.get("biggest_gap", ""),
        "experience_gap": data.get("experience_gap", "none"),
        "resume_suggestions": json.dumps(data.get("resume_suggestions", [])),
    }
    with get_conn() as conn:
        conn.execute(sql, row)


def save_cover_letter(job_id: int, cover_letter: str) -> None:
    sql = """
        UPDATE enrichments
        SET cover_letter = ?, cover_letter_at = datetime('now'), notion_synced_cover = 0
        WHERE job_id = ?
    """
    with get_conn() as conn:
        conn.execute(sql, (cover_letter, job_id))


def save_recruiter_info(job_id: int, email: str, name: str, email_draft: str) -> None:
    sql = """
        UPDATE enrichments
        SET recruiter_email = ?, recruiter_name = ?,
            email_draft = ?, email_found_at = datetime('now'),
            notion_synced_email = 0
        WHERE job_id = ?
    """
    with get_conn() as conn:
        conn.execute(sql, (email, name, email_draft, job_id))


# ── Notion sync tracking ──────────────────────────────────────────────────────

def update_notion_page_id(job_id: int, page_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET notion_page_id = ? WHERE id = ?",
            (page_id, job_id),
        )


def mark_notion_synced(job_id: int, section: str) -> None:
    """section: 'analysis' | 'cover' | 'email'"""
    col = f"notion_synced_{section}"
    with get_conn() as conn:
        conn.execute(f"UPDATE enrichments SET {col} = 1 WHERE job_id = ?", (job_id,))


def reset_notion_analysis_flags() -> int:
    """
    Clear notion_synced_analysis for all enriched jobs so the
    updated ATS analysis gets re-appended on next sync.
    Returns number of rows reset.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE enrichments SET notion_synced_analysis = 0 WHERE notion_synced_analysis = 1"
        )
        return cur.rowcount


# ── Statistics ────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        enriched = conn.execute(
            "SELECT COUNT(*) FROM enrichments WHERE match_score > 0"
        ).fetchone()[0]
        apply_count = conn.execute(
            "SELECT COUNT(*) FROM enrichments WHERE recommendation='apply'"
        ).fetchone()[0]
        maybe_count = conn.execute(
            "SELECT COUNT(*) FROM enrichments WHERE recommendation='maybe'"
        ).fetchone()[0]
        with_email = conn.execute(
            "SELECT COUNT(*) FROM enrichments WHERE recruiter_email IS NOT NULL"
        ).fetchone()[0]
        with_cover = conn.execute(
            "SELECT COUNT(*) FROM enrichments WHERE cover_letter IS NOT NULL"
        ).fetchone()[0]
        avg_score = conn.execute(
            "SELECT ROUND(AVG(match_score),1) FROM enrichments WHERE match_score > 0"
        ).fetchone()[0] or 0
        top_jobs = conn.execute("""
            SELECT j.title, j.company, j.location, e.match_score, e.recommendation
            FROM jobs j JOIN enrichments e ON j.id = e.job_id
            WHERE e.match_score > 0
            ORDER BY e.match_score DESC LIMIT 5
        """).fetchall()

    return {
        "total": total,
        "enriched": enriched,
        "apply": apply_count,
        "maybe": maybe_count,
        "with_email": with_email,
        "with_cover": with_cover,
        "avg_score": avg_score,
        "top_jobs": [dict(r) for r in top_jobs],
    }


def list_jobs(
    min_score: int = 0,
    recommendation: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    conditions = ["1=1"]
    params: list = []

    if min_score:
        conditions.append("COALESCE(e.match_score, 0) >= ?")
        params.append(min_score)
    if recommendation:
        conditions.append("e.recommendation = ?")
        params.append(recommendation)

    where = " AND ".join(conditions)
    sql = f"""
        SELECT j.id, j.title, j.company, j.location, j.source, j.url,
               j.date_posted, j.created_at,
               COALESCE(e.match_score, 0) as match_score,
               COALESCE(e.recommendation, 'pending') as recommendation,
               e.summary, e.recruiter_email
        FROM jobs j
        LEFT JOIN enrichments e ON j.id = e.job_id
        WHERE {where}
        ORDER BY match_score DESC, j.created_at DESC
        LIMIT ?
    """
    params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
