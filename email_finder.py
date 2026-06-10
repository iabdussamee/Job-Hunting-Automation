"""
email_finder.py — Find recruiter email addresses using Hunter.io and Apollo.io.

Free tier limits:
  Hunter.io  — 25 searches / month
  Apollo.io  — 50 credits / month

Strategy:
  1. Extract company domain from job URL or company name heuristic
  2. Try Hunter.io domain search (finds email pattern + people list)
  3. Fall back to Apollo.io people search if Hunter fails or quota exceeded
  4. Return best match with confidence level
"""
import re
import time
from typing import Optional
from urllib.parse import urlparse

import requests

from config import config

_TIMEOUT = 10  # seconds per HTTP call


# ── Domain extraction ─────────────────────────────────────────────────────────

# Well-known company → domain overrides (avoids bad guesses)
_KNOWN_DOMAINS: dict[str, str] = {
    "google": "google.com",
    "microsoft": "microsoft.com",
    "amazon": "amazon.com",
    "meta": "meta.com",
    "apple": "apple.com",
    "netflix": "netflix.com",
    "uber": "uber.com",
    "airbnb": "airbnb.com",
    "twitter": "twitter.com",
    "linkedin": "linkedin.com",
    "salesforce": "salesforce.com",
    "oracle": "oracle.com",
    "ibm": "ibm.com",
    "accenture": "accenture.com",
    "infosys": "infosys.com",
    "wipro": "wipro.com",
    "tcs": "tcs.com",
    "tata consultancy": "tcs.com",
    "hcl": "hcltech.com",
    "cognizant": "cognizant.com",
    "deloitte": "deloitte.com",
    "pwc": "pwc.com",
    "kpmg": "kpmg.com",
}


def _extract_domain_from_url(url: str) -> Optional[str]:
    """Extract root domain from a job listing URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        host = parsed.netloc.lower()
        # Strip www and known job board subdomains
        host = re.sub(r"^(www\.|jobs\.|careers\.|apply\.)", "", host)
        # Skip job board domains
        job_boards = {
            "linkedin.com", "indeed.com", "glassdoor.com",
            "ziprecruiter.com", "google.com", "lever.co",
            "greenhouse.io", "workday.com", "taleo.net",
            "smartrecruiters.com", "icims.com", "myworkdayjobs.com",
        }
        if any(host.endswith(board) for board in job_boards):
            return None
        # Must look like a real domain
        if "." in host and len(host) > 4:
            return host
    except Exception:
        pass
    return None


def _guess_domain_from_company(company: str) -> str:
    """
    Heuristic: try known overrides, then lowercase + .com
    e.g. "Stripe Inc." → "stripe.com"
    """
    name = company.lower().strip()

    # Check known mappings
    for key, domain in _KNOWN_DOMAINS.items():
        if key in name:
            return domain

    # Strip common suffixes
    name = re.sub(
        r"\b(inc\.?|ltd\.?|llc\.?|corp\.?|co\.?|pvt\.?|technologies|tech|solutions|services|group|global|india|uae)\b",
        "",
        name,
        flags=re.IGNORECASE,
    )
    # Keep only alphanumeric chars
    name = re.sub(r"[^a-z0-9]", "", name.strip())
    return f"{name}.com" if name else ""


def get_company_domain(company: str, job_url: str = "") -> str:
    """Best-effort company domain: URL first, then heuristic."""
    url_domain = _extract_domain_from_url(job_url)
    if url_domain:
        return url_domain
    return _guess_domain_from_company(company)


# ── Hunter.io ─────────────────────────────────────────────────────────────────

_HUNTER_BASE = "https://api.hunter.io/v2"

_RECRUITER_TITLES = {
    "recruiter", "talent acquisition", "talent partner",
    "hr manager", "human resources", "people operations",
    "hiring manager", "staffing",
}


def _is_recruiter(position: str) -> bool:
    if not position:
        return False
    pos_lower = position.lower()
    return any(t in pos_lower for t in _RECRUITER_TITLES)


def hunter_find(company: str, domain: str) -> Optional[dict]:
    """
    Search Hunter.io for recruiter emails at a company domain.
    Returns {"email": ..., "name": ..., "confidence": ..., "source": "hunter"}
    or None if not found / quota exceeded.
    """
    if not config.HUNTER_API_KEY or not domain:
        return None

    try:
        resp = requests.get(
            f"{_HUNTER_BASE}/domain-search",
            params={
                "domain": domain,
                "type": "personal",
                "api_key": config.HUNTER_API_KEY,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 429:
            return None  # Quota exceeded
        resp.raise_for_status()
        data = resp.json().get("data", {})
    except Exception:
        return None

    emails = data.get("emails", [])
    if not emails:
        return None

    # Prefer recruiters/HR roles; fall back to first email
    recruiter_emails = [e for e in emails if _is_recruiter(e.get("position", ""))]
    chosen = recruiter_emails[0] if recruiter_emails else emails[0]

    first = chosen.get("first_name", "")
    last = chosen.get("last_name", "")
    name = f"{first} {last}".strip() or "Hiring Manager"

    return {
        "email": chosen.get("value", ""),
        "name": name,
        "confidence": chosen.get("confidence", 0),
        "position": chosen.get("position", ""),
        "source": "hunter",
    }


# ── Apollo.io ─────────────────────────────────────────────────────────────────

_APOLLO_SEARCH_URL = "https://api.apollo.io/v1/mixed_people/search"

_APOLLO_TITLES = [
    "recruiter",
    "talent acquisition",
    "technical recruiter",
    "senior recruiter",
    "hr manager",
    "talent partner",
]


def apollo_find(company: str, domain: str) -> Optional[dict]:
    """
    Search Apollo.io for recruiters at a company.
    Returns {"email": ..., "name": ..., "source": "apollo"} or None.
    """
    if not config.APOLLO_API_KEY:
        return None

    try:
        resp = requests.post(
            _APOLLO_SEARCH_URL,
            headers={"Content-Type": "application/json"},
            json={
                "api_key": config.APOLLO_API_KEY,
                "q_organization_domains": [domain] if domain else [],
                "person_titles": _APOLLO_TITLES,
                "page": 1,
                "per_page": 5,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403, 429):
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    people = data.get("people", [])
    if not people:
        return None

    person = people[0]
    email = person.get("email", "")
    # Apollo may return guessed emails; check confidence
    if not email or email.startswith("*"):
        # Unveil email requires a credit — skip if guessed
        return None

    first = person.get("first_name", "")
    last = person.get("last_name", "")
    name = f"{first} {last}".strip() or "Hiring Manager"

    return {
        "email": email,
        "name": name,
        "confidence": 70,
        "position": person.get("title", ""),
        "source": "apollo",
    }


# ── Main public function ──────────────────────────────────────────────────────

def find_recruiter(company: str, job_url: str = "") -> dict:
    """
    Find a recruiter's email for a company.

    Returns:
        {
          "email":      str or "",
          "name":       str,
          "confidence": 0-100,
          "position":   str,
          "source":     "hunter" | "apollo" | "none",
          "domain":     str,
        }
    """
    domain = get_company_domain(company, job_url)
    result = {
        "email": "",
        "name": "Hiring Manager",
        "confidence": 0,
        "position": "",
        "source": "none",
        "domain": domain,
    }

    if not domain:
        return result

    # Try Hunter first
    if config.has_hunter:
        found = hunter_find(company, domain)
        if found and found.get("email"):
            result.update(found)
            return result

    # Fall back to Apollo
    if config.has_apollo:
        time.sleep(1)  # Brief pause between services
        found = apollo_find(company, domain)
        if found and found.get("email"):
            result.update(found)
            return result

    return result
