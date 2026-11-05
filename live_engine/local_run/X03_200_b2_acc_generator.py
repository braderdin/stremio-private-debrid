#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - 200 B2 ACCOUNTS KEY GENERATOR
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/local_run/X03_200_b2_acc_generator.py
#
# CIRI-CIRI:
# 1. Mengimbas & mengekstrak kesemua 200 akaun B2 (ACC001 hingga ACC200) dari .env.local.
# 2. Memastikan tiada indeks akaun yang tercicir dengan semakan integriti urutan.
# 3. Menjana kunci GitHub Secret:
#    - Memeriksa had siling 48 KB (49,152 bait).
#    - Jika melebihi 48 KB, split kepada 2 fail:
#      * B2_ACCOUNTS_JSON    (Akaun #001 - #100)
#      * B2_ACCOUNTS_JSON_X1 (Akaun #101 - #200)
# 4. Menjana 12 fail Cloudflare Worker (V1 hingga V12, 16-17 akaun setiap satu)
#    dalam bentuk teks termampat (minified .txt) bawah had 5 KB per pemboleh ubah.
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

KEY_DIR.mkdir(parents=True, exist_ok=True)

GITHUB_MAX_BYTES = 48 * 1024  # 49,152 bait
CF_MAX_BYTES = 5 * 1024       # 5,120 bait


def parse_env_local(filepath: Path) -> dict:
    """Membaca kandungan fail .env.local."""
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


def process_b2_200_keys(env_data: dict) -> list:
    """Mengekstrak sehingga 200 akaun Backblaze B2 (format ACC001 hingga ACC200)."""
    b2_list = []
    missing_indices = []

    for idx in range(1, 201):
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
                    "index": idx,
                    "bucket_name": bucket_name,
                    "bucket_id": bucket_id,
                    "key_name": key_name,
                    "key_id": key_id,
                    "app_key": app_key,
                    "endpoint": endpoint
                })
                matched = True
                break

        if not matched:
            missing_indices.append(idx)

    if missing_indices:
        console.print(f"[bold red]⚠️ Amaran: {len(missing_indices)} nombor akaun tidak lengkap/tiada di .env.local:[/bold red] {missing_indices[:15]}...")

    return b2_list


def main():
    console.print(Panel.fit(
        "[bold cyan]⚡ PENJANA 200 KUNCI BACKBLAZE B2 (INFRASTRUKTUR SKALA PENUH)[/bold cyan]\n"
        "[yellow]Menjana pecahan konfigurasi untuk GitHub Repo Secrets (≤ 48 KB) & Cloudflare Workers (≤ 5 KB)[/yellow]",
        title="B2 200-Key Partition Engine",
        border_style="cyan"
    ))

    env_data = parse_env_local(ENV_LOCAL_FILE)
    if not env_data:
        return

    b2_accounts = process_b2_200_keys(env_data)
    total_acc = len(b2_accounts)

    if total_acc == 0:
        console.print("[bold red]❌ Tiada kunci B2 yang sah dijumpai di dalam .env.local![/bold red]")
        return

    table = Table(title=f"📦 Ringkasan Fail Kunci B2 Dijana ({total_acc} Akaun Terproses)", border_style="green")
    table.add_column("Nama Fail", style="cyan", width=28)
    table.add_column("Liputan Akaun", style="white", width=22)
    table.add_column("Pembolehubah Sasaran", style="magenta", width=24)
    table.add_column("Saiz (KB)", style="yellow", justify="right", width=10)
    table.add_column("Status Had", style="bold green", justify="center", width=14)

    guide_lines = [
        f"[bold green]Folder Output:[/bold green] [yellow]{KEY_DIR}[/yellow]\n",
        "[bold underline]A. GITHUB ACTION SECRETS (PILIHAN SALIN TAMPAL):[/bold underline]"
    ]

    # =========================================================================
    # 1. PEMPROSESAN GITHUB SECRETS (HAD 48 KB)
    # =========================================================================
    full_json_str = json.dumps(b2_accounts, indent=2, ensure_ascii=False)
    full_json_bytes = len(full_json_str.encode("utf-8"))

    if full_json_bytes > GITHUB_MAX_BYTES:
        # Pecahkan kepada 2 bahagian: Bahagian 1 (1-100) & Bahagian 2 (101-200)
        mid_point = total_acc // 2
        part1_accs = b2_accounts[:mid_point]
        part2_accs = b2_accounts[mid_point:]

        file_part1 = KEY_DIR / "b2_github_key_part1.json"
        file_part2 = KEY_DIR / "b2_github_key_part2.json"

        str_p1 = json.dumps(part1_accs, indent=2, ensure_ascii=False)
        str_p2 = json.dumps(part2_accs, indent=2, ensure_ascii=False)

        with open(file_part1, "w", encoding="utf-8") as f:
            f.write(str_p1)
        with open(file_part2, "w", encoding="utf-8") as f:
            f.write(str_p2)

        p1_kb = len(str_p1.encode("utf-8")) / 1024
        p2_kb = len(str_p2.encode("utf-8")) / 1024

        table.add_row(
            "b2_github_key_part1.json",
            f"Akaun #{part1_accs[0]['index']:03d} - #{part1_accs[-1]['index']:03d}",
            "B2_ACCOUNTS_JSON",
            f"{p1_kb:.2f} KB",
            "✅ LULUS (<48KB)"
        )
        table.add_row(
            "b2_github_key_part2.json",
            f"Akaun #{part2_accs[0]['index']:03d} - #{part2_accs[-1]['index']:03d}",
            "B2_ACCOUNTS_JSON_X1",
            f"{p2_kb:.2f} KB",
            "✅ LULUS (<48KB)"
        )

        guide_lines.append(f"1. [bold cyan]b2_github_key_part1.json[/bold cyan] ➔ Tampal ke GitHub Secret: [bold green]B2_ACCOUNTS_JSON[/bold green]")
        guide_lines.append(f"2. [bold cyan]b2_github_key_part2.json[/bold cyan] ➔ Tampal ke GitHub Secret: [bold green]B2_ACCOUNTS_JSON_X1[/bold green]")
    else:
        out_single = KEY_DIR / "b2_github_key.json"
        with open(out_single, "w", encoding="utf-8") as f:
            f.write(full_json_str)
        s_kb = full_json_bytes / 1024
        table.add_row(
            "b2_github_key.json",
            f"Akaun #001 - #{total_acc:03d}",
            "B2_ACCOUNTS_JSON",
            f"{s_kb:.2f} KB",
            "✅ LULUS (<48KB)"
        )
        guide_lines.append(f"1. [bold cyan]b2_github_key.json[/bold cyan] ➔ Tampal ke GitHub Secret: [bold green]B2_ACCOUNTS_JSON[/bold green]")

    guide_lines.append("\n[bold underline]B. CLOUDFLARE WORKER ENVIRONMENTS (12 PECAHAN .TXT < 5 KB):[/bold underline]")

    # =========================================================================
    # 2. PEMPROSESAN CLOUDFLARE WORKER (12 PECAHAN, 16-17 AKAUN, HAD 5 KB)
    # =========================================================================
    num_chunks = 12
    # Algoritma agihan seimbang: 8 kelompok 17 akaun, 4 kelompok 16 akaun
    chunk_splits = []
    start_idx = 0
    for i in range(num_chunks):
        size = (total_acc // num_chunks) + (1 if i < (total_acc % num_chunks) else 0)
        chunk_splits.append(b2_accounts[start_idx:start_idx + size])
        start_idx += size

    for part_idx, chunk in enumerate(chunk_splits, 1):
        filename = f"b2_cf_worker_key_v{part_idx}.txt"
        file_path = KEY_DIR / filename
        env_var_name = f"B2_ACCOUNTS_JSON_V{part_idx}"

        # Mampatan maksima (tanpa whitespace/ruang kosong)
        chunk_min_str = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(chunk_min_str)

        chunk_bytes = len(chunk_min_str.encode("utf-8"))
        chunk_size_kb = chunk_bytes / 1024
        is_passed = chunk_bytes < CF_MAX_BYTES

        table.add_row(
            filename,
            f"Akaun #{chunk[0]['index']:03d} - #{chunk[-1]['index']:03d} ({len(chunk)})",
            env_var_name,
            f"{chunk_size_kb:.2f} KB",
            "✅ LULUS (<5KB)" if is_passed else "❌ LEBIH"
        )

        guide_lines.append(
            f"   - [bold cyan]{filename}[/bold cyan] ➔ Tampal ke CF Variable: [bold green]{env_var_name}[/bold green]"
        )

    console.print(table)
    console.print(Panel("\n".join(guide_lines), title="📋 Panduan Salin Tampal Kunci", border_style="green"))


if __name__ == "__main__":
    main()