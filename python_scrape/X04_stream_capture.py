#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - RESOLUSI PEMAIN & PINTASAN STRIM
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/X04_stream_capture.py
# ==============================================================================

import os
import sys
import time
from typing import Dict
from playwright.sync_api import Page
from rich.console import Console

SCRAPE_DIR = Path(__file__).resolve().parent if "__file__" in locals() else None
from pathlib import Path
if SCRAPE_DIR and str(SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPE_DIR))

from X03_browser_stealth import human_delay

console = Console()


def configure_resolution_and_sniff(player_page: Page, wait_timeout: int = 40) -> Dict[str, str]:
    console.print("\n[bold yellow][*] Mengkonfigurasi resolusi pada pemain video popup...[/bold yellow]")

    try:
        user_agent_str = player_page.evaluate("() => navigator.userAgent")
    except Exception:
        user_agent_str = None

    intercepted = {
        "stream_url": "",
        "referer": player_page.url,
        "user_agent": user_agent_str or "",
        "quality": "unknown"
    }

    def on_ad_tab_opened(new_tab: Page):
        try:
            time.sleep(0.4)
            if new_tab != player_page:
                console.print(f"[yellow][!] Mengesan dan menutup tab iklan bertindih: {new_tab.url[:55]}...[/yellow]")
                new_tab.close()
                player_page.bring_to_front()
        except Exception:
            pass

    player_page.context.on("page", on_ad_tab_opened)

    def is_media_target(url: str, content_type: str = "") -> bool:
        u = url.lower().split("?")[0]
        if any(bad in url.lower() for bad in [
            "youtube.com", "googlevideo.com", "ytimg.com", "doubleclick",
            "analytics", "google-analytics", "facebook.com", "favicon",
            "adsterra", "monetag", "histats", "popads", "clarity.ms"
        ]):
            return False

        if any(u.endswith(ext) for ext in [".m3u8", ".mp4", ".mpd", ".mkv", ".webm", ".m4s", ".ts"]):
            return True

        if any(kw in url.lower() for kw in [".m3u8", "/hls/", "master.m3u8", "playlist.m3u8", "manifest.mpd", "slug=", "abysscdn"]):
            if not any(u.endswith(no_ext) for no_ext in [".js", ".css", ".png", ".jpg", ".svg", ".json", ".html"]):
                return True

        ct = content_type.lower()
        if any(t in ct for t in ["application/vnd.apple.mpegurl", "application/x-mpegurl", "video/mp4", "video/webm", "application/dash+xml"]):
            return True

        return False

    def process_media_candidate(url: str, headers: dict, ct: str = ""):
        if not is_media_target(url, ct):
            return

        quality = "unknown"
        if "1080" in url:
            quality = "1080p"
        elif "720" in url:
            quality = "720p"
        elif "480" in url:
            quality = "480p"

        if intercepted["quality"] == "1080p" and quality != "1080p":
            return

        intercepted["stream_url"] = url
        intercepted["quality"] = quality
        intercepted["referer"] = headers.get("referer", player_page.url)

        q_label = f" {quality}" if quality != "unknown" else ""
        console.print(f"[bold green][🎯 STRIM{q_label} DIPINTAS!] {url[:85]}...[/bold green]")

    def on_req(request):
        try:
            if request.resource_type == "media":
                process_media_candidate(request.url, request.headers)
            elif is_media_target(request.url):
                process_media_candidate(request.url, request.headers)
        except Exception:
            pass

    def on_res(response):
        try:
            ct = response.headers.get("content-type", "")
            if is_media_target(response.url, ct):
                process_media_candidate(response.url, response.request.headers, ct)
        except Exception:
            pass

    player_page.on("request", on_req)
    player_page.on("response", on_res)

    console.print("[cyan][*] Memastikan video dimainkan (membersihkan lapisan iklan)...[/cyan]")

    def check_video_playing(target_page: Page) -> bool:
        for fr in [target_page] + target_page.frames:
            try:
                is_active = fr.evaluate("() => { const v = document.querySelector('video'); return v && (!v.paused || v.currentTime > 0); }")
                if is_active:
                    return True
            except Exception:
                continue
        return False

    play_selectors = [
        'button[aria-label*="play" i]',
        '.vjs-big-play-button',
        '.jw-display-icon-display',
        'button.play-button',
        '.plyr__control--overlaid',
        'div[class*="play" i][role="button"]',
        'button.play'
    ]

    for attempt in range(1, 5):
        if check_video_playing(player_page) or intercepted["stream_url"]:
            console.print("[bold green][✓] Pemain video disahkan aktif dan berjalan.[/bold green]")
            break

        console.print(f"[cyan]    └─ Percubaan cetusan Play #{attempt}...[/cyan]")
        clicked = False

        for scope in [player_page] + player_page.frames:
            for p_sel in play_selectors:
                try:
                    btn = scope.locator(p_sel).first
                    if btn.is_visible(timeout=400):
                        btn.click(force=True)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break

        if not clicked:
            try:
                player_page.mouse.click(640, 360)
            except Exception:
                pass

        human_delay(1.5, 2.5)
        player_page.bring_to_front()

    for fr in [player_page] + player_page.frames:
        try:
            direct_src = fr.evaluate("() => { const v = document.querySelector('video'); if (v && v.src && !v.src.startsWith('blob:')) return v.src; if (v && v.currentSrc && !v.currentSrc.startsWith('blob:')) return v.currentSrc; const s = document.querySelector('video source'); if (s && s.src && !s.src.startsWith('blob:')) return s.src; return null; }")
            if direct_src and is_media_target(direct_src):
                process_media_candidate(direct_src, {"referer": player_page.url})
                break
        except Exception:
            pass

    time.sleep(2.5)
    if intercepted["quality"] == "1080p":
        console.print("[bold green][✓] Resolusi 1080p dikesan automatik! Melangkau butang gear.[/bold green]")
        try:
            player_page.context.remove_listener("page", on_ad_tab_opened)
            player_page.remove_listener("request", on_req)
            player_page.remove_listener("response", on_res)
        except Exception:
            pass
        return intercepted

    console.print("[yellow][!] Resolusi 1080p belum aktif. Memulakan pemilihan menu Gear...[/yellow]")
    try:
        player_page.mouse.move(600, 350)
        human_delay(0.5, 1.0)
    except Exception:
        pass

    gear_selectors = [
        'button[aria-label*="setting" i]',
        'button[title*="setting" i]',
        'button[aria-label*="kualiti" i]',
        'button[aria-label*="quality" i]',
        'button[title*="quality" i]',
        '.jw-icon-settings',
        '.vjs-icon-cog',
        '.vjs-settings-button',
        'xpath=//button[contains(@class, "setting") or contains(@class, "gear")]',
        'xpath=//*[normalize-space(text())="⚙"]'
    ]

    gear_found = False
    active_scope = player_page

    for scope in [player_page] + player_page.frames:
        for g_sel in gear_selectors:
            try:
                g_loc = scope.locator(g_sel).first
                if g_loc.is_visible(timeout=500):
                    console.print("[green][✓] Butang Gear (Tetapan) ditemui! Mengklik...[/green]")
                    g_loc.scroll_into_view_if_needed()
                    human_delay(0.4, 0.8)
                    g_loc.click(force=True)
                    gear_found = True
                    active_scope = scope
                    human_delay(1.0, 1.8)
                    break
            except Exception:
                continue
        if gear_found:
            break

    if gear_found:
        for sub in ['xpath=//*[contains(translate(text(), "QUALITY", "quality"), "quality")]', 
                    'xpath=//*[contains(translate(text(), "KUALITI", "kualiti"), "kualiti")]']:
            try:
                sub_btn = active_scope.locator(sub).first
                if sub_btn.is_visible(timeout=400):
                    sub_btn.click(force=True)
                    human_delay(0.4, 0.8)
                    break
            except Exception:
                continue

        quality_selected = False
        target_resolutions = ["1080p", "1080", "720p", "720"]

        for res in target_resolutions:
            label = "1080p" if "1080" in res else "720p"
            is_priority = "1080" in res

            res_selectors = [
                f"xpath=//*[self::button or self::li or self::span or self::div][@role='menuitem' or @role='menuitemradio' or contains(@class, 'item') or contains(@class, 'option')][contains(normalize-space(text()), '{res}')]",
                f"xpath=//li[contains(normalize-space(text()), '{res}')]",
                f"xpath=//button[contains(normalize-space(text()), '{res}')]",
                f"xpath=//*[normalize-space(text())='{res}']"
            ]

            for scope in [active_scope, player_page] + player_page.frames:
                for r_sel in res_selectors:
                    try:
                        btn_res = scope.locator(r_sel).first
                        if btn_res.is_visible(timeout=400):
                            status_label = "Keutamaan" if is_priority else "Pilihan Sandaran"
                            console.print(f"[bold green][✓] Memilih resolusi: '{label}' ({status_label})[/bold green]")
                            btn_res.scroll_into_view_if_needed()
                            human_delay(0.4, 0.8)
                            btn_res.click(force=True)
                            quality_selected = True
                            human_delay(1.5, 2.5)
                            break
                    except Exception:
                        continue
                if quality_selected:
                    break
            if quality_selected:
                break

        if not quality_selected:
            console.print("[yellow][!] Pilihan 1080p / 720p tidak ditemui pada menu tetapan.[/yellow]")
    else:
        console.print("[yellow][!] Butang Gear tidak ditemui; meneruskan pemintasan pautan sedia ada.[/yellow]")

    console.print("[cyan][*] Menunggu penjejakan pautan strim media...[/cyan]")
    start_sniff = time.time()
    while time.time() - start_sniff < wait_timeout:
        if intercepted["stream_url"]:
            break
        player_page.wait_for_timeout(500)

    try:
        player_page.context.remove_listener("page", on_ad_tab_opened)
        player_page.remove_listener("request", on_req)
        player_page.remove_listener("response", on_res)
    except Exception:
        pass

    return intercepted