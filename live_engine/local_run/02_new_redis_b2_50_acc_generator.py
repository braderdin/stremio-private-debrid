#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - 50 B2 ACCOUNTS KEY GENERATOR
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/local_run/02_new_redis_b2_50_acc_generator.py
#
# CIRI-CIRI:
# 1. Mengekstrak sehingga 50 akaun B2 (ACC001 hingga ACC050) dari .env.local.
# 2. Menjana fail gabungan 50 akaun penuh untuk GitHub Secret (B2_ACCOUNTS_JSON).
# 3. Menjana 5 pecahan fail termampat (V1 hingga V5, 10 akaun setiap satu) untuk
#    mematuhi had siling 5 KB per pemboleh ubah Cloudflare Worker.
# 4. Paparan jadual Rich untuk pengesahan saiz bait dan status pelepasan.
# ==============================================================================

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

# Pastikan folder sasaran 'key' wujud
KEY_DIR.mkdir(parents=True, exist_ok=True)

OUT_B2_GITHUB = KEY_DIR / "b2_github_key.json"


def parse_env_local(filepath: Path) -> dict:
    """Membaca kandungan fail .env.local dan mengekstrak pemboleh ubah persekitaran."""
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


def process_b2_50_keys(env_data: dict) -> list:
    """Mengekstrak sehingga 50 akaun Backblaze B2 (format ACC001 hingga ACC050)."""
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

    return b2_list


def main():
    console.print(Panel.fit(
        "[bold cyan]⚡ PENJANA 50 KUNCI BACKBLAZE B2 (V3 INFRASTRUCTURE)[/bold cyan]\n"
        "[yellow]Menjana fail konfigurasi untuk GitHub Repo Secrets & Cloudflare Workers (Had 5 KB)[/yellow]",
        title="B2 Key Partition Engine",
        border_style="cyan"
    ))

    env_data = parse_env_local(ENV_LOCAL_FILE)
    if not env_data:
        return

    b2_accounts = process_b2_50_keys(env_data)
    total_acc = len(b2_accounts)

    if total_acc == 0:
        console.print("[bold red]❌ Tiada kunci B2 yang sah dijumpai di dalam .env.local![/bold red]")
        return

    # 1. Simpan b2_github_key.json (Kesemua 50 Akaun dengan Indent Penuh)
    github_json_str = json.dumps(b2_accounts, indent=2, ensure_ascii=False)
    with open(OUT_B2_GITHUB, "w", encoding="utf-8") as f:
        f.write(github_json_str)
    github_size_kb = len(github_json_str.encode("utf-8")) / 1024

    # 2. Pecahkan akaun kepada kelompok 10 akaun per fail (Chunking 10 Accounts)
    chunk_size = 10
    chunks = [b2_accounts[i:i + chunk_size] for i in range(0, total_acc, chunk_size)]

    table = Table(title=f"📦 Ringkasan Kunci B2 Berjaya Dijana ({total_acc} Akaun)", border_style="green")
    table.add_column("Nama Fail", style="cyan", width=26)
    table.add_column("Liputan Akaun", style="white", width=22)
    table.add_column("Sasaran Platform", style="magenta", width=26)
    table.add_column("Saiz (KB)", style="yellow", justify="right", width=10)
    table.add_column("Had 5 KB CF", style="bold green", justify="center", width=12)

    # Tambah baris fail GitHub Secret
    table.add_row(
        "b2_github_key.json",
        f"Akaun #001 - #{total_acc:03d} (Semua)",
        "GitHub Actions Secret",
        f"{github_size_kb:.2f} KB",
        "-"
    )

    guide_lines = [
        f"[bold green]Folder Output:[/bold green] [yellow]{KEY_DIR}[/yellow]\n",
        f"1. [bold cyan]b2_github_key.json[/bold cyan] ➔ Tampal ke GitHub Secret: [bold green]B2_ACCOUNTS_JSON[/bold green] ({total_acc} Akaun)\n"
    ]

    # Simpan pecahan Cloudflare Worker (V1, V2, V3, V4, V5...)
    for part_idx, chunk in enumerate(chunks, 1):
        filename = f"b2_cf_worker_key_v{part_idx}.txt"
        file_path = KEY_DIR / filename
        env_var_name = f"B2_ACCOUNTS_JSON_V{part_idx}"

        # Mampatkan JSON (Minified tanpa ruang putih kosong)
        chunk_min_str = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(chunk_min_str)

        chunk_size_kb = len(chunk_min_str.encode("utf-8")) / 1024
        is_passed = chunk_size_kb < 5.0

        table.add_row(
            filename,
            f"Akaun #{chunk[0]['index']:03d} - #{chunk[-1]['index']:03d}",
            f"CF Worker: {env_var_name}",
            f"{chunk_size_kb:.2f} KB",
            "✅ LULUS" if is_passed else "❌ LEBIH"
        )

        guide_lines.append(
            f"{part_idx + 1}. [bold cyan]{filename}[/bold cyan] ➔ Tampal ke CF Worker Env: [bold green]{env_var_name}[/bold green]"
        )

    console.print(table)
    console.print(Panel("\n".join(guide_lines), title="📋 Panduan Penyimpanan Kunci", border_style="green"))


if __name__ == "__main__":
    main()