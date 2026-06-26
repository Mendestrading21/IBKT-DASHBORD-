"""
notion_sync.py — Sync IBKR (LECTURE SEULE) -> Notion "Trading Cockpit 2026".

Tire le compte + les prix depuis IBKR (via ib_reader.py, connexion readonly)
et met a jour la page Notion : base "Actifs" (prix, var %, derniere MAJ) et
"Brokers & Comptes" (NAV, cash, valeur positions, levier).

  >>> ANALYSE ONLY — ce script ne place AUCUN ordre, par design. <<<

Pre-requis
----------
  1. TWS ou IB Gateway ouvert, API activee (Global Config > API > Settings).
  2. Une integration Notion (token) qui a acces a la page du cockpit :
     https://www.notion.so/my-integrations  ->  cree l'integration,
     puis sur la page du cockpit : ... > Connexions > ajoute l'integration.
  3. pip install -r requirements-notion.txt

Usage
-----
  set/exports NOTION_TOKEN=secret_xxx   (ou fichier .env)
  python notion_sync.py            # compte + prix
  python notion_sync.py --account  # compte seulement
  python notion_sync.py --prices   # prix seulement
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from ib_reader import IBKRReader

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

# IDs des bases du cockpit (page racine 38a9b704-4a53-817d-b9d5-dee6118f297e).
DB = {
    "actifs":    "607ead6ce6e54dd5936b5e4d0f041be1",
    "positions": "4cc8f72666c04cc18de305327439256e",
    "brokers":   "473e05a13ae3441cb9057711c66bdcae",
    "options":   "c405e4365059417db8ab8832071f32f0",
    "briefing":  "00f83a462d6f4b0f86e6cdb8648f1bfc",
}

# Symbole Notion -> symbole IBKR quand ils different.
SYMBOL_MAP = {"BRK.B": "BRK B"}

# Prefixes non-actions US : on ne tente PAS la chaine SMART/STK (crypto, FX,
# commodites, indices). Le briefing cloud / le web gerent ceux-la.
SKIP_PREFIXES = ("BTC", "ETH", "XAU", "XAG", "WTI", "COPPER", "NATGAS", "DXY", "VIX")


# --------------------------------------------------------------------------
# Notion REST helpers
# --------------------------------------------------------------------------
def _headers() -> dict:
    if not NOTION_TOKEN:
        sys.exit("ERREUR: NOTION_TOKEN manquant (export NOTION_TOKEN=... ou .env).")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_query(db_id: str) -> list[dict]:
    """Toutes les pages d'une base (avec pagination)."""
    out: list[dict] = []
    payload = {"page_size": 100}
    url = f"{API}/databases/{db_id}/query"
    while True:
        r = requests.post(url, headers=_headers(), json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        out.extend(data["results"])
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]
    return out


def notion_update(page_id: str, props: dict) -> None:
    r = requests.patch(
        f"{API}/pages/{page_id}",
        headers=_headers(),
        json={"properties": props},
        timeout=30,
    )
    r.raise_for_status()


def _plain(prop: dict) -> str:
    for key in ("rich_text", "title"):
        if prop.get(key):
            return "".join(t["plain_text"] for t in prop[key])
    return ""


def today() -> str:
    return dt.date.today().isoformat()


# --------------------------------------------------------------------------
# Prix via IBKR (reutilise ib_reader.historical_bars, connexion readonly)
# --------------------------------------------------------------------------
def last_and_change(reader: IBKRReader, symbol: str):
    """(dernier cours, variation % vs cloture precedente) depuis les bougies jour."""
    sym = SYMBOL_MAP.get(symbol, symbol)
    bars = reader.historical_bars(sym, duration="5 D", bar_size="1 day")
    if bars is None or len(bars) < 2:
        return None, None
    last = float(bars.iloc[-1]["close"])
    prev = float(bars.iloc[-2]["close"])
    pct = (last - prev) / prev * 100 if prev else 0.0
    return round(last, 2), round(pct, 2)


def sync_prices(reader: IBKRReader) -> None:
    pages = notion_query(DB["actifs"])
    ok = skip = err = 0
    print(f"[Prix] {len(pages)} actifs dans la watchlist...")
    for pg in pages:
        ticker = _plain(pg["properties"].get("Ticker", {})).strip().upper()
        if not ticker or ticker.startswith(SKIP_PREFIXES):
            skip += 1
            continue
        try:
            last, pct = last_and_change(reader, ticker)
            if last is None:
                skip += 1
                continue
            notion_update(pg["id"], {
                "Prix actuel": {"number": last},
                "Var % jour": {"number": pct},
                "Dernière MAJ": {"date": {"start": today()}},
            })
            ok += 1
            print(f"  {ticker:8} {last:>11}  {pct:+.2f}%")
        except Exception as e:  # ticker non resolu / pas de data / autre
            err += 1
            print(f"  {ticker:8} ERREUR: {e}")
        time.sleep(0.05)  # courtoisie API Notion
    print(f"[Prix] {ok} mis a jour, {skip} sautes, {err} erreurs.")


def sync_account(reader: IBKRReader) -> None:
    s = reader.account_summary()
    nav = float(s.get("NetLiquidation", 0) or 0)
    cash = float(s.get("TotalCashValue", 0) or 0)
    gpv = float(s.get("GrossPositionValue", 0) or 0)
    lev = round(gpv / nav, 2) if nav else 0.0
    pages = notion_query(DB["brokers"])
    if not pages:
        print("[Compte] Aucune ligne dans 'Brokers & Comptes'.")
        return
    notion_update(pages[0]["id"], {
        "NAV": {"number": round(nav, 2)},
        "Cash": {"number": round(cash, 2)},
        "Valeur positions": {"number": round(gpv, 2)},
        "Levier": {"number": lev},
        "Dernière MAJ": {"date": {"start": today()}},
    })
    print(f"[Compte] NAV {nav:.2f} | cash {cash:.2f} | positions {gpv:.2f} | levier {lev}")


def main() -> None:
    args = sys.argv[1:]
    do_acct = (not args) or ("--account" in args)
    do_px = (not args) or ("--prices" in args)
    reader = IBKRReader().connect_auto()
    try:
        if do_acct:
            sync_account(reader)
        if do_px:
            sync_prices(reader)
    finally:
        reader.disconnect()
    print("[OK] Sync terminee. ANALYSE ONLY — aucun ordre n'a ete place.")


if __name__ == "__main__":
    main()
