# -*- coding: utf-8 -*-
"""
TRACK — MOTEUR D'OPPORTUNITÉS QUOTIDIENNES  ("le script formidable")
====================================================================
Chaque jour : scanne TOUT l'univers, applique l'edge VALIDÉ (moteur Track 7-D,
même code que les backtests), et sort les meilleures opportunités LONG du moment
avec le plan exact (entrée / stop / cibles) et la TAILLE pour TON capital à 1,5 %
de risque. Réutilise le moteur de stock_backtest.py (edge mesuré : PF ~1.5,
expectancy ~+0.4 R).

⛔ ANALYSE ONLY. Aucun ordre. Ce n'est pas une promesse de gain — c'est une aide
à la décision honnête. Magnitude réaliste : un trade gagnant ~+2R, perdant ~-1R.
Pas de "2 % par jour" : ça n'existe pas.
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows cp1252 -> UTF-8 (emoji/accents)
except Exception:
    pass
import numpy as np
import pandas as pd
try:
    import yfinance as yf
except Exception as e:
    print("yfinance manquant :", e); sys.exit(1)

# on réutilise le MOTEUR VALIDÉ (mêmes formules que les backtests)
from stock_backtest import build_engine, precompute_signals, ATR_MULT

# ───────── config ─────────
CAPITAL   = 1000.0     # ton capital
RISK_PCT  = 1.5        # risque par trade
LOOKBACK  = 3          # un signal des N derniers jours = "frais / actionnable"
WATCHLIST = ["AAPL","MSFT","NVDA","AMD","GOOGL","AMZN","META","LLY","UNH","JPM",
             "V","MA","AVGO","COST","HD","NFLX","CRM","ADBE","PG","XOM",
             "TSLA","ORCL","WMT","KO","PEP","BAC","DIS","INTC","QCOM","TXN"]
PERIOD, INTERVAL = "2y", "1d"

REG = {2:"HAUSSE FORTE", 1:"Hausse", 0:"Range", -1:"Baisse", -2:"BAISSE FORTE"}

def analyze(tk, df):
    e = build_engine(df)
    if len(e) < 50:
        return None
    rows, sigs = precompute_signals(e)
    last = rows[-1]
    price = last["close"]; a = last["atr"]
    if a <= 0 or price <= 0:
        return None
    # signal récent ?
    fired_idx = None
    for i in range(len(sigs)-1, max(-1, len(sigs)-1-LOOKBACK), -1):
        if sigs[i]:
            fired_idx = i; break
    bars_since = (len(sigs)-1 - fired_idx) if fired_idx is not None else None
    stop_dist = ATR_MULT * a
    stop = price - stop_dist
    qty = min((CAPITAL * RISK_PCT/100.0) / stop_dist, CAPITAL / price)
    risk_d = qty * stop_dist
    return dict(
        tk=tk, score=last["net"], regime=int(last["rawRegime"]),
        price=price, atr=a, fresh=(fired_idx is not None), bars_since=bars_since,
        stop=stop, t1=price+stop_dist, t2=price+2*stop_dist, t3=price+3*stop_dist,
        qty=qty, notional=qty*price, risk_d=risk_d, gain2R=2*risk_d)

def main():
    print(f"[scan] {len(WATCHLIST)} titres via yfinance ({PERIOD} {INTERVAL}) ...")
    raw = yf.download(WATCHLIST, period=PERIOD, interval=INTERVAL,
                      auto_adjust=True, progress=False, group_by="ticker")
    res = []
    asof = None
    for tk in WATCHLIST:
        try:
            df = raw[tk].rename(columns=str.lower)[["open","high","low","close","volume"]].dropna()
        except Exception:
            continue
        if len(df) < 260:
            continue
        asof = df.index[-1]
        r = analyze(tk, df)
        if r:
            res.append(r)
    if not res:
        print("Aucune donnée."); return

    fresh = sorted([r for r in res if r["fresh"] and r["score"] > 0],
                   key=lambda x: -x["score"])
    watch = sorted([r for r in res if not r["fresh"] and r["score"] > 15],
                   key=lambda x: -x["score"])

    print("\n" + "="*82)
    print(f" TRACK — OPPORTUNITÉS DU JOUR   (au {asof:%Y-%m-%d})   capital {CAPITAL:.0f} $ · risque {RISK_PCT}%/trade")
    print("="*82)

    if fresh:
        print(f"\n  🎯 SETUPS ACTIONNABLES — signal LONG des {LOOKBACK} derniers jours ({len(fresh)})")
        print("  " + "-"*78)
        print(f"  {'TITRE':6s}{'score':>6s}{'régime':>14s}{'prix':>9s}{'stop':>9s}{'cible 2R':>10s}{'qté':>8s}{'risque':>8s}")
        for r in fresh:
            print(f"  {r['tk']:6s}{r['score']:+6.0f}{REG[r['regime']]:>14s}{r['price']:>9.2f}"
                  f"{r['stop']:>9.2f}{r['t2']:>10.2f}{r['qty']:>8.3f}{r['risk_d']:>7.0f}$")
            print(f"         → entrée {r['price']:.2f} · stop {r['stop']:.2f} (-{r['price']-r['stop']:.2f}) · "
                  f"T1 {r['t1']:.2f} / T2 {r['t2']:.2f} / T3 {r['t3']:.2f} · "
                  f"notionnel {r['notional']:.0f}$ · gain à 2R ≈ +{r['gain2R']:.0f}$ ({r['gain2R']/CAPITAL*100:.1f}%)")
    else:
        print("\n  🎯 Aucun setup LONG frais aujourd'hui. (Normal : l'edge est sélectif, ~1 jour/3 investi.)")

    if watch:
        print(f"\n  👁 SOUS SURVEILLANCE — score haussier, pas encore de déclencheur ({len(watch)})")
        print("  " + "-"*78)
        for r in watch[:12]:
            print(f"  {r['tk']:6s} score {r['score']:+4.0f} · {REG[r['regime']]:14s} · prix {r['price']:.2f}")

    print("\n" + "-"*82)
    print("  RÉALITÉ CHIFFRÉE (edge validé sur 5 ans) : ~+0,4 R/trade, win ~35 %, ~1 trade/jour")
    print("  sur tout l'univers. Un gagnant ≈ +2 % du compte, un perdant ≈ -1,5 %. PAS 2 %/jour.")
    print("  Le moteur est un BOUCLIER (drawdown ~8 % vs 46 % en buy&hold), pas une machine à cash.")
    print("  ⛔ ANALYSE ONLY — tu valides et tu passes l'ordre. Jamais d'exécution automatique.")
    print("="*82)

if __name__ == "__main__":
    main()
