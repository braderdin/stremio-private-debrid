import os
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

# =============================================================================
# [TETAPAN MANUAL PENGGUNA DI BAHAGIAN TERATAS]
# =============================================================================
TARGET_WEB_URL = "https://tv.lk21official.us"      # Tukar ke web sasaran anda
SEARCH_KEYWORD = "shaolin soccer 2001"          # Kata kunci carian anda
# =============================================================================

CURRENT_FILE_PATH = Path(__file__).resolve()
SCRAPE_DIR = CURRENT_FILE_PATH.parent
BASE_PROJECT_DIR = SCRAPE_DIR.parent
DOWNLOAD_DIR = BASE_PROJECT_DIR / "exp_download"

if str(SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPE_DIR))

from X01_step01_search_nav import execute_step01, human_delay
from X01_step02_ytdlp_extract import download_media_1080p
from camoufox.sync_api import Camoufox

console = Console()


def main():
    console.print(Panel.fit(
        f"[bold yellow]EKSPERIMEN AUTOMATIK CAMOUFOX + YT-DLP (STREAM SNIFFER)[/bold yellow]\n"
        f"Laman Sasaran : [cyan]{TARGET_WEB_URL}[/cyan]\n"
        f"Input Carian  : [cyan]{SEARCH_KEYWORD}[/cyan]\n"
        f"Folder Output : [green]{DOWNLOAD_DIR}[/green]",
        title="Ujian Aliran Penuh"
    ))

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    user_agent_gecko = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"

    console.print("[cyan][*] Melancarkan pelayar Camoufox (Mod Paparan Terbuka: GUI ON)...[/cyan]")
    with Camoufox(headless=False, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=user_agent_gecko
        )
        page = context.new_page()

        try:
            # ---------------------------------------------------------
            # LANGKAH 1: Navigasi, Carian & Pintas Strim Sebenar
            # ---------------------------------------------------------
            active_page, media_info = execute_step01(
                page=page,
                target_url=TARGET_WEB_URL,
                search_query=SEARCH_KEYWORD
            )

            human_delay(2.0, 3.5)

            # ---------------------------------------------------------
            # LANGKAH 2: Muat Turun Melalui yt-dlp Menggunakan Pautan Pintasan
            # ---------------------------------------------------------
            download_result = download_media_1080p(
                media_info=media_info,
                download_folder=DOWNLOAD_DIR,
                custom_user_agent=user_agent_gecko
            )

            # ---------------------------------------------------------
            # Ringkasan Keputusan
            # ---------------------------------------------------------
            console.print("\n" + "=" * 65)
            if download_result["status"] == "success":
                console.print("[bold green]🎉 PROSES BERJAYA DISELESAIKAN![/bold green]")
                console.print(f"📁 Lokasi Fail: {download_result['file_path']}")
            else:
                console.print("[bold yellow]⚠️ Peringatan: Proses selesai tetapi muat turun memerlukan semakan strim.[/bold yellow]")
                console.print(f"Info Ralat: {download_result.get('error')}")
            console.print("=" * 65 + "\n")

            console.print("[cyan][*] Menutup pelayar dalam masa 5 saat...[/cyan]")
            human_delay(4.0, 5.0)

        finally:
            try:
                page.close()
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()