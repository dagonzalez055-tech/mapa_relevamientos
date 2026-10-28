"""Une las planillas de EDESA (padrón de CT y tabla de distribuidores) con la red del mapa.

Uso:
    python preparar_distribuidores.py "Padrón de CTs.xlsx" "Tabla Distribuidores.xlsx"

Los archivos se reconocen por sus columnas, así que el orden no importa. Se puede pasar más de un
padrón (por ejemplo uno por distrito): se juntan.

Deja en datos/:
    distribuidores.csv      un renglón por distribuidor: nombre, origen (E.T. y barra), tensión, CT, km, localidades
    ct_distribuidor.csv     cada CT con su distribuidor, localidad y distrito
    dist_tramos.npz         tramos de la red que alimenta cada distribuidor
y agrega a datos/subestaciones.csv los CT del padrón que no estaban (ids que empiezan con «P-»).

Cómo se saca la línea de cada distribuidor: la red no trae el dato de qué distribuidor es cada tramo.
Se ubican sus CT sobre la red (a menos de 150 m) y se une la cabecera (si se reconoce la estación) con todos
sus CT por el camino más corto. Es una aproximación: en mallas o cruces puede tomar un tramo vecino.
Si se corre de nuevo preparar_datos.py, hay que volver a correr este script.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra

import logica as lg
import rutas

DATOS = lg.DATOS
DIST_MAX_CT_M = 150
DIST_MAX_ESTACION_M = 300
ZONAS_DISTRITO = {}            # se completa con el punto más cercano ya existente


def n2(x) -> str:
    x = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", x)


def leer_planillas(rutas_xlsx):
    padrones, tablas = [], []
    for r in rutas_xlsx:
        crudo = pd.read_excel(r, header=None)
        for i in range(min(8, len(crudo))):
            fila = [str(c).strip() for c in crudo.iloc[i].tolist()]
            if "CT MT/BT" in fila:
                padrones.append(pd.read_excel(r, header=i))
                break
            if "Cod_padre" in fila and "ID Distribuidor" in fila:
                tablas.append(pd.read_excel(r, header=i))
                break
        else:
            print(f"  (no reconocí {Path(r).name}: no es el padrón de CT ni la tabla de distribuidores; la salto)")
    if not padrones or not tablas:
        raise SystemExit("Hacen falta el padrón de CT (columna «CT MT/BT») y la tabla de distribuidores (columna «Cod_padre»).")
    return pd.concat(padrones, ignore_index=True), pd.concat(tablas, ignore_index=True)


def main(rutas_xlsx):
    P, T = leer_planillas(rutas_xlsx)
    P = P.dropna(subset=["CT MT/BT"]).copy()
    P["CT"] = P["CT MT/BT"].astype(str).str.strip()
    P = P.drop_duplicates("CT")
    P["ID"] = P["ID Distribuidor"].astype(str).str.strip().where(P["ID Distribuidor"].notna(), "")
    T = T.dropna(subset=["ID Distribuidor"]).copy()
    T["ID"] = T["ID Distribuidor"].astype(str).str.strip()
    T = T.drop_duplicates("ID")
    ll = P["Latitud, Longitud"].astype(str).str.split(",", expand=True)
    P["lat"] = pd.to_numeric(ll[0], errors="coerce")
    P["lon"] = pd.to_numeric(ll[1], errors="coerce") if ll.shape[1] > 1 else np.nan
    fuera = ~(P["lon"].between(*lg.LIMITES[:2]) & P["lat"].between(*lg.LIMITES[2:]))
    P.loc[fuera, ["lat", "lon"]] = np.nan

    red = lg.cargar_red()
    grafo = rutas.construir_grafo(red)

    # ---- CT nuevos en el mapa
    sub = lg.cargar_subestaciones()
    sub = sub[~sub["id"].astype(str).str.startswith("P-")].copy()
    existentes = set(sub["nombre"].astype(str))
    nuevos = P[~P["CT"].isin(existentes) & P["lat"].notna()].copy()
    if len(nuevos):
        from scipy.spatial import cKDTree
        arbol = cKDTree(np.column_stack((sub["lon"], sub["lat"])))
        _, j = arbol.query(np.column_stack((nuevos["lon"], nuevos["lat"])))
        zonas = sub["zona"].to_numpy()[j]
        add = pd.DataFrame({"id": "P-" + nuevos["CT"], "nombre": nuevos["CT"], "zona": zonas, "tipo": "CT",
                            "lon": nuevos["lon"].round(6), "lat": nuevos["lat"].round(6)})
        sub = pd.concat([sub, add[sub.columns]], ignore_index=True)
    sub.to_csv(DATOS / "subestaciones.csv", index=False, encoding="utf-8-sig")
    print(f"CT del padrón: {len(P)} · con coordenadas: {int(P['lat'].notna().sum())} · "
          f"nuevos agregados al mapa: {len(nuevos)} · sin coordenadas (no se pueden dibujar): {int(P['lat'].isna().sum())}")

    # ---- posición de cada CT: la del padrón, o la del mapa si el padrón no la trae
    pos = sub.set_index("nombre")[["lon", "lat"]]
    P["clon"] = P["lon"].fillna(P["CT"].map(pos["lon"]))
    P["clat"] = P["lat"].fillna(P["CT"].map(pos["lat"]))

    # ---- estación de cabecera de cada distribuidor
    estaciones = sub[sub["tipo"] != "CT"]
    nombres = pos.index
    cabecera = {}
    for _, r in T.drop_duplicates("Cod_padre").iterrows():
        cp = str(r["Cod_padre"]).strip()
        punto = None
        for cand in (cp[:4], "CD" + cp[:2], "CG" + cp[:2], "CR" + cp[:2], "ET" + cp[:2]):
            if cand in nombres:
                punto = cand
                break
        if punto is None:
            clave = n2(r["E. T."])
            hits = [n for n in estaciones["nombre"] if clave and clave in n2(n)]
            punto = hits[0] if hits else None
        cabecera[cp] = punto

    # ---- tramos de cada distribuidor
    g, seg, comp = grafo["g"], grafo["seg"], grafo["comp"]
    cod_padre = T.set_index("ID")["Cod_padre"].astype(str).str.strip()
    ids = sorted(set(P["ID"]) - {"", "nan"})
    todos_segs, offs = [], [0]
    filas = []
    for did in ids:
        grp = P[(P["ID"] == did)]
        term, ubicados = [], 0
        for lo, la in zip(grp["clon"], grp["clat"]):
            if pd.isna(lo):
                continue
            ubicados += 1
            v, d = rutas.nodo_cercano(grafo, lo, la)
            if d <= DIST_MAX_CT_M:
                term.append(v)
        raiz, nom_cab = None, None
        cp = cod_padre.get(did)
        if cp is not None and cabecera.get(cp):
            nom_cab = cabecera[cp]
            r = pos.loc[nom_cab]
            v, d = rutas.nodo_cercano(grafo, float(r["lon"]), float(r["lat"]))
            if d <= DIST_MAX_ESTACION_M:
                raiz = v
        terminales = np.unique(term + ([raiz] if raiz is not None else []))
        segs = []
        if len(terminales):
            comps = comp[terminales]
            for c in np.unique(comps):
                tt = terminales[comps == c]
                r0 = raiz if (raiz is not None and comp[raiz] == c) else int(tt[0])
                _, pred = dijkstra(g, directed=False, indices=r0, return_predecessors=True)
                vistos = set()
                for f in tt:
                    x = int(f)
                    while x != r0 and x not in vistos:
                        vistos.add(x)
                        p = int(pred[x])
                        if p < 0:
                            break
                        if seg[x] == seg[p] and abs(x - p) == 1:
                            segs.append(int(seg[x]))
                        x = p
        segs = np.unique(np.array(segs, dtype=np.int32))
        todos_segs.append(segs)
        offs.append(offs[-1] + len(segs))
        km = float(red["largo_m"][segs].sum()) / 1000 if len(segs) else 0.0
        trow = T[T["ID"] == did]
        info = trow.iloc[0] if len(trow) else None
        nombre = str(info["Distribuidor"]).strip() if info is not None else str(grp["Distribuidor"].iloc[0]).strip()
        locs = grp["Localidad"].dropna().astype(str).str.strip().value_counts()
        dens = grp["Distrito"].dropna().astype(str).str.strip().value_counts()
        filas.append({
            "id": did, "nombre": nombre,
            "distrito": str(info["Distrito"]).strip() if info is not None and pd.notna(info["Distrito"]) else (dens.index[0] if len(dens) else ""),
            "cod_padre": cp or "",
            "tipo_origen": str(info["Tipo"]).strip() if info is not None else "",
            "estacion": str(info["E. T."]).strip() if info is not None else "",
            "barra": str(info["Barra"]).strip() if info is not None and pd.notna(info["Barra"]) else "",
            "tension": float(str(info["Tension"]).replace(",", ".")) if info is not None and pd.notna(info["Tension"]) else np.nan,
            "n_ct": len(grp), "n_ct_ubicados": ubicados, "n_ct_en_red": len(term),
            "km": round(km, 3), "tramos": len(segs), "cabecera_ubicada": "sí" if raiz is not None else "no",
            "localidades": "; ".join(f"{k} ({v})" for k, v in locs.items()),
        })
    dist = pd.DataFrame(filas)
    # distribuidores de la tabla que no tienen CT en el padrón: se listan igual
    sin_ct = T[~T["ID"].isin(dist["id"])]
    extra = pd.DataFrame({"id": sin_ct["ID"], "nombre": sin_ct["Distribuidor"].astype(str).str.strip(),
                          "distrito": sin_ct["Distrito"].astype(str).str.strip(), "cod_padre": sin_ct["Cod_padre"].astype(str).str.strip(),
                          "tipo_origen": sin_ct["Tipo"].astype(str).str.strip(), "estacion": sin_ct["E. T."].astype(str).str.strip(),
                          "barra": sin_ct["Barra"].astype(str).str.strip(),
                          "tension": pd.to_numeric(sin_ct["Tension"].astype(str).str.replace(",", "."), errors="coerce"),
                          "n_ct": 0, "n_ct_ubicados": 0, "n_ct_en_red": 0, "km": 0.0, "tramos": 0, "cabecera_ubicada": "no",
                          "localidades": ""})
    for e in extra["id"]:
        todos_segs.append(np.empty(0, np.int32))
        offs.append(offs[-1])
    dist = pd.concat([dist, extra], ignore_index=True)
    dist.to_csv(DATOS / "distribuidores.csv", index=False, encoding="utf-8-sig")

    ct = P[P["ID"].isin(set(P["ID"]) - {"", "nan"})][["CT", "ID", "Localidad", "Distrito", "clat", "clon"]].copy()
    ct.columns = ["ct", "id_distribuidor", "localidad", "distrito", "lat", "lon"]
    ct["localidad"] = ct["localidad"].fillna("").astype(str).str.strip()
    ct["nuevo"] = ct["ct"].isin(set(nuevos["CT"]))
    ct.to_csv(DATOS / "ct_distribuidor.csv", index=False, encoding="utf-8-sig")

    np.savez_compressed(DATOS / "dist_tramos.npz", ids=np.array(dist["id"].astype(str).tolist(), dtype="U32"), off=np.array(offs, dtype=np.int64),
                        segs=np.concatenate(todos_segs) if todos_segs else np.empty(0, np.int32))
    con_km = dist[dist["km"] > 0]
    todo = np.unique(np.concatenate(todos_segs)) if todos_segs else np.empty(0)
    print(f"Distribuidores: {len(dist)} ({len(con_km)} con línea en el mapa) · red cubierta: "
          f"{float(red['largo_m'][todo].sum()) / 1000:,.0f} km de {float(red['largo_m'].sum()) / 1000:,.0f} km")
    print(f"Cabecera ubicada: {int((dist['cabecera_ubicada'] == 'sí').sum())} de {len(dist)} distribuidores")
    print("Listo: datos/distribuidores.csv, datos/ct_distribuidor.csv, datos/dist_tramos.npz y datos/subestaciones.csv")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    main(sys.argv[1:])
