#!/usr/bin/env python3
"""
Dashboard data fetcher — runs via GitHub Actions, commits stats.json to the softlinen repo.
Sources:
  - GitHub API          : article counts, video data (always available)
  - Cloudflare GraphQL  : page views, unique visitors per site (available now)
  - YouTube Data API v3 : video view counts, likes (uses OAuth access token)
  - AdSense API         : revenue, RPM, impressions (needs adsense.readonly scope)
"""

import os, json, requests
from datetime import datetime, timezone, timedelta

# ── Credentials ────────────────────────────────────────────────────────────────
GH_TOKEN        = os.environ["GITHUB_TOKEN"]
CF_TOKEN        = os.environ["CF_TOKEN"]
CF_ACCOUNT_ID   = os.environ["CF_ACCOUNT_ID"]
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
ADSENSE_REFRESH_TOKEN = os.environ.get("ADSENSE_REFRESH_TOKEN", "")  # separate token w/ adsense scope
ELEVENLABS_API_KEY   = os.environ.get("ELEVENLABS_API_KEY", "")

GH_HEADERS = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
CF_HEADERS  = {"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}

TODAY     = datetime.now(timezone.utc).date()
MTD_START = TODAY.replace(day=1).isoformat()
TODAY_STR = TODAY.isoformat()
LAST_30   = (TODAY - timedelta(days=30)).isoformat()

# ── Site manifest ──────────────────────────────────────────────────────────────
SITES = [
    {"slug": "medicare-starter",        "domain": "medicarestarter.com",       "zone": "63d2f663d43099397a0f06b721bcf4be", "color": "#f59e0b", "niche": "Medicare & senior health",    "rpm_est": "12-18"},
    {"slug": "solar-planner-guide",     "domain": "solarplannerguide.com",     "zone": "e155d87c2b2e6ced46a56ef6cab8dbbf", "color": "#22c55e", "niche": "Solar energy",                "rpm_est": "8-14"},
    {"slug": "solar-home-planner",      "domain": "solarhomeplanner.com",      "zone": "39a154e2ab3799012adcd2e3d7ba497f", "color": "#10b981", "niche": "Solar energy",                "rpm_est": "8-14"},
    {"slug": "injury-victim-guide",     "domain": "injuryvictimguide.com",     "zone": "94e6681a7955d38ee6f051b456f2617c", "color": "#ef4444", "niche": "Personal injury law",         "rpm_est": "15-25"},
    {"slug": "home-insurance-guide",    "domain": "homeinsuranceclear.com",    "zone": "5f3ac8b9602dfae29590df0e134ec064", "color": "#3b82f6", "niche": "Home insurance",              "rpm_est": "12-20"},
    {"slug": "mortgage-advisor-guide",  "domain": "mortgageadvisorguide.com",  "zone": "8b4b46577ff705404bc9ff51a0afe5f7", "color": "#8b5cf6", "niche": "Mortgage & home finance",    "rpm_est": "10-18"},
    {"slug": "therapy-finder-guide",    "domain": "therapyfinderguide.com",    "zone": "bfd123744e25ba62408bc1e5d9131e0c", "color": "#ec4899", "niche": "Mental health & therapy",     "rpm_est": "8-14"},
    {"slug": "pet-doctor-guide",        "domain": "petdoctorguide.com",        "zone": "f8ba94856bed0462d289108c029918ed", "color": "#f97316", "niche": "Pet health & vet care",       "rpm_est": "6-12"},
    {"slug": "small-biz-finance-guide", "domain": "smallbizfinanceguide.com",  "zone": "f017bfad279ae9c19ac62f1b211f5143", "color": "#14b8a6", "niche": "Small business finance",      "rpm_est": "8-15"},
    # Tier 2
    {"slug": "keto-living-guide",       "domain": "ketolivingguide.com",       "zone": "f5c44a49fbed28497138c0cd8a66f64c", "color": "#1b4332", "niche": "Keto & low-carb living",      "rpm_est": "5-10"},
    {"slug": "chicken-keeper-guide",    "domain": "chickenkeeperguide.com",    "zone": "8eca9af5b63390c863a003a40d75d038", "color": "#744210", "niche": "Backyard chickens & poultry", "rpm_est": "4-8"},
    {"slug": "rv-life-guide",           "domain": "rv-life-guide.com",         "zone": "6985e9d6f84254f29742981a91a6c6c1", "color": "#1a1a2e", "niche": "RV life & van living",        "rpm_est": "5-9"},
    # Authority
    {"slug": "gamedevproducer",          "domain": "gamedevproducer.com",       "zone": "6d57c09c827f709c73a4b8b28928c3e8", "color": "#6d28d9", "niche": "Game producer best practices","rpm_est": "3-7"},
]

ORG = "peacoat-sites"


# ── Helpers ────────────────────────────────────────────────────────────────────

def google_access_token(refresh_token):
    """Exchange a refresh token for a short-lived access token."""
    if not refresh_token:
        return None
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    })
    if r.status_code == 200:
        return r.json().get("access_token")
    print(f"  [WARN] Google token exchange failed: {r.status_code} {r.text[:200]}")
    return None


# ── Data fetchers ──────────────────────────────────────────────────────────────

def fetch_gh_articles(slug):
    """Count published markdown articles from content/posts/."""
    r = requests.get(f"https://api.github.com/repos/{ORG}/{slug}/contents/content/posts", headers=GH_HEADERS)
    if r.status_code != 200:
        return 0, []
    files = [f for f in r.json() if f["name"].endswith(".md")]
    titles = [f["name"].replace(".md", "").replace("-", " ").title() for f in files]
    return len(files), titles


def fetch_gh_videos(slug):
    """Read data/youtube.json from the site repo."""
    import base64
    r = requests.get(f"https://api.github.com/repos/{ORG}/{slug}/contents/data/youtube.json", headers=GH_HEADERS)
    if r.status_code != 200:
        return []
    content = base64.b64decode(r.json()["content"].replace("\n", "")).decode()
    return json.loads(content)


def fetch_cf_analytics(zone_id, start=MTD_START, end=TODAY_STR):
    """Fetch page views and unique visitors from Cloudflare GraphQL Analytics."""
    query = """
    query($zoneTag: String!, $start: Date!, $end: Date!) {
      viewer {
        zones(filter: {zoneTag: $zoneTag}) {
          httpRequests1dGroups(
            limit: 31
            filter: {date_geq: $start, date_lt: $end}
            orderBy: [date_ASC]
          ) {
            dimensions { date }
            sum { pageViews visits requests }
            uniq { uniques }
          }
        }
      }
    }
    """
    _empty = {"page_views": 0, "visits": 0, "unique_visitors": 0, "daily": []}
    try:
        r = requests.post(
            "https://api.cloudflare.com/client/v4/graphql",
            headers=CF_HEADERS,
            json={"query": query, "variables": {"zoneTag": zone_id, "start": start, "end": end}},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"  [WARN] CF HTTP {r.status_code}: {r.text[:200]}")
            return _empty
        resp = r.json()
        errors = resp.get("errors")
        if errors:
            print(f"  [WARN] CF GraphQL errors: {str(errors)[:300]}")
        data = resp.get("data")
        if not data:
            return _empty
        zones = data.get("viewer", {}).get("zones", [])
        if not zones:
            return _empty
        groups = zones[0].get("httpRequests1dGroups", [])
        total_pv   = sum(g["sum"]["pageViews"] for g in groups)
        total_vis  = sum(g["sum"]["visits"]    for g in groups)
        total_uniq = sum(g["uniq"]["uniques"]  for g in groups)
        daily = [{"date": g["dimensions"]["date"], "pageViews": g["sum"]["pageViews"], "visits": g["sum"]["visits"]} for g in groups]
        return {"page_views": total_pv, "visits": total_vis, "unique_visitors": total_uniq, "daily": daily}
    except Exception as e:
        print(f"  [WARN] CF analytics exception: {e}")
        return _empty


def fetch_yt_video_stats(video_ids, access_token):
    """Fetch view counts for a list of YouTube video IDs."""
    if not video_ids or not access_token:
        return {}
    ids_param = ",".join(video_ids[:50])
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"part": "statistics,snippet", "id": ids_param},
    )
    if r.status_code != 200:
        print(f"  [WARN] YT stats failed: {r.status_code}")
        return {}
    stats = {}
    for item in r.json().get("items", []):
        vid_id = item["id"]
        s = item.get("statistics", {})
        stats[vid_id] = {
            "views":    int(s.get("viewCount", 0)),
            "likes":    int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0)),
            "title":    item.get("snippet", {}).get("title", ""),
        }
    return stats


def fetch_elevenlabs_balance():
    """Fetch ElevenLabs character usage from subscription endpoint."""
    if not ELEVENLABS_API_KEY:
        return None
    r = requests.get(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        timeout=10,
    )
    if r.status_code == 200:
        sub = r.json()
        used  = sub.get("character_count", 0)
        limit = sub.get("character_limit", 0)
        reset_unix = sub.get("next_character_count_reset_unix")
        reset_date = None
        if reset_unix:
            from datetime import datetime, timezone
            reset_date = datetime.fromtimestamp(reset_unix, tz=timezone.utc).strftime("%Y-%m-%d")
        return {
            "tier":            sub.get("tier", ""),
            "chars_used":      used,
            "chars_limit":     limit,
            "chars_remaining": max(0, limit - used),
            "pct_used":        round(used / limit * 100, 1) if limit else 0,
            "reset_date":      reset_date,
        }
    print(f"  [WARN] ElevenLabs subscription failed: {r.status_code} {r.text[:200]}")
    return None


def fetch_adsense_revenue(access_token, start=MTD_START, end=TODAY_STR):
    """Fetch AdSense earnings via Management API v2."""
    if not access_token:
        return None
    # First get account ID
    acc_r = requests.get(
        "https://adsense.googleapis.com/v2/accounts",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if acc_r.status_code != 200:
        print(f"  [WARN] AdSense accounts failed: {acc_r.status_code} {acc_r.text[:200]}")
        return None
    accounts = acc_r.json().get("accounts", [])
    if not accounts:
        return None
    account_name = accounts[0]["name"]

    # Fetch report
    rpt_r = requests.get(
        f"https://adsense.googleapis.com/v2/{account_name}/reports:generate",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "dateRange":  "CUSTOM",
            "startDate.year":  start[:4], "startDate.month": start[5:7], "startDate.day": start[8:],
            "endDate.year":    end[:4],   "endDate.month":   end[5:7],   "endDate.day":   end[8:],
            "metrics":    ["ESTIMATED_EARNINGS", "IMPRESSIONS", "CLICKS", "PAGE_VIEWS_RPM"],
            "dimensions": ["DOMAIN_NAME"],
        },
    )
    if rpt_r.status_code != 200:
        print(f"  [WARN] AdSense report failed: {rpt_r.status_code} {rpt_r.text[:300]}")
        return None
    return rpt_r.json()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"Fetching dashboard data — {TODAY_STR}")

    # Get Google access tokens
    yt_token      = google_access_token(GOOGLE_REFRESH_TOKEN)
    adsense_token = google_access_token(ADSENSE_REFRESH_TOKEN) if ADSENSE_REFRESH_TOKEN else None
    print(f"  YouTube token:  {'OK' if yt_token else 'MISSING'}")
    print(f"  AdSense token:  {'OK' if adsense_token else 'MISSING (needs adsense.readonly scope)'}")

    # ElevenLabs balance
    el_balance = fetch_elevenlabs_balance()
    if el_balance:
        print(f"  ElevenLabs:     {el_balance['chars_remaining']:,} chars remaining / {el_balance['chars_limit']:,} ({el_balance['pct_used']}% used)")
    else:
        print("  ElevenLabs:     MISSING or key invalid")

    # Fetch AdSense totals
    adsense_data = fetch_adsense_revenue(adsense_token) if adsense_token else None

    # Parse AdSense by domain
    adsense_by_domain = {}
    if adsense_data:
        for row in adsense_data.get("rows", []):
            cells = row.get("cells", [])
            if len(cells) >= 5:
                domain  = cells[0].get("value", "")
                revenue = float(cells[1].get("value", 0))
                impr    = int(cells[2].get("value", 0))
                clicks  = int(cells[3].get("value", 0))
                rpm     = float(cells[4].get("value", 0))
                adsense_by_domain[domain] = {"revenue": revenue, "impressions": impr, "clicks": clicks, "rpm": rpm}

    sites_data = []
    total_articles = 0
    total_pv       = 0
    total_visits   = 0

    for site in SITES:
        slug   = site["slug"]
        domain = site["domain"]
        print(f"\n  {slug}")

        # GitHub data
        art_count, art_titles = fetch_gh_articles(slug)
        videos = fetch_gh_videos(slug)
        print(f"    Articles: {art_count}  |  Videos: {len(videos)}")

        # Cloudflare analytics
        cf = fetch_cf_analytics(site["zone"])
        print(f"    Page views: {cf['page_views']}  |  Visitors: {cf['unique_visitors']}")

        # YouTube video stats
        video_ids = []
        for v in videos:
            if v.get("shorts_id"):   video_ids.append(v["shorts_id"])
            if v.get("standard_id"): video_ids.append(v["standard_id"])
        yt_stats = fetch_yt_video_stats(video_ids, yt_token) if video_ids else {}

        yt_total_views = sum(s["views"] for s in yt_stats.values())
        print(f"    YT video IDs: {len(video_ids)}  |  Total views: {yt_total_views}")

        # Enrich videos with live stats
        enriched_videos = []
        for v in videos:
            ev = dict(v)
            sid = v.get("shorts_id")
            stid = v.get("standard_id")
            ev["shorts_stats"]   = yt_stats.get(sid,   {}) if sid  else {}
            ev["standard_stats"] = yt_stats.get(stid,  {}) if stid else {}
            ev["total_views"]    = ev["shorts_stats"].get("views", 0) + ev["standard_stats"].get("views", 0)
            enriched_videos.append(ev)

        # AdSense per site
        ads = adsense_by_domain.get(domain, {"revenue": 0, "impressions": 0, "clicks": 0, "rpm": 0})

        total_articles += art_count
        total_pv       += cf["page_views"]
        total_visits   += cf["visits"]

        sites_data.append({
            "slug":         slug,
            "domain":       domain,
            "name":         " ".join(w.capitalize() for w in slug.split("-")),
            "color":        site["color"],
            "niche":        site["niche"],
            "rpm_est":      site["rpm_est"],
            "articles":     art_count,
            "article_titles": art_titles[:20],
            "videos":       enriched_videos,
            "cloudflare":   cf,
            "adsense":      ads,
            "yt_total_views": yt_total_views,
        })

    # Portfolio totals
    total_revenue = sum(s["adsense"]["revenue"] for s in sites_data)
    total_yt_views = sum(s["yt_total_views"] for s in sites_data)
    total_videos = sum(len(s["videos"]) for s in sites_data)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": MTD_START, "end": TODAY_STR},
        "totals": {
            "revenue":      round(total_revenue, 2),
            "page_views":   total_pv,
            "visits":       total_visits,
            "articles":     total_articles,
            "videos":       total_videos,
            "yt_views":     total_yt_views,
            "sites":        len(sites_data),
        },
        "adsense_connected": adsense_token is not None,
        "youtube_connected": yt_token is not None,
        "elevenlabs":        el_balance,
        "sites": sites_data,
    }

    # Write stats.json (works both locally and in GitHub Actions runner)
    import pathlib
    out_path = pathlib.Path(__file__).parent / "stats.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n✓ stats.json written to {out_path}  |  Views: {total_pv}  |  Articles: {total_articles}  |  Revenue: ${total_revenue:.2f}")
    return output


if __name__ == "__main__":
    main()
