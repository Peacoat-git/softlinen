"""IndexNow submission for all 15 sites (Bing, which also powers Yahoo; plus Yandex/Seznam/Naver).
Daily: submit sitemap URLs whose <lastmod> is within the last N days (new + updated articles).
Usage: python indexnow_ping.py [--days N] [--all] [--extra urls.txt]   (--extra: removed/redirected URLs, one per line)"""
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

KEY = "f155e5946b534b6697b4a5185e39fa21"   # public by design; hosted at https://<domain>/<KEY>.txt
# Per-host override: Bing returned 403 UserForbiddedToAccessSite for gamedev on the shared key (2026-11-23)
HOST_KEYS = {"gamedevproducer.com": "5772170f5fa3ce9106100523c7b801e3"}
DOMAINS = [
    "medicarestarter.com", "solarplannerguide.com", "solarhomeplanner.com",
    "injuryvictimguide.com", "homeinsuranceclear.com", "mortgageadvisorguide.com",
    "therapyfinderguide.com", "petdoctorguide.com", "smallbizfinanceguide.com",
    "ketolivingguide.com", "chickenkeeperguide.com", "rv-life-guide.com",
    "gamedevproducer.com", "seniorstrength.today", "fixitrightway.com",
]
SKIP = ("/tags/", "/page/", "/search/")


def sitemap_urls(domain, since):
    xml = requests.get(f"https://{domain}/sitemap.xml", timeout=30).text
    out = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>([^<]+)</loc>", block)
        if not loc or any(s in loc.group(1) for s in SKIP):
            continue
        lm = re.search(r"<lastmod>([^<]+)</lastmod>", block)
        if since is None or (lm and datetime.fromisoformat(lm.group(1).replace("Z", "+00:00")) >= since):
            out.append(loc.group(1))
    return out


def submit(domain, urls):
    for i in range(0, len(urls), 9000):
        r = requests.post("https://api.indexnow.org/indexnow", timeout=60,
                          headers={"Content-Type": "application/json; charset=utf-8"},
                          json={"host": domain, "key": HOST_KEYS.get(domain, KEY), "keyLocation": f"https://{domain}/{HOST_KEYS.get(domain, KEY)}.txt",
                                "urlList": urls[i:i + 9000]})
        print(f"  {domain:<26} {len(urls[i:i + 9000]):>5} URLs -> HTTP {r.status_code} {r.text[:120].strip()}")


def main():
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 2
    since = None if "--all" in sys.argv else datetime.now(timezone.utc) - timedelta(days=days)
    extra = {}
    if "--extra" in sys.argv:
        for u in open(sys.argv[sys.argv.index("--extra") + 1], encoding="utf-8").read().split():
            extra.setdefault(urlparse(u).hostname.removeprefix("www."), []).append(u)
    print(f"IndexNow: {'all URLs' if since is None else f'lastmod since {since:%Y-%m-%d %H:%M} UTC'}")
    for d in DOMAINS:
        try:
            urls = list(dict.fromkeys(sitemap_urls(d, since) + extra.get(d, [])))
            if urls:
                submit(d, urls)
            else:
                print(f"  {d:<26}     0 URLs changed")
        except Exception as e:
            print(f"  {d:<26} ERROR {e}")


if __name__ == "__main__":
    main()
