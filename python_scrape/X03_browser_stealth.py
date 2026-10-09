#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - AUTOMASI PELAYAR CAMOUFOX & ANTI-IKLAN STEALTH
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/03_browser_stealth.py
# CIRI:
# 1. Konteks Pelayar Camoufox Berpusat (Suis GUI Headless On/Off).
# 2. Penutup Tab Iklan Agresif (Popunder/Ad-Shield Auto-Closer).
# 3. Ritme Manusia Semula Jadi (Penaipan, Penatalan, Gauss Delays).
# 4. Enjin Carian Web Adaptif berdasarkan Tajuk Stremio & Hasil Carian.
# ==============================================================================

import time
import random
from typing import Optional, List, Tuple
from pathlib import Path
from contextlib import contextmanager

from playwright.sync_api import Page, BrowserContext, Locator
from camoufox.sync_api import Camoufox
from rich.console import Console

# Penyelarasan modul konfigurasi
SCRAPE_DIR = Path(__file__).resolve().parent
import X00_scrape_config as cfg

console = Console()

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


def human_delay(min_s: float = 1.5, max_s: float = 3.5, mode: str = "gaussian"):
    """Jeda masa berasaskan taburan Gauss untuk menyerupai tingkah laku manusia."""
    if mode == "gaussian":
        mean = (min_s + max_s) / 2.0
        sigma = (max_s - min_s) / 4.0
        delay = random.gauss(mean, sigma)
        delay = max(min_s, min(delay, max_s))
    else:
        delay = random.uniform(min_s, max_s)
    time.sleep(delay)


def smooth_scroll(page: Page, pixels: int = 400):
    """Menatal halaman ke bawah secara berperingkat agar elemen penting dimuatkan."""
    steps = 6
    step_px = pixels // steps
    for _ in range(steps):
        page.mouse.wheel(0, step_px)
        time.sleep(random.uniform(0.06, 0.14))
    human_delay(0.8, 1.4)


def human_type(locator: Locator, text: str):
    """Menaip kata carian mengikut kelajuan fizikal papan kekunci."""
    locator.scroll_into_view_if_needed()
    locator.click()
    human_delay(0.4, 0.8)
    locator.fill("")
    time.sleep(0.2)

    for idx, char in enumerate(text):
        delay_ms = int(random.uniform(120, 220) if char in " -_.,/'" else random.uniform(70, 150))
        locator.press_sequentially(char, delay=delay_ms)
        if idx > 0 and idx % random.randint(4, 7) == 0 and random.random() < 0.2:
            time.sleep(random.uniform(0.25, 0.5))

    human_delay(0.8, 1.5)


class BrowserStealthManager:
    @staticmethod
    def attach_ad_blocker(context: BrowserContext, target_page: Page):
        """Memantau dan menutup serta-merta sebarang tab iklan yang muncul secara mengejut."""
        def on_page_opened(new_tab: Page):
            try:
                time.sleep(0.3)
                if new_tab != target_page and not target_page.is_closed():
                    tab_url = new_tab.url.lower()
                    ad_domains = [
                        "about:blank", "adsterra", "monetag", "histats", "popads",
                        "clarity.ms", "doubleclick", "syndication", "directrev",
                        "onclick", "traffic", "bet", "casino"
                    ]
                    # Tutup tab jika URL kosong atau sepadan dengan corak rangkaian iklan
                    if any(ad in tab_url for ad in ad_domains) or len(tab_url) <= 12:
                        console.print(f"[dim yellow][!] Menutup tab iklan mencelah: {tab_url[:60]}...[/dim yellow]")
                        new_tab.close()
                        target_page.bring_to_front()
            except Exception:
                pass

        context.on("page", on_page_opened)

    @classmethod
    @contextmanager
    def launch_session(cls):
        """
        Pengurus konteks pelayar Camoufox.
        Mematuhi suis konfigurasi BROWSER_HEADLESS (GUI hidup atau mod senyap).
        """
        is_headless = cfg.BROWSER_HEADLESS
        status_txt = "Latar Belakang (Headless)" if is_headless else "Paparan Visual (GUI Terbuka)"
        console.print(f"[cyan]🚀 Melancarkan Pelayar Camoufox: [bold green]{status_txt}[/bold green]...[/cyan]")

        with Camoufox(headless=is_headless, geoip=True) as browser:
            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=cfg.USER_AGENT_DESKTOP
            )
            page = context.new_page()
            cls.attach_ad_blocker(context, page)

            try:
                yield browser, context, page
            finally:
                try:
                    page.close()
                    context.close()
                except Exception:
                    pass

    @staticmethod
    def find_search_box(page: Page, timeout_sec: int = 15) -> Optional[Locator]:
        """Mengesan kotak input carian merentasi halaman utama dan bingkai iframe."""
        selectors = [
            'input[type="search"]',
            'input[name*="search" i]',
            'input[name*="q" i]',
            'input[placeholder*="search" i]',
            'input[placeholder*="cari" i]',
            'input[id*="search" i]',
            'input.search-input',
            'input#search'
        ]

        start_t = time.time()
        while time.time() - start_t < timeout_sec:
            for frame in [page] + page.frames:
                for sel in selectors:
                    try:
                        loc = frame.locator(sel).first
                        if loc.is_visible(timeout=300):
                            return loc
                    except Exception:
                        continue

            # Cuba buka bar carian jika ia berbentuk butang ikon
            triggers = ['button[aria-label*="search" i]', '.search-toggle', '#search-button']
            for trig in triggers:
                try:
                    btn = page.locator(trig).first
                    if btn.is_visible(timeout=250):
                        btn.click()
                        human_delay(0.5, 1.0)
                        break
                except Exception:
                    continue

            page.wait_for_timeout(400)

        return None

    @staticmethod
    def navigate_and_search(page: Page, site_url: str, query_list: List[str]) -> bool:
        """
        Melayari laman sasaran dan melaksanakan carian berperingkat
        menggunakan susunan kata carian tajuk terbaik dari Stremio/TMDB.
        """
        console.print(f"[*] Melayari portal sasaran: [underline]{site_url}[/underline]")
        try:
            page.goto(site_url, wait_until="domcontentloaded", timeout=45000)
            human_delay(2.0, 3.5)
        except Exception as e:
            console.print(f"[bold red]❌ Gagal memuatkan laman sasaran: {e}[/bold red]")
            return False

        search_input = BrowserStealthManager.find_search_box(page)
        if not search_input:
            console.print("[bold red]❌ Medan kotak carian tidak ditemui pada portal sasaran.[/bold red]")
            return False

        # Utamakan carian tajuk pertama yang dibekalkan
        target_query = query_list[0] if query_list else ""
        console.print(f"[cyan]🔍 Mengisi kata carian tajuk: [bold]{target_query}[/bold][/cyan]")
        human_type(search_input, target_query)

        # Hantar carian menggunakan kekunci Enter
        page.keyboard.press("Enter")
        human_delay(3.0, 4.5)
        return True


if __name__ == "__main__":
    console.print("[bold yellow]🧪 UJIAN PENGESAHAN MODUL 03_BROWSER_STEALTH.PY[/bold yellow]")
    with BrowserStealthManager.launch_session() as (browser, context, page):
        console.print(f"✅ Sesi Camoufox berjaya diwujudkan! URL Semasa: {page.url}")
        human_delay(1.0, 2.0)