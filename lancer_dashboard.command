#!/bin/bash
# ============================================================
#  TRADING DESK — DASHBOARD EN DIRECT (IBKR temps réel) — macOS
#  Double-clic = lance le cockpit connecté à TWS/IB Gateway.
#
#  AVANT : ouvre TWS/IB Gateway + API en LECTURE SEULE
#    (Enable Socket Clients + Read-Only API + Trusted IP 127.0.0.1).
#  ENSUITE : http://localhost:5002 sur ce Mac,
#            ou http://<IP-locale>:5002 sur l'iPhone (même WiFi).
# ============================================================
cd "$(dirname "$0")"
echo "=== TRADING DESK — mode DIRECT (IBKR) ==="
echo "Vérifie que TWS/IB Gateway est ouvert + API en lecture seule."
unset NO_IBKR            # IBKR activé => temps réel si TWS dispo
python3 terminal.py
echo "--- Dashboard arrêté. ---"
