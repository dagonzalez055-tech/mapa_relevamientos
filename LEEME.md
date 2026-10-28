# Mapa de relevamientos de redes eléctricas (Salta)

Muestra la red de media tensión (13,2, 33 y 66 kV), los CT y las subestaciones, y marca qué líneas fueron relevadas y si EDESA ya ejecutó las adecuaciones. Cada relevamiento es un expediente.

## Código de colores

| Color | Significa |
|---|---|
| Azul | Línea relevada. Adecuaciones pendientes de EDESA |
| Verde | EDESA ejecutó las adecuaciones del expediente |
| Gris | Red sin relevar (más oscura y gruesa cuanto mayor la tensión) |
| Amarillo (halo) | Expediente elegido |
| Naranja | Puntos de observación (se activan en el panel izquierdo) |

El estado se lleva **por expediente completo**, no por observación.

## Instalación (una sola vez)

En una terminal dentro de esta carpeta (en VS Code: Terminal > Nuevo terminal):

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```
.venv\Scripts\activate
streamlit run app.py
```

Se abre en el navegador. Hacé clic sobre una línea azul o verde para ver su expediente. Debajo del mapa aparecen los datos, el botón para descargar el informe en PDF y el botón **Marcar como ejecutado por EDESA** (con fecha y comentario). Si te equivocás, «Deshacer» lo vuelve a azul.

## Buscar en el mapa

En el panel izquierdo, **Localidad** y **CT o subestación** son buscadores: se elige (o se escribe) un nombre y el mapa se acerca. El CT buscado queda marcado con un círculo amarillo y su nombre. «Volver a la vista anterior» deshace la búsqueda. Los nombres de los CT se ven al acercar el zoom (opción «Nombres de CT»).

## Distribuidores (morado, amarillo y anaranjado)

En «Buscar en el mapa»:
- **Distribuidor 1, 2 y 3**: se pueden ver hasta tres a la vez, cada uno con su color: **morado**, **amarillo** y **anaranjado**. Se eligen por código o nombre (ej. `CF14`, «Etchart»). El mapa se acerca al conjunto y debajo aparece una ficha por distribuidor: estación de la que sale, kV, CT, km de línea, km ya relevados y localidades que alimenta.
- **Localidad**: además de acercar el mapa, resalta los distribuidores que tienen CT en esa localidad. Se muestran hasta **6**, los de más CT en la localidad, **cada uno con su color** (morado, amarillo, anaranjado, rojo, marrón y violeta). Con «Solo el tramo dentro de la localidad» (por defecto) cada distribuidor se ve únicamente a menos de 800 m de los CT de esa localidad; con «El distribuidor completo» se ve de punta a punta, aunque pase por otras localidades. Si elegís distribuidores a mano, se muestran solo esos.
- Lo ya relevado **no pierde su color**: el azul o el verde se dibuja encima y el color del distribuidor queda como borde.
- **Nombre del distribuidor de cualquier línea**: hacé clic sobre la línea (o un CT) y debajo del mapa aparece el distribuidor y otros datos. Streamlit no permite el clic derecho ni mostrar el dato al pasar el mouse en todas las versiones.

Cómo se obtiene: la extracción de EDESA no indica a qué distribuidor pertenece cada tramo. Se arma con el padrón de CT y la tabla de distribuidores, uniendo por la red todos los CT de un distribuidor (y su estación de cabecera si se reconoce). Es una **aproximación**.

Para actualizar con planillas nuevas de EDESA (cierra la app, y en la terminal con el entorno activado):

```
python preparar_distribuidores.py "Padrón de CTs.xlsx" "Tabla Distribuidores.xlsx"
```

Reconoce los archivos por sus columnas. Agrega al mapa los CT que no estaban (ids «P-…» en `datos/subestaciones.csv`) y regenera `distribuidores.csv`, `ct_distribuidor.csv` y `dist_tramos.npz`. Si alguna vez se vuelve a correr `preparar_datos.py`, hay que correr después este script.

## Vista satélite y Google Maps

- **Mapa base > Satélite** muestra imágenes de Esri (sin clave, necesita internet). Si no se ve, usá el botón **Abrir este punto en Google Maps**.
- Al hacer clic sobre una línea, un CT o una estación, debajo del mapa aparece el botón **Abrir este punto en Google Maps**. En un CT o estación lleva a su ubicación exacta; en una línea lleva al centro del tramo, porque el mapa no informa el lugar exacto del clic.

## Reportes (pestaña «Reportes»)

- **Excel.** Hojas: *Resumen* (totales y porcentajes con fórmulas), *Relevamientos* (una fila por expediente: localidad, fechas de relevamiento y de solución de EDESA, km por tensión, observaciones, estado), *Observaciones* (una fila por observación) y *Localidades* (los 60 municipios y si fueron relevados). Las hojas tienen filtros y la fila 4 suma solo lo visible. Si Excel muestra ceros, apretar «Habilitar edición».
- **Word.** Nota al Directorio de la Gerencia Eléctrica con el formato de `datos/plantilla_nota.docx`: porcentaje de red y de localidades relevadas, detalle por expediente y localidades pendientes por departamento.
- Los % de localidades se calculan sobre los 60 municipios de `datos/localidades.csv`. Las que no tienen `lat` y `lon` cuentan en el %, pero no aparecen en el buscador; se pueden completar a mano.
- Si dos expedientes comparten tramos, la suma de km los cuenta dos veces; el % usa los km sin repetir.

## Copias de seguridad (pestaña «Copias de seguridad»)

Antes de cada cambio (cargar, cambiar estado, eliminar, subir un informe nuevo) el programa hace solo una copia en `datos/copias/` (se guardan las últimas 40 automáticas; varias escrituras seguidas comparten una). También hay un botón **Hacer una copia ahora** con nombre opcional. Cada copia es un .zip con relevamientos, observaciones, localidades, informes y zonas, y desde la pestaña se puede descargar o **restaurar** (antes de restaurar se guarda una copia de lo actual, así que se puede volver atrás). Esa carpeta está en la misma PC: bajá una copia de vez en cuando y guardala en otro lugar.

## Cargar un relevamiento nuevo (pestaña «Cargar relevamiento»)

Hay tres formas de definir la línea relevada. En todas se completan el N° de expediente, la localidad, las fechas y la referencia del informe, y se puede adjuntar el PDF.

1. **Informe PDF.** Se sube el informe: la app lee localidad, fechas, referencia y las observaciones con sus enlaces de Google Maps. El número de expediente se escribe a mano porque el informe no lo trae. Con el botón «Ubicar observaciones» (necesita internet) se obtienen las coordenadas y se marca como relevada la red dentro de una zona alrededor de ellas. El margen se ajusta. Es la forma menos precisa.
2. **De un CT o subestación a otro.** Se elige el punto de partida y el de llegada (se puede buscar por nombre o código) y, si hace falta, puntos intermedios. La app busca el camino más corto por la red y lo muestra en un mapa antes de guardar.
3. **Por coordenadas.** Se pegan latitud y longitud, una por línea, copiadas de Google Maps. Con dos puntos se busca el camino entre ellos; con más, pasa por todos en orden.

Si el camino da una vuelta que no corresponde (por ejemplo en un anillo), agregá un punto intermedio. Si el expediente ya existe, se reemplaza.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `app.py` | La aplicación |
| `logica.py`, `rutas.py`, `informes.py` | Cálculos, recorridos sobre la red y lectura del PDF |
| `datos/relevamientos.csv` | Un renglón por expediente, con su estado |
| `datos/observaciones.csv` | Observaciones de cada informe (informativas) |
| `datos/localidades.csv` | Los 60 municipios de Salta (nombre, alias, departamento, población 2010, categoría, lat, lon). Sirve para los rótulos del mapa, el buscador y el % de localidades |
| `reportes.py`, `datos/plantilla_nota.docx` | Armado del Excel y de la nota en Word |
| `datos/copias/` | Copias de seguridad (.zip), automáticas y manuales |
| `datos/informes/<expediente>.pdf` | Informes guardados, para descargar desde el mapa |
| `datos/zonas/<expediente>.geojson` | Opcional: polígono dibujado a mano para un expediente cargado por informe |
| `datos/red_mt.npz`, `datos/subestaciones.csv` | Red y subestaciones ya preparadas (incluye los CT nuevos del padrón) |
| `datos/distribuidores.csv`, `datos/ct_distribuidor.csv`, `datos/dist_tramos.npz` | Distribuidores, el distribuidor y la localidad de cada CT, y los tramos de cada distribuidor. Los genera `preparar_distribuidores.py` |
| `preparar_datos.py` | Regenera los dos anteriores cuando EDESA entregue una extracción nueva: `python preparar_datos.py "<carpeta INSTALACIONES>"` |
| `resolver_enlaces.py` | Versión por línea de comandos del botón «Ubicar observaciones» |

Los CSV se pueden abrir en Excel. No cambies los nombres de las columnas.

## Compartirlo en la Gerencia

La información es pública, así que alcanza con una de estas dos formas:

- **Una PC o servidor de la red interna.** Se deja corriendo con `streamlit run app.py --server.address 0.0.0.0 --server.port 8501` y los demás entran desde el navegador a `http://<IP de esa PC>:8501`. Hay que permitir el puerto 8501 en el firewall de Windows. La PC tiene que quedar encendida.
- **Streamlit Community Cloud.** Gratis. Se sube la carpeta a GitHub y se publica. Ojo: en la nube los cambios que se guardan (estado, expedientes nuevos) no son permanentes, porque la carpeta se restablece al reiniciar la app. Para ese caso hay que cambiar el almacenamiento a una base de datos.

Con varias personas usando la app a la vez, dos que guarden en el mismo momento pueden pisarse. Las copias automáticas ayudan, pero igual conviene guardar una fuera de la PC.

## Detalles a tener en cuenta

- Dos líneas se consideran empalmadas si están a menos de 12 m. Dos sistemas de la red que no se tocan (hay 26 en el archivo actual) no se pueden unir con un recorrido.
- Al hacer clic en una línea el mapa se acerca a ese expediente. Si después movés el mapa y cambiás un filtro, vuelve a esa vista.
- El clic sobre el mapa necesita Streamlit 1.40 o más nuevo. Si tu versión no lo permite, el expediente se elige en la lista que está debajo del mapa.
- Las observaciones en baja tensión se muestran como puntos, pero no tienen tramo asociado.


## Usuarios, roles y nube (versión publicada)
Para usar el programa desde internet y el celular, con inicio de sesión de Google, roles (lector / editor / administrador), registro de cambios y los datos guardados en Supabase, seguí **GUIA_PUBLICACION.md**.

- Archivos nuevos: `acceso.py` (inicio de sesión y roles), `almacen.py` (sincronización con Supabase y registro de cambios), `panel_admin.py` (pestaña Administración), `supabase_setup.sql` (se pega una vez en Supabase), `subir_datos.py` (pasa los datos de la PC a la nube, o los baja), `.github/workflows/mantener_activo.yml` (aviso diario) y `.streamlit/secrets.toml.ejemplo`.
- Sin `.streamlit/secrets.toml` el programa funciona como antes, en tu PC, sin usuarios.
- El administrador ve la pestaña **Administración**: memoria que usa el programa, espacio usado en Supabase, usuarios y registro de cambios.
