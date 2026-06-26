@echo off
REM ============================================================
REM  TRACK — BOT PAPER AUTONOME — lanceur (double-clic)
REM  Le bot achete/vend SEUL en SIMULATION (paper). Zero argent reel.
REM  Astuce : "lancer_bot.bat reset" pour repartir de 1000 $.
REM ============================================================
cd /d "%~dp0"
if "%1"=="reset" (
    py paper_live_bot.py --reset
) else (
    py paper_live_bot.py
)
echo.
echo --- Termine. Relance demain : le bot avancera tout seul. ---
pause
