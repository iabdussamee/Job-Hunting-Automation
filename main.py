"""
main.py — Job Hunter CLI

Commands:
  python main.py run            Full pipeline (scrape + enrich + email + sync)
  python main.py scrape         Scrape new jobs
  python main.py enrich         AI-score all unenriched jobs
  python main.py covers         Generate cover letters for top matches
  python main.py emails         Find recruiter emails for top matches
  python main.py sync           Push everything to Notion
  python main.py cover <id>     Generate cover letter for a specific job
  python main.py list           List jobs in the terminal
  python main.py stats          Show pipeline statistics
  python main.py schedule       Start the daily scheduler (blocks — run with nohup)
"""
import json
import sys
import time

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich import box

import database as db
from config import config

console = Console()


# ── Validation guard ──────────────────────────────────────────────────────────

def _check_config():
    errors = config.validate()
    if errors:
        console.print("\n[bold red]⚠ Configuration errors:[/]")
        for e in errors:
            console.print(f"  • {e}")
        console.print("\nFix these in your [bold].env[/] file then retry.\n")
        sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rec_style(rec: str) -> str:
    return {
        "apply": "[green]✅ Apply[/]",
        "maybe": "[yellow]🟡 Maybe[/]",
        "skip":  "[red]❌ Skip[/]",
    }.get((rec or "").lower(), "[dim]⏳ Pending[/]")


def _score_bar(score: int, width: int = 10) -> str:
    filled = score // (100 // width)
    return "█" * filled + "░" * (width - filled)


# ── Commands ──────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """🎯 Job Hunter — Automated job search, ATS matching & Notion dashboard"""
    pass


@cli.command()
def scrape():
    """Scrape new job listings from all configured locations."""
    _check_config()
    console.print(Panel.fit("🔍 Scraping Jobs", style="bold cyan"))
    console.print(f"  Locations:    {', '.join(config.LOCATIONS)}")
    console.print(f"  Search terms: {', '.join(config.SEARCH_TERMS)}")
    console.print(f"  Sites:        {', '.join(config.JOB_SITES)}")
    console.print(f"  Hours old:    {config.HOURS_OLD}h\n")

    db.init_db()
    from scraper import scrape_all
    result = scrape_all(show_progress=True)

    console.print(
        f"\n[bold green]✓[/] Scraped [cyan]{result['scraped']}[/] jobs — "
        f"[green]{result['new']} new[/], [dim]{result['skipped']} duplicates[/]"
    )


@cli.command()
@click.option("--limit",   default=0,     help="Max jobs to process (0 = all)")
@click.option("--rescore", is_flag=True,  help="Re-score ALL jobs, including already-scored ones")
def enrich(limit, rescore):
    """AI-score jobs against your resume. Use --rescore to re-run on all jobs."""
    _check_config()
    console.print(Panel.fit("🤖 AI Enrichment", style="bold magenta"))

    db.init_db()
    from ai_engine import AIEngine

    jobs = db.get_unenriched_jobs(rescore=rescore)
    if limit:
        jobs = jobs[:limit]

    if not jobs:
        if rescore:
            console.print("[yellow]No jobs found in the database.[/] Run [bold]scrape[/] first.")
        else:
            console.print("[yellow]All jobs are already scored.[/] Use [bold]--rescore[/] to re-run on all jobs.")
        return

    mode = "[yellow]RE-SCORING ALL[/]" if rescore else "[cyan]new only[/]"
    console.print(f"  Mode: {mode} — [cyan]{len(jobs)}[/] jobs queued")

    if rescore:
        reset = db.reset_notion_analysis_flags()
        console.print(f"  [dim]Reset {reset} Notion analysis flags — updated scores will sync on next run[/]")

    console.print(f"  Loading resume: [cyan]{config.RESUME_PATH}[/]")
    try:
        engine = AIEngine(config.RESUME_PATH)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        return

    console.print(f"  Scoring [cyan]{len(jobs)}[/] jobs with [magenta]{config.GEMINI_MODEL}[/]...\n")

    apply_count = maybe_count = skip_count = error_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=20),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching...", total=len(jobs))

        for job in jobs:
            label = f"{job.get('title', '?')[:40]} @ {job.get('company', '?')[:20]}"
            progress.update(task, description=f"[magenta]Scoring[/] {label}")

            try:
                result = engine.score_job(job)
                db.save_enrichment(job["id"], result)

                rec = result.get("recommendation", "maybe")
                score = result.get("score", 0)
                icon = {"apply": "✅", "maybe": "🟡", "skip": "❌"}.get(rec, "?")

                progress.console.print(
                    f"  {icon} [dim]{score:>3}/100[/]  {label}"
                )

                if rec == "apply":
                    apply_count += 1
                elif rec == "maybe":
                    maybe_count += 1
                else:
                    skip_count += 1

            except Exception as e:
                error_count += 1
                progress.console.print(f"  [red]⚠[/] Error on '{label}': {e}")

            progress.advance(task)

    console.print(f"\n[bold]Results:[/]  ✅ {apply_count} apply  🟡 {maybe_count} maybe  ❌ {skip_count} skip  ⚠ {error_count} errors")


@cli.command()
@click.option("--min-score", default=65, help="Minimum match score to generate for (default: 65)")
@click.option("--limit", default=10, help="Max cover letters to generate")
def covers(min_score, limit):
    """Generate cover letters for your top job matches."""
    _check_config()
    console.print(Panel.fit("✉️ Cover Letter Generator", style="bold blue"))

    db.init_db()
    from ai_engine import AIEngine

    jobs = db.get_jobs_without_cover_letter()
    jobs = [j for j in jobs if (j.get("match_score") or 0) >= min_score][:limit]

    if not jobs:
        console.print(f"[yellow]No jobs with score ≥ {min_score} need cover letters.[/]")
        return

    console.print(f"  Generating cover letters for [cyan]{len(jobs)}[/] jobs...\n")

    try:
        engine = AIEngine(config.RESUME_PATH)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        return

    for job in jobs:
        label = f"{job.get('title', '?')} @ {job.get('company', '?')}"
        console.print(f"  ✍️  {label}...", end=" ")
        try:
            letter = engine.generate_cover_letter(job)
            db.save_cover_letter(job["id"], letter)
            console.print("[green]done[/]")
        except Exception as e:
            console.print(f"[red]error: {e}[/]")

    console.print(f"\n[green]✓[/] Cover letters saved. Run [bold]sync[/] to push to Notion.")


@cli.command("cover")
@click.argument("job_id", type=int)
def cover_single(job_id):
    """Generate a cover letter for a specific job by ID."""
    _check_config()
    db.init_db()

    job = db.get_job_with_enrichment(job_id)
    if not job:
        console.print(f"[red]Job #{job_id} not found.[/]")
        return

    console.print(f"\n✍️  Generating cover letter for: [bold]{job['title']}[/] @ {job['company']}\n")

    from ai_engine import AIEngine
    try:
        engine = AIEngine(config.RESUME_PATH)
        letter = engine.generate_cover_letter(job)
        db.save_cover_letter(job_id, letter)
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")
        return

    console.print(Panel(letter, title="📄 Cover Letter", border_style="blue"))
    console.print("\n[green]✓[/] Saved to database. Run [bold]sync[/] to push to Notion.")


@cli.command()
@click.option("--limit", default=20, help="Max jobs to attempt email lookup for")
def emails(limit):
    """Find recruiter emails for your top job matches."""
    _check_config()
    console.print(Panel.fit("📧 Recruiter Email Finder", style="bold yellow"))

    if not config.has_email_finder:
        console.print(
            "[yellow]No email-finder API keys configured.[/]\n"
            "Add [bold]HUNTER_API_KEY[/] or [bold]APOLLO_API_KEY[/] to .env\n"
            "  • Hunter.io: 25 free searches/month — hunter.io\n"
            "  • Apollo.io: 50 free credits/month — apollo.io"
        )
        return

    db.init_db()
    from ai_engine import AIEngine
    from email_finder import find_recruiter

    jobs = db.get_jobs_without_recruiter_email()[:limit]

    if not jobs:
        console.print("[yellow]No eligible jobs need recruiter emails.[/]")
        return

    console.print(f"  Looking up emails for [cyan]{len(jobs)}[/] jobs...\n")
    engine = AIEngine(config.RESUME_PATH)

    found = 0
    for job in jobs:
        label = f"{job.get('title', '?')[:40]} @ {job.get('company', '?')[:25]}"
        console.print(f"  🔍 {label}...", end=" ")

        result = find_recruiter(job.get("company", ""), job.get("url", ""))

        if result.get("email"):
            email = result["email"]
            name = result["name"]
            console.print(f"[green]found[/] → {email} ({result['source']})")

            # Generate email draft
            draft = ""
            try:
                draft = engine.generate_email_draft(job, recruiter_name=name)
            except Exception:
                pass

            db.save_recruiter_info(job["id"], email, name, draft)
            found += 1
        else:
            console.print("[dim]not found[/]")

        time.sleep(1)  # Be polite to APIs

    console.print(f"\n[green]✓[/] Found emails for {found}/{len(jobs)} jobs. Run [bold]sync[/] to push to Notion.")


@cli.command()
def sync():
    """Push all jobs and enrichment data to Notion."""
    _check_config()
    console.print(Panel.fit("📓 Syncing to Notion", style="bold green"))

    db.init_db()
    from notion_sync import sync_all

    result = sync_all(verbose=True)

    console.print(
        f"\n[bold green]✓[/] Sync complete — "
        f"[cyan]{result['created']}[/] created, "
        f"[blue]{result['updated']}[/] updated, "
        f"[magenta]{result['sections_added']}[/] sections added, "
        f"[red]{result['errors']}[/] errors"
    )


@cli.command("clean-notion")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def clean_notion(yes):
    """Delete all pages from Notion and clear notion_page_id from the local DB so you can resync fresh."""
    _check_config()
    db.init_db()

    if not yes:
        click.confirm(
            "⚠️  This will DELETE all pages in your Notion database. Continue?",
            abort=True,
        )

    from notion_sync import ensure_database
    from notion_client import Client

    notion = Client(auth=config.NOTION_TOKEN)
    database_id = ensure_database()

    console.print("\n\U0001F5D1\uFE0F  Fetching all pages from Notion...")

    deleted = 0
    errors  = 0
    cursor  = None

    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        response = notion.request(
            path=f"databases/{database_id}/query",
            method="POST",
            body=body,
        )
        pages = response.get("results", [])

        for page in pages:
            try:
                notion.pages.update(page_id=page["id"], archived=True)
                deleted += 1
            except Exception as e:
                errors += 1
                console.print(f"  [red]⚠ Could not delete page {page['id']}: {e}[/]")

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    # Clear notion_page_id from local DB so sync recreates all pages
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET notion_page_id = NULL")

    # Reset notion sync flags so all content sections get re-added
    with db.get_conn() as conn:
        conn.execute("UPDATE enrichments SET notion_synced_analysis = 0, notion_synced_cover = 0, notion_synced_email = 0")

    console.print(f"[bold green]✓[/] Deleted [cyan]{deleted}[/] pages, [red]{errors}[/] errors.")
    console.print("  Run [bold]python main.py sync[/] to push everything fresh.")


@cli.command()
@click.option("--filter", "rec_filter", default=None,
              type=click.Choice(["apply", "maybe", "skip"]),
              help="Filter by recommendation")
@click.option("--min-score", default=0, help="Minimum match score")
@click.option("--limit", default=30, help="Max rows to show")
def list(rec_filter, min_score, limit):
    """List jobs in a formatted terminal table."""
    db.init_db()
    jobs = db.list_jobs(min_score=min_score, recommendation=rec_filter, limit=limit)

    if not jobs:
        console.print("[yellow]No jobs found.[/] Run [bold]scrape[/] first.")
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False)
    table.add_column("#",       width=5,  style="dim")
    table.add_column("Score",   width=7,  justify="center")
    table.add_column("Rec",     width=10)
    table.add_column("Title",   width=32, no_wrap=True)
    table.add_column("Company", width=22, no_wrap=True)
    table.add_column("Location",width=22, no_wrap=True)
    table.add_column("Source",  width=10)
    table.add_column("Posted",  width=12, style="dim")

    for job in jobs:
        score = job.get("match_score", 0) or 0
        rec   = (job.get("recommendation") or "pending").lower()
        score_str = f"{score:>3}/100" if score else "  —  "
        rec_str   = {"apply":"✅", "maybe":"🟡", "skip":"❌"}.get(rec, "⏳")

        table.add_row(
            str(job["id"]),
            score_str,
            rec_str,
            (job.get("title") or "")[:32],
            (job.get("company") or "")[:22],
            (job.get("location") or "")[:22],
            (job.get("source") or "")[:10],
            (job.get("date_posted") or "")[:10],
        )

    console.print(table)
    console.print(f"[dim]Showing {len(jobs)} jobs. Use --filter and --min-score to narrow.[/]")


@cli.command()
def stats():
    """Show pipeline statistics."""
    db.init_db()
    s = db.get_stats()

    console.print(Panel.fit("📊 Job Hunt Statistics", style="bold cyan"))
    console.print(f"  Total jobs scraped:   [cyan]{s['total']}[/]")
    console.print(f"  AI-enriched:          [cyan]{s['enriched']}[/]")
    console.print(f"  Avg match score:      [cyan]{s['avg_score']}/100[/]")
    console.print(f"  Recommended (apply):  [green]{s['apply']}[/]")
    console.print(f"  Recommended (maybe):  [yellow]{s['maybe']}[/]")
    console.print(f"  Cover letters ready:  [blue]{s['with_cover']}[/]")
    console.print(f"  Recruiter emails:     [magenta]{s['with_email']}[/]")

    if s["top_jobs"]:
        console.print("\n  [bold]Top Matches:[/]")
        for j in s["top_jobs"]:
            icon = {"apply": "✅", "maybe": "🟡", "skip": "❌"}.get(
                j.get("recommendation", ""), "⏳"
            )
            console.print(
                f"    {icon} {j['match_score']:>3}/100  "
                f"{j['title'][:35]:<35}  {j['company'][:25]}"
            )


@cli.command()
@click.option("--skip-scrape",   is_flag=True, help="Skip scraping (use existing jobs)")
@click.option("--skip-emails",   is_flag=True, help="Skip recruiter email lookup")
@click.option("--skip-covers",   is_flag=True, help="Skip cover letter generation")
@click.option("--skip-sync",     is_flag=True, help="Skip Notion sync")
@click.option("--enrich-limit",  default=50,   help="Max jobs to enrich per run")
@click.option("--cover-limit",   default=10,   help="Max cover letters to generate")
def run(skip_scrape, skip_emails, skip_covers, skip_sync, enrich_limit, cover_limit):
    """Run the full pipeline: scrape → enrich → covers → emails → sync."""
    _check_config()
    db.init_db()

    console.print(Panel("🎯 Job Hunter — Full Pipeline", style="bold cyan"))
    start = time.time()

    # 1. Scrape
    if not skip_scrape:
        console.rule("[cyan]Step 1/5 — Scraping[/]")
        from scraper import scrape_all
        r = scrape_all(show_progress=True)
        console.print(f"  → {r['new']} new jobs added\n")

    # 2. Enrich
    console.rule("[magenta]Step 2/5 — AI Enrichment[/]")
    from ai_engine import AIEngine
    jobs_to_enrich = db.get_unenriched_jobs()[:enrich_limit]
    if jobs_to_enrich:
        try:
            engine = AIEngine(config.RESUME_PATH)
            a = m = s = err = 0
            for job in jobs_to_enrich:
                try:
                    result = engine.score_job(job)
                    db.save_enrichment(job["id"], result)
                    rec = result.get("recommendation", "maybe")
                    if rec == "apply": a += 1
                    elif rec == "maybe": m += 1
                    else: s += 1
                except Exception as e:
                    err += 1
                    console.print(f"  [red]⚠ Error:[/] {e}")
            console.print(f"  → ✅ {a} apply  🟡 {m} maybe  ❌ {s} skip  ⚠ {err} errors\n")
        except Exception as e:
            console.print(f"  [red]AI engine error: {e}[/]\n")
    else:
        console.print("  → No new jobs to enrich\n")
        engine = None

    # 3. Cover letters
    if not skip_covers:
        console.rule("[blue]Step 3/5 — Cover Letters[/]")
        if engine is None:
            try:
                engine = AIEngine(config.RESUME_PATH)
            except Exception:
                engine = None
        if engine:
            jobs_for_covers = db.get_jobs_without_cover_letter()[:cover_limit]
            count = 0
            for job in jobs_for_covers:
                try:
                    letter = engine.generate_cover_letter(job)
                    db.save_cover_letter(job["id"], letter)
                    count += 1
                except Exception:
                    pass
            console.print(f"  → {count} cover letters generated\n")
        else:
            console.print("  → Skipped (AI engine unavailable)\n")

    # 4. Emails
    if not skip_emails and config.has_email_finder:
        console.rule("[yellow]Step 4/5 — Recruiter Emails[/]")
        from email_finder import find_recruiter
        email_jobs = db.get_jobs_without_recruiter_email()[:20]
        found = 0
        if engine is None:
            try:
                engine = AIEngine(config.RESUME_PATH)
            except Exception:
                engine = None
        for job in email_jobs:
            result = find_recruiter(job.get("company", ""), job.get("url", ""))
            if result.get("email"):
                draft = ""
                if engine:
                    try:
                        draft = engine.generate_email_draft(job, result["name"])
                    except Exception:
                        pass
                db.save_recruiter_info(job["id"], result["email"], result["name"], draft)
                found += 1
            time.sleep(1)
        console.print(f"  → {found} recruiter emails found\n")

    # 5. Sync to Notion
    if not skip_sync:
        console.rule("[green]Step 5/5 — Notion Sync[/]")
        from notion_sync import sync_all
        r = sync_all(verbose=False)
        console.print(
            f"  → {r['created']} created, {r['updated']} updated, "
            f"{r['sections_added']} sections added\n"
        )

    elapsed = time.time() - start
    console.print(Panel.fit(
        f"[bold green]✓ Pipeline complete in {elapsed:.0f}s[/]",
        style="green"
    ))
    console.print("  Open Notion to see your dashboard 📓")


@cli.command()
def schedule():
    """Start the daily scheduler (runs at 07:00 every day). Use nohup or screen."""
    _check_config()
    from scheduler import start_scheduler
    start_scheduler()


if __name__ == "__main__":
    cli()

