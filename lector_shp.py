"""Lector mínimo de shapefiles (líneas y puntos) sin dependencias externas.

Solo usa la biblioteca estándar. Alcanza para los archivos de EDESA
(cables.shp y subestacion.shp, en WGS84).
"""
import struct


def leer_dbf(ruta):
    """Devuelve (campos, registros). Cada registro es (borrado, [valores])."""
    with open(ruta, "rb") as f:
        d = f.read()
    n = struct.unpack("<I", d[4:8])[0]
    largo_cab = struct.unpack("<H", d[8:10])[0]
    largo_reg = struct.unpack("<H", d[10:12])[0]
    campos = []
    p = 32
    while d[p] != 0x0D:
        nombre = d[p:p + 11].split(b"\0")[0].decode("latin-1")
        campos.append((nombre, chr(d[p + 11]), d[p + 16], d[p + 17]))
        p += 32
    try:
        d[largo_cab:].decode("utf-8")
        cod = "utf-8"
    except UnicodeDecodeError:
        cod = "latin-1"
    registros = []
    for i in range(n):
        o = largo_cab + i * largo_reg
        if o + largo_reg > len(d):
            break
        fila = d[o:o + largo_reg]
        borrado = fila[0] == 0x2A
        q = 1
        valores = []
        for _, tipo, ln, _dec in campos:
            crudo = fila[q:q + ln]
            q += ln
            s = crudo.decode(cod, errors="replace").strip()
            if tipo in "NF":
                try:
                    v = float(s) if s else None
                except ValueError:
                    v = None
            else:
                v = s
            valores.append(v)
        registros.append((borrado, valores))
    return campos, registros


def leer_shp(ruta):
    """Devuelve una lista de geometrías. Cada una es una lista de partes y
    cada parte una lista de (lon, lat). Los registros nulos dan None."""
    with open(ruta, "rb") as f:
        d = f.read()
    geoms = []
    p = 100
    while p + 8 <= len(d):
        largo = struct.unpack(">i", d[p + 4:p + 8])[0] * 2
        c = d[p + 8:p + 8 + largo]
        p += 8 + largo
        t = struct.unpack("<i", c[:4])[0]
        if t == 0:
            geoms.append(None)
        elif t in (1, 11, 21):
            x, y = struct.unpack("<2d", c[4:20])
            geoms.append([[(x, y)]])
        elif t in (3, 13, 23, 5, 15, 25):
            nparts, npts = struct.unpack("<2i", c[36:44])
            partes = list(struct.unpack("<%di" % nparts, c[44:44 + 4 * nparts]))
            o = 44 + 4 * nparts
            xy = struct.unpack("<%dd" % (2 * npts), c[o:o + 16 * npts])
            pts = [(xy[2 * i], xy[2 * i + 1]) for i in range(npts)]
            partes.append(npts)
            geoms.append([pts[partes[i]:partes[i + 1]] for i in range(nparts)])
        elif t in (8, 18, 28):
            npts = struct.unpack("<i", c[36:40])[0]
            xy = struct.unpack("<%dd" % (2 * npts), c[40:40 + 16 * npts])
            geoms.append([[(xy[2 * i], xy[2 * i + 1]) for i in range(npts)]])
        else:
            geoms.append(None)
    return geoms
