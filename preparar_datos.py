"""Prepara los datos de la extracción regulatoria de EDESA para el mapa.

Uso:
    python preparar_datos.py "C:\\ruta\\a\\la\\carpeta\\INSTALACIONES_2025"

Busca cables.shp y subestacion.shp dentro de esa carpeta (en cualquier
subcarpeta), se queda con la red de media tensión (13,2, 33 y 66 kV) y con
todas las subestaciones y CT, y deja en la carpeta datos/:
    red_mt.npz           red de media tensión en formato compacto
    subestaciones.csv    subestaciones y CT

Solo hace falta correrlo cuando EDESA entregue una extracción nueva.
"""
import csv
import math
import re
import sys
from pathlib import Path

import numpy as np

from lector_shp import leer_dbf, leer_shp

SALIDA = Path(__file__).resolve().parent / "datos"
TENSIONES = (13.2, 33.0, 66.0)
ZONAS = {
    "Salta": "Salta",
    "Oran,San Ramon de la Nueva": "Orán – San Ramón",
    "Joaquin V.Gonzalez": "J. V. González",
    "Tartagal": "Tartagal",
    "Metan": "Metán",
    "Cafayate": "Cafayate",
    "Guemes": "Güemes",
    "San Antonio de los Cobres": "San Antonio de los Cobres",
}
PRINCIPAL = re.compile(r"^(ET|EG|CG|CD|Rebaje|C\. Rebaje|Central|Pto)\b", re.I)


def buscar(carpeta, nombre):
    for p in Path(carpeta).rglob("*"):
        if p.is_file() and p.name.lower() == nombre.lower():
            return p
    raise SystemExit(f"No encontré {nombre} dentro de {carpeta}")


def haversine_m(a, b):
    r = math.pi / 180
    dl = (b[1] - a[1]) * r
    dn = (b[0] - a[0]) * r
    x = math.sin(dl / 2) ** 2 + math.cos(a[1] * r) * math.cos(b[1] * r) * math.sin(dn / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(x))


def indice(campos, nombre):
    for i, c in enumerate(campos):
        if c[0].lower() == nombre:
            return i
    raise SystemExit(f"Falta el campo '{nombre}' en el archivo de cables")


def red_media_tension(carpeta):
    shp = buscar(carpeta, "cables.shp")
    campos, regs = leer_dbf(shp.with_suffix(".dbf"))
    geoms = leer_shp(shp)
    i_ten, i_area, i_id = indice(campos, "tension"), indice(campos, "area_name"), indice(campos, "id")
    nombres = []
    off, lon, lat = [0], [], []
    tension, area, idc, cx, cy, largo = [], [], [], [], [], []
    leidos = 0
    for (borrado, v), g in zip(regs, geoms):
        leidos += 1
        if borrado or not g or v[i_ten] not in TENSIONES:
            continue
        pts = g[0]
        zona = ZONAS.get(v[i_area], v[i_area])
        if zona not in nombres:
            nombres.append(zona)
        for x, y in pts:
            lon.append(x)
            lat.append(y)
        off.append(len(lon))
        tension.append(v[i_ten])
        area.append(nombres.index(zona))
        idc.append(int(v[i_id]) if v[i_id] is not None else 0)
        cx.append(sum(p[0] for p in pts) / len(pts))
        cy.append(sum(p[1] for p in pts) / len(pts))
        largo.append(sum(haversine_m(pts[i - 1], pts[i]) for i in range(1, len(pts))))
    print(f"Cables leídos: {leidos}. Tramos de media tensión: {len(tension)}")
    SALIDA.mkdir(exist_ok=True)
    np.savez_compressed(
        SALIDA / "red_mt.npz",
        off=np.array(off, dtype=np.int32),
        lon=np.array(lon, dtype=np.float64),
        lat=np.array(lat, dtype=np.float64),
        tension=np.array(tension, dtype=np.float32),
        area=np.array(area, dtype=np.int16),
        areas=np.array(nombres),
        idc=np.array(idc, dtype=np.int64),
        cx=np.array(cx, dtype=np.float64),
        cy=np.array(cy, dtype=np.float64),
        largo_m=np.array(largo, dtype=np.float32),
    )
    for t in TENSIONES:
        km = sum(l for l, tt in zip(largo, tension) if tt == t) / 1000
        print(f"  {t:g} kV: {km:,.1f} km")


def subestaciones(carpeta):
    shp = buscar(carpeta, "subestacion.shp")
    campos, regs = leer_dbf(shp.with_suffix(".dbf"))
    geoms = leer_shp(shp)
    i_id, i_nom, i_area = indice(campos, "id"), indice(campos, "nombre"), indice(campos, "area_name")
    filas = []
    for (borrado, v), g in zip(regs, geoms):
        if borrado or not g:
            continue
        lon, lat = g[0][0]
        nombre = re.sub(r"\s+", " ", v[i_nom] or "").strip()
        tipo = "Estación o central" if PRINCIPAL.match(nombre) else "CT"
        filas.append([int(v[i_id]) if v[i_id] is not None else "", nombre,
                      ZONAS.get(v[i_area], v[i_area]), tipo, round(lon, 6), round(lat, 6)])
    with open(SALIDA / "subestaciones.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "nombre", "zona", "tipo", "lon", "lat"])
        w.writerows(filas)
    n_est = sum(1 for r in filas if r[3] != "CT")
    print(f"Subestaciones y CT: {len(filas)} ({n_est} estaciones o centrales, {len(filas) - n_est} CT)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    carpeta = sys.argv[1]
    red_media_tension(carpeta)
    subestaciones(carpeta)
    print("Listo. Los datos quedaron en", SALIDA)
