# 🔺 VERTEX 1.0 — Cockpit de décision trading (analyse only)

Machine de décision **offensive mais disciplinée** pour actions US + options CALL.
**Lecture seule absolue — aucun ordre n'est jamais passé.** Vertex *analyse, score,
explique et alerte* ; il ne *trade pas*.

---

## 1. Ce qui marche (audité ✅)

- **35 fichiers Python compilent**, **26 tests** passent (dont 10 invariants quant + garde-fous lecture seule).
- **Toutes les pages** répondent : Cockpit `/`, Stratégie `/strategie`, Watchlist `/watchlist`,
  Entreprises `/entreprises`, Options `/options`, Ma Page `/ma-page`, Fiche titre `/titre/<sym>`.
- **Toutes les API** répondent : `/api/command`, `/api/comite`, `/api/strategie`, `/api/portefeuille`,
  `/api/risk`, `/api/validator`, `/api/vertex/<sym>`, `/healthz`.
- **Mode DÉMO** (cloud) : chiffres synthétiques réalistes pour valider le design, sans réseau.
- **Mode DIRECT** (bureau) : temps réel IBKR via TWS, lecture seule.

## 2. Les 7 moteurs (package `elio/`)

| Moteur | Fichier | Rôle |
|---|---|---|
| Data | `terminal.py` | IBKR live (TWS readonly) · yfinance · fallback Stooq · démo |
| Régime marché | `market.py` | SPY/VIX, breadth, risk-on/off |
| Scoring | `scoring.py` | score /100 (technique, momentum, fonda, risque) |
| Structure | `pivots.py` | plis (sommets/creux), tendance, entrée confirmée (anti-rebond-piège) |
| Options Desk | `strategy.py` + `options.py` | CALL/PUT 1→12 mois, Black-Scholes, scénarios |
| Risk Manager | `committee.py` + `portfolio_risk.py` | 4 portes, R:R ≥ 2:1, corrélation, concentration |
| Décision | `committee.py` | ACHETER / RENFORCER / ATTENDRE / ÉVITER documenté |

### 🔺 Noyau quantitatif VERTEX (`vertex.py`, `vertex_ml.py`, `validator.py`)
- **v1** scores quant + verdicts S+/BUY/WATCH/WAIT/AVOID
- **v2** edge **Monte-Carlo** : P(TP1), first-touch, edge + intervalle de confiance, `no_trade`
- **v3** **ML/calibration** : probabilité de gain `p_win` (logistique, plafond 85 %, upgrade XGBoost optionnel)
- **v4** **Risk Manager portefeuille** : corrélation, concentration, secteurs, risk-parity capé
- **v5** **Validateur** : walk-forward, DSR, PSR, PBO → crédibilité hors échantillon
- **Bootstrap réel** + **espérance (EV)** + **décomposition explicable** par titre

---

## 3. Démarrer au BUREAU (temps réel IBKR)

### Étape 1 — TWS / IB Gateway en LECTURE SEULE
Dans TWS : `Configuration globale → API → Settings` :
- ✅ **Enable ActiveX and Socket Clients**
- ✅ **Read-Only API** (verrou anti-ordre)
- Trusted IP : `127.0.0.1` · Port : `7496` (réel) ou `7497` (paper)

### Étape 2 — Lancer Vertex
- **Windows** : double-clic sur **`lancer_dashboard.bat`**
- **Mac** : double-clic sur **`lancer_dashboard.command`**
- Puis ouvrir **http://localhost:5002** (ou `http://<IP-locale>:5002` sur l'iPhone, même WiFi).

Le badge en haut passe **🟢 LIVE IBKR** quand TWS est connecté.

### Étape 3 — TradingView
Le graphique TradingView est intégré sur chaque **fiche titre** (`/titre/<sym>`).
Pour des alertes TradingView → Vertex : webhook (nécessite TradingView Pro), à brancher en v1.1.

---

## 4. Vérifier que tout tourne

Ouvre **http://localhost:5002/healthz** :
```json
{"status":"ok","build":"v1.0","data_source":"ibkr|stooq|demo",
 "scanned":NN,"vertex_ready":NN,"engines":[...],"ibkr_enabled":true}
```
- `vertex_ready` > 0 → le noyau quant tourne sur tous les titres.
- `data_source":"ibkr"` → temps réel actif.

---

## 5. Sécurité (invariants non négociables)

- 🔒 **Connexion IBKR `readonly=True`** (verrou structurel côté IBKR)
- ⛔ **Aucun chemin d'ordre** (`placeOrder`/`bracketOrder`/… absents — vérifié par test CI)
- 💾 Favoris/notes **uniquement** dans le navigateur (localStorage), jamais côté serveur
- 📊 Chaque carte affiche « analyse éducative — jamais un conseil financier »

---

## 6. Cloud (Render) vs Local

| | Cloud (Render) | Bureau (PC + TWS) |
|---|---|---|
| Accès | partout (iPhone, 4G) | réseau local |
| Données | démo / différé | **temps réel IBKR** |
| Lancement | automatique | `lancer_dashboard.bat` |

⛔ ANALYSE ÉDUCATIVE — VERTEX ne passe aucun ordre et ne promet pas de battre le marché.
Son but : améliorer la **qualité des décisions** et la **gestion du risque**, avec discipline.
