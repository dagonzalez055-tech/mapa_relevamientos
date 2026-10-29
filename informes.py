"""Lectura de los informes de relevamiento en PDF.

Saca del informe: la localidad, las fechas del relevamiento, la referencia del
informe (IF ... GEE ...) y la lista de observaciones con su enlace de Google Maps.
El número de expediente NO figura en el informe: se completa a mano.

Lo que se lee es un borrador: la app lo muestra para revisarlo antes de guardar.
"""
from __future__ import annotations

import io
import re

MESES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
     "octubre", "noviembre", "diciembre"], 1)}
MESES["setiembre"] = 9


def texto_pdf(contenido: bytes) -> str:
    from pypdf import PdfReader
    lector = PdfReader(io.BytesIO(contenido))
    return "\n".join((p.extract_text() or "") for p in lector.pages)


def _normalizar_tipo(d: str) -> str:
    """Pequeñas correcciones de ortografía frecuentes en los informes."""
    d = re.sub(r"\s+", " ", d).strip()
    d = re.sub(r"\bel CT\b", "del CT", d)
    d = d.replace("Ámbito", "ámbito")
    d = re.sub(r"\bPat\b", "PAT", d)
    d = re.sub(r"\bremplazo\b", "reemplazo", d)
    d = re.sub(r" y reemplazo LBT convencional", " y reemplazo de LBT convencional", d)
    d = d.replace(" y Sanear", " y sanear")
    d = re.sub(r"^(PAT cortada) sanear CT", r"\1, sanear CT", d, flags=re.I)
    return d[:1].upper() + d[1:] if d else d


def _observaciones_numeradas(lineas: list[str]) -> list[dict]:
    """Formato de anexo: «1. Sanear CT» y, debajo, el enlace de Google Maps (una observación por página)."""
    res = []
    i = 0
    while i < len(lineas):
        m = re.match(r"(\d{1,3})\s*[.)\-]\s+(\S.*)$", lineas[i])
        if not m:
            i += 1
            continue
        tipo, j = m.group(2), i + 1
        while j < len(lineas) and not lineas[j].lower().startswith("http") and j - i <= 3 \
                and not re.match(r"\d{1,3}\s*[.)\-]\s+\S", lineas[j]):
            if lineas[j]:
                tipo += " " + lineas[j]
            j += 1
        if j < len(lineas) and lineas[j].lower().startswith("http"):         # solo cuenta si tiene enlace: evita falsos positivos
            enlace = lineas[j].split()[0]
            k = j + 1
            while k < len(lineas) and lineas[k] and " " not in lineas[k] and len(enlace) < 40 \
                    and not re.match(r"\d{1,3}\s*[.)\-]\s+\S", lineas[k]):
                enlace += lineas[k]
                k += 1
            res.append({"n": int(m.group(1)), "tipo": _normalizar_tipo(tipo), "enlace": enlace})
            i = max(k, j + 1)
        else:
            i += 1
    return res


def observaciones(texto: str) -> list[dict]:
    """[{'n': 1, 'tipo': 'Poda en LMT', 'enlace': 'https://maps.app.goo.gl/...'}, ...]"""
    lineas = [l.strip() for l in texto.splitlines()]
    if not any(re.match(r"Observaci[oó]n\s+\d+", l, re.I) for l in lineas):
        return _observaciones_numeradas(lineas)
    res = []
    i = 0
    while i < len(lineas):
        m = re.match(r"Observaci[oó]n\s+(\d+)\s*[:.\-]?\s*(.*)$", lineas[i], re.I)
        if not m:
            i += 1
            continue
        n, tipo = int(m.group(1)), m.group(2)
        enlace = ""
        j = i + 1
        # la descripción puede seguir en la línea siguiente, antes del enlace
        while j < len(lineas) and lineas[j] and not lineas[j].lower().startswith("http") \
                and not re.match(r"Observaci[oó]n\s+\d+", lineas[j], re.I) and j - i <= 2:
            tipo += " " + lineas[j]
            j += 1
        if j < len(lineas) and lineas[j].lower().startswith("http"):
            enlace = lineas[j].split()[0]
            k = j + 1       # un enlace cortado por el fin de línea sigue en la línea de abajo
            while k < len(lineas) and lineas[k] and " " not in lineas[k] and "." not in lineas[k][:3] \
                    and not re.match(r"Observaci[oó]n", lineas[k], re.I) and len(enlace) < 40:
                enlace += lineas[k]
                k += 1
            i = max(k, j + 1)
        else:
            i = max(j, i + 1)
        res.append({"n": n, "tipo": _normalizar_tipo(tipo), "enlace": enlace})
    return res


def _fecha(d: int, mes: str, anio: int) -> str:
    return f"{anio:04d}-{MESES[mes.lower()]:02d}-{d:02d}"


def datos_generales(texto: str) -> dict:
    """Localidad, fechas del relevamiento y referencia del informe (lo que se pueda leer)."""
    plano = re.sub(r"\s+", " ", texto)
    out = {"localidad": "", "fecha_desde": "", "fecha_hasta": "", "informe": "", "expediente": "", "fecha_firma": ""}
    m = re.search(r"localidad de ([A-ZÁÉÍÓÚÑ][^,.;:]*?)(?= con motivo| con el|,|\.|;| en el marco|$)", plano)
    if not m:
        m = re.search(r"Municipio de ([A-ZÁÉÍÓÚÑ][^,.;:]*?)(?=,|\.|;| con | para |$)", plano)
    if m:
        out["localidad"] = m.group(1).strip()
    if not out["localidad"]:                     # encabezado del anexo: «... Relevamiento Media y Baja Tensión Gral. Pizarro y Luis Burela - Salta.»
        m = re.search(r"Relevamiento\s+(?:de\s+)?(?:Media y Baja|Media|Baja)\s+Tensi[oó]n\s+(.+?)\s*-\s*Salta", plano, re.I)
        if m:
            out["localidad"] = re.sub(r"\bGral\.?\s", "General ", m.group(1).strip())
    m = re.search(r"Expte\.?\s*(?:N[°º]\s*)?\d{1,4}\s*-\s*(\d{3,7})\s*/\s*\d{4}", plano, re.I)
    out["expediente"] = m.group(1) if m else ""
    m = re.search(r"firmado electr[oó]nicamente.*?\bel\s+(\d{1,2})/(\d{1,2})/(\d{4})", plano, re.I)
    out["fecha_firma"] = f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else ""
    m = re.search(r"IF\s*\d+\s*-\s*GEE\s*N[°º]?\s*\d+\s*-\s*\d{4}", plano)
    if m:
        out["informe"] = re.sub(r"\s+", " ", m.group(0))
    anio = None
    m = re.search(r"Salta,?\s+\d{1,2}\s+de\s+\w+\s+de\s+(\d{4})", plano)
    if m:
        anio = int(m.group(1))
    meses = "|".join(MESES)
    m = re.search(rf"d[ií]as?\s+(\d{{1,2}})(?:\s*(?:y|al|a)\s*(\d{{1,2}}))?\s+de\s+({meses})(?:\s+de\s+(\d{{4}}))?", plano, re.I)
    if m and (anio or m.group(4)):
        a = int(m.group(4)) if m.group(4) else anio
        out["fecha_desde"] = _fecha(int(m.group(1)), m.group(3), a)
        out["fecha_hasta"] = _fecha(int(m.group(2) or m.group(1)), m.group(3), a)
    return out


def leer_informe(contenido: bytes) -> dict:
    """Todo junto: datos generales, observaciones y avisos para mostrar al usuario."""
    texto = texto_pdf(contenido)
    datos = datos_generales(texto)
    obs = observaciones(texto)
    avisos = []
    if not obs:
        avisos.append("No encontré observaciones con el formato «Observación N ...». Podés cargarlas a mano más abajo.")
    else:
        numeros = [o["n"] for o in obs]
        if numeros != list(range(1, len(numeros) + 1)):
            avisos.append("La numeración de las observaciones no es correlativa; revisá la lista.")
        sin_enlace = [o["n"] for o in obs if not o["enlace"]]
        if sin_enlace:
            avisos.append(f"Observaciones sin enlace de Google Maps: {', '.join(map(str, sin_enlace))}.")
    if not datos["fecha_desde"] and datos["fecha_firma"]:
        datos["fecha_desde"] = datos["fecha_hasta"] = datos["fecha_firma"]
        f = datos["fecha_firma"].split("-")
        avisos.append(f"El informe no indica las fechas del relevamiento: se propuso la fecha de firma ({f[2]}/{f[1]}/{f[0]}). Corregila si hace falta.")
    if datos["expediente"]:
        avisos.append(f"El informe menciona el expediente {datos['expediente']}: se completó el número, verificalo.")
    for campo, nombre in (("localidad", "la localidad"), ("fecha_desde", "las fechas del relevamiento"), ("informe", "la referencia del informe")):
        if not datos[campo]:
            avisos.append(f"No pude leer {nombre}; completalo a mano.")
    return {**datos, "observaciones": obs, "avisos": avisos, "caracteres": len(texto)}
