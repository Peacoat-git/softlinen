#!/usr/bin/env python3
"""
Portfolio health audit — runs every 12h via GitHub Actions in peacoat-git/softlinen.
Checks each site's publish + video workflow, HTTP status, and article counts.
Writes health.json and opens a GitHub Issue if critical problems are found.
Closes stale open issues when all clear.
"""

import os, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

GH_TOKEN  = os.environ["GITHUB_TOKEN"]
GH_HEADS  = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
AUDIT_REPO = "peacoat-git/softlinen"   # where to open issues

# Max acceptable age (hours) before flagging as stale
PUBLISH_STALE_H = 30   # should run daily; flag if >30h
VIDEO_STALE_H   = 120  # runs Mon/Wed/Fri; max gap ~96h, flag at 120h

SITES = [
    {"slug": "medicare-starter",        "domain": "medicarestarter.com"},
    {"slug": "solar-planner-guide",     "domain": "solarplannerguide.com"},
    {"slug": "solar-home-planner",      "domain": "solarhomeplanner.com"},
    {"slug": "injury-victim-guide",     "domain": "injuryvictimguide.com"},
    {"slug": "home-insurance-guide",    "domain": "homeinsuranceclear.com"},
    {"slug": "mortgage-advisor-guide",  "domain": "mortgageadvisorguide.com"},
    {"slug": "therapy-finder-guide",    "domain": "therapyfinderguide.com"},
    {"slug": "pet-doctor-guide",        "domain": "petdoctorguide.com"},
    {"slug": "small-biz-finance-guide", "domain": "smallbizfinanceguide.com"},
]


def check_workflow(slug, workflow_file):
    r = requests.get(
        f"https://api.github.com/repos/peacoat-sites/{slug}/actions/workflows/{workflow_file}/runs?per_page=1",
        headers=GH_HEADS, timeout=15,
    )
    if r.status_code != 200:
        return {"status": "api_error", "conclusion": None, "age_hours": None, "created_at": None}
    runs = r.json().get("workflow_runs", [])
    if not runs:
        return {"status": "never_run", "conclusion": "never_run", "age_hours": None, "created_at": None}
    run = runs[0]
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
    return {
        "status":     run["status"],
        "conclusion": run["conclusion"] or run["status"],
        "age_hours":  round(age_h, 1),
        "created_at": run["created_at"],
        "run_url":    run["html_url"],
    }


def check_http(domain):
    try:
        r = requests.get(f"https://{domain}/", timeout=8, allow_redirects=True)
        return r.status_code
    except Exception as e:
        return f"ERR: {str(e)[:40]}"


def count_articles(slug):
    r = requests.get(
        f"https://api.github.com/repos/peacoat-sites/{slug}/contents/content/posts",
        headers=GH_HEADS, timeout=15,
    )
    if r.status_code != 200:
        return -1
    return len([f for f in r.json() if isinstance(f, dict) and f.get("name", "").endswith(".md")])


def open_issue(title, body):
    """Open a new GitHub issue in the audit repo."""
    # First check if there's already an open issue with the same title to avoid spam
    search_r = requests.get(
        f"https://api.github.com/repos/{AUDIT_REPO}/issues?state=open&labels=health-alert&per_page=20",
        headers=GH_HEADS, timeout=15,
    )
    open_issues = search_r.json() if search_r.status_code == 200 else []
    for issue in open_issues:
        if isinstance(issue, dict) and issue.get("title") == title:
            # Already open — update body instead
            requests.patch(
                f"https://api.github.com/repos/{AUDIT_REPO}/issues/{issue['number']}",
                headers=GH_HEADS, json={"body": body}, timeout=15,
            )
            return issue["html_url"], "updated"

    r = requests.post(
        f"https://api.github.com/repos/{AUDIT_REPO}/issues",
        headers=GH_HEADS,
        json={"title": title, "body": body, "labels": ["health-alert"]},
        timeout=15,
    )
    if r.status_code in (200, 201):
        return r.json().get("html_url"), "created"
    return None, f"failed:{r.status_code}"


def close_health_issues():
    """Close any open health-alert issues if everything is now clean."""
    r = requests.get(
        f"https://api.github.com/repos/{AUDIT_REPO}/issues?state=open&labels=health-alert&per_page=20",
        headers=GH_HEADS, timeout=15,
    )
    if r.status_code != 200:
        return
    for issue in r.json():
        if isinstance(issue, dict):
            requests.patch(
                f"https://api.github.com/repos/{AUDIT_REPO}/issues/{issue['number']}",
                headers=GH_HEADS,
                json={"state": "closed", "body": issue.get("body", "") + "\n\n_Auto-closed: all systems healthy._"},
                timeout=15,
            )


def main():
    now = datetime.now(timezone.utc)
    print(f"Portfolio health audit — {now.isoformat()}")

    all_issues  = []   # critical problems → will open GitHub issue
    site_health = []

    for site in SITES:
        slug   = site["slug"]
        domain = site["domain"]
        print(f"\n  {slug}")

        pub  = check_workflow(slug, "publish.yml")
        vid  = check_workflow(slug, "video.yml")
        http = check_http(domain)
        arts = count_articles(slug)

        site_issues = []

        # HTTP check
        if http != 200:
            site_issues.append(f"site DOWN — HTTP {http}")

        # Publish workflow
        if pub["conclusion"] == "failure":
            site_issues.append(f"publish workflow FAILED ({pub['run_url']})")
        elif pub["conclusion"] == "never_run":
            site_issues.append("publish workflow has NEVER RUN")
        elif pub["age_hours"] and pub["age_hours"] > PUBLISH_STALE_H:
            site_issues.append(f"publish not run in {pub['age_hours']:.0f}h (threshold {PUBLISH_STALE_H}h)")

        # Video workflow — only flag failure or very stale, not never_run (newly deployed)
        if vid["conclusion"] == "failure":
            site_issues.append(f"video workflow FAILED ({vid['run_url']})")
        elif vid["age_hours"] and vid["age_hours"] > VIDEO_STALE_H:
            site_issues.append(f"video not run in {vid['age_hours']:.0f}h (threshold {VIDEO_STALE_H}h)")

        pub_str = f"{pub['conclusion']} ({pub['age_hours']}h ago)" if pub['age_hours'] else pub['conclusion']
        vid_str = f"{vid['conclusion']} ({vid['age_hours']}h ago)" if vid['age_hours'] else vid['conclusion']
        print(f"    HTTP={http}  articles={arts}  publish={pub_str}  video={vid_str}")
        if site_issues:
            print(f"    ⚠ {'; '.join(site_issues)}")

        all_issues.extend([f"**{slug}**: {i}" for i in site_issues])

        site_health.append({
            "slug":         slug,
            "domain":       domain,
            "http":         http,
            "articles":     arts,
            "publish":      pub,
            "video":        vid,
            "issues":       site_issues,
            "healthy":      len(site_issues) == 0,
        })

    # Write health.json
    health = {
        "generated_at": now.isoformat(),
        "healthy":       len(all_issues) == 0,
        "issue_count":   len(all_issues),
        "issues":        all_issues,
        "thresholds": {
            "publish_stale_hours": PUBLISH_STALE_H,
            "video_stale_hours":   VIDEO_STALE_H,
        },
        "sites": site_health,
    }
    out = Path(__file__).parent / "health.json"
    out.write_text(json.dumps(health, indent=2))
    print(f"\n✓ health.json written — {len(all_issues)} issue(s) found")

    # GitHub issue management
    if all_issues:
        body = (
            f"## ⚠️ Portfolio Health Alert\n\n"
            f"**Detected at:** {now.strftime('%Y-%m-%d %H:%M UTC')}  \n"
            f"**Issues found:** {len(all_issues)}\n\n"
            + "\n".join(f"- {i}" for i in all_issues)
            + "\n\n---\n_Auto-generated by the 12h audit workflow. "
            "Resolve the issues above and the alert will auto-close on the next audit run._"
        )
        url, action = open_issue(
            f"⚠️ Portfolio Health Issues — {now.strftime('%Y-%m-%d')}",
            body,
        )
        print(f"  GitHub issue {action}: {url}")
    else:
        print("  All systems healthy — closing any open health alerts")
        close_health_issues()


if __name__ == "__main__":
    main()
