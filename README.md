# 🔱 Trading Desk — Cockpit IBKR (analyse only)

Cockpit d'analyse trading **local** (Python/Flask) pour 57 leaders US : scoring,
moteur de décision IBKR /40, options desk, cours **en direct via IBKR**, fiches
entreprise complètes, plan du jour, watchlist poster.

> ⛔ **ANALYSE / LECTURE SEULE.** Aucun ordre n'est jamais passé. La connexion
> IBKR est verrouillée en `readonly=True`. Ceci n'est pas un conseil financier.

## Lancer

```bash
pip install -r requirements.txt   # flask, yfinance, pandas, numpy, ib_async, anthropic…
python terminal.py
```

Puis ouvrir **http://localhost:5002**.

- `.env` (privé, ignoré par git) : copier `.env.example` → `.env` et y mettre sa clé
  Anthropic (pour la traduction FR des news, optionnel).
- **Cours en direct + compte** : lancer **TWS / IB Gateway**, activer l'API
  (Configuration globale → API → Settings : *Enable Socket Clients* + *Read-Only API*,
  port 7496/7497, IP de confiance `127.0.0.1`).

## 📱 Accès depuis l'iPhone / une tablette (même WiFi maison)

Le serveur écoute déjà sur tout le réseau local (`host='0.0.0.0'`).

1. Sur le PC, trouver son IP locale : `ipconfig` (ex. `192.168.x.x`).
2. Autoriser le port 5002 dans le pare-feu Windows (une fois, en admin) :
   ```powershell
   New-NetFirewallRule -DisplayName "Trading Desk 5002" -Direction Inbound -LocalPort 5002 -Protocol TCP -Action Allow -Profile Private
   ```
3. Sur l'iPhone (même WiFi) : ouvrir **http://192.168.x.x:5002**
   (le PC + TWS doivent rester allumés).

## Pages

- `/` — cockpit live (plan du jour, recommandations IBKR /40, marché, positions, options)
- `/titre/<TICKER>` — fiche entreprise complète (technique + fondamentaux + options + décision)
- `/entreprises` — screener des 57 sociétés (cours live + fondamentaux, triable)
- `/options` — options desk complet (LEAPS / Swing / Tactique, Option Quality /100)
- `/watchlist` — poster « Daily Watchlist » imprimable

## Structure

- `terminal.py` — app Flask + toutes les pages + connexion IBKR + flux cours live
- `elio/` — moteurs : `scoring`, `engine` (décision), `ibkr` (verdict /40), `options`,
  `fundamentals`, `sectors`, `daily`, `anomalies`, `market`, `weekly`, `research`, `ai`
- `ib_reader.py` — lecture IBKR (readonly, garde-fou anti-ordre)
