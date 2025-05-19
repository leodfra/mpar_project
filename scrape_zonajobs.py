from __future__ import annotations
"""
scrape_zonajobs.py
Scraper modular y eficiente para ZonaJobs usando Playwright.

• login con credenciales .env
• búsqueda mediante inputs (puesto + ubicación)  ó  feed “recientes”
• paginación automática (botón flecha-derecha) en búsquedas filtradas
• extracción de detalle en pestañas aisladas para no romper listado
• detención opcional en caliente mediante scraper_control.ask_to_stop
"""
import os
import re
import unicodedata
import urllib.parse as ul
from typing import Any, Dict, List, Optional
from playwright.sync_api import Browser, sync_playwright

# ─── control de parada opcional ──────────────────────────────────────
try:
    from scraper_control import ask_to_stop  # type: ignore
except ImportError:
    def ask_to_stop(job_id: str) -> bool:
        return False

BASE_URL = "https://www.zonajobs.com.ar/"
LISTING_SELECTOR = (
    "#listado-avisos a[href$='.html'],"
    "div[id^='job-list'] a[href$='.html']"
)
DETAIL_CONTAINER = "#section-detalle"
TIMEOUT = 15_000  # ms
ZERO_JOBS_SEL = "h1:has(span:has-text('0'))"
NEXT_BTN = "a.sc-dzVpKk:not([disabled])"


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text)
    ascii_ = norm.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")


class ZonaJobsScraper:
    def __init__(
        self,
        browser: Browser,
        query: str = "",
        location: str = "",
        max_pages: Optional[int] = None,
        job_id: Optional[str] = None,
    ) -> None:
        self.browser = browser
        self.query = query.strip()
        self.location = location.strip()
        self.max_pages = max_pages
        self.job_id = job_id or ""

        self.context = browser.new_context(viewport=None)
        self.page = self.context.new_page()
        self.filtered_base_url: Optional[str] = None
        self.results: List[Dict[str, Any]] = []

    def run(self) -> List[Dict[str, Any]]:
        self._login()
        page_num = 1
        while True:
            if self.job_id and ask_to_stop(self.job_id):
                break

            hrefs = self._get_listing_hrefs(page_num)
            if not hrefs:
                break

            for url in hrefs:
                if self.job_id and ask_to_stop(self.job_id):
                    break
                try:
                    self.results.append(self._scrape_detail(url))
                except Exception:
                    continue

            if self.max_pages and page_num >= self.max_pages:
                break
            page_num += 1

        self.context.close()
        return self.results

    def _login(self) -> None:
        p = self.page
        p.goto(BASE_URL, timeout=TIMEOUT)
        p.click("#ingresarNavBar")
        p.fill("#email", os.getenv("ZONAJOBS_USER", ""))
        p.fill("#password", os.getenv("ZONAJOBS_PASS", ""))
        p.eval_on_selector("#ingresar", "el => el.removeAttribute('disabled')")
        p.click("#ingresar")
        p.wait_for_load_state("networkidle", timeout=TIMEOUT)

        if self.query or self.location:
            p.goto(BASE_URL, timeout=TIMEOUT)
            self._buscar_con_inputs()
        else:
            self.filtered_base_url = f"{BASE_URL}empleos.html?recientes=true"
            p.goto(f"{self.filtered_base_url}&page=1", timeout=TIMEOUT)
            p.wait_for_selector(LISTING_SELECTOR, timeout=TIMEOUT)

    def _buscar_con_inputs(self) -> None:
        p = self.page
        if self.query:
            p.wait_for_selector("#react-select-6-input", timeout=TIMEOUT)
            p.fill("#react-select-6-input", self.query)
            p.wait_for_timeout(300)

        if self.location:
            ctl = "#lugar-de-trabajo .select__control:not(.select__control--is-disabled)"
            p.wait_for_selector(ctl, timeout=TIMEOUT)
            p.click(ctl)
            p.wait_for_selector("#react-select-7-input", timeout=TIMEOUT)
            p.fill("#react-select-7-input", self.location)
            p.wait_for_timeout(400)
            p.keyboard.press("Enter")
        elif self.query:
            p.click("#buscarTrabajo")

        p.wait_for_load_state("networkidle", timeout=TIMEOUT)
        if p.locator(ZERO_JOBS_SEL).count():
            self.filtered_base_url = None
            return

        p.wait_for_selector(LISTING_SELECTOR, timeout=TIMEOUT)
        self.filtered_base_url = p.url

    def _click_next_page(self) -> bool:
        btn = self.page.locator(NEXT_BTN).first
        if not btn.count():
            return False
        btn.scroll_into_view_if_needed()
        btn.click()
        self.page.wait_for_load_state("networkidle", timeout=TIMEOUT)
        return True


    def _page_url(self, page: int) -> str:
        if self.query or self.location:
            loc = f"{slugify(self.location)}/" if self.location else ""
            base = f"{BASE_URL}{loc}empleos.html"
            params = [f"page={page}"]
            if self.query:
                params.insert(0, f"palabra={ul.quote_plus(self.query)}")
            return f"{base}?{'&'.join(params)}"
        return f"{BASE_URL}empleos.html?recientes=true&page={page}"

    def _get_listing_hrefs(self, page: int) -> List[str]:
        # 1) parada “en caliente”
        if self.job_id and ask_to_stop(self.job_id):
            return []

        # 2) primera página ya viene cargada tras _login/_buscar_con_inputs
        if page > 1 and self.filtered_base_url:
            # búsqueda filtrada → click en siguiente
            if not self._click_next_page():
                print(f"[INFO] Ya no hay más páginas tras la {page-1}")
                return []
        elif page > 1:
            # feed “recientes” o manual → fallback a URL directa
            self.page.goto(self._page_url(page), timeout=TIMEOUT)

        # 3) esperar listado o “0 empleos…”
        try:
            self.page.wait_for_selector(f"{LISTING_SELECTOR}, {ZERO_JOBS_SEL}", timeout=TIMEOUT)
        except Exception:
            print(f"[WARN] timeout esperando página {page}")
            return []

        # 4) si hay 0 resultados, terminar
        if self.page.locator(ZERO_JOBS_SEL).count():
            print(f"[INFO] Página {page}: 0 avisos — finalizado")
            return []

        # 5) extraer URLs
        urls = self.page.locator(LISTING_SELECTOR).evaluate_all(
            "els => [...new Set(els.map(e => e.href))]"
        )
        return sorted(urls)

    def _scrape_detail(self, url: str) -> Dict[str, Any]:
        if self.job_id and ask_to_stop(self.job_id):
            raise Exception("Parado por usuario")
        page_detail = self.context.new_page()
        page_detail.goto(url, timeout=TIMEOUT)
        page_detail.wait_for_selector(DETAIL_CONTAINER, timeout=TIMEOUT)

        data: Dict[str, Any] = {"url": url}
        data["titulo"] = page_detail.locator(
            f"h1, {DETAIL_CONTAINER} h1, {DETAIL_CONTAINER} h2"
        ).first.inner_text().strip()

        comp = page_detail.locator("text=Confidencial").first or \
               page_detail.locator("a[href*='/perfiles/empresa'], h3").first
        data["empresa"] = comp.inner_text().strip() if comp.count() else ""

        pub = page_detail.locator(
            f"{DETAIL_CONTAINER} h2:has-text('Publicado'),"
            f"{DETAIL_CONTAINER} h2:has-text('Actualizado')"
        ).first
        data["publicado"] = pub.inner_text().strip() if pub.count() else ""

        desc_nodes = page_detail.locator(f"{DETAIL_CONTAINER} p").all()
        data["descripcion"] = "\n\n".join(
            n.inner_text().strip() for n in desc_nodes if n.inner_text().strip()
        )

        def grab(sel: str) -> str:
            elem = page_detail.locator(sel).first
            return elem.inner_text().strip() if elem.count() else ""

        data["industria"] = grab("p:has-text('Industria') + p")
        data["ubicacion"] = grab("p:has-text('Ubicación') + p")
        data["tamano_empresa"] = grab("p:has-text('Tamaño de la empresa') + span")
        data["modalidad"] = grab(f"{DETAIL_CONTAINER} li a[href*='modalidad-'] p")

        data["beneficios"] = [
            b.inner_text().strip()
            for b in page_detail.locator(f"{DETAIL_CONTAINER} ul li").all()
            if b.inner_text().strip()
        ]
        data["tags"] = sorted({
            t.inner_text().strip()
            for t in page_detail.locator(f"{DETAIL_CONTAINER} li p").all()
            if t.inner_text().strip()
        })

        page_detail.close()
        return data


def scrape_zonajobs(
    *, job_id: Optional[str] = None,
    query: str = "", location: str = "", **_
) -> List[Dict[str, Any]]:
    pages_env = os.getenv("ZJ_PAGES", "")
    pages = int(pages_env) if pages_env.isdigit() else None
    query_env = query or os.getenv("ZJ_QUERY", "")
    location_env = location or os.getenv("ZJ_LOCATION", "")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--start-maximized"],
        )
        try:
            return ZonaJobsScraper(
                browser=browser,
                query=query_env,
                location=location_env,
                max_pages=pages,
                job_id=job_id,
            ).run()
        finally:
            browser.close()
