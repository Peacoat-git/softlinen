#!/usr/bin/env python3
"""
Games summary fetcher — runs via GitHub Actions alongside fetch_stats.py, commits games.json.
Source: games.json on the private Peacoat-git/game repo's ops-state branch (written nightly by its
live-ops job). Needs GAME_REPO_TOKEN: a fine-grained token, read-only Contents on Peacoat-git/game.
Without the token it leaves the existing games.json untouched.
"""

import os, sys, json, pathlib, requests

REPO = "Peacoat-git/game"
REF  = "ops-state"
OUT  = pathlib.Path(__file__).parent / "games.json"


# This repo is public: copy only the known non-sensitive fields, so anything added upstream
# later never lands here by accident.
def sanitize(src):
    return {
        "updatedAt": src.get("updatedAt"),
        "games": [
            {
                "name":            g.get("name"),
                "dailyNumber":     g.get("dailyNumber"),
                "dailiesBundled":  g.get("dailiesBundled"),
                "channels": [
                    {"id": c.get("id"), "status": c.get("status"), "runwayDays": c.get("runwayDays")}
                    for c in g.get("channels", [])
                ],
                "health":          g.get("health", "unknown"),
                "healthCheckedAt": g.get("healthCheckedAt"),
            }
            for g in src.get("games", [])
        ],
    }


def main():
    token = os.environ.get("GAME_REPO_TOKEN", "")
    if not token:
        print("GAME_REPO_TOKEN not set — keeping existing games.json")
        return
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/contents/games.json",
        params={"ref": REF},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"},
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"games.json fetch failed: HTTP {r.status_code} {r.text[:200]}")
    data = sanitize(r.json())
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"✓ games.json written  |  {len(data['games'])} game(s)  |  updatedAt {data['updatedAt']}")


if __name__ == "__main__":
    main()
