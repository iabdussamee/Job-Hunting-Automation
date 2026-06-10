"""
ai_engine.py — All Gemini AI calls: ATS scoring, cover letters,
               resume suggestions, and email drafts.

Model: configurable via GEMINI_MODEL (defaults to gemini-2.5-flash)
"""
import json
import re
import time
from typing import Optional

from google import genai
from google.genai import types

from config import config
from resume_parser import get_resume_summary

# ── Setup ─────────────────────────────────────────────────────────────────────

_client = genai.Client(api_key=config.GEMINI_API_KEY)

_JSON_CONFIG = types.GenerateContentConfig(
    temperature=0.1,           # Low temp = consistent, deterministic JSON
    response_mime_type="application/json",
)
_TEXT_CONFIG = types.GenerateContentConfig(
    temperature=0.7,           # Slightly higher for cover letters / prose
)

# Seconds to wait between API calls to respect free-tier rate limit (15 RPM)
_RATE_LIMIT_SLEEP = 4.5


class GeminiAPIError(RuntimeError):
    """Raised when Gemini returns an API error."""


class GeminiRateLimitError(GeminiAPIError):
    """Raised when Gemini rate/quota limits remain after retries."""


# ── Prompts ───────────────────────────────────────────────────────────────────

_ATS_PROMPT = """
You are an expert ATS (Applicant Tracking System) analyzer and senior technical recruiter.
Your job is to evaluate how well a candidate's resume matches a job description.

RESUME:
{resume}

JOB TITLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{description}

Return ONLY a valid JSON object with exactly these keys (no markdown, no explanation):
{{
  "score": <integer 0-100>,
  "recommendation": "<apply|maybe|skip>",
  "summary": "<single sentence verdict, max 20 words>",
  "matched_skills": ["<skill or experience present in both>", ...],
  "missing_skills": ["<required skill absent from resume>", ...],
  "ats_keywords": ["<keyword/phrase in JD but not in resume>", ...],
  "experience_gap": "<none|junior|senior>",
  "strongest_match": "<candidate's best qualification for this role, max 15 words>",
  "biggest_gap": "<most critical missing requirement, max 15 words>",
  "resume_suggestions": [
    {{"section": "<Summary|Experience|Skills|Education>", "issue": "<what is weak or missing>", "fix": "<specific rewrite suggestion>"}}
  ]
}}

Scoring guide:
  85-100  Exceptional fit — apply immediately
  70-84   Strong fit — definitely apply
  55-69   Moderate fit — worth applying with tailoring
  40-54   Weak fit — significant gaps exist
  0-39    Poor fit — core requirements missing

Rules:
- matched_skills: list actual skills/experiences that appear in BOTH documents
- missing_skills: only hard requirements missing from resume (max 8)
- ats_keywords: exact keywords/phrases the ATS would flag as missing (max 10)
- resume_suggestions: max 5 specific, actionable rewrites ordered by priority
- Be realistic. 75+ should mean genuinely strong fit.
"""

_COVER_LETTER_PROMPT = """
Write a professional, personalized cover letter for this job application.

APPLICANT RESUME:
{resume}

JOB TITLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{description}

Guidelines:
- Exactly 3 paragraphs
- Paragraph 1: Strong opening with genuine enthusiasm + specific knowledge of the company/role (avoid "I am writing to express my interest")
- Paragraph 2: Connect 2–3 SPECIFIC achievements from the resume to KEY requirements in this JD. Use numbers/metrics where available.
- Paragraph 3: Brief closing with a clear, confident call to action
- Tone: Professional, confident, human — not robotic or generic
- Length: 250–320 words total
- Use the actual company name and role title
- Do NOT include salutation or signature (those will be added separately)

Write only the cover letter body. No extra commentary.
"""

_EMAIL_DRAFT_PROMPT = """
Write a short, personalized cold outreach email to a recruiter about a job opening.

MY ROLE: {title}
COMPANY: {company}
RECRUITER NAME: {recruiter_name}
KEY HIGHLIGHTS FROM MY RESUME: {resume_highlights}

Requirements:
- Subject line + body (separated by a blank line)
- Subject: short, specific, professional
- Body: 3–4 sentences only
- Sentence 1: Introduce yourself + mention the role
- Sentence 2: One specific achievement relevant to the company/role
- Sentence 3: Clear, polite ask (15-minute call or application review)
- NO attachments reference, NO "I hope this email finds you well"
- Tone: Confident but not pushy

Format exactly as:
Subject: [subject line here]

[email body here]
"""

_RESUME_HIGHLIGHTS_PROMPT = """
Extract 3–4 key achievements and skills from this resume as a brief comma-separated list.
Focus on metrics, technologies, and impact. Max 80 words total.
Return only the list, no explanation.

RESUME:
{resume}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_gemini_error(error: Exception, max_chars: int = 700) -> str:
    """Return a compact, redacted Gemini error for CLI output."""
    message = str(error).strip() or error.__class__.__name__
    if config.GEMINI_API_KEY:
        message = message.replace(config.GEMINI_API_KEY, "[redacted]")
    message = re.sub(r"\s+", " ", message)
    if len(message) > max_chars:
        return message[: max_chars - 3] + "..."
    return message


def _is_rate_or_quota_error(error: Exception) -> bool:
    err = str(error).lower()
    return any(
        marker in err
        for marker in (
            "429",
            "quota",
            "rate limit",
            "rate_limit",
            "resource_exhausted",
            "resource exhausted",
        )
    )


def _is_retryable_error(error: Exception) -> bool:
    err = str(error).lower()
    return _is_rate_or_quota_error(error) or any(
        marker in err
        for marker in (
            "500",
            "503",
            "deadline",
            "temporarily unavailable",
            "timeout",
            "timed out",
            "connection",
        )
    )


def _call(prompt: str, json_mode: bool = True, retries: int = 3) -> str:
    """Call Gemini with retry logic and useful final errors."""
    cfg = _JSON_CONFIG if json_mode else _TEXT_CONFIG
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            response = _client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config=cfg,
            )
            time.sleep(_RATE_LIMIT_SLEEP)
            if not response.text:
                raise GeminiAPIError("Gemini returned an empty response")
            return response.text
        except Exception as e:
            last_error = e
            if attempt >= retries or not _is_retryable_error(e):
                detail = _format_gemini_error(e)
                if _is_rate_or_quota_error(e):
                    raise GeminiRateLimitError(
                        f"Gemini rate/quota limit after {attempt} attempts: {detail}"
                    ) from e
                raise GeminiAPIError(f"Gemini API request failed: {detail}") from e

            wait = attempt * 30 if _is_rate_or_quota_error(e) else 5
            time.sleep(wait)

    detail = _format_gemini_error(last_error) if last_error else "unknown error"
    raise GeminiAPIError(f"Gemini API request failed after retries: {detail}")


def _parse_json(raw: str) -> dict | list:
    """Robustly parse JSON from LLM output (handles stray markdown fences)."""
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    # Find the first JSON object or array
    match = re.search(r"[\[{].*[\]}]", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(cleaned)


def _truncate_jd(description: str, max_chars: int = 6000) -> str:
    """Truncate job description to stay within token budget."""
    if len(description) <= max_chars:
        return description
    # Cut at a paragraph boundary if possible
    truncated = description[:max_chars]
    last_para = truncated.rfind("\n\n")
    if last_para > max_chars * 0.7:
        return truncated[:last_para] + "\n\n[... description truncated ...]"
    return truncated + "\n[... description truncated ...]"


# ── Public API ────────────────────────────────────────────────────────────────

class AIEngine:
    """
    All AI operations using Gemini.

    Usage:
        engine = AIEngine(resume_text)
        result = engine.score_job(job)
        letter = engine.generate_cover_letter(job)
    """

    def __init__(self, resume_path: str):
        from resume_parser import extract_text
        self.resume_text = extract_text(resume_path)
        self.resume_summary = get_resume_summary(self.resume_text, max_chars=4000)
        self._highlights_cache: Optional[str] = None

    # ── ATS Scoring ───────────────────────────────────────────────────────────

    def score_job(self, job: dict) -> dict:
        """
        Run ATS analysis on a job. Returns structured dict with score,
        recommendation, skills, gaps, and resume suggestions.
        """
        prompt = _ATS_PROMPT.format(
            resume=self.resume_summary,
            title=job.get("title", ""),
            company=job.get("company", ""),
            description=_truncate_jd(job.get("description", "")),
        )
        raw = _call(prompt, json_mode=True)
        try:
            data = _parse_json(raw)
        except json.JSONDecodeError:
            # Fallback: safe defaults if JSON is malformed
            data = {
                "score": 0,
                "recommendation": "maybe",
                "summary": "Could not parse AI response",
                "matched_skills": [],
                "missing_skills": [],
                "ats_keywords": [],
                "experience_gap": "none",
                "strongest_match": "",
                "biggest_gap": "",
                "resume_suggestions": [],
            }

        # Enforce types
        data["score"] = max(0, min(100, int(data.get("score", 0))))
        data["recommendation"] = str(data.get("recommendation", "maybe")).lower()
        if data["recommendation"] not in ("apply", "maybe", "skip"):
            data["recommendation"] = "maybe"
        for key in ("matched_skills", "missing_skills", "ats_keywords", "resume_suggestions"):
            if not isinstance(data.get(key), list):
                data[key] = []

        return data

    # ── Cover Letter ──────────────────────────────────────────────────────────

    def generate_cover_letter(self, job: dict) -> str:
        """Generate a tailored cover letter for the given job."""
        prompt = _COVER_LETTER_PROMPT.format(
            resume=self.resume_summary,
            title=job.get("title", ""),
            company=job.get("company", ""),
            description=_truncate_jd(job.get("description", ""), max_chars=4000),
        )
        return _call(prompt, json_mode=False).strip()

    # ── Email Draft ───────────────────────────────────────────────────────────

    def generate_email_draft(self, job: dict, recruiter_name: str = "Hiring Manager") -> str:
        """
        Draft a cold outreach email given job details and recruiter name.
        Returns a string with 'Subject: ...' on line 1, blank line, then body.
        """
        if not self._highlights_cache:
            self._highlights_cache = self._get_resume_highlights()

        prompt = _EMAIL_DRAFT_PROMPT.format(
            title=job.get("title", ""),
            company=job.get("company", ""),
            recruiter_name=recruiter_name,
            resume_highlights=self._highlights_cache,
        )
        return _call(prompt, json_mode=False).strip()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_resume_highlights(self) -> str:
        """Extract key bullet points from the resume (cached)."""
        prompt = _RESUME_HIGHLIGHTS_PROMPT.format(resume=self.resume_summary[:2000])
        try:
            return _call(prompt, json_mode=False).strip()
        except Exception:
            return "Experienced professional with strong technical background"
