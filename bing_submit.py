"""Bing Webmaster API, all 15 sites, daily:
1. Keep /sitemap.xml registered (SubmitFeed if Bing has none).
2. Submit sitemap URLs through the URL Submission API up to each site's daily quota:
   new or updated URLs first (lastmod after our last submission), then the never-submitted backlog.
State persists in bing_submit_state.json. Needs BING_WEBMASTER_API_KEY.
Usage: python bing_submit.py [--dry-run]"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests

KEY = os.environ["BING_WEBMASTER_API_KEY"]
API = "https://ssl.bing.com/webmaster/api.svc/json/"
STATE = Path(__file__).with_name("bing_submit_state.json")
DRY = "--dry-run" in sys.argv
SKIP = ("/tags/", "/page/", "/search/")


def get(method, **params):
    r = requests.get(API + method, params={**params, "apikey": KEY}, timeout=60)
    r.raise_for_status()
    return r.json().get("d")


def post(method, body):
    r = requests.post(API + method, params={"apikey": KEY}, json=body, timeout=60)
    return r.status_code, r.text[:160]


def sitemap(site):
    xml = requests.get(site.rstrip("/") + "/sitemap.xml", timeout=30, headers={"User-Agent": "softlinen-indexer"}).text
    out = {}
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>([^<]+)</loc>", block)
        if loc and not any(s in loc.group(1) for s in SKIP):
            lm = re.search(r"<lastmod>([^<]+)</lastmod>", block)
            out[loc.group(1)] = lm.group(1)[:10] if lm else ""
    return out


def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    today = date.today().isoformat()
    for s in get("GetUserSites"):
        site = s["Url"]
        dom = urlparse(site).hostname.removeprefix("www.")
        try:
            want = site.rstrip("/") + "/sitemap.xml"
            feeds = get("GetFeeds", siteUrl=site) or []
            if not any(f["Url"].rstrip("/") == want for f in feeds) and not DRY:
                print(f"  {dom:<26} sitemap registered: {post('SubmitFeed', {'siteUrl': site, 'feedUrl': want})[0]}")
            quota = (get("GetUrlSubmissionQuota", siteUrl=site) or {}).get("DailyQuota", 0)
            done = state.setdefault(dom, {})
            urls = sitemap(site)
            changed = [u for u, lm in urls.items() if u in done and lm > done[u]]
            backlog = sorted((u for u in urls if u not in done), key=lambda u: urls[u], reverse=True)
            batch = (changed + backlog)[:quota]
            left = len(backlog) - max(0, len(batch) - len(changed))
            if batch and not DRY:
                code, body = post("SubmitUrlbatch", {"siteUrl": site, "urlList": batch})
                if code == 200:
                    done.update({u: today for u in batch})
                else:
                    batch = []
                    print(f"  {dom:<26} SubmitUrlbatch HTTP {code} {body}")
            print(f"  {dom:<26} quota {quota:>4} | submitted {len(batch):>4} ({len(changed)} changed) | backlog left {left}")
        except Exception as e:
            print(f"  {dom:<26} ERROR {e}")
    if not DRY:
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
