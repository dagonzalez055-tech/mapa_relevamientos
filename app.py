"""Mapa de relevamientos de redes eléctricas (Salta).

Para abrirlo:   streamlit run app.py

Código de colores:  azul = relevado (adecuaciones pendientes de EDESA)
                    verde = adecuaciones ejecutadas por EDESA
                    gris = red sin relevar
"""
from __future__ import annotations

import base64
import contextlib
import html
import json
import time
from datetime import date

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

import acceso
import almacen
import informes
import logica as lg
import panel_admin
import reportes
import rutas

st.set_page_config(page_title="Mapa de relevamientos", page_icon="🗺️", layout="wide")
USUARIO = acceso.iniciar(lg)              # pide el inicio de sesión (si está configurado) y conecta la nube
PUEDE_EDITAR = USUARIO.puede_editar
ES_ADMIN = USUARIO.es_admin

ALTO_MAPA = 700
ALTO_PREVIA = 430

PALETAS = {
    "claro": {
        "red": {13.2: [160, 171, 182], 33.0: [110, 124, 138], 66.0: [52, 64, 78]},
        "relevado": [47, 111, 237],
        "ejecutado": [24, 150, 84],
        "obs": [228, 87, 46],
        "seleccion": [255, 196, 0],
        "dist": [[136, 68, 204], [244, 196, 0], [255, 112, 0], [214, 40, 40], [140, 90, 40], [214, 0, 184]],
        "estacion": [31, 41, 51],
        "ct": [82, 96, 109],
        "borde": [255, 255, 255],
        "texto": [24, 33, 43],
        "fondo": [255, 255, 255, 215],
    },
    "oscuro": {
        "red": {13.2: [96, 110, 124], 33.0: [140, 154, 168], 66.0: [200, 210, 220]},
        "relevado": [110, 165, 255],
        "ejecutado": [63, 208, 138],
        "obs": [255, 138, 90],
        "seleccion": [255, 214, 64],
        "dist": [[178, 130, 255], [255, 224, 51], [255, 140, 38], [255, 90, 90], [190, 140, 90], [255, 79, 232]],
        "estacion": [236, 242, 247],
        "ct": [150, 165, 178],
        "borde": [20, 28, 35],
        "texto": [240, 244, 248],
        "fondo": [20, 28, 35, 215],
    },
}
ANCHO_RED = {13.2: 1.0, 33.0: 1.8, 66.0: 2.6}
# Satélite: imágenes de Esri (World Imagery), sin clave. se pasa como texto (ver crear_deck).
ESTILO_SATELITE = {"version": 8,
                   "sources": {"satelite": {"type": "raster", "tileSize": 256, "maxzoom": 18,
                                            "tiles": ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
                                            "attribution": "Esri, Maxar, Earthstar Geographics"}},
                   "layers": [{"id": "satelite", "type": "raster", "source": "satelite"}]}
ESTILOS_BASE = {"Claro": "light", "Calles": "road", "Satélite": "dark"}
# Streamlit solo acepta el estilo como texto: se pasa el estilo de Esri como "data URI" (texto con el JSON).
ESTILO_SATELITE_URI = "data:application/json;base64," + base64.b64encode(
    json.dumps(ESTILO_SATELITE, separators=(",", ":")).encode()).decode()


def crear_deck(base, **args):
    """Crea el mapa de pydeck; si se eligió Satélite, usa el estilo de imágenes de Esri."""
    estilo = ESTILO_SATELITE_URI if base == "Satélite" else ESTILOS_BASE[base]
    try:                                # radio de selección: no todas las versiones de pydeck lo traen
        return pdk.Deck(**args, map_style=estilo, picking_radius=4)
    except TypeError:
        return pdk.Deck(**args, map_style=estilo)


MODOS = {
    "informe": "Informe PDF (zona alrededor de las observaciones)",
    "ct": "Manual: de un CT o subestación a otro",
    "coord": "Por coordenadas",
}
NOMBRE_MODO = {"informe": "informe PDF", "ct": "CT a CT", "coord": "coordenadas"}


# ---------------------------------------------------------------- utilidades
def num(x: float, dec: int = 1) -> str:
    """Número con formato argentino (1.234,5)."""
    s = f"{x:,.{dec}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def fmt_fecha(texto: str) -> str:
    try:
        a, m, d = str(texto).split("-")
        return f"{int(d):02d}/{int(m):02d}/{a}"
    except ValueError:
        return str(texto)


def mostrar(fn, *args, **kwargs):
    """Llama a un elemento de Streamlit con ancho completo, sea cual sea la versión instalada."""
    error = None
    for extra in ({"width": "stretch"}, {"use_container_width": True}, {}):
        try:
            return fn(*args, **extra, **kwargs)
        except TypeError as e:
            error = e
    raise error


def _acercar():
    """El usuario eligió un expediente a mano: el mapa se acerca y lo resalta."""
    st.session_state["zoom_exp"] = True
    _limpiar_busqueda()


def _ver_provincia():
    st.session_state["zoom_exp"] = False
    _limpiar_busqueda()


CLAVES_DIST = ("busca_dist", "busca_dist2", "busca_dist3")
NOMBRES_COLOR = ("morado", "amarillo", "anaranjado")
EMOJI_COLOR = ("🟣", "🟡", "🟠", "🔴", "🟤", "🟪")
MAX_DIST_LOCALIDAD = 6          # cuántos distribuidores de una localidad se resaltan a la vez, cada uno con su color
RADIO_LOCALIDAD_M = 800.0       # «solo dentro de la localidad»: tramos a menos de esta distancia de algún CT de la localidad


def _limpiar_busqueda():
    st.session_state["foco"] = None
    st.session_state["busca_loc"] = SIN_BUSCAR
    st.session_state["busca_pt"] = SIN_BUSCAR
    for k_ in CLAVES_DIST:
        st.session_state[k_] = SIN_BUSCAR


SIN_BUSCAR = "—"
ZOOM_LOCALIDAD, ZOOM_PUNTO = 13.0, 17.0


def _buscar_localidad():
    """Eligió una localidad: el mapa se acerca a ella y se resaltan los distribuidores que la alimentan."""
    nombre = st.session_state.get("busca_loc", SIN_BUSCAR)
    st.session_state["busca_pt"] = SIN_BUSCAR
    for k_ in CLAVES_DIST:
        st.session_state[k_] = SIN_BUSCAR
    st.session_state["foco"] = None
    if nombre != SIN_BUSCAR:
        fila_ = lista_localidades().query("nombre == @nombre")
        if len(fila_):
            f_ = fila_.iloc[0]
            if pd.notna(f_["x0"]):
                lat_, lon_, z_ = lg.vista_para(f_["x0"] - 0.01, f_["x1"] + 0.01, f_["y0"] - 0.01, f_["y1"] + 0.01, alto_px=ALTO_MAPA)
                z_ = float(min(max(z_, 9.5), 15.0))
            else:
                lat_, lon_, z_ = float(f_["lat"]), float(f_["lon"]), ZOOM_LOCALIDAD
            st.session_state["foco"] = {"lon": float(lon_), "lat": float(lat_), "zoom": z_, "nombre": nombre, "punto": False}
            st.session_state["zoom_exp"] = False


def _buscar_punto():
    """Eligió un CT o subestación: el mapa se acerca y lo marca."""
    etiqueta = st.session_state.get("busca_pt", SIN_BUSCAR)
    st.session_state["busca_loc"] = SIN_BUSCAR
    for k_ in CLAVES_DIST:
        st.session_state[k_] = SIN_BUSCAR
    st.session_state["foco"] = None
    if etiqueta != SIN_BUSCAR:
        fila_ = lista_puntos().query("etiqueta == @etiqueta")
        if len(fila_):
            st.session_state["foco"] = {"lon": float(fila_.iloc[0]["lon"]), "lat": float(fila_.iloc[0]["lat"]),
                                        "zoom": ZOOM_PUNTO, "nombre": str(fila_.iloc[0]["nombre"]), "punto": True}
            st.session_state["zoom_exp"] = False


def _buscar_distribuidor():
    """Eligió hasta tres distribuidores: el mapa se acerca al conjunto y cada uno se resalta con su color."""
    st.session_state["busca_loc"] = SIN_BUSCAR
    st.session_state["busca_pt"] = SIN_BUSCAR
    st.session_state["foco"] = None
    d_df_, _, tramos_d_ = datos_distribuidores()
    a_id_ = dict(zip(d_df_["etiqueta"], d_df_["id"]))
    segs_ = [tramos_d_.get(a_id_.get(st.session_state.get(k_, SIN_BUSCAR)), []) for k_ in CLAVES_DIST]
    segs_ = [x for x in segs_ if len(x)]
    if segs_:
        x0_, x1_, y0_, y1_ = lg.caja_segmentos(datos_red()[0], np.concatenate(segs_))
        lat_, lon_, z_ = lg.vista_para(x0_, x1_, y0_, y1_, alto_px=ALTO_MAPA)
        st.session_state["foco"] = {"lon": float(lon_), "lat": float(lat_), "zoom": float(min(z_, 15.0)), "nombre": "distribuidores",
                                    "punto": False}
        st.session_state["zoom_exp"] = False


def color(rgb, alfa: int = 255):
    return [int(c) for c in rgb] + [alfa]


def pintar_mapa(deck, alto: int, clave: str | None = None):
    """Dibuja el mapa. Con `clave`, intenta hacerlo seleccionable con un clic (versiones recientes de Streamlit)."""
    if clave:
        sel = {"on_select": "rerun", "selection_mode": "single-object", "key": clave}
        for extra in ({"width": "stretch", "height": alto}, {"use_container_width": True, "height": alto},
                      {"use_container_width": True}):
            try:
                return st.pydeck_chart(deck, **sel, **extra)
            except TypeError:
                continue
    error = None
    for extra in ({"width": "stretch", "height": alto}, {"use_container_width": True, "height": alto},
                  {"use_container_width": True}):
        try:
            st.pydeck_chart(deck, **extra)
            return None
        except TypeError as e:
            error = e
    raise error


def expediente_clicado(evento):
    """Expediente de la línea (o punto) que se tocó en el mapa; None si no hay clic o la versión no lo soporta."""
    if evento is None:
        return None
    try:
        objetos = evento.selection.objects
    except AttributeError:
        try:
            objetos = evento["selection"]["objects"]
        except (KeyError, TypeError):
            return None
    for lista in (objetos or {}).values():
        for o in lista or []:
            if isinstance(o, dict) and o.get("expediente"):
                return str(o["expediente"])
    return None


def linea_clicada(evento):
    """(título, detalle, (lon, lat)) del objeto del mapa que se tocó (línea, CT o estación), o None.
    Para una línea el punto es el centro del tramo: el mapa no informa el lugar exacto del clic."""
    if evento is None:
        return None
    try:
        objetos = evento.selection.objects
    except AttributeError:
        try:
            objetos = evento["selection"]["objects"]
        except (KeyError, TypeError):
            return None
    for lista in (objetos or {}).values():
        for o in lista or []:
            if not isinstance(o, dict):
                continue
            punto = None
            if o.get("tramo") is not None:
                i_ = int(o["tramo"])
                punto = (float(red["cx"][i_]), float(red["cy"][i_]))
            elif o.get("lon") is not None and o.get("lat") is not None:
                punto = (float(o["lon"]), float(o["lat"]))
            if o.get("tramo") is not None and o.get("titulo") == "Red sin relevar":
                return (f"Distribuidor: {TXT_DIST[i_]}".replace("&amp;", "&"),
                        f"Red de {float(red['tension'][i_]):g} kV sin relevar", punto)
            if o.get("titulo"):
                return html.unescape(str(o["titulo"])), html.unescape(str(o.get("detalle", ""))).replace("<br/>", " · "), punto
    return None


# -------------------------------------------------------------------- datos
@st.cache_resource(show_spinner="Cargando la red de media tensión...")
def datos_red():
    red = lg.cargar_red()
    xy = np.column_stack((red["lon"], red["lat"])).round(5).tolist()
    return red, xy


@st.cache_resource(show_spinner="Armando las conexiones de la red (una sola vez)...")
def datos_grafo():
    return rutas.construir_grafo(datos_red()[0])


@st.cache_data(show_spinner=False)
def datos_subestaciones():
    df = lg.cargar_subestaciones()
    ctd = lg.cargar_ct_distribuidor().drop_duplicates("ct").set_index("ct")
    dist = lg.cargar_distribuidores().drop_duplicates("id").set_index("id")["nombre"]
    df["dist_id"] = df["nombre"].map(ctd["id_distribuidor"]).fillna("")
    df["titulo"] = np.where(df["tipo"] == "CT", "CT " + df["nombre"], df["nombre"]).astype(str)
    df["titulo"] = df["titulo"].map(html.escape)
    extra = np.where(df["dist_id"] != "", " · Distribuidor " + df["dist_id"] + " " + df["dist_id"].map(dist).fillna(""), "")
    df["detalle"] = ("Zona " + df["zona"].astype(str) + " · " + df["tipo"].astype(str) + extra).map(html.escape)
    cols = ["lon", "lat", "titulo", "detalle", "nombre", "dist_id"]
    return (df[df["tipo"] != "CT"][cols].reset_index(drop=True), df[df["tipo"] == "CT"][cols].reset_index(drop=True))


@st.cache_data(show_spinner=False)
def datos_distribuidores():
    """Distribuidores (con etiqueta para el buscador), CT con su distribuidor y tramos de cada distribuidor."""
    d = lg.cargar_distribuidores()
    d = d[(d["n_ct"] > 0) | (d["tramos"] > 0)].copy()
    d["etiqueta"] = d["id"] + " · " + d["nombre"] + " (" + d["distrito"] + ")"
    d = d.sort_values("id", key=lambda c: c.map(lg.norm_nombre)).reset_index(drop=True)
    return d, lg.cargar_ct_distribuidor(), lg.cargar_tramos_distribuidores()


@st.cache_data(show_spinner=False)
def lista_localidades():
    """Localidades para el buscador: las de la lista de la provincia y las que solo aparecen en el padrón de CT."""
    uni = lg.cargar_universo()
    ct = datos_distribuidores()[1]
    ct = ct[(ct["localidad_mapa"] != "") & ct["lat"].notna()]
    g = ct.groupby("localidad_mapa").agg(lon=("lon", "median"), lat=("lat", "median"), n_ct=("ct", "size"),
                                         x0=("lon", lambda v: v.quantile(0.03)), x1=("lon", lambda v: v.quantile(0.97)),
                                         y0=("lat", lambda v: v.quantile(0.03)), y1=("lat", lambda v: v.quantile(0.97)))
    filas = []
    for nombre, r in g.iterrows():
        filas.append({"nombre": nombre, "lon": r["lon"], "lat": r["lat"], "n_ct": int(r["n_ct"]),
                      "x0": r["x0"], "x1": r["x1"], "y0": r["y0"], "y1": r["y1"]})
    hay = {lg.norm_nombre(f["nombre"]) for f in filas}
    for _, u in lg.cargar_localidades().iterrows():          # de la lista, con posición pero sin CT en el padrón
        if lg.norm_nombre(u["nombre"]) not in hay:
            filas.append({"nombre": u["nombre"], "lon": u["lon"], "lat": u["lat"], "n_ct": 0,
                          "x0": np.nan, "x1": np.nan, "y0": np.nan, "y1": np.nan})
    df = pd.DataFrame(filas)
    pos = lg.cargar_localidades().set_index("nombre")        # si la lista tiene posición, esa manda para el centro
    for k, r in df.iterrows():
        if r["nombre"] in pos.index:
            df.loc[k, ["lon", "lat"]] = [pos.at[r["nombre"], "lon"], pos.at[r["nombre"], "lat"]]
    return df.sort_values("nombre", key=lambda c: c.map(lg.norm_nombre)).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def lista_puntos():
    """Subestaciones y CT para elegir en los menús: estaciones primero, después CT por nombre."""
    df = lg.cargar_subestaciones()
    df["orden"] = (df["tipo"] == "CT").astype(int)
    df = df.sort_values(["orden", "zona", "nombre"]).reset_index(drop=True)
    df["etiqueta"] = np.where(df["tipo"] == "CT", "CT " + df["nombre"], df["nombre"]) + " · " + df["zona"].astype(str)
    return df[["etiqueta", "nombre", "tipo", "zona", "lon", "lat"]]


@st.cache_data(show_spinner=False)
def calcular_tramos(expediente, definicion, buffer_m, puntos_obs, mtime_zona, _red, _grafo):
    """Tramos de un relevamiento (cacheado: solo se recalcula si cambia su definición)."""
    obs_df = pd.DataFrame(list(puntos_obs), columns=["lon", "lat"])
    obs_df["expediente"] = expediente
    fila = {"expediente": expediente, "definicion": definicion, "buffer_m": buffer_m}
    return lg.tramos_de(fila, _red, _grafo, obs_df, buffer_defecto=600.0)


def tramos_de_fila(fila, obs_df, margen):
    o = obs_df[obs_df["expediente"] == fila["expediente"]]
    o = o[lg.coordenadas_validas(o)]
    puntos = tuple((round(float(a), 6), round(float(b), 6)) for a, b in zip(o["lon"], o["lat"]))
    zona = lg.ZONAS / f"{fila['expediente']}.geojson"
    mtime = zona.stat().st_mtime if zona.exists() else 0
    buf = float(fila["buffer_m"]) if pd.notna(fila["buffer_m"]) else float(margen)
    return calcular_tramos(fila["expediente"], fila["definicion"], buf, puntos, mtime, red, grafo)


red, XY = datos_red()
OFF = red["off"]
grafo = datos_grafo()
est_df, ct_df = datos_subestaciones()
rels = lg.cargar_relevamientos()
obs = lg.cargar_observaciones()

if st.session_state.get("aviso"):
    st.toast(st.session_state.pop("aviso"))

st.markdown("<style>@media (max-width: 640px) { .block-container { padding: 1rem 0.6rem 3rem; } "
            "h1 { font-size: 1.45rem !important; } }</style>", unsafe_allow_html=True)
if PUEDE_EDITAR and almacen.activo() and (almacen.estado()["error"] or almacen.pendientes()):
    st.error("Los últimos cambios todavía no llegaron a la nube (" + (almacen.estado()["error"] or "pendiente de subir") +
             "). No cierres el programa: se reintenta solo al próximo movimiento. El administrador puede forzarlo en «Administración».")
st.title("Mapa de relevamientos de redes eléctricas")
st.caption("Provincia de Salta · red de media tensión (13,2, 33 y 66 kV), subestaciones y CT · fuente: extracción regulatoria de EDESA")

etiquetas = {r["expediente"]: f"{r['expediente']} · {r['localidad']}" for _, r in rels.iterrows()}

# ------------------------------------------------------------------ filtros
with st.sidebar:
    acceso.caja_usuario(USUARIO)
    st.header("Buscar en el mapa")
    DIST_DF, CT_DIST, TRAMOS_DIST = datos_distribuidores()
    ETIQUETA_A_ID = dict(zip(DIST_DF["etiqueta"], DIST_DF["id"]))
    for n_, (k_, col_) in enumerate(zip(CLAVES_DIST, NOMBRES_COLOR), 1):
        st.selectbox(f"Distribuidor {n_} ({col_})", [SIN_BUSCAR] + DIST_DF["etiqueta"].tolist(), key=k_,
                     on_change=_buscar_distribuidor,
                     help="Escribí el código (ej. CF14) o parte del nombre. Se pueden elegir hasta tres, cada uno con su color; "
                          "el mapa se acerca al conjunto. La línea es una aproximación (ver «Cómo se calcula»)."
                     if n_ == 1 else "Otro distribuidor para ver al mismo tiempo, con su propio color.")
    _locs_b = lista_localidades()
    st.selectbox("Localidad", [SIN_BUSCAR] + _locs_b["nombre"].tolist(), key="busca_loc", on_change=_buscar_localidad,
                 help="Elegí o escribí una localidad: el mapa se acerca y se resaltan en morado los distribuidores que la "
                      "alimentan (los que tienen CT en ella). Si ya hay relevamiento, sigue verde o azul sobre el morado.")
    _pts = lista_puntos()
    st.selectbox("CT o subestación", [SIN_BUSCAR] + _pts["etiqueta"].tolist(), key="busca_pt", on_change=_buscar_punto,
                 help="Escribí parte del nombre o del número del CT. El mapa se acerca y lo marca con un círculo amarillo.")
    if st.session_state.get("foco") or any(st.session_state.get(k, SIN_BUSCAR) != SIN_BUSCAR for k in CLAVES_DIST + ("busca_loc", "busca_pt")):
        st.button("Quitar la búsqueda", on_click=_limpiar_busqueda)
    st.header("Qué mostrar")
    estados_ver = st.multiselect("Estado", list(lg.ESTADOS_REL), default=list(lg.ESTADOS_REL))
    exps_todos = rels["expediente"].tolist()
    elegidos = st.multiselect("Expedientes", exps_todos, default=exps_todos, format_func=lambda e: etiquetas.get(e, e))

# Parámetros fijos (antes eran filtros de la columna izquierda).
ver_t = {13.2: True, 33.0: True, 66.0: True}
ver_est = True
ver_ct = True
nom_est = True
nom_ct = "Al acercar el zoom (todos)"
ver_obs = True
ver_loc = "Automático"
tam_txt = 10
grosor = 9
ver_exp_txt = True
margen = 800
with st.sidebar:
    base = st.selectbox("Mapa base", list(ESTILOS_BASE), index=0)

pal = PALETAS["oscuro" if base == "Satélite" else "claro"]

# Distribuidores a resaltar: el elegido, o los que alimentan la localidad elegida.
_sel_l = st.session_state.get("busca_loc", SIN_BUSCAR)
_sel_d = [st.session_state.get(k_, SIN_BUSCAR) for k_ in CLAVES_DIST]
# grupos = [(índice de color, [ids])]: cada distribuidor elegido con su color. Si se eligió una localidad (y ningún distribuidor
# a mano), se resaltan los que más CT tienen en ella, cada uno con un color distinto.
grupos: list[tuple[int, list[str]]] = []
TRAMOS_RES: dict[str, np.ndarray] = {}           # tramos que se resaltan de cada distribuidor
CT_RES: dict[str, set] = {}                      # CT que se resaltan de cada distribuidor (si falta, todos)
n_ocultos = 0
solo_loc = False
for n_, e_ in enumerate(_sel_d):
    i_ = ETIQUETA_A_ID.get(e_)
    if i_ and i_ in TRAMOS_DIST and all(i_ not in g_[1] for g_ in grupos):
        grupos.append((n_, [i_]))
        TRAMOS_RES[i_] = TRAMOS_DIST[i_]
if not any(e_ != SIN_BUSCAR for e_ in _sel_d) and _sel_l != SIN_BUSCAR:
    solo_loc = True                 # fijo: solo el tramo del distribuidor dentro de la localidad
    _dl = lg.distribuidores_de_localidad(CT_DIST, _sel_l)
    _dl = _dl[_dl["id_distribuidor"].isin(TRAMOS_DIST)]
    if len(_dl):
        _umbral = max(3, 0.25 * float(_dl["n_ct_localidad"].max()))      # los que tienen una presencia real en la localidad
        _top = _dl[_dl["n_ct_localidad"] >= _umbral].head(MAX_DIST_LOCALIDAD)
        _top = _top if len(_top) else _dl.head(1)
        n_ocultos = len(_dl) - len(_top)
        _ct_loc = CT_DIST[CT_DIST["localidad_mapa"].map(lg.norm_nombre) == lg.norm_nombre(_sel_l)]
        _ct_loc = _ct_loc[_ct_loc["lat"].notna()]
        for i_ in _top["id_distribuidor"]:
            if solo_loc:
                _cerca = lg.tramos_cerca(red, TRAMOS_DIST[i_], _ct_loc["lon"], _ct_loc["lat"], RADIO_LOCALIDAD_M)
                if len(_cerca) == 0:
                    continue
                TRAMOS_RES[i_] = _cerca
                CT_RES[i_] = set(_ct_loc[_ct_loc["id_distribuidor"] == i_]["ct"])
            else:
                TRAMOS_RES[i_] = TRAMOS_DIST[i_]
            grupos.append((len(grupos), [i_]))
ids_res = [i_ for _, g_ in grupos for i_ in g_]
color_de = {i_: c_ for c_, g_ in grupos for i_ in g_}

# ------------------------------------------------- tramos de cada relevamiento
vis = rels[rels["estado"].isin(estados_ver) & rels["expediente"].isin(elegidos)].reset_index(drop=True)
tramos = [tramos_de_fila(f, obs, margen) for _, f in vis.iterrows()]
asig = lg.asignar_relevamientos(red, tramos)
relevado = asig >= 0
est_arr = np.array([f["estado"] for _, f in vis.iterrows()] + [""])      # el último valor cubre el -1
ejecutado = relevado & (est_arr[asig] == lg.EJECUTADO)
azul = relevado & ~ejecutado

total_km = float(red["largo_m"].sum()) / 1000
km_rel, km_eje = lg.km(red, relevado), lg.km(red, ejecutado)
n_eje = int((vis["estado"] == lg.EJECUTADO).sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Expedientes", len(vis))
c2.metric("Red de MT relevada", f"{num(km_rel)} km", f"{num(km_rel / total_km * 100)} % de {num(total_km, 0)} km", delta_color="off")
c3.metric("Adecuaciones ejecutadas por EDESA", f"{n_eje} de {len(vis)}", f"{num(km_eje)} km", delta_color="off")
c4.metric("Pendientes de EDESA", len(vis) - n_eje, f"{num(km_rel - km_eje)} km", delta_color="off")


def cuadro(rgb, ancho_px=26, alto_px=4):
    return (f"<span style='display:inline-block;width:{ancho_px}px;height:{alto_px}px;border-radius:3px;"
            f"background:rgb({rgb[0]},{rgb[1]},{rgb[2]});vertical-align:middle;margin-right:6px'></span>")


def punto(rgb):
    return (f"<span style='display:inline-block;width:12px;height:12px;border-radius:50%;"
            f"background:rgb({rgb[0]},{rgb[1]},{rgb[2]});vertical-align:middle;margin-right:6px'></span>")


st.markdown(
    "<div style='display:flex;flex-wrap:wrap;gap:6px 22px;font-size:0.9rem;margin:4px 0 8px 0'>"
    f"<span>{cuadro(pal['relevado'], 26, 6)}Relevado (adecuaciones pendientes)</span>"
    f"<span>{cuadro(pal['ejecutado'], 26, 6)}Adecuaciones ejecutadas por EDESA</span>"
    + "".join(f"<span>{cuadro(pal['dist'][c_], 26, 6)}Distribuidor {g_[0] if len(g_) == 1 else 'de la localidad'}</span>"
              for c_, g_ in grupos) +
    f"<span>{cuadro(pal['red'][13.2])}Sin relevar 13,2 kV</span>"
    f"<span>{cuadro(pal['red'][33.0], 26, 5)}33 kV</span>"
    f"<span>{cuadro(pal['red'][66.0], 26, 6)}66 kV</span>"
    f"<span>{punto(pal['estacion'])}Estación o central</span>"
    f"<span>{punto(pal['ct'])}CT</span>"
    f"<span>{punto(pal['obs'])}Observación</span>"
    "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------- el mapa
def capa_tramos(indices, rgb, ancho: float, id_=None, extra: dict | None = None, pickable: bool = False):
    if len(indices) == 0:
        return None
    df = pd.DataFrame({"path": [XY[OFF[i]:OFF[i + 1]] for i in indices]})
    for k, v in (extra or {}).items():
        df[k] = list(v)
    kw = {"id": id_} if id_ else {}
    # Ancho fijo en píxeles: el mínimo y el máximo son iguales, así no depende de las unidades del mapa.
    return pdk.Layer("PathLayer", df, get_path="path", get_color=color(rgb), get_width=1,
                     width_min_pixels=ancho, width_max_pixels=ancho, cap_rounded=True, joint_rounded=True,
                     pickable=pickable, **kw)


def capa_puntos(df: pd.DataFrame, rgb, radio: float, borde: bool = True, id_=None, pickable=True):
    if df.empty:
        return None
    kw = {"id": id_} if id_ else {}
    return pdk.Layer("ScatterplotLayer", df, get_position=["lon", "lat"], get_fill_color=color(rgb),
                     get_radius=1, radius_min_pixels=radio, radius_max_pixels=radio, stroked=borde,
                     get_line_color=color(pal["borde"]), line_width_min_pixels=1.5, pickable=pickable, **kw)


_TIT, _DET = [], []                      # textos del tooltip, una vez por expediente
for _, _f in vis.iterrows():
    _TIT.append(html.escape(f"Expte. {_f['expediente']} · {_f['localidad']}"))
    if _f["estado"] == lg.EJECUTADO:
        _cuando = f" el {fmt_fecha(_f['fecha_ejecucion'])}" if _f["fecha_ejecucion"] else ""
        _DET.append(html.escape(f"Adecuaciones ejecutadas por EDESA{_cuando} · clic para ver el detalle"))
    else:
        _DET.append("Relevado, adecuaciones pendientes · clic para ver el detalle")


@st.cache_data(show_spinner=False)
def textos_distribuidor_por_tramo(n_tramos: int):
    """Para cada tramo de la red, el texto «Distribuidor X · nombre» (o «sin distribuidor asignado»)."""
    nombres = dict(zip(DIST_DF["id"], DIST_DF["nombre"]))
    ids_ = [[] for _ in range(n_tramos)]
    for did_, segs_ in TRAMOS_DIST.items():
        for s_ in segs_:
            if len(ids_[int(s_)]) < 2:
                ids_[int(s_)].append(did_)
    return [html.escape(" / ".join(f"{d_} {nombres.get(d_, '')}".strip() for d_ in lst)) if lst else "sin distribuidor asignado"
            for lst in ids_]


TXT_DIST = textos_distribuidor_por_tramo(len(red["largo_m"]))


def textos_tramos(idx_tramos):
    """Expediente, título y detalle del tooltip de cada tramo relevado."""
    a_ = asig[idx_tramos]
    return {"expediente": [str(vis.at[k, "expediente"]) for k in a_], "tramo": idx_tramos.tolist(), "titulo": [_TIT[k] for k in a_],
            "detalle": [f"{_DET[k]}<br/>Distribuidor: {TXT_DIST[i]}" for k, i in zip(a_, idx_tramos)]}


def textos_sin_relevar(idx_tramos, kv):
    """Datos mínimos por tramo (para que el mapa pese poco); el distribuidor se busca recién al hacer clic."""
    n_ = len(idx_tramos)
    return {"tramo": idx_tramos.tolist(), "titulo": ["Red sin relevar"] * n_, "detalle": ["Clic para ver el distribuidor"] * n_}


sel_exp = st.session_state.get("exp_sel") if st.session_state.get("zoom_exp") else None   # solo si se eligió a propósito
capas = []
for t in lg.TENSIONES:                                   # red sin relevar, debajo
    if ver_t[t]:
        idx_g = np.flatnonzero((red["tension"] == np.float32(t)) & ~relevado)
        capas.append(capa_tramos(idx_g, pal["red"][t], ANCHO_RED[t], id_=f"red_{t:g}", extra=textos_sin_relevar(idx_g, t), pickable=True))
# Distribuidores resaltados: debajo de lo relevado, así el azul/verde se ve por encima (con borde del color del distribuidor).
_dn = dict(zip(DIST_DF["id"], DIST_DF["nombre"]))
for c_, g_ in grupos:
    _dueno: dict[int, str] = {}
    for i_ in reversed(g_):
        for s_ in TRAMOS_RES[i_]:
            _dueno[int(s_)] = i_
    idx_d = np.array(sorted(_dueno), dtype=np.int64)
    if len(idx_d):
        capas.append(capa_tramos(idx_d, pal["dist"][c_], grosor + 4, id_=f"distribuidor_{c_}", pickable=True, extra={
            "tramo": idx_d.tolist(), "titulo": [html.escape(f"Distribuidor {_dueno[int(k)]} · {_dn.get(_dueno[int(k)], '')}") for k in idx_d],
            "detalle": ["Aproximación a partir de los CT del padrón de EDESA"] * len(idx_d)}))
if sel_exp in set(vis["expediente"]):                     # halo del expediente elegido
    k = int(vis.index[vis["expediente"] == sel_exp][0])
    capas.append(capa_tramos(tramos[k]["segmentos"], pal["seleccion"], grosor + 8))
for estado_arr, rgb, nombre in ((azul, pal["relevado"], "relevado"), (ejecutado, pal["ejecutado"], "ejecutado")):
    for t in lg.TENSIONES:
        if not ver_t[t]:
            continue
        idx = np.flatnonzero(estado_arr & (red["tension"] == np.float32(t)))
        capas.append(capa_tramos(idx, rgb, grosor + 0.8 * (ANCHO_RED[t] - 1), id_=f"{nombre}_{t:g}", extra=textos_tramos(idx), pickable=True))
foco = st.session_state.get("foco")
if foco and foco["punto"]:
    capas.append(pdk.Layer("ScatterplotLayer", pd.DataFrame([foco]), id="foco_punto", get_position=["lon", "lat"],
                           get_fill_color=color(pal["seleccion"], 90), get_line_color=color(pal["seleccion"]), stroked=True,
                           get_radius=1, radius_min_pixels=16, radius_max_pixels=16, line_width_min_pixels=3, pickable=False))
if ver_ct:
    capas.append(capa_puntos(ct_df, pal["ct"], 2.2, borde=False, pickable=True))
    for c_, g_ in grupos:
        _cts = ct_df[ct_df["dist_id"].isin(g_)]
        if g_[0] in CT_RES:
            _cts = _cts[_cts["nombre"].isin(CT_RES[g_[0]])]
        capas.append(capa_puntos(_cts, pal["dist"][c_], 3.6, borde=True, id_=f"ct_distribuidor_{c_}"))
if ver_est:
    capas.append(capa_puntos(est_df, pal["estacion"], 5.5))
def capa_texto(df: pd.DataFrame, tam: int, id_: str, desplazar=(0, 0)):
    """Rótulos de texto de tamaño fijo en píxeles, con fondo para que se lean sobre cualquier mapa."""
    if df.empty:
        return None
    return pdk.Layer("TextLayer", df, id=id_, get_position=["lon", "lat"], get_text="nombre", get_size=1,
                     size_min_pixels=tam, size_max_pixels=tam, get_color="color",
                     get_text_anchor='"middle"', get_alignment_baseline='"center"', get_pixel_offset=list(desplazar),
                     font_family='"Arial, sans-serif"', font_weight='"bold"', character_set='"auto"',
                     background=True, get_background_color=pal["fondo"], background_padding=[4, 2], pickable=False)


def punto_central(indices):
    """(lon, lat) de un tramo ubicado hacia el medio del conjunto, para poner el rótulo sobre la línea."""
    cx, cy = red["cx"][indices], red["cy"][indices]
    j = int(np.argmin((cx - cx.mean()) ** 2 + (cy - cy.mean()) ** 2))
    return float(cx[j]), float(cy[j])


if ver_obs:
    o = obs[obs["expediente"].isin(vis["expediente"])]
    o = o[lg.coordenadas_validas(o)].copy()
    o["titulo"] = ("Obs. " + o["n"].astype(str) + " · expte. " + o["expediente"]).map(html.escape)
    o["detalle"] = o["tipo"].map(html.escape)
    capas.append(capa_puntos(o[["lon", "lat", "titulo", "detalle", "expediente"]], pal["obs"], 6.5, id_="obs"))
capas = [c for c in capas if c is not None]

acercado = True
if sel_exp in set(vis["expediente"]) and len(tramos[int(vis.index[vis["expediente"] == sel_exp][0])]["segmentos"]):
    caja = lg.caja_segmentos(red, tramos[int(vis.index[vis["expediente"] == sel_exp][0])]["segmentos"])
else:
    caja = (float(red["lon"].min()), float(red["lon"].max()), float(red["lat"].min()), float(red["lat"].max()))
    acercado = False
lat0, lon0, zoom0 = lg.vista_para(*caja, alto_px=ALTO_MAPA)
if foco:
    lat0, lon0, zoom0 = foco["lat"], foco["lon"], foco["zoom"]
    acercado = True

# Rótulos: nombres de localidades y número de expediente sobre cada línea (van arriba de todo).
locs = lg.cargar_localidades()
modo_loc = ("Todas" if acercado else "Solo principales") if ver_loc == "Automático" else ver_loc
if modo_loc == "Solo principales":
    locs = locs[locs["categoria"] == "principal"]
if modo_loc != "Ninguna" and not locs.empty:
    locs = locs.assign(color=[color(pal["texto"])] * len(locs))
    capas.append(capa_texto(locs[locs["categoria"] == "menor"], tam_txt, "loc_menores"))
    capas.append(capa_texto(locs[locs["categoria"] == "principal"], tam_txt + 6, "loc_principales"))
if nom_est and len(est_df):
    capas.append(capa_texto(est_df.assign(color=[color(pal["texto"])] * len(est_df)), max(10, tam_txt - 6), "nombres_estaciones", (0, -16)))
if nom_ct.startswith("Al acercar") and len(ct_df):
    # Tamaño en metros: crece al acercarse y casi no se ve de lejos. size_max_pixels lo frena para que no tape.
    # Los textos van entre comillas dobles: pydeck trata un texto sin comillas como una expresión y el mapa no lo entiende.
    capas.append(pdk.Layer("TextLayer", ct_df[["lon", "lat", "nombre"]], id="nombres_ct_zoom", get_position=["lon", "lat"],
                           get_text="nombre", get_size=16, size_units='"meters"', size_min_pixels=0,
                           size_max_pixels=max(10, tam_txt - 8), get_color=color(pal["texto"]),
                           get_text_anchor='"middle"', get_alignment_baseline='"center"', get_pixel_offset=[0, -10],
                           font_family='"Arial, sans-serif"', font_weight='"bold"', character_set='"auto"', pickable=False))
if nom_ct.startswith("Los de"):
    ve = list(vis["expediente"])
    exp_foco = st.session_state.get("exp_sel") if st.session_state.get("exp_sel") in ve else (ve[0] if ve else None)
    d_ct = ct_df.iloc[0:0]
    if exp_foco is not None and len(tramos[ve.index(exp_foco)]["segmentos"]):
        x0_, x1_, y0_, y1_ = lg.caja_segmentos(red, tramos[ve.index(exp_foco)]["segmentos"])
        d_ct = ct_df[ct_df["lon"].between(x0_ - 0.008, x1_ + 0.008) & ct_df["lat"].between(y0_ - 0.008, y1_ + 0.008)]
    if len(d_ct):
        capas.append(capa_texto(d_ct.assign(color=[color(pal["texto"])] * len(d_ct)), max(10, tam_txt - 10), "nombres_ct", (0, -11)))
if foco and foco["punto"]:
    capas.append(capa_texto(pd.DataFrame([{**foco, "color": color(pal["texto"])}]), tam_txt, "nombre_buscado", (0, -26)))
if ver_exp_txt and len(vis):
    filas_txt = []
    for k, f_ in vis.iterrows():
        if len(tramos[k]["segmentos"]):
            lo_, la_ = punto_central(np.asarray(tramos[k]["segmentos"], dtype=np.int64))
            rgb_ = pal["ejecutado"] if f_["estado"] == lg.EJECUTADO else pal["relevado"]
            filas_txt.append({"lon": lo_, "lat": la_, "nombre": f"Expte. {f_['expediente']}", "color": color(rgb_)})
    capas.append(capa_texto(pd.DataFrame(filas_txt), max(11, tam_txt - 3), "rotulos_expedientes"))
capas = [c for c in capas if c is not None]

TOOLTIP = {"html": "<b>{titulo}</b><br/>{detalle}", "style": {"backgroundColor": "#1f2933", "color": "white", "fontSize": "13px"}}
_args_deck = dict(layers=capas,
                  initial_view_state=pdk.ViewState(latitude=float(lat0), longitude=float(lon0),
                                                   zoom=float(min(zoom0, 18 if foco else 16)), pitch=0),
                  height=ALTO_MAPA, tooltip=TOOLTIP)
deck = crear_deck(base, **_args_deck)
# El mapa muestra una manito sobre las líneas y los CT; se cambia por la flecha normal.
st.markdown("<style>div[data-testid='stDeckGlJsonChart'] canvas, div[data-testid='stDeckGlJsonChart'] div "
            "{ cursor: default !important; }</style>", unsafe_allow_html=True)
evento = pintar_mapa(deck, ALTO_MAPA, clave="mapa")

_info = linea_clicada(evento)
if _info:
    st.info(f"**{_info[0]}**" + (f"  \n{_info[1]}" if _info[1] else ""), icon="📍")
    if _info[2]:
        _url = f"https://www.google.com/maps/search/?api=1&query={_info[2][1]:.6f},{_info[2][0]:.6f}"
        try:
            st.link_button("Abrir este punto en Google Maps", _url)
        except AttributeError:                      # Streamlit viejo: sin botón de enlace
            st.markdown(f"[Abrir este punto en Google Maps]({_url})")

clic = expediente_clicado(evento)
if clic and clic != st.session_state.get("ultimo_clic"):
    st.session_state["ultimo_clic"] = clic
    if clic in exps_todos:
        st.session_state["exp_sel"] = clic
        st.session_state["zoom_exp"] = True
        st.rerun()

if vis.empty:
    st.info("Todavía no hay expedientes para mostrar con estos filtros. Cargá uno en la pestaña «Cargar relevamiento».")

# ------------------------------------------------- distribuidores resaltados
for n_, e_ in enumerate(_sel_d):
    if e_ != SIN_BUSCAR and ETIQUETA_A_ID.get(e_) not in TRAMOS_DIST:
        st.warning(f"«{e_}» no tiene CT ubicados sobre la red del mapa, así que no se puede dibujar su línea.")
if _sel_l != SIN_BUSCAR and all(e_ == SIN_BUSCAR for e_ in _sel_d) and not ids_res:
    st.info(f"No hay CT del padrón en «{_sel_l}», así que no se sabe qué distribuidor la alimenta.")
if ids_res:
    _por_loc = _sel_l != SIN_BUSCAR and all(e_ == SIN_BUSCAR for e_ in _sel_d)
    n_loc_ = (lg.distribuidores_de_localidad(CT_DIST, _sel_l).set_index("id_distribuidor")["n_ct_localidad"] if _por_loc else None)
    st.subheader("Distribuidores resaltados" if len(ids_res) > 1 else "Distribuidor resaltado")
    if n_loc_ is not None:
        st.caption(f"«{_sel_l}» recibe energía de {len(n_loc_)} distribuidor{'es' if len(n_loc_) > 1 else ''}. Se resaltan "
                   f"{len(ids_res)} (los de más CT en la localidad), cada uno con su color"
                   + (" y solo cerca de sus CT." if solo_loc else ", de punta a punta.")
                   + (f" Los otros {n_ocultos}, con pocos CT allí, no se resaltan: se pueden ver con los buscadores de distribuidor." if n_ocultos else ""))
    filas_d = []
    for i_ in ids_res:
        d_ = DIST_DF[DIST_DF["id"] == i_].iloc[0]
        m_ = np.zeros(len(red["cx"]), dtype=bool)
        m_[TRAMOS_RES[i_]] = True
        km_d, km_dr = lg.km(red, m_), lg.km(red, m_ & relevado)
        fila_d = {"Color": EMOJI_COLOR[color_de[i_]], "Código": i_, "Distribuidor": d_["nombre"], "Distrito": d_["distrito"],
                  "Sale de": f"{d_['estacion']} · barra {d_['barra']}".strip(" ·") if d_["estacion"] else "",
                  "kV": d_["tension"], "CT": d_["n_ct"], "Km de línea": km_d, "Km relevados": km_dr, "Km del distribuidor": d_["km"],
                  "% relevado": (km_dr / km_d * 100) if km_d else 0.0, "Localidades (CT)": d_["localidades"]}
        if n_loc_ is not None:
            fila_d = {"CT en la localidad": int(n_loc_.get(i_, 0)), **fila_d}
        filas_d.append(fila_d)
    tabla_d = pd.DataFrame(filas_d)
    st.dataframe(tabla_d, hide_index=True, use_container_width=True, column_config={
        "kV": st.column_config.NumberColumn(format="%.1f"),
        "Km de línea": st.column_config.NumberColumn(format="%.1f"),
        "Km relevados": st.column_config.NumberColumn(format="%.1f"),
        "Km del distribuidor": st.column_config.NumberColumn(format="%.1f"),
        "% relevado": st.column_config.ProgressColumn(format="%.0f %%", min_value=0, max_value=100)})
    st.caption("La línea de cada distribuidor se arma uniendo sus CT por el camino más corto de la red (desde la estación de cabecera "
               "cuando se la reconoce), así que es una aproximación: puede faltar algún ramal o incluir un tramo vecino. "
               "Los tramos ya relevados siguen en azul o verde, con borde del color del distribuidor. "
               "Haciendo clic sobre cualquier línea del mapa se ve a qué distribuidor pertenece.")

# ------------------------------------------------------ detalle del expediente
st.subheader("Detalle del expediente")
if not exps_todos:
    st.caption("Cuando cargues un relevamiento, hacé clic en su línea del mapa (o elegilo acá) para ver sus datos.")
else:
    if st.session_state.get("exp_sel") not in exps_todos:
        st.session_state["exp_sel"] = exps_todos[0]
    st.selectbox("Expediente (también se elige haciendo clic en la línea del mapa)", exps_todos,
                 format_func=lambda e: etiquetas.get(e, e), key="exp_sel", on_change=_acercar)
    exp = st.session_state["exp_sel"]
    fila = rels[rels["expediente"] == exp].iloc[0]
    t = tramos_de_fila(fila, obs, margen)
    km_exp = lg.km(red, np.isin(np.arange(len(red["cx"])), t["segmentos"]))
    o_exp = obs[obs["expediente"] == exp]
    ejec = fila["estado"] == lg.EJECUTADO

    a, b, c = st.columns([1.1, 1.2, 1.4])
    with a:
        st.markdown(f"**Expediente {html.escape(exp)}**")
        st.markdown(f"{'🟢 **Adecuaciones ejecutadas por EDESA**' if ejec else '🔵 **Relevado · adecuaciones pendientes**'}")
        if ejec and fila["fecha_ejecucion"]:
            st.caption(f"Ejecutado el {fmt_fecha(fila['fecha_ejecucion'])}")
    with b:
        st.markdown(f"**Localidad:** {fila['localidad'] or '—'}")
        fechas = fmt_fecha(fila["fecha_desde"])
        if fila["fecha_hasta"] and fila["fecha_hasta"] != fila["fecha_desde"]:
            fechas += f" al {fmt_fecha(fila['fecha_hasta'])}"
        st.markdown(f"**Relevado:** {fechas or '—'}")
        st.markdown(f"**Informe:** {fila['informe'] or '—'}")
    with c:
        st.markdown(f"**Línea relevada:** {num(km_exp)} km en {len(t['segmentos'])} tramos")
        st.markdown(f"**Cómo se marcó:** {fila['modo']} · {t['origen']}")
        st.markdown(f"**Observaciones del informe:** {len(o_exp)}")
    if not t["ok"]:
        st.warning(t["mensaje"] or "Este expediente no tiene línea marcada en el mapa.")

    ruta_pdf = lg.ruta_informe(exp)
    d1, d2 = st.columns([1, 2])
    with d1:
        if ruta_pdf:
            st.download_button("Descargar el informe (PDF)", ruta_pdf.read_bytes(), file_name=f"Informe_expte_{exp}.pdf",
                               mime="application/pdf", key=f"dl_{exp}")
        else:
            st.caption("No hay PDF guardado para este expediente. Se puede subir en «Cargar relevamiento».")
    with d2:
        if not PUEDE_EDITAR:
            st.caption("Tu usuario es de solo lectura: no puede cambiar el estado.")
        elif ejec:
            if st.button("Deshacer: volver a «Relevado»", key=f"undo_{exp}"):
                lg.cambiar_estado(exp, lg.RELEVADO)
                almacen.registrar(USUARIO.etiqueta, "Deshizo ejecución", f"Expediente {exp}: vuelve a Relevado")
                st.session_state["aviso"] = f"Expediente {exp}: vuelve a Relevado"
                st.rerun()
        else:
            e1, e2, e3 = st.columns([1, 1.4, 1.2])
            fecha_e = e1.date_input("Fecha de ejecución", value=date.today(), key=f"fe_{exp}", format="DD/MM/YYYY")
            nota = e2.text_input("Comentario (opcional)", key=f"nt_{exp}")
            e3.markdown("<div style='height:1.8rem'></div>", unsafe_allow_html=True)
            if e3.button("Marcar como ejecutado por EDESA", type="primary", key=f"ok_{exp}"):
                lg.cambiar_estado(exp, lg.EJECUTADO, str(fecha_e), nota)
                almacen.registrar(USUARIO.etiqueta, "Marcó ejecutado por EDESA",
                                  f"Expediente {exp} · fecha {fecha_e}" + (f" · {nota}" if nota else ""))
                st.session_state["aviso"] = f"Expediente {exp}: ejecutado por EDESA"
                st.rerun()
    if fila["comentario"]:
        st.caption(f"Comentario: {fila['comentario']}")

# ------------------------------------------------------------------ pestañas
# Cada rol ve sus pestañas: lector (ver y descargar), editor (+ cargar y copias), administrador (+ Administración).
_PESTANAS = [("cargar", "Cargar relevamiento", PUEDE_EDITAR), ("rel", "Relevamientos", True), ("rep", "Reportes", True),
             ("copias", "Copias de seguridad", PUEDE_EDITAR), ("admin", "Administración", ES_ADMIN),
             ("info", "Cómo se calcula", True)]
_visibles = [p for p in _PESTANAS if p[2]]
_tabs = dict(zip([p[0] for p in _visibles], st.tabs([p[1] for p in _visibles])))
tab_cargar, tab_rel, tab_rep = _tabs.get("cargar"), _tabs["rel"], _tabs["rep"]
tab_copias, tab_admin, tab_info = _tabs.get("copias"), _tabs.get("admin"), _tabs["info"]


def ubicar_observaciones(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Completa lat/lon abriendo los enlaces de Google Maps (necesita internet; ~1,5 s por enlace)."""
    import resolver_enlaces as rs
    df = df.copy()
    pendientes = df.index[~lg.coordenadas_validas(df) & df["enlace"].astype(str).str.startswith("http")]
    barra = st.progress(0.0, text="Ubicando observaciones...")
    logradas = 0
    for k, i in enumerate(pendientes, 1):
        c = rs.resolver(df.at[i, "enlace"])
        if c:
            df.at[i, "lat"], df.at[i, "lon"] = c
            logradas += 1
        barra.progress(k / len(pendientes), text=f"Ubicando observaciones... {k} de {len(pendientes)}")
        time.sleep(1.5)
    barra.empty()
    return df, logradas, len(pendientes)


def deck_previa(segmentos, linea, puntos):
    capas_p = []
    if len(segmentos):
        capas_p.append(capa_tramos(segmentos, pal["relevado"], 6))
    if linea:
        df = pd.DataFrame({"path": [[list(p) for p in linea]]})
        capas_p.append(pdk.Layer("PathLayer", df, get_path="path", get_color=color(pal["seleccion"]), get_width=1,
                                 width_min_pixels=2, width_max_pixels=2))
    if puntos:
        dfp = pd.DataFrame([{"lon": lo, "lat": la, "titulo": html.escape(str(e)), "detalle": ""} for lo, la, e in puntos])
        capas_p.append(capa_puntos(dfp, pal["estacion"], 7))
    if len(segmentos):
        x0, x1, y0, y1 = lg.caja_segmentos(red, segmentos)
        xs = [x0, x1] + [p[0] for p in puntos]
        ys = [y0, y1] + [p[1] for p in puntos]
        caja_p = (min(xs), max(xs), min(ys), max(ys))
    else:
        xs, ys = [p[0] for p in puntos], [p[1] for p in puntos]
        caja_p = (min(xs) - .01, max(xs) + .01, min(ys) - .01, max(ys) + .01)
    la, lo, z = lg.vista_para(*caja_p, alto_px=ALTO_PREVIA)
    return crear_deck(base, layers=[c for c in capas_p if c is not None],
                      initial_view_state=pdk.ViewState(latitude=float(la), longitude=float(lo), zoom=float(min(z, 16))),
                      height=ALTO_PREVIA, tooltip=TOOLTIP)


if PUEDE_EDITAR:
    with tab_cargar:
        if st.session_state.pop("reiniciar_carga", False):
            for k in [k for k in st.session_state if k.startswith("c_")]:
                del st.session_state[k]

        modo = st.radio("Cómo querés definir la línea relevada", list(MODOS), format_func=MODOS.get, key="c_modo")
        pdf = st.file_uploader("Informe de relevamiento en PDF" + (" (obligatorio en este modo)" if modo == "informe" else " (opcional)"),
                               type="pdf", key="c_pdf",
                               help="Se guarda para poder descargarlo haciendo clic sobre la línea del expediente.")
        leido = None
        if pdf is not None:
            contenido = pdf.getvalue()
            id_pdf = (pdf.name, len(contenido))
            if st.session_state.get("c_pdf_id") != id_pdf:                      # archivo nuevo: leerlo y precargar el formulario
                try:
                    leido = informes.leer_informe(contenido)
                except Exception as error_pdf:                                   # PDF dañado o con otro formato
                    leido = {"error": str(error_pdf)}
                st.session_state["c_pdf_id"] = id_pdf
                st.session_state["c_leido"] = leido
                if "error" not in leido:
                    st.session_state["c_loc"] = leido["localidad"]
                    st.session_state["c_inf"] = leido["informe"]
                    for campo, clave in (("fecha_desde", "c_desde"), ("fecha_hasta", "c_hasta")):
                        if leido[campo]:
                            st.session_state[clave] = date.fromisoformat(leido[campo])
                    o = pd.DataFrame(leido["observaciones"], columns=["n", "tipo", "enlace"])
                    for col, val in (("expediente", ""), ("estado", "Pendiente"), ("fecha_resolucion", ""), ("comentario", ""),
                                     ("lat", np.nan), ("lon", np.nan)):
                        o[col] = val
                    st.session_state["c_obs"] = o[lg.COL_OBS]
            leido = st.session_state.get("c_leido")
            if leido and "error" in leido:
                st.error(f"No pude leer el PDF ({leido['error']}). Completá los datos a mano.")
            elif leido:
                for aviso in leido["avisos"]:
                    st.warning(aviso)
                if not leido["avisos"]:
                    st.success(f"Leí el informe: {len(leido['observaciones'])} observaciones, localidad, fechas y referencia. Revisalos antes de guardar.")
        else:
            st.session_state.pop("c_pdf_id", None)
            st.session_state.pop("c_obs", None)
            st.session_state.pop("c_leido", None)

        st.session_state.setdefault("c_desde", date.today())
        st.session_state.setdefault("c_hasta", date.today())
        f1, f2, f3, f4, f5 = st.columns([1, 1.2, 1, 1, 1.6])
        exp_nuevo = f1.text_input("N° de expediente", key="c_exp", help="No figura en el informe: hay que escribirlo.").strip()
        loc_nueva = f2.text_input("Localidad / distribuidor", key="c_loc")
        desde = f3.date_input("Relevado desde", key="c_desde", format="DD/MM/YYYY")
        hasta = f4.date_input("hasta", key="c_hasta", format="DD/MM/YYYY")
        inf_nuevo = f5.text_input("Referencia del informe", key="c_inf", placeholder="IF 267 - GEE N° 578 - 2026")

        lista = lista_puntos()
        seg_nuevos, linea_nueva, puntos_prev, definicion, buffer_def, mensaje = np.empty(0, np.int32), [], [], "", np.nan, ""
        listo = False
        puntos_def = []

        if modo == "informe":
            o = st.session_state.get("c_obs")
            if pdf is None or o is None:
                st.info("Subí el informe en PDF. Se leen las observaciones y sus enlaces de Google Maps; la línea se marca como la zona que las rodea.")
            else:
                ubicadas = int(lg.coordenadas_validas(o).sum())
                st.markdown(f"**{len(o)} observaciones** leídas del informe, **{ubicadas}** con coordenadas.")
                if ubicadas < len(o) and st.button("Ubicar observaciones en el mapa (abre cada enlace de Google Maps)", key="c_ubicar"):
                    nuevo, logradas, intentadas = ubicar_observaciones(o)
                    st.session_state["c_obs"] = nuevo
                    st.session_state["aviso"] = f"Ubiqué {logradas} de {intentadas} observaciones"
                    st.rerun()
                mostrar(st.dataframe, o[["n", "tipo", "lat", "lon", "enlace"]], hide_index=True, height=220)
                buffer_def = st.slider("Margen alrededor de las observaciones (m)", 100, 2000, int(margen), 100, key="c_buf")
                definicion = lg.definicion_zona()
                ob = o.copy()
                ob["expediente"] = exp_nuevo or "nuevo"
                fila_n = {"expediente": exp_nuevo or "nuevo", "definicion": definicion, "buffer_m": float(buffer_def)}
                tr = lg.tramos_de(fila_n, red, grafo, ob)
                seg_nuevos, mensaje = tr["segmentos"], tr["mensaje"]
                listo = tr["ok"]
                if ubicadas == 0:
                    st.warning("Ninguna observación tiene coordenadas todavía: sin ellas no se puede dibujar la zona. "
                               "Usá el botón de arriba (necesita internet) o guardá igual y ubicalas después.")
                elif ubicadas < 3:
                    st.caption("Con muy pocas observaciones ubicadas la zona es chica; conviene ubicar el resto.")
                if len(seg_nuevos) and ubicadas:
                    puntos_prev = [(float(r_.lon), float(r_.lat), f"Obs. {int(r_.n)}") for r_ in o[lg.coordenadas_validas(o)].itertuples()]
        else:
            if modo == "ct":
                filtro = st.text_input("Buscar CT o subestación (por nombre o código)", key="c_filtro",
                                       help="Escribí parte del nombre o número para acortar las listas de abajo.")
                vista = lista[lista["etiqueta"].str.contains(filtro.strip(), case=False, regex=False)] if filtro.strip() else lista
                opciones = vista.index.tolist()[:2000]
                if filtro.strip() and not opciones:
                    st.warning("Ningún CT ni subestación coincide con la búsqueda.")
                g1, g2 = st.columns(2)
                ia = g1.selectbox("Desde", opciones, format_func=lambda i: lista.at[i, "etiqueta"], key="c_a", index=None,
                                  placeholder="Elegí el CT o subestación de partida")
                ib = g2.selectbox("Hasta", opciones, format_func=lambda i: lista.at[i, "etiqueta"], key="c_b", index=None,
                                  placeholder="Elegí el CT o subestación de llegada")
                via = st.multiselect("Puntos intermedios (opcional, en orden de recorrido)", opciones,
                                     format_func=lambda i: lista.at[i, "etiqueta"], key="c_via",
                                     help="Sirven para forzar el camino cuando hay un anillo o dos recorridos posibles.")
                if ia is not None and ib is not None:
                    secuencia = [ia] + list(via) + [ib]
                    puntos_def = [{"etiqueta": lista.at[i, "etiqueta"], "lon": lista.at[i, "lon"], "lat": lista.at[i, "lat"]} for i in secuencia]
            else:
                texto = st.text_area("Coordenadas, una por línea (latitud, longitud), en el orden del recorrido", key="c_coord", height=130,
                                     placeholder="-25.0782292, -65.5065759\n-25.1178514, -65.5247248",
                                     help="Se pueden copiar directo de Google Maps (clic derecho sobre el punto). "
                                          "Con dos puntos se busca el camino por la red de uno a otro; con más, pasa por todos en orden.")
                pts, errores = lg.leer_puntos(texto)
                for e_ in errores:
                    st.warning(e_)
                puntos_def = [{"etiqueta": f"Punto {k + 1}", "lon": lo, "lat": la} for k, (lo, la) in enumerate(pts)]
                if texto.strip() and len(pts) < 2 and not errores:
                    st.info("Hacen falta al menos dos puntos.")
            if len(puntos_def) >= 2:
                r = rutas.camino(grafo, [(p["lon"], p["lat"]) for p in puntos_def])
                seg_nuevos, linea_nueva, mensaje, listo = r["segmentos"], r["linea"], r["mensaje"], r["ok"]
                definicion = lg.definicion_recorrido(puntos_def)
                puntos_prev = [(p["lon"], p["lat"], p["etiqueta"]) for p in puntos_def]
                if listo:
                    recta = np.hypot((puntos_def[0]["lon"] - puntos_def[-1]["lon"]) * 101000, (puntos_def[0]["lat"] - puntos_def[-1]["lat"]) * 111320)
                    st.markdown(f"**Recorrido encontrado:** {num(r['largo_m'] / 1000, 2)} km por la línea, {len(seg_nuevos)} tramos "
                                f"(en línea recta, {num(recta / 1000, 2)} km).")
                    if recta > 0 and r["largo_m"] > 4 * recta and r["largo_m"] > 2000:
                        st.warning("El recorrido es mucho más largo que la distancia en línea recta. Revisalo en el mapa: "
                                   "puede estar dando una vuelta; agregá un punto intermedio para forzar el camino.")

        if mensaje and not listo:
            st.error(mensaje)

        if listo and len(seg_nuevos):
            st.markdown("**Vista previa** (en azul, la línea que quedaría marcada):")
            pintar_mapa(deck_previa(seg_nuevos, linea_nueva, puntos_prev), ALTO_PREVIA)
            existe = exp_nuevo in exps_todos
            if existe:
                st.warning(f"El expediente {exp_nuevo} ya está cargado: si guardás, se reemplazan sus datos y su línea "
                           "(el estado vuelve a «Relevado»).")
            if not exp_nuevo:
                st.info("Falta el número de expediente.")
            if st.button("Guardar relevamiento", type="primary", disabled=not exp_nuevo, key="c_guardar"):
                ref = exp_nuevo
                if pdf is not None:
                    lg.guardar_informe(ref, pdf.getvalue())
                lg.agregar_relevamiento({
                    "expediente": ref, "localidad": loc_nueva, "fecha_desde": str(desde), "fecha_hasta": str(hasta),
                    "informe": inf_nuevo, "estado": lg.RELEVADO, "modo": NOMBRE_MODO[modo], "definicion": definicion,
                    "buffer_m": float(buffer_def) if modo == "informe" else "", "fecha_ejecucion": "", "comentario": ""})
                o = st.session_state.get("c_obs")
                if o is not None and len(o):
                    o = o.copy()
                    o["expediente"] = ref
                    resto = lg.cargar_observaciones()
                    lg.guardar_observaciones(pd.concat([resto[resto["expediente"] != ref], o], ignore_index=True))
                almacen.registrar(USUARIO.etiqueta, "Reemplazó relevamiento" if existe else "Cargó relevamiento",
                                  f"Expediente {ref} · {loc_nueva} · {NOMBRE_MODO[modo]}"
                                  + (f" · {len(o)} observaciones" if o is not None and len(o) else "")
                                  + (" · con PDF" if pdf is not None else ""))
                st.session_state["aviso"] = f"Expediente {ref} guardado"
                st.session_state["reiniciar_carga"] = True
                st.session_state["exp_sel"] = ref
                st.session_state["zoom_exp"] = True
                st.rerun()
        elif not mensaje:
            st.caption("Cuando la línea esté definida, acá aparece la vista previa y el botón para guardar.")

with tab_rel:
    filas = []
    for i, r_ in vis.iterrows():
        e = r_["expediente"]
        tr = tramos[i]
        filas.append({
            "Expediente": e, "Localidad": r_["localidad"],
            "Relevado": fmt_fecha(r_["fecha_desde"]) + (f" al {fmt_fecha(r_['fecha_hasta'])}" if r_["fecha_hasta"] and r_["fecha_hasta"] != r_["fecha_desde"] else ""),
            "Informe": r_["informe"],
            "Estado": r_["estado"], "Ejecutado el": fmt_fecha(r_["fecha_ejecucion"]) if r_["fecha_ejecucion"] else "",
            "Cómo se marcó": r_["modo"], "Línea (km)": round(lg.km(red, asig == i), 1), "Tramos": int(len(tr["segmentos"])),
            "Observaciones": int((obs["expediente"] == e).sum()), "PDF": "sí" if lg.ruta_informe(e) else "no",
        })
    if filas:
        mostrar(st.dataframe, pd.DataFrame(filas), hide_index=True)
    else:
        st.caption("No hay expedientes con estos filtros.")
    with st.expander("Eliminar un expediente") if PUEDE_EDITAR else contextlib.nullcontext():
        if exps_todos and PUEDE_EDITAR:
            borrar = st.selectbox("Expediente a eliminar", exps_todos, format_func=lambda e: etiquetas.get(e, e), key="c_borrar")
            seguro = st.checkbox("Confirmo que quiero eliminarlo (se pierde su línea marcada; el PDF y las observaciones quedan guardados)", key="c_seguro")
            if st.button("Eliminar", disabled=not seguro, key="c_eliminar"):
                lg.eliminar_relevamiento(borrar)
                almacen.registrar(USUARIO.etiqueta, "Eliminó relevamiento", f"Expediente {borrar}")
                st.session_state["aviso"] = f"Expediente {borrar} eliminado"
                st.rerun()

with tab_rep:
    st.markdown("Descargá el estado de los relevamientos en **Excel** (una fila por expediente y otra por observación, con "
                "totales que se recalculan al filtrar) o como **nota en Word** dirigida al Directorio.")
    if rels.empty:
        st.info("Todavía no hay expedientes cargados.")
    else:
        r1, r2 = st.columns([2, 1])
        if "rep_exps" not in st.session_state:
            st.session_state["rep_exps"] = list(exps_todos)
        else:                                            # por si se borró algún expediente
            st.session_state["rep_exps"] = [e for e in st.session_state["rep_exps"] if e in exps_todos]
        exp_rep = r1.multiselect("Expedientes a incluir", exps_todos, key="rep_exps",
                                 format_func=lambda e: etiquetas.get(e, e))
        b_t, b_n, _ = r1.columns([1, 1, 3])
        b_t.button("Seleccionar todos", key="rep_todos",
                   on_click=lambda: st.session_state.__setitem__("rep_exps", list(exps_todos)))
        b_n.button("Quitar todos", key="rep_ninguno", on_click=lambda: st.session_state.__setitem__("rep_exps", []))
        fecha_rep = r2.date_input("Fecha del reporte", value=date.today(), key="rep_fecha", format="DD/MM/YYYY")
        ref_rep = st.text_input("Referencia de la nota (Word)", key="rep_ref",
                                value="Estado de avance de los relevamientos de redes de media tensión")
        sel_rep = rels[rels["expediente"].isin(exp_rep)].reset_index(drop=True)
        tr_rep = {f["expediente"]: tramos_de_fila(f, obs, margen)["segmentos"] for _, f in sel_rep.iterrows()}
        datos_rep = reportes.datos_reporte(red, sel_rep, tr_rep, obs, lg.cargar_universo(), fecha_rep)
        R_ = datos_rep["res"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Expedientes", R_["n_exp"], f"{R_['n_eje']} ejecutados por EDESA", delta_color="off")
        m2.metric("Km relevados", num(R_["km_unico"]), f"{num(R_['pct_km'])} % de la red MT", delta_color="off")
        m3.metric("Observaciones", R_["n_obs"])
        m4.metric("Localidades relevadas", f"{R_['n_loc_rel']} de {R_['n_loc']}", f"{num(R_['pct_loc'])} %", delta_color="off")
        if R_["km_suma"] - R_["km_unico"] > 0.05:
            st.caption(f"Algunos expedientes comparten tramos: la suma simple da {num(R_['km_suma'])} km y sin repetir {num(R_['km_unico'])} km. "
                       "El porcentaje usa el segundo valor.")
        if R_["fuera_de_lista"]:
            st.caption("Localidades de expedientes que no están en la lista de la provincia (no entran en el %): "
                       + ", ".join(R_["fuera_de_lista"]))
        if sel_rep.empty:
            st.warning("Elegí al menos un expediente.")
        else:
            try:
                x_bytes = reportes.excel_bytes(datos_rep)
                w_bytes = reportes.word_bytes(datos_rep, fecha_rep, ref_rep, lg.DATOS / "plantilla_nota.docx")
            except Exception as e_:                      # no debe romper el resto de la app
                st.error(f"No se pudo armar el reporte: {e_}")
            else:
                sello = fecha_rep.strftime("%Y-%m-%d")
                b1, b2 = st.columns(2)
                b1.download_button("Descargar Excel", x_bytes, file_name=f"Relevamientos_MT_{sello}.xlsx", key="dl_xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                b2.download_button("Descargar nota en Word", w_bytes, file_name=f"Nota_Directorio_relevamientos_{sello}.docx",
                                   key="dl_docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                st.divider()
                st.markdown("**Nota en Word solo de algunas localidades**")
                nombres_loc = list(datos_rep["loc"]["nombre"])
                loc_rep = st.multiselect("Localidades a incluir", nombres_loc, key="rep_locs",
                                         placeholder="Elegí una o más localidades")
                ref_loc = st.text_input("Referencia de la nota por localidades", key="rep_ref_loc",
                                        value="Estado de los relevamientos de redes de media tensión en localidades seleccionadas")
                if loc_rep:
                    try:
                        wl_bytes = reportes.word_localidades_bytes(datos_rep, loc_rep, fecha_rep, ref_loc,
                                                                   lg.DATOS / "plantilla_nota.docx")
                    except Exception as e_:
                        st.error(f"No se pudo armar la nota por localidades: {e_}")
                    else:
                        st.download_button("Descargar nota de las localidades elegidas", wl_bytes,
                                           file_name=f"Nota_localidades_{sello}.docx", key="dl_docx_loc",
                                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                else:
                    st.caption("Elegí al menos una localidad para generar esta nota.")
                st.caption("En el Excel, la hoja «Resumen» tiene los totales y las hojas «Relevamientos» y «Observaciones» tienen "
                           "filtros: al filtrar, la fila de totales (fila 4) suma solo lo visible. Si Excel muestra ceros, "
                           "hacé clic en «Habilitar edición».")

if PUEDE_EDITAR:
    with tab_copias:
        st.markdown("Una copia es un archivo .zip con los expedientes, las observaciones, las localidades, los informes PDF y las zonas dibujadas. "
                    "Se hace **sola** antes de cada cambio (se conservan las últimas 40 automáticas) y también cuando vos quieras.")
        if almacen.activo():
            st.info("Los datos viven en Supabase (la nube). Estas copias quedan en el servidor del programa y se pierden si se reinicia: "
                    "descargá una de vez en cuando y guardala en otro lugar. Una copia completa baja antes todos los PDF, y puede tardar.")
        else:
            st.info("Las copias quedan en la carpeta `datos/copias/` de esta misma PC. Si se rompe el disco se pierden con todo lo demás: "
                    "bajá una de vez en cuando y guardala en otro lugar (pendrive, unidad de red, nube).")
        cc1, cc2 = st.columns([1, 2])
        nota_copia = cc2.text_input("Nombre para la copia (opcional)", key="copia_nota", placeholder="ej. antes de cargar Iruya")
        cc1.markdown("<div style='height:1.8rem'></div>", unsafe_allow_html=True)
        if cc1.button("Hacer una copia ahora", type="primary", key="copia_hacer"):
            r_ = lg.hacer_copia(nota_copia or "manual")
            almacen.registrar(USUARIO.etiqueta, "Hizo una copia de seguridad", r_.name)
            st.session_state["aviso"] = f"Copia guardada: {r_.name}"
            st.rerun()
        copias = lg.listar_copias()
        if not copias:
            st.caption("Todavía no hay copias.")
        else:
            st.markdown(f"**{len(copias)} copias** (las más nuevas primero)")
            for f_ in copias[:15]:
                k1, k2, k3 = st.columns([3, 1, 1])
                k1.markdown(f"`{f_.name}` · {f_.stat().st_size / 1024:,.0f} KB".replace(",", "."))
                k2.download_button("Descargar", f_.read_bytes(), file_name=f_.name, mime="application/zip", key=f"cdl_{f_.name}")
                if k3.button("Restaurar", key=f"cres_{f_.name}"):
                    st.session_state["restaurar"] = f_.name
            if len(copias) > 15:
                st.caption(f"Se muestran las 15 más nuevas; las {len(copias) - 15} anteriores están en datos/copias/.")
            pendiente = st.session_state.get("restaurar")
            if pendiente:
                st.warning(f"Vas a volver los datos al estado de **{pendiente}**. Lo cargado después de esa copia se pierde "
                           "(antes se hace una copia de lo actual, así que se puede volver atrás).")
                y1, y2, _ = st.columns([1, 1, 3])
                if y1.button("Sí, restaurar", type="primary", key="cres_si"):
                    try:
                        previa = lg.restaurar_copia(pendiente)
                        almacen.registrar(USUARIO.etiqueta, "Restauró una copia", f"{pendiente} (lo anterior quedó en {previa.name})")
                        st.session_state["aviso"] = f"Datos restaurados. Lo anterior quedó en {previa.name}"
                    except (OSError, ValueError, KeyError) as e_:
                        st.session_state["aviso"] = f"No se pudo restaurar: {e_}"
                    st.session_state["restaurar"] = None
                    st.cache_data.clear()
                    st.rerun()
                if y2.button("Cancelar", key="cres_no"):
                    st.session_state["restaurar"] = None
                    st.rerun()

if ES_ADMIN:
    with tab_admin:
        panel_admin.mostrar(USUARIO, rels, obs)

with tab_info:
    st.markdown(
        """
**Qué se ve.** Los tramos de media tensión de la extracción de EDESA (13,2, 33 y 66 kV), todas las subestaciones y CT, y los expedientes de relevamiento.

**Código de colores.** Azul: línea relevada, con las adecuaciones pendientes de EDESA. Verde: EDESA ejecutó las adecuaciones. Gris: red sin relevar (más oscura y gruesa cuanto mayor la tensión). Naranja: puntos de observación (se muestran si los activás en el panel de la izquierda).

**Cómo se marca el estado.** Se lleva por expediente completo, no por observación: se hace clic en la línea (o se elige el expediente debajo del mapa) y se aprieta «Marcar como ejecutado por EDESA».

**Cómo se define la línea de un expediente.**

- *De un CT o subestación a otro:* se busca el camino más corto por la red de media tensión entre los dos puntos (y los intermedios que agregues) y se marcan los tramos que recorre. Las líneas se consideran empalmadas cuando están a menos de 12 m. Si el camino da una vuelta que no es, agregá un punto intermedio.
- *Por coordenadas:* igual, pero con puntos de latitud y longitud pegados a mano.
- *Informe PDF:* se lee el informe y se marca como relevada la red que queda dentro de una zona alrededor de las observaciones ubicadas (el margen se ajusta). Es la opción menos precisa, porque la zona puede incluir líneas vecinas. Si existe un dibujo en `datos/zonas/<expediente>.geojson`, se usa ese polígono.

**Distribuidores (morado, amarillo y anaranjado).** La extracción de EDESA no dice a qué distribuidor pertenece cada tramo. Se sacó del padrón de CT y la tabla de distribuidores: se ubica cada CT sobre la red y se unen todos los de un distribuidor (y su estación de cabecera, cuando se reconoce) por el camino más corto. Es una aproximación. Al buscar una localidad se resaltan los distribuidores que tienen CT en ella. Los CT que el padrón no trae con coordenadas no se pueden dibujar ni usar. Para actualizarlo hay que correr `preparar_distribuidores.py` con las planillas nuevas.

**Qué no incluye.** La red de baja tensión (380 V), la de 132 kV y las tensiones fuera de 13,2, 33 y 66 kV. Las observaciones en baja tensión no tienen tramo asociado.
        """)
