"""
elio/anomalies.py — Détecteur d'ANOMALIES (titres « intéressants » qui sortent de l'ordinaire).

N'utilise QUE des champs déjà calculés par analyse() : change, atr_pct, volx, ext_atr,
rsi, roc, rs, pos52, series{close, ema20}. Zéro requête réseau, zéro donnée inventée.

⚠️ Limites honnêtes (yfinance différé ~15min) :
  - pas de GAP (open/high/low non exposés) → anomalie GAP volontairement omise.
  - pas de série RSI → la "divergence prix/momentum" est un PROXY close-based (étiqueté).
  - volume par barre non exposé → VOL_DRY_UP basé sur le RVOL du jour (instantané).
"""


INTEREST = {
    'VOLUME_SPIKE': "Volume anormal = les gros interviennent. Si le prix monte avec, c'est de l'accumulation institutionnelle — surveiller pour entrer sur force.",
    'OUTSIZED_MOVE': "Mouvement plus grand que la volatilité normale = vrai catalyseur (pas du bruit). En hausse, impulsion à suivre.",
    'BREAKOUT_52W': "Nouveau plus-haut 52 semaines = aucune résistance au-dessus, tous les détenteurs sont gagnants. Setup haussier classique : on achète la force.",
    'OVEREXTENSION': "Trop loin de la MM20 = risque de repli. NE PAS acheter ici — attendre une respiration vers la moyenne.",
    'RS_DIVERGENCE': "Force relative élevée alors que le jour est calme = le titre tient mieux que le marché. Candidat à l'achat dès la reprise.",
    'RSI_DIVERGENCE': "Prix au plus-haut mais RSI qui faiblit = essoufflement. Signal de prudence, pas un achat.",
    'VOL_DRY_UP': "Volume qui s'assèche collé sous un plus-haut = compression avant cassure. Précède souvent un breakout haussier — à surveiller de près.",
    'STALL_AT_HIGHS': "Rejet en haut de range sur gros volume = distribution (les gros vendent). Prudence, retournement possible.",
    'REVERSAL_FROM_LOWS': "Rebond puissant depuis le bas du range = retournement potentiel. À CONFIRMER (peut être un faux signal de contre-tendance).",
    'MA20_RECLAIM': "Reconquête de la MM20 = reprise de la tendance courte. Bon point d'entrée swing si la tendance de fond reste haussière.",
}


def _f(d, k, default=0.0):
    v = d.get(k)
    return float(v) if v is not None else default


def _series(d):
    s = d.get('series') or {}
    close = [x for x in (s.get('close') or []) if x is not None]
    ema20 = s.get('ema20') or []
    return close, ema20


def detect_anomalies(rows, detail):
    """-> liste de dicts {symbol, code, label, dir, sev, score_anom, note} triée par sévérité."""
    out = []
    for r in rows:
        sym = r.get('symbol')
        d = detail.get(sym)
        if not d:
            continue
        change = _f(d, 'change')
        atr_pct = max(_f(d, 'atr_pct', 0.1), 0.1)
        volx = _f(d, 'volx', 1.0)
        ext_atr = _f(d, 'ext_atr')
        rsi = _f(d, 'rsi', 50)
        rs = _f(d, 'rs', 50)
        pos52 = _f(d, 'pos52', 50)
        close, ema20 = _series(d)
        hits = []

        if volx >= 2.0:
            hits.append(('VOLUME_SPIKE', 'VOLUME SPIKE', 'NEUTRAL', 60 + min(40, (volx - 2) * 20),
                         f'RVOL {volx:.1f}x la moyenne 20j'))

        ratio = abs(change) / atr_pct
        if ratio >= 1.5:
            dirn = 'UP' if change >= 0 else 'DOWN'
            hits.append(('OUTSIZED_MOVE', 'MOVE OUTSIZED', dirn, 50 + min(50, (ratio - 1.5) * 40),
                         f'{change:+.1f}% = {ratio:.1f}x l ATR journalier'))

        if pos52 >= 98:
            sev = 70 + (20 if volx >= 1.3 else 0) + (10 if pos52 >= 99.5 else 0)
            hits.append(('BREAKOUT_52W', 'CASSURE 52s', 'UP', min(sev, 100),
                         f'pos52 {pos52:.0f}%' + (' + volume' if volx >= 1.3 else ' (volume mou)')))

        if ext_atr >= 4 or rsi >= 80:
            base = max(ext_atr, 4)
            sev = 55 + min(45, (base - 4) * 12) + (10 if rsi >= 85 else 0)
            hits.append(('OVEREXTENSION', 'SUREXTENSION', 'WARN', min(sev, 100),
                         f'{ext_atr:.1f} ATR au-dessus EMA20, RSI {rsi:.0f} - risque pullback'))

        if rs >= 70 and change <= 0.3:
            hits.append(('RS_DIVERGENCE', 'RS DIVERGENCE', 'UP', min(50 + (rs - 70) * 0.8, 100),
                         f'RS {rs:.0f} mais jour mou ({change:+.1f}%) - tient le marche'))
        elif rs <= 35 and change >= 1.5:
            hits.append(('RS_DIVERGENCE', 'RS DIVERGENCE', 'WARN', 55,
                         f'rebond {change:+.1f}% sur titre faible (RS {rs:.0f}) - suspect'))

        if d.get('rsi_div') == 'bear':
            hits.append(('RSI_DIVERGENCE', 'DIVERGENCE RSI', 'WARN', 64,
                         'prix au plus-haut mais RSI en repli — vraie divergence baissiere'))
        elif d.get('rsi_div') == 'bull':
            hits.append(('RSI_DIVERGENCE', 'DIVERGENCE RSI', 'UP', 58,
                         'prix au plus-bas mais RSI remonte — divergence haussiere'))

        if volx <= 0.7 and pos52 >= 85 and ext_atr < 2:
            hits.append(('VOL_DRY_UP', 'VOL DRY-UP', 'UP', min(50 + (0.7 - volx) * 40 + (pos52 - 85) * 0.5, 100),
                         f'volume sec ({volx:.1f}x) colle sous le haut (pos52 {pos52:.0f}%)'))

        if pos52 >= 95 and change <= -1 and volx >= 1.5:
            hits.append(('STALL_AT_HIGHS', 'CALAGE AU SOMMET', 'DOWN',
                         min(55 + abs(change) * 5 + (volx - 1.5) * 15, 100),
                         f'rejet {change:+.1f}% en haut de range sur volume {volx:.1f}x'))

        if pos52 <= 30 and change >= 2 and volx >= 1.5:
            hits.append(('REVERSAL_FROM_LOWS', 'RETOURNEMENT BAS', 'UP',
                         min(45 + change * 3 + (volx - 1.5) * 10, 100),
                         f'rebond {change:+.1f}% du bas de range (pos52 {pos52:.0f}%) - confirmer'))

        if len(close) >= 2 and len(ema20) >= 2 and ema20[-1] is not None and ema20[-2] is not None:
            if close[-1] > ema20[-1] and close[-2] <= ema20[-2] and change > 0:
                hits.append(('MA20_RECLAIM', 'RECONQUETE EMA20', 'UP',
                             min(40 + min(30, change * 4) + (10 if volx >= 1.3 else 0), 100),
                             f'repasse au-dessus EMA20 ({change:+.1f}%)'))

        if not hits:
            continue
        sev_max = max(h[3] for h in hits)
        score_anom = min(100, round(sev_max + (len(hits) - 1) * 6))
        for code, label, dirn, sev, note in hits:
            out.append({'symbol': sym, 'code': code, 'label': label, 'dir': dirn,
                        'sev': round(sev), 'score_anom': score_anom, 'note': note,
                        'interest': INTEREST.get(code, '')})

    out.sort(key=lambda a: (a['sev'], a['score_anom']), reverse=True)
    return out
