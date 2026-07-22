#!/usr/bin/env python3
"""
Portfolio health audit — runs every 12h via GitHub Actions in peacoat-git/softlinen.
Checks each site's publish + video workflow, HTTP status, and article counts.
Writes health.json and opens a GitHub Issue if critical problems are found.
Closes stale open issues when all clear.
"""

import os, re, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

GH_TOKEN  = os.environ["GITHUB_TOKEN"]
GH_HEADS  = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
AUDIT_REPO = "peacoat-git/softlinen"   # where to open issues

# Max acceptable age (hours) before flagging as stale
PUBLISH_STALE_H = 30   # should run daily; flag if >30h
VIDEO_STALE_H   = 216  # some sites run weekly (Mon); max gap ~168h, flag at 216h

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
    {"slug": "keto-living-guide",       "domain": "ketolivingguide.com"},
    {"slug": "rv-life-guide",           "domain": "rv-life-guide.com"},
    {"slug": "seniorstrength",          "domain": "seniorstrength.today"},
    {"slug": "chicken-keeper-guide",    "domain": "chickenkeeperguide.com"},
    {"slug": "fixitrightway",           "domain": "fixitrightway.com"},
    {"slug": "gamedevproducer",         "domain": "gamedevproducer.com"},
]

# Run status alone is blind to a feed that quietly stops updating: a swallowed
# push or a no-op fetch both leave the run green. So check the OUTPUT age too.
# (slug, workflow file, committed data path, cadence days)
DATA_FEEDS = [
    ("keto-living-guide",       "keto-data.yml",  "data/keto_foods.json",   30),
    ("mortgage-advisor-guide",  "rates.yml",      "data/rates.json",        30),
    ("small-biz-finance-guide", "rates.yml",      "data/rates.json",        30),
    ("solar-home-planner",      "solar-data.yml", "data/solar_states.json", 30),
    ("solar-planner-guide",     "solar-data.yml", "data/solar_states.json", 30),
    ("chicken-keeper-guide",    "eggs.yml",       "data/eggs.json",         30),
    ("gamedevproducer",         "steam.yml",      "data/steam.json",         7),
    ("rv-life-guide",           "fuel.yml",       "data/fuel_prices.json",   7),
]


def check_data_feed(slug, path):
    """Age in days of the newest commit touching `path`, or None if unknown."""
    try:
        r = requests.get(
            f"https://api.github.com/repos/peacoat-sites/{slug}/commits",
            params={"path": path, "per_page": 1}, headers=GH_HEADS, timeout=15,
        )
        if r.status_code != 200 or not r.json():
            return None
        ts = r.json()[0]["commit"]["committer"]["date"]
        created = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - created).total_seconds() / 86400
    except Exception:
        return None


def check_sitemap_hygiene(domain):
    """Check EVERY URL in the live sitemap. A healthy sitemap lists only canonical
    200 URLs. requests follows redirects by default (masking a merged/renamed page
    as 200), so we use allow_redirects=False and classify each entry:
      DEAD  : 4xx/5xx — URL listed but gone
      REDIR : 3xx — merged/renamed page still emitted (redirect is fine for users,
              but a redirecting URL in a sitemap is a duplicate-content signal;
              the source .md should be deleted)
    Returns (checked, dead[(path,code)], redir[(path,target)])."""
    try:
        r = requests.get(f"https://{domain}/sitemap.xml",
                         headers={"User-Agent": "Googlebot"}, timeout=15)
        if r.status_code != 200:
            return (0, [], [])
        locs = re.findall(r"<loc>([^<]+)</loc>", r.text)
    except Exception:
        return (0, [], [])
    if not locs:
        return (0, [], [])

    def chk(u):
        for _ in range(2):  # retry once — a transient error is not a real status
            try:
                rr = requests.head(u, headers={"User-Agent": "Mozilla/5.0"},
                                   allow_redirects=False, timeout=12)
                return (u, rr.status_code, rr.headers.get("Location", "") or "")
            except Exception:
                pass
        return (u, 0, "")

    dead, redir = [], []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for u, code, loc in ex.map(chk, locs):
            if code == 0:
                continue
            path = u.replace(f"https://{domain}", "")
            if code in (301, 302, 303, 307, 308):
                redir.append((path, loc.replace(f"https://{domain}", "") if loc else "?"))
            elif code >= 400:
                dead.append((path, code))
    return (len(locs), dead, redir)


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


# ── TOKEN HEALTH ───────────────────────────────────────────────

def check_tokens():
    """Validate all long-lived credentials so a revoked/expired token is flagged
    directly (by name) BEFORE it silently breaks publishing / video / indexing."""
    issues = []
    # GitHub
    try:
        r = requests.get("https://api.github.com/user", headers=GH_HEADS, timeout=15)
        if r.status_code != 200:
            issues.append(f"GitHub token INVALID (HTTP {r.status_code}) - workflows + admin will fail")
    except Exception as e:
        issues.append(f"GitHub token check error: {e}")
    # Google OAuth refresh tokens
    cid  = os.environ.get("GOOGLE_CLIENT_ID", "")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if cid and csec:
        for label, key in [("Google main", "GOOGLE_REFRESH_TOKEN"),
                           ("AdSense", "ADSENSE_REFRESH_TOKEN"),
                           ("GSC Indexing", "GSC_INDEXING_TOKEN")]:
            rt = os.environ.get(key, "")
            if not rt:
                continue
            try:
                r = requests.post("https://oauth2.googleapis.com/token", data={
                    "client_id": cid, "client_secret": csec,
                    "refresh_token": rt, "grant_type": "refresh_token",
                }, timeout=15)
                if r.status_code != 200:
                    err = r.json().get("error", "unknown")
                    issues.append(f"{label} OAuth token INVALID ({err}) - RE-AUTHORIZE required")
            except Exception as e:
                issues.append(f"{label} token check error: {e}")
    # Cloudflare
    cf = os.environ.get("CF_TOKEN", "") or os.environ.get("CF_PAGES_TOKEN", "")
    if cf:
        try:
            r = requests.get("https://api.cloudflare.com/client/v4/user/tokens/verify",
                             headers={"Authorization": f"Bearer {cf}"}, timeout=15)
            if r.status_code != 200 or not r.json().get("success"):
                issues.append("Cloudflare token INVALID - Pages deploys may fail")
        except Exception as e:
            issues.append(f"Cloudflare token check error: {e}")
    return issues


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

        # Video generation intentionally paused 2026-07 (Shotstack/ElevenLabs cancelled);
        # crons removed from all video.yml. No video alerts until it is re-enabled.

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

    # Data-feed output freshness (catches feeds that silently stop updating)
    print("\n  data feeds...")
    feed_health = []
    for slug, wf, path, cadence in DATA_FEEDS:
        age = check_data_feed(slug, path)
        # generous: only alert at 2x cadence + a week, since a feed legitimately
        # commits nothing when the upstream values have not moved
        limit = cadence * 2 + 7
        stale = age is not None and age > limit
        if age is None:
            print(f"    {slug}/{path}: no commit history")
        else:
            print(f"    {slug}/{path}: {age:.0f}d old (limit {limit}d)" + ("  STALE" if stale else ""))
        if stale:
            all_issues.append(
                f"**{slug}**: {path} not updated in {age:.0f}d "
                f"(cadence {cadence}d) — {wf} may be silently failing"
            )
        feed_health.append({
            "slug": slug, "workflow": wf, "path": path,
            "cadence_days": cadence, "age_days": None if age is None else round(age, 1),
            "stale": bool(stale),
        })

    # Sitemap hygiene — every sitemap URL must be a canonical 200. Catches merged/
    # renamed pages still emitted as redirecting URLs (a duplicate-content signal
    # that run-status and HTTP checks are both blind to) and dead URLs in the sitemap.
    print("\n  sitemap hygiene...")
    sitemap_health = []
    for site in SITES:
        slug, domain = site["slug"], site["domain"]
        checked, dead, redir = check_sitemap_hygiene(domain)
        note = f"{checked} urls"
        if dead:  note += f"  {len(dead)} DEAD"
        if redir: note += f"  {len(redir)} REDIR"
        print(f"    {slug}: {note}")
        if dead:
            ds = ", ".join(f"{u}({c})" for u, c in dead[:3])
            all_issues.append(f"**{slug}**: sitemap lists {len(dead)} dead URL(s) — {ds}")
        if redir:
            rs = ", ".join(f"{u}→{t}" for u, t in redir[:3])
            all_issues.append(
                f"**{slug}**: sitemap lists {len(redir)} redirecting URL(s) — "
                f"merged/renamed page still emitted, delete its source .md ({rs})"
            )
        sitemap_health.append({
            "slug": slug, "domain": domain, "checked": checked,
            "dead":  [{"path": u, "code": c} for u, c in dead],
            "redir": [{"path": u, "target": t} for u, t in redir],
        })

    # Token health (proactive: catches revocation/expiry before workflows fail)
    print("  token health...")
    token_issues = check_tokens()
    for ti in token_issues:
        all_issues.append(f"**TOKEN**: {ti}")
        print(f"    ALERT: {ti}")
    if not token_issues:
        print("    all tokens valid")

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
        "data_feeds": feed_health,
        "sitemap_health": sitemap_health,
        "token_issues": token_issues,
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
