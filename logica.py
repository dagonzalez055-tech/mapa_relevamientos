"""Lógica del mapa de relevamientos.

No usa Streamlit: así se puede probar por separado. La app (app.py) solo
arma la pantalla con lo que calcula este módulo.
"""
from __future__ import annotations

import json
import math
import time
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

CARPETA = Path(__file__).resolve().parent
DATOS = CARPETA / "datos"
ZONAS = DATOS / "zonas"
TENSIONES = (13.2, 33.0, 66.0)
INFORMES = DATOS / "informes"
ESTADOS_OBS = ("Pendiente", "Resuelta")
# Un expediente es "Relevado" (azul) hasta que EDESA ejecuta las adecuaciones ("Ejecutado por EDESA", verde).
RELEVADO, EJECUTADO = "Relevado", "Ejecutado por EDESA"
ESTADOS_REL = (RELEVADO, EJECUTADO)
COL_OBS = ["expediente", "n", "tipo", "estado", "fecha_resolucion", "comentario", "lat", "lon", "enlace"]
COL_REL = ["expediente", "localidad", "fecha_desde", "fecha_hasta", "informe", "estado", "modo",
           "definicion", "buffer_m", "fecha_ejecucion", "comentario"]
# Ganchos que conecta la nube (almacen.py): se llaman después de escribir, o para traer un archivo que falta. Sin nube no hacen nada.
POST_ESCRITURA = None       # función sin argumentos: sube a Supabase lo que cambió
TRAER = None                # función(nombre): baja un archivo (p. ej. «informes/70717.pdf») si no está en esta máquina
TRAER_TODO = None           # función sin argumentos: baja todos los archivos que faltan (para una copia completa)


def _post_escritura() -> None:
    if POST_ESCRITURA:
        POST_ESCRITURA()


# Marco aproximado de la provincia, para descartar coordenadas mal cargadas.
LIMITES = (-69.5, -61.5, -27.0, -21.5)  # lon mín, lon máx, lat mín, lat máx


# ----------------------------------------------------------------- lectura
def cargar_red(ruta: Path = DATOS / "red_mt.npz") -> dict:
    """Red de media tensión preparada por preparar_datos.py."""
    z = np.load(ruta, allow_pickle=False)
    return {k: z[k] for k in z.files}


def cargar_subestaciones(ruta: Path = DATOS / "subestaciones.csv") -> pd.DataFrame:
    df = pd.read_csv(ruta, encoding="utf-8-sig", dtype={"nombre": str, "zona": str, "tipo": str})
    df["nombre"] = df["nombre"].fillna("").astype(str)
    return df


def cargar_universo(ruta: Path = DATOS / "localidades.csv") -> pd.DataFrame:
    """Lista completa de localidades de la provincia (los 60 municipios), con o sin posición en el mapa.
    Columnas: nombre, alias, departamento, poblacion_2010, categoria (principal o menor), lat, lon, fuente.
    El archivo se puede editar: para sumar una localidad, agregar una fila; para ubicar una, completar lat y lon."""
    cols = ["nombre", "alias", "departamento", "poblacion_2010", "categoria", "lat", "lon", "fuente"]
    df = _leer_csv(ruta, cols)
    df["nombre"] = df["nombre"].astype(str).str.strip()
    df = df[df["nombre"] != ""].reset_index(drop=True)
    df["lat"], df["lon"] = a_numero(df["lat"]), a_numero(df["lon"])
    df["poblacion_2010"] = a_numero(df["poblacion_2010"])
    df["categoria"] = np.where(df["categoria"].astype(str).str.strip().str.lower().str.startswith("princ"), "principal", "menor")
    return df[cols]


def cargar_localidades(ruta: Path = DATOS / "localidades.csv") -> pd.DataFrame:
    """Localidades que tienen posición, para rotular el mapa y para el buscador."""
    df = cargar_universo(ruta)
    return df[coordenadas_validas(df)][["nombre", "lat", "lon", "categoria"]].reset_index(drop=True)


def norm_nombre(texto: str) -> str:
    """Nombre sin tildes, en minúsculas y sin signos, para comparar localidades."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def partir_localidades(texto: str) -> list[str]:
    """Un expediente puede abarcar varias localidades: «Chicoana; La Merced» o «Chicoana y El Carril»."""
    import re
    return [p.strip() for p in re.split(r"[;/,]|\sy\s", str(texto)) if p.strip()]


def estado_localidades(universo: pd.DataFrame, rels: pd.DataFrame):
    """Estado de cada localidad según los expedientes cargados.

    Devuelve (tabla, fuera_de_lista). La tabla tiene una fila por localidad con: expedientes, estado
    («Sin relevar», «Relevada · adecuaciones pendientes» o «Adecuaciones ejecutadas por EDESA»),
    fecha de relevamiento y fecha de solución. `fuera_de_lista` son las localidades escritas en los
    expedientes que no están en la lista de la provincia."""
    por_clave: dict[str, list[int]] = {}
    for i, r in rels.reset_index(drop=True).iterrows():
        for parte in partir_localidades(r["localidad"]):
            por_clave.setdefault(norm_nombre(parte), []).append(i)
    rr = rels.reset_index(drop=True)
    usadas, filas = set(), []
    for _, u in universo.iterrows():
        claves = {norm_nombre(u["nombre"])} | {norm_nombre(a) for a in str(u["alias"]).split(";") if a.strip()}
        idx = sorted({i for k in claves for i in por_clave.get(k, [])})
        usadas |= {k for k in claves if k in por_clave}
        sub = rr.iloc[idx]
        if sub.empty:
            estado, desde, sol = "Sin relevar", "", ""
        else:
            ejec = (sub["estado"] == EJECUTADO)
            estado = "Adecuaciones ejecutadas por EDESA" if ejec.all() else "Relevada · adecuaciones pendientes"
            desde = min((d for d in sub["fecha_desde"] if d), default="")
            sol = max((d for d in sub["fecha_ejecucion"] if d), default="") if ejec.all() else ""
        filas.append({"nombre": u["nombre"], "departamento": u["departamento"], "poblacion_2010": u["poblacion_2010"],
                      "expedientes": ", ".join(sub["expediente"]), "estado": estado, "fecha_relevamiento": desde,
                      "fecha_solucion": sol})
    fuera = []
    for r in rr.itertuples():
        for parte in partir_localidades(r.localidad):
            if norm_nombre(parte) not in usadas and parte not in fuera:
                fuera.append(parte)
    return pd.DataFrame(filas), fuera


def _leer_csv(ruta: Path, columnas: list[str]) -> pd.DataFrame:
    """Lee un CSV aceptando coma o punto y coma (Excel en español guarda con ';')."""
    if not ruta.exists():
        return pd.DataFrame({c: [] for c in columnas})
    try:
        df = pd.read_csv(ruta, sep=None, engine="python", encoding="utf-8-sig", dtype=str, keep_default_na=False)
    except Exception:
        df = pd.read_csv(ruta, sep=",", encoding="utf-8-sig", dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    for c in columnas:
        if c not in df.columns:
            df[c] = ""
    return df


def a_numero(serie: pd.Series) -> pd.Series:
    """Convierte a número aceptando coma decimal."""
    return pd.to_numeric(serie.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")


def normalizar_estado(valor: str) -> str:
    return "Resuelta" if str(valor).strip().lower().startswith("resuel") else "Pendiente"


def normalizar_estado_rel(valor: str) -> str:
    """«Realizado» (formato anterior) y cualquier otro valor cuentan como Relevado."""
    return EJECUTADO if str(valor).strip().lower().startswith("ejecut") else RELEVADO


def cargar_relevamientos(ruta: Path = DATOS / "relevamientos.csv") -> pd.DataFrame:
    df = _leer_csv(ruta, COL_REL)
    df["expediente"] = df["expediente"].astype(str).str.strip()
    df = df[df["expediente"] != ""].reset_index(drop=True)
    df["buffer_m"] = a_numero(df["buffer_m"])
    df["estado"] = df["estado"].map(normalizar_estado_rel)
    df["modo"] = df["modo"].replace("", "informe PDF")
    for c in ("localidad", "fecha_desde", "fecha_hasta", "informe", "definicion", "fecha_ejecucion", "comentario"):
        df[c] = df[c].astype(str)
    return df[COL_REL].copy()


def guardar_relevamientos(df: pd.DataFrame, ruta: Path = DATOS / "relevamientos.csv") -> None:
    copia_automatica(Path(ruta).parent)
    df = df[COL_REL].copy()
    df["expediente"] = df["expediente"].astype(str)
    df.to_csv(ruta, index=False, encoding="utf-8-sig")
    _post_escritura()


def agregar_relevamiento(fila: dict, ruta: Path = DATOS / "relevamientos.csv") -> None:
    """Agrega o reemplaza (mismo expediente) una fila del archivo de relevamientos."""
    df = cargar_relevamientos(ruta)
    df = df[df["expediente"] != str(fila["expediente"])]
    nueva = {c: fila.get(c, "") for c in COL_REL}
    nueva["expediente"] = str(nueva["expediente"]).strip()
    nueva["estado"] = normalizar_estado_rel(nueva["estado"] or RELEVADO)
    df = pd.concat([df, pd.DataFrame([nueva])], ignore_index=True)
    guardar_relevamientos(df, ruta)


def cambiar_estado(expediente: str, estado: str, fecha: str = "", comentario: str = "",
                   ruta: Path = DATOS / "relevamientos.csv") -> None:
    """Marca un expediente como Ejecutado por EDESA (o lo vuelve a Relevado)."""
    df = cargar_relevamientos(ruta)
    m = df["expediente"] == str(expediente)
    if not m.any():
        raise KeyError(expediente)
    df.loc[m, "estado"] = normalizar_estado_rel(estado)
    df.loc[m, "fecha_ejecucion"] = fecha if normalizar_estado_rel(estado) == EJECUTADO else ""
    if comentario:
        df.loc[m, "comentario"] = comentario
    guardar_relevamientos(df, ruta)


def eliminar_relevamiento(expediente: str, ruta: Path = DATOS / "relevamientos.csv") -> None:
    df = cargar_relevamientos(ruta)
    guardar_relevamientos(df[df["expediente"] != str(expediente)], ruta)


# ---------------------------------------------------------------- informes
def _nombre_archivo(expediente: str) -> str:
    return "".join(c for c in str(expediente) if c.isalnum() or c in "-_") or "sin_numero"


def ruta_informe(expediente: str, carpeta: Path = INFORMES) -> Path | None:
    p = Path(carpeta) / f"{_nombre_archivo(expediente)}.pdf"
    if not p.exists() and TRAER and Path(carpeta) == INFORMES:
        TRAER(f"informes/{p.name}")                      # con nube, los PDF se bajan cuando se piden
    return p if p.exists() else None


def guardar_informe(expediente: str, contenido: bytes, carpeta: Path = INFORMES) -> Path:
    Path(carpeta).mkdir(parents=True, exist_ok=True)
    p = Path(carpeta) / f"{_nombre_archivo(expediente)}.pdf"
    if p.exists():
        copia_automatica(Path(carpeta).parent)
    p.write_bytes(contenido)
    _post_escritura()
    return p


# ------------------------------------------------- definición de la línea
def definicion_recorrido(puntos: list[dict]) -> str:
    """puntos: [{'etiqueta': 'CT 123', 'lon': -65.5, 'lat': -25.1}, ...]"""
    return json.dumps({"tipo": "recorrido", "puntos": [
        {"etiqueta": str(p.get("etiqueta", "")), "lon": round(float(p["lon"]), 7), "lat": round(float(p["lat"]), 7)}
        for p in puntos]}, ensure_ascii=False)


def definicion_zona() -> str:
    return json.dumps({"tipo": "zona"})


def leer_definicion(texto: str) -> dict:
    try:
        d = json.loads(texto) if str(texto).strip() else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) and d.get("tipo") in ("recorrido", "zona") else {"tipo": "zona"}


def tramos_de(fila, red: dict, grafo, obs: pd.DataFrame, buffer_defecto: float = 600.0) -> dict:
    """Tramos de la red que corresponden a un relevamiento.

    Devuelve {'segmentos', 'ok', 'mensaje', 'origen', 'linea', 'poligonos'}.
    `linea` es el recorrido (solo para definiciones tipo recorrido) y `poligonos` la zona."""
    d = leer_definicion(fila["definicion"])
    if d["tipo"] == "recorrido":
        import rutas
        pts = [(p["lon"], p["lat"]) for p in d.get("puntos", [])]
        r = rutas.camino(grafo, pts)
        return {"segmentos": r["segmentos"], "ok": r["ok"], "mensaje": r["mensaje"],
                "origen": f"recorrido por {len(pts)} puntos", "linea": r["linea"], "poligonos": []}
    buf = fila["buffer_m"] if pd.notna(fila["buffer_m"]) else buffer_defecto
    polis, origen = zona_de(str(fila["expediente"]), obs, float(buf))
    mascara = np.zeros(len(red["cx"]), dtype=bool)
    for poli in polis:
        mascara |= dentro(red["cx"], red["cy"], poli)
    ok = bool(mascara.any())
    return {"segmentos": np.flatnonzero(mascara).astype(np.int32), "ok": ok,
            "mensaje": "" if ok else "No hay observaciones ubicadas ni dibujo de zona para este expediente.",
            "origen": origen, "linea": [], "poligonos": polis}


def asignar_relevamientos(red: dict, tramos: list[dict]) -> np.ndarray:
    """Para cada tramo de la red, el índice del relevamiento que lo contiene (-1 si ninguno).
    Si dos relevamientos comparten un tramo, vale el primero de la lista."""
    asig = np.full(len(red["cx"]), -1, dtype=np.int32)
    for i, t in enumerate(tramos):
        s = np.asarray(t["segmentos"], dtype=np.int64)
        s = s[asig[s] == -1] if len(s) else s
        asig[s] = i
    return asig


def caja_segmentos(red: dict, segmentos) -> tuple[float, float, float, float]:
    off = red["off"]
    xs, ys = [], []
    for s in np.asarray(segmentos, dtype=np.int64):
        xs.append(red["lon"][off[s]:off[s + 1]])
        ys.append(red["lat"][off[s]:off[s + 1]])
    if not xs:
        raise ValueError("sin tramos")
    xs, ys = np.concatenate(xs), np.concatenate(ys)
    return float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())


def leer_puntos(texto: str) -> tuple[list[tuple[float, float]], list[str]]:
    """Lee coordenadas pegadas, una por línea: «-25.0782, -65.5065» (acepta coma decimal).
    Devuelve (lista de (lon, lat), errores)."""
    import re
    puntos, errores = [], []
    for k, linea in enumerate(str(texto).splitlines(), 1):
        if not linea.strip():
            continue
        nums = re.findall(r"-?\d+(?:[.,]\d+)?", linea)
        if len(nums) != 2:
            errores.append(f"Línea {k}: «{linea.strip()[:40]}» no tiene exactamente dos números (latitud y longitud).")
            continue
        lat, lon = (float(n.replace(",", ".")) for n in nums)
        x0, x1, y0, y1 = LIMITES
        if not (y0 <= lat <= y1 and x0 <= lon <= x1):
            errores.append(f"Línea {k}: {lat}, {lon} queda fuera de la provincia (¿latitud y longitud invertidas?).")
            continue
        puntos.append((lon, lat))
    return puntos, errores


def cargar_observaciones(ruta: Path = DATOS / "observaciones.csv") -> pd.DataFrame:
    df = _leer_csv(ruta, COL_OBS)
    df["expediente"] = df["expediente"].astype(str).str.strip()
    df["n"] = a_numero(df["n"])
    df = df[df["n"].notna() & (df["expediente"] != "")].copy()
    df["n"] = df["n"].astype(int)
    df["lat"] = a_numero(df["lat"])
    df["lon"] = a_numero(df["lon"])
    df["estado"] = df["estado"].map(normalizar_estado)
    for c in ("tipo", "fecha_resolucion", "comentario", "enlace"):
        df[c] = df[c].astype(str)
    return df[COL_OBS + [c for c in df.columns if c not in COL_OBS]].sort_values(["expediente", "n"]).reset_index(drop=True)


def guardar_observaciones(df: pd.DataFrame, ruta: Path = DATOS / "observaciones.csv") -> None:
    copia_automatica(Path(ruta).parent)
    df = df.sort_values(["expediente", "n"]).reset_index(drop=True)
    df.to_csv(ruta, index=False, encoding="utf-8-sig")
    _post_escritura()


def coordenadas_validas(df: pd.DataFrame) -> pd.Series:
    x0, x1, y0, y1 = LIMITES
    return df["lat"].between(y0, y1) & df["lon"].between(x0, x1)


# ------------------------------------------------------------------ zonas
def _circulo(lon: float, lat: float, radio_m: float, n: int = 24):
    for k in range(n):
        a = 2 * math.pi * k / n
        yield (lon + radio_m * math.sin(a) / (111320 * math.cos(math.radians(lat))),
               lat + radio_m * math.cos(a) / 111320)


def casco_convexo(puntos):
    """Casco convexo (monotone chain). Devuelve la lista de vértices."""
    p = sorted(set(puntos))
    if len(p) <= 2:
        return p

    def cruz(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    inf = []
    for q in p:
        while len(inf) >= 2 and cruz(inf[-2], inf[-1], q) <= 0:
            inf.pop()
        inf.append(q)
    sup = []
    for q in reversed(p):
        while len(sup) >= 2 and cruz(sup[-2], sup[-1], q) <= 0:
            sup.pop()
        sup.append(q)
    return inf[:-1] + sup[:-1]


def zona_desde_observaciones(puntos, buffer_m: float):
    """Zona provisoria: casco convexo de las observaciones más un margen."""
    pts = []
    for lon, lat in puntos:
        pts.extend(_circulo(lon, lat, buffer_m))
    return [casco_convexo(pts)] if pts else []


def zona_desde_geojson(ruta: Path):
    """Polígonos (anillos exteriores) de un GeoJSON dibujado a mano. [] si no sirve."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    geoms = []

    def recorrer(o):
        t = o.get("type")
        if t == "FeatureCollection":
            for f in o.get("features", []):
                recorrer(f)
        elif t == "Feature":
            recorrer(o.get("geometry") or {})
        elif t == "GeometryCollection":
            for g in o.get("geometries", []):
                recorrer(g)
        elif t == "Polygon":
            geoms.append(o["coordinates"][:1])
        elif t == "MultiPolygon":
            for poly in o["coordinates"]:
                geoms.append(poly[:1])

    recorrer(datos)
    return [[(float(x), float(y)) for x, y, *_ in anillo] for g in geoms for anillo in g if len(anillo) >= 3]


def zona_de(expediente: str, obs: pd.DataFrame, buffer_m: float, carpeta_zonas: Path = ZONAS):
    """Devuelve (polígonos, origen). Si existe zonas/<expediente>.geojson se usa ese
    dibujo; si no, se arma una zona provisoria con las observaciones ubicadas."""
    archivo = Path(carpeta_zonas) / f"{expediente}.geojson"
    if archivo.exists():
        polis = zona_desde_geojson(archivo)
        if polis:
            return polis, "dibujo (GeoJSON)"
    sub = obs[(obs["expediente"] == expediente)]
    sub = sub[coordenadas_validas(sub)]
    if sub.empty:
        return [], "sin zona"
    polis = zona_desde_observaciones(list(zip(sub["lon"], sub["lat"])), buffer_m)
    return polis, f"observaciones + {buffer_m:g} m"


def dentro(px: np.ndarray, py: np.ndarray, poligono) -> np.ndarray:
    """Qué puntos caen dentro del polígono (método del rayo, vectorizado)."""
    res = np.zeros(px.shape, dtype=bool)
    x1, y1 = poligono[-1]
    for x2, y2 in poligono:
        cruza = (y1 > py) != (y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2 - x1) * (py - y1) / (y2 - y1) + x1
        res ^= cruza & (px < xint)
        x1, y1 = x2, y2
    return res


def asignar(red: dict, zonas: list) -> np.ndarray:
    """Para cada tramo, el número de la zona que lo contiene (-1 si ninguna).
    Un tramo cuenta como relevado si su punto medio cae dentro de la zona.
    `zonas` es una lista de listas de polígonos, una por relevamiento."""
    asig = np.full(len(red["cx"]), -1, dtype=np.int32)
    for i, polis in enumerate(zonas):
        for poli in polis:
            m = dentro(red["cx"], red["cy"], poli) & (asig == -1)
            asig[m] = i
    return asig


# ---------------------------------------------------------------- vistas
def caja(polis) -> tuple[float, float, float, float]:
    xs = [p[0] for poli in polis for p in poli]
    ys = [p[1] for poli in polis for p in poli]
    return min(xs), max(xs), min(ys), max(ys)


def vista_para(x0: float, x1: float, y0: float, y1: float, ancho_px: int = 1100, alto_px: int = 650):
    """(lat, lon, zoom) para que la caja entre en la pantalla (mapa web, teselas de 512 px)."""
    lat = (y0 + y1) / 2
    dx = max(x1 - x0, 1e-4)
    dy = max(y1 - y0, 1e-4)
    z_x = math.log2(ancho_px * 360 / (dx * 512))
    z_y = math.log2(alto_px * 360 * math.cos(math.radians(lat)) / (dy * 512))
    return lat, (x0 + x1) / 2, max(3.0, min(z_x, z_y) - 0.25)


# ------------------------------------------------------------------ cifras
def km(red: dict, mascara: np.ndarray) -> float:
    return float(red["largo_m"][mascara].sum()) / 1000


def km_por_tension(red: dict, mascara: np.ndarray | None = None) -> dict:
    if mascara is None:
        mascara = np.ones(len(red["tension"]), dtype=bool)
    return {t: km(red, mascara & (red["tension"] == np.float32(t))) for t in TENSIONES}


def tabla_por_zona(red: dict, relevado: np.ndarray, ejecutado: np.ndarray | None = None) -> pd.DataFrame:
    """Por zona de EDESA: km de red, km relevados (incluye los ejecutados) y km con adecuaciones ejecutadas."""
    if ejecutado is None:
        ejecutado = np.zeros_like(relevado)
    filas = []
    for i, nombre in enumerate(red["areas"]):
        en_zona = red["area"] == i
        filas.append({"Zona de EDESA": str(nombre),
                      "Red MT (km)": km(red, en_zona),
                      "Relevada (km)": km(red, en_zona & relevado),
                      "Ejecutada por EDESA (km)": km(red, en_zona & ejecutado)})
    df = pd.DataFrame(filas)
    df["% relevado"] = np.where(df["Red MT (km)"] > 0, df["Relevada (km)"] / df["Red MT (km)"] * 100, 0.0)
    return df.sort_values("Red MT (km)", ascending=False).reset_index(drop=True)


# ------------------------------------------------------ copias de seguridad
# Una copia es un .zip con lo que se carga a mano (relevamientos, observaciones, localidades, informes y zonas).
# La red y las subestaciones no se copian: no cambian y están en la carpeta del programa.
CARPETA_COPIAS = "copias"
MAX_COPIAS_AUTOMATICAS = 40
SEGUNDOS_ENTRE_COPIAS = 120          # varias escrituras seguidas (una carga) comparten una sola copia


def _archivos_a_copiar(datos: Path):
    for nombre in ("relevamientos.csv", "observaciones.csv", "localidades.csv"):
        if (datos / nombre).exists():
            yield datos / nombre
    for sub in ("informes", "zonas"):
        if (datos / sub).is_dir():
            yield from (f for f in sorted((datos / sub).rglob("*")) if f.is_file())


def hacer_copia(motivo: str = "manual", datos: Path = DATOS, completa: bool = True) -> Path:
    """Guarda un .zip con los datos cargados en datos/copias/. Devuelve su ruta.

    Con nube, una copia completa baja antes los PDF que no estén en esta máquina."""
    datos = Path(datos)
    if completa and TRAER_TODO:
        TRAER_TODO()
    destino = datos / CARPETA_COPIAS
    destino.mkdir(parents=True, exist_ok=True)
    motivo = "".join(c for c in motivo if c.isalnum() or c in "-_") or "manual"
    ruta = destino / f"copia_{datetime.now():%Y-%m-%d_%H%M%S}_{motivo}.zip"
    n = 1
    while ruta.exists():                  # dos copias en el mismo segundo
        n += 1
        ruta = destino / f"copia_{datetime.now():%Y-%m-%d_%H%M%S}_{motivo}_{n}.zip"
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _archivos_a_copiar(datos):
            z.write(f, f.relative_to(datos).as_posix())
    return ruta


def listar_copias(datos: Path = DATOS) -> list[Path]:
    """Copias existentes, la más nueva primero."""
    carpeta = Path(datos) / CARPETA_COPIAS
    return sorted(carpeta.glob("copia_*.zip"), reverse=True) if carpeta.is_dir() else []


def copia_automatica(datos: Path = DATOS) -> Path | None:
    """Copia previa a un cambio. No falla nunca (si no se puede copiar, el cambio sigue) y no repite copias seguidas."""
    try:
        datos = Path(datos)
        if not (datos / "relevamientos.csv").exists() and not (datos / "observaciones.csv").exists():
            return None
        recientes = [f for f in listar_copias(datos) if f.stat().st_mtime > time.time() - SEGUNDOS_ENTRE_COPIAS]
        if recientes:
            return None
        ruta = hacer_copia("auto", datos, completa=False)
        autos = [f for f in listar_copias(datos) if f.stem.endswith("_auto") or "_auto_" in f.stem]
        for vieja in autos[MAX_COPIAS_AUTOMATICAS:]:
            vieja.unlink(missing_ok=True)
        return ruta
    except OSError:
        return None


def restaurar_copia(nombre: str, datos: Path = DATOS) -> Path:
    """Vuelve los datos al estado de una copia. Antes hace otra copia de lo que hay, por si hubo un error."""
    datos = Path(datos)
    origen = datos / CARPETA_COPIAS / Path(nombre).name
    if not origen.exists():
        raise FileNotFoundError(nombre)
    previa = hacer_copia("antes-de-restaurar", datos)
    base = datos.resolve()
    with zipfile.ZipFile(origen) as z:
        miembros = [m for m in z.namelist() if not m.endswith("/")]
        for m in miembros:                                   # solo archivos que caigan dentro de datos/
            if not (datos / m).resolve().is_relative_to(base):
                raise ValueError(f"La copia trae una ruta no permitida: {m}")
        for sub in ("informes", "zonas"):                    # lo que no estaba en la copia no debe quedar
            for f in list((datos / sub).rglob("*")) if (datos / sub).is_dir() else []:
                if f.is_file():
                    f.unlink()
        for m in miembros:
            destino = datos / m
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(z.read(m))
    _post_escritura()
    return previa


# ------------------------------------------------------------ distribuidores
# Los genera preparar_distribuidores.py a partir del padrón de CT y la tabla de distribuidores de EDESA.
# El padrón de EDESA escribe la localidad de muchas formas («JVG Las Lajitas», «ORAN Embarcacion», «GA_Iruya»,
# «R. de la FRONTERA»). Estas son las equivalencias para llevarlas a los nombres de localidades.csv.
_ALIAS_PADRON = {
    "ap saravia": "Apolinario Saravia", "r de la frontera": "Rosario de la Frontera",
    "r de la frontera rn34": "Rosario de la Frontera", "oran san ramon de la nueva": "Orán",
    "sta v este": "Santa Victoria Este", "sta victoria oeste": "Santa Victoria Oeste",
    "mosconi rn34s": "General Mosconi",
}


def _claves_padron(texto: str) -> list[str]:
    """Formas de escribir una localidad del padrón, de la más literal a la más limpia."""
    import re as _re
    n = norm_nombre(texto)
    claves = [n]
    sin = _re.sub(r"^(jvg|oran|salta|tartagal|metan|cafayate|guemes|ga)\s+", "", n)
    sin = _re.sub(r"\s+\d+$", "", sin)
    if sin and sin != n:
        claves.append(sin)
    return claves


def localidad_del_padron(texto: str, universo: pd.DataFrame) -> str:
    """Nombre de la localidad de la lista (localidades.csv) a la que corresponde un texto del padrón.
    Si no se reconoce, devuelve el texto tal cual (queda como una localidad más para el buscador)."""
    if not hasattr(localidad_del_padron, "_cache") or localidad_del_padron._cache[0] is not universo:
        claves = {}
        for _, u in universo.iterrows():
            for k in [u["nombre"]] + [a for a in str(u["alias"]).split(";") if a.strip()]:
                claves[norm_nombre(k)] = u["nombre"]
        localidad_del_padron._cache = (universo, claves)
    claves = localidad_del_padron._cache[1]
    for c in _claves_padron(texto):
        if c in claves:
            return claves[c]
        if c in _ALIAS_PADRON and norm_nombre(_ALIAS_PADRON[c]) in claves:
            return claves[norm_nombre(_ALIAS_PADRON[c])]
    return str(texto).strip()


def cargar_distribuidores(datos: Path = DATOS) -> pd.DataFrame:
    ruta = Path(datos) / "distribuidores.csv"
    if not ruta.exists():
        return pd.DataFrame(columns=["id", "nombre", "distrito", "cod_padre", "tipo_origen", "estacion", "barra", "tension",
                                     "n_ct", "n_ct_ubicados", "n_ct_en_red", "km", "tramos", "cabecera_ubicada", "localidades"])
    df = pd.read_csv(ruta, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    for c in ("tension", "km"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("n_ct", "n_ct_ubicados", "n_ct_en_red", "tramos"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df


def cargar_ct_distribuidor(universo: pd.DataFrame | None = None, datos: Path = DATOS) -> pd.DataFrame:
    """Cada CT con su distribuidor y su localidad (columna `localidad_mapa`, ya llevada a los nombres de la lista)."""
    ruta = Path(datos) / "ct_distribuidor.csv"
    if not ruta.exists():
        return pd.DataFrame(columns=["ct", "id_distribuidor", "localidad", "distrito", "lat", "lon", "nuevo", "localidad_mapa"])
    df = pd.read_csv(ruta, encoding="utf-8-sig", dtype={"ct": str, "id_distribuidor": str, "localidad": str, "distrito": str},
                     keep_default_na=False)
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    universo = cargar_universo() if universo is None else universo
    df["localidad_mapa"] = [localidad_del_padron(x, universo) if str(x).strip() else "" for x in df["localidad"]]
    return df


def cargar_tramos_distribuidores(datos: Path = DATOS) -> dict:
    """{id de distribuidor: array con los tramos de la red que lo componen}."""
    ruta = Path(datos) / "dist_tramos.npz"
    if not ruta.exists():
        return {}
    z = np.load(ruta, allow_pickle=False)
    ids, off, segs = z["ids"], z["off"], z["segs"]
    return {str(i): segs[off[k]:off[k + 1]].astype(np.int64) for k, i in enumerate(ids)}


def distribuidores_de_localidad(ct: pd.DataFrame, nombre: str) -> pd.DataFrame:
    """Distribuidores que tienen CT en la localidad, con la cantidad de CT de cada uno (de mayor a menor)."""
    sel = ct[ct["localidad_mapa"].map(norm_nombre) == norm_nombre(nombre)]
    return sel.groupby("id_distribuidor").size().sort_values(ascending=False).rename("n_ct_localidad").reset_index()


def tramos_cerca(red: dict, segmentos, lon, lat, radio_m: float = 800.0) -> np.ndarray:
    """De una lista de tramos, los que pasan a menos de `radio_m` de alguno de los puntos (lon, lat)."""
    from scipy.spatial import cKDTree
    segmentos = np.asarray(segmentos, dtype=np.int64)
    lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    ok = np.isfinite(lon) & np.isfinite(lat)
    if len(segmentos) == 0 or not ok.any():
        return np.empty(0, dtype=np.int64)
    off = red["off"]
    k = 111320.0
    kx = k * math.cos(math.radians(24.5))
    arbol = cKDTree(np.column_stack((lon[ok] * kx, lat[ok] * k)))
    largos = (off[segmentos + 1] - off[segmentos]).astype(np.int64)
    idx = np.concatenate([np.arange(off[s], off[s + 1]) for s in segmentos])
    pts = np.column_stack((red["lon"][idx] * kx, red["lat"][idx] * k))
    d, _ = arbol.query(pts)
    dueno = np.repeat(np.arange(len(segmentos)), largos)
    cerca = np.bincount(dueno, weights=(d <= radio_m).astype(float), minlength=len(segmentos)) > 0
    return segmentos[cerca]
