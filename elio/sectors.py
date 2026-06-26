"""
elio/sectors.py — Rotation sectorielle + watchlists par secteur.

Mappe les 45 leaders en 9 secteurs, agrège la force par secteur (score moyen,
% BUY, change, breadth MM50/200, RS, RVOL, risk) et classe. Diff vs hier via la
baseline persistée (daily_prev.json). Tout se calcule depuis rows/detail — zéro réseau.
"""

SECTOR_MAP = {
    'NVDA': 'Semiconducteurs', 'AVGO': 'Semiconducteurs', 'TSM': 'Semiconducteurs',
    'ASML': 'Semiconducteurs', 'AMD': 'Semiconducteurs', 'MU': 'Semiconducteurs',
    'QCOM': 'Semiconducteurs', 'ARM': 'Semiconducteurs', 'AMAT': 'Semiconducteurs', 'MRVL': 'Semiconducteurs',
    'MSFT': 'Software', 'CRM': 'Software', 'NOW': 'Software', 'SNOW': 'Software', 'CRWD': 'Software',
    'PANW': 'Software', 'INTU': 'Software', 'ORCL': 'Software', 'ADBE': 'Software',
    'AAPL': 'Big Tech', 'META': 'Big Tech', 'GOOGL': 'Big Tech', 'AMZN': 'Big Tech', 'NFLX': 'Big Tech',
    'UBER': 'Big Tech', 'ABNB': 'Big Tech', 'SHOP': 'Big Tech', 'TSLA': 'Big Tech',
    'COST': 'Conso', 'HD': 'Conso', 'WMT': 'Conso',
    'LLY': 'Sante', 'UNH': 'Sante', 'ISRG': 'Sante',
    'JPM': 'Finance', 'V': 'Finance', 'MA': 'Finance', 'HOOD': 'Finance',
    'XOM': 'Energie',
    'COIN': 'Crypto', 'MSTR': 'Crypto',
    'PLTR': 'Infra-IA', 'ANET': 'Infra-IA', 'SMCI': 'Infra-IA', 'DELL': 'Infra-IA', 'CEG': 'Infra-IA', 'VRT': 'Infra-IA',
    'GEV': 'Infra-IA', 'VST': 'Infra-IA', 'NBIS': 'Infra-IA',
    'NET': 'Software', 'AXON': 'Software', 'APP': 'Software',
    'MELI': 'Big Tech', 'RDDT': 'Big Tech',
    'ALAB': 'Semiconducteurs', 'CRDO': 'Semiconducteurs',
}

SECTOR_ICON = {
    'Semiconducteurs': '🔩', 'Software': '🧠', 'Big Tech': '🌐', 'Conso': '🛒', 'Sante': '⚕️',
    'Finance': '🏦', 'Energie': '🛢️', 'Crypto': '₿', 'Infra-IA': '⚡',
}


def _sig(d, key):
    return bool((d.get('signals') or {}).get(key))


def build_sectors(rows, detail, prev=None):
    groups = {}
    for r in rows:
        sym = r.get('symbol')
        sec = SECTOR_MAP.get(sym)
        if not sec:
            continue
        groups.setdefault(sec, []).append({**(detail.get(sym) or {}), 'symbol': sym})

    out = []
    for sec, items in groups.items():
        n = len(items)
        avg_score = round(sum(i.get('score', 0) for i in items) / n)
        n_buy = sum(1 for i in items if i.get('verdict') == 'BUY')
        n_watch = sum(1 for i in items if i.get('verdict') in ('WATCH', 'WAIT'))
        n_avoid = sum(1 for i in items if i.get('verdict') == 'AVOID')
        avg_atr = sum(i.get('atr_pct', 2) for i in items) / n
        ranked = sorted(items, key=lambda i: (i.get('score', 0), i.get('sigcount', 0)), reverse=True)
        lead, lag = ranked[0], ranked[-1]
        delta = None
        if prev:
            psc = [prev[i['symbol']]['score'] for i in items
                   if i['symbol'] in prev and prev[i['symbol']].get('score') is not None]
            if psc:
                delta = avg_score - round(sum(psc) / len(psc))
        out.append({
            'sector': sec, 'icon': SECTOR_ICON.get(sec, '•'), 'n': n,
            'avg_score': avg_score, 'pct_buy': round(100 * n_buy / n),
            'n_buy': n_buy, 'n_watch': n_watch, 'n_avoid': n_avoid,
            'avg_change': round(sum(i.get('change', 0) for i in items) / n, 2),
            'avg_rs': round(sum(i.get('rs', 50) for i in items) / n),
            'avg_rvol': round(sum(i.get('volx', 1) for i in items) / n, 2),
            'b50': round(100 * sum(1 for i in items if _sig(i, 'above50')) / n),
            'b200': round(100 * sum(1 for i in items if _sig(i, 'above200')) / n),
            'risk_band': 'Low' if avg_atr < 3 else 'Med' if avg_atr <= 5 else 'High',
            'delta': delta,
            'leader': {'symbol': lead['symbol'], 'score': lead.get('score'),
                       'grade': lead.get('grade'), 'change': lead.get('change')},
            'laggard': {'symbol': lag['symbol'], 'score': lag.get('score')},
            'members': [{'symbol': i['symbol'], 'score': i.get('score'), 'grade': i.get('grade'),
                         'verdict': i.get('verdict'), 'change': i.get('change'), 'rvol': i.get('volx')}
                        for i in ranked],
        })
    out.sort(key=lambda s: s['avg_score'], reverse=True)
    return out
