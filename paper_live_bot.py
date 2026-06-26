# -*- coding: utf-8 -*-
"""
TRACK — BOT PAPER AUTONOME (live forward, état persistant)
==========================================================
Le bot que tu ACTIVES et qui fait TOUT TOUT SEUL — en PAPER (simulation).
À chaque exécution (« tick »), il :
  1. récupère les dernières données du jour ;
  2. GÈRE seul ses positions ouvertes (prise partielle à +1R -> point mort,
     trailing chandelier, stop) ;
  3. DÉCIDE et ACHÈTE seul de nouvelles positions (filtre marché SPY>MM200,
     meilleurs scores Track, max 5 positions, risque 1,5 %) ;
  4. sauvegarde son état (cash, positions, journal, equity) dans paper_state.json.

Lance-le chaque jour (ou via tâche planifiée) → il avance tout seul dans le temps.
Config = la version DISCIPLINÉE (celle qui gagne au backtest), PAS l'agressive.

⛔ PAPER / SIMULATION UNIQUEMENT. Zéro ordre réel, zéro argent réel. C'est le
forward-test qui prouve l'edge EN AVANÇANT, avant tout passage au réel (semi-auto).
   Usage :  py paper_live_bot.py          (un tick)
            py paper_live_bot.py --reset   (réinitialise le compte à 1000 $)
"""
import sys, os, json
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
import pandas as pd
try:
    import yfinance as yf
except Exception as e:
    print("yfinance manquant :", e); sys.exit(1)
from stock_backtest import build_engine, precompute_signals, ATR_MULT

STATE_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_state.json")
INIT_CAPITAL = 1000.0
RISK_PCT     = 1.5
MAX_POS      = 5
CHAND_MULT   = 4.0
PARTIAL_FRAC = 0.5
COMMISSION_PCT, SLIPPAGE_PCT = 0.03, 0.05
PERIOD, INTERVAL = "2y", "1d"
BENCH = "SPY"
UNIVERSE = ["AAPL","MSFT","NVDA","AMD","GOOGL","AMZN","META","LLY","UNH","JPM",
            "V","MA","AVGO","COST","HD","NFLX","CRM","ADBE","PG","XOM",
            "TSLA","ORCL","WMT","KO","PEP","BAC","DIS","INTC","QCOM","TXN"]
CM = COMMISSION_PCT/100.0
SLP = SLIPPAGE_PCT/100.0

def fresh_state():
    return dict(cash=INIT_CAPITAL, positions={}, journal=[], equity=[], last_bar=None, init=INIT_CAPITAL)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return fresh_state()

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, default=str)

def main():
    reset = "--reset" in sys.argv
    s = fresh_state() if reset else load_state()
    if reset:
        print("[reset] compte paper réinitialisé à", INIT_CAPITAL, "$")

    print(f"[bot] tick — téléchargement {len(UNIVERSE)+1} titres ...")
    raw = yf.download(UNIVERSE+[BENCH], period=PERIOD, interval=INTERVAL,
                      auto_adjust=True, progress=False, group_by="ticker")
    # filtre marché
    spy = raw[BENCH].rename(columns=str.lower)[["close"]].dropna()
    spy_sma = spy["close"].rolling(200).mean()
    mkt_ok = bool(spy["close"].iloc[-1] > spy_sma.iloc[-1]) if pd.notna(spy_sma.iloc[-1]) else True

    latest = {}
    for tk in UNIVERSE:
        try:
            df = raw[tk].rename(columns=str.lower)[["open","high","low","close","volume"]].dropna()
        except Exception:
            continue
        if len(df) < 260:
            continue
        e = build_engine(df)
        if len(e) < 10:
            continue
        rows, sigs = precompute_signals(e)
        latest[tk] = dict(row=rows[-1], sig=bool(sigs[-1]), date=str(e.index[-1].date()))
    if not latest:
        print("Pas de données exploitables."); return

    bar_date = max(v["date"] for v in latest.values())
    new_day = (s.get("last_bar") != bar_date)
    actions = []

    def cur_equity():
        return s["cash"] + sum(p["qty"]*latest[t]["row"]["close"]
                               for t, p in s["positions"].items() if t in latest)

    if new_day:
        # 1) GESTION des positions ouvertes
        for tk in list(s["positions"]):
            if tk not in latest:
                continue
            row = latest[tk]["row"]; h=row["high"]; l=row["low"]; c=row["close"]
            chH=row["chHigh"]; chA=row["chATR"]; p=s["positions"][tk]
            # prise partielle à +1R -> point mort
            if not p["partial"] and h >= p["entry"]+p["risk"]:
                half = p["qty"]*PARTIAL_FRAC; px=(p["entry"]+p["risk"])*(1-SLP)
                pnl = (px-p["entry"])*half - (px*half)*CM
                s["cash"] += px*half - (px*half)*CM
                p["qty"] -= half; p["partial"]=True; p["stop"]=max(p["stop"], p["entry"])
                actions.append(f"PARTIELLE {tk} : vendu {half:.3f} @ {px:.2f} (+1R) → stop au point mort")
                s["journal"].append(dict(date=bar_date, tk=tk, act="TP1", qty=round(half,4), px=round(px,2), pnl=round(pnl,2)))
            # trailing chandelier
            if p["partial"] or (c-p["entry"]) >= p["risk"]:
                p["stop"] = max(p["stop"], min(chH - CHAND_MULT*chA, c*0.999))
            # stop touché
            if l <= p["stop"]:
                px = p["stop"]*(1-SLP); pnl=(px-p["entry"])*p["qty"] - (px*p["qty"])*CM
                s["cash"] += px*p["qty"] - (px*p["qty"])*CM
                actions.append(f"STOP {tk} : vendu {p['qty']:.3f} @ {px:.2f}  (P&L {pnl:+.2f}$)")
                s["journal"].append(dict(date=bar_date, tk=tk, act="STOP", qty=round(p["qty"],4), px=round(px,2), pnl=round(pnl,2)))
                del s["positions"][tk]
        # 2) NOUVELLES ENTRÉES (filtre marché + slots libres)
        if mkt_ok:
            slots = MAX_POS - len(s["positions"])
            cands = sorted([(tk, latest[tk]) for tk in latest
                            if tk not in s["positions"] and latest[tk]["sig"] and latest[tk]["row"]["net"] > 0],
                           key=lambda x: -x[1]["row"]["net"])
            eq = cur_equity()
            for tk, info in cands[:max(0, slots)]:
                row = info["row"]; c=row["close"]; a=row["atr"]
                if a <= 0:
                    continue
                stopDist = ATR_MULT*a
                qty = (eq*RISK_PCT/100.0)/stopDist
                cost = qty*c*(1+SLP)
                if cost > s["cash"]:
                    qty = (s["cash"]*0.98)/(c*(1+SLP)); cost = qty*c*(1+SLP)
                if qty > 0 and cost > 5:
                    s["cash"] -= cost + (qty*c)*CM
                    s["positions"][tk] = dict(qty=qty, entry=c*(1+SLP), stop=c-stopDist,
                                              risk=stopDist, partial=False, date=bar_date)
                    actions.append(f"ACHAT {tk} : {qty:.3f} @ {c*(1+SLP):.2f}  stop {c-stopDist:.2f}  (score {row['net']:+.0f})")
                    s["journal"].append(dict(date=bar_date, tk=tk, act="BUY", qty=round(qty,4), px=round(c*(1+SLP),2), pnl=0.0))
        else:
            actions.append("Marché sous MM200 → aucune nouvelle entrée (filtre actif)")
        s["last_bar"] = bar_date
        s["equity"].append([bar_date, round(cur_equity(), 2)])

    save_state(s)

    # ── DASHBOARD ──
    eq = cur_equity()
    ret = (eq/s["init"]-1)*100
    print("\n" + "="*70)
    print(f"  🤖 TRACK — BOT PAPER AUTONOME   (au {bar_date})")
    print("="*70)
    print(f"  Equity : {eq:,.2f} $   ({ret:+.2f} % depuis {s['init']:.0f} $)   |   cash {s['cash']:,.2f} $")
    print(f"  Marché : {'▲ haussier (entrées ON)' if mkt_ok else '▼ baissier (entrées OFF)'}   |   positions {len(s['positions'])}/{MAX_POS}")
    if not new_day:
        print(f"  (déjà à jour pour {bar_date} — pas de nouveau tick ; MTM rafraîchi)")
    print("-"*70)
    if actions:
        print("  ACTIONS DE CE TICK :")
        for a in actions:
            print("   • " + a)
    else:
        print("  Aucune action ce tick (rien à faire — c'est normal, l'edge est sélectif).")
    print("-"*70)
    if s["positions"]:
        print("  POSITIONS OUVERTES :")
        print(f"   {'TITRE':6s}{'qté':>9s}{'entrée':>9s}{'actuel':>9s}{'stop':>9s}{'P&L':>9s}")
        for tk, p in s["positions"].items():
            c = latest[tk]["row"]["close"] if tk in latest else p["entry"]
            pl = (c-p["entry"])*p["qty"]
            plp = (c/p["entry"]-1)*100
            print(f"   {tk:6s}{p['qty']:>9.3f}{p['entry']:>9.2f}{c:>9.2f}{p['stop']:>9.2f}{pl:>+8.2f}$  ({plp:+.1f}%)")
    else:
        print("  Aucune position ouverte.")
    # journal récent
    if s["journal"]:
        print("-"*70)
        print("  JOURNAL (8 derniers mouvements) :")
        for j in s["journal"][-8:]:
            tag = {"BUY":"ACHAT","TP1":"PARTIEL","STOP":"VENTE"}.get(j["act"], j["act"])
            extra = "" if j["act"]=="BUY" else f"  P&L {j['pnl']:+.2f}$"
            print(f"   {j['date']}  {tag:8s} {j['tk']:6s} {j['qty']:.3f} @ {j['px']:.2f}{extra}")
    # 📋 TICKETS IBKR — le bot PRÉPARE l'ordre, TOI tu le passes (semi-auto)
    if s["positions"] or any(a.startswith(("ACHAT","STOP","PARTIELLE")) for a in actions):
        print("-"*70)
        print("  📋 ORDRES À PASSER À LA MAIN CHEZ IBKR (semi-auto — tu valides chaque ordre) :")
        for a in actions:
            if a.startswith("ACHAT"):
                seg = a.split(":",1)[1].strip()
                print(f"     ➤ BUY  MKT  {seg}")
        for tk, p in s["positions"].items():
            print(f"     ➤ SELL STP  {p['qty']:.3f} {tk:6s} @ {p['stop']:.2f}   (stop protecteur à maintenir)")
    # 📈 PERFORMANCE RÉALISÉE du bot (paper, forward)
    closed = [j for j in s["journal"] if j["act"] in ("TP1","STOP")]
    if closed:
        rp = sum(j["pnl"] for j in closed)
        wr = sum(1 for j in closed if j["pnl"] > 0)/len(closed)*100
        eqv = [e[1] for e in s["equity"]] or [s["init"]]
        peak = max(eqv); dd = (peak-eq)/peak*100 if peak > 0 else 0.0
        print("-"*70)
        print(f"  📈 RÉALISÉ (paper) : {len(closed)} sorties · {wr:.0f}% gagnantes · P&L réalisé {rp:+.2f}$ · drawdown {dd:.1f}%")
    print("="*70)
    print("  ⛔ PAPER ONLY — simulation. Relance chaque jour : le bot avance seul.")
    print("  État sauvegardé :", os.path.basename(STATE_FILE))
    print("="*70)

if __name__ == "__main__":
    main()
