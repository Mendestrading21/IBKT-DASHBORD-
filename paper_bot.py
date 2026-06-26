# -*- coding: utf-8 -*-
"""
TRACK — PAPER BOT  (portefeuille simulé : le bot ACHÈTE et VEND, zéro argent réel)
==================================================================================
Améliore la MÉTHODE (analyse + gestion) et simule un vrai portefeuille partagé :

  ANALYSE  : filtre de marché — n'ouvre des LONGS que si SPY > sa MM200 (ne pas
             se battre contre le marché). Sélection des meilleurs scores Track.
  GESTION  : capital partagé, max N positions concurrentes, sizing 1,5 % risque ;
             PRISE PARTIELLE 50 % à +1R puis stop au POINT MORT ; trailing
             chandelier large (4×ATR) sur le runner. Entrées au PROCHAIN open
             (pas de lookahead). Coûts 0,03 %/côté + slippage 0,05 %/côté.

Sortie : courbe d'equity, rendement, vs SPY buy&hold, drawdown, Sharpe, win rate.
⛔ ANALYSE / PAPER ONLY. Aucun ordre réel. Le bot prouve l'edge EN AVANÇANT.
"""
import sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
import pandas as pd
try:
    import yfinance as yf
except Exception as e:
    print("yfinance manquant :", e); sys.exit(1)
from stock_backtest import build_engine, precompute_signals, ATR_MULT

CAPITAL      = 1000.0
RISK_PCT     = 1.5
MAX_POS      = 5
CHAND_MULT   = 4.0
PARTIAL_FRAC = 0.5      # vendre 50 % à +1R
COMMISSION_PCT, SLIPPAGE_PCT = 0.03, 0.05
PERIOD, INTERVAL = "5y", "1d"
BENCH = "SPY"
UNIVERSE = ["AAPL","MSFT","NVDA","AMD","GOOGL","AMZN","META","LLY","UNH","JPM",
            "V","MA","AVGO","COST","HD","NFLX","CRM","ADBE","PG","XOM",
            "TSLA","ORCL","WMT","KO","PEP","BAC","DIS","INTC","QCOM","TXN"]

SL = SLIPPAGE_PCT/100.0
CM = COMMISSION_PCT/100.0
def comm(notional): return abs(notional)*CM

def main():
    print(f"[bot] téléchargement {len(UNIVERSE)+1} titres ({PERIOD} {INTERVAL}) ...")
    raw = yf.download(UNIVERSE+[BENCH], period=PERIOD, interval=INTERVAL,
                      auto_adjust=True, progress=False, group_by="ticker")
    eng = {}
    for tk in UNIVERSE:
        try:
            df = raw[tk].rename(columns=str.lower)[["open","high","low","close","volume"]].dropna()
        except Exception:
            continue
        if len(df) < 300: continue
        e = build_engine(df)
        if len(e) < 60: continue
        rows, sigs = precompute_signals(e)
        e = e.copy(); e["signal"] = sigs
        eng[tk] = {d: r for d, r in zip(e.index, e.to_dict("records"))}
    # filtre marché SPY > MM200
    spy = raw[BENCH].rename(columns=str.lower)[["close"]].dropna()
    spy["sma200"] = spy["close"].rolling(200).mean()
    spy_ok = {d: (bool(c > s) if pd.notna(s) else True)
              for d, c, s in zip(spy.index, spy["close"], spy["sma200"])}
    spy_close = {d: c for d, c in zip(spy.index, spy["close"])}

    dates = sorted(set().union(*[set(d.keys()) for d in eng.values()]))
    positions = {}          # tk -> dict(qty,entry,stop,risk,partial)
    pending = []            # tk décidés hier -> exécutés à l'open d'aujourd'hui
    cash = CAPITAL
    trades = []            # legs de vente (pnl)
    eqc = []              # (date, equity)
    in_market_days = 0; concur = []

    def equity_at(d):
        return cash + sum(positions[t]["qty"]*eng[t][d]["close"]
                          for t in positions if d in eng[t])

    for d in dates:
        # 1) exécuter les entrées en attente à l'OPEN du jour
        newpend = []
        for tk in pending:
            if tk in positions or d not in eng[tk]:
                continue
            row = eng[tk][d]; o = row["open"]; atrv = row["atr"]
            if o <= 0 or atrv <= 0: continue
            stopDist = ATR_MULT*atrv
            eq_now = equity_at(d)
            qty = (eq_now*RISK_PCT/100.0)/stopDist
            entryPx = o*(1+SL)
            cost = qty*entryPx
            if cost > cash:                      # plafonné par le cash dispo
                qty = max(0.0, (cash*0.98)/entryPx); cost = qty*entryPx
            if qty > 0 and cost > 5:
                cash -= cost + comm(cost)
                positions[tk] = dict(qty=qty, entry=entryPx, stop=entryPx-stopDist,
                                     risk=stopDist, partial=False)
        pending = newpend

        mkt = spy_ok.get(d, True)

        # 2) gérer les positions ouvertes
        for tk in list(positions):
            if d not in eng[tk]: continue
            row = eng[tk][d]; o=row["open"]; h=row["high"]; l=row["low"]; c=row["close"]
            p = positions[tk]
            # stop touché (intrabar)
            if l <= p["stop"]:
                px = (p["stop"] if o >= p["stop"] else o)*(1-SL)
                pnl = (px-p["entry"])*p["qty"] - comm(px*p["qty"])
                cash += px*p["qty"] - comm(px*p["qty"])
                trades.append(pnl); del positions[tk]; continue
            # prise partielle à +1R -> stop point mort
            if not p["partial"] and h >= p["entry"]+p["risk"]:
                half = p["qty"]*PARTIAL_FRAC
                px = (p["entry"]+p["risk"])*(1-SL)
                pnl = (px-p["entry"])*half - comm(px*half)
                cash += px*half - comm(px*half)
                trades.append(pnl)
                p["qty"] -= half; p["partial"] = True
                p["stop"] = max(p["stop"], p["entry"])      # break-even
            # trailing chandelier sur le runner
            if p["partial"] or (c-p["entry"])/p["risk"] >= 1.0:
                ch = row["chHigh"] - CHAND_MULT*row["chATR"]
                p["stop"] = max(p["stop"], min(ch, c*0.999))

        # 3) nouvelles entrées (si marché OK et slots libres) -> queue pour demain
        if mkt:
            slots = MAX_POS - len(positions) - len(pending)
            if slots > 0:
                cands = [(tk, eng[tk][d]["net"]) for tk in eng
                         if d in eng[tk] and tk not in positions and tk not in pending
                         and eng[tk][d]["signal"] and eng[tk][d]["net"] > 0]
                cands.sort(key=lambda x: -x[1])
                for tk, _ in cands[:slots]:
                    pending.append(tk)

        # 4) mark-to-market
        if positions: in_market_days += 1
        concur.append(len(positions))
        eqc.append((d, equity_at(d)))

    # ── rapport ──
    eqs = pd.Series([e for _, e in eqc], index=[d for d, _ in eqc])
    final = eqs.iloc[-1]; ret = (final/CAPITAL-1)*100
    yrs = len(eqs)/252.0
    cagr = ((final/CAPITAL)**(1/yrs)-1)*100 if yrs > 0 else 0
    peak = eqs.cummax(); maxdd = ((peak-eqs)/peak*100).max()
    dret = eqs.pct_change().dropna()
    sharpe = (dret.mean()/dret.std()*np.sqrt(252)) if dret.std() > 0 else 0
    wins = [t for t in trades if t > 0]; losses = [t for t in trades if t <= 0]
    wr = len(wins)/len(trades)*100 if trades else 0
    pf = sum(wins)/(-sum(losses)) if losses and sum(losses) < 0 else float("inf")
    # SPY buy&hold sur la même fenêtre
    bench_dates = [d for d, _ in eqc if d in spy_close]
    spy_ret = (spy_close[bench_dates[-1]]/spy_close[bench_dates[0]]-1)*100
    spy_eq = pd.Series([spy_close[d] for d in bench_dates], index=bench_dates)
    spy_dd = ((spy_eq.cummax()-spy_eq)/spy_eq.cummax()*100).max()

    print("\n" + "="*72)
    print(" TRACK — PAPER BOT — portefeuille simulé (le bot achète & vend)")
    print("="*72)
    print(f" Période   : {eqs.index[0]:%Y-%m-%d} -> {eqs.index[-1]:%Y-%m-%d}  ({yrs:.1f} ans)")
    print(f" Univers   : {len(eng)} titres  |  max {MAX_POS} positions  |  risque {RISK_PCT}%/trade")
    print(f" Réglages  : filtre marché SPY>MM200 · partielle 50% à 1R · trailing {CHAND_MULT}xATR")
    print("-"*72)
    print(f" Capital final     : {final:,.0f} $   (départ {CAPITAL:.0f} $)")
    print(f" Rendement total   : {ret:+.1f} %    |   CAGR {cagr:+.1f} %/an")
    print(f" Max drawdown      : {maxdd:.1f} %")
    print(f" Sharpe            : {sharpe:.2f}")
    print(f" Win rate          : {wr:.1f} %   ({len(trades)} legs de sortie)")
    print(f" Profit factor     : {pf:.2f}")
    print(f" Temps investi     : {in_market_days/len(eqc)*100:.0f} %   |  positions moy {np.mean(concur):.1f}")
    print("-"*72)
    print(f" >>> COMPARAISON SPY (buy & hold, même période) <<<")
    print(f"   BOT  : {ret:+.1f} %   maxDD {maxdd:.1f} %")
    print(f"   SPY  : {spy_ret:+.1f} %   maxDD {spy_dd:.1f} %")
    verdict = "le BOT bat le SPY" if ret > spy_ret else "le SPY (buy&hold) gagne"
    print(f"   -> {verdict}.  (rappel : protéger le capital compte autant que le rendement)")
    print("="*72)
    print(" ANALYSE / PAPER ONLY — aucun ordre réel. Forward-test avant tout réel.")
    print("="*72)

if __name__ == "__main__":
    main()
