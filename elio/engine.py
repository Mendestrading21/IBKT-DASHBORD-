"""
elio/engine.py — MOTEUR DE DÉCISION : synthétise tout en un verdict clair.

decide(detail, options_pack) → décision (ACHETER FORT / ACHETER / SURVEILLER /
ATTENDRE / ÉVITER) + conviction 0-100 + forces (pros) + risques (cons) + action
concrète. C'est « ce que ferait un trader discipliné » à partir de TOUTES les
données déjà calculées (technique, momentum, régime, setup, confiance, options,
fondamentaux). 100% règles, déterministe. ⛔ ANALYSE ONLY, jamais une promesse.
"""


def _sig(d, k):
    return bool((d.get('signals') or {}).get(k))


def decide(d, opt=None):
    if not d:
        return None
    opt = opt or {}
    score = int(d.get('score', 0))
    trend = float(d.get('trend', 0))
    regime = d.get('regime')
    sq = float(d.get('setup_quality', 50))
    conf = float(d.get('confidence', 50))
    rsi = float(d.get('rsi', 50))
    ext = float(d.get('ext_atr', 0))
    pos = float(d.get('pos52', 50))
    rs = float(d.get('rs', 50))
    volx = float(d.get('volx', 1))
    val = opt.get('valuation') or {}
    ed = opt.get('earnings_dte')
    plan = d.get('plan') or {}

    pros, cons = [], []
    # ── Forces ──
    if _sig(d, 'stacked'):
        pros.append('Tendance haussière nette (MM20 > MM50 > MM200)')
    elif _sig(d, 'above200'):
        pros.append('Au-dessus de la MM200 (fond haussier)')
    if rs >= 65:
        pros.append(f'Surperforme le marché (force relative {round(rs)})')
    if regime == 'TREND':
        pros.append('Marché en tendance directionnelle (ADX élevé)')
    if sq >= 65:
        pros.append(f'Setup de qualité — marge vers la résistance ({round(sq)}/100)')
    if volx >= 1.3:
        pros.append(f'Volume soutenu ({volx:.1f}x la moyenne)')
    if val.get('tone') == 'good':
        pros.append(f"Valorisation décotée vs son secteur (×{val.get('ratio')})")
    if 48 <= rsi <= 68:
        pros.append(f'Momentum sain (RSI {round(rsi)})')
    if conf >= 70:
        pros.append('Sous-scores alignés (haute confiance)')

    # ── Risques ──
    if regime == 'CHOP':
        cons.append('Marché en range agité — les cassures échouent souvent')
    if ext >= 4:
        cons.append(f'Sur-étendu ({ext:.1f} ATR au-dessus de la MM20) — risque de repli')
    if rsi >= 78:
        cons.append(f'RSI en surchauffe ({round(rsi)})')
    if pos >= 98:
        cons.append('Collé au plus-haut 52 semaines (peu de marge)')
    if d.get('rsi_div') == 'bear':
        cons.append('Divergence RSI baissière (essoufflement du momentum)')
    if val.get('tone') == 'warn':
        cons.append(f"Cher vs son secteur (P/E ×{val.get('ratio')})")
    if ed is not None and 0 <= ed <= 14:
        cons.append(f'Résultats dans {ed} jours (risque + IV-crush sur les options)')
    if conf < 45:
        cons.append('Sous-scores contradictoires (faible confiance)')
    if not _sig(d, 'above200'):
        cons.append('Sous la MM200 (tendance de fond non confirmée)')

    # ── Décision ──
    if score >= 80 and regime != 'CHOP' and sq >= 55 and conf >= 55 and len(cons) <= 2:
        dec, tone = 'ACHETER FORT', 'strong'
    elif score >= 75 and _sig(d, 'above50') and regime != 'CHOP':
        dec, tone = 'ACHETER', 'buy'
    elif score >= 60:
        dec, tone = 'SURVEILLER', 'watch'
    elif trend >= 50:
        dec, tone = 'ATTENDRE', 'wait'
    else:
        dec, tone = 'ÉVITER', 'avoid'

    reg_bonus = 15 if regime == 'TREND' else (-12 if regime == 'CHOP' else 0)
    conv = round(min(100, max(0, score * 0.4 + conf * 0.25 + sq * 0.2 + reg_bonus - len(cons) * 4)))

    # ── Action concrète ──
    if dec in ('ACHETER FORT', 'ACHETER'):
        if ext >= 4:
            action = "Sur-étendu : attendre un repli vers la MM20 avant d'entrer."
        else:
            action = (f"Entrée vers ${plan.get('entry')}, stop ${plan.get('stop')} "
                      f"({plan.get('stop_type', '')}), objectifs ${plan.get('tp1')} / "
                      f"${plan.get('tp2')} / ${plan.get('tp3')}.")
    elif dec == 'SURVEILLER':
        action = (f"Pas d'achat franc encore. Guetter une cassure au-dessus de "
                  f"${plan.get('resistance') or plan.get('tp1')} avec du volume.")
    elif dec == 'ATTENDRE':
        action = 'Tendance pas alignée. Attendre le retour au-dessus des moyennes mobiles.'
    else:
        action = 'Structure faible — aucune position tant que la tendance ne se retourne pas.'

    return {'decision': dec, 'tone': tone, 'conviction': conv,
            'pros': pros[:5], 'cons': cons[:5], 'action': action,
            'summary': f'{dec} · conviction {conv}/100 · {len(pros)} forces / {len(cons)} risques'}
