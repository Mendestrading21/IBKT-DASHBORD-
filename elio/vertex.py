"""elio/vertex.py — VERTEX V1 : noyau quantitatif supérieur (cerveau du desk).

Ne remplace pas les scores existants : il les SURCLASSE, les explique et corrige
leurs failles (faux signaux, achats trop hauts, R:R faibles, cibles inatteignables).

Entrée : le dict `detail` produit par analyse() (price, ma20/50/200, rsi, rs, roc,
adx, chop, volx, ext_atr, pos52, regime, setup_quality, confidence, signals, plan…).
Sortie : un bloc `vertex` (scores 0-100, kelly indicatif capé, verdict, action).

Principes V1 : déterministe · pur · rapide · robuste (jamais de crash si donnée
manquante) · aucun appel API · aucune dépendance lourde · aucun ordre.
⛔ ANALYSE ÉDUCATIVE — jamais un conseil, jamais une promesse de battre le marché.
"""


def _f(x, d=0.0):
    """float robuste : None/NaN/erreur → défaut."""
    try:
        if x is None:
            return d
        v = float(x)
        return d if v != v else v          # NaN
    except Exception:
        return d


def _clamp(x, lo=0.0, hi=100.0):
    try:
        return max(lo, min(hi, x))
    except Exception:
        return lo


# ── 1. QUALITÉ DE TENDANCE ────────────────────────────────────────────────────
def trend_quality(d):
    try:
        price = _f(d.get('price'))
        ma20, ma50, ma200 = _f(d.get('ma20')), _f(d.get('ma50')), _f(d.get('ma200'))
        rs, roc = _f(d.get('rs'), 50), _f(d.get('roc'))
        adx, chop = _f(d.get('adx')), _f(d.get('chop'), 50)
        volx = _f(d.get('volx'), 1.0)
        s = 0.0
        s += 18 if (ma200 > 0 and price > ma200) else 0          # au-dessus MM200
        s += 14 if (ma50 > 0 and price > ma50) else 0            # au-dessus MM50
        if ma200 > 0 and ma20 > ma50 > ma200:                    # empilement parfait
            s += 16
        elif ma20 > ma50:
            s += 8
        s += _clamp((rs - 50) * 0.5, 0, 12)                      # force relative
        s += _clamp(roc * 0.6, 0, 12)                            # ROC (%)
        s += _clamp((adx - 15) * 0.7, 0, 16)                     # force de tendance
        s += 8 if chop < 45 else (4 if chop < 55 else 0)         # peu de bruit
        s += _clamp((volx - 1.0) * 8, 0, 8)                      # volume > moyenne
        return round(_clamp(s))
    except Exception:
        return 0


# ── 2. QUALITÉ DU POINT D'ENTRÉE ──────────────────────────────────────────────
def entry_quality(d):
    try:
        rsi = _f(d.get('rsi'), 50)
        ext = _f(d.get('ext_atr'))
        sq = _f(d.get('setup_quality'), 50)
        reg = d.get('regime')
        s = 50.0
        if 48 <= rsi <= 66:
            s += 22                                              # zone idéale
        elif 40 <= rsi < 48 or 66 < rsi <= 70:
            s += 10
        elif rsi > 72:
            s -= 18                                              # acheter trop haut
        elif rsi < 35:
            s -= 6
        if ext > 2.5:
            s -= 18
        elif ext > 1.8:
            s -= 8
        elif ext < 0.5:
            s += 4
        s += _clamp((sq - 50) * 0.3, -10, 15)                   # bonus setup
        if reg == 'TREND':
            s += 10
        elif reg == 'CHOP':
            s -= 14
        return round(_clamp(s))
    except Exception:
        return 50


# ── 3. PÉNALITÉ D'EXTENSION (NON LINÉAIRE) ────────────────────────────────────
def nonlinear_extension_penalty(d):
    """0 = pas étendu (bon), 100 = euphorie dangereuse. Quadratique sur ext_atr."""
    try:
        ext = _f(d.get('ext_atr'))
        rsi = _f(d.get('rsi'), 50)
        pos = _f(d.get('pos52'), 50)
        p = 0.0
        if ext > 1.0:
            p += _clamp((ext - 1.0) ** 2 * 6, 0, 55)            # non linéaire
        if rsi > 72:
            p += _clamp((rsi - 72) * 2.2, 0, 30)
        if pos > 90:
            p += _clamp((pos - 90) * 1.2, 0, 15)
        return round(_clamp(p))
    except Exception:
        return 0


# ── 4. REWARD / RISK ──────────────────────────────────────────────────────────
def rr_score(d):
    """Score 0-100 du rapport rendement/risque réel (reward plafonné par la résistance)."""
    try:
        plan = d.get('plan') or {}
        entry = _f(plan.get('entry') or d.get('price'))
        stop = _f(plan.get('stop'))
        tp1, tp2, tp3 = _f(plan.get('tp1')), _f(plan.get('tp2')), _f(plan.get('tp3'))
        res = _f(plan.get('resistance'))
        risk = entry - stop
        if risk <= 0 or entry <= 0:
            return 0, {'rr1': 0, 'rr2': 0, 'rr3': 0}
        rr1 = (tp1 - entry) / risk if tp1 else 0
        rr2 = (tp2 - entry) / risk if tp2 else 0
        rr3 = (tp3 - entry) / risk if tp3 else 0
        # reward RÉEL : si une résistance est sous TP2, elle plafonne le potentiel
        real_target = tp2
        if entry < res < tp2:
            real_target = res
        rr_real = (real_target - entry) / risk if real_target else rr2
        s = _clamp(rr_real * 32, 0, 100)                        # 2:1→64, 3:1→96
        return round(s), {'rr1': round(rr1, 1), 'rr2': round(rr2, 1), 'rr3': round(rr3, 1)}
    except Exception:
        return 0, {'rr1': 0, 'rr2': 0, 'rr3': 0}


# ── 5. ATTEIGNABILITÉ DES OBJECTIFS ───────────────────────────────────────────
def expected_move_score(d):
    """Les objectifs sont-ils réalistes vu l'ATR ? (TP2 proche = atteignable)."""
    try:
        plan = d.get('plan') or {}
        price = _f(plan.get('entry') or d.get('price'))
        atr = _f(plan.get('atr')) or price * 0.02
        tp2 = _f(plan.get('tp2'))
        res = _f(plan.get('resistance'))
        if atr <= 0 or price <= 0:
            return 50
        dist_tp2 = (tp2 - price) / atr if tp2 > price else 0
        dist_res = (res - price) / atr if res > price else 0
        s = 100 - _clamp((dist_tp2 - 3) * 9, 0, 60)             # TP2 lointain = pénalité
        if dist_res > 4:
            s += 8                                              # ciel dégagé
        elif 0 < dist_res < 1.5:
            s -= 10                                             # résistance collée
        return round(_clamp(s))
    except Exception:
        return 50


# ── 6. KELLY SIMPLIFIÉ ET CAPÉ (INDICATIF) ────────────────────────────────────
def kelly_cap(edge, confidence, rr=2.0):
    """Demi-Kelly capé 12 %. JAMAIS un sizing automatique — indicatif seulement."""
    try:
        edge = _f(edge); confidence = _f(confidence, 50)
        if confidence <= 1:
            confidence *= 100
        p = _clamp(0.40 + edge / 100 * 0.20 + confidence / 100 * 0.10, 0.30, 0.70)
        b = max(_f(rr, 2.0), 1.0)
        k = p - (1 - p) / b                                     # Kelly
        k = max(0.0, k)
        pct = _clamp(k * 100 * 0.5, 0, 12)                      # demi-Kelly, cap 12 %
        return round(pct, 1)
    except Exception:
        return 0.0


# ── 7. INSTITUTIONNALITÉ (qualité « desk ») ───────────────────────────────────
def institutionality(d):
    """Persistance de tendance + liquidité + force = profil tenu par les institutions."""
    try:
        adx = _f(d.get('adx'))
        volx = _f(d.get('volx'), 1.0)
        rs = _f(d.get('rs'), 50)
        score = _f(d.get('score'))
        s = (_clamp((adx - 15) * 1.4, 0, 30) + _clamp((rs - 50) * 0.6, 0, 25)
             + _clamp(score * 0.30, 0, 30) + _clamp((volx - 0.8) * 12, 0, 15))
        return round(_clamp(s))
    except Exception:
        return 0


# ── FONCTION CENTRALE ─────────────────────────────────────────────────────────
_ACTION = {
    'VERTEX S+': 'Setup d\'élite — conviction maximale, déployer (cœur + option CALL).',
    'VERTEX BUY': 'Achat de qualité — entrer avec discipline, R:R respecté.',
    'VERTEX WATCH': 'Sur la liste — bon profil mais pas le point d\'entrée idéal. Surveiller.',
    'VERTEX WAIT': 'Patienter — avantage insuffisant, attendre un meilleur setup.',
    'VERTEX AVOID': 'Écarter — qualité/risque défavorable. On ne force pas.',
}


def evaluate(detail):
    """Cerveau VERTEX. Entrée = dict detail (analyse). Sortie = bloc vertex complet."""
    try:
        d = detail or {}
        tq = trend_quality(d)
        eq = entry_quality(d)
        ext_pen = nonlinear_extension_penalty(d)
        rr, rr_detail = rr_score(d)
        em = expected_move_score(d)
        inst = institutionality(d)
        # asymétrie = potentiel (rr + atteignabilité) corrigé de l'extension
        asym = round(_clamp(0.55 * rr + 0.45 * em - 0.5 * ext_pen, 0, 100))
        # EDGE composite (puis pénalité d'extension)
        base = 0.30 * tq + 0.24 * eq + 0.18 * rr + 0.16 * em + 0.12 * inst
        edge = round(_clamp(base - 0.25 * ext_pen))
        kelly = kelly_cap(edge, d.get('confidence'), max(rr_detail.get('rr2', 2.0), 1.0))

        if edge >= 82 and ext_pen < 40 and rr >= 58 and tq >= 70:
            verdict = 'VERTEX S+'
        elif edge >= 70 and ext_pen < 55:
            verdict = 'VERTEX BUY'
        elif edge >= 58:
            verdict = 'VERTEX WATCH'
        elif edge >= 45:
            verdict = 'VERTEX WAIT'
        else:
            verdict = 'VERTEX AVOID'

        return {
            'score': edge, 'edge': edge,
            'trend_quality': tq, 'entry_quality': eq, 'rr': rr,
            'expected_move': em, 'asymmetry': asym, 'institutionality': inst,
            'extension_penalty': ext_pen,
            'kelly': {'pct': kelly, 'note': 'indicatif · demi-Kelly capé 12 % · jamais automatique'},
            'rr_detail': rr_detail,
            'verdict': verdict, 'action': _ACTION.get(verdict, ''),
        }
    except Exception:
        return None
