"""Guarda los datos cargados a mano en la nube (Supabase), para que no se pierdan y los vea todo el equipo.

No usa Streamlit: se puede probar por separado.

Cómo funciona:
- El programa sigue leyendo y escribiendo archivos en la carpeta datos/ (como siempre).
- Supabase es la copia que no se pierde. Después de cada escritura se suben los archivos que cambiaron
  y al arrancar (o cada tanto) se bajan los que cambiaron en la nube.
- Los PDF de informes se bajan solo cuando alguien los pide (para no bajar cientos de MB al despertar la app).
- Nunca se borra nada de la nube desde acá.
- Sin configuración de Supabase todo esto no hace nada y el programa funciona como antes, solo con archivos.

Qué se guarda: relevamientos.csv, observaciones.csv, informes/*.pdf y zonas/*. Además van a la nube el registro
de cambios (tabla «cambios») y la lista de usuarios (tabla «usuarios»). Ver supabase_setup.sql.
"""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

BUCKET = "datos"
EXTENSIONES = {".csv", ".pdf", ".geojson", ".json"}
SEGUNDOS_ENTRE_BAJADAS = 30
COL_CAMBIOS = ["fecha", "usuario", "accion", "detalle"]
LOG_LOCAL = "cambios_local.csv"


class ErrorNube(Exception):
    """No se pudo hablar con Supabase."""


_cfg: dict = {"url": "", "key": "", "datos": None}
BLOQUEO = threading.RLock()
_estado = {"error": "", "ultima_bajada": 0.0, "ultima_subida": 0.0}
_firmas: dict[str, dict] = {}          # nombre -> {mtime, size, sha, sync}; «sync» = contenido que se sabe igual en la nube


def iniciar(datos: Path, url: str = "", key: str = "") -> None:
    """datos: carpeta de trabajo. Con url y key se activa la nube."""
    _cfg.update(url=str(url or "").strip().rstrip("/"), key=str(key or "").strip(), datos=Path(datos))


def activo() -> bool:
    return bool(_cfg["url"] and _cfg["key"])


def estado() -> dict:
    return dict(_estado)


# ------------------------------------------------------------------ pedidos
def _encabezados(extra: dict | None = None) -> dict:
    h = {"apikey": _cfg["key"]}
    if _cfg["key"].startswith("eyJ"):            # claves antiguas (JWT) van también como Bearer; las nuevas, no
        h["Authorization"] = "Bearer " + _cfg["key"]
    h.update(extra or {})
    return h


def _pedir(metodo: str, ruta: str, headers: dict | None = None, timeout: int = 30, **kw) -> requests.Response:
    try:
        r = requests.request(metodo, _cfg["url"] + ruta, headers=_encabezados(headers), timeout=timeout, **kw)
    except requests.RequestException as e:
        raise ErrorNube(f"sin conexión con Supabase ({e.__class__.__name__})") from e
    if r.status_code >= 400:
        raise ErrorNube(f"Supabase respondió {r.status_code}: {r.text[:200]}")
    return r


# ----------------------------------------------------------------- archivos
def _datos() -> Path:
    return Path(_cfg["datos"])


def _nombres_locales() -> list[str]:
    d = _datos()
    out = [n for n in ("relevamientos.csv", "observaciones.csv") if (d / n).is_file()]
    for sub in ("informes", "zonas"):
        if (d / sub).is_dir():
            out += [f.relative_to(d).as_posix() for f in sorted((d / sub).rglob("*"))
                    if f.is_file() and f.suffix.lower() in EXTENSIONES]
    return out


def _sha_local(nombre: str) -> str:
    p = _datos() / nombre
    st = p.stat()
    f = _firmas.setdefault(nombre, {})
    if f.get("mtime") != st.st_mtime_ns or f.get("size") != st.st_size:
        f.update(mtime=st.st_mtime_ns, size=st.st_size, sha=hashlib.sha256(p.read_bytes()).hexdigest())
    return f["sha"]


def _diferido(nombre: str) -> bool:
    return nombre.startswith("informes/")


def _manifiesto() -> dict[str, dict]:
    filas = _pedir("GET", "/rest/v1/archivos", params={"select": "nombre,sha256,bytes,version", "limit": "5000"}).json()
    return {f["nombre"]: f for f in filas}


def _descargar(nombre: str) -> None:
    r = _pedir("GET", f"/storage/v1/object/{BUCKET}/{quote(nombre)}", timeout=120)
    p = _datos() / nombre
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(r.content)
    os.replace(tmp, p)
    st = p.stat()
    sha = hashlib.sha256(r.content).hexdigest()
    _firmas[nombre] = {"mtime": st.st_mtime_ns, "size": st.st_size, "sha": sha, "sync": sha}


def bajar(forzar: bool = False) -> None:
    """Trae de la nube lo que cambió (salvo los PDF, que se bajan al pedirlos). No falla: deja el error en estado()."""
    if not activo():
        return
    with BLOQUEO:
        ahora = time.time()
        if not forzar and ahora - _estado["ultima_bajada"] < SEGUNDOS_ENTRE_BAJADAS:
            return
        _estado["ultima_bajada"] = ahora
        try:
            for nombre, m in _manifiesto().items():
                if _diferido(nombre):
                    continue
                p = _datos() / nombre
                if p.exists():
                    sha = _sha_local(nombre)
                    f = _firmas[nombre]
                    if sha == m["sha256"]:
                        f["sync"] = sha
                        continue
                    if f.get("sync") is None:                       # primera vez que se ve: manda la nube, se guarda lo local aparte
                        shutil.copy2(p, p.with_name(p.name + ".copia_local"))
                    elif f["sync"] != sha:                          # hay cambios locales sin subir: no se pisan
                        continue
                _descargar(nombre)
            _estado["error"] = ""
        except ErrorNube as e:
            _estado["error"] = str(e)


def pendientes() -> list[str]:
    """Archivos con cambios que todavía no llegaron a la nube."""
    if not activo():
        return []
    with BLOQUEO:
        return [n for n in _nombres_locales() if _sha_local(n) != _firmas[n].get("sync")]


def subir() -> list[str]:
    """Sube a la nube los archivos que cambiaron aquí. No falla: deja el error en estado(). Devuelve lo subido."""
    hechos: list[str] = []
    if not activo():
        return hechos
    with BLOQUEO:
        try:
            remoto = _manifiesto()
            for nombre in _nombres_locales():
                sha = _sha_local(nombre)
                f = _firmas[nombre]
                m = remoto.get(nombre)
                if m and m["sha256"] == sha:
                    f["sync"] = sha
                    continue
                if f.get("sync") == sha:                           # no cambió aquí: lo de la nube es más nuevo
                    continue
                datos = (_datos() / nombre).read_bytes()
                tipo = "application/pdf" if nombre.endswith(".pdf") else "application/octet-stream"
                _pedir("POST", f"/storage/v1/object/{BUCKET}/{quote(nombre)}", data=datos, timeout=180,
                       headers={"x-upsert": "true", "Content-Type": tipo})
                _pedir("POST", "/rest/v1/archivos", params={"on_conflict": "nombre"},
                       json=[{"nombre": nombre, "sha256": sha, "bytes": len(datos),
                              "version": int(m["version"]) + 1 if m else 1,
                              "actualizado": datetime.utcnow().isoformat() + "Z"}],
                       headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
                f["sync"] = sha
                hechos.append(nombre)
            _estado["error"] = ""
            _estado["ultima_subida"] = time.time()
        except ErrorNube as e:
            _estado["error"] = str(e)
    return hechos


def traer(nombre: str) -> bool:
    """Baja un archivo si no está aquí. True si queda disponible."""
    p = _datos() / nombre
    if p.exists():
        return True
    if not activo():
        return False
    with BLOQUEO:
        try:
            _descargar(nombre)
            return True
        except ErrorNube:
            return False


def traer_todo() -> int:
    """Baja todos los archivos de la nube que no estén aquí (para una copia de seguridad completa). Devuelve cuántos."""
    if not activo():
        return 0
    n = 0
    with BLOQUEO:
        try:
            for nombre in _manifiesto():
                if not (_datos() / nombre).exists():
                    _descargar(nombre)
                    n += 1
            _estado["error"] = ""
        except ErrorNube as e:
            _estado["error"] = str(e)
    return n


def conectar(logica) -> None:
    """Enchufa este módulo a logica.py (ganchos de escritura y de lectura de PDF)."""
    logica.POST_ESCRITURA = subir if activo() else None
    logica.TRAER = traer if activo() else None
    logica.TRAER_TODO = traer_todo if activo() else None


# ------------------------------------------------------------------ cambios
def _log_local(fila: dict) -> None:
    ruta = _datos() / LOG_LOCAL
    nuevo = not ruta.exists()
    with open(ruta, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COL_CAMBIOS)
        if nuevo:
            w.writeheader()
        w.writerow(fila)


def registrar(usuario: str, accion: str, detalle: str = "") -> None:
    """Anota quién hizo qué. Va a la nube; si no se puede, queda en datos/cambios_local.csv. No falla nunca."""
    fila = {"usuario": str(usuario), "accion": str(accion), "detalle": str(detalle)[:500]}
    if activo():
        try:
            _pedir("POST", "/rest/v1/cambios", json=[fila], headers={"Prefer": "return=minimal"})
            return
        except ErrorNube:
            pass
    try:
        _log_local({"fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **fila})
    except OSError:
        pass


def leer_cambios(limite: int = 1000) -> list[dict]:
    """Registro de cambios, el más nuevo primero."""
    if activo():
        try:
            return _pedir("GET", "/rest/v1/cambios", params={"select": ",".join(COL_CAMBIOS), "order": "fecha.desc",
                                                              "limit": str(limite)}).json()
        except ErrorNube:
            pass
    ruta = _datos() / LOG_LOCAL
    if not ruta.exists():
        return []
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))[::-1][:limite]


# ----------------------------------------------------------------- usuarios
ROLES = ("lector", "editor", "admin")


def usuarios_listar() -> list[dict]:
    if not activo():
        return []
    return _pedir("GET", "/rest/v1/usuarios", params={"select": "email,nombre,rol,activo", "order": "email"}).json()


def usuarios_guardar(filas: list[dict]) -> None:
    """Deja la tabla de usuarios igual a la lista recibida (agrega, modifica y quita)."""
    limpias, vistos = [], set()
    for f in filas:
        email = str(f.get("email", "")).strip().lower()
        if "@" not in email or email in vistos:
            continue
        rol = str(f.get("rol", "lector")).strip().lower()
        limpias.append({"email": email, "nombre": str(f.get("nombre", "") or "").strip(),
                        "rol": rol if rol in ROLES else "lector", "activo": bool(f.get("activo", True))})
        vistos.add(email)
    actuales = {u["email"] for u in usuarios_listar()}
    if limpias:
        _pedir("POST", "/rest/v1/usuarios", params={"on_conflict": "email"}, json=limpias,
               headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    for email in actuales - vistos:
        _pedir("DELETE", "/rest/v1/usuarios", params={"email": "eq." + email})


# -------------------------------------------------------------- mantenimiento
def tamanos() -> dict | None:
    """Bytes usados por la base de datos y por los archivos en Supabase (None si no se pudo consultar)."""
    if not activo():
        return None
    try:
        return {k: int(_pedir("POST", f"/rest/v1/rpc/{fn}", json={}).json())
                for k, fn in (("base", "tamano_base"), ("archivos", "tamano_archivos"))}
    except (ErrorNube, ValueError, TypeError):
        return None


def ultimo_aviso() -> str | None:
    """Fecha del último aviso automático que mantiene activo el proyecto (None si nunca o no se pudo leer)."""
    if not activo():
        return None
    try:
        f = _pedir("GET", "/rest/v1/actividad", params={"select": "valor", "clave": "eq.ping"}).json()
        return f[0]["valor"] if f else None
    except (ErrorNube, KeyError, IndexError):
        return None
