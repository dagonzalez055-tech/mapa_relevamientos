"""Pestaña «Administración»: memoria, uso de la nube, usuarios y registro de cambios. Solo la ve el administrador."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import acceso
import almacen

LIMITE_BASE = 500 * 1024 ** 2         # plan gratuito de Supabase (supabase.com/pricing)
LIMITE_ARCHIVOS = 1024 ** 3
LIMITE_MEMORIA_REF = 1024 ** 3        # referencia de Streamlit Community Cloud (dato de 2021; puede haber cambiado)


def _ancho(fn, *args, **kwargs):
    """Elemento de Streamlit a ancho completo, sea cual sea la versión instalada."""
    error = None
    for extra in ({"width": "stretch"}, {"use_container_width": True}, {}):
        try:
            return fn(*args, **extra, **kwargs)
        except TypeError as e:
            error = e
    raise error


def _mb(b: float) -> str:
    return f"{b / 1024 ** 2:,.1f} MB".replace(",", "X").replace(".", ",").replace("X", ".")


def _memoria() -> dict:
    """Memoria del proceso (todas las sesiones juntas) y, si se puede, el tope del contenedor."""
    out: dict = {}
    try:
        import psutil
        out["proceso"] = psutil.Process(os.getpid()).memory_info().rss
    except Exception:
        pass
    try:
        import resource                                   # solo Linux/Mac
        out["pico"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    except Exception:
        pass
    for usado, tope in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
                        ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            u = int(open(usado).read().strip())
            t = open(tope).read().strip()
            out["contenedor"] = u
            if t.isdigit() and int(t) < 1 << 50:
                out["tope"] = int(t)
            break
        except (OSError, ValueError):
            continue
    return out


def _hace(iso: str | None) -> str:
    if not iso:
        return "nunca"
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        s = (datetime.now(timezone.utc) - t).total_seconds()
    except ValueError:
        return str(iso)
    return f"hace {s / 3600:.1f} h" if s < 172800 else f"hace {s / 86400:.0f} días"


def mostrar(usuario, rels: pd.DataFrame, obs: pd.DataFrame) -> None:
    st.subheader("Memoria del programa")
    m = _memoria()
    if "proceso" in m:
        tope = m.get("tope", LIMITE_MEMORIA_REF)
        c1, c2, c3 = st.columns(3)
        c1.metric("En uso ahora", _mb(m["proceso"]))
        if "pico" in m:
            c2.metric("Máximo desde que arrancó", _mb(m["pico"]))
        c3.metric("Tope " + ("del servidor" if "tope" in m else "de referencia"), _mb(tope))
        st.progress(min(m["proceso"] / tope, 1.0), text=f"{m['proceso'] / tope * 100:.0f} % del tope")
        if "tope" not in m:
            st.caption("El servidor no informa su tope; se muestra 1 GB como referencia de Streamlit Community Cloud "
                       "(dato de 2021, puede haber cambiado). Si el programa lo supera, Streamlit lo reinicia.")
        st.caption("Es la memoria de todo el programa (todas las sesiones abiertas juntas). Entrar desde más dispositivos la sube un poco.")
    else:
        st.warning("No se pudo medir la memoria (falta el paquete psutil: `pip install psutil`).")

    st.subheader("Uso de la nube (Supabase)")
    if not almacen.activo():
        st.info("Esta copia del programa no está conectada a Supabase: los datos están solo en esta PC.")
    else:
        t = almacen.tamanos()
        c1, c2, c3, c4 = st.columns(4)
        if t:
            c1.metric("Base de datos", _mb(t["base"]), f"de {_mb(LIMITE_BASE)}", delta_color="off")
            c2.metric("Archivos (PDF)", _mb(t["archivos"]), f"de {_mb(LIMITE_ARCHIVOS)}", delta_color="off")
            st.progress(min(t["archivos"] / LIMITE_ARCHIVOS, 1.0), text=f"Archivos: {t['archivos'] / LIMITE_ARCHIVOS * 100:.1f} % del plan gratuito")
            st.progress(min(t["base"] / LIMITE_BASE, 1.0), text=f"Base de datos: {t['base'] / LIMITE_BASE * 100:.1f} % del plan gratuito")
        else:
            st.warning("No se pudo consultar el tamaño (¿se ejecutó supabase_setup.sql completo?).")
        c3.metric("Expedientes", len(rels))
        c4.metric("Observaciones", len(obs))
        aviso = almacen.ultimo_aviso()
        st.caption(f"Último aviso automático que mantiene activo el proyecto: **{_hace(aviso)}**. "
                   "Si pasan más de 2 días, revisá la pestaña Actions del repositorio en GitHub.")
        est = almacen.estado()
        pend = almacen.pendientes()
        if est["error"]:
            st.error(f"Último error con la nube: {est['error']}")
        if pend:
            st.warning("Cambios que todavía no llegaron a la nube: " + ", ".join(pend))
        b1, b2, _ = st.columns([1, 1, 3])
        if b1.button("Sincronizar ahora", key="adm_sync"):
            almacen.bajar(forzar=True)
            almacen.subir()
            st.session_state["aviso"] = "Sincronización hecha" if not almacen.estado()["error"] else "La sincronización falló"
            st.rerun()
        if b2.button("Bajar todos los PDF", key="adm_pdf", help="Útil antes de una copia de seguridad completa."):
            n = almacen.traer_todo()
            st.session_state["aviso"] = f"Se bajaron {n} archivos"
            st.rerun()

    st.subheader("Usuarios")
    if not almacen.activo():
        st.caption("Sin Supabase no hay tabla de usuarios: se usan los correos de los secrets ([app] admins / editores / lectores).")
    else:
        st.caption("Rol **lector**: ve el mapa, filtra y descarga reportes. **editor**: además carga, modifica y elimina. "
                   "**admin**: además ve esta pestaña. Los administradores de los secrets siempre pueden entrar, aunque no figuren acá. "
                   "Para quitar a alguien, borrá su fila o destildá «activo».")
        try:
            actuales = pd.DataFrame(almacen.usuarios_listar(), columns=["email", "nombre", "rol", "activo"])
        except almacen.ErrorNube as e:
            st.error(f"No se pudo leer la lista: {e}")
            actuales = None
        if actuales is not None:
            editada = _ancho(
                st.data_editor, actuales, num_rows="dynamic", hide_index=True, key="adm_usuarios",
                column_config={
                    "email": st.column_config.TextColumn("Correo de Google", required=True),
                    "nombre": st.column_config.TextColumn("Nombre"),
                    "rol": st.column_config.SelectboxColumn("Rol", options=list(almacen.ROLES), required=True, default="lector"),
                    "activo": st.column_config.CheckboxColumn("Activo", default=True),
                })
            if st.button("Guardar usuarios", type="primary", key="adm_guardar_usuarios"):
                filas = editada.fillna({"nombre": "", "rol": "lector", "activo": True}).to_dict("records")
                filas = [f for f in filas if str(f.get("email", "")).strip()]
                try:
                    almacen.usuarios_guardar(filas)
                except almacen.ErrorNube as e:
                    st.error(f"No se pudo guardar: {e}")
                else:
                    antes = {a["email"]: (a["rol"], a["activo"]) for a in actuales.to_dict("records")}
                    ahora = {str(f["email"]).strip().lower(): (f["rol"], bool(f["activo"])) for f in filas}
                    cambios = [f"{e}: {antes.get(e, 'nuevo')} → {v}" for e, v in ahora.items() if antes.get(e) != v]
                    cambios += [f"{e}: eliminado" for e in antes if e not in ahora]
                    almacen.registrar(usuario.etiqueta, "Modificó usuarios", "; ".join(cambios)[:480] or "sin cambios")
                    acceso.olvidar_usuarios()
                    st.session_state["aviso"] = "Usuarios guardados"
                    st.rerun()

    st.subheader("Registro de cambios")
    filas = almacen.leer_cambios(1000)
    if not filas:
        st.caption("Todavía no hay movimientos registrados.")
    else:
        df = pd.DataFrame(filas, columns=almacen.COL_CAMBIOS)
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce", utc=True).dt.tz_convert("America/Argentina/Salta") \
            .dt.strftime("%d/%m/%Y %H:%M")
        f1, f2 = st.columns(2)
        quienes = f1.multiselect("Usuario", sorted(df["usuario"].dropna().unique()), key="adm_f_usr")
        que = f2.multiselect("Acción", sorted(df["accion"].dropna().unique()), key="adm_f_acc")
        if quienes:
            df = df[df["usuario"].isin(quienes)]
        if que:
            df = df[df["accion"].isin(que)]
        _ancho(st.dataframe, df.rename(columns={"fecha": "Fecha", "usuario": "Usuario", "accion": "Acción", "detalle": "Detalle"}),
               hide_index=True)
        st.download_button("Descargar el registro (CSV)", df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                           file_name=f"registro_cambios_{datetime.now():%Y-%m-%d}.csv", mime="text/csv", key="adm_dl_log")
        st.caption("Se muestran los últimos 1.000 movimientos. Los ingresos se anotan una vez por sesión.")
