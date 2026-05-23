#!/usr/bin/env python3
"""
monitor.py — Auto-retry failed GitHub Actions workflows across all Peacoat sites.
Runs daily from Peacoat-git/softlinen via monitor.yml.
"""

import os
import sys
import requests
from datetime import datetime, timezone

GH_TOKEN = os.environ["GH_TOKEN"]
HEADERS = {
    "Authorization": f"token {GH_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

REPOS = [
    ("peacoat-sites", "medicare-starter"),
    ("peacoat-sites", "solar-planner-guide"),
    ("peacoat-sites", "solar-home-planner"),
    ("peacoat-sites", "injury-victim-guide"),
    ("peacoat-sites", "home-insurance-guide"),
    ("peacoat-sites", "mortgage-advisor-guide"),
    ("peacoat-sites", "therapy-finder-guide"),
    ("peacoat-sites", "pet-doctor-guide"),
    ("peacoat-sites", "small-biz-finance-guide"),
    ("peacoat-sites", "keto-living-guide"),
    ("peacoat-sites", "chicken-keeper-guide"),
    ("peacoat-sites", "rv-life-guide"),
    ("peacoat-sites", "gamedevproducer"),
    ("peacoat-sites", "seniorstrength"),
    ("peacoat-sites", "fixitrightway"),
    ("Peacoat-git", "seniorstrength-pipeline"),
    ("Peacoat-git", "fixitrightway-pipeline"),
]

def get_latest_run_per_workflow(org, repo):
    """Return the most recent run for each distinct workflow in the repo."""
    url = f"https://api.github.com/repos/{org}/{repo}/actions/runs?per_page=30"
    r = requests.get(url, headers=HEADERS, timeout=15)
    if r.status_code != 200:
        print(f"  ⚠️  {org}/{repo} — API error {r.status_code}")
        return []
    seen = {}
    for run in r.json().get("workflow_runs", []):
        wid = run["workflow_id"]
        if wid not in seen:
            seen[wid] = run
    return list(seen.values())

def rerun_failed_jobs(org, repo, run_id):
    """Trigger a rerun of only the failed jobs in a run."""
    url = f"https://api.github.com/repos/{org}/{repo}/actions/runs/{run_id}/rerun-failed-jobs"
    r = requests.post(url, headers=HEADERS, timeout=15)
    return r.status_code in (201, 204)

def main():
    now = datetime.now(timezone.utc)
    healthy, retried, errors = [], [], []

    print(f"\n{'='*62}")
    print(f"  Peacoat Pipeline Monitor — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*62}")

    for org, repo in REPOS:
        runs = get_latest_run_per_workflow(org, repo)
        if not runs:
            errors.append(f"{org}/{repo} (no runs / API error)")
            continue

        for run in runs:
            wf_name   = run["name"]
            conclusion = run.get("conclusion")
            status     = run.get("status")
            run_id     = run["id"]
            created_at = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            age_min    = (now - created_at).total_seconds() / 60

            label = f"{repo} / {wf_name}"

            # Still running — skip
            if status in ("in_progress", "queued", "waiting"):
                print(f"  ⏳ {label} — in progress")
                continue

            if conclusion == "failure":
                # Retry window: failed between 10 min and 25 hours ago
                # (avoids retry loops and very stale failures)
                if 10 < age_min < 1500:
                    ok = rerun_failed_jobs(org, repo, run_id)
                    if ok:
                        print(f"  🔄 {label} — failed → retried (run {run_id})")
                        retried.append(label)
                    else:
                        print(f"  ❌ {label} — failed, retry request failed")
                        errors.append(label)
                else:
                    print(f"  ⏭️  {label} — failed but outside retry window ({age_min:.0f}m old)")
            elif conclusion in ("success", "skipped"):
                print(f"  ✅ {label} — {conclusion}")
                healthy.append(label)
            else:
                print(f"  ❓ {label} — {conclusion or status}")

    print(f"\n{'='*62}")
    print(f"  ✅ Healthy : {len(healthy)}")
    print(f"  🔄 Retried : {len(retried)}")
    print(f"  ❌ Errors  : {len(errors)}")
    if retried:
        print(f"\n  Workflows retried:")
        for r in retried:
            print(f"    • {r}")
    if errors:
        print(f"\n  Errors / unreachable:")
        for e in errors:
            print(f"    • {e}")
    print(f"{'='*62}\n")

if __name__ == "__main__":
    main()
