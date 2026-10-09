import time
import random
from typing import Optional
from playwright.sync_api import Page, Locator
from rich.console import Console

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

console = Console()


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
    """Skrol halaman ke bawah secara berperingkat agar elemen di bawah video kelihatan."""
    steps = 8
    step_px = pixels // steps
    for _ in range(steps):
        page.mouse.wheel(0, step_px)
        time.sleep(random.uniform(0.05, 0.12))
    human_delay(1.0, 1.8)


def smart_human_type(locator: Locator, page: Page, text: str):
    """Menaip perkataan mengikut ritme semula jadi papan kekunci."""
    locator.scroll_into_view_if_needed()
    locator.click()
    human_delay(0.6, 1.2)

    locator.fill("")
    time.sleep(random.uniform(0.3, 0.5))

    for idx, char in enumerate(text):
        char_delay = random.uniform(0.14, 0.25) if char in " -_.,/" else random.uniform(0.08, 0.18)
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


def find_search_input(page: Page, timeout_sec: int = 15) -> Optional[Locator]:
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
        for frame in [page] + page.frames:
            for sel in selectors:
                try:
                    loc = frame.locator(sel).first
                    if loc.is_visible(timeout=250):
                        console.print(f"[green][✓] Kotak carian ditemui: '{sel}'[/green]")
                        return loc
                except Exception:
                    continue

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

    try:
        generic_inputs = page.locator('input[type="text"]').all()
        for inp in generic_inputs:
            if inp.is_visible():
                return inp
    except Exception:
        pass

    return None


def select_dropdown_match(page: Page, query_text: str, timeout_sec: int = 8) -> bool:
    console.print(f"[cyan][*] Memeriksa menu dropdown cadangan bagi: '{query_text}'...[/cyan]")
    dropdown_containers = [
        '[role="listbox"]',
        '[role="menu"]',
        'ul[class*="suggest" i]',
        'ul[class*="search" i]',
        'div[class*="autocomplete" i]',
        'div[class*="search" i]',
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


def ensure_movie_watch_page(page: Page, query_text: str) -> Page:
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


def activate_hydrax_and_open_popup(page: Page) -> Page:
    console.print("\n[bold yellow][*] Mengesan bar pelayan di bawah video...[/bold yellow]")
    smooth_scroll_down(page, pixels=350)
    human_delay(1.5, 2.5)

    hydrax_locators = [
        page.locator("text=/^\\s*HYDRAX\\s*$/i"),
        page.get_by_text("HYDRAX", exact=True),
        page.locator("xpath=//*[normalize-space(text())='HYDRAX']"),
        page.locator("a:has-text('HYDRAX')"),
        page.locator("span:has-text('HYDRAX')"),
        page.locator("li:has-text('HYDRAX')")
    ]

    hydrax_btn = None
    start_find = time.time()
    while time.time() - start_find < 15:
        for loc in hydrax_locators:
            try:
                first_item = loc.first
                if first_item.is_visible(timeout=400):
                    hydrax_btn = first_item
                    break
            except Exception:
                continue
        if hydrax_btn:
            break
        page.wait_for_timeout(500)

    if not hydrax_btn:
        for frame in page.frames:
            try:
                loc = frame.locator("text=/HYDRAX/i").first
                if loc.is_visible(timeout=400):
                    hydrax_btn = loc
                    break
            except Exception:
                continue

    if hydrax_btn:
        console.print("[bold green][✓] Butang 'HYDRAX' ditemui! Melakukan klik santai...[/bold green]")
        hydrax_btn.scroll_into_view_if_needed()
        human_delay(1.0, 1.8)
        hydrax_btn.click()
    else:
        console.print("[red][✕] Butang 'HYDRAX' tidak ditemui pada bar pelayan![/red]")

    console.print("[cyan]⏳ Menunggu tepat 5 saat selepas memilih HYDRAX...[/cyan]")
    for sec in range(5, 0, -1):
        console.print(f"   [dim]{sec} saat lagi...[/dim]", end="\r")
        time.sleep(1.0)
    print()

    popup_locators = [
        page.locator("text=/^\\s*Popup\\s*$/i"),
        page.get_by_text("Popup", exact=False),
        page.locator("xpath=//*[normalize-space(text())='Popup']"),
        page.locator("a:has-text('Popup')"),
        page.locator("button:has-text('Popup')"),
        page.locator("span:has-text('Popup')")
    ]

    popup_btn = None
    for p_loc in popup_locators:
        try:
            first_item = p_loc.first
            if first_item.is_visible(timeout=500):
                popup_btn = first_item
                break
        except Exception:
            continue

    if not popup_btn:
        console.print("[yellow][!] Butang 'Popup' tidak ditemui; kekal pada laman semasa.[/yellow]")
        return page

    console.print("[bold green][✓] Butang 'Popup' ditemui! Mengklik untuk membuka pemain...[/bold green]")
    popup_btn.scroll_into_view_if_needed()
    human_delay(0.8, 1.5)

    target_popup_page = None
    try:
        with page.expect_popup(timeout=10000) as popup_info:
            popup_btn.click()
        target_popup_page = popup_info.value
    except Exception:
        current_pages = page.context.pages
        if len(current_pages) > 1:
            target_popup_page = current_pages[-1]

    if target_popup_page:
        console.print(f"[bold green][✓] Laman popup pemain dibuka: {target_popup_page.url[:70]}[/bold green]")
        try:
            target_popup_page.wait_for_load_state("domcontentloaded", timeout=15000)
            target_popup_page.bring_to_front()
        except Exception:
            pass
        human_delay(2.5, 4.0)
        return target_popup_page

    return page