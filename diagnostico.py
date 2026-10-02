#!/usr/bin/env python3
"""
Diagnóstico: muestra, tienda por tienda, por qué el agente no encuentra un perfume.

Uso:
  python diagnostico.py daarej            # prueba ese perfume (id de config.yaml) en todas las tiendas
  python diagnostico.py daarej notino     # solo en una tienda
  python diagnostico.py URL_DE_PRODUCTO   # prueba si se puede leer el precio de esa página concreta
  python diagnostico.py resumen daarej    # UNA línea por tienda: cuáles funcionan y cuáles no
"""
import sys
from urllib.parse import quote_plus

import requests
import yaml

import agente as a

cfg = yaml.safe_load((a.ROOT / "config.yaml").read_text(encoding="utf-8"))


def pedir(url):
    try:
        r = requests.get(url, headers=a.HEADERS, timeout=25)
        return r.status_code, r.text
    except requests.RequestException as e:
        return None, str(e)


def probar_url(url, perfume=None):
    st, html = pedir(url)
    print(f"   HTTP {st} · {len(html)} caracteres")
    if st != 200:
        print("   → La tienda no deja entrar (bloqueo, error o URL mal). Prueba otra tienda o fija URLs a mano.")
        return
    prod = a.leer_producto(html)
    if not prod:
        print("   → Página cargada, pero NO se puede leer el precio (no hay datos estructurados).")
        return
    print(f"   Título: {prod[0][:90]}")
    print(f"   Precio leído: {prod[1]} €")
    if perfume:
        print("   ¿Coincide con el perfume?:", "SÍ" if a.coincide(prod[0], perfume) else "NO (filtro de nombre/ml/concentración)")


def resumen(pid):
    todos = cfg["perfumes"] + cfg.get("alternativas", [])
    p = next((x for x in todos if x["id"] == pid), None)
    if not p:
        print("No existe ese id. Ids disponibles:", ", ".join(x["id"] for x in todos))
        return
    print(f"Resumen para {p['marca']} {p['nombre']} {p['ml']} ml\n")
    for tid, t in cfg["tiendas"].items():
        if not t.get("search"):
            print(f"{t['nombre']:<20} sin búsqueda automática (solo URL fijada)")
            continue
        st, html = pedir(t["search"].format(q=quote_plus(f"{p['marca']} {p['nombre']}")))
        if st != 200:
            print(f"{t['nombre']:<20} NO FUNCIONA · HTTP {st} (bloqueo o URL de búsqueda incorrecta)")
            continue
        cands = a.candidatos(html, t["search"], p)
        if not cands:
            print(f"{t['nombre']:<20} sin resultados legibles (HTTP 200, {len(html)//1000} KB)")
            continue
        ok = None
        for u in cands:
            s2, h2 = pedir(u)
            prod = a.leer_producto(h2) if s2 == 200 else None
            if prod and a.coincide(prod[0], p):
                ok = (prod[1], u)
                break
        if ok:
            print(f"{t['nombre']:<20} FUNCIONA · {ok[0]} € · {ok[1]}")
        else:
            print(f"{t['nombre']:<20} enlaces encontrados ({len(cands)}) pero sin precio válido")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "resumen":
        resumen(sys.argv[2])
        return
    if len(sys.argv) < 2:
        print(__doc__)
        return
    arg = sys.argv[1].strip().strip('"').strip()
    if arg.lower().startswith("http"):
        probar_url(arg.split("&gclid")[0])
        return
    todos = cfg["perfumes"] + cfg.get("alternativas", [])
    p = next((x for x in todos if x["id"] == arg), None)
    if not p:
        print("No existe ese id. Ids disponibles:", ", ".join(x["id"] for x in todos))
        return
    solo = sys.argv[2] if len(sys.argv) > 2 else None
    for tid, t in cfg["tiendas"].items():
        if solo and tid != solo:
            continue
        print(f"\n=== {t['nombre']} ===")
        url = t["search"].format(q=quote_plus(f"{p['marca']} {p['nombre']}"))
        print("   Búsqueda:", url)
        st, html = pedir(url)
        print(f"   HTTP {st} · {len(html)} caracteres")
        if st != 200:
            print("   → Bloqueada o URL de búsqueda incorrecta.")
            continue
        cands = a.candidatos(html, t["search"], p)
        print(f"   Enlaces candidatos encontrados: {len(cands)}")
        if not cands:
            print("   → La página no muestra el producto en el HTML (carga con JavaScript o el nombre no aparece).")
            continue
        for u in cands:
            print("  -", u)
            probar_url(u, p)


if __name__ == "__main__":
    main()
