"""
config.py — Loads and validates all settings from .env
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── AI ────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    # ── Notion ────────────────────────────────────────────────
    NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
    NOTION_DB_ID: str = os.getenv("NOTION_DB_ID", "")
    NOTION_PARENT_PAGE_ID: str = os.getenv("NOTION_PARENT_PAGE_ID", "")
    MIN_NOTION_SCORE: int = int(os.getenv("MIN_NOTION_SCORE", "55"))

    # ── Email Finding ─────────────────────────────────────────
    HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")
    APOLLO_API_KEY: str = os.getenv("APOLLO_API_KEY", "")

    # ── Resume ────────────────────────────────────────────────
    RESUME_PATH: str = os.getenv("RESUME_PATH", "resume.pdf")

    # ── Scraping ──────────────────────────────────────────────
    SEARCH_TERMS: list = [
        t.strip()
        for t in os.getenv("SEARCH_TERMS", "software engineer").split(",")
        if t.strip()
    ]
    LOCATIONS: list = [
        loc.strip()
        for loc in os.getenv("LOCATIONS", "Remote").split("|")
        if loc.strip()
    ]
    JOB_SITES: list = [
        s.strip()
        for s in os.getenv("JOB_SITES", "linkedin,indeed").split(",")
        if s.strip()
    ]
    RESULTS_PER_LOCATION: int = int(os.getenv("RESULTS_PER_LOCATION", "15"))
    HOURS_OLD: int = int(os.getenv("HOURS_OLD", "72"))

    # ── Storage ───────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "jobs.db")

    def validate(self) -> list[str]:
        """Return a list of configuration errors (empty = all good)."""
        errors = []

        if not self.GEMINI_API_KEY:
            errors.append("GEMINI_API_KEY is missing. Get one free at aistudio.google.com")

        if not self.NOTION_TOKEN:
            errors.append("NOTION_TOKEN is missing. Create one at notion.so/my-integrations")

        if not self.NOTION_DB_ID and not self.NOTION_PARENT_PAGE_ID:
            errors.append(
                "Set either NOTION_DB_ID (existing DB) or NOTION_PARENT_PAGE_ID "
                "(auto-create DB under that page)"
            )

        resume = Path(self.RESUME_PATH)
        if not resume.exists():
            errors.append(
                f"Resume not found at '{self.RESUME_PATH}'. "
                "Set RESUME_PATH in .env to the correct path."
            )

        if not self.SEARCH_TERMS:
            errors.append("SEARCH_TERMS is empty. Add at least one job title.")

        if not self.LOCATIONS:
            errors.append("LOCATIONS is empty. Add at least one location.")

        return errors

    @property
    def has_hunter(self) -> bool:
        return bool(self.HUNTER_API_KEY)

    @property
    def has_apollo(self) -> bool:
        return bool(self.APOLLO_API_KEY)

    @property
    def has_email_finder(self) -> bool:
        return self.has_hunter or self.has_apollo


config = Config()
