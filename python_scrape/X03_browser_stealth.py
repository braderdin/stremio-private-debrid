#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - AUTOMASI PELAYAR CAMOUFOX & ANTI-IKLAN STEALTH
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/X03_browser_stealth.py
# CIRI:
# 1. Mengimport 100% Gaya Pergerakan Manusia Asal dari X01_step01a_nav.py.
# 2. Ritme Penaipan Natural (smart_human_type) & Penatalan Berperingkat.
# 3. Pengendali Cloudflare Turnstile & Pengesan Tab Iklan Agresif.
# 4. Pemadanan Dropdown & Kad Filem Sasaran Berasaskan Rapidfuzz.
# ==============================================================================

import time
import random
from typing import Optional, List, Tuple
from pathlib import Path
from contextlib import contextmanager

from playwright.sync_api import Page, BrowserContext, Locator
from camoufox.sync_api import Camoufox
from rich.console import Console

SCRAPE_DIR = Path(__file__).resolve().parent
import X00_scrape_config as cfg

console = Console()

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


def human_delay(min_s: float = 2.0, max_s: float = 4.0, mode: str = "gaussian"):
    """Jeda masa rawak berasaskan taburan Gauss untuk menyerupai manusia."""
    if mode == "gaussian":
        mean = (min_s + max_s) / 2.0
        sigma = (max_s - min_s) / 4.0
        delay = random.gauss(mean, sigma)
        delay = max(min_s, min(delay, max_s))
    else:
        delay = random.uniform(min_s, max_s)
    time.sleep(delay)


def smooth_scroll_down(page: Page, pixels: int = 400):
    """Skrol halaman ke bawah secara berperingkat agar elemen penting kelihatan."""
    steps = 8
    step_px = pixels // steps
    for _ in range(steps):
        page.mouse.wheel(0, step_px)
        time.sleep(random.uniform(0.05, 0.12))
    human_delay(1.0, 1.8)


def smart_human_type(locator: Locator, page: Page, text: str):
    """Menaip perkataan mengikut ritme semula jadi papan kekunci (dari X01_step01a_nav)."""
    locator.scroll_into_view_if_needed()
    locator.click()
    human_delay(0.6, 1.2)

    locator.fill("")
    time.sleep(random.uniform(0.3, 0.5))

    for idx, char in enumerate(text):
        char_delay = random.uniform(0.14, 0.25) if char in " -_.,/'" else random.uniform(0.08, 0.18)
        locator.press_sequentially(char, delay=int(char_delay * 1000))

        if idx > 0 and idx % random.randint(5, 7) == 0 and random.random() < 0.20:
            time.sleep(random.uniform(0.35, 0.65))

    if locator.input_value() != text:
        locator.fill(text)

    try:
        locator.dispatch_event("input")
        locator.dispatch_event("change")
    except Exception:
        pass

    human_delay(1.2, 2.2)


class BrowserStealthManager:
    @staticmethod
    def handle_cloudflare_challenge(page: Page, max_wait_sec: int = 20) -> bool:
        """Mengesan dan menekan Turnstile Cloudflare jika disekat di GitHub Actions."""
        start_t = time.time()
        while time.time() - start_t < max_wait_sec:
            page_title = (page.title() or "").lower()
            page_content = (page.content() or "").lower()

            if "just a moment" not in page_title and "attention required" not in page_title and "challenges.cloudflare" not in page_content:
                return True

            console.print("[dim yellow]🛡️ Mengesan cabaran Cloudflare/Turnstile... Menyelesaikan...[/dim yellow]")
            for frame in page.frames:
                try:
                    if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                        chk = frame.locator("input[type=checkbox], .ctp-checkbox-label, #challenge-stage").first
                        if chk.is_visible(timeout=300):
                            chk.click(force=True)
                            human_delay(1.5, 2.5)
                            break
                except Exception:
                    continue

            time.sleep(1.0)
        return False

    @staticmethod
    def attach_ad_blocker(context: BrowserContext, target_page: Page):
        """Menutup tab iklan bertindih secara automatik."""
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
                    if any(ad in tab_url for ad in ad_domains) or len(tab_url) <= 12:
                        console.print(f"[dim yellow][!] Menutup tab iklan mencelah: {tab_url[:55]}...[/dim yellow]")
                        new_tab.close()
                        target_page.bring_to_front()
            except Exception:
                pass

        context.on("page", on_page_opened)

    @classmethod
    @contextmanager
    def launch_session(cls):
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
    def find_search_input(page: Page, timeout_sec: int = 20) -> Optional[Locator]:
        """Mengesan kotak input carian merentasi halaman, iframe, dan generic fallback."""
        console.print("[cyan][*] Mengesan kotak input carian...[/cyan]")
        selectors = [
            'input[type="search"]',
            'input[name*="search" i]',
            'input[name*="query" i]',
            'input[name*="q" i]',
            'input[id*="search" i]',
            'input[placeholder*="search" i]',
            'input[placeholder*="cari" i]',
            'input[placeholder*="find" i]',
            'input[class*="search" i]',
            'input#search'
        ]

        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            # 1. Semak Turnstile jika terperangkap
            BrowserStealthManager.handle_cloudflare_challenge(page, max_wait_sec=2)

            # 2. Imbas semua selektor merentasi semua rangka (frames)
            for frame in [page] + page.frames:
                for sel in selectors:
                    try:
                        loc = frame.locator(sel).first
                        if loc.is_visible(timeout=250):
                            console.print(f"[green][✓] Kotak carian ditemui: '{sel}'[/green]")
                            return loc
                    except Exception:
                        continue

            # 3. Cuba tekan butang ikon carian jika tersorok
            trigger_buttons = ['button[aria-label*="search" i]', 'button[title*="search" i]', '.search-toggle', '#search-button']
            for btn_sel in trigger_buttons:
                try:
                    btn = page.locator(btn_sel).first
                    if btn.is_visible(timeout=250):
                        btn.click()
                        human_delay(1.0, 1.8)
                        break
                except Exception:
                    continue

            page.wait_for_timeout(400)

        # 4. Fallback Terakhir: Ambil input teks biasa yang sedang kelihatan di laman
        try:
            for frame in [page] + page.frames:
                generic_inputs = frame.locator('input[type="text"]').all()
                for inp in generic_inputs:
                    if inp.is_visible():
                        console.print("[green][✓] Kotak carian ditemui melalui generic input[type=text]![/green]")
                        return inp
        except Exception:
            pass

        return None

    @staticmethod
    def select_dropdown_match(page: Page, query_text: str, timeout_sec: int = 8) -> bool:
        """Memeriksa dropdown cadangan (100% daripada kod asal X01_step01a_nav.py)."""
        console.print(f"[cyan][*] Memeriksa menu dropdown cadangan bagi: '{query_text}'...[/cyan]")
        dropdown_containers = [
            '[role="listbox"]', '[role="menu"]',
            'ul[class*="suggest" i]', 'ul[class*="search" i]',
            'div[class*="autocomplete" i]', 'div[class*="search" i]',
            '.search-results'
        ]
        item_selectors = ['[role="option"]', '[role="menuitem"]', 'li', 'div[class*="item" i]', 'a']

        start_wait = time.time()
        best_element = None
        highest_score = 0.0

        while time.time() - start_wait < timeout_sec:
            scope = page
            for c_sel in dropdown_containers:
                try:
                    c_loc = page.locator(c_sel).first
                    if c_loc.is_visible(timeout=250):
                        scope = c_loc
                        break
                except Exception:
                    continue

            candidate_locators = []
            for i_sel in item_selectors:
                try:
                    items = scope.locator(i_sel).all()
                    if items:
                        candidate_locators.extend(items)
                except Exception:
                    continue

            query_clean = query_text.strip().lower()
            for item in candidate_locators:
                try:
                    if not item.is_visible():
                        continue
                    item_text = (item.inner_text() or "").strip().lower()
                    if not item_text:
                        continue

                    if query_clean in item_text:
                        best_element = item
                        highest_score = 100.0
                        break

                    if HAS_RAPIDFUZZ:
                        score = fuzz.partial_ratio(query_clean, item_text)
                        if score > highest_score and score >= 65.0:
                            highest_score = score
                            best_element = item
                except Exception:
                    continue

            if best_element:
                break
            page.wait_for_timeout(350)

        if best_element:
            try:
                console.print(f"[green][✓] Cadangan dropdown dipilih (Skor: {highest_score}). Mengklik...[/green]")
                best_element.scroll_into_view_if_needed()
                human_delay(0.6, 1.2)
                best_element.click()
                human_delay(3.0, 4.5)
                return True
            except Exception:
                pass

        console.print("[yellow][!] Menekan 'Enter' sebagai alternatif dropdown...[/yellow]")
        page.keyboard.press("Enter")
        human_delay(3.0, 4.5)
        return False

    @staticmethod
    def ensure_movie_watch_page(page: Page, query_text: str) -> Page:
        """Memastikan berada di laman tontonan video sebenar (100% dari X01_step01a_nav.py)."""
        human_delay(2.0, 3.5)

        has_ganti_player = page.locator("text=/GANTI PLAYER/i").count() > 0
        has_hydrax_text = page.locator("text=/HYDRAX/i").count() > 0
        has_popup_text = page.locator("text=/Popup/i").count() > 0

        if has_ganti_player or has_hydrax_text or has_popup_text:
            console.print("[bold green][✓] Laman pemain video sah dikesan! Kekal pada halaman ini.[/bold green]")
            return page

        console.print("[cyan][*] Masih di senarai hasil carian. Mencari kad filem sasaran...[/cyan]")
        forbidden_slugs = ["/country/", "/genre/", "/category/", "/tag/", "/year/", "/cast/", "/actor/"]
        links = page.locator('a[href]').all()
        target_link = None
        query_parts = [p.lower() for p in query_text.strip().split() if len(p) > 2]

        for lnk in links:
            try:
                if not lnk.is_visible():
                    continue
                href = (lnk.get_attribute("href") or "").lower()
                text = (lnk.inner_text() or "").lower()

                if any(bad in href for bad in forbidden_slugs) or "youtube.com" in href:
                    continue

                if any(part in href or part in text for part in query_parts):
                    target_link = lnk
                    break
            except Exception:
                continue

        if target_link:
            console.print("[green][✓] Membuka halaman video yang tepat...[/green]")
            target_link.scroll_into_view_if_needed()
            human_delay(0.8, 1.5)
            target_link.click()
            human_delay(4.0, 6.0)

        return page