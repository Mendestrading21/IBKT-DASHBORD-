"""
elio/market.py — Contexte marché du jour (la "météo" avant de regarder un titre).

Régime du SPY lui-même (TREND/CHOP/NEUTRAL), niveau VIX + bande, Risk-On/Risk-Off
(force secteurs cycliques vs défensifs), breadth détaillée (participation), et une
phrase "verdict du jour" qui résume tout. Tout depuis les données déjà téléchargées.

⚠️ Honnête : breadth = sur les 45 leaders (pas le $ADD NYSE). RORO = interne au panier
leaders growth (pas de XLP/XLU/TLT). yfinance différé ~15min.
"""
import math

import numpy as np
import pandas as pd

RISK_ON = ['Semiconducteurs', 'Software', 'Big Tech', 'Crypto', 'Infra-IA']
DEFENSIVE = ['Conso', 'Sante', 'Finance']


def _adx(df, n=14):
    h, l, c = df['High'], df['Low'], df['Close']
    up, dn = h.diff(), -l.diff()
    plus = ((up > dn) & (up > 0)) * up
    minus = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return float(dx.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def context(spy_df, vix_close, rows, detail, secs):
    out = {'spy_regime': None, 'spy_trend_txt': None, 'spy_adx': None,
           'vix': None, 'vix_band': None, 'vix_chg': None,
           'roro': None, 'roro_gap': None, 'breadth': {}, 'verdict': None}

    # — Régime du SPY (le marché lui-même, pas un titre) —
    try:
        c = spy_df['Close']
        adx = _adx(spy_df)
        tr14 = pd.concat([spy_df['High'] - spy_df['Low'], (spy_df['High'] - c.shift()).abs(),
                          (spy_df['Low'] - c.shift()).abs()], axis=1).max(axis=1).tail(14).sum()
        rng14 = float(spy_df['High'].tail(14).max() - spy_df['Low'].tail(14).min())
        chop = float(100 * math.log10(tr14 / rng14) / math.log10(14)) if rng14 > 0 else 50.0
        out['spy_regime'] = 'TREND' if adx >= 22 and chop < 55 else 'CHOP' if chop >= 62 else 'NEUTRAL'
        out['spy_adx'] = round(adx)
        e20 = float(c.ewm(span=20).mean().iloc[-1])
        s50 = float(c.rolling(50).mean().iloc[-1])
        last = float(c.iloc[-1])
        out['spy_trend_txt'] = ('au-dessus MM20 & MM50' if last > e20 and last > s50
                                else 'sous la MM20' if last < e20 else 'mitigé')
    except Exception:
        pass

    # — VIX —
    try:
        v = vix_close.dropna() if vix_close is not None else None
        if v is not None and len(v) > 1:
            out['vix'] = round(float(v.iloc[-1]), 1)
            out['vix_chg'] = round((float(v.iloc[-1]) / float(v.iloc[-2]) - 1) * 100, 1)
            out['vix_band'] = 'calme' if out['vix'] < 16 else 'normal' if out['vix'] < 22 else 'stress'
    except Exception:
        pass

    # — Breadth détaillée (participation sur les 45 leaders) —
    try:
        N = len(rows) or 1

        def pct(key):
            return round(100 * sum(1 for s in detail if (detail[s].get('signals') or {}).get(key)) / N)
        adv = sum(1 for r in rows if r['change'] > 0)
        out['breadth'] = {
            'above50': pct('above50'), 'above200': pct('above200'),
            'adv': adv, 'dec': N - adv,
            'nh': sum(1 for s in detail if detail[s].get('pos52', 0) >= 98),
            'nl': sum(1 for s in detail if detail[s].get('pos52', 100) <= 5),
            'buy': round(100 * sum(1 for r in rows if r['verdict'] == 'BUY') / N),
        }
    except Exception:
        pass

    # — Risk-On / Risk-Off (cyclique vs défensif) —
    try:
        sm = {s['sector']: s['avg_score'] for s in secs}
        ro = np.mean([sm[x] for x in RISK_ON if x in sm]) if any(x in sm for x in RISK_ON) else 50
        de = np.mean([sm[x] for x in DEFENSIVE if x in sm]) if any(x in sm for x in DEFENSIVE) else 50
        gap = round(float(ro - de))
        out['roro'] = 'RISK-ON' if gap >= 8 else 'RISK-OFF' if gap <= -8 else 'NEUTRE'
        out['roro_gap'] = gap
    except Exception:
        pass

    # — Verdict du jour (la phrase qui résume tout) —
    try:
        reg = {'TREND': 'MARCHÉ EN TENDANCE', 'CHOP': 'MARCHÉ EN RANGE AGITÉ',
               'NEUTRAL': 'MARCHÉ NEUTRE'}.get(out['spy_regime'], 'MARCHÉ')
        b = out['breadth']
        bits = [reg, out['roro'] or '', f"participation {b.get('above50', '?')}% au-dessus MM50"]
        if out['vix']:
            bits.append(f"VIX {out['vix']} ({out['vix_band']})")
        out['verdict'] = ' · '.join(x for x in bits if x)
    except Exception:
        pass
    return out
