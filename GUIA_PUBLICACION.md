# Guía para publicar el programa en internet

Qué vas a armar: el programa en **Streamlit Community Cloud**, los datos en **Supabase**, el ingreso con **Google**, y un aviso diario para que Supabase no se pause.
Necesitás cuentas (gratuitas) en **GitHub**, **Supabase**, **Streamlit Community Cloud** y **Google Cloud**. Tomate una tarde: son varios pasos, pero se hacen una sola vez.

> Mientras no completes esto, el programa sigue funcionando en tu PC como hasta ahora (con `iniciar.bat`), sin usuario ni nube.

Los nombres de los menús de Google, Supabase y Streamlit cambian de vez en cuando: si algo no aparece donde dice la guía, buscá la opción por su nombre.

---

## Paso 1. Supabase (donde se guardan los datos)

1. Entrá a **supabase.com** y creá una cuenta (podés usar tu Gmail).
2. **New project**. Nombre: `mapa-relevamientos`. Elegí una contraseña de base de datos y guardala. Región: **South America (São Paulo)**. Esperá unos minutos a que termine de crearse.
3. En el menú de la izquierda abrí **SQL Editor** > **New query**. Abrí el archivo `supabase_setup.sql` de tu carpeta, copiá todo su contenido, pegalo y apretá **Run**. Debe decir que terminó con éxito. Se puede volver a correr sin problema.
4. Abrí **Storage**: tiene que aparecer una carpeta (bucket) llamada `datos`, privada.
5. Buscá las claves: **Project Settings** > **API** (o **API Keys**). Anotá dos datos:
   - **Project URL** (algo como `https://abcd1234.supabase.co`).
   - La clave **secreta** (`secret key`, o `service_role` en el formato anterior). **Es una llave maestra: no la compartas, no la subas a GitHub ni la pegues en chats.** Si alguna vez se filtra, se genera otra en la misma pantalla.

## Paso 2. Pasar los expedientes que ya tenés cargados (Chicoana)

1. En tu PC, copiá el archivo `.streamlit/secrets.toml.ejemplo` con el nombre `.streamlit/secrets.toml` (dentro de la misma carpeta `.streamlit`).
2. Completá en ese archivo solo la sección `[supabase]` (la dirección y la clave del paso 1). Por ahora dejá lo demás como está.
3. En la terminal de VS Code, con el entorno activado:
   ```
   pip install -r requirements.txt
   python subir_datos.py
   ```
   Tiene que decir cuántos archivos subió (`relevamientos.csv`, `observaciones.csv` y los PDF). Podés comprobarlo en Supabase > **Storage** > `datos`.

## Paso 3. Google (para el inicio de sesión)

1. Entrá a **console.cloud.google.com** con tu Gmail y creá un proyecto nuevo (nombre: `Mapa relevamientos`).
2. Buscá **Google Auth Platform** (o **APIs y servicios** > **Pantalla de consentimiento de OAuth**) y configurala:
   - Nombre de la aplicación: `Mapa de relevamientos`.
   - Correo de asistencia: tu Gmail.
   - Público: **Externo**.
   - Datos de contacto: tu Gmail.
3. Publicá la aplicación (botón **Publicar la aplicación** / *In production*). Como solo se piden los datos básicos (correo y nombre), no hace falta verificación de Google. Si la dejás en modo "Prueba", solo entran los correos que cargues como "usuarios de prueba" y hay que mantener dos listas.
4. **Clientes** (o **Credenciales**) > **Crear cliente de OAuth** > Tipo: **Aplicación web**. En **URI de redireccionamiento autorizados** agregá estos dos (el primero lo completás en el paso 5, cuando conozcas la dirección de tu app):
   - `https://NOMBRE-DE-TU-APP.streamlit.app/oauth2callback`
   - `http://localhost:8501/oauth2callback` (para probar en tu PC)
5. Google te muestra el **ID de cliente** y el **Secreto del cliente**. Anotalos.

## Paso 4. Completar los secretos

Armá este texto (es el contenido de `.streamlit/secrets.toml`; el archivo `secrets.toml.ejemplo` ya lo trae armado):

```toml
[auth]
redirect_uri = "https://NOMBRE-DE-TU-APP.streamlit.app/oauth2callback"
cookie_secret = "UNA-CADENA-LARGA-ALEATORIA"
client_id = "ID-DE-CLIENTE.apps.googleusercontent.com"
client_secret = "SECRETO-DEL-CLIENTE"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[supabase]
url = "https://abcd1234.supabase.co"
key = "CLAVE-SECRETA-DE-SUPABASE"

[app]
admins = ["TU-CORREO@gmail.com"]
```

- **`cookie_secret`**: generá una cadena aleatoria con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **`admins`**: tu correo de Google, escrito **exactamente** como es tu cuenta. Ese correo siempre entra como administrador.
- Este archivo **nunca** se sube a GitHub (el `.gitignore` ya lo impide).

Para probarlo en tu PC antes de publicar: poné `redirect_uri = "http://localhost:8501/oauth2callback"`, corré `iniciar.bat` y entrá con Google.
Ojo: con la sección `[auth]` completa, el programa en tu PC también pide inicio de sesión. Para volver al modo local sin ingreso, renombrá `secrets.toml`.

## Paso 5. GitHub y Streamlit Community Cloud

**GitHub (repositorio privado)**
1. Creá una cuenta en **github.com**.
2. Creá un repositorio nuevo, **Private**, llamado `mapa-relevamientos`.
3. Subí la carpeta del proyecto. Lo más simple es desde VS Code: panel **Control de código fuente** > **Inicializar repositorio** > mensaje de confirmación (*commit*) > **Publicar en GitHub** > elegir **privado**.
4. **Verificá** en la página del repositorio que **NO** aparezcan `.streamlit/secrets.toml`, `datos/relevamientos.csv`, `datos/observaciones.csv` ni `datos/informes/`. Tienen que quedar afuera (el `.gitignore` se encarga). Si aparecen, avisame antes de seguir.

**Streamlit Community Cloud**
1. Entrá a **share.streamlit.io** con tu cuenta de GitHub y autorizá el acceso a tus repositorios (incluidos los privados).
2. **Create app** > repositorio `mapa-relevamientos`, rama `main`, archivo principal `app.py`.
3. Elegí una dirección fácil (`NOMBRE-DE-TU-APP.streamlit.app`). Con esa dirección completá el `redirect_uri` del paso 4 y agregá la misma dirección en Google (paso 3.4).
4. **Advanced settings** > **Secrets**: pegá el contenido del paso 4 (con el `redirect_uri` de la nube) y elegí **Python 3.11 o 3.12**.
5. **Deploy**. Tarda unos minutos la primera vez.

## Paso 6. Aviso diario para que Supabase no se pause

1. En GitHub, en tu repositorio: **Settings** > **Secrets and variables** > **Actions** > **New repository secret**. Creá dos:
   - `SUPABASE_URL` = la dirección de tu proyecto.
   - `SUPABASE_KEY` = la clave secreta.
2. Pestaña **Actions** > **Mantener activo Supabase** > **Run workflow**. Tiene que terminar en verde.
3. Volvé al programa, pestaña **Administración**: en "Último aviso automático" debe decir "hace 0,0 h". Desde ahí lo ves todos los días: si pasan más de 2 días, algo falló.

Una advertencia de GitHub: en repositorios públicos, las tareas programadas se desactivan tras 60 días sin actividad en el repositorio. Con repositorio privado no debería pasar, pero la pestaña Administración te muestra si dejó de correr.

## Paso 7. Cargar los usuarios

1. Entrá a la dirección de tu app con tu cuenta de Google: entrás como administrador.
2. Pestaña **Administración** > **Usuarios**: agregá una fila por persona con su **correo de Google**, nombre y rol:
   - **lector**: ve el mapa, filtra y descarga reportes. Ideal para el celular.
   - **editor**: además carga, modifica, elimina y hace copias.
   - **admin**: además ve la pestaña Administración.
3. **Guardar usuarios**. Quien no esté en la lista ve "sin acceso". Para dar de baja a alguien, destildá *Activo* o borrá su fila.

En el celular, cada persona entra a la dirección de la app, toca **Iniciar sesión con Google** y confirma con la huella como siempre hace su teléfono con la cuenta de Google. En el navegador del celular se puede usar **Agregar a pantalla de inicio** para tenerla como un ícono.

---

## Qué mirar de vez en cuando

- **Administración**: memoria, espacio usado en Supabase (plan gratuito: 500 MB de base de datos y 1 GB de archivos), último aviso diario y registro de cambios (se puede descargar en CSV).
- **Copias de seguridad**: en la nube se guardan en el servidor y se pierden si se reinicia. Descargá una cada tanto. `python subir_datos.py --bajar` baja todo lo que está en Supabase a tu PC.

## Si algo falla

- *"Los últimos cambios todavía no llegaron a la nube"*: no hay conexión con Supabase. Se reintenta solo; el administrador puede forzarlo con **Sincronizar ahora**.
- *Después de iniciar sesión con Google dice "redirect_uri_mismatch"*: la dirección del paso 3.4 no coincide exactamente con el `redirect_uri` de los secretos.
- *Dice "sin acceso" con tu propio correo*: revisá que `admins` o la tabla de usuarios tengan el correo exactamente como lo usa Google.
- *La app tarda al abrir*: Streamlit Community Cloud "duerme" las aplicaciones sin uso y tarda unos segundos en despertarlas.
