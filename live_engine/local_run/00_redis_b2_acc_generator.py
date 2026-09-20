import os
import json
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# 1. Konfigurasi Laluan Direktori & Fail
BASE_DIR = Path("/home/braderdin/stremio-private-debrid")
ENV_LOCAL_FILE = BASE_DIR / ".env.local"
LOCAL_RUN_DIR = BASE_DIR / "live_engine" / "local_run"
KEY_DIR = LOCAL_RUN_DIR / "key"

# Cipta folder key jika belum wujud
KEY_DIR.mkdir(parents=True, exist_ok=True)

# 2. Sasaran 5 Fail Output
OUT_B2_GITHUB = KEY_DIR / "b2_github_key.json"
OUT_B2_CF_V1 = KEY_DIR / "b2_cf_worker_key_v1.txt"
OUT_B2_CF_V2 = KEY_DIR / "b2_cf_worker_key_v2.txt"

OUT_REDIS_GITHUB = KEY_DIR / "redis_github_key.json"
OUT_REDIS_CF = KEY_DIR / "redis_cf_worker_key.txt"


def parse_env_local(filepath: Path) -> dict:
    """Membaca kandungan fail .env.local dan mengekstrak pembolehubah persekitaran."""
    env_vars = {}
    if not filepath.exists():
        console.print(f"[bold red]❌ Fail persekitaran tidak wujud: {filepath}[/bold red]")
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


def process_redis_keys(env_data: dict) -> list:
    """Mengekstrak akaun Upstash Redis (menyokong format 001-004, 01-04, atau 1-4)."""
    redis_list = []
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
            redis_list.append({
                "index": len(redis_list) + 1,
                "redis_rest_url": url.strip().rstrip("/"),
                "redis_rest_token": token.strip()
            })

    return redis_list


def process_b2_keys(env_data: dict) -> list:
    """Mengekstrak sehingga 20 akaun Backblaze B2 (menyokong format ACC001-ACC020)."""
    b2_list = []
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
                b2_list.append({
                    "index": len(b2_list) + 1,
                    "bucket_name": bucket_name,
                    "bucket_id": bucket_id,
                    "key_name": key_name,
                    "key_id": key_id,
                    "app_key": app_key,
                    "endpoint": endpoint
                })
                matched = True
                break

        if not matched and len(b2_list) >= 20:
            break

    return b2_list


def main():
    console.print(Panel.fit(
        "[bold cyan]⚡ PENJANA GABUNGAN KUNCI B2 & UPSTASH REDIS[/bold cyan]\n"
        "[yellow]Menjana fail konfigurasi untuk GitHub Repo Secrets & Cloudflare Workers (Had 5KB)[/yellow]",
        title="Stremio Private Debrid Key Manager",
        border_style="cyan"
    ))

    env_data = parse_env_local(ENV_LOCAL_FILE)
    if not env_data:
        return

    # --- PROSES REDIS (4 AKAUN) ---
    redis_accounts = process_redis_keys(env_data)
    if not redis_accounts:
        console.print("[bold red]❌ Tiada kunci Upstash Redis yang sah dijumpai di dalam .env.local![/bold red]")
        return

    # 1. Simpan redis_github_key.json (Full Indent)
    with open(OUT_REDIS_GITHUB, "w", encoding="utf-8") as f:
        json.dump(redis_accounts, f, indent=2, ensure_ascii=False)

    # 2. Simpan redis_cf_worker_key.txt (Minified)
    redis_min_str = json.dumps(redis_accounts, ensure_ascii=False, separators=(',', ':'))
    with open(OUT_REDIS_CF, "w", encoding="utf-8") as f:
        f.write(redis_min_str)

    redis_size_kb = len(redis_min_str.encode('utf-8')) / 1024

    # --- PROSES B2 (20 AKAUN) ---
    b2_accounts = process_b2_keys(env_data)
    if not b2_accounts:
        console.print("[bold red]❌ Tiada kunci B2 yang sah dijumpai di dalam .env.local![/bold red]")
        return

    # 3. Simpan b2_github_key.json (Full Indent untuk GitHub Secrets)
    with open(OUT_B2_GITHUB, "w", encoding="utf-8") as f:
        json.dump(b2_accounts, f, indent=2, ensure_ascii=False)

    # Pisahkan kepada V1 (1-10) dan V2 (11-20) untuk had 5KB Cloudflare Worker
    mid_point = len(b2_accounts) // 2
    b2_v1 = b2_accounts[:mid_point]
    b2_v2 = b2_accounts[mid_point:]

    # 4. Simpan b2_cf_worker_key_v1.txt (Minified)
    b2_v1_str = json.dumps(b2_v1, ensure_ascii=False, separators=(',', ':'))
    with open(OUT_B2_CF_V1, "w", encoding="utf-8") as f:
        f.write(b2_v1_str)

    # 5. Simpan b2_cf_worker_key_v2.txt (Minified)
    b2_v2_str = json.dumps(b2_v2, ensure_ascii=False, separators=(',', ':'))
    with open(OUT_B2_CF_V2, "w", encoding="utf-8") as f:
        f.write(b2_v2_str)

    b2_v1_kb = len(b2_v1_str.encode('utf-8')) / 1024
    b2_v2_kb = len(b2_v2_str.encode('utf-8')) / 1024

    # --- JADUAL PAPARAN STATUS RICH ---
    table = Table(title="📦 Ringkasan 5 Kunci Berjaya Dijana", border_style="green")
    table.add_column("Nama Fail", style="cyan")
    table.add_column("Kandungan", style="white")
    table.add_column("Sasaran Platform", style="magenta")
    table.add_column("Saiz (KB)", style="yellow", justify="right")
    table.add_column("Had 5KB CF", style="bold green", justify="center")

    table.add_row(
        "redis_cf_worker_key.txt",
        f"{len(redis_accounts)} Akaun Redis (Minified)",
        "Cloudflare Worker Secret",
        f"{redis_size_kb:.2f} KB",
        "✅ LULUS" if redis_size_kb < 5.0 else "❌ LEBIH"
    )
    table.add_row(
        "redis_github_key.json",
        f"{len(redis_accounts)} Akaun Redis (Full JSON)",
        "GitHub Actions Secret",
        f"{(len(json.dumps(redis_accounts, indent=2).encode('utf-8')) / 1024):.2f} KB",
        "-"
    )
    table.add_row(
        "b2_cf_worker_key_v1.txt",
        f"Akaun #{b2_v1[0]['index']} - #{b2_v1[-1]['index']} (Minified)",
        "Cloudflare Worker Secret",
        f"{b2_v1_kb:.2f} KB",
        "✅ LULUS" if b2_v1_kb < 5.0 else "❌ LEBIH"
    )
    table.add_row(
        "b2_cf_worker_key_v2.txt",
        f"Akaun #{b2_v2[0]['index']} - #{b2_v2[-1]['index']} (Minified)",
        "Cloudflare Worker Secret",
        f"{b2_v2_kb:.2f} KB",
        "✅ LULUS" if b2_v2_kb < 5.0 else "❌ LEBIH"
    )
    table.add_row(
        "b2_github_key.json",
        f"{len(b2_accounts)} Akaun B2 (Full JSON)",
        "GitHub Actions Secret",
        f"{(len(json.dumps(b2_accounts, indent=2).encode('utf-8')) / 1024):.2f} KB",
        "-"
    )

    console.print(table)

    console.print(Panel(
        f"[bold green]Folder Output:[/bold green] [yellow]{KEY_DIR}[/yellow]\n\n"
        "1. [bold cyan]b2_github_key.json[/bold cyan] ➔ Tampal ke GitHub Secret: [bold green]B2_ACCOUNTS_JSON[/bold green]\n"
        "2. [bold cyan]redis_github_key.json[/bold cyan] ➔ Tampal ke GitHub Secret: [bold green]REDIS_ACCOUNTS_JSON[/bold green]\n"
        "3. [bold cyan]redis_cf_worker_key.txt[/bold cyan] ➔ Tampal ke CF Worker Env: [bold green]REDIS_ACCOUNTS_JSON[/bold green]\n"
        "4. [bold cyan]b2_cf_worker_key_v1.txt[/bold cyan] ➔ Tampal ke CF Worker Env: [bold green]B2_ACCOUNTS_JSON_V1[/bold green]\n"
        "5. [bold cyan]b2_cf_worker_key_v2.txt[/bold cyan] ➔ Tampal ke CF Worker Env: [bold green]B2_ACCOUNTS_JSON_V2[/bold green]",
        title="📋 Panduan Penyimpanan Kunci",
        border_style="green"
    ))


if __name__ == "__main__":
    main()