#!/usr/bin/env python3
"""Backfill the Google Indexing API: submit every existing article URL across all 15 sites,
working through the backlog at a safe daily budget (the Indexing API quota is 200/day,
shared per Google Cloud project; daily publishing already uses ~30, so we cap at 150 here).

State (which URLs have been submitted) persists in index_backfill_state.json so each daily
run resumes where it left off. Once the whole backlog is submitted, the job idles.
"""
import os, json, re, time, requests

DOMAINS = [
    "medicarestarter.com", "solarplannerguide.com", "solarhomeplanner.com",
    "injuryvictimguide.com", "homeinsuranceclear.com", "mortgageadvisorguide.com",
    "therapyfinderguide.com", "petdoctorguide.com", "smallbizfinanceguide.com",
    "ketolivingguide.com", "chickenkeeperguide.com", "rv-life-guide.com",
    "gamedevproducer.com", "seniorstrength.today", "fixitrightway.com",
]
STATE_FILE = "index_backfill_state.json"
DAILY_BUDGET = int(os.environ.get("INDEX_DAILY_BUDGET", "150"))
# Skip non-article URLs (noindex taxonomy pages waste quota)
SKIP = ("/categories/", "/tags/", "/page/")

def access_token():
    refresh = os.environ.get("GSC_INDEXING_TOKEN", "")
    cid = os.environ.get("GOOGLE_CLIENT_ID", "")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not (refresh and cid and csec):
        print("Missing GSC_INDEXING_TOKEN / GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET"); return None
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": csec,
        "refresh_token": refresh, "grant_type": "refresh_token"}, timeout=15)
    if r.status_code == 200:
        return r.json().get("access_token")
    print(f"Token exchange failed ({r.status_code}): {r.text[:120]}"); return None

def all_article_urls():
    urls = []
    for d in DOMAINS:
        try:
            xml = requests.get(f"https://{d}/sitemap.xml", headers={"User-Agent": "Googlebot"}, timeout=25).text
            locs = re.findall(r"<loc>([^<]+)</loc>", xml)
            kept = [u for u in locs if not any(sk in u for sk in SKIP) and u.rstrip("/") != f"https://{d}"]
            urls += kept
        except Exception as e:
            print(f"  sitemap {d}: ERROR {e}")
    return urls

def main():
    state = {"submitted": []}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            pass
    done = set(state.get("submitted", []))

    all_urls = all_article_urls()
    total = len(all_urls)
    remaining = [u for u in all_urls if u not in done]
    print(f"Catalog: {total} article URLs · already submitted: {len(done)} · remaining: {len(remaining)}")

    if not remaining:
        print("Backlog clear — all article URLs have been submitted. Idling.")
        return

    token = access_token()
    if not token:
        print("No access token — aborting (state unchanged)."); return
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    submitted_today = 0
    errors = 0
    fatal = None
    quota_stop = False
    ownership_skip = set()  # domains that returned 403 ownership error — skip remaining URLs
    for u in remaining[:DAILY_BUDGET]:
        try:
            # Skip domains that already returned an ownership error this run
            from urllib.parse import urlparse
            if urlparse(u).netloc in ownership_skip:
                continue
            r = requests.post("https://indexing.googleapis.com/v3/urlNotifications:publish",
                              headers=headers, json={"url": u, "type": "URL_UPDATED"}, timeout=15)
            if r.status_code == 200:
                done.add(u); submitted_today += 1
            elif r.status_code in (429, 403) and ("quota" in r.text.lower() or "rateLimit" in r.text):
                print(f"  Daily quota reached after {submitted_today} (resumes tomorrow)")
                quota_stop = True; break
            elif r.status_code == 403 and ("SCOPE_INSUFFICIENT" in r.text or "insufficient authentication scopes" in r.text.lower()):
                # True OAuth scope error — token doesn't have the indexing scope at all.
                fatal = f"403 PERMISSION_DENIED / insufficient scope - token is missing the 'indexing' scope. {r.text[:140]}"
                print(f"  FATAL: {fatal}"); break
            elif r.status_code == 403 and "Failed to verify the URL ownership" in r.text:
                # Domain not verified in Google Search Console — skip all URLs for this domain.
                from urllib.parse import urlparse
                bad_domain = urlparse(u).netloc
                ownership_skip.add(bad_domain)
                errors += 1
                print(f"  SKIP domain {bad_domain}: not verified in GSC (ownership error) — skipping remaining URLs for this domain")
            else:
                errors += 1
                print(f"  {r.status_code} for {u}: {r.text[:80]}")
        except Exception as e:
            errors += 1
            print(f"  error {e}")
        time.sleep(0.2)

    state["submitted"] = sorted(done)
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), indent=0)
    print(f"\nSubmitted {submitted_today} URLs today. Total done: {len(done)}/{total}. "
          f"Remaining: {total - len(done)}")

    # Fail loudly so a broken backfill turns the run RED instead of a green run that submits
    # nothing. A silent scope-403 froze this job for days before the 2026-06-15 fix.
    if fatal:
        raise SystemExit(f"BACKFILL FAILED: {fatal}")
    if submitted_today == 0 and not quota_stop:
        raise SystemExit(f"BACKFILL FAILED: 0 of {len(remaining)} remaining URLs submitted "
                         f"({errors} errors) - check token scope / quota.")

if __name__ == "__main__":
    main()
