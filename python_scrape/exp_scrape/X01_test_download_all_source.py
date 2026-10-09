import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# =============================================================================
# [TETAPAN MANUAL PENGGUNA DI BAHAGIAN TERATAS]
# =============================================================================
TARGET_WEB_URL = "https://tv.lk21official.us"
SEARCH_KEYWORD = "shaolin soccer 2001"
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

# Pustaka Pelayar & Navigasi
from camoufox.sync_api import Camoufox
from playwright.sync_api import Page, Error

from X01_step01a_nav import (
    human_delay,
    smooth_scroll_down,
    find_search_input,
    smart_human_type,
    select_dropdown_match,
    ensure_movie_watch_page
)
from X01_step02_ytdlp_extract import download_media_1080p

console = Console()

# Senarai Sumber Pelayan untuk Diuji Mengikut Urutan
SOURCES_TO_TEST = ["P2P", "TURBOVIP", "CAST"]


def activate_server_source(page: Page, server_name: str) -> Optional[Page]:
    """Mencari dan mengaktifkan butang sumber pelayan di bawah pemain video."""
    console.print(f"\n[cyan][*] Mencari butang pelayan: [bold]{server_name}[/bold]...[/cyan]")
    try:
        smooth_scroll_down(page, pixels=320)
        human_delay(1.0, 1.8)
    except Exception:
        pass

    server_locators = [
        page.locator(f"text=/^\\s*{server_name}\\s*$/i"),
        page.get_by_text(server_name, exact=True),
        page.locator(f"xpath=//*[normalize-space(translate(text(), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'))='{server_name.upper()}']"),
        page.locator(f"a:has-text('{server_name}')"),
        page.locator(f"button:has-text('{server_name}')"),
        page.locator(f"span:has-text('{server_name}')"),
        page.locator(f"li:has-text('{server_name}')")
    ]

    target_btn = None
    start_search = time.time()
    while time.time() - start_search < 8:
        for loc in server_locators:
            try:
                first_item = loc.first
                if first_item.is_visible(timeout=300):
                    target_btn = first_item
                    break
            except Exception:
                continue
        if target_btn:
            break
        time.sleep(0.3)

    if not target_btn:
        console.print(f"[yellow][!] Butang pelayan '{server_name}' tidak ditemui.[/yellow]")
        return None

    try:
        console.print(f"[bold green][✓] Menekan pelayan '{server_name}'...[/bold green]")
        target_btn.scroll_into_view_if_needed()
        human_delay(0.5, 1.0)
        target_btn.click()
    except Exception as e:
        console.print(f"[yellow][!] Gagal menekan butang {server_name}: {e}[/yellow]")
        return None

    human_delay(2.0, 3.0)

    popup_locators = [
        page.locator("text=/^\\s*Popup\\s*$/i"),
        page.get_by_text("Popup", exact=False),
        page.locator("xpath=//*[normalize-space(text())='Popup']"),
        page.locator("a:has-text('Popup')"),
        page.locator("button:has-text('Popup')")
    ]

    popup_btn = None
    for p_loc in popup_locators:
        try:
            first_p = p_loc.first
            if first_p.is_visible(timeout=400):
                popup_btn = first_p
                break
        except Exception:
            continue

    if popup_btn:
        console.print(f"[green][✓] Butang 'Popup' dikesan untuk {server_name}. Membuka tab pemain...[/green]")
        target_popup = None
        try:
            with page.expect_popup(timeout=8000) as popup_info:
                popup_btn.click()
            target_popup = popup_info.value
        except Exception:
            if len(page.context.pages) > 1:
                target_popup = page.context.pages[-1]

        if target_popup and not target_popup.is_closed():
            try:
                target_popup.wait_for_load_state("domcontentloaded", timeout=12000)
                target_popup.bring_to_front()
            except Exception:
                pass
            human_delay(2.0, 3.0)
            return target_popup

    return page


def sniff_stream_and_probe_quality(player_page: Page, server_name: str, wait_timeout: int = 20) -> Dict[str, Any]:
    """Memintas pautan media tulen (.m3u8/.mp4) dengan perlindungan TargetClosedError."""
    console.print(f"[cyan][*] Memeriksa aliran video bagi sumber: [bold]{server_name}[/bold]...[/cyan]")

    if player_page.is_closed():
        return {"status": "failed", "server": server_name, "stream_url": "", "quality": "unknown"}

    try:
        user_agent_str = player_page.evaluate("() => navigator.userAgent")
    except Exception:
        user_agent_str = ""

    try:
        page_origin = player_page.url.split("?")[0].rstrip("/")
        page_url = player_page.url
    except Exception:
        page_origin = ""
        page_url = ""

    intercepted = {
        "status": "failed",
        "server": server_name,
        "stream_url": "",
        "referer": page_url,
        "user_agent": user_agent_str,
        "quality": "unknown"
    }

    def close_ad_popups(new_tab: Page):
        try:
            time.sleep(0.3)
            if not player_page.is_closed() and new_tab != player_page:
                new_tab.close()
                player_page.bring_to_front()
        except Exception:
            pass

    try:
        player_page.context.on("page", close_ad_popups)
    except Exception:
        pass

    def is_valid_media_target(url: str, content_type: str = "") -> bool:
        u_lower = url.lower()
        base_clean = u_lower.split("?")[0]
        ct = content_type.lower()

        if page_origin and base_clean == page_origin.lower():
            return False
        if any(bad in u_lower for bad in [
            "youtube.com", "googlevideo.com", "doubleclick", "analytics",
            "adsterra", "monetag", "histats", "popads", "clarity.ms"
        ]):
            return False

        if any(base_clean.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".json", ".html"]):
            return False
        if any(bad_ct in ct for bad_ct in ["text/html", "text/css", "application/javascript"]):
            return False

        if any(base_clean.endswith(ext) for ext in [".m3u8", ".mpd", ".mp4", ".mkv", ".webm"]):
            return True
        if any(kw in u_lower for kw in [".m3u8", "master.m3u8", "playlist.m3u8", "manifest.mpd"]):
            return True

        if any(vct in ct for vct in ["application/vnd.apple.mpegurl", "application/x-mpegurl", "video/mp4"]):
            return True

        return False

    def on_network_traffic(url: str, headers: dict, ct: str = ""):
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
        elif "480" in url.lower():
            quality = "480p"
        elif "master.m3u8" in url.lower() or "playlist.m3u8" in url.lower():
            quality = "adaptive_hls"

        # Jangan timpa kualiti lebih tinggi dengan kualiti lebih rendah
        current_q = intercepted["quality"]
        priority_rank = {"1080p": 5, "adaptive_hls": 4, "720p": 3, "480p": 2, "unknown": 1}

        if priority_rank.get(current_q, 0) > priority_rank.get(quality, 0):
            return

        intercepted["status"] = "success"
        intercepted["stream_url"] = url
        intercepted["quality"] = quality
        intercepted["referer"] = headers.get("referer", page_url)

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
        player_page.on("request", on_req)
        player_page.on("response", on_res)
    except Exception:
        pass

    # Cetus Play untuk memulakan penstriman
    play_selectors = [
        'button[aria-label*="play" i]',
        '.vjs-big-play-button',
        '.jw-display-icon-display',
        'button.play-button',
        '.plyr__control--overlaid',
        'button.play',
        'video'
    ]

    for attempt in range(1, 4):
        if intercepted["stream_url"] or player_page.is_closed():
            break

        try:
            for scope in [player_page] + player_page.frames:
                if player_page.is_closed():
                    break
                for p_sel in play_selectors:
                    try:
                        btn = scope.locator(p_sel).first
                        if btn.is_visible(timeout=250):
                            btn.click(force=True)
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        try:
            if not player_page.is_closed():
                player_page.mouse.click(640, 360)
        except Exception:
            pass

        time.sleep(1.2)

    # Periksa Gear untuk memilih 1080p jika masih aktif
    if not player_page.is_closed() and intercepted["quality"] not in ["1080p", "adaptive_hls"]:
        gear_selectors = [
            'button[aria-label*="setting" i]',
            'button[title*="setting" i]',
            '.jw-icon-settings',
            '.vjs-settings-button',
            'xpath=//*[normalize-space(text())="⚙"]'
        ]
        gear_btn = None
        try:
            for scope in [player_page] + player_page.frames:
                if player_page.is_closed():
                    break
                for g_sel in gear_selectors:
                    try:
                        loc = scope.locator(g_sel).first
                        if loc.is_visible(timeout=250):
                            gear_btn = loc
                            break
                    except Exception:
                        continue
                if gear_btn:
                    break

            if gear_btn and not player_page.is_closed():
                gear_btn.click(force=True)
                time.sleep(0.8)
                btn_1080 = player_page.locator("xpath=//*[contains(normalize-space(text()), '1080')]").first
                if btn_1080.is_visible(timeout=350):
                    btn_1080.click(force=True)
                    console.print(f"[bold green][✓] Memilih tetapan 1080p pada {server_name}[/bold green]")
                    time.sleep(1.5)
                else:
                    btn_720 = player_page.locator("xpath=//*[contains(normalize-space(text()), '720')]").first
                    if btn_720.is_visible(timeout=350):
                        btn_720.click(force=True)
                        time.sleep(1.5)
        except Exception:
            pass

    # Menunggu strim dengan perlindungan TargetClosedError
    start_sniff = time.time()
    while time.time() - start_sniff < wait_timeout:
        if player_page.is_closed():
            console.print(f"[yellow][!] Tab bagi sumber '{server_name}' ditutup oleh laman web.[/yellow]")
            break
        if intercepted["quality"] in ["1080p", "adaptive_hls"]:
            break
        if intercepted["stream_url"] and (time.time() - start_sniff > 6):
            break
        time.sleep(0.4)

    try:
        player_page.context.remove_listener("page", close_ad_popups)
    except Exception:
        pass
    try:
        if not player_page.is_closed():
            player_page.remove_listener("request", on_req)
            player_page.remove_listener("response", on_res)
    except Exception:
        pass

    return intercepted


def execute_smart_fallback_pipeline(active_page: Page) -> Optional[Dict[str, Any]]:
    """Logik Fallback Pintar: Utamakan 1080p/Adaptive; Sandaran 720p; Sandaran Terakhir 480p."""
    console.print("\n[bold yellow]=== [MEMULAKAN PENILAIAN SUMBER & FALLBACK PINTAR] ===[/bold yellow]")

    candidate_720p = None
    candidate_other = None
    results_summary = []

    for server in SOURCES_TO_TEST:
        console.print(f"\n[bold blue]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold blue]")
        console.print(f"[bold white on blue] MENGUJI SUMBER: {server} [/bold white on blue]")

        try:
            player_page = activate_server_source(active_page, server)
        except Exception as e:
            console.print(f"[yellow][!] Gagal membuka pelayan '{server}': {e}[/yellow]")
            results_summary.append({"server": server, "status": "Ralat Buka", "quality": "-"})
            continue

        if not player_page:
            results_summary.append({"server": server, "status": "Tidak Ditemui", "quality": "-"})
            continue

        try:
            media_info = sniff_stream_and_probe_quality(player_page, server)
        except Exception as e:
            console.print(f"[yellow][!] Ralat semasa menghidu strim {server}: {e}[/yellow]")
            media_info = {"status": "failed", "server": server, "stream_url": "", "quality": "unknown"}

        # Tutup tab popup pemain dan fokus kembali ke halaman induk
        if player_page != active_page:
            try:
                if not player_page.is_closed():
                    player_page.close()
                active_page.bring_to_front()
            except Exception:
                pass

        # Jeda bertenang sebelum beralih ke sumber berikutnya
        time.sleep(2.0)

        if media_info["status"] == "success" and media_info["stream_url"]:
            q = media_info["quality"]
            results_summary.append({"server": server, "status": "Berjaya", "quality": q})
            console.print(f"[green][✓] Sumber '{server}' dikesan dengan kualiti: [bold]{q}[/bold][/green]")
            console.print(f"    └─ URL Strim: [underline]{media_info['stream_url'][:85]}...[/underline]")

            # Jika 1080p atau Adaptive HLS ditemui, tamatkan carian serta-merta
            if q in ["1080p", "adaptive_hls"]:
                console.print(f"\n[bold green]🎯 KEJAYAAN: Resolusi Optimum ({q}) Ditemui pada '{server}'! Menghentikan carian sumber lain.[/bold green]")
                return media_info

            # Simpan 720p sebagai sandaran terbaik
            if q == "720p" and candidate_720p is None:
                candidate_720p = media_info
                console.print(f"[yellow][i] Sumber '{server}' (720p) disimpan sebagai calon sandaran utama.[/yellow]")

            # Simpan kualiti lain (cth: 480p daripada P2P) sebagai sandaran terakhir
            if candidate_other is None:
                candidate_other = media_info
        else:
            results_summary.append({"server": server, "status": "Gagal Menjejak", "quality": "-"})
            console.print(f"[red][✕] Tiada pautan strim yang sah dikesan pada '{server}'. Meneruskan semakan...[/red]")

    # Paparkan Jadual Keputusan Semakan Semua Sumber
    table = Table(title="Keputusan Imbasan Sumber Penstriman")
    table.add_column("Sumber", style="cyan")
    table.add_column("Status", style="magenta")
    table.add_column("Kualiti Dikesan", style="green")

    for r in results_summary:
        table.add_row(r["server"], r["status"], r["quality"])
    console.print("\n", table)

    # 1. Pilih 720p jika wujud
    if candidate_720p:
        console.print(f"\n[bold yellow]⚡ [FALLBACK KEUTAMAAN 2]: Tiada sumber 1080p ditemui.[/bold yellow]")
        console.print(f"[bold green]✓ Menggunakan sandaran terbaik: '{candidate_720p['server']}' (720p)[/bold green]")
        return candidate_720p

    # 2. Pilih 480p / kualiti sah lain jika 720p pun tiada
    if candidate_other:
        console.print(f"\n[bold yellow]⚡ [FALLBACK KEUTAMAAN 3]: Tiada sumber 1080p mahupun 720p ditemui.[/bold yellow]")
        console.print(f"[bold green]✓ Menggunakan strim yang sah sedia ada: '{candidate_other['server']}' ({candidate_other['quality']})[/bold green]")
        return candidate_other

    console.print("\n[bold red][✕] Semua sumber gagal menghasilkan sebarang strim yang boleh dimuat turun.[/bold red]")
    return None


def main():
    console.print(Panel.fit(
        f"[bold yellow]UJIAN PIPELINE MUAT TURUN FALLBACK PINTAR (P2P, TURBOVIP, CAST)[/bold yellow]\n"
        f"Carian        : [cyan]{SEARCH_KEYWORD}[/cyan]\n"
        f"Folder Simpan : [green]{DOWNLOAD_DIR}[/green]",
        title="Ujian Penuh Strim Media"
    ))

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    user_agent_gecko = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"

    console.print("[cyan][*] Melancarkan pelayar Camoufox...[/cyan]")
    with Camoufox(headless=False, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=user_agent_gecko
        )
        page = context.new_page()

        try:
            # 1. Navigasi & Masuk ke Halaman Tontonan Video
            console.print(f"[*] Melayari halaman carian...")
            page.goto(TARGET_WEB_URL, wait_until="domcontentloaded", timeout=45000)
            human_delay(2.0, 3.5)

            input_box = find_search_input(page)
            if not input_box:
                raise RuntimeError("Kotak carian tidak ditemui.")

            smart_human_type(input_box, page, SEARCH_KEYWORD)
            select_dropdown_match(page, SEARCH_KEYWORD)
            active_page = ensure_movie_watch_page(page, SEARCH_KEYWORD)

            # 2. Imbas Sumber & Fallback Pintar (P2P, TURBOVIP, CAST)
            chosen_stream = execute_smart_fallback_pipeline(active_page)

            # 3. Muat Turun Menggunakan yt-dlp
            if chosen_stream:
                console.print(f"\n[bold magenta]=== [MEMULAKAN MUAT TURUN YT-DLP] ===[/bold magenta]")
                console.print(f"    ├─ Sumber Dipilih : [bold cyan]{chosen_stream['server']}[/bold cyan]")
                console.print(f"    ├─ Resolusi       : [bold green]{chosen_stream['quality']}[/bold green]")
                console.print(f"    └─ Pautan Strim   : [underline]{chosen_stream['stream_url']}[/underline]\n")

                download_res = download_media_1080p(
                    media_info=chosen_stream,
                    download_folder=DOWNLOAD_DIR,
                    custom_user_agent=user_agent_gecko
                )

                if download_res["status"] == "success":
                    console.print(f"\n[bold green]🎉 PROSES SELESAI DENGAN JAYANYA![/bold green]")
                    console.print(f"📁 Lokasi Fail: [bold white]{download_res['file_path']}[/bold white]")
                else:
                    console.print(f"\n[bold red][✕] Muat turun gagal: {download_res.get('error')}[/bold red]")
            else:
                console.print("\n[bold red]Operasi dihentikan kerana tiada strim yang memenuhi kriteria.[/bold red]")

            console.print("\n[*] Menutup pelayar dalam masa 5 saat...")
            human_delay(4.0, 5.0)

        finally:
            try:
                page.close()
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()