from __future__ import annotations
"""
scrape_mpar.py
────────────────────────────────────────────────────────────────────
Scraper de MPAR (Cornerstone) con Playwright + Chrome

• Recorre todas las tarjetas de empleo
• Entra a cada aviso, extrae campos clave y vuelve al listado
• Pagina con el botón “next”
• Se detiene cuando no hay más páginas, se alcanza `max_pages`
  o `ask_to_stop(job_id)` indica que el usuario pulsó «Detener scraping»
────────────────────────────────────────────────────────────────────
"""
from typing import List, Dict, Optional
import os

from playwright.sync_api import (
    sync_playwright,
    Playwright,
    Page,
    TimeoutError,
)

from scraper_control import ask_to_stop        # 🆕 señal de cancelación

BASE_URL  = "https://mpar.csod.com/ux/ats/careersite/10/home?c=mpar"
CARD_SEL  = 'a[data-tag="displayJobTitle"]'
NEXT_SEL  = 'button.page-nav-caret.next'

# ─────────── helpers ────────────
def safe_text(page: Page, sel: str, timeout: int = 3_000) -> str:
    try:
        page.wait_for_selector(sel, timeout=timeout)
        return page.locator(sel).inner_text().strip()
    except TimeoutError:
        return ""

# ─────────── runner ─────────────
def run(
    pw: Playwright,
    *,
    job_id: Optional[str] = None,
    max_pages: Optional[int] = None,
) -> List[Dict]:
    browser  = pw.chromium.launch(headless=False)
    ctx      = browser.new_context()
    ctx.set_default_navigation_timeout(60_000)
    page     = ctx.new_page()

    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_selector(CARD_SEL, timeout=10_000)

    data: List[Dict] = []
    page_num = 1

    while True:
        # —— Cancelación global ——
        if job_id and ask_to_stop(job_id):
            print("[INFO] Scraping MPAR detenido por el usuario.")
            break

        # —— Recorrer tarjetas de la página actual ——
        total_cards = page.locator(CARD_SEL).count()
        for i in range(total_cards):
            if job_id and ask_to_stop(job_id):
                break

            page.locator(CARD_SEL).nth(i).click()
            page.wait_for_load_state("domcontentloaded")

            data.append(
                {
                    "titulo":      safe_text(page, 'h1[data-tag="ReqTitle"]'),
                    "ubicacion":   safe_text(page, '[data-tag="displayLocationMessage"]'),
                    "req_id":      safe_text(page, 'span[data-tag="ReqId"]'),
                    "descripcion": safe_text(
                        page,
                        'div[data-tag="JobDescription"], div[id="jobdescription"]',
                    ),
                    "fuente": "mpar",
                    "url":    page.url,
                }
            )

            page.go_back()
            page.wait_for_selector(CARD_SEL, timeout=15_000)

        # —— Cancelación tras terminar la página ——
        if job_id and ask_to_stop(job_id):
            break

        # —— Límite de páginas ——
        if max_pages and page_num >= max_pages:
            break

        # —— Paginar —— 
        next_btn = page.locator(NEXT_SEL)
        if next_btn.count() and not next_btn.get_attribute("disabled"):
            next_btn.click()
            page.wait_for_selector(CARD_SEL, timeout=15_000)
            page_num += 1
        else:
            break

    browser.close()
    return data

# ────────── wrapper PnP ──────────
def scrape_mpar(job_id: Optional[str] = None) -> List[Dict]:
    """
    Punto de entrada para app.py.
    Recibe el `job_id` (string) que generó el backend y lo
    re-envía al runner para poder cancelar desde la UI.
    """
    pages_env = os.getenv("MPAR_PAGES")
    max_pages = int(pages_env) if pages_env and pages_env.isdigit() else None

    with sync_playwright() as pw:
        return run(pw, job_id=job_id, max_pages=max_pages)
