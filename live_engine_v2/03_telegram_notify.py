#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V2 - TELEGRAM NOTIFIER ENGINE
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v2/03_telegram_notify.py
# CIRI: NOTIFIKASI SENARAI, SELESAI SEDUT & AMARAN RALAT GITHUB ACTIONS
# ==============================================================================

import os
import sys
import json
import zlib
import html
import urllib.parse
import urllib.request
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def load_env_local():
    """Membaca pembolehubah dari .env.local jika tiada di os.environ."""
    candidates = [
        ROOT_DIR / ".env.local",
        Path("/home/braderdin/stremio-private-debrid/.env.local"),
        CURRENT_DIR / ".env.local"
    ]
    for p in candidates:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
            break


load_env_local()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
REDIS_ACCOUNTS_RAW = os.environ.get("REDIS_ACCOUNTS_JSON", "").strip()


def calculate_crc32(key_str: str) -> int:
    """CRC32 sepadan dengan modul pangkalan data untuk kiraan modulo shard."""
    return zlib.crc32(key_str.encode("utf-8")) & 0xFFFFFFFF


def get_redis_shard_info(imdb_id: str) -> Tuple[int, Optional[str], Optional[str]]:
    """Mengesan shard index dan REST URL Redis berdasarkan IMDb ID."""
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
    """Membaca kunci daripada REST API Upstash Redis tanpa dependensi luar."""
    if not url or not token:
        return None
    req_url = f"{url}/get/{key}"
    req = urllib.request.Request(req_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                res = data.get("result")
                return json.loads(res) if isinstance(res, str) else res
    except Exception:
        return None
    return None


def format_bytes(bytes_val: int) -> str:
    """Menukar nilai bait kepada format GB atau MB yang kemas."""
    gb = bytes_val / (1024 * 1024 * 1024)
    if gb >= 1.0:
        return f"{gb:.2f} GB"
    mb = bytes_val / (1024 * 1024)
    return f"{mb:.2f} MB"


def send_telegram_message(text: str) -> bool:
    """Menghantar mesej Telegram dengan sokongan pemecahan had 4096 aksara."""
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID tiada. Notifikasi dilangkau.")
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    messages = []
    if len(text) <= 4000:
        messages.append(text)
    else:
        parts = text.split("\n\n")
        curr = ""
        for p in parts:
            if len(curr) + len(p) + 2 > 4000:
                messages.append(curr.strip())
                curr = p + "\n\n"
            else:
                curr += p + "\n\n"
        if curr.strip():
            messages.append(curr.strip())

    success = True
    for msg in messages:
        payload = {
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=data_bytes,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    success = False
        except Exception as e:
            print(f"❌ Gagal menghantar Telegram: {e}")
            success = False
    return success


def notify_seeder_list(imdb_id: str, title: str):
    """Membina dan menghantar notifikasi selesai carian senarai seeder."""
    shard_num, shard_url, shard_token = get_redis_shard_info(imdb_id)
    cached_list = fetch_redis_data(shard_url, shard_token, f"stremio:list:{imdb_id}") if shard_url else []

    total_found = len(cached_list) if isinstance(cached_list, list) else 0
    clean_title = html.escape(title)

    lines = [
        "📋 <b>[V2 SMART PICKER: SENARAI TORRENT SIAP]</b>",
        "",
        f"🎬 <b>Tajuk:</b> {clean_title}",
        f"🆔 <b>IMDb:</b> <code>{imdb_id}</code>",
        f"🔍 <b>Jumlah Calon Dijumpai:</b> <b>{total_found} torrent</b>",
        f"🗄 <b>Pangkalan Data:</b> Upstash Redis Shard #{shard_num}",
        "",
        "🌟 <b>Pilihan Seeder Tertinggi:</b>"
    ]

    if isinstance(cached_list, list) and cached_list:
        for idx, item in enumerate(cached_list[:6], start=1):
            name = html.escape(item.get("name", "Torrent"))
            q = item.get("quality", "HD")
            sz = format_bytes(item.get("size", 0))
            seeds = item.get("seeders", 0)
            lines.append(f"{idx}. <b>[{q}]</b> 👤 <code>{seeds} Seeds</code> | 💾 {sz}\n   └ <i>{name}</i>")
    else:
        lines.append("<i>(Senarai sedia di Stremio Addon untuk pemilihan)</i>")

    lines.append("")
    lines.append("⚡ <i>Buka Stremio TV sekarang untuk memilih kualiti dan muat turun!</i>")
    send_telegram_message("\n".join(lines))


def notify_download_complete(imdb_id: str, info_hash: str, title: str):
    """Membina dan menghantar notifikasi selesai muat turun fail ke B2."""
    shard_num, shard_url, shard_token = get_redis_shard_info(imdb_id)
    meta = fetch_redis_data(shard_url, shard_token, f"stremio:{imdb_id}") if shard_url else None

    matched = None
    if isinstance(meta, dict):
        if "streams" in meta and isinstance(meta["streams"], list):
            for s in meta["streams"]:
                if s.get("info_hash", "").lower() == info_hash.lower():
                    matched = s
                    break
            if not matched and meta["streams"]:
                matched = meta["streams"][0]
        else:
            matched = meta

    file_name = html.escape(matched.get("file_name", "video.mp4") if matched else "video.mp4")
    file_size = format_bytes(matched.get("size_bytes", 0)) if matched else "Tidak diketahui"
    res = matched.get("resolution", "HD") if matched else "HD"
    b2_acc_idx = matched.get("b2_account_index", 1) if matched else 1
    b2_bucket = matched.get("b2_bucket", "B2-Storage") if matched else "B2-Storage"
    disp_title = html.escape(matched.get("title", title) if matched else title)

    lines = [
        "🎉 <b>[B2 DEBRID: MUAT TURUN SELESAI]</b>",
        "",
        f"🎬 <b>Tajuk:</b> {disp_title}",
        f"🎞 <b>Kualiti:</b> <b>{res}</b>",
        f"📦 <b>Saiz Fail:</b> <b>{file_size}</b>",
        f"📁 <b>Nama Fail:</b> <code>{file_name}</code>",
        f"🆔 <b>IMDb:</b> <code>{imdb_id}</code>",
        f"🔑 <b>InfoHash:</b> <code>{info_hash.lower()}</code>",
        "",
        "☁️ <b>Destinasi Storan:</b>",
        f"├ <b>Akaun B2:</b> Akaun #{b2_acc_idx} (<code>{b2_bucket}</code>)",
        f"└ <b>Audit Redis:</b> Shard #{shard_num}",
        "",
        "⚡ <b>Status:</b> Sedia ditonton lancar di Stremio menerusi <i>[⚡ B2 Fast Stream]</i>!"
    ]
    send_telegram_message("\n".join(lines))


def notify_job_error(imdb_id: str, title: str, workflow_name: str, error_msg: str):
    """Membina dan menghantar notifikasi kecemasan apabila alur kerja GitHub Actions gagal."""
    clean_title = html.escape(title)
    clean_wf = html.escape(workflow_name)
    clean_err = html.escape(error_msg)

    lines = [
        "🚨 <b>[AMARAN: GITHUB ACTIONS GAGAL]</b>",
        "",
        f"🎬 <b>Tajuk:</b> {clean_title}",
        f"🆔 <b>IMDb:</b> <code>{imdb_id}</code>",
        f"⚙️ <b>Workflow:</b> <code>{clean_wf}</code>",
        f"❌ <b>Punca Ralat:</b> <i>{clean_err}</i>",
        "",
        "⚠️ <i>Sila semak log penuh di GitHub Actions untuk tindakan pembetulan.</i>"
    ]
    send_telegram_message("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stremio B2 Telegram Notifier")
    parser.add_argument("--mode", required=True, choices=["list", "download", "error"], help="Mod notifikasi")
    parser.add_argument("--imdb", required=True, help="IMDb ID filem")
    parser.add_argument("--hash", default="", help="InfoHash torrent")
    parser.add_argument("--title", default="", help="Tajuk filem")
    parser.add_argument("--workflow", default="Workflow Engine", help="Nama fail workflow")
    parser.add_argument("--error", default="Ralat semasa pelaksanaan tugasan.", help="Mesej ralat terperinci")

    args = parser.parse_args()

    clean_title = args.title.strip() or args.imdb
    if args.mode == "list":
        notify_seeder_list(args.imdb.strip(), clean_title)
    elif args.mode == "download":
        notify_download_complete(args.imdb.strip(), args.hash.strip(), clean_title)
    elif args.mode == "error":
        notify_job_error(args.imdb.strip(), clean_title, args.workflow.strip(), args.error.strip())