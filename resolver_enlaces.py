"""Completa la latitud y la longitud de datos/observaciones.csv a partir de los
enlaces cortos de Google Maps (https://maps.app.goo.gl/...).

Uso (con internet):
    python resolver_enlaces.py

Solo procesa las observaciones que tienen enlace y todavía no tienen
coordenadas. Espera un poco entre consulta y consulta para no saturar a Google
y guarda el avance cada pocas observaciones, así que se puede cortar y volver
a correr sin perder lo hecho.
"""
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urljoin

import logica as lg

PAUSA_S = 1.5
CABECERAS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
             "Accept-Language": "es-AR,es;q=0.9"}
_RE_PIN = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")          # ubicación del pin
_RE_ARROBA = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")           # centro del mapa
_RE_Q = re.compile(r"[?&](?:q|ll|query)=(-?\d+\.\d+),\+?(-?\d+\.\d+)")


def extraer_coordenadas(url: str):
    """Busca (lat, lon) dentro de una dirección de Google Maps. None si no hay."""
    texto = unquote(url)
    for patron in (_RE_PIN, _RE_Q, _RE_ARROBA):
        m = patron.search(texto)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def resolver(url: str, intentos: int = 4, saltos: int = 4):
    """Sigue las redirecciones a mano y devuelve (lat, lon) o None."""
    opener = urllib.request.build_opener(_SinRedireccion)
    actual = url
    for _ in range(saltos):
        for intento in range(intentos):
            try:
                opener.open(urllib.request.Request(actual, headers=CABECERAS), timeout=20)
                return extraer_coordenadas(actual)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    destino = e.headers.get("Location", "")
                    c = extraer_coordenadas(destino)
                    if c:
                        return c
                    if not destino:
                        return None
                    actual = urljoin(actual, destino)
                    break
                if e.code == 429:
                    espera = 30 * (intento + 1)
                    print(f"    Google pidió esperar; pausa de {espera} s...")
                    time.sleep(espera)
                    continue
                return None
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(3)
                continue
        else:
            return None
    return None


def main():
    obs = lg.cargar_observaciones()
    pendientes = obs[(obs["enlace"].str.startswith("http")) & (obs["lat"].isna() | obs["lon"].isna())]
    if pendientes.empty:
        print("No hay observaciones con enlace y sin coordenadas. Nada que hacer.")
        return
    print(f"Observaciones por resolver: {len(pendientes)}")
    hechas = fallidas = 0
    for idx, fila in pendientes.iterrows():
        c = resolver(fila["enlace"].strip())
        if c:
            obs.loc[idx, "lat"], obs.loc[idx, "lon"] = c
            hechas += 1
            print(f"  Expte. {fila['expediente']} obs. {fila['n']}: {c[0]:.6f}, {c[1]:.6f}")
        else:
            fallidas += 1
            print(f"  Expte. {fila['expediente']} obs. {fila['n']}: no pude resolver {fila['enlace']}")
        if (hechas + fallidas) % 5 == 0:
            lg.guardar_observaciones(obs)
        time.sleep(PAUSA_S)
    lg.guardar_observaciones(obs)
    print(f"Listo: {hechas} resueltas, {fallidas} sin resolver. Datos guardados en {lg.DATOS / 'observaciones.csv'}")
    if fallidas:
        print("Las que fallaron se pueden cargar a mano en la pestaña Observaciones de la app, o volver a correr este script.")


if __name__ == "__main__":
    sys.exit(main())
