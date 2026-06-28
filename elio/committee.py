"""elio/committee.py — COMITÉ D'INVESTISSEMENT (profil utilisateur encodé).

Profil : investisseur OFFENSIF DE CROISSANCE, concentré (10-15 lignes), actions de
qualité comme socle (70-90 %) + options CALL longues comme levier tactique (10-20 %),
ETF en tampon. Horizon 12-24 mois. Objectif : surperformer largement, discipline
stricte. Biais à maîtriser : l'IMPATIENCE (pas la peur).

Chaque candidat passe les 4 PORTES et reçoit une décision DOCUMENTÉE :
  ✅ Entreprise de qualité   ✅ Catalyseur   ✅ Timing   ✅ R:R ≥ 2:1
  → thèse · plan · invalidation · conviction · verdict (ACHETER/RENFORCER/ATTENDRE/ÉVITER)

Le rôle du comité est aussi de dire ATTENDRE quand l'avantage est insuffisant
(anti-FOMO, anti-impatience). ⛔ ANALYSE ÉDUCATIVE — jamais un ordre.
"""
from . import sectors

PROFILE = {
    'name': 'Offensif de Croissance · Concentrated Growth + LEAPS Trader',
    'alloc': 'Actions 70-90% (socle) · Options CALL 10-20% (levier) · ETF tampon',
    'sectors': 'IA · Logiciels · Semi-conducteurs · Cybersécurité · Fintech',
    'gates': '✅ Entreprise de qualité · ✅ Catalyseur · ✅ Timing · ✅ R:R ≥ 2:1',
    'horizon': '12-24 mois · 10-15 lignes max · forte conviction',
    'discipline': "Risque n°1 = l'impatience. On achète SEULEMENT avec un avantage "
                  "statistique (R:R ≥ 2:1 + catalyseur + timing). Sinon : ATTENDRE.",
}

RR_MIN = 2.0
_THEME_KW = ('tech', 'semi', 'soft', 'logiciel', 'cyber', 'fin', 'ia', 'ai',
             'internet', 'cloud', 'data', 'paiement', 'consum disc')


def _is_theme(sector):
    s = (sector or '').lower()
    return any(k in s for k in _THEME_KW)


def _evaluate_one(sym, d):
    score = d.get('score') or 0
    grade = d.get('grade') or ''
    sig = d.get('signals') or {}
    plan = d.get('plan') or {}
    rr = plan.get('rr_res') or 0
    ext = d.get('ext_atr')
    regime = d.get('regime')
    rsi = d.get('rsi') or 0
    pos52 = d.get('pos52') or 0
    mom = d.get('mom') or 0
    sector = sectors.SECTOR_MAP.get(sym)
    overext = ext is not None and ext >= 3.0

    # STRUCTURE DE MARCHÉ (pivots) — calculée d'abord car elle nourrit R:R et le verdict
    st = d.get('structure') or {}
    s_trend = st.get('trend')
    s_conf = bool(st.get('confirmed'))
    s_logic = st.get('logic') or ''
    s_hi, s_lo, s_rr = st.get('last_high'), st.get('last_low'), st.get('rr')

    g_company = score >= 62 and grade in ('S', 'A', 'B')
    g_catalyst = bool(sig.get('goldenNow') or sig.get('momCross')
                      or (sig.get('volUp') and (d.get('roc') or 0) > 4) or mom >= 70 or s_conf)
    g_timing = regime != 'CHOP' and sig.get('above50') and not overext and rsi < 75
    # R:R ≥ 2:1 vers la cible OU entrée confirmée par la structure en tendance haussière
    # (cassure / repli repris = le plan vise 2-3× le risque, achevable en tendance).
    breakout = pos52 >= 78 and mom >= 60 and not overext
    g_rr = rr >= RR_MIN or breakout or (s_conf and s_trend == 'UP')

    gates = {'company': g_company, 'catalyst': g_catalyst, 'timing': g_timing, 'rr': g_rr}

    # ── TIMING D'ENTRÉE : prix sous lequel le R:R redevient ≥ 2:1 (anti-impatience)
    # R:R = (résistance − entrée)/(entrée − stop) ≥ 2  ⇒  entrée ≤ (résistance + 2·stop)/3
    res, stop, price = plan.get('resistance'), plan.get('stop'), d.get('price')
    entry_zone, in_zone = None, False
    if res and stop and price and res > stop:
        ez = (res + 2 * stop) / 3.0
        if ez < price:                      # zone de repli réaliste sous le cours
            entry_zone = round(ez, 2)
            in_zone = price <= entry_zone

    gates['structure'] = s_conf

    # ── VERDICT (discipline avant tout) ──────────────────────────────────────
    if not g_company:
        verdict, color = 'ÉVITER', '#EF4444'
        note = f"Qualité insuffisante (score {score}, {grade or '—'}) — hors critères."
    elif s_trend == 'DOWN':
        # REFUS EXPLICITE : on n'achète pas un rebond en tendance baissière
        verdict, color = 'ÉVITER', '#EF4444'
        note = (f"⛔ Tendance BAISSIÈRE (sommets/creux descendants) — un rebond ici est un PIÈGE, "
                f"pas un achat. Aucun long tant que ${s_hi} n'est pas repris (cassure de structure).")
    elif overext:
        verdict, color = 'ATTENDRE', '#FFB23F'
        note = f"Titre étendu (+{ext}× ATR au-dessus de la MM20) — risque de repli, attendre un repli sur ${s_lo or plan.get('stop')}."
    elif not g_timing:
        verdict, color = 'ATTENDRE', '#FFB23F'
        why = 'range (chop)' if regime == 'CHOP' else 'sous la MM50' if not sig.get('above50') else f'RSI {rsi} (suracheté)'
        note = f"Timing défavorable : {why}. On patiente."
    elif not g_rr:
        verdict, color = 'ATTENDRE', '#FFB23F'
        if entry_zone and in_zone:
            verdict, color = 'ACHETER', '#22C55E'
            note = f"✅ DANS LA ZONE D'ACHAT (≤ ${entry_zone}) — le repli est là, R:R redevient ≥ 2:1. Fenêtre d'entrée ouverte."
        elif entry_zone:
            note = f"R:R {rr}:1 < 2:1 — avantage insuffisant. 🎯 Zone d'achat : sous ${entry_zone} (R:R ≥ 2:1). On guette le repli, on ne court pas après."
        else:
            note = f"R:R {rr}:1 < 2:1 — avantage insuffisant. Attendre un meilleur point d'entrée."
    elif not s_conf:
        # PAS d'entrée confirmée par la structure → on ne court pas après un simple rebond
        verdict, color = 'ATTENDRE', '#FFB23F'
        note = f"Pas d'entrée CONFIRMÉE par la structure. {s_logic} On ne force pas."
    elif score >= 74:
        verdict, color = 'ACHETER', '#22C55E'
        note = f"✅ ENTRÉE CONFIRMÉE — {s_logic} R:R {rr}:1 · forte conviction. Setup d'élite."
    else:
        verdict, color = 'RENFORCER', '#34D399'
        note = f"✅ ENTRÉE CONFIRMÉE — {s_logic} R:R {rr}:1. Position de qualité à construire/renforcer."

    # thèse documentée
    bits = []
    if sector:
        bits.append(sector + (' ★ thème cœur' if _is_theme(sector) else ''))
    if s_trend:
        bits.append({'UP': 'structure haussière (sommets/creux montants)',
                     'DOWN': 'structure baissière', 'RANGE': 'en range'}.get(s_trend, ''))
    bits.append(f"momentum {d.get('mom', '—')}/100, force rel. {d.get('rs', '—')}")
    if sig.get('goldenNow'):
        bits.append('croisement doré (golden cross) récent')
    elif sig.get('stacked'):
        bits.append('moyennes alignées (tendance saine)')
    thesis = ' · '.join(bits) + '.'

    invalidation = f"clôture sous ${plan.get('stop')} ({plan.get('stop_type', 'structure')})"
    return {
        'symbol': sym, 'sector': sector, 'theme': _is_theme(sector),
        'conviction': int(score), 'grade': grade, 'verdict': verdict, 'color': color,
        'gates': gates, 'thesis': thesis, 'note': note,
        'plan': {'entry': plan.get('entry'), 'stop': plan.get('stop'),
                 'tp2': plan.get('tp2'), 'tp3': plan.get('tp3'), 'rr': rr},
        'invalidation': invalidation,
        'entry_zone': entry_zone, 'in_zone': in_zone,
        'vertex': {'verdict': (d.get('vertex') or {}).get('verdict'),
                   'edge': (d.get('vertex') or {}).get('edge'),
                   'no_trade': (d.get('vertex') or {}).get('no_trade')},
        'structure': {'trend': s_trend, 'signal': st.get('signal'),
                      'last_high': s_hi, 'last_low': s_lo,
                      'swing_highs': st.get('swing_highs'), 'swing_lows': st.get('swing_lows')},
        'price': d.get('price'), 'change': d.get('change'),
    }


_ORDER = {'ACHETER': 0, 'RENFORCER': 1, 'ATTENDRE': 2, 'ÉVITER': 3}


def evaluate(rows, detail, market=None, top_n=12):
    decisions = []
    for r in (rows or [])[:top_n]:
        sym = r.get('symbol')
        d = detail.get(sym) if detail else None
        if not d:
            continue
        decisions.append(_evaluate_one(sym, d))
    decisions.sort(key=lambda x: (_ORDER.get(x['verdict'], 9), -x['conviction']))
    counts = {'ACHETER': 0, 'RENFORCER': 0, 'ATTENDRE': 0, 'ÉVITER': 0}
    for x in decisions:
        counts[x['verdict']] = counts.get(x['verdict'], 0) + 1
    actionable = counts['ACHETER'] + counts['RENFORCER']
    if actionable == 0:
        verdict_global = "🟡 AUCUN ACHAT AUJOURD'HUI — pas d'avantage suffisant. On garde le cash, on attend le bon setup."
    elif actionable <= 2:
        verdict_global = f"🟢 {actionable} setup(s) de qualité validé(s) — sélectivité maximale, on n'achète que le meilleur."
    else:
        verdict_global = f"🟢 {actionable} setups validés — marché porteur, déployer avec discipline (R:R ≥ 2:1)."
    return {'profile': PROFILE, 'decisions': decisions, 'counts': counts,
            'verdict_global': verdict_global, 'rr_min': RR_MIN}
