"""Inicio de sesión (Google), roles y conexión con la nube.

Roles:  lector (ve, filtra y descarga reportes) · editor (además carga y modifica) · admin (además administra).

Se configura en los «secrets» de Streamlit (ver .streamlit/secrets.toml.ejemplo y GUIA_PUBLICACION.md).
Sin la sección [auth] no hay inicio de sesión y el programa funciona como siempre, en modo local (todo permitido):
es lo que pasa cuando se corre en la PC con iniciar.bat.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import streamlit as st

import almacen

ROTULO = {"lector": "lector (solo ver)", "editor": "editor", "admin": "administrador"}
_ULTIMO_DENEGADO: dict[str, float] = {}


@dataclass(frozen=True)
class Usuario:
    email: str
    nombre: str
    rol: str
    con_login: bool = True

    @property
    def puede_editar(self) -> bool:
        return self.rol in ("editor", "admin")

    @property
    def es_admin(self) -> bool:
        return self.rol == "admin"

    @property
    def etiqueta(self) -> str:
        return self.email if self.email else "local"


LOCAL = Usuario("", "Modo local", "admin", con_login=False)


def _secreto(seccion: str) -> dict:
    try:
        if seccion in st.secrets:
            return dict(st.secrets[seccion])
    except Exception:
        pass
    return {}


def _correos(valor) -> set[str]:
    if isinstance(valor, str):
        valor = [valor]
    return {str(v).strip().lower() for v in (valor or []) if str(v).strip()}


@st.cache_data(ttl=60, show_spinner=False)
def _tabla_usuarios(_version: int = 0) -> list[dict]:
    try:
        return almacen.usuarios_listar()
    except Exception:
        return []


def olvidar_usuarios() -> None:
    _tabla_usuarios.clear()


def rol_de(email: str) -> str | None:
    """Rol que corresponde a un correo, o None si no tiene acceso. Los admins de los secrets siempre entran."""
    email = email.strip().lower()
    app = _secreto("app")
    if email in _correos(app.get("admins")):
        return "admin"
    for u in _tabla_usuarios():
        if str(u.get("email", "")).lower() == email:
            return u.get("rol") if u.get("activo", True) and u.get("rol") in almacen.ROLES else None
    if email in _correos(app.get("editores")):
        return "editor"
    if email in _correos(app.get("lectores")):
        return "lector"
    return None


def _pantalla_ingreso() -> None:
    st.title("Mapa de relevamientos de redes eléctricas")
    st.caption("ENRESP · Gerencia de Energía Eléctrica · Provincia de Salta")
    st.info("Para entrar, iniciá sesión con tu cuenta de Google. Solo pueden ver el mapa las personas autorizadas.")
    st.button("Iniciar sesión con Google", type="primary", on_click=st.login)


def _pantalla_sin_acceso(email: str, motivo: str) -> None:
    st.title("Mapa de relevamientos de redes eléctricas")
    st.error(f"La cuenta **{email or 'desconocida'}** no tiene acceso. {motivo}")
    st.caption("Si deberías tenerlo, pedile al administrador que agregue ese correo. "
               "También podés probar con otra cuenta de Google.")
    st.button("Cerrar sesión", on_click=st.logout)


def iniciar(lg) -> Usuario:
    """Conecta la nube y pide el inicio de sesión. Devuelve el usuario; si no corresponde, corta la página."""
    sb = _secreto("supabase")
    almacen.iniciar(lg.DATOS, sb.get("url", ""), sb.get("key", ""))
    almacen.conectar(lg)

    if "auth" in _secreto_nombres():
        u = st.user
        if not getattr(u, "is_logged_in", False):
            _pantalla_ingreso()
            st.stop()
        email = str(u.get("email", "") or "").strip().lower()
        if not email or u.get("email_verified") is False:
            _pantalla_sin_acceso(email, "Google no confirmó el correo de esa cuenta.")
            st.stop()
        rol = rol_de(email)
        if rol is None:
            ahora = time.time()
            if ahora - _ULTIMO_DENEGADO.get(email, 0) > 3600:           # sin repetir el aviso a cada rato
                _ULTIMO_DENEGADO[email] = ahora
                almacen.registrar(email, "Acceso denegado", "Cuenta que no figura en la lista de usuarios")
            _pantalla_sin_acceso(email, "No figura en la lista de usuarios autorizados.")
            st.stop()
        usuario = Usuario(email, str(u.get("name", "") or ""), rol)
        if not st.session_state.get("_ingreso_registrado"):
            st.session_state["_ingreso_registrado"] = True
            almacen.registrar(email, "Ingreso", ROTULO[rol])
    else:
        usuario = LOCAL

    almacen.bajar()
    if almacen.pendientes():
        almacen.subir()
    return usuario


def _secreto_nombres() -> set[str]:
    try:
        return set(st.secrets.keys())
    except Exception:
        return set()


def caja_usuario(u: Usuario) -> None:
    """Datos de quien está usando el programa, arriba de la columna izquierda."""
    if u.con_login:
        st.caption(f"👤 **{u.nombre or u.email}**  \n{u.email}  \nRol: {ROTULO[u.rol]}")
        st.button("Cerrar sesión", on_click=st.logout, key="btn_logout")
    else:
        st.caption("Modo local: sin inicio de sesión. Todo está permitido.")
