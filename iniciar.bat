@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
echo Mapa de relevamientos en marcha. No cierres esta ventana.
streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
pause
