"""Recorridos sobre la red de media tensión.

Arma un grafo con los vértices de todos los tramos:
  * dos vértices consecutivos de un mismo tramo se unen con su distancia real;
  * vértices de tramos distintos que quedan a menos de `TOL_UNION_M` se unen con un
    costo penalizado (así el camino prefiere seguir la línea y solo salta entre
    líneas donde hay empalmes).
Con eso se puede buscar el camino más corto entre dos puntos (CT, subestaciones o
coordenadas) y saber qué tramos recorre.

Usa scipy, que se instala con requirements.txt.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

TOL_UNION_M = 12.0      # distancia máxima para considerar que dos líneas empalman
PENALIZACION = 3.0      # un salto entre líneas "cuesta" 3 veces su distancia
DIST_MAX_PUNTO_M = 150  # si un punto está más lejos que esto de la red, se avisa
_LAT0, _LON0 = -24.5, -65.0


def _proyectar(lon, lat):
    k = 111320.0
    return np.column_stack(((np.asarray(lon) - _LON0) * k * math.cos(math.radians(24.2)),
                            (np.asarray(lat) - _LAT0) * k))


def construir_grafo(red: dict, tol_m: float = TOL_UNION_M) -> dict:
    off = red["off"]
    lon, lat = red["lon"], red["lat"]
    n_seg = len(off) - 1
    n = len(lon)
    seg = np.repeat(np.arange(n_seg, dtype=np.int32), np.diff(off))
    P = _proyectar(lon, lat)

    # aristas dentro de cada tramo (vértice i con el i+1, si son del mismo tramo)
    i = np.flatnonzero(seg[:-1] == seg[1:])
    j = i + 1
    w_int = np.hypot(*(P[j] - P[i]).T)

    # empalmes entre tramos distintos
    arbol = cKDTree(P)
    pares = arbol.query_pairs(tol_m, output_type="ndarray")
    if len(pares):
        pares = pares[seg[pares[:, 0]] != seg[pares[:, 1]]]
    a, b = (pares[:, 0], pares[:, 1]) if len(pares) else (np.empty(0, int), np.empty(0, int))
    w_ext = np.maximum(np.hypot(*(P[b] - P[a]).T), 1.0) * PENALIZACION

    filas = np.concatenate([i, a])
    cols = np.concatenate([j, b])
    pesos = np.concatenate([np.maximum(w_int, 0.01), w_ext])
    g = coo_matrix((pesos, (filas, cols)), shape=(n, n)).tocsr()
    g = g.maximum(g.T)                      # sin dirección
    n_comp, comp = connected_components(g, directed=False)
    return {"g": g, "P": P, "seg": seg, "arbol": arbol, "comp": comp, "n_comp": n_comp,
            "n_seg": n_seg}


def nodo_cercano(grafo: dict, lon: float, lat: float):
    """(vértice más cercano, distancia en metros)."""
    p = _proyectar([lon], [lat])[0]
    d, k = grafo["arbol"].query(p)
    return int(k), float(d)


def camino(grafo: dict, puntos: list[tuple[float, float]]) -> dict:
    """Camino más corto que pasa por los puntos (lon, lat) en orden.

    Devuelve un dict con:
      ok        True si se encontró camino en todos los tramos
      mensaje   explicación si no, o aviso si algún punto quedó lejos de la red
      segmentos índices de los tramos recorridos (sin repetir)
      largo_m   longitud recorrida sobre la línea
      linea     lista de (lon, lat) del recorrido, para dibujarlo
      lejos     lista de (posición, distancia) de los puntos alejados de la red
    """
    res = {"ok": False, "mensaje": "", "segmentos": np.empty(0, np.int32), "largo_m": 0.0,
           "linea": [], "lejos": []}
    if len(puntos) < 2:
        res["mensaje"] = "Hacen falta al menos dos puntos."
        return res
    nodos = []
    for k, (lo, la) in enumerate(puntos):
        v, d = nodo_cercano(grafo, lo, la)
        nodos.append(v)
        if d > DIST_MAX_PUNTO_M:
            res["lejos"].append((k, d))
    if res["lejos"]:
        txt = ", ".join(f"punto {k + 1} a {d:,.0f} m".replace(",", ".") for k, d in res["lejos"])
        res["mensaje"] = f"Hay puntos lejos de la red ({txt}). Revisá las coordenadas o elegí otro CT."
        return res

    g, seg, P = grafo["g"], grafo["seg"], grafo["P"]
    camino_v = [nodos[0]]
    for ini, fin in zip(nodos[:-1], nodos[1:]):
        if ini == fin:
            continue
        if grafo["comp"][ini] != grafo["comp"][fin]:
            res["mensaje"] = ("No hay camino entre esos puntos: están en partes de la red que no se tocan "
                              "(a menos de %g m). Probá agregando un punto intermedio o usá otro modo de carga." % TOL_UNION_M)
            return res
        dist, pred = dijkstra(g, directed=False, indices=ini, return_predecessors=True)
        tramo = [fin]
        while tramo[-1] != ini:
            tramo.append(int(pred[tramo[-1]]))
        camino_v.extend(reversed(tramo[:-1]))
    v = np.array(camino_v)
    if len(v) < 2:
        res["mensaje"] = "Los puntos coinciden con el mismo lugar de la red."
        return res
    u, w = v[:-1], v[1:]
    mismo = (seg[u] == seg[w]) & (np.abs(u - w) == 1)       # arista real de la línea
    segs = np.unique(seg[u[mismo]])
    res["largo_m"] = float(np.hypot(*(P[w[mismo]] - P[u[mismo]]).T).sum())
    res["segmentos"] = segs.astype(np.int32)
    res["linea"] = [(float(x), float(y)) for x, y in zip(_inversa(P[v])[0], _inversa(P[v])[1])]
    res["ok"] = bool(len(segs))
    if not res["ok"]:
        res["mensaje"] = "El recorrido no sigue ninguna línea."
    return res


def _inversa(P):
    k = 111320.0
    return (P[:, 0] / (k * math.cos(math.radians(24.2))) + _LON0, P[:, 1] / k + _LAT0)
