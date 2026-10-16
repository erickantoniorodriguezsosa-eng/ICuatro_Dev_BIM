@echo off
rem Inicia el Asistente BIM Vialidad (requiere: pip install ifcopenshell numpy scipy shapely openpyxl)
cd /d "%~dp0"
start "" http://127.0.0.1:8765
python server.py
pause
