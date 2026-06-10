"""
scheduler.py — Runs the full pipeline automatically every day at 07:00.

Usage:
  python main.py schedule          # foreground (Ctrl+C to stop)

  # Run in background (Linux/Mac):
  nohup python main.py schedule > scheduler.log 2>&1 &

  # Or add to cron (no scheduler process needed):
  # 0 7 * * * cd /path/to/job_hunter && python main.py run >> cron.log 2>&1
"""
import subprocess
import sys
import time
from datetime import datetime

import schedule
from rich.console import Console

console = Console()

_RUN_AT = "07:00"   # 24-hour time, local timezone


def _run_pipeline():
    console.print(f"\n[cyan]⏰ Scheduled run at {datetime.now().strftime('%Y-%m-%d %H:%M')}[/]")
    try:
        result = subprocess.run(
            [sys.executable, "main.py", "run"],
            check=False,
            text=True,
        )
        if result.returncode != 0:
            console.print("[yellow]⚠ Pipeline exited with non-zero status[/]")
    except Exception as e:
        console.print(f"[red]Scheduler error: {e}[/]")


def start_scheduler(run_at: str = _RUN_AT):
    console.print(f"[green]🕐 Scheduler started — pipeline will run daily at {run_at}[/]")
    console.print("  Press [bold]Ctrl+C[/] to stop.\n")

    schedule.every().day.at(run_at).do(_run_pipeline)

    # Show next run time
    next_run = schedule.next_run()
    if next_run:
        console.print(f"  Next run: [cyan]{next_run.strftime('%Y-%m-%d %H:%M')}[/]")

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            console.print("\n[yellow]Scheduler stopped.[/]")
            break
