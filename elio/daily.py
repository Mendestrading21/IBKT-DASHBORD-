"""
elio/daily.py — Moteur du BRIEF QUOTIDIEN « Daily Watchlist » (poster néon).

Range les 45 leaders scannés en sections actionnables, ADAPTÉ au profil
(long-only leaders US, swing, options 6-12M) — PAS de short-squeeze / penny / biotech.
Sections : top picks · swing trades · live momentum · top movers · second leg ·
à éviter · garde-fous risque · « ce qui a changé depuis hier » (diff persistant).

Données = exactement ce que scan()/analyse() produisent (terminal.py). Zéro donnée
inventée : si un champ manque, dégradation propre. ⛔ ANALYSE ONLY.
"""
import json
import math


def _g(d, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return default if cur is None else cur


def _sig(d, key):
    return bool(_g(d, 'signals', key, default=False))


def _atr_pct(d):
    v = d.get('atr_pct')
    if v is not None:
        return float(v)
    price = float(d.get('price') or 0)
    atr = float(_g(d, 'plan', 'atr', default=0) or 0)
    return (atr / price * 100.0) if price else 0.0


def _ext_atr(d):
    v = d.get('ext_atr')
    if v is not None:
        return float(v)
    price = float(d.get('price') or 0)
    ma20 = float(d.get('ma20') or price)
    atr = float(_g(d, 'plan', 'atr', default=0) or 0)
    return ((price - ma20) / atr) if atr else 0.0


def _risk_band(atr_pct):
    if atr_pct < 3:
        return 'Low'
    if atr_pct <= 5:
        return 'Med'
    return 'High'


def _avoid_reason(d):
    bits = []
    if not _sig(d, 'above200'):
        bits.append('sous MM200')
    if not _sig(d, 'above50'):
        bits.append('sous MM50')
    if float(d.get('rsi') or 50) < 45:
        bits.append('momentum faible')
    if float(d.get('change') or 0) < -3:
        bits.append('forte baisse jour')
    return ' · '.join(bits) or 'structure faible'


def _enrich(sym, d):
    ap = _atr_pct(d)
    return {
        'symbol': sym,
        'price': float(d.get('price') or 0),
        'change': float(d.get('change') or 0),
        'score': int(d.get('score') or 0),
        'grade': d.get('grade') or '—',
        'verdict': d.get('verdict') or 'WAIT',
        'sigcount': int(d.get('sigcount') or 0),
        'trend': float(d.get('trend') or 0),
        'rsi': float(d.get('rsi') or 50),
        'rs': float(d.get('rs') or 50),
        'roc': float(d.get('roc') or 0),
        'pos52': float(d.get('pos52') or 50),
        'rvol': float(d.get('volx') or 1.0),
        'atr_pct': round(ap, 2),
        'ext_atr': round(_ext_atr(d), 2),
        'risk_band': _risk_band(ap),
        'stacked': _sig(d, 'stacked'),
        'above50': _sig(d, 'above50'),
        'above200': _sig(d, 'above200'),
        'plan': _g(d, 'plan', default={}),
    }


def build_daily(rows, detail, prev=None):
    """rows/detail = structures de scan(). prev = baseline d'hier {sym:{score,verdict,pos52}}."""
    items = [_enrich(r['symbol'], detail.get(r['symbol'], r)) for r in rows
             if r.get('symbol') in detail]
    if not items:
        return {'sections': {}, 'meta': {'n': 0, 'note': 'scan vide'}}

    by_score = sorted(items, key=lambda x: (x['score'], x['sigcount']), reverse=True)

    # 1) TOP PICKS — BUY, score desc ; complété WATCH>=70 'soft' si <5
    buys = [x for x in by_score if x['verdict'] == 'BUY']
    picks = list(buys[:5])
    if len(picks) < 5:
        soft = [{**x, 'soft': True} for x in by_score
                if x['verdict'] == 'WATCH' and x['score'] >= 70 and x not in picks]
        picks = (picks + soft)[:5]

    # 2) SWING TRADES — BUY/WATCH & score>=65 & above50 ; plan ATR complet
    swing = [x for x in by_score
             if x['verdict'] in ('BUY', 'WATCH') and x['score'] >= 65 and x['above50']][:8]

    # 3) LIVE MOMENTUM — gainers du jour (change>0), tri change puis rvol
    live = sorted([x for x in items if x['change'] > 0],
                  key=lambda x: (x['change'], x['rvol']), reverse=True)[:6]

    # 4) TOP MOVERS — |move| pondéré volume ; filtré qualité (score>=50)
    qual = [x for x in items if x['score'] >= 50]
    movers = sorted(qual or items,
                    key=lambda x: abs(x['change']) * math.sqrt(max(x['rvol'], 0.1)),
                    reverse=True)[:6]
    movers = [{**x, 'dir': 'UP' if x['change'] >= 0 else 'DOWN'} for x in movers]

    # 5) SECOND LEG — reprise saine : stacked & score>=70 & ext_atr<3
    second = sorted([x for x in items if x['stacked'] and x['score'] >= 70 and x['ext_atr'] < 3],
                    key=lambda x: (-x['score'], x['ext_atr']))[:6]

    # 6) À ÉVITER — verdict AVOID (exclusion, jamais un short)
    avoid = sorted([x for x in items if x['verdict'] == 'AVOID'], key=lambda x: x['score'])[:6]
    avoid = [{**x, 'reason': _avoid_reason(detail.get(x['symbol'], {}))} for x in avoid]

    # 7) GARDE-FOUS — titres tentants (BUY/WATCH) mais surchauffés
    guard = []
    for x in by_score:
        if x['verdict'] not in ('BUY', 'WATCH'):
            continue
        flags = []
        if x['ext_atr'] >= 4:
            flags.append('surextendu')
        if x['rsi'] >= 78:
            flags.append('RSI surchauffe')
        if x['pos52'] >= 98:
            flags.append('sommet 52s')
        if flags:
            guard.append({'symbol': x['symbol'], 'flags': flags, 'rsi': round(x['rsi']),
                          'ext_atr': x['ext_atr'], 'grade': x['grade'], 'score': x['score']})
    guard = guard[:6]

    # 8) CE QUI A CHANGÉ DEPUIS HIER — diff vs baseline persistée
    changes = []
    if prev:
        seen = set()
        for x in by_score:
            p = prev.get(x['symbol'])
            if not p:
                continue
            pv, ps, ppos = p.get('verdict'), p.get('score'), p.get('pos52')
            key = None
            if x['verdict'] == 'BUY' and pv != 'BUY':
                key, kind, txt = (x['symbol'], 'up', '→ ACHAT 🟢')
            elif x['verdict'] == 'AVOID' and pv not in (None, 'AVOID'):
                key, kind, txt = (x['symbol'], 'down', '→ ÉVITER 🔴')
            elif x['verdict'] == 'WATCH' and pv in ('WAIT', 'AVOID'):
                key, kind, txt = (x['symbol'], 'up', '→ SURVEILLER 🟡')
            if key and key not in seen:
                seen.add(key)
                changes.append({'symbol': x['symbol'], 'kind': kind, 'txt': txt, 'score': x['score']})
            if ppos is not None and ppos < 98 <= x['pos52'] and x['symbol'] not in seen:
                seen.add(x['symbol'])
                changes.append({'symbol': x['symbol'], 'kind': 'up', 'txt': 'nouveau +haut 52s 🚀', 'score': x['score']})
            elif ps is not None and abs(x['score'] - ps) >= 8 and x['symbol'] not in seen:
                ds = x['score'] - ps
                seen.add(x['symbol'])
                changes.append({'symbol': x['symbol'], 'kind': 'up' if ds > 0 else 'down',
                                'txt': 'score %+d' % ds, 'score': x['score']})
        changes = changes[:10]

    return {
        'sections': {
            'top_picks': picks, 'swing_trades': swing, 'live_momentum': live,
            'top_movers': movers, 'second_leg': second, 'avoid': avoid,
            'guardrails': guard, 'changes': changes,
        },
        'meta': {
            'n': len(items), 'n_buy': len(buys),
            'breadth': round(len(buys) / len(items) * 100) if items else 0,
            'has_prev': bool(prev),
            'disclaimer': 'ANALYSE ONLY — aucun ordre, aucune promesse de gain.',
        },
    }


# ── Persistance du diff jour/jour (baseline = dernier snap du jour précédent) ──
def snapshot(rows, detail):
    out = {}
    for r in rows:
        d = detail.get(r['symbol'], {})
        out[r['symbol']] = {'score': r.get('score'), 'verdict': r.get('verdict'),
                            'pos52': d.get('pos52')}
    return out


def load_baseline(path, today):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            st = json.load(f)
    except Exception:
        return {}
    if st.get('cur_date') == today:
        return st.get('prev') or {}        # même jour → baseline = hier
    return st.get('cur') or {}             # nouveau jour → baseline = dernier snap d'hier


def save_state(path, today, snap):
    try:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                st = json.load(f)
        except Exception:
            st = {}
        if st.get('cur_date') == today:
            new = {'cur_date': today, 'cur': snap,
                   'prev_date': st.get('prev_date'), 'prev': st.get('prev') or {}}
        else:
            new = {'cur_date': today, 'cur': snap,
                   'prev_date': st.get('cur_date'), 'prev': st.get('cur') or {}}
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(new, f)
    except Exception:
        pass
