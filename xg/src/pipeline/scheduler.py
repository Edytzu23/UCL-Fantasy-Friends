"""
Scheduler — auto-runs the update pipeline on a schedule.
Uses Python's built-in threading (runs locally, no external deps).

Usage:
    python -m src.pipeline.scheduler
"""

import time
import threading
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.pipeline.update import run_full_update

# ── Schedule config ───────────────────────────────────────────────────
# How often to run each update (seconds)

SCHEDULES = {
    "fotmob_only": {
        "interval_seconds": 6 * 3600,    # Every 6 hours
        "competition_id":   None,
        "skip_fbref":       True,
        "label":            "FotMob stats only",
    },
    "full_ucl": {
        "interval_seconds": 24 * 3600,   # Every 24 hours
        "competition_id":   1,            # UCL competition id (set after seed)
        "scoring_system":   "ucl",
        "skip_fbref":       False,
        "label":            "Full UCL pipeline (FotMob + FBref + xPts)",
    },
}


def _run_job(job_name, job_cfg):
    """Run one scheduled job, catching all exceptions."""
    print(f"\n[scheduler] Running: {job_cfg['label']}")
    try:
        run_full_update(
            competition_id=job_cfg.get("competition_id"),
            scoring_system=job_cfg.get("scoring_system", "ucl"),
            skip_fbref=job_cfg.get("skip_fbref", True),
        )
    except Exception as e:
        print(f"[scheduler] {job_name} error: {e}")


def start_scheduler(jobs=None):
    """Start background threads for each scheduled job."""
    if jobs is None:
        jobs = SCHEDULES

    for name, cfg in jobs.items():
        def make_loop(job_name, job_cfg):
            def loop():
                while True:
                    _run_job(job_name, job_cfg)
                    time.sleep(job_cfg["interval_seconds"])
            return loop

        t = threading.Thread(target=make_loop(name, cfg), daemon=True)
        t.start()
        print(f"[scheduler] Started: {name} (every {cfg['interval_seconds']//3600}h)")


def run_once(job_name="full_ucl"):
    """Run a single job immediately (for testing or manual trigger)."""
    cfg = SCHEDULES.get(job_name)
    if not cfg:
        print(f"[scheduler] Unknown job: {job_name}")
        return
    _run_job(job_name, cfg)


if __name__ == "__main__":
    # Run full update once then start scheduler
    run_once("full_ucl")
    start_scheduler()

    # Keep main thread alive
    print("\n[scheduler] Running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[scheduler] Stopped.")
