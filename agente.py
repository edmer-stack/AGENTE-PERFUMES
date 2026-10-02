#!/usr/bin/env python3
"""
Agente diario de perfumes.
  1) Busca precios en las tiendas de config.yaml y calcula el PRECIO TOTAL
     (precio + envío + IVA/aranceles/gestión si viene de fuera de la UE).
  2) Guarda el histórico propio (data/historial.csv), lo analiza y da veredicto.
  3) Genera gráficas, un informe HTML en tabla y lo envía por email + WhatsApp.

Uso:
  python agente.py              # respeta la hora de envío (para automatizar)
  python agente.py --ahora      # fuerza la ejecución ya (envía email/WhatsApp)
  python agente.py --prueba     # ejecuta ya, NO envía nada, guarda data/ultimo_informe.html
  python agente.py --prueba --visible   # igual, pero ves el navegador trabajando (para depurar)

Puede ejecutarse varias veces al día: envía como máximo un informe por cada hora que
figure en "horarios" de config.yaml (por ejemplo 7:00 y 15:00), nunca dos veces la misma franja.
"""
import csv, json, os, re, smtplib, statistics, sys, time, unicodedata
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):  # evita UnicodeEncodeError en consolas de Windows (cp1252)
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import datetime as dt
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
DATA = ROOT / "data"
GRAF = DATA / "graficos"
HIST = DATA / "historial.csv"
ESTADO = DATA / "estado.json"
CAMPOS = ["fecha", "perfume_id", "tienda", "precio", "total", "url"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}
RECHAZAR = ("muestra", "sample", "miniatura", "tester", "vaporizador", "rellenable", "cofre", "estuche", "set ")


# ------------------------------------------------------------------ utilidades
def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def eur(x):
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def descargar(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        time.sleep(1.5)  # cortesía con las webs
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


# ------------------------------------------------------------------ scraping
def leer_producto(html):
    """Devuelve (titulo, precio) leyendo JSON-LD schema.org o metaetiquetas."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        pila = [data]
        while pila:
            x = pila.pop()
            if isinstance(x, list):
                pila.extend(x)
                continue
            if not isinstance(x, dict):
                continue
            if isinstance(x.get("@graph"), list):
                pila.extend(x["@graph"])
            t = x.get("@type")
            if "Product" in (t if isinstance(t, list) else [t]):
                ofertas = x.get("offers")
                ofertas = ofertas if isinstance(ofertas, list) else [ofertas]
                precios = []
                for o in ofertas:
                    if not isinstance(o, dict) or "OutOfStock" in str(o.get("availability", "")):
                        continue
                    if o.get("priceCurrency", "EUR") != "EUR":
                        continue
                    try:
                        precios.append(float(str(o.get("price") or o.get("lowPrice")).replace(",", ".")))
                    except (TypeError, ValueError):
                        pass
                if precios:
                    return str(x.get("name", "")), min(precios)
    tit = soup.find(id="productTitle")
    pre = soup.select_one("span.a-price span.a-offscreen")
    if tit and pre:  # Amazon
        num = re.sub(r"[^\d,\.]", "", pre.get_text()).replace(".", "").replace(",", ".")
        try:
            return tit.get_text(strip=True), float(num)
        except ValueError:
            pass
    m = soup.find("meta", property="product:price:amount") or soup.find("meta", itemprop="price")
    if m and m.get("content"):
        try:
            return (soup.title.string if soup.title else ""), float(m["content"].replace(",", "."))
        except ValueError:
            pass
    return None


def coincide(titulo, p):
    """False = no es; True = es; 'sin_ml' = parece que es pero el título no indica tamaño."""
    t = norm(titulo)
    if not all(tok in t for tok in norm(p["nombre"]).split()) or norm(p["marca"]) not in t:
        return False
    sin_ml = False
    if not re.search(rf"\b{p['ml']}\s?ml", t):
        if re.search(r"\b\d+([.,]\d+)?\s?ml", t):
            return False  # otro tamaño
        sin_ml = True
    if any(w in t for w in RECHAZAR):
        return False
    conc = p.get("concentracion", "").upper()
    if conc == "EDP" and re.search(r"eau de toilette|\bedt\b", t):
        return False
    if conc == "EXTRACTO" and re.search(r"eau de (parfum|toilette)|\bedp\b|\bedt\b", t):
        return False
    return "sin_ml" if sin_ml else True


def candidatos(html, base, p):
    soup = BeautifulSoup(html, "html.parser")
    toks = norm(p["nombre"]).split()
    dominio = urlparse(base).netloc.replace("www.", "")
    vistos, res = set(), []
    for a in soup.find_all("a", href=True):
        u = urljoin(base, a["href"].split("#")[0])
        pu = urlparse(u)
        if dominio not in pu.netloc or any(x in u for x in ("signin", "sponsored", "/ap/", "login")):
            continue
        txt = norm(a.get_text(" ") + " " + pu.path.replace("-", " ").replace("/", " "))
        if all(t in txt for t in toks) and u not in vistos:
            vistos.add(u)
            res.append(u)
    return res[:4]


def precio_total(precio, t, imp):
    envio = 0.0 if precio >= t.get("envio_gratis_desde", 1e9) else t.get("envio", 0.0)
    base = precio + envio
    total = base
    if t.get("origen") == "fuera_UE":
        arancel = base * imp["arancel"]
        total = base + arancel
        if not t.get("iva_incluido", True):
            total += total * imp["iva"]
        total += imp["gestion_aduana"]
    elif not t.get("iva_incluido", True):
        total += base * imp["iva"]
    return round(total, 2)


PATRONES = ["search?q={q}", "buscar?q={q}", "busqueda?controller=search&s={q}", "buscar?controller=search&s={q}",
            "catalogsearch/result/?q={q}", "?s={q}", "search?query={q}", "es/search?q={q}", "search.php?f={q}",
            "buscador?q={q}", "search?text={q}"]
PATRON_OK, PATRON_PROBADO = {}, set()


def buscar_urls(nav, p, t, tid):
    """Devuelve (lista_de_urls, motivo). lista None = tienda inaccesible."""
    consulta = f"{p['marca']} {p['nombre']}"
    q = quote_plus(consulta)
    base = t.get("web", "")
    cargo = False
    # 1) URL directa conocida (config o descubierta antes)
    directas = ([t["search"]] if t.get("search") else []) + ([urljoin(base, PATRON_OK[tid])] if tid in PATRON_OK else [])
    for d in directas:
        html, url = nav.abrir(d.format(q=q))
        if html is not None:
            cargo = True
            c = candidatos(html, url, p)
            if c:
                return c, "ok"
    # 2) escribir en el buscador de la web
    if not base:
        return (None, "sin web en config") if not cargo else ([], "sin resultados")
    html, url = nav.buscar_con_formulario(base, consulta)
    if html is not None:
        c = candidatos(html, url, p)
        return (c, "ok") if c else ([], "sin resultados")
    motivo = url
    if motivo == "sin buscador" and tid not in PATRON_PROBADO:
        nav.depurar(tid)  # captura en data/debug para ver qué muestra la tienda
    # 3) probar URLs de búsqueda habituales (una sola vez por tienda)
    if not str(motivo).startswith("HTTP") and tid not in PATRON_OK and tid not in PATRON_PROBADO:
        PATRON_PROBADO.add(tid)
        for pat in PATRONES:
            h, u = nav.abrir(urljoin(base, pat.format(q=q)))
            if h is not None and norm(p["nombre"].split()[0]) in norm(h):
                PATRON_OK[tid] = pat
                c = candidatos(h, u, p)
                return (c, "ok") if c else ([], "sin resultados")
    return ([], "sin resultados") if cargo else (None, motivo)


def buscar(p, tiendas, imp, hoy, nav, muertas):
    filas, estado = [], {}
    for tid, t in tiendas.items():
        if not t.get("activa", True):
            continue
        if tid in muertas:
            estado[tid] = muertas[tid]
            continue
        fijada = (p.get("urls") or {}).get(tid)
        if fijada:
            urls, fija = [fijada.strip()], True
        else:
            cands, info = buscar_urls(nav, p, t, tid)
            if cands is None:
                estado[tid] = f"no accesible ({info})"
                muertas[tid] = estado[tid]  # no insistir con esta tienda en toda la ejecución
                continue
            if not cands:
                estado[tid] = "sin resultados"
                continue
            urls, fija = cands[:2], False
        mejor = None
        for u in urls:
            html, _ = nav.abrir(u)
            prod = leer_producto(html) if html else None
            if not prod:
                continue
            ok = True if fija else coincide(prod[0], p)
            if not ok:
                continue
            tot = precio_total(prod[1], t, imp)
            if not mejor or tot < mejor["total"]:
                mejor = dict(fecha=str(hoy), perfume_id=p["id"], tienda=t["nombre"], precio=prod[1],
                             total=tot, url=u, aviso="tamaño sin verificar" if ok == "sin_ml" else "")
        estado[tid] = "ok" if mejor else "sin precio válido"
        if mejor:
            filas.append(mejor)
    return filas, estado


def resumen_cobertura(cob, tiendas):
    filas = []
    for tid, estados in cob.items():
        ok = sum(1 for e in estados if e == "ok")
        malos = [e for e in estados if e != "ok"]
        filas.append((tiendas[tid]["nombre"], ok, len(estados), malos[0] if malos else ""))
    return sorted(filas, key=lambda f: -f[1])


# ------------------------------------------------------------------ histórico
def cargar_hist():
    if not HIST.exists():
        return []
    with open(HIST, newline="", encoding="utf-8") as f:
        return [dict(r, precio=float(r["precio"]), total=float(r["total"])) for r in csv.DictReader(f)]


def guardar_hist(filas, ids, hoy):
    previas = [r for r in cargar_hist() if not (r["fecha"] == str(hoy) and r["perfume_id"] in ids)]
    DATA.mkdir(exist_ok=True)
    with open(HIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, extrasaction="ignore")
        w.writeheader()
        w.writerows(previas + filas)


def serie_diaria(hist, pid, hoy):
    """Mejor precio total de cada día, últimos 24 meses."""
    corte = str(hoy - dt.timedelta(days=730))
    d = {}
    for r in hist:
        if r["perfume_id"] == pid and r["fecha"] >= corte:
            d[r["fecha"]] = min(d.get(r["fecha"], 1e9), r["total"])
    return sorted(d.items())


# ------------------------------------------------------------------ análisis
def analizar(serie, ofertas_hoy, cfg):
    actual = min(o["total"] for o in ofertas_hoy)
    vals = [v for _, v in serie]
    n = len(vals)
    mercado = [o["total"] for o in ofertas_hoy]
    res = dict(actual=actual, n=n, minimo=min(vals), mediana=statistics.median(vals))
    if n < cfg["min_dias_historial"]:
        med = statistics.median(mercado)
        res.update(objetivo=round(med * 0.92, 2), confianza="baja (modo arranque)", tendencia="sin datos")
        if len(mercado) >= 3 and actual <= med * 0.88:
            res["veredicto"] = "COMPRA INMEDIATA"
            res["motivo"] = "Provisional: está claramente por debajo de la mediana de tiendas."
        else:
            res["veredicto"] = "PERMANECER EN ESPERA"
            res["motivo"] = "Recopilando histórico propio; aún no hay base para otra cosa."
        return res
    pct = sum(v < actual for v in vals) / n * 100
    ultimos = vals[-min(30, n):]
    xs = list(range(len(ultimos)))
    mx, my = statistics.mean(xs), statistics.mean(ultimos)
    den = sum((x - mx) ** 2 for x in xs) or 1
    pend = sum((x - mx) * (y - my) for x, y in zip(xs, ultimos)) / den
    sem = pend * 7 / my * 100
    srt = sorted(vals)
    obj = srt[int(0.2 * (n - 1))] if n >= 30 else statistics.median(vals) * 0.92
    res.update(objetivo=round(obj, 2), pct=round(pct),
               confianza="alta" if n >= 90 else "media" if n >= 30 else "baja",
               tendencia=f"{sem:+.1f} %/semana")
    if actual <= res["minimo"] * 1.03 or pct <= 15:
        res["veredicto"], res["motivo"] = "COMPRA INMEDIATA", f"Está entre el {pct:.0f} % más barato de su histórico."
    elif pct >= 75 or actual >= res["mediana"] * 1.10:
        res["veredicto"], res["motivo"] = "DESESTIMAR COMPRA", "Precio inflado frente a su mediana histórica."
    else:
        extra = " La tendencia es a la baja." if sem < -0.5 else ""
        res["veredicto"], res["motivo"] = "PERMANECER EN ESPERA", "Precio normal; hay margen de mejora." + extra
    return res


# ------------------------------------------------------------------ gráficas
COL = {"COMPRA INMEDIATA": "#2E9E6B", "PERMANECER EN ESPERA": "#E0A030", "DESESTIMAR COMPRA": "#D9534F"}


def grafica(p, serie, res):
    GRAF.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 3.2), dpi=140)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#F7FAFA")
    fechas = [dt.date.fromisoformat(f) for f, _ in serie]
    vals = [v for _, v in serie]
    color = COL.get(res["veredicto"], "#3AA6A0")
    if len(vals) > 1:
        ax.plot(fechas, vals, color="#3AA6A0", lw=2.4, solid_capstyle="round")
        ax.fill_between(fechas, vals, min(vals) * 0.97, color="#3AA6A0", alpha=0.12)
        ax.axhline(res["mediana"], color="#9AA5B1", ls="--", lw=1)
        ax.text(fechas[0], res["mediana"], " mediana", va="bottom", fontsize=8, color="#7B8794")
    ax.axhline(res["objetivo"], color="#2E9E6B", ls=":", lw=1.4)
    ax.text(fechas[-1], res["objetivo"], "objetivo ", ha="right", va="bottom", fontsize=8, color="#2E9E6B")
    ax.scatter([fechas[-1]], [vals[-1]], s=90, color=color, zorder=5, edgecolor="white", linewidth=2)
    ax.annotate(eur(vals[-1]), (fechas[-1], vals[-1]), textcoords="offset points", xytext=(0, 12),
                ha="center", fontsize=10, fontweight="bold", color=color)
    ax.set_title(f"{p['nombre']} · {p['marca']}  ({res['n']} días de histórico)", loc="left",
                 fontsize=11, fontweight="bold", color="#2B3A42")
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color="white", lw=1.5)
    ax.tick_params(colors="#7B8794", labelsize=8, length=0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f} €"))
    if len(vals) <= 1:
        ax.text(0.5, 0.5, "Empezando a recopilar histórico…", transform=ax.transAxes,
                ha="center", color="#9AA5B1")
    fig.tight_layout()
    ruta = GRAF / f"{p['id']}.png"
    fig.savefig(ruta)
    plt.close(fig)
    return ruta


# ------------------------------------------------------------------ informe
def informe(resultados, alternativas, cobertura, hoy):
    def badge(v):
        return f'<span style="background:{COL.get(v, "#999")};color:#fff;padding:3px 8px;border-radius:10px;font-size:12px;white-space:nowrap">{v}</span>'

    css = "font-family:Arial,Helvetica,sans-serif;color:#2B3A42"
    th = 'style="text-align:left;padding:8px;background:#EAF4F3;font-size:12px"'
    td = 'style="padding:8px;border-bottom:1px solid #E5ECEC;font-size:13px;vertical-align:top"'
    h = [f'<div style="{css};max-width:680px;margin:auto"><h2>Informe de perfumes · {hoy:%d/%m/%Y}</h2>']
    h.append(f'<table width="100%" cellspacing="0" style="border-collapse:collapse"><tr>'
             f'<th {th}>Perfume</th><th {th}>Mejor total</th><th {th}>Tienda</th><th {th}>Objetivo</th><th {th}>Veredicto</th></tr>')
    for p, res, ofertas in resultados:
        b = min(ofertas, key=lambda o: o["total"])
        h.append(f'<tr><td {td}><b>{p["nombre"]}</b><br><small>{p["marca"]} · {p["ml"]} ml</small></td>'
                 f'<td {td}><b>{eur(res["actual"])}</b></td><td {td}><a href="{b["url"]}">{b["tienda"]}</a></td>'
                 f'<td {td}>{eur(res["objetivo"])}</td><td {td}>{badge(res["veredicto"])}</td></tr>')
    h.append("</table>")
    for p, res, ofertas in resultados:
        h.append(f'<h3 style="margin-bottom:4px">{p["nombre"]}</h3>')
        h.append(f'<p style="margin:2px 0;font-size:13px">{badge(res["veredicto"])} {res["motivo"]}<br>'
                 f'<small>Tendencia: {res["tendencia"]} · Confianza: {res["confianza"]}</small></p>')
        h.append(f'<img src="cid:g_{p["id"]}" style="width:100%;max-width:640px;border-radius:12px">')
        h.append(f'<table width="100%" cellspacing="0" style="border-collapse:collapse;margin-top:4px"><tr>'
                 f'<th {th}>Tienda</th><th {th}>Precio web</th><th {th}>Total con todo</th></tr>')
        for o in sorted(ofertas, key=lambda o: o["total"])[:4]:
            h.append(f'<tr><td {td}><a href="{o["url"]}">{o["tienda"]}</a>{" ⚠" if o.get("aviso") else ""}</td><td {td}>{eur(o["precio"])}</td>'
                     f'<td {td}><b>{eur(o["total"])}</b></td></tr>')
        h.append("</table>")
    if alternativas:
        h.append("<h3>Alternativas parecidas (dentro de presupuesto)</h3>")
        h.append(f'<table width="100%" cellspacing="0" style="border-collapse:collapse"><tr><th {th}>Alternativa</th>'
                 f'<th {th}>Parecido a</th><th {th}>Mejor total</th><th {th}>Por qué</th></tr>')
        for a, o in alternativas:
            h.append(f'<tr><td {td}><a href="{o["url"]}">{a["nombre"]}</a><br><small>{a["marca"]}</small></td>'
                     f'<td {td}>{a.get("similar_a", "")}</td><td {td}><b>{eur(o["total"])}</b></td>'
                     f'<td {td}>{a.get("motivo", "")}</td></tr>')
        h.append("</table>")
    if cobertura:
        h.append("<h3>Tiendas consultadas hoy</h3>")
        h.append(f'<table width="100%" cellspacing="0" style="border-collapse:collapse"><tr><th {th}>Tienda</th>'
                 f'<th {th}>Perfumes con precio</th><th {th}>Incidencia</th></tr>')
        for nombre, ok, total, motivo in cobertura:
            h.append(f'<tr><td {td}>{nombre}</td><td {td}>{ok} de {total}</td><td {td}>{motivo}</td></tr>')
        h.append("</table>")
        h.append('<p style="font-size:11px;color:#9AA5B1">⚠ = la ficha no indicaba el tamaño; comprueba los ml antes de comprar.</p>')
    h.append('<p style="font-size:11px;color:#9AA5B1">Precios totales = precio + envío + impuestos/aduana según config.yaml. '
             'Comprueba siempre el importe final en la tienda antes de pagar.</p></div>')
    return "\n".join(h)


def enviar_email(html, imagenes, hoy):
    user, pw, to = os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASS"), os.getenv("EMAIL_TO")
    if not (user and pw and to):
        print("⚠ Faltan EMAIL_USER / EMAIL_PASS / EMAIL_TO: no se envía email.")
        return
    msg = MIMEMultipart("related")
    msg["Subject"], msg["From"], msg["To"] = f"Informe de perfumes {hoy:%d/%m/%Y}", user, to
    msg.attach(MIMEText(html, "html", "utf-8"))
    for pid, ruta in imagenes.items():
        img = MIMEImage(Path(ruta).read_bytes())
        img.add_header("Content-ID", f"<g_{pid}>")
        img.add_header("Content-Disposition", "inline")
        msg.attach(img)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.send_message(msg)
    print("✔ Email enviado")


def enviar_whatsapp(resultados):
    phone, key = os.getenv("WA_PHONE"), os.getenv("WA_APIKEY")
    if not (phone and key):
        print("⚠ Faltan WA_PHONE / WA_APIKEY: no se envía WhatsApp.")
        return
    icono = {"COMPRA INMEDIATA": "🟢", "PERMANECER EN ESPERA": "🟡", "DESESTIMAR COMPRA": "🔴"}
    lineas = ["*Perfumes hoy*"] + [f'{icono.get(r["veredicto"], "•")} {p["nombre"]}: {eur(r["actual"])} · {r["veredicto"].title()}'
                                   for p, r, _ in resultados]
    lineas.append("Detalle completo en tu email.")
    try:
        requests.get("https://api.callmebot.com/whatsapp.php",
                     params={"phone": phone, "text": "\n".join(lineas), "apikey": key}, timeout=30)
        print("✔ WhatsApp enviado")
    except requests.RequestException as e:
        print("⚠ WhatsApp falló:", e)


# ------------------------------------------------------------------ principal
def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    ahora = dt.datetime.now(ZoneInfo("Europe/Madrid"))
    hoy = ahora.date()
    prueba, forzar = "--prueba" in sys.argv, "--ahora" in sys.argv or os.getenv("FORCE") == "1"
    estado_f = json.loads(ESTADO.read_text()) if ESTADO.exists() else {}
    enviados_hoy = estado_f.get("enviados", {}).get(str(hoy), [])
    horarios = sorted(cfg.get("horarios", [cfg.get("minuto_inicio", 570)]))
    minutos = ahora.hour * 60 + ahora.minute
    franja = max([h for h in horarios if h <= minutos], default=None)
    if not (prueba or forzar):
        if franja is None:
            print(f"Aún no toca (próxima franja: {horarios[0]//60:02d}:{horarios[0]%60:02d}). Saliendo.")
            return
        if franja in enviados_hoy:
            print(f"Ya se envió el informe de la franja {franja//60:02d}:{franja%60:02d} de hoy. Saliendo.")
            return
    try:
        from navegador import Navegador
    except ImportError:
        print("Falta Playwright. Ejecuta:  pip install playwright   y luego:  playwright install chromium")
        return
    DATA.mkdir(exist_ok=True)
    imp, tiendas = cfg["impuestos"], cfg["tiendas"]
    hist_previa = cargar_hist()
    nuevas, resultados, alts, imagenes = [], [], [], {}
    cob, muertas = defaultdict(list), {}

    print("Agente perfumes v3 (navegador + patrones de búsqueda)", flush=True)
    with Navegador(visible="--visible" in sys.argv, carpeta_debug=DATA / "debug") as nav:
        for p in cfg["perfumes"] + cfg.get("alternativas", []):
            es_alt = "similar_a" in p
            print(("Alternativa: " if es_alt else "Perfume: ") + p["nombre"], flush=True)
            filas, est = buscar(p, tiendas, imp, hoy, nav, muertas)
            for tid, e in est.items():
                cob[tid].append(e)
            buenas = [tiendas[t]["nombre"] for t, e in est.items() if e == "ok"]
            print(f"   precios en {len(buenas)} tienda(s): {', '.join(buenas) or 'ninguna'}", flush=True)
            nuevas += [(p["id"], r) for r in filas]
            if not filas:
                continue
            if es_alt:
                mejor = min(filas, key=lambda r: r["total"])
                if mejor["total"] <= cfg["presupuesto_alternativas"]:
                    alts.append((p, mejor))
                continue
            serie = serie_diaria(hist_previa, p["id"], hoy)
            serie = [(d, v) for d, v in serie if d != str(hoy)] + [(str(hoy), min(r["total"] for r in filas))]
            res = analizar(serie, filas, cfg)
            imagenes[p["id"]] = grafica(p, serie, res)
            resultados.append((p, res, filas))

    cobertura = resumen_cobertura(cob, tiendas)
    print("\nCOBERTURA POR TIENDA")
    for nombre, ok, total, motivo in cobertura:
        print(f"  {nombre:<20} {ok}/{total}  {motivo}")
    guardar_hist([r for _, r in nuevas], {i for i, _ in nuevas}, hoy)
    html = informe(resultados, alts, cobertura, hoy)
    (DATA / "ultimo_informe.html").write_text(html, encoding="utf-8")
    if prueba:
        print("Modo prueba: informe en data/ultimo_informe.html (nada enviado).")
        return
    try:
        enviar_email(html, imagenes, hoy)
    except Exception as e:
        print(f"⚠ Email falló: {e}")
    try:
        enviar_whatsapp(resultados)
    except Exception as e:
        print(f"⚠ WhatsApp falló: {e}")
    if franja is not None:
        estado_f.setdefault("enviados", {}).setdefault(str(hoy), [])
        if franja not in estado_f["enviados"][str(hoy)]:
            estado_f["enviados"][str(hoy)].append(franja)
        estado_f["enviados"] = {d: v for d, v in estado_f["enviados"].items()
                                 if d >= str(hoy - dt.timedelta(days=7))}  # limpia días viejos
    ESTADO.write_text(json.dumps(estado_f))


if __name__ == "__main__":
    main()
