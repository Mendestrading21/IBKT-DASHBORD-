"""
elio/ibkr.py — LA COUCHE DÉCISION IBKR.

Transforme les scores bruts en LANGAGE DE TRADER selon la « loi des asymétries » :
perdre peu quand on a tort, gagner beaucoup quand on a raison.

Produit, pour chaque idée :
  • SCORE IBKR /40  — 6 composantes (fond /8, tech /8, catalyseur /6, institutions /6,
                       option fit /6, asymétrie /6) → niveau S+/S/A/B/rejeté + allocation
  • ENTRY TIMING    — BUY NOW / BUY PULLBACK / WATCH BREAKOUT / TOO LATE
  • NO-CHASE FILTER — bloque les achats en surchauffe (« je ne poursuis pas les prix »)
  • VERDICT         — ACCEPTÉ / ACCEPTÉ SUR REPLI / ATTENTE / REFUSÉ + raison + action + taille

100% règles, déterministe, basé sur les données déjà calculées (yfinance différé).
⛔ ANALYSE ONLY — jamais une promesse, jamais un ordre.
"""


def _f(d, k, default=0.0):
    v = d.get(k) if d else None
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _sig(d, k):
    return bool((d.get('signals') or {}).get(k))


# ─────────────────────────── NIVEAUX ───────────────────────────
def level(total):
    """Score /40 → (niveau, allocation max, couleur)."""
    if total >= 36:
        return 'S+', '10-15 %', '#22C55E'
    if total >= 32:
        return 'S', '7-10 %', '#34D399'
    if total >= 28:
        return 'A', '3-5 %', '#FFD27A'
    if total >= 22:
        return 'B', '1-2 %', '#F5A623'
    return 'rejeté', '0 %', '#EF4444'


# ─────────────────────────── NO-CHASE ───────────────────────────
def no_chase(d):
    """Liste des raisons de NE PAS acheter maintenant (surchauffe / chase)."""
    r = []
    rsi = _f(d, 'rsi', 50)
    ext = _f(d, 'ext_atr', 0)
    change = _f(d, 'change', 0)
    atr = max(_f(d, 'atr_pct', 0.1), 0.1)
    pos = _f(d, 'pos52', 50)
    volx = _f(d, 'volx', 1)
    move_x = abs(change) / atr
    if rsi >= 72:
        r.append(f'RSI {round(rsi)} > 72 (surchauffe)')
    if ext >= 2.5:
        r.append(f'Extension {ext:.1f}x ATR au-dessus de la MM20')
    if change > 0 and move_x >= 2.0:
        r.append(f'Bougie du jour {change:+.1f}% = {move_x:.1f}x l ATR (mouvement violent)')
    if pos >= 99 and volx < 1.0:
        r.append('Collé au sommet 52s sans volume (cassure fragile)')
    return r


# ─────────────────────────── ENTRY TIMING ───────────────────────────
def entry_timing(d):
    """BUY NOW / BUY PULLBACK / WATCH BREAKOUT / TOO LATE + niveaux d'entrée."""
    score = _f(d, 'score', 0)
    rsi = _f(d, 'rsi', 50)
    ext = _f(d, 'ext_atr', 0)
    change = _f(d, 'change', 0)
    atr = max(_f(d, 'atr_pct', 0.1), 0.1)
    move_x = abs(change) / atr
    plan = d.get('plan') or {}
    nc = no_chase(d)

    if score < 50:
        state, label = 'AVOID', 'Pas de setup exploitable'
    elif ext >= 3 or rsi >= 76 or (change > 0 and move_x >= 2.5):
        state, label = 'TOO_LATE', 'Trop étendu — ne pas poursuivre'
    elif nc:
        state, label = 'BUY_PULLBACK', 'Excellent titre, mais attendre un repli'
    elif score >= 70 and ext < 1.5 and rsi < 68:
        state, label = 'BUY_NOW', 'Entrée propre possible maintenant'
    elif score >= 60:
        state, label = 'WATCH_BREAKOUT', 'Acheter seulement sur cassure confirmée'
    else:
        state, label = 'BUY_PULLBACK', 'Attendre un meilleur point d entrée'

    return {
        'state': state, 'label': label,
        'optimal': plan.get('entry'),
        'aggressive': plan.get('resistance') or plan.get('tp1'),
        'invalidation': plan.get('stop'),
        'reasons': nc,
    }


# ─────────────────────────── SCORE /40 ───────────────────────────
def ibkr_score(d, opt=None, fund=None):
    """6 composantes → /40 + niveau + allocation."""
    opt = opt or {}
    fund = fund or {}
    score = _f(d, 'score', 0)
    regime = d.get('regime')
    rs = _f(d, 'rs', 50)
    volx = _f(d, 'volx', 1)
    val = opt.get('valuation') or {}
    ed = opt.get('earnings_dte')
    bp = opt.get('best_pick') or {}

    # Fondamentaux /8 — valorisation vs secteur + croissance/marge si dispo
    tone = val.get('tone')
    f8 = 8 if tone == 'good' else (4 if tone == 'warn' else 5)
    gr = fund.get('growth')
    mg = fund.get('margin')
    if gr is not None and gr > 0.20:
        f8 = min(8, f8 + 1)
    if mg is not None and mg > 0.20:
        f8 = min(8, f8 + 1)
    if tone is None and not fund:
        f8 = 5  # inconnu → neutre

    # Technique /8 — score global + bonus régime directionnel
    reg_b = 2 if regime == 'TREND' else (1 if regime in (None, 'NEUTRAL') else 0)
    t8 = round(min(8, score / 100 * 6 + reg_b))

    # Catalyseur /6 — proximité des résultats (fenêtre idéale 15-45j)
    if ed is None:
        c6 = 3
    elif ed < 7:
        c6 = 3            # trop proche → risque IV / binaire
    elif ed <= 45:
        c6 = 6            # fenêtre idéale
    elif ed <= 90:
        c6 = 4
    else:
        c6 = 3
    if regime == 'TREND':
        c6 = min(6, c6 + 1)  # momentum sectoriel porteur

    # Institutions /6 — force relative + volume (proxy d'accumulation)
    i6 = round(min(6, (rs / 100) * 4 + min(2, max(0, volx - 1) * 2)))

    # Option Fit /6 — le contrat respecte-t-il le profil ?
    if bp:
        bucket = bp.get('bucket')
        delta = _f(bp, 'delta', 0)
        danger = bp.get('danger')
        of6 = 2
        if bucket in ('long', 'moyen'):
            of6 += 1
        if 0.65 <= delta <= 0.90:
            of6 += 2
        elif 0.50 <= delta < 0.65:
            of6 += 1
        if danger == 'Faible':
            of6 += 1
        elif danger in ('Élevé', 'Extrême'):
            of6 -= 1
        of6 = max(0, min(6, of6))
    else:
        of6 = 4  # neutre : un leader a en général un LEAPS exploitable (affiné sur la fiche)

    # Asymétrie /6 — probabilité (POP) + potentiel projeté
    if bp:
        pop = _f(bp, 'pop', 0)
        pot = _f(bp, 'pot', 0)
        a6 = round(min(6, (pop / 100) * 3 + min(3, max(0, pot) / 60 * 3)))
    else:
        a6 = 3  # neutre tant que le contrat n'est pas évalué

    total = int(f8 + t8 + c6 + i6 + of6 + a6)
    niveau, alloc, col = level(total)
    return {
        'total': total, 'max': 40,
        'fond': f8, 'tech': t8, 'cata': c6, 'inst': i6, 'optfit': of6, 'asym': a6,
        'niveau': niveau, 'alloc': alloc, 'color': col,
    }


# ─────────────────────────── VERDICT FINAL ───────────────────────────
def verdict(d, opt=None, fund=None):
    """ACCEPTÉ / ACCEPTÉ SUR REPLI / ATTENTE / REFUSÉ + raison + action + taille."""
    if not d:
        return None
    sc = ibkr_score(d, opt, fund)
    tm = entry_timing(d)
    nc = tm['reasons']
    state = tm['state']
    total = sc['total']

    if total < 22 or state == 'AVOID':
        decision, dtone = 'REFUSÉ', 'avoid'
    elif state == 'TOO_LATE':
        decision, dtone = 'ATTENTE', 'wait'
    elif state == 'WATCH_BREAKOUT':
        decision, dtone = 'ATTENTE', 'wait'
    elif state == 'BUY_PULLBACK':
        decision, dtone = 'ACCEPTÉ SUR REPLI', 'pullback'
    elif state == 'BUY_NOW':
        decision, dtone = 'ACCEPTÉ', 'buy'
    else:
        decision, dtone = 'ATTENTE', 'wait'

    # Raison (forces clés)
    bits = []
    if sc['tech'] >= 6:
        bits.append('technique solide')
    if sc['inst'] >= 5:
        bits.append('force relative élevée')
    if sc['cata'] >= 5:
        bits.append('catalyseur proche')
    if sc['asym'] >= 4:
        bits.append('asymétrie favorable')
    if sc['fond'] >= 7:
        bits.append('valorisation attractive')
    if not bits:
        bits.append('signal faible')
    raison = f"Niveau {sc['niveau']} ({total}/40) · " + ', '.join(bits[:3])
    if nc:
        raison += f" — MAIS {nc[0].lower()}"

    # Action
    opt_e = tm['optimal']
    agg_e = tm['aggressive']
    inval = tm['invalidation']
    if decision == 'REFUSÉ':
        action = 'Aucune position — score insuffisant ou pas de setup.'
    elif decision == 'ATTENTE' and state == 'TOO_LATE':
        action = f"Ne pas chase. Attendre un repli vers ${opt_e}. Invalidation sous ${inval}."
    elif decision == 'ATTENTE':
        action = f"Acheter seulement sur cassure confirmée au-dessus de ${agg_e} (volume). Sinon patienter."
    elif decision == 'ACCEPTÉ SUR REPLI':
        action = f"Entrée optimale sur repli vers ${opt_e} · agressive sur cassure > ${agg_e} · stop ${inval}."
    else:  # ACCEPTÉ
        action = f"Entrée propre vers ${opt_e} · stop ${inval} · viser les TP du plan."

    taille = sc['alloc'] if decision != 'REFUSÉ' else '0 %'

    return {
        'decision': decision, 'tone': dtone,
        'score40': total, 'niveau': sc['niveau'], 'alloc': taille,
        'components': {'Fondamentaux': [sc['fond'], 8], 'Technique': [sc['tech'], 8],
                       'Catalyseur': [sc['cata'], 6], 'Institutions': [sc['inst'], 6],
                       'Option Fit': [sc['optfit'], 6], 'Asymétrie': [sc['asym'], 6]},
        'color': sc['color'],
        'timing': {'state': state, 'label': tm['label'],
                   'optimal': opt_e, 'aggressive': agg_e, 'invalidation': inval},
        'no_chase': nc,
        'raison': raison, 'action': action, 'taille': taille,
    }
