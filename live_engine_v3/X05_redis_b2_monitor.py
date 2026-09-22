#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - LIVE ENGINE V3
# FAIL  : X05_redis_b2_monitor.py
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X05_redis_b2_monitor.py
# FUNGSI: Papan Pemuka Diagnostik Bersepadu Bagi 50 Akaun B2 & 4 Shard Upstash Redis
# ==============================================================================

import os
import sys
import json
import time
import base64
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple

try:
    from curl_cffi import requests
except ImportError:
    import requests

from dotenv import dotenv_values
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress_bar import ProgressBar

console = Console()

# ------------------------------------------------------------------------------
# 1. KONFIGURASI LALUAN & FAIL PERSEKITARAN (.env.local)
# ------------------------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
DATA_DIR = PROJECT_ROOT / "data"
DAILY_TRACKER_FILE = DATA_DIR / "redis_daily_tracker.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Parameter Had Kapasiti & Ambang Selamat
B2_MAX_SAFE_BYTES = int(9.5 * 1024 * 1024 * 1024)      # Had selamat: 9.5 GB per akaun
B2_FREE_TIER_BYTES = int(10.0 * 1024 * 1024 * 1024)    # Had siling percuma: 10.0 GB per akaun
REDIS_MAX_MEMORY_BYTES = 256 * 1024 * 1024             # Had RAM: 256 MB per akaun
REDIS_MAX_DAILY_CMDS = 10000                           # Had mutlak Upstash: 10,000 cmds/hari
REDIS_SAFE_DAILY_CMDS = 5000                          # Had selamat lokal: 5,000 cmds/hari


# ------------------------------------------------------------------------------
# 2. PENGESAN KUNCI PINTAR (B2_ACC001..050 & UPSTASH_REDIS_001..004)
# ------------------------------------------------------------------------------
def clean_val(val: Optional[str]) -> str:
    """Membersihkan ruang kosong dan tanda petik ganda/tunggal."""
    if not val:
        return ""
    return str(val).strip().strip('"').strip("'")


def load_environment_credentials() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Mengekstrak akaun B2 (1-50) dan Upstash Redis (1-4) dari .env.local."""
    env_vars = {}
    if ENV_LOCAL_PATH.exists():
        env_vars = dotenv_values(str(ENV_LOCAL_PATH))
    else:
        env_vars = dict(os.environ)

    # A. Ekstrak Akaun Backblaze B2 (Imbas dari 001 hingga 050)
    b2_accounts: List[Dict[str, Any]] = []

    for i in range(1, 51):
        # Menyokong format 3 digit (ACC001), 2 digit (ACC01) dan 1 digit (ACC1)
        prefixes = [f"B2_ACC{i:03d}_", f"B2_ACC{i:02d}_", f"B2_ACC{i}_"]
        
        for pfx in prefixes:
            key_id = clean_val(env_vars.get(f"{pfx}KEY_ID") or os.getenv(f"{pfx}KEY_ID"))
            app_key = clean_val(env_vars.get(f"{pfx}APP_KEY") or os.getenv(f"{pfx}APP_KEY"))
            b_name = clean_val(env_vars.get(f"{pfx}BUCKET_NAME") or os.getenv(f"{pfx}BUCKET_NAME"))
            b_id = clean_val(env_vars.get(f"{pfx}BUCKET_ID") or os.getenv(f"{pfx}BUCKET_ID"))
            s3_ep = clean_val(env_vars.get(f"{pfx}S3_API_ENDPOINT") or os.getenv(f"{pfx}S3_API_ENDPOINT"))

            if key_id and app_key and b_name:
                b2_accounts.append({
                    "index": i,
                    "key_id": key_id,
                    "app_key": app_key,
                    "bucket_name": b_name,
                    "bucket_id": b_id,
                    "s3_endpoint": s3_ep
                })
                break

    # B. Ekstrak Akaun Upstash Redis (Kekal 4 Akaun: 001 hingga 004)
    redis_accounts: List[Dict[str, Any]] = []

    for i in range(1, 5):
        prefixes = [f"UPSTASH_REDIS_{i:03d}_", f"UPSTASH_REDIS_{i:02d}_", f"UPSTASH_REDIS_{i}_"]

        for pfx in prefixes:
            r_url = clean_val(env_vars.get(f"{pfx}REST_URL") or os.getenv(f"{pfx}REST_URL"))
            r_tok = clean_val(env_vars.get(f"{pfx}REST_TOKEN") or os.getenv(f"{pfx}REST_TOKEN"))

            if r_url and r_tok:
                redis_accounts.append({
                    "index": i,
                    "url": r_url.rstrip("/"),
                    "token": r_tok
                })
                break

    # Sandaran JSON sekiranya format teks tiada
    if not redis_accounts:
        raw_json = env_vars.get("REDIS_ACCOUNTS_JSON") or os.getenv("REDIS_ACCOUNTS_JSON")
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, list):
                    redis_accounts = parsed
            except Exception:
                pass

    return b2_accounts, redis_accounts


# ------------------------------------------------------------------------------
# 3. MODUL DIAGNOSTIK BACKBLAZE B2 (REST API ASLI)
# ------------------------------------------------------------------------------
def format_size(bytes_val: int) -> str:
    """Menukar bait kepada unit KB, MB, atau GB."""
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.3f} GB"


def inspect_b2_account(acc: Dict[str, Any]) -> Dict[str, Any]:
    """Mengira saiz storan dan bilangan fail fizikal via B2 REST API."""
    idx = acc["index"]
    key_id = acc["key_id"]
    app_key = acc["app_key"]
    bucket_name = acc["bucket_name"]
    bucket_id = acc.get("bucket_id", "")

    res = {
        "index": idx,
        "bucket_name": bucket_name,
        "online": False,
        "files": 0,
        "bytes": 0,
        "pct": 0.0,
        "latency_ms": 0.0,
        "status": "[bold red]RALAT[/bold red]",
        "error": ""
    }

    start_t = time.perf_counter()
    try:
        # 1. Authorize Account
        auth_pair = f"{key_id}:{app_key}"
        b64_auth = base64.b64encode(auth_pair.encode("utf-8")).decode("utf-8")

        auth_resp = requests.get(
            "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
            headers={"Authorization": f"Basic {b64_auth}"},
            timeout=12
        )
        if auth_resp.status_code != 200:
            res["error"] = f"Auth Gagal: HTTP {auth_resp.status_code}"
            return res

        auth_data = auth_resp.json()
        api_url = auth_data.get("apiUrl")
        auth_token = auth_data.get("authorizationToken")
        account_id = auth_data.get("accountId")

        # Cari bucket_id jika belum diisi di .env.local
        if not bucket_id:
            b_resp = requests.post(
                f"{api_url}/b2api/v2/b2_list_buckets",
                headers={"Authorization": auth_token},
                json={"accountId": account_id, "bucketName": bucket_name},
                timeout=12
            )
            if b_resp.status_code == 200:
                for b in b_resp.json().get("buckets", []):
                    if b.get("bucketName") == bucket_name:
                        bucket_id = b.get("bucketId")
                        break

        if not bucket_id:
            res["error"] = "Baldi tidak ditemui"
            return res

        # 2. Imbas Fail dalam Baldi
        file_count = 0
        used_bytes = 0
        next_file_name = None

        while True:
            payload: Dict[str, Any] = {"bucketId": bucket_id, "maxFileCount": 1000}
            if next_file_name:
                payload["startFileName"] = next_file_name

            list_resp = requests.post(
                f"{api_url}/b2api/v2/b2_list_file_names",
                headers={"Authorization": auth_token},
                json=payload,
                timeout=25
            )
            if list_resp.status_code != 200:
                break

            list_data = list_resp.json()
            files = list_data.get("files", [])
            for f in files:
                file_count += 1
                used_bytes += f.get("contentLength", 0)

            next_file_name = list_data.get("nextFileName")
            if not next_file_name or len(files) == 0:
                break

        res["online"] = True
        res["files"] = file_count
        res["bytes"] = used_bytes
        res["pct"] = (used_bytes / B2_MAX_SAFE_BYTES) * 100

        if used_bytes >= B2_MAX_SAFE_BYTES:
            res["status"] = "[bold red]ZON MERAH (PENUH)[/bold red]"
        elif res["pct"] >= 70.0:
            res["status"] = "[bold yellow]ZON KUNING[/bold yellow]"
        else:
            res["status"] = "[bold green]ZON HIJAU (AKTIF)[/bold green]"

    except Exception as e:
        res["error"] = str(e)
    finally:
        res["latency_ms"] = (time.perf_counter() - start_t) * 1000

    return res


# ------------------------------------------------------------------------------
# 4. MODUL DIAGNOSTIK UPSTASH REDIS (REST API & DAILY USAGE)
# ------------------------------------------------------------------------------
def execute_redis_command(url: str, token: str, command: str, *args) -> Tuple[Optional[dict], float]:
    """Menghantar arahan ke Upstash REST API dan mengira latensi."""
    if not url or not token:
        return None, 0.0

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    target_url = url.rstrip("/")
    payload = [command] + [str(a) for a in args]

    start_t = time.perf_counter()
    try:
        resp = requests.post(target_url, headers=headers, json=payload, timeout=8)
        latency = (time.perf_counter() - start_t) * 1000
        if resp.status_code == 200:
            return resp.json(), latency
    except Exception:
        pass
    return None, 0.0


def parse_redis_info(raw_text: str) -> Dict[str, str]:
    info = {}
    for line in raw_text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return info


def load_tracker_data() -> dict:
    if DAILY_TRACKER_FILE.exists():
        try:
            with open(DAILY_TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_tracker_data(tracker: dict):
    try:
        with open(DAILY_TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(tracker, f, indent=2)
    except Exception:
        pass


def calculate_daily_usage(acc_idx: int, total_cmds: int, tracker: dict, today_utc: str) -> int:
    acc_key = str(acc_idx)
    acc_data = tracker.get("accounts", {}).get(acc_key, {})
    baseline = acc_data.get("start_baseline", total_cmds)

    if tracker.get("date_utc") != today_utc or total_cmds < baseline:
        baseline = total_cmds

    if "accounts" not in tracker:
        tracker["accounts"] = {}

    tracker["accounts"][acc_key] = {
        "start_baseline": baseline,
        "last_checked_commands": total_cmds
    }
    return max(0, total_cmds - baseline)


def inspect_redis_account(acc: Dict[str, Any], tracker: dict, today_utc: str) -> Dict[str, Any]:
    idx = acc["index"]
    url = acc["url"]
    token = acc["token"]
    host = url.replace("https://", "").split("/")[0]

    res = {
        "index": idx,
        "host": host,
        "online": False,
        "latency": 0.0,
        "keys": 0,
        "used_mem_str": "N/A",
        "mem_pct": 0.0,
        "cmds_today": 0,
        "status": "[bold red]OFFLINE[/bold red]"
    }

    ping_res, latency = execute_redis_command(url, token, "PING")
    if not ping_res or ping_res.get("result") != "PONG":
        return res

    res["online"] = True
    res["latency"] = latency

    # Ambil DBSIZE
    dbsize_res, _ = execute_redis_command(url, token, "DBSIZE")
    res["keys"] = int(dbsize_res.get("result", 0)) if dbsize_res else 0

    # Ambil INFO
    info_res, _ = execute_redis_command(url, token, "INFO")
    raw_info = info_res.get("result", "") if info_res else ""
    info_data = parse_redis_info(raw_info)

    used_mem = int(info_data.get("used_memory", 0))
    res["used_mem_str"] = info_data.get("used_memory_human", "0B")
    res["mem_pct"] = (used_mem / REDIS_MAX_MEMORY_BYTES) * 100

    total_cmds = int(info_data.get("total_commands_processed", 0))
    cmds_today = calculate_daily_usage(idx, total_cmds, tracker, today_utc)
    res["cmds_today"] = cmds_today

    if cmds_today < 3000:
        res["status"] = "[bold green]ZON HIJAU[/bold green]"
    elif cmds_today < 5000:
        res["status"] = "[bold yellow]ZON KUNING[/bold yellow]"
    else:
        res["status"] = "[bold red]ZON MERAH (CAP)[/bold red]"

    return res


# ------------------------------------------------------------------------------
# 5. PELAKSANAAN UTAMA & PAPARAN DIAGNOSTIK
# ------------------------------------------------------------------------------
def run_monitor(check_b2: bool = True, check_redis: bool = True):
    console.print(Panel.fit(
        f"[bold cyan]⚡ STREMIO PRIVATE DEBRID & SUB-ADDON INFRASTRUCTURE MONITOR[/bold cyan]\n"
        f"Laluan Fail Rahsia: [yellow]{ENV_LOCAL_PATH}[/yellow]\n"
        f"Pemeriksaan Aktif : [bold magenta]{'B2 (Sedia Sehingga 50) & Redis (4 Shard)' if (check_b2 and check_redis) else ('B2 Sahaja' if check_b2 else 'Redis Sahaja')}[/bold magenta]",
        border_style="cyan"
    ))

    b2_accs, redis_accs = load_environment_credentials()

    # =========================================================================
    # BAHAGIAN 1: DIAGNOSTIK BACKBLAZE B2
    # =========================================================================
    if check_b2:
        console.print(f"\n[bold yellow]🔍 Mengesan {len(b2_accs)} Akaun Backblaze B2 di .env.local...[/bold yellow]")
        if not b2_accs:
            console.print("[dim red]⚠️ Tiada format kunci B2_ACC001..050 dikesan di .env.local![/dim red]")
        else:
            b2_results = []
            total_b2_files = 0
            total_b2_bytes = 0

            for acc in b2_accs:
                r = inspect_b2_account(acc)
                b2_results.append(r)
                total_b2_files += r["files"]
                total_b2_bytes += r["bytes"]

            table_b2 = Table(title=f"📊 Status Kapasiti {len(b2_accs)} Akaun Backblaze B2", border_style="cyan")
            table_b2.add_column("Akaun", justify="center", style="cyan", width=8)
            table_b2.add_column("Nama Baldi / Bucket", style="white")
            table_b2.add_column("Latensi", justify="right", style="dim", width=10)
            table_b2.add_column("Jumlah Fail", justify="right", style="yellow", width=12)
            table_b2.add_column("Storan Digunakan", justify="right", style="white", width=16)
            table_b2.add_column("Peratus 9.5GB", justify="center", style="white", width=14)
            table_b2.add_column("Status Akaun", justify="center", width=22)

            for b in b2_results:
                if not b["online"]:
                    table_b2.add_row(f"#{b['index']:03d}", b["bucket_name"], "N/A", "0", "N/A", "0.0%", b["status"])
                else:
                    table_b2.add_row(
                        f"#{b['index']:03d}",
                        b["bucket_name"],
                        f"{b['latency_ms']:.0f} ms",
                        f"{b['files']:,} fail",
                        format_size(b["bytes"]),
                        f"{b['pct']:.2f}%",
                        b["status"]
                    )
            console.print(table_b2)

            sys_b2_safe = len(b2_accs) * B2_MAX_SAFE_BYTES
            pct_b2_sys = (total_b2_bytes / sys_b2_safe) * 100 if sys_b2_safe else 0
            baki_b2_bytes = max(0, sys_b2_safe - total_b2_bytes)

            sum_b2 = Table(title=f"🌐 Ringkasan Agregat Storan B2 ({len(b2_accs)} Akaun Aktif)", border_style="green")
            sum_b2.add_column("Metrik B2", style="cyan")
            sum_b2.add_column("Nilai Semasa", style="white")
            sum_b2.add_column("Kapasiti Sistem", style="white")

            sum_b2.add_row("Jumlah Fail Tersimpan", f"[bold yellow]{total_b2_files:,} fail[/bold yellow]", "Fail Torrent & Sarikata")
            sum_b2.add_row("Jumlah Storan Digunakan", f"[bold green]{format_size(total_b2_bytes)}[/bold green]", f"Had Selamat: {format_size(sys_b2_safe)}")
            sum_b2.add_row("Baki Storan Selamat", f"[bold cyan]{format_size(baki_b2_bytes)}[/bold cyan]", f"Maks Percuma: {format_size(len(b2_accs) * B2_FREE_TIER_BYTES)}")
            sum_b2.add_row("Akaun Aktif Berfungsi", f"{sum(1 for x in b2_results if x['online'])} / {len(b2_accs)} akaun", "[green]Siap Sedia[/green]")
            console.print(sum_b2)

            console.print(f"[bold]Kemajuan Storan B2 ({format_size(total_b2_bytes)} / {format_size(sys_b2_safe)}):[/bold]")
            bar_b2 = ProgressBar(total=100, completed=min(100.0, pct_b2_sys), width=50)
            console.print(bar_b2)

    # =========================================================================
    # BAHAGIAN 2: DIAGNOSTIK UPSTASH REDIS (4 SHARD)
    # =========================================================================
    if check_redis:
        console.print(f"\n[bold yellow]🔍 Mengesan {len(redis_accs)} Shard Upstash Redis di .env.local...[/bold yellow]")
        if not redis_accs:
            console.print("[dim red]⚠️ Tiada format UPSTASH_REDIS_001..004 dikesan di .env.local![/dim red]")
        else:
            today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            tracker = load_tracker_data()
            if tracker.get("date_utc") != today_utc:
                tracker = {"date_utc": today_utc, "accounts": {}}

            redis_results = []
            total_keys = 0
            total_cmds_today = 0

            for acc in redis_accs:
                r = inspect_redis_account(acc, tracker, today_utc)
                redis_results.append(r)
                total_keys += r["keys"]
                total_cmds_today += r["cmds_today"]

            tracker["date_utc"] = today_utc
            tracker["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_tracker_data(tracker)

            table_r = Table(title=f"📊 Status Penggunaan {len(redis_accs)} Shard Upstash Redis ({today_utc} UTC)", border_style="cyan")
            table_r.add_column("Shard", justify="center", style="cyan", width=8)
            table_r.add_column("Host Upstash", style="white")
            table_r.add_column("Latensi", justify="right", style="white", width=10)
            table_r.add_column("Kunci (DBSIZE)", justify="right", style="yellow", width=14)
            table_r.add_column("Penggunaan RAM", justify="right", style="white", width=16)
            table_r.add_column("Panggilan Hari Ini", justify="right", style="white", width=18)
            table_r.add_column("Status Zon", justify="center", width=18)

            for r in redis_results:
                lbl = f"#{r['index']:03d}"
                if not r["online"]:
                    table_r.add_row(lbl, r["host"], "N/A", "0", "N/A", "0", r["status"])
                else:
                    cmd_txt = f"{r['cmds_today']:,} / {REDIS_SAFE_DAILY_CMDS:,}"
                    table_r.add_row(
                        lbl,
                        r["host"],
                        f"{r['latency']:.1f} ms",
                        f"{r['keys']:,}",
                        f"{r['used_mem_str']} ({r['mem_pct']:.1f}%)",
                        cmd_txt,
                        r["status"]
                    )
            console.print(table_r)

            total_safe_cmds_sys = len(redis_accs) * REDIS_SAFE_DAILY_CMDS
            pct_cmds_sys = (total_cmds_today / total_safe_cmds_sys) * 100 if total_safe_cmds_sys else 0
            baki_cmds_sys = max(0, total_safe_cmds_sys - total_cmds_today)

            sum_r = Table(title=f"🌐 Ringkasan Agregat Upstash Redis ({len(redis_accs)}-Shard)", border_style="green")
            sum_r.add_column("Metrik Redis", style="cyan")
            sum_r.add_column("Nilai Semasa", style="white")
            sum_r.add_column("Kapasiti Had Sistem", style="white")

            sum_r.add_row("Jumlah Panggilan Hari Ini", f"[bold green]{total_cmds_today:,} commands[/bold green]", f"Had Selamat: {total_safe_cmds_sys:,}")
            sum_r.add_row("Kapasiti Mutlak Upstash", f"{total_cmds_today:,} commands", f"{len(redis_accs) * REDIS_MAX_DAILY_CMDS:,} commands/hari")
            sum_r.add_row("Jumlah Kunci Tersimpan", f"[bold yellow]{total_keys:,} kunci[/bold yellow]", "Merentasi 4 Shard")
            sum_r.add_row("Baki Kuota Selamat Lokal", f"[bold cyan]{baki_cmds_sys:,} commands[/bold cyan]", f"Baki Selamat Hari Ini")
            sum_r.add_row("Waktu Reset Kuota Upstash", "00:00 UTC Harian", "[cyan]8:00 Pagi Waktu Malaysia (MYT)[/cyan]")
            console.print(sum_r)

            console.print(f"[bold]Kemajuan Had Panggilan Sistem ({total_cmds_today:,} / {total_safe_cmds_sys:,} commands):[/bold]")
            bar_r = ProgressBar(total=100, completed=min(100.0, pct_cmds_sys), width=50)
            console.print(bar_r)

    console.print("\n[bold green]✔ Pemeriksaan kesihatan infrastruktur selesai dengan jayanya![/bold green]\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pemeriksa Had Kuota & Kapasiti Storan B2 & Upstash Redis")
    parser.add_argument("--b2-only", action="store_true", help="Semak akaun B2 sahaja")
    parser.add_argument("--redis-only", action="store_true", help="Semak akaun Upstash Redis sahaja")
    args = parser.parse_args()

    c_b2 = not args.redis_only
    c_red = not args.b2_only

    run_monitor(check_b2=c_b2, check_redis=c_red)