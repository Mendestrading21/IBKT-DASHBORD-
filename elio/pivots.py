"""elio/pivots.py — STRUCTURE DE MARCHÉ par PIVOTS (plis de chaque mouvement).

Détecte les sommets/creux (swing highs/lows fractals), classe la tendance
(haussière = sommets+creux montants, baissière, range) et déduit une LOGIQUE
d'entrée rigoureuse :
  • BREAKOUT   : clôture au-dessus du dernier sommet (plus-haut cassé)
  • REPLI REPRIS: tendance haussière + repli sur le dernier creux + reprise
  • REFUS       : rebond en tendance baissière (= piège, on n'achète pas)

Stop = sous le dernier creux (structure). Cible = prochain sommet / extension.
⛔ ANALYSE ÉDUCATIVE — jamais un ordre.
"""
import numpy as np


def _pivots(arr, k, kind):
    """Pivots fractals : arr[i] est un sommet (resp. creux) s'il est le max
    (resp. min) de la fenêtre [i-k, i+k]. Renvoie [(index, valeur)]."""
    out = []
    n = len(arr)
    for i in range(k, n - k):
        w = arr[i - k:i + k + 1]
        v = float(arr[i])
        if kind == 'high' and v >= float(w.max()):
            out.append((i, v))
        elif kind == 'low' and v <= float(w.min()):
            out.append((i, v))
    # déduplique les plateaux consécutifs (garde le dernier)
    dedup = []
    for i, v in out:
        if dedup and i - dedup[-1][0] <= 1 and abs(v - dedup[-1][1]) < 1e-9:
            dedup[-1] = (i, v)
        else:
            dedup.append((i, v))
    return dedup


def structure(df, atr, lookback=140, k=3):
    """Analyse la structure par pivots. Renvoie un dict prêt pour la décision."""
    try:
        h = df['High'].tail(lookback).to_numpy(dtype=float)
        l = df['Low'].tail(lookback).to_numpy(dtype=float)
        c = df['Close'].tail(lookback).to_numpy(dtype=float)
    except Exception:
        return None
    if len(c) < 2 * k + 5:
        return None
    last = float(c[-1])
    prev = float(c[-2])
    atr = float(atr) if atr else max(last * 0.01, 0.01)

    phs = _pivots(h, k, 'high')
    pls = _pivots(l, k, 'low')
    sh = [p[1] for p in phs]
    sl = [p[1] for p in pls]
    last_sh = sh[-1] if sh else float(h.max())
    last_sl = sl[-1] if sl else float(l.min())

    hh = len(sh) >= 2 and sh[-1] > sh[-2]
    hl = len(sl) >= 2 and sl[-1] > sl[-2]
    lh = len(sh) >= 2 and sh[-1] < sh[-2]
    ll = len(sl) >= 2 and sl[-1] < sl[-2]
    if hh and hl:
        trend = 'UP'
    elif lh and ll:
        trend = 'DOWN'
    else:
        trend = 'RANGE'

    # distances en ATR
    dist_to_sh = (last_sh - last) / atr if atr else 0.0     # >0 = sous la résistance
    dist_to_sl = (last - last_sl) / atr if atr else 0.0     # >0 = au-dessus du support

    # ── LOGIQUE D'ENTRÉE (rigoureuse) ─────────────────────────────────────────
    signal, confirmed = 'AUCUN', False
    entry = stop = target = None
    logic = ''
    # cassure RÉCENTE : prix au-dessus du dernier sommet, franchi dans les ~6 dernières
    # séances et pas encore étendu (≤ 1.2 ATR au-dessus) → vraie cassure, sans chasser.
    recent_below = any(float(c[-j]) <= last_sh for j in range(2, min(7, len(c))))
    fresh_breakout = last > last_sh and recent_below and (last - last_sh) <= 1.2 * atr
    if trend == 'DOWN':
        signal = 'REFUS_DOWNTREND'
        logic = (f"Tendance BAISSIÈRE (sommets {last_sh:.0f} et creux {last_sl:.0f} descendants). "
                 f"Un rebond ici = piège, pas un achat. On attend une cassure de structure.")
    elif fresh_breakout:
        # cassure récente du dernier sommet (plus-haut cassé)
        signal, confirmed = 'BREAKOUT', True
        entry = round(last, 2)
        stop = round(last_sl - 0.3 * atr, 2)
        target = round(last_sh + (last_sh - last_sl), 2)      # extension (measured move)
        logic = (f"CASSURE du dernier sommet ${last_sh:.0f} → ciel ouvert. "
                 f"Stop sous le dernier creux ${last_sl:.0f}, cible par extension ${target:.0f}.")
    elif trend == 'UP' and 0 <= dist_to_sl <= 1.8 and last > prev:
        # repli sur le dernier creux (support) PUIS reprise
        signal, confirmed = 'REPLI_REPRIS', True
        entry = round(last, 2)
        stop = round(last_sl - 0.4 * atr, 2)
        target = round(last_sh, 2)
        logic = (f"Tendance HAUSSIÈRE, repli sur le support ${last_sl:.0f} (dernier creux) PUIS reprise. "
                 f"Stop sous ${last_sl:.0f}, cible le sommet ${last_sh:.0f}.")
    elif trend == 'UP':
        signal = 'EN_TENDANCE'
        logic = (f"Tendance haussière mais milieu de mouvement (à {dist_to_sh:.1f} ATR sous ${last_sh:.0f}). "
                 f"Pas d'entrée optimale : attendre la cassure de ${last_sh:.0f} ou un repli sur ${last_sl:.0f}.")
    else:  # RANGE
        signal = 'RANGE'
        logic = (f"Range entre ${last_sl:.0f} et ${last_sh:.0f}, pas de tendance. "
                 f"Acheter seulement sur cassure confirmée de ${last_sh:.0f}.")

    rr = None
    if entry and stop and target and entry > stop:
        rr = round((target - entry) / (entry - stop), 1)

    return {
        'trend': trend, 'signal': signal, 'confirmed': confirmed,
        'last_high': round(last_sh, 2), 'last_low': round(last_sl, 2),
        'n_highs': len(sh), 'n_lows': len(sl),
        'dist_to_high_atr': round(dist_to_sh, 1), 'dist_to_low_atr': round(dist_to_sl, 1),
        'entry': entry, 'stop': stop, 'target': target, 'rr': rr,
        'logic': logic,
        'swing_highs': [round(x, 2) for x in sh[-4:]],
        'swing_lows': [round(x, 2) for x in sl[-4:]],
    }
