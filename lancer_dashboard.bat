@echo off
REM ============================================================
REM  TRADING DESK — DASHBOARD EN DIRECT (IBKR temps reel)
REM  Double-clic = lance le cockpit connecte a TWS/IB Gateway.
REM
REM  AVANT de double-cliquer :
REM    1) Ouvre TWS ou IB Gateway et connecte-toi a ton compte
REM    2) Active l'API en LECTURE SEULE :
REM       Configuration globale > API > Settings :
REM         - cocher "Enable ActiveX and Socket Clients"
REM         - cocher "Read-Only API"   (verrou anti-ordre)
REM         - Trusted IP : 127.0.0.1
REM       Port : 7496 (reel) ou 7497 (paper) / 4001-4002 (Gateway)
REM
REM  Ensuite : ouvre http://localhost:5002 sur ce PC,
REM            ou http://<IP-locale-du-PC>:5002 sur l'iPhone (meme WiFi).
REM  ============================================================
cd /d "%~dp0"
echo.
echo  === TRADING DESK - mode DIRECT (IBKR) ===
echo  Verifie que TWS/IB Gateway est ouvert + API en lecture seule.
echo.
REM  IBKR active : on NE met PAS NO_IBKR (donc temps reel si TWS dispo)
set NO_IBKR=
py terminal.py
echo.
echo  --- Dashboard arrete. Reouvre TWS + relance pour repartir en direct. ---
pause
