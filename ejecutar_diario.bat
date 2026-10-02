@echo off
REM ============================================================
REM  Lanzador diario del agente de perfumes (Windows)
REM  1) Rellena tus datos abajo (sin comillas). NO compartas este archivo.
REM  2) Programa este .bat en el Programador de tareas de Windows (ver GUIA).
REM ============================================================
cd /d "%~dp0"
if not exist data mkdir data

set EMAIL_USER=tu_gmail@gmail.com
set EMAIL_PASS=las16letrasSinEspacios
set EMAIL_TO=tu_gmail@gmail.com
set WA_PHONE=+34XXXXXXXXX
set WA_APIKEY=tu_apikey_de_callmebot

echo ==== %date% %time% ==== >> data\registro.txt
python agente.py >> data\registro.txt 2>&1
