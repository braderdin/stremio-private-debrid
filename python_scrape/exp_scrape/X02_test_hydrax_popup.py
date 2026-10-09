import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import unquote
from rich.console import Console
from rich.panel import Panel

# =============================================================================
# [RUANG MANUAL: TAMPAL PAUTAN POPUP ANDA DI SINI]
# =============================================================================
MANUAL_POPUP_URL = "https://xxxxxxxxx.us/windowPopup?url=https%3A%2F%2Fvideonode.de%2Fiframe3%2Fhydrax%2FlxLZ92GoDQe2Sx6WtEzrMQ"
# =============================================================================

# Konfigurasi Laluan Folder & Modul
CURRENT_FILE_PATH = Path(__file__).resolve()
EXP_SCRAPE_DIR = CURRENT_FILE_PATH.parent
PYTHON_SCRAPE_DIR = EXP_SCRAPE_DIR.parent
BASE_PROJECT_DIR = PYTHON_SCRAPE_DIR.parent
DOWNLOAD_DIR = BASE_PROJECT_DIR / "exp_download"

if str(EXP_SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_SCRAPE_DIR))
if str(PYTHON_SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_SCRAPE_DIR))

from camoufox.sync_api import Camoufox
from playwright.sync_api import Page, Error

from X01_step02_ytdlp_extract import download_media_1080p

console = Console()


def sniff_and_control_hydrax(page: Page, wait_timeout: int = 30) -> Dict[str, Any]:
    """Mengawal pemain Hydrax di dalam iframe, memilih resolusi 1080p/720p, dan menjejak strim."""
    console.print("\n[bold yellow][*] Menganalisis pemain video popup Hydrax...[/bold yellow]")

    intercepted = {
        "status": "failed",
        "server": "HYDRAX",
        "stream_url": "",
        "referer": page.url,
        "quality": "unknown",
        "error": None
    }

    # Tutup sebarang pop-up tab iklan bertindih
    def on_ad_popup(new_tab: Page):
        try:
            time.sleep(0.3)
            if not page.is_closed() and new_tab != page:
                console.print(f"[yellow][!] Menutup tab iklan bertindih: {new_tab.url[:55]}...[/yellow]")
                new_tab.close()
                page.bring_to_front()
        except Exception:
            pass

    try:
        page.context.on("page", on_ad_popup)
    except Exception:
        pass

    def is_valid_media_target(url: str, content_type: str = "") -> bool:
        u_lower = url.lower()
        base_clean = u_lower.split("?")[0]
        ct = content_type.lower()

        # Tolak penjejak iklan dan fail statik web
        if any(bad in u_lower for bad in [
            "youtube.com", "googlevideo.com", "doubleclick", "analytics",
            "adsterra", "monetag", "histats", "popads", "clarity.ms"
        ]):
            return False

        if any(base_clean.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".json", ".html"]):
            return False
        if any(bad_ct in ct for bad_ct in ["text/html", "text/css", "application/javascript"]):
            return False

        # Pautan manifes HLS/DASH atau MP4
        if any(base_clean.endswith(ext) for ext in [".m3u8", ".mpd", ".mp4", ".mkv", ".webm"]):
            return True
        if any(kw in u_lower for kw in [".m3u8", "master.m3u8", "playlist.m3u8"]):
            return True
        if any(vct in ct for vct in ["application/vnd.apple.mpegurl", "application/x-mpegurl", "video/mp4"]):
            return True

        return False

    def on_network_traffic(url: str, headers: dict, ct: str = ""):
        # Rekod pengesanan API serpihan binari untuk diagnosis terminal
        if "videonode.de" in url.lower() and any(p in url.lower() for p in ["ping", "track", "slice", "data"]):
            console.print(f"[dim cyan]    [Trafik Data Hydrax] {url[:75]}...[/dim cyan]")

        if not is_valid_media_target(url, ct):
            return

        base_url = url.split("?")[0].lower()
        if base_url.endswith(".ts") or base_url.endswith(".m4s"):
            return

        quality = "unknown"
        if "1080" in url.lower():
            quality = "1080p"
        elif "720" in url.lower():
            quality = "720p"
        elif "master.m3u8" in url.lower() or "playlist.m3u8" in url.lower():
            quality = "adaptive_hls"

        # Tolak resolusi rendah (480p ke bawah)
        if "480" in url.lower() or "360" in url.lower():
            return

        current_q = intercepted["quality"]
        priority_rank = {"1080p": 3, "adaptive_hls": 2, "720p": 1, "unknown": 0}

        if priority_rank.get(current_q, 0) > priority_rank.get(quality, 0):
            return

        intercepted["status"] = "success"
        intercepted["stream_url"] = url
        intercepted["quality"] = quality
        intercepted["referer"] = headers.get("referer", page.url)

    def on_req(req):
        try:
            if req.resource_type in ["media", "fetch", "xhr"]:
                on_network_traffic(req.url, req.headers)
        except Exception:
            pass

    def on_res(res):
        try:
            ct = res.headers.get("content-type", "")
            on_network_traffic(res.url, res.request.headers, ct)
        except Exception:
            pass

    try:
        page.on("request", on_req)
        page.on("response", on_res)
    except Exception:
        pass

    # 1. Cetus Butang Play merentasi semua frame (termasuk iframe videonode)
    console.print("[cyan][*] Mencetuskan Play pada pemain video...[/cyan]")
    play_selectors = [
        'button[aria-label*="play" i]',
        '.vjs-big-play-button',
        '.jw-display-icon-display',
        'button.play-button',
        'button.play',
        '#play',
        'video'
    ]

    for attempt in range(1, 4):
        if intercepted["stream_url"] or page.is_closed():
            break

        for scope in [page] + page.frames:
            for p_sel in play_selectors:
                try:
                    btn = scope.locator(p_sel).first
                    if btn.is_visible(timeout=300):
                        btn.click(force=True)
                        break
                except Exception:
                    continue

        try:
            if not page.is_closed():
                page.mouse.click(640, 360)
        except (Error, Exception):
            pass

        time.sleep(1.5)

    # 2. Cuba akses Tetapan Gear untuk memilih 1080p atau 720p
    console.print("[cyan][*] Mencari tetapan resolusi pada menu pemain...[/cyan]")
    gear_selectors = [
        'button[aria-label*="setting" i]',
        'button[title*="setting" i]',
        '.jw-icon-settings',
        '.vjs-settings-button',
        'xpath=//*[normalize-space(text())="⚙"]'
    ]

    gear_btn = None
    target_scope = page
    for scope in [page] + page.frames:
        for g_sel in gear_selectors:
            try:
                loc = scope.locator(g_sel).first
                if loc.is_visible(timeout=300):
                    gear_btn = loc
                    target_scope = scope
                    break
            except Exception:
                continue
        if gear_btn:
            break

    if gear_btn and not page.is_closed():
        try:
            gear_btn.click(force=True)
            time.sleep(0.8)

            # Keutamaan 1: 1080p
            btn_1080 = target_scope.locator("xpath=//*[contains(normalize-space(text()), '1080')]").first
            if btn_1080.is_visible(timeout=350):
                btn_1080.click(force=True)
                console.print("[bold green][✓] Pilihan '1080p' berjaya ditekan![/bold green]")
                time.sleep(2.0)
            else:
                # Fallback Keutamaan 2: 720p
                btn_720 = target_scope.locator("xpath=//*[contains(normalize-space(text()), '720')]").first
                if btn_720.is_visible(timeout=350):
                    btn_720.click(force=True)
                    console.print("[yellow][i] Resolusi 1080p tiada; beralih dan memilih '720p'.[/yellow]")
                    time.sleep(2.0)
                else:
                    console.print("[yellow][!] Pilihan menu 1080p mahupun 720p tidak ditemui secara fizikal.[/yellow]")
        except (Error, Exception) as e:
            console.print(f"[dim yellow][!] Ralat semasa interaksi menu gear: {e}[/dim yellow]")

    # 3. Tunggu penjejakan fail strim
    console.print(f"[cyan][*] Menunggu penjejakan fail (.m3u8/.mp4) sehingga {wait_timeout} saat...[/cyan]")
    start_sniff = time.time()
    while time.time() - start_sniff < wait_timeout:
        if page.is_closed():
            break
        if intercepted["quality"] in ["1080p", "adaptive_hls"]:
            break
        if intercepted["stream_url"] and (time.time() - start_sniff > 6):
            break
        time.sleep(0.5)

    try:
        page.context.remove_listener("page", on_ad_popup)
        if not page.is_closed():
            page.remove_listener("request", on_req)
            page.remove_listener("response", on_res)
    except Exception:
        pass

    return intercepted


def main():
    console.print(Panel.fit(
        f"[bold yellow]UJIAN TUNGGAL POPUP HYDRAX + YT-DLP[/bold yellow]\n"
        f"URL Sasaran   : [cyan]{MANUAL_POPUP_URL[:85]}...[/cyan]\n"
        f"Keutamaan     : [green]1080p -> 720p Sahaja (Tolak 480p)[/green]\n"
        f"Folder Simpan : [green]{DOWNLOAD_DIR}[/green]",
        title="Ujian Manual Hydrax"
    ))

    if "xxxxxxxxx" in MANUAL_POPUP_URL:
        console.print("[bold red][✕] Sila masukkan pautan popup sebenar ke dalam pemboleh ubah 'MANUAL_POPUP_URL'.[/bold red]")
        return

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    user_agent_gecko = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"

    console.print("[cyan][*] Membuka pelayar Camoufox...[/cyan]")
    with Camoufox(headless=False, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=user_agent_gecko
        )
        page = context.new_page()

        try:
            console.print("[*] Melayari terus ke pautan popup...")
            page.goto(MANUAL_POPUP_URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.0)

            media_info = sniff_and_control_hydrax(page, wait_timeout=25)

            if media_info["status"] == "success" and media_info["stream_url"]:
                console.print(f"\n[bold green]🎯 STRIM DITEMUI: {media_info['quality']}[/bold green]")
                console.print(f"    └─ URL: [underline]{media_info['stream_url']}[/underline]\n")

                download_res = download_media_1080p(
                    media_info=media_info,
                    download_folder=DOWNLOAD_DIR,
                    custom_user_agent=user_agent_gecko
                )

                if download_res["status"] == "success":
                    console.print(f"\n[bold green]🎉 BERJAYA MUAT TURUN: {download_res['file_path']}[/bold green]")
                else:
                    console.print(f"\n[bold red][✕] Muat turun gagal: {download_res.get('error')}[/bold red]")
            else:
                console.print("\n[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold red]")
                console.print("[bold red][✕] KEPUTUSAN: Tiada pautan manifes (.m3u8/.mp4) sah untuk 1080p/720p.[/bold red]")
                console.print("[yellow]Pemain Hydrax menggunakan paket binari memori (MSE/Blob) tanpa fail manifes terbuka.[/yellow]")
                console.print("[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold red]")

            console.print("\n[*] Menutup pelayar dalam masa 5 saat...")
            time.sleep(5.0)

        finally:
            try:
                page.close()
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()