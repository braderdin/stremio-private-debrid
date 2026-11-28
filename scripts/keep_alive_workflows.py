#!/usr/bin/env python3
"""
GitHub Actions 60-Day Anti-Disable Heartbeat & Auto-Enabler
- Mengemas kini fail .github/heartbeat.json untuk mengekalkan aktiviti repo.
- Mengaktifkan semula mana-mana workflow yang terhenti akibat 'disabled_inactivity'
  menggunakan GITHUB_TOKEN terbina dalam GitHub Actions.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

MYT = timezone(timedelta(hours=8))
HEARTBEAT_FILE = Path(".github/heartbeat.json")


def update_heartbeat_file() -> None:
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    now_myt = datetime.now(tz=MYT).isoformat()

    prev_count = 0
    if HEARTBEAT_FILE.exists():
        try:
            old_data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
            prev_count = int(old_data.get("heartbeat_count", 0))
        except Exception:
            prev_count = 0

    payload = {
        "status": "ACTIVE",
        "heartbeat_count": prev_count + 1,
        "last_ping_myt": now_myt,
        "repo": os.getenv("GITHUB_REPOSITORY", "local"),
        "note": "Auto-heartbeat untuk mengelakkan sekatan 60 hari tidak aktif GitHub Actions.",
    }
    HEARTBEAT_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"✅ Fail {HEARTBEAT_FILE} dikemas kini (Ping #{payload['heartbeat_count']} pada {now_myt}).")


def auto_enable_disabled_workflows() -> None:
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not repo or not token:
        print("ℹ️ Mod tempatan: GITHUB_REPOSITORY / GITHUB_TOKEN tiada, melangkau semakan API.")
        return

    api_url = f"https://api.github.com/repos/{repo}/actions/workflows"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GitHub-Actions-KeepAlive-Bot",
    }

    try:
        req = urllib.request.Request(api_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        workflows = data.get("workflows", [])
        re_enabled = 0
        for wf in workflows:
            wf_id = wf.get("id")
            wf_name = wf.get("name", "Unknown")
            wf_state = wf.get("state", "")

            if wf_state == "disabled_inactivity":
                enable_url = f"https://api.github.com/repos/{repo}/actions/workflows/{wf_id}/enable"
                en_req = urllib.request.Request(enable_url, headers=headers, method="PUT")
                with urllib.request.urlopen(en_req, timeout=20) as en_resp:
                    if en_resp.status in (200, 204):
                        re_enabled += 1
                        print(f"🔄 Berjaya aktifkan semula workflow: {wf_name} (ID: {wf_id})")

        if re_enabled == 0:
            print(f"✅ Semua {len(workflows)} workflow berada dalam keadaan aktif.")
    except urllib.error.HTTPError as exc:
        print(f"⚠️ Amaran GitHub API (HTTP {exc.code}): {exc.read().decode('utf-8', errors='ignore')[:160]}")
    except Exception as exc:
        print(f"⚠️ Amaran semakan workflow: {exc}")


if __name__ == "__main__":
    update_heartbeat_file()
    auto_enable_disabled_workflows()