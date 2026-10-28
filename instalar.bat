@echo off
cd /d "%~dp0"
echo Creando entorno e instalando paquetes (una sola vez)...
python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Listo. Ahora ejecuta iniciar.bat
pause
