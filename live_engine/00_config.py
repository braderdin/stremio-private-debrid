#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - MODUL KONFIGURASI PUSAT
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/00_config.py
# ==============================================================================

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# 1. Konfigurasi Laluan Direktori Projek
LIVE_ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = LIVE_ENGINE_DIR.parent
DATA_DIR = LIVE_ENGINE_DIR / "data"
TEMP_DIR = LIVE_ENGINE_DIR / "temp"
OUTPUT_DIR = LIVE_ENGINE_DIR / "output"

# Pastikan folder fizikal wujud secara automatik
for folder in [DATA_DIR, TEMP_DIR, OUTPUT_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# 2. Muat Turun Pembolehubah Persekitaran (.env.local untuk Local Run)
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
if ENV_LOCAL_PATH.exists():
    load_dotenv(ENV_LOCAL_PATH)
else:
    load_dotenv()

# 3. Kunci Keselamatan, Cloudflare Workers & GitHub API
ADDON_SECRET_TOKEN = os.getenv("ADDON_SECRET_TOKEN", "")
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "")
CF_WORKER_B2_PROXY_STORAGE = os.getenv("CF_WORKER_B2_PROXY_STORAGE", "")
CF_WORKER_RENDER_PING = os.getenv("CF_WORKER_RENDER_PING", "")

GH_PAT = os.getenv("GH_PAT", "")
GH_OWNER = os.getenv("GH_OWNER", "braderdin")
GH_REPO = os.getenv("GH_REPO", "stremio-private-debrid")

# 4. Konfigurasi Render API
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "")

# 5. Had Kapasiti & Ambang Selamat (Free-Tier Thresholds)
B2_MAX_BYTES_PER_ACCOUNT = int(9.5 * 1024 * 1024 * 1024)   # Ambang selamat: 9.5 GB / akaun
B2_CEILING_LIMIT_BYTES = int(10.0 * 1024 * 1024 * 1024)    # Had mutlak percuma: 10.0 GB / akaun
UPSTASH_MAX_FREE_DAILY_COMMANDS = 10000                     # Had panggilan Upstash harian
UPSTASH_MAX_MEMORY_BYTES = 256 * 1024 * 1024               # Had RAM: 256 MB / akaun


# 6. Pengekstrakan 4 Akaun Upstash Redis (Sharding)
def get_redis_accounts() -> list:
    """
    Mengekstrak akaun Redis:
    Keutamaan 1: Membaca format JSON dari GitHub Secret (REDIS_ACCOUNTS_JSON).
    Keutamaan 2: Membaca dari .env.local (UPSTASH_REDIS_001_REST_URL hingga 004).
    """
    accounts = []

    # Keutamaan 1: GitHub Action Secret / CF Secret
    redis_json_raw = os.getenv("REDIS_ACCOUNTS_JSON", "").strip() or os.getenv("REDIS_MULTI_ACCOUNT_JSON", "").strip()
    if redis_json_raw:
        try:
            parsed = json.loads(redis_json_raw)
            if isinstance(parsed, list) and len(parsed) > 0:
                for idx, item in enumerate(parsed, 1):
                    if isinstance(item, dict):
                        url = str(item.get("redis_rest_url") or item.get("url") or "").strip()
                        token = str(item.get("redis_rest_token") or item.get("token") or "").strip()
                        if url and token:
                            accounts.append({
                                "index": int(item.get("index", idx)),
                                "url": url.rstrip("/"),
                                "token": token,
                                "redis_rest_url": url.rstrip("/"),
                                "redis_rest_token": token
                            })
                if accounts:
                    accounts.sort(key=lambda x: x["index"])
                    return accounts
        except Exception as e:
            print(f"⚠️ Ralat membaca REDIS_ACCOUNTS_JSON: {e}. Beralih ke .env.local...")

    # Keutamaan 2: Imbas format .env.local (001 hingga 010)
    for idx in range(1, 11):
        formats = [f"{idx:03d}", f"{idx:02d}", f"{idx}"]
        url, token = None, None

        for fmt in formats:
            url_candidate = os.getenv(f"UPSTASH_REDIS_{fmt}_REST_URL")
            token_candidate = os.getenv(f"UPSTASH_REDIS_{fmt}_REST_TOKEN")
            if url_candidate and token_candidate:
                url = url_candidate
                token = token_candidate
                break

        if url and token:
            accounts.append({
                "index": len(accounts) + 1,
                "url": url.strip().rstrip("/"),
                "token": token.strip(),
                "redis_rest_url": url.strip().rstrip("/"),
                "redis_rest_token": token.strip()
            })

    return accounts


REDIS_ACCOUNTS = get_redis_accounts()

# Pemetaan Akaun Master #1 untuk Keserasian Modul
if REDIS_ACCOUNTS:
    UPSTASH_REDIS_REST_URL = REDIS_ACCOUNTS[0]["url"]
    UPSTASH_REDIS_REST_TOKEN = REDIS_ACCOUNTS[0]["token"]
else:
    UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL", "")
    UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")


# 7. Pengekstrakan 20 Akaun Backblaze B2 (Multi-Bucket Storage)
def get_b2_accounts() -> list:
    """
    Mengekstrak akaun B2:
    Keutamaan 1: Membaca format JSON dari GitHub Secret (B2_ACCOUNTS_JSON).
    Keutamaan 2: Membaca dari .env.local (B2_ACC001_KEY_ID hingga B2_ACC020_KEY_ID).
    """
    accounts = []

    # Keutamaan 1: GitHub Action Secret
    b2_json_raw = os.getenv("B2_ACCOUNTS_JSON", "").strip() or os.getenv("B2_MULTI_ACCOUNT_JSON", "").strip()
    if b2_json_raw:
        try:
            parsed = json.loads(b2_json_raw)
            if isinstance(parsed, list) and len(parsed) > 0:
                for idx, item in enumerate(parsed, 1):
                    if isinstance(item, dict):
                        key_id = str(item.get("key_id", "")).strip()
                        app_key = str(item.get("app_key", "")).strip()
                        bucket_name = str(item.get("bucket_name", "")).strip()

                        if key_id and app_key and bucket_name:
                            accounts.append({
                                "index": int(item.get("index", idx)),
                                "key_id": key_id,
                                "app_key": app_key,
                                "bucket_name": bucket_name,
                                "bucket_id": str(item.get("bucket_id", "")).strip(),
                                "key_name": str(item.get("key_name", f"key-{idx:03d}")).strip(),
                                "endpoint": str(item.get("endpoint", "s3.us-west-004.backblazeb2.com")).strip()
                            })
                if accounts:
                    accounts.sort(key=lambda x: x["index"])
                    return accounts
        except Exception as e:
            print(f"⚠️ Ralat membaca B2_ACCOUNTS_JSON: {e}. Beralih ke .env.local...")

    # Keutamaan 2: Imbas format .env.local (ACC001 hingga ACC050)
    for idx in range(1, 51):
        formats = [f"{idx:03d}", f"{idx:02d}", f"{idx}"]
        matched = False

        for fmt in formats:
            prefix = f"B2_ACC{fmt}_"
            key_id = os.getenv(f"{prefix}KEY_ID")
            app_key = os.getenv(f"{prefix}APP_KEY")
            bucket_name = os.getenv(f"{prefix}BUCKET_NAME")
            bucket_id = os.getenv(f"{prefix}BUCKET_ID", "")
            endpoint = os.getenv(f"{prefix}S3_API_ENDPOINT", "s3.us-west-004.backblazeb2.com")
            key_name = os.getenv(f"{prefix}KEY_NAME", f"key-{fmt}")

            if key_id and app_key and bucket_name:
                accounts.append({
                    "index": len(accounts) + 1,
                    "key_id": key_id.strip(),
                    "app_key": app_key.strip(),
                    "bucket_name": bucket_name.strip(),
                    "bucket_id": bucket_id.strip() if bucket_id else "",
                    "key_name": key_name.strip(),
                    "endpoint": endpoint.strip()
                })
                matched = True
                break

        if not matched and len(accounts) >= 20:
            break

    return accounts


B2_ACCOUNTS = get_b2_accounts()


# 8. Ujian Diagnostik Kendiri Ringkas (Apabila dijalankan secara terus)
if __name__ == "__main__":
    print("=" * 65)
    print("🧪 UJIAN PENGESAHAN MODUL 00_CONFIG.PY")
    print("=" * 65)
    print(f"📁 Project Root: {PROJECT_ROOT}")
    print(f"🔑 Addon Token : {'✅ Wujud' if ADDON_SECRET_TOKEN else '❌ Tiada'}")
    print(f"🐙 GitHub Repo : {GH_OWNER}/{GH_REPO}")
    print(f"🚀 Render Key  : {'✅ Wujud' if RENDER_API_KEY else '❌ Tiada'}")
    print("-" * 65)
    print(f"📦 Akaun Upstash Redis Dikesan : {len(REDIS_ACCOUNTS)} Akaun")
    for r in REDIS_ACCOUNTS:
        print(f"   - Shard #{r['index']}: {r['url'].split('@')[-1].replace('https://', '')}")
    print("-" * 65)
    print(f"🪣 Akaun Backblaze B2 Dikesan   : {len(B2_ACCOUNTS)} Akaun")
    for b in B2_ACCOUNTS[:3]:
        print(f"   - Bucket #{b['index']}: {b['bucket_name']} ({b['endpoint']})")
    if len(B2_ACCOUNTS) > 3:
        print(f"   - ... dan {len(B2_ACCOUNTS) - 3} akaun B2 lagi.")
    print("=" * 65)