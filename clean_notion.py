"""
clean_notion.py — Delete all pages from your Notion job database.
Uses raw httpx with no notion-client dependency.

Usage:
    python3 clean_notion.py
"""
import os, sys, time
import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.getenv("NOTION_TOKEN", "")
DB_ID   = os.getenv("NOTION_DB_ID", "").replace("-", "")

if not TOKEN or not DB_ID:
    print("❌  Set NOTION_TOKEN and NOTION_DB_ID in .env")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
BASE = "https://api.notion.com/v1"

# ── Confirm ───────────────────────────────────────────────────────────────────
print(f"\n⚠️  This will archive ALL pages in database: {DB_ID}")
answer = input("Type 'yes' to continue: ").strip().lower()
if answer != "yes":
    print("Aborted.")
    sys.exit(0)

# ── Fetch and archive all pages ───────────────────────────────────────────────
print("\n🗑️  Fetching pages...")

deleted = 0
errors  = 0
cursor  = None

with httpx.Client(timeout=30) as client:
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        resp = client.post(
            f"{BASE}/databases/{DB_ID}/query",
            headers=HEADERS,
            json=body,
        )

        if resp.status_code != 200:
            print(f"❌  Query failed {resp.status_code}: {resp.text[:300]}")
            sys.exit(1)

        data  = resp.json()
        pages = data.get("results", [])
        print(f"   Fetched {len(pages)} pages (total so far: {deleted + len(pages)})")

        for page in pages:
            r = client.patch(
                f"{BASE}/pages/{page['id']}",
                headers=HEADERS,
                json={"archived": True},
            )
            if r.status_code == 200:
                deleted += 1
            else:
                errors += 1
                print(f"  ⚠  Failed to archive {page['id']}: {r.status_code}")
            time.sleep(0.35)   # ~3 req/s limit

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

print(f"\n✅  Archived {deleted} pages, {errors} errors.")

# ── Reset local DB tracking ───────────────────────────────────────────────────
import sqlite3
DB_PATH = os.getenv("DB_PATH", "jobs.db")

if os.path.exists(DB_PATH):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE jobs SET notion_page_id = NULL")
    conn.execute(
        "UPDATE enrichments SET "
        "notion_synced_analysis=0, notion_synced_cover=0, notion_synced_email=0"
    )
    conn.commit()
    conn.close()
    print(f"✅  Reset notion_page_id and sync flags in {DB_PATH}")
else:
    print(f"⚠️  {DB_PATH} not found — skipping local DB reset")

print("\n   Run:  python3 main.py sync\n")

