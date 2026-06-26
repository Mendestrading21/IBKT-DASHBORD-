"""
elio/weekly.py — WATCHLIST DE LA SEMAINE (actions + options à suivre).

Objet : produire, le LUNDI (premier scan de la semaine ISO), une LISTE D'ACTIONS
À SUIVRE pour la semaine + les OPTIONS associées, puis GELER ce choix jusqu'au
lundi suivant. On ne régénère donc PAS la sélection à chaque scan : c'est un
snapshot hebdo daté (cohérent avec le profil swing : un plan tenu sur la semaine).

Logique de sélection (depuis rows/detail de scan(), zéro donnée inventée) :
  - candidats = verdict BUY/WATCH, au-dessus de la MM50, régime TREND ou NEUTRAL
    (on exclut CHOP : les cassures meurent dans le bruit),
  - on EXCLUT les titres dont les résultats (earnings) tombent dans la semaine
    (sauf earnings_ok=True) — risque de gap / IV-crush sur une option,
  - score de sélection = mix setup_quality + confidence + score global + bonus
    régime/structure − pénalités surchauffe (RSI, sur-extension ATR),
  - DIVERSIFICATION secteur : max 2 titres par secteur (sectors.SECTOR_MAP),
  - on garde les N meilleurs (défaut 6).

Pour chaque action : pourquoi (catalyseur technique/setup), niveaux clés
(entry / stop / TP1-3 / résistance), et l'OPTION recommandée (court ou long via
options.recommend) avec POP / danger / coût.

Le ROSTER (la liste des titres) est figé pour la semaine. Les NIVEAUX et les
OPTIONS, eux, peuvent être rafraîchis sur demande (les niveaux bougent avec le
prix ; figer le roster ≠ figer les prix). Tout est étiqueté.

⛔ ANALYSE ONLY — aucun ordre, aucune promesse de gain. yfinance différé ~15min.
"""
import json
import os
from datetime import datetime, timedelta

from . import sectors as _sectors
from . import options as _options


# ─── helpers ─────────────────────────────────────────────────────────────
def _g(d, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return default if cur is None else cur


def _f(x, default=0.0):
    try:
        return default if x is None else float(x)
    except Exception:
        return default


def week_id(today=None):
    """Identifiant de semaine ISO : 'YYYY-Www' (ex. 2026-W26). Change chaque lundi."""
    d = today or datetime.now()
    iso = d.isocalendar()
    return '%04d-W%02d' % (iso[0], iso[1])


def monday_of(today=None):
    """Date (str ISO) du lundi de la semaine courante."""
    d = today or datetime.now()
    m = d - timedelta(days=d.weekday())
    return m.strftime('%Y-%m-%d')


def friday_of(today=None):
    d = today or datetime.now()
    m = d - timedelta(days=d.weekday()) + timedelta(days=4)
    return m.strftime('%Y-%m-%d')


# ─── 1) SCORE DE SÉLECTION + RAISONS ─────────────────────────────────────
def _select_score(d):
    """Note de priorité hebdo (0-100+) d'un titre, depuis son detail analyse().
    Mélange qualité du setup, confiance, score global, régime ; pénalise la surchauffe."""
    score = _f(d.get('score'))
    setup = _f(_g(d, 'plan', 'setup_quality', default=d.get('setup_quality')))
    conf = _f(d.get('confidence'), 50)
    trend = _f(d.get('trend'))
    rr_res = _f(_g(d, 'plan', 'rr_res'))
    ext_atr = _f(d.get('ext_atr'))
    rsi = _f(d.get('rsi'), 50)
    regime = d.get('regime')

    s = 0.42 * score + 0.30 * setup + 0.16 * conf + 0.12 * (trend)
    if regime == 'TREND':
        s += 8                      # tendance propre = terrain favorable au swing
    elif regime == 'CHOP':
        s -= 12
    s += min(rr_res, 4) * 2.0       # plus de marge vers la résistance = meilleur R:R
    if ext_atr >= 4:                # déjà loin de la MM20 = entrée tardive
        s -= (ext_atr - 4) * 4
    if rsi >= 78:
        s -= (rsi - 78) * 1.5
    return round(s, 1)


def _why(d):
    """Construit la liste des raisons FR (catalyseur / setup) — uniquement depuis des champs réels."""
    bits = []
    if d.get('regime') == 'TREND':
        bits.append('régime TREND (ADX %d)' % round(_f(d.get('adx'))))
    if (d.get('signals') or {}).get('stacked'):
        bits.append('MM empilées (20>50>200)')
    elif (d.get('signals') or {}).get('above50'):
        bits.append('au-dessus MM50')
    if (d.get('signals') or {}).get('goldenNow'):
        bits.append('golden cross récent')
    rs = _f(d.get('rs'), 50)
    if rs >= 60:
        bits.append('force relative %d (bat le SPY)' % round(rs))
    pos52 = _f(d.get('pos52'), 50)
    if pos52 >= 90:
        bits.append('proche +haut 52s (%d%%)' % round(pos52))
    rr_res = _f(_g(d, 'plan', 'rr_res'))
    if rr_res >= 2:
        bits.append('R:R %.1f vers résistance' % rr_res)
    setup = _f(_g(d, 'plan', 'setup_quality', default=d.get('setup_quality')))
    if setup >= 70:
        bits.append('qualité setup %d/100' % round(setup))
    rsi_div = d.get('rsi_div')
    if rsi_div == 'bull':
        bits.append('divergence RSI haussière')
    return bits[:4] or ['setup propre, structure haussière']


def _warnings(d, earn_dte):
    """Garde-fous honnêtes : ce qui pourrait faire échouer le trade cette semaine."""
    w = []
    ext_atr = _f(d.get('ext_atr'))
    rsi = _f(d.get('rsi'), 50)
    pos52 = _f(d.get('pos52'), 50)
    if ext_atr >= 3.5:
        w.append('sur-étendu (%.1f ATR au-dessus MM20)' % ext_atr)
    if rsi >= 76:
        w.append('RSI chaud (%d)' % round(rsi))
    if pos52 >= 98:
        w.append('au sommet 52s — peu de marge')
    if earn_dte is not None and 0 <= earn_dte <= 10:
        w.append('résultats dans %dj (gap possible)' % earn_dte)
    if d.get('regime') == 'NEUTRAL':
        w.append('régime NEUTRAL (tendance pas encore franche)')
    return w


# ─── 2) SÉLECTION DU ROSTER (figé une fois par semaine) ──────────────────
def select_roster(rows, detail, earnings=None, n=6, per_sector=2, earnings_ok=False):
    """Choisit les meilleurs titres à suivre cette semaine.
    earnings = {sym: dte} (jours avant résultats), depuis cal_state. n = nb max de titres."""
    earnings = earnings or {}
    cands = []
    for r in rows:
        sym = r.get('symbol')
        d = detail.get(sym)
        if not d:
            continue
        if d.get('verdict') not in ('BUY', 'WATCH'):
            continue
        if not (d.get('signals') or {}).get('above50'):
            continue
        if d.get('regime') == 'CHOP':            # cassures peu fiables en range agité
            continue
        edte = earnings.get(sym)
        # earnings DANS la semaine (0-7j) → on écarte par défaut (risque gap/IV-crush)
        if not earnings_ok and edte is not None and 0 <= edte <= 7:
            continue
        cands.append((sym, d, edte))

    # tri par score de sélection desc
    cands.sort(key=lambda x: _select_score(x[1]), reverse=True)

    # diversification : max `per_sector` titres par secteur
    picked, sec_count = [], {}
    for sym, d, edte in cands:
        sec = _sectors.SECTOR_MAP.get(sym, '—')
        if sec_count.get(sec, 0) >= per_sector:
            continue
        sec_count[sec] = sec_count.get(sec, 0) + 1
        picked.append((sym, d, edte, sec))
        if len(picked) >= n:
            break
    return picked


# ─── 3) FICHE d'un titre retenu (niveaux + option recommandée) ───────────
def _pick_card(sym, d, edte, sec, with_options=True):
    plan = d.get('plan') or {}
    sel = _select_score(d)
    card = {
        'symbol': sym, 'sector': sec, 'icon': _sectors.SECTOR_ICON.get(sec, '•'),
        'price': round(_f(d.get('price')), 2), 'change': round(_f(d.get('change')), 2),
        'score': int(_f(d.get('score'))), 'grade': d.get('grade') or '—',
        'verdict': d.get('verdict') or 'WATCH', 'select_score': sel,
        'trend': round(_f(d.get('trend'))), 'rs': round(_f(d.get('rs'), 50)),
        'rsi': round(_f(d.get('rsi'), 50)), 'pos52': round(_f(d.get('pos52'), 50)),
        'regime': d.get('regime') or '—', 'adx': round(_f(d.get('adx'))),
        'setup_quality': round(_f(_g(d, 'plan', 'setup_quality', default=d.get('setup_quality')))),
        'confidence': round(_f(d.get('confidence'), 50)),
        'rvol': round(_f(d.get('volx'), 1.0), 2), 'atr_pct': round(_f(d.get('atr_pct')), 2),
        'ext_atr': round(_f(d.get('ext_atr')), 2),
        'earnings_dte': edte,
        'levels': {
            'entry': plan.get('entry'), 'stop': plan.get('stop'),
            'tp1': plan.get('tp1'), 'tp2': plan.get('tp2'), 'tp3': plan.get('tp3'),
            'resistance': plan.get('resistance'), 'rr_res': plan.get('rr_res'),
            'stop_type': plan.get('stop_type'), 'atr': plan.get('atr'),
            'risk_pct': (round((_f(plan.get('entry')) - _f(plan.get('stop'))) / _f(plan.get('entry')) * 100, 2)
                         if _f(plan.get('entry')) else None),
        },
        'why': _why(d),
        'warnings': _warnings(d, edte),
        'series': (d.get('series') or {}).get('close'),
        'option': None,
    }
    if with_options:
        try:
            tgt = plan.get('tp2') or d.get('price')
            contracts = _options.best_for_symbol(
                sym, _f(d.get('price')), _f(tgt), 'call',
                max_n=1, buckets=('court', 'long'), earnings_dte=edte)
            best = _options.recommend(contracts)
            if best:
                card['option'] = {
                    'bucket': best.get('bucket'), 'exp': best.get('exp'), 'dte': best.get('dte'),
                    'strike': best.get('strike'), 'mid': best.get('mid'), 'cost': best.get('cost'),
                    'delta': best.get('delta'), 'pop': best.get('pop'), 'p_tgt': best.get('p_tgt'),
                    'danger': best.get('danger'), 'suit': best.get('suit'), 'grade': best.get('grade'),
                    'pot': best.get('pot'), 'tgt': best.get('tgt'), 'be': best.get('be'),
                    'theta_burn': best.get('theta_burn'), 'iv': best.get('iv'),
                    'flags': best.get('flags'), 'stale': best.get('stale'), 'why': best.get('why'),
                }
        except Exception:
            card['option'] = None
    return card


# ─── 4) BUILD complet de la sélection hebdo ──────────────────────────────
def build_weekly(rows, detail, earnings=None, n=6, with_options=True, today=None):
    """Construit le snapshot hebdo (roster + fiches). Appelé UNE fois le lundi puis figé."""
    picked = select_roster(rows, detail, earnings=earnings, n=n)
    picks = [_pick_card(sym, d, edte, sec, with_options=with_options) for sym, d, edte, sec in picked]
    sectors_used = sorted({p['sector'] for p in picks})
    n_options = sum(1 for p in picks if p.get('option'))
    return {
        'week': week_id(today),
        'monday': monday_of(today), 'friday': friday_of(today),
        'generated_at': (today or datetime.now()).strftime('%Y-%m-%d %H:%M'),
        'picks': picks,
        'meta': {
            'n': len(picks), 'n_options': n_options,
            'sectors': sectors_used, 'n_sectors': len(sectors_used),
            'universe': len(rows),
            'disclaimer': 'Sélection FIGÉE pour la semaine (snapshot du lundi). '
                          'Niveaux et options rafraîchis live. ANALYSE ONLY — aucun ordre, '
                          'aucune promesse. yfinance différé ~15min.',
        },
    }


# ─── 5) PERSISTANCE HEBDO (un snapshot par semaine ISO, figé) ────────────
def load_snapshot(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_snapshot(path, snap):
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def get_or_build(path, rows, detail, earnings=None, n=6, with_options=True,
                 today=None, force=False):
    """Cœur de la persistance :
      - si un snapshot existe pour la semaine ISO courante → on le RENVOIE tel quel (figé),
        en rafraîchissant seulement le PRIX/var courant + niveaux des fiches (le roster reste figé) ;
      - sinon (nouvelle semaine, ou force=True) → on REGÉNÈRE et on persiste.
    Renvoie (snapshot, regenerated:bool)."""
    wid = week_id(today)
    snap = load_snapshot(path)
    if snap and snap.get('week') == wid and not force:
        # roster figé : on ré-aligne juste les chiffres vivants sur le dernier scan
        _refresh_live(snap, detail)
        return snap, False
    # nouvelle semaine (ou forçage) → on reconstruit
    snap = build_weekly(rows, detail, earnings=earnings, n=n,
                        with_options=with_options, today=today)
    save_snapshot(path, snap)
    return snap, True


def _refresh_live(snap, detail):
    """Met à jour, SANS changer le roster, les chiffres qui bougent (prix, var, score,
    verdict, niveaux du plan) pour chaque titre figé — à partir du dernier scan."""
    for p in (snap.get('picks') or []):
        d = detail.get(p.get('symbol'))
        if not d:
            p['live'] = None
            continue
        plan = d.get('plan') or {}
        p['live'] = {
            'price': round(_f(d.get('price')), 2), 'change': round(_f(d.get('change')), 2),
            'score': int(_f(d.get('score'))), 'verdict': d.get('verdict'),
            'rsi': round(_f(d.get('rsi'), 50)), 'regime': d.get('regime'),
            'entry': plan.get('entry'), 'stop': plan.get('stop'),
            'tp1': plan.get('tp1'), 'tp2': plan.get('tp2'),
            'resistance': plan.get('resistance'), 'rr_res': plan.get('rr_res'),
            # statut vs le plan figé : déclenché / en cours / cassé
            'status': _trade_status(p, d, plan),
        }
        p['series'] = (d.get('series') or {}).get('close') or p.get('series')
    return snap


def _trade_status(p, d, plan):
    """État du trade vs le plan FIGÉ du lundi (purement descriptif, pas un ordre)."""
    px = _f(d.get('price'))
    entry0 = _f(_g(p, 'levels', 'entry'))
    stop0 = _f(_g(p, 'levels', 'stop'))
    tp1_0 = _f(_g(p, 'levels', 'tp1'))
    if not px or not entry0:
        return '—'
    if stop0 and px <= stop0:
        return 'STOP touché'
    if tp1_0 and px >= tp1_0:
        return 'TP1 atteint'
    if px >= entry0:
        return 'au-dessus entrée'
    return 'sous entrée (attente)'
