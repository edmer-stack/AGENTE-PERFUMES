"""
Navegador automático (Chromium sin ventana) para el agente de perfumes.
Hace lo que haría una persona: abre la web, acepta cookies, escribe el perfume
en el buscador y devuelve el HTML ya cargado (con JavaScript ejecutado).
"""
import re

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
COOKIES = re.compile(r"^(aceptar( todas?| todo| cookies)?|acepto|permitir( todas?)?|accept( all)?|entendido|de acuerdo)$", re.I)
CAJAS = ['input[type="search"]', 'input[name="q"]', 'input[name="s"]', 'input[name="search"]',
         'input[name="query"]', 'input[name="keyword"]', 'input[placeholder*="uscar" i]',
         'input[aria-label*="uscar" i]', 'input[id*="search" i]', 'input[class*="search" i]',
         'input[role="searchbox"]', '[role="searchbox"]', 'input[placeholder*="busca" i]',
         'input[placeholder*="qué" i]', 'input[placeholder*="encuentr" i]', 'input[name*="search" i]',
         'input[name*="buscar" i]', 'input[type="text"]:visible']
LUPAS = ['button[aria-label*="uscar" i]', 'a[aria-label*="uscar" i]',
         '[class*="search" i][class*="icon" i]', '[class*="search" i][class*="toggle" i]',
         '[class*="search" i][class*="button" i]']


class Navegador:
    def __init__(self, visible=False, carpeta_debug=None):
        self.visible = visible
        self.carpeta_debug = carpeta_debug

    def depurar(self, nombre):
        """Guarda captura y HTML de la página actual para ver por qué falla una tienda."""
        if not self.carpeta_debug:
            return
        try:
            self.carpeta_debug.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(self.carpeta_debug / f"{nombre}.png"))
        except Exception:
            pass

    def __enter__(self):
        self.pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        try:  # Chrome real instalado: las tiendas lo bloquean menos que al Chromium de pruebas
            self.browser = self.pw.chromium.launch(channel="chrome", headless=not self.visible, args=args)
        except Exception:
            self.browser = self.pw.chromium.launch(headless=not self.visible, args=args)
        self.ctx = self.browser.new_context(
            locale="es-ES", timezone_id="Europe/Madrid", user_agent=UA,
            viewport={"width": 1366, "height": 900})
        self.ctx.set_default_timeout(20000)
        self.page = self.ctx.new_page()
        return self

    def __exit__(self, *exc):
        for obj in (self.ctx, self.browser):
            try:
                obj.close()
            except Exception:
                pass
        try:
            self.pw.stop()
        except Exception:
            pass

    def _cookies(self):
        try:
            self.page.get_by_role("button", name=COOKIES).first.click(timeout=2500)
        except Exception:
            pass

    def abrir(self, url):
        """Devuelve (html, url_final) o (None, motivo)."""
        try:
            r = self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self.page.wait_for_timeout(1500)
            self._cookies()
            self.page.wait_for_timeout(400)
            if r is not None and r.status >= 400:
                return None, f"HTTP {r.status}"
            return self.page.content(), self.page.url
        except Exception as e:
            return None, type(e).__name__

    def buscar_con_formulario(self, home, consulta):
        """Abre la home, escribe en el buscador y devuelve (html, url) o (None, motivo)."""
        html, info = self.abrir(home)
        if html is None:
            return None, info
        for _ in range(2):
            try:
                self.page.keyboard.press("Escape")  # cierra ventanas emergentes
            except Exception:
                pass
            for sel in CAJAS:
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click()
                        loc.fill(consulta)
                        loc.press("Enter")
                        self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                        self.page.wait_for_timeout(2200)
                        return self.page.content(), self.page.url
                except Exception:
                    continue
            for sel in LUPAS:  # abrir el buscador si estaba oculto tras un icono
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click()
                        self.page.wait_for_timeout(800)
                        break
                except Exception:
                    continue
        return None, "sin buscador"
