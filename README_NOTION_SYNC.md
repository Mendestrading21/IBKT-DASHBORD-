# 🔄 notion_sync.py — Sync IBKR → Notion (Trading Cockpit 2026)

Met à jour le cockpit Notion avec **tes vraies données IBKR**, en local, sans
limite de plan Notion et sans dépendre du run cloud. **Lecture seule côté
IBKR — aucun ordre n'est jamais placé.**

Ce qu'il fait à chaque exécution :
- **Compte** → met à jour `Brokers & Comptes` (NAV, cash, valeur positions, levier).
- **Prix** → pour chaque titre de la watchlist `Actifs`, met à jour
  `Prix actuel`, `Var % jour`, `Dernière MAJ` (cours du jour via IBKR).

> Les crypto / FX / commodités / indices (BTC, XAU, WTI, DXY, VIX…) sont sautés :
> ils ne passent pas par la chaîne actions SMART. Le briefing cloud les couvre.

---

## 1. Installation (une fois)
```bash
cd track-python
pip install -r requirements-notion.txt
```

## 2. Créer l'intégration Notion (une fois)
1. Va sur **https://www.notion.so/my-integrations** → **New integration** → copie le **token** (`secret_…`).
2. Ouvre la page **🔱 Trading Cockpit 2026** dans Notion → menu **⋯** en haut à droite → **Connexions** → ajoute ton intégration. *(Ça donne accès à toutes les bases enfants.)*
3. Copie `.env.example` en `.env` et colle le token :
   ```
   NOTION_TOKEN=secret_xxxxxxxx
   ```

## 3. Lancer
TWS / IB Gateway ouvert (API activée), puis :
```bash
python notion_sync.py            # compte + prix
python notion_sync.py --account  # compte seulement
python notion_sync.py --prices   # prix seulement
```

## 4. Automatiser (tous les matins)
**Windows — Planificateur de tâches :**
- Action : `Démarrer un programme`
- Programme : `python` (ou chemin complet de python.exe)
- Arguments : `notion_sync.py`
- Démarrer dans : le dossier `track-python`
- Déclencheur : tous les jours à 14h30 (pré-ouverture US) — adapte à ton goût.

*(TWS doit être ouvert au moment du run.)*

---

## Notes
- Les IDs des 17 bases sont dans `notion_sync.py` (dict `DB`). Si tu recrées le
  cockpit, mets-les à jour.
- Les colonnes formule (P/L, DTE, Urgence) ne sont **pas** écrites : Notion les
  calcule seul.
- Symboles spéciaux : `BRK.B` → `BRK B` (table `SYMBOL_MAP`). Ajoute-en au besoin.
- 100 % lecture côté IBKR (`readonly=True` dans `ib_reader.py`). Aucun module
  d'ordre n'est importé.
