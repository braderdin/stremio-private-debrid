#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - NOTIFIKASI TELEGRAM BERPUSAT
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/X06_telegram_scrape_notify.py
# CIRI: NOTIFIKASI MULA CARIAN, SELESAI SCRAPE KE B2 & AMARAN RALAT GITHUB ACTIONS
# ==============================================================================

import os
import sys
import json
import zlib
import html
import urllib.request
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

for p in [CURRENT_DIR, PROJECT_ROOT]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

import X00_scrape_config as cfg

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
REDIS_ACCOUNTS_RAW = os.environ.get("REDIS_ACCOUNTS_JSON", "").strip() or os.environ.get("REDIS_MULTI_ACCOUNT_JSON", "").strip()


def calculate_crc32(key_str: str) -> int:
    return zlib.crc32(key_str.encode("utf-8")) & 0xFFFFFFFF


def get_redis_shard_info(imdb_id: str) -> Tuple[int, Optional[str], Optional[str]]:
    if not REDIS_ACCOUNTS_RAW:
        return 1, None, None
    try:
        accounts = json.loads(REDIS_ACCOUNTS_RAW)
        if isinstance(accounts, list) and accounts:
            idx = calculate_crc32(imdb_id) % len(accounts)
            target = accounts[idx]
            shard_num = target.get("index", idx + 1)
            url = (target.get("redis_rest_url") or target.get("url", "")).rstrip("/")
            token = target.get("redis_rest_token") or target.get("token", "")
            return shard_num, url, token
    except Exception:
        pass
    return 1, None, None


def fetch_redis_data(url: str, token: str, key: str) -> Optional[Any]:
    if not url or not token:
        return None
    req_url = f"{url}/get/{key}"
    req = urllib.request.Request(req_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                res = data.get("result")
                return json.loads(res) if isinstance(res, str) else res
    except Exception:
        return None
    return None


def format_bytes(bytes_val: int) -> str:
    gb = bytes_val / (1024 * 1024 * 1024)
    if gb >= 1.0:
        return f"{gb:.2f} GB"
    mb = bytes_val / (1024 * 1024)
    return f"{mb:.2f} MB"


def send_telegram_message(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID tiada. Notifikasi dilangkau.")
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(api_url, data=data_bytes, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"❌ Gagal menghantar Telegram: {e}")
        return False


def notify_scrape_start(imdb_id: str, title: str, query: str = ""):
    clean_title = html.escape(title)
    clean_query = html.escape(query or title)
    lines = [
        "🌐 <b>[WEB SCRAPER: MEMULAKAN PROSES CARIAN]</b>",
        "",
        f"🎬 <b>Tajuk Stremio:</b> {clean_title}",
        f"🆔 <b>IMDb ID:</b> <code>{imdb_id}</code>",
        f"🔎 <b>Sasaran Carian Web:</b> <i>{clean_query}</i>",
        f"🎯 <b>Portal Sasaran:</b> <code>{cfg.TARGET_WEB_URL}</code>",
        "",
        "⏳ <i>Pelayar Camoufox sedang mengimbas portal & menjejak pautan video...</i>"
    ]
    send_telegram_message("\n".join(lines))


def notify_scrape_complete(imdb_id: str, title: str, quality: str = "1080p", info_hash: str = ""):
    shard_num, shard_url, shard_token = get_redis_shard_info(imdb_id)
    meta = fetch_redis_data(shard_url, shard_token, f"stremio:{imdb_id}") if shard_url else None

    matched = None
    if isinstance(meta, dict):
        if "streams" in meta and isinstance(meta["streams"], list):
            for s in meta["streams"]:
                if info_hash and s.get("info_hash", "").lower() == info_hash.lower():
                    matched = s
                    break
            if not matched and meta["streams"]:
                matched = meta["streams"][0]
        else:
            matched = meta

    file_name = html.escape(matched.get("file_name", "scraped_video.mp4") if matched else "scraped_video.mp4")
    file_size = format_bytes(matched.get("size_bytes", 0)) if matched else "N/A"
    res = matched.get("resolution", quality) if matched else quality
    b2_acc_idx = matched.get("b2_account_index", 1) if matched else 1
    b2_bucket = matched.get("b2_bucket", "B2-Storage") if matched else "B2-Storage"
    disp_title = html.escape(matched.get("title", title) if matched else title)

    lines = [
        "🎉 <b>[WEB SCRAPER: BERJAYA DIMUAT NAIK KE B2]</b>",
        "",
        f"🎬 <b>Tajuk:</b> {disp_title}",
        f"🎞 <b>Kualiti:</b> <b>{res}</b>",
        f"📦 <b>Saiz Fail:</b> <b>{file_size}</b>",
        f"📁 <b>Nama Fail:</b> <code>{file_name}</code>",
        f"🆔 <b>IMDb:</b> <code>{imdb_id}</code>",
        "",
        "☁️ <b>Destinasi Storan:</b>",
        f"├ <b>Akaun B2:</b> Akaun #{b2_acc_idx} (<code>{b2_bucket}</code>)",
        f"└ <b>Audit Redis:</b> Shard #{shard_num}",
        "",
        "⚡ <b>Status:</b> Sedia ditonton lancar di Stremio tanpa iklan web!"
    ]
    send_telegram_message("\n".join(lines))


def notify_scrape_error(imdb_id: str, title: str, workflow_name: str, error_msg: str):
    clean_title = html.escape(title)
    clean_wf = html.escape(workflow_name)
    clean_err = html.escape(error_msg)
    lines = [
        "🚨 <b>[AMARAN: WEB SCRAPER GAGAL]</b>",
        "",
        f"🎬 <b>Tajuk:</b> {clean_title}",
        f"🆔 <b>IMDb:</b> <code>{imdb_id}</code>",
        f"⚙️ <b>Workflow:</b> <code>{clean_wf}</code>",
        f"❌ <b>Punca Ralat:</b> <i>{clean_err}</i>",
        "",
        "⚠️ <i>Sila semak log penuh di GitHub Actions untuk pengesahan lanjut.</i>"
    ]
    send_telegram_message("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Web Scraper Telegram Notifier")
    parser.add_argument("--mode", required=True, choices=["start", "download", "error"], help="Mod notifikasi")
    parser.add_argument("--imdb", required=True, help="IMDb ID media")
    parser.add_argument("--title", default="", help="Tajuk media")
    parser.add_argument("--query", default="", help="Kata carian")
    parser.add_argument("--quality", default="1080p", help="Kualiti video")
    parser.add_argument("--hash", default="", help="InfoHash media")
    parser.add_argument("--workflow", default="X05_series_web_download.yml", help="Nama workflow")
    parser.add_argument("--error", default="Ralat tidak diketahui.", help="Mesej ralat")

    args = parser.parse_args()
    clean_title = args.title.strip() or args.imdb

    if args.mode == "start":
        notify_scrape_start(args.imdb.strip(), clean_title, args.query.strip())
    elif args.mode == "download":
        notify_scrape_complete(args.imdb.strip(), clean_title, args.quality.strip(), args.hash.strip())
    elif args.mode == "error":
        notify_scrape_error(args.imdb.strip(), clean_title, args.workflow.strip(), args.error.strip())