#!/usr/bin/env python3
# ==============================================================================
# PROYEK: STREMIO PRIVATE DEBRID - DIAGNOSTIK KONEKSI REDIS & B2 MULTI-ACCOUNT
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/local_run/01_test_redis_b2_all_acc.py
# ==============================================================================

import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress_bar import ProgressBar

console = Console()

# 1. Konfigurasi Path File Lingkungan (.env.local)
BASE_DIR = Path("/home/braderdin/stremio-private-debrid")
ENV_LOCAL_FILE = BASE_DIR / ".env.local"

# Batas Kuota & Parameter Free-Tier
B2_SAFE_LIMIT_BYTES = int(9.5 * 1024 * 1024 * 1024)  # 9.5 GB per akun
B2_CEILING_LIMIT_BYTES = int(10.0 * 1024 * 1024 * 1024)  # 10.0 GB batas absolut
REDIS_MAX_MEMORY_BYTES = 256 * 1024 * 1024  # 256 MB RAM per akun Upstash


def format_size(bytes_val: int) -> str:
    """Mengonversi nilai byte menjadi format yang mudah dibaca (KB, MB, GB)."""
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.3f} GB"


def parse_env_local(filepath: Path) -> Dict[str, str]:
    """Membaca dan memetakan variabel dari file .env.local."""
    env_vars = {}
    if not filepath.exists():
        console.print(f"[bold red]❌ File konfigurasi tidak ditemukan: {filepath}[/bold red]")
        return env_vars

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                clean_key = key.strip()
                clean_val = val.strip().strip('"').strip("'")
                env_vars[clean_key] = clean_val
    return env_vars


def extract_redis_configs(env_data: Dict[str, str]) -> List[Dict[str, Any]]:
    """Mengekstrak konfigurasi 4 akun Upstash Redis dari .env.local."""
    accounts = []
    for idx in range(1, 11):
        formats = [f"{idx:03d}", f"{idx:02d}", f"{idx}"]
        url, token = None, None

        for fmt in formats:
            url_candidate = env_data.get(f"UPSTASH_REDIS_{fmt}_REST_URL")
            token_candidate = env_data.get(f"UPSTASH_REDIS_{fmt}_REST_TOKEN")
            if url_candidate and token_candidate:
                url = url_candidate
                token = token_candidate
                break

        if url and token:
            accounts.append({
                "index": len(accounts) + 1,
                "url": url.strip().rstrip("/"),
                "token": token.strip()
            })
    return accounts


def extract_b2_configs(env_data: Dict[str, str]) -> List[Dict[str, Any]]:
    """Mengekstrak konfigurasi 20 akun Backblaze B2 dari .env.local."""
    accounts = []
    for idx in range(1, 51):
        formats = [f"{idx:03d}", f"{idx:02d}", f"{idx}"]
        matched = False

        for fmt in formats:
            prefix = f"B2_ACC{fmt}_"
            key_id = env_data.get(f"{prefix}KEY_ID")
            app_key = env_data.get(f"{prefix}APP_KEY")
            bucket_name = env_data.get(f"{prefix}BUCKET_NAME")
            bucket_id = env_data.get(f"{prefix}BUCKET_ID", "")
            endpoint = env_data.get(f"{prefix}S3_API_ENDPOINT", "s3.us-west-004.backblazeb2.com")
            key_name = env_data.get(f"{prefix}KEY_NAME", f"key-{fmt}")

            if key_id and app_key and bucket_name:
                accounts.append({
                    "index": len(accounts) + 1,
                    "key_id": key_id,
                    "app_key": app_key,
                    "bucket_name": bucket_name,
                    "bucket_id": bucket_id,
                    "key_name": key_name,
                    "endpoint": endpoint
                })
                matched = True
                break

        if not matched and len(accounts) >= 20:
            break
    return accounts


def check_single_redis(client: httpx.Client, acc: Dict[str, Any]) -> Dict[str, Any]:
    """Menguji koneksi PING, DBSIZE, dan info memori pada akun Upstash Redis."""
    url = acc["url"]
    token = acc["token"]
    host = url.replace("https://", "").split("/")[0]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    start_t = time.perf_counter()
    try:
        # 1. PING Command
        ping_resp = client.post(url, headers=headers, json=["PING"], timeout=8.0)
        latency = (time.perf_counter() - start_t) * 1000

        if ping_resp.status_code != 200 or ping_resp.json().get("result") != "PONG":
            return {"index": acc["index"], "host": host, "online": False, "latency": latency, "keys": 0, "mem_str": "N/A", "mem_pct": 0.0, "status": "[bold red]OFFLINE[/bold red]"}

        # 2. DBSIZE Command
        dbsize_resp = client.post(url, headers=headers, json=["DBSIZE"], timeout=5.0)
        keys_count = int(dbsize_resp.json().get("result", 0)) if dbsize_resp.status_code == 200 else 0

        # 3. INFO Command (Memory)
        info_resp = client.post(url, headers=headers, json=["INFO"], timeout=5.0)
        raw_info = info_resp.json().get("result", "") if info_resp.status_code == 200 else ""
        
        used_memory = 0
        used_mem_str = "0B"
        for line in raw_info.splitlines():
            if line.startswith("used_memory:"):
                used_memory = int(line.split(":")[1].strip())
            elif line.startswith("used_memory_human:"):
                used_mem_str = line.split(":")[1].strip()

        mem_pct = (used_memory / REDIS_MAX_MEMORY_BYTES) * 100

        return {
            "index": acc["index"],
            "host": host,
            "online": True,
            "latency": latency,
            "keys": keys_count,
            "mem_str": used_mem_str,
            "mem_pct": mem_pct,
            "status": "[bold green]ONLINE[/bold green]"
        }
    except Exception:
        return {"index": acc["index"], "host": host, "online": False, "latency": 0.0, "keys": 0, "mem_str": "N/A", "mem_pct": 0.0, "status": "[bold red]ERROR[/bold red]"}


def check_single_b2(client: httpx.Client, acc: Dict[str, Any]) -> Dict[str, Any]:
    """Menguji otentikasi B2 dan memindai kuota penyimpanan file secara langsung."""
    key_id = acc["key_id"]
    app_key = acc["app_key"]
    bucket_name = acc["bucket_name"]
    bucket_id = acc["bucket_id"]

    start_t = time.perf_counter()
    try:
        # 1. Autentikasi B2 Native REST API
        auth_resp = client.get(
            "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
            auth=(key_id, app_key),
            timeout=10.0
        )
        latency = (time.perf_counter() - start_t) * 1000

        if auth_resp.status_code != 200:
            return {
                "index": acc["index"], "bucket_name": bucket_name, "online": False,
                "latency": latency, "files": 0, "bytes": 0, "pct": 0.0,
                "status": "[bold red]AUTH FAILED[/bold red]", "err": f"HTTP {auth_resp.status_code}"
            }

        auth_data = auth_resp.json()
        api_url = auth_data["apiUrl"]
        auth_token = auth_data["authorizationToken"]

        # Jika bucket_id belum terisi di .env.local, ambil dari daftar bucket resmi
        actual_bucket_id = bucket_id
        if not actual_bucket_id:
            acc_id = auth_data["accountId"]
            b_resp = client.post(
                f"{api_url}/b2api/v2/b2_list_buckets",
                headers={"Authorization": auth_token},
                json={"accountId": acc_id, "bucketName": bucket_name},
                timeout=8.0
            )
            if b_resp.status_code == 200:
                buckets = b_resp.json().get("buckets", [])
                if buckets:
                    actual_bucket_id = buckets[0]["bucketId"]

        # 2. Ambil Jumlah File & Ukuran Penyimpanan (Listing File Aktif)
        file_count = 0
        used_bytes = 0

        if actual_bucket_id:
            list_resp = client.post(
                f"{api_url}/b2api/v2/b2_list_file_names",
                headers={"Authorization": auth_token},
                json={"bucketId": actual_bucket_id, "maxFileCount": 1000},
                timeout=10.0
            )
            if list_resp.status_code == 200:
                files = list_resp.json().get("files", [])
                file_count = len(files)
                used_bytes = sum(f.get("contentLength", 0) for f in files)

        pct_used = (used_bytes / B2_SAFE_LIMIT_BYTES) * 100

        if used_bytes >= B2_SAFE_LIMIT_BYTES:
            status_text = "[bold red]PENUH (9.5GB)[/bold red]"
        elif pct_used > 70.0:
            status_text = "[bold yellow]ZONA KUNING[/bold yellow]"
        else:
            status_text = "[bold green]AKTIF (SIAP)[/bold green]"

        return {
            "index": acc["index"],
            "bucket_name": bucket_name,
            "online": True,
            "latency": latency,
            "files": file_count,
            "bytes": used_bytes,
            "pct": pct_used,
            "status": status_text,
            "err": ""
        }
    except Exception as e:
        return {
            "index": acc["index"], "bucket_name": bucket_name, "online": False,
            "latency": 0.0, "files": 0, "bytes": 0, "pct": 0.0,
            "status": "[bold red]ERROR[/bold red]", "err": str(e)
        }


def main():
    console.print(Panel.fit(
        "[bold cyan]🔍 DIAGNOSTIK KONEKSI & KUOTA UPSTASH REDIS + BACKBLAZE B2[/bold cyan]\n"
        "[yellow]Membaca langsung dari .env.local | Memverifikasi 4 Akun Redis & 20 Akun B2[/yellow]",
        title="Stremio Private Debrid Diagnostics",
        border_style="cyan"
    ))

    env_data = parse_env_local(ENV_LOCAL_FILE)
    if not env_data:
        return

    redis_confs = extract_redis_configs(env_data)
    b2_confs = extract_b2_configs(env_data)

    with httpx.Client(timeout=12.0) as client:
        # =========================================================================
        # 1. PENGUJIAN AKUN UPSTASH REDIS (4 SHARDS)
        # =========================================================================
        console.print(f"[cyan]1. Memeriksa status dan latensi {len(redis_confs)} Akun Upstash Redis...[/cyan]\n")
        redis_results = [check_single_redis(client, r) for r in redis_confs]

        redis_table = Table(title="📊 Status Infrastruktur Upstash Redis", border_style="cyan")
        redis_table.add_column("Akun", justify="center", style="cyan", no_wrap=True)
        redis_table.add_column("Host Upstash", style="white")
        redis_table.add_column("Latensi", justify="right", style="white")
        redis_table.add_column("Total Kunci", justify="right", style="yellow")
        redis_table.add_column("Penggunaan RAM", justify="right", style="white")
        redis_table.add_column("Status Koneksi", justify="center")

        total_keys = 0
        for res in redis_results:
            total_keys += res["keys"]
            label = f"#{res['index']} [bold magenta](Master)[/bold magenta]" if res["index"] == 1 else f"#{res['index']}"
            redis_table.add_row(
                label,
                res["host"],
                f"{res['latency']:.1f} ms" if res["online"] else "N/A",
                f"{res['keys']:,}",
                f"{res['mem_str']} ({res['mem_pct']:.1f}%)" if res["online"] else "N/A",
                res["status"]
            )
        console.print(redis_table)

        # =========================================================================
        # 2. PENGUJIAN AKUN BACKBLAZE B2 (20 BUCKETS)
        # =========================================================================
        console.print(f"\n[cyan]2. Memeriksa otentikasi dan kapasitas {len(b2_confs)} Akun Backblaze B2...[/cyan]\n")
        b2_results = [check_single_b2(client, b) for b in b2_confs]

        b2_table = Table(title="📦 Status Kapasitas & Kredensial 20 Akun Backblaze B2", border_style="green")
        b2_table.add_column("Akun", justify="center", style="cyan", no_wrap=True)
        b2_table.add_column("Nama Bucket", style="white")
        b2_table.add_column("Latensi", justify="right", style="dim")
        b2_table.add_column("File Aktif", justify="right", style="yellow")
        b2_table.add_column("Ukuran Terpakai", justify="right", style="white")
        b2_table.add_column("Kuota Terpakai (%)", justify="center", style="white")
        b2_table.add_column("Status Bucket", justify="center")

        total_b2_files = 0
        total_b2_bytes = 0
        for b_res in b2_results:
            total_b2_files += b_res["files"]
            total_b2_bytes += b_res["bytes"]
            b2_table.add_row(
                f"#{b_res['index']}",
                b_res["bucket_name"],
                f"{b_res['latency']:.0f} ms" if b_res["online"] else "N/A",
                f"{b_res['files']:,} file",
                format_size(b_res["bytes"]) if b_res["online"] else "N/A",
                f"{b_res['pct']:.2f}%",
                b_res["status"]
            )
        console.print(b2_table)

        # =========================================================================
        # 3. RINGKASAN TOTAL SISTEM
        # =========================================================================
        total_safe_capacity = len(b2_confs) * B2_SAFE_LIMIT_BYTES
        sys_pct_used = (total_b2_bytes / total_safe_capacity * 100) if total_safe_capacity > 0 else 0.0

        summary = Table(title="🌐 Ringkasan Total Sistem Penyimpanan & Metadata", border_style="cyan")
        summary.add_column("Komponen Sistem", style="cyan")
        summary.add_column("Status Operasional", style="green")
        summary.add_column("Total Metrik", style="yellow", justify="right")
        summary.add_column("Keterangan", style="white")

        online_redis = sum(1 for r in redis_results if r["online"])
        online_b2 = sum(1 for b in b2_results if b["online"])

        summary.add_row(
            "Upstash Redis",
            f"{online_redis} / {len(redis_confs)} Shard Aktif",
            f"{total_keys:,} Kunci",
            "Siap untuk pemetaan metadata & caching"
        )
        summary.add_row(
            "Backblaze B2",
            f"{online_b2} / {len(b2_confs)} Akun Aktif",
            f"{format_size(total_b2_bytes)} / {format_size(total_safe_capacity)}",
            f"{total_b2_files:,} video tersimpan ({sys_pct_used:.2f}% kuota aman terpakai)"
        )
        console.print(summary)
        console.print()


if __name__ == "__main__":
    main()