"""Sube a Supabase los datos de esta PC (relevamientos, observaciones, informes PDF y zonas), o los baja.

    python subir_datos.py            sube lo que haya en datos/ y no esté en la nube
    python subir_datos.py --bajar    baja de la nube todo (incluidos los PDF) a datos/ de esta PC

Lee la dirección y la clave de .streamlit/secrets.toml (sección [supabase]) o de las variables de entorno
SUPABASE_URL y SUPABASE_KEY. Hace falta una sola vez para pasar los expedientes que ya tenías cargados.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import almacen

CARPETA = Path(__file__).resolve().parent


def credenciales() -> tuple[str, str]:
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    ruta = CARPETA / ".streamlit" / "secrets.toml"
    if (not url or not key) and ruta.exists():
        try:
            import tomllib
            with open(ruta, "rb") as f:
                sb = tomllib.load(f).get("supabase", {})
            url, key = url or sb.get("url", ""), key or sb.get("key", "")
        except Exception as e:
            print(f"No pude leer {ruta}: {e}")
    return url, key


def main() -> int:
    url, key = credenciales()
    if not url or not key:
        print("Faltan la dirección y la clave de Supabase. Completá la sección [supabase] de .streamlit/secrets.toml.")
        return 1
    almacen.iniciar(CARPETA / "datos", url, key)
    if "--bajar" in sys.argv:
        almacen.bajar(forzar=True)
        n = almacen.traer_todo()
        print(f"Listo. Se bajaron los datos y {n} archivos más." if not almacen.estado()["error"]
              else f"Error: {almacen.estado()['error']}")
    else:
        pendientes = almacen.pendientes()
        print("Para subir:", ", ".join(pendientes) if pendientes else "nada (ya está todo en la nube)")
        hechos = almacen.subir()
        if almacen.estado()["error"]:
            print("Error:", almacen.estado()["error"])
            return 1
        print(f"Listo. Se subieron {len(hechos)} archivos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
