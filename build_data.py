#!/usr/bin/env python3
"""
build_data.py - builds docs/data.json for the Acca Picks PWA.

Free data only: football-data.co.uk
  * fixtures.csv                      upcoming matches + Bet365 / market-average odds
  * mmz4281/<season>/<DIV>.csv        results (goals, corners) for current + previous season

Model (Dixon-Coles-lite):
  1. Team attack/defence strengths from weighted results (current season weight 1,
     previous season 0.35), shrunk towards the league average.
  2. Expected goals = league home/away average x attack x opposing defence
     (home advantage is built in through the separate home/away league averages).
  3. If the bookmakers have a price (Bet365 / market average 1X2 and 2.5 goals) the
     expected goals are blended 60/40 (market/model) with the goals implied by the
     de-vigged odds, so the model never strays far from the market.
  4. Scoreline grid (Poisson with a small Dixon-Coles low-score correction) gives
     1X2, double chance, draw no bet, BTTS and over/under goals.
  5. Corners: team corners-for / corners-against rates, negative-binomial total.

Odds are NEVER invented. Where we have no bookmaker price for a market, `odds` is
null and only the model's fair odds (an estimate) are shown.
"""
import csv, io, json, math, os, sys, time, difflib, urllib.request, urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data.json")
CACHE = os.path.join(HERE, ".cache")
LONDON = ZoneInfo("Europe/London")
BASE = "https://www.football-data.co.uk"
UA = {"User-Agent": "Mozilla/5.0 (acca-picks-pwa; personal use)"}

# Season codes: football-data uses e.g. 2627 for 2026/27.
def season_codes(now):
    y = now.year if now.month >= 7 else now.year - 1
    f = lambda a: "%02d%02d" % (a % 100, (a + 1) % 100)
    return f(y), f(y - 1)

LEAGUES = {
    "E0": "England: Premier League", "E1": "England: Championship", "E2": "England: League One",
    "E3": "England: League Two", "EC": "England: National League",
    "SC0": "Scotland: Premiership", "SC1": "Scotland: Championship", "SC2": "Scotland: League One",
    "SC3": "Scotland: League Two",
    "D1": "Germany: Bundesliga", "D2": "Germany: 2. Bundesliga", "SP1": "Spain: La Liga",
    "SP2": "Spain: Segunda", "I1": "Italy: Serie A", "I2": "Italy: Serie B", "F1": "France: Ligue 1",
    "F2": "France: Ligue 2", "N1": "Netherlands: Eredivisie", "B1": "Belgium: Pro League",
    "P1": "Portugal: Primeira Liga", "T1": "Turkey: Super Lig", "G1": "Greece: Super League",
}

WEIGHT_CUR, WEIGHT_PREV = 1.0, 0.35
SHRINK_GOALS = 6.0      # pseudo-games of league-average performance
SHRINK_CORNERS = 5.0
MARKET_WEIGHT = 0.60
RHO = -0.04             # Dixon-Coles low-score correlation
MAXG = 10
DAYS_AHEAD = 7
MIN_P_KEEP = 0.30

# ------------------------------------------------------------------ download
def fetch(url, name, retries=3):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    err = None
    if os.environ.get("ACCA_USE_CACHE") and os.path.exists(path):   # dev shortcut
        return open(path, encoding="utf-8-sig", errors="replace").read()
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
            if len(raw) < 200 or raw.lstrip()[:1] == b"<":
                raise ValueError("not a CSV (%d bytes)" % len(raw))
            with open(path, "wb") as f:
                f.write(raw)
            return raw.decode("utf-8-sig", errors="replace")
        except Exception as e:  # noqa
            err = e
            time.sleep(1.5 * (i + 1))
    if os.path.exists(path):
        age = (time.time() - os.path.getmtime(path)) / 3600
        print("  ! download failed (%s) - using cache %.0fh old: %s" % (err, age, name))
        return open(path, encoding="utf-8-sig", errors="replace").read()
    print("  ! download failed (%s) and no cache: %s" % (err, name))
    return None

def rows(text):
    return list(csv.DictReader(io.StringIO(text))) if text else []

def fnum(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None

def odds(x):
    v = fnum(x)
    return v if v and v > 1.0 else None

def parse_dt(date, tm):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.strptime(date.strip(), fmt)
            break
        except ValueError:
            d = None
    if d is None:
        return None
    try:
        hh, mm = (tm or "12:00").strip().split(":")[:2]
        d = d.replace(hour=int(hh), minute=int(mm))
    except ValueError:
        pass
    return d.replace(tzinfo=LONDON)   # football-data times are UK local time

# ------------------------------------------------------------------ maths
_pois_cache = {}
def pois(l):
    k = round(l, 4)
    if k not in _pois_cache:
        _pois_cache[k] = np.array([math.exp(-l + i * math.log(max(l, 1e-9)) - math.lgamma(i + 1)) for i in range(MAXG + 1)])
    return _pois_cache[k]

def grid(lh, la, rho=RHO):
    M = np.outer(pois(lh), pois(la))
    M[0, 0] *= max(0.0, 1 - lh * la * rho)
    M[0, 1] *= max(0.0, 1 + lh * rho)
    M[1, 0] *= max(0.0, 1 + la * rho)
    M[1, 1] *= max(0.0, 1 - rho)
    return M / M.sum()

IDX = np.add.outer(np.arange(MAXG + 1), np.arange(MAXG + 1))
HI, AI = np.meshgrid(np.arange(MAXG + 1), np.arange(MAXG + 1), indexing="ij")

def outcomes(M):
    return {
        "H": float(M[HI > AI].sum()), "D": float(M[HI == AI].sum()), "A": float(M[HI < AI].sum()),
        "btts": float(M[1:, 1:].sum()),
        "o15": float(M[IDX >= 2].sum()), "o25": float(M[IDX >= 3].sum()),
    }

def devig(os_):
    """Proportional de-vig. os_ = list of decimal odds for a complete market."""
    if any(o is None for o in os_):
        return None
    inv = [1 / o for o in os_]
    s = sum(inv)
    return [i / s for i in inv]

def fit_lambdas(target):
    """Find (lh, la) whose grid best matches market probs: H,D,A and optional o25 (vectorised grid search)."""
    n = MAXG + 1
    i = np.arange(n)
    lg_fact = np.array([math.lgamma(k + 1) for k in range(n)])
    lo_h, hi_h, lo_a, hi_a = 0.15, 4.0, 0.15, 4.0
    best = None
    for rnd in range(4):
        hs = np.linspace(lo_h, hi_h, 25)
        as_ = np.linspace(lo_a, hi_a, 25)
        LH, LA = np.meshgrid(hs, as_, indexing="ij")
        lh, la = LH.ravel(), LA.ravel()
        ph = np.exp(-lh[:, None] + i[None, :] * np.log(lh[:, None]) - lg_fact[None, :])
        pa = np.exp(-la[:, None] + i[None, :] * np.log(la[:, None]) - lg_fact[None, :])
        M = ph[:, :, None] * pa[:, None, :]
        M[:, 0, 0] *= np.maximum(0, 1 - lh * la * RHO)
        M[:, 0, 1] *= np.maximum(0, 1 + lh * RHO)
        M[:, 1, 0] *= np.maximum(0, 1 + la * RHO)
        M[:, 1, 1] *= (1 - RHO)
        M /= M.sum(axis=(1, 2), keepdims=True)
        H = (M * (HI > AI)).sum(axis=(1, 2)); D = (M * (HI == AI)).sum(axis=(1, 2)); A = 1 - H - D
        err = (H - target["H"]) ** 2 + (D - target["D"]) ** 2 + (A - target["A"]) ** 2
        if target.get("o25") is not None:
            O = (M * (IDX >= 3)).sum(axis=(1, 2))
            err = err + 1.5 * (O - target["o25"]) ** 2
        k = int(np.argmin(err))
        best = (lh[k], la[k])
        sh, sa = (hi_h - lo_h) / 24 * 2, (hi_a - lo_a) / 24 * 2
        lo_h, hi_h, lo_a, hi_a = max(0.05, best[0] - sh), best[0] + sh, max(0.05, best[1] - sa), best[1] + sa
    return best

def nb_pmf(mean, phi, n=40):
    k = np.arange(n + 1)
    if phi <= 1.02:
        return np.array([math.exp(-mean + i * math.log(mean) - math.lgamma(i + 1)) for i in k])
    r = mean / (phi - 1)
    p = r / (r + mean)
    lp = np.array([math.lgamma(i + r) - math.lgamma(r) - math.lgamma(i + 1) + r * math.log(p) + i * math.log(1 - p) for i in k])
    pm = np.exp(lp)
    return pm / pm.sum()

def tier(p, edge, has_price=False):
    # S needs a real bookmaker price (not a derived/estimated one) with positive edge.
    if p >= 0.80 and edge is not None and edge > 0 and has_price:
        return "S"
    if p >= 0.72:
        return "A"
    if p >= 0.63:
        return "B"
    return "C"

def pct(x):
    return "%d%%" % round(x * 100)

# ------------------------------------------------------------------ results model
class League:
    def __init__(self, code):
        self.code = code
        self.matches = []   # dict(date, h, a, hg, ag, hc, ac, w)

    def add(self, text, w):
        for r in rows(text):
            hg, ag = fnum(r.get("FTHG")), fnum(r.get("FTAG"))
            if hg is None or ag is None or not r.get("HomeTeam"):
                continue
            d = parse_dt(r.get("Date", ""), r.get("Time", ""))
            self.matches.append(dict(date=d, h=r["HomeTeam"].strip(), a=r["AwayTeam"].strip(), hg=hg, ag=ag,
                                     hc=fnum(r.get("HC")), ac=fnum(r.get("AC")), w=w))

    def build(self):
        ms = self.matches
        sw = sum(m["w"] for m in ms) or 1
        self.mu_h = sum(m["hg"] * m["w"] for m in ms) / sw if ms else 1.5
        self.mu_a = sum(m["ag"] * m["w"] for m in ms) / sw if ms else 1.2
        cm = [m for m in ms if m["hc"] is not None and m["ac"] is not None]
        cw = sum(m["w"] for m in cm)
        self.has_corners = cw > 20
        self.mu_hc = sum(m["hc"] * m["w"] for m in cm) / cw if cw else 5.5
        self.mu_ac = sum(m["ac"] * m["w"] for m in cm) / cw if cw else 4.5
        self.teams = sorted({m["h"] for m in ms} | {m["a"] for m in ms})
        acc = defaultdict(lambda: defaultdict(float))
        for m in ms:
            w = m["w"]
            for team, gf, ga, home in ((m["h"], m["hg"], m["ag"], True), (m["a"], m["ag"], m["hg"], False)):
                t = acc[team]
                t["w"] += w; t["gf"] += gf * w; t["ga"] += ga * w
                t["exp_f"] += (self.mu_h if home else self.mu_a) * w   # expected scored vs avg opp
                t["exp_a"] += (self.mu_a if home else self.mu_h) * w
        self.att, self.dfn, self.gw = {}, {}, {}
        for team, t in acc.items():
            self.att[team] = (t["gf"] + SHRINK_GOALS * 1.0 * (t["exp_f"] / t["w"])) / (t["exp_f"] + SHRINK_GOALS * (t["exp_f"] / t["w"]))
            self.dfn[team] = (t["ga"] + SHRINK_GOALS * (t["exp_a"] / t["w"])) / (t["exp_a"] + SHRINK_GOALS * (t["exp_a"] / t["w"]))
            self.gw[team] = t["w"]
        cacc = defaultdict(lambda: defaultdict(float))
        for m in cm:
            w = m["w"]
            for team, cf, ca, e_f, e_a in ((m["h"], m["hc"], m["ac"], self.mu_hc, self.mu_ac), (m["a"], m["ac"], m["hc"], self.mu_ac, self.mu_hc)):
                t = cacc[team]
                t["w"] += w; t["cf"] += cf * w; t["ca"] += ca * w; t["ef"] += e_f * w; t["ea"] += e_a * w
        self.catt, self.cdef, self.cw = {}, {}, {}
        for team, t in cacc.items():
            ef, ea = t["ef"] / t["w"], t["ea"] / t["w"]
            self.catt[team] = (t["cf"] + SHRINK_CORNERS * ef) / (t["ef"] + SHRINK_CORNERS * ef)
            self.cdef[team] = (t["ca"] + SHRINK_CORNERS * ea) / (t["ea"] + SHRINK_CORNERS * ea)
            self.cw[team] = t["w"]
        # corner dispersion from in-sample residuals
        res, means = [], []
        for m in cm:
            mean = self.corner_mean(m["h"], m["a"])
            if mean:
                res.append((m["hc"] + m["ac"] - mean) ** 2); means.append(mean)
        self.phi = max(1.05, min(2.5, (sum(res) / len(res)) / (sum(means) / len(means)) * 1.1)) if res else 1.4

    def resolve(self, name):
        if name in self.att:
            return name
        m = difflib.get_close_matches(name, self.teams, n=1, cutoff=0.82)
        return m[0] if m else None

    def goal_lambdas(self, h, a):
        lh = self.mu_h * self.att.get(h, 1.0) * self.dfn.get(a, 1.0)
        la = self.mu_a * self.att.get(a, 1.0) * self.dfn.get(h, 1.0)
        return lh, la

    def corner_mean(self, h, a):
        if h not in self.catt or a not in self.catt:
            return None
        return self.mu_hc * self.catt[h] * self.cdef[a] + self.mu_ac * self.catt[a] * self.cdef[h]

    def recent(self, team, n=8):
        """Most recent n matches of a team, current season first (by date)."""
        ms = [m for m in self.matches if m["h"] == team or m["a"] == team]
        ms.sort(key=lambda m: m["date"] or datetime(2000, 1, 1, tzinfo=LONDON), reverse=True)
        return ms[:n]

def rate(ms, fn):
    vals = [fn(m) for m in ms]
    vals = [v for v in vals if v is not None]
    return (sum(vals), len(vals))

# ------------------------------------------------------------------ main build
def main():
    now = datetime.now(LONDON)
    cur, prev = season_codes(now)
    print("Season codes: current %s, previous %s" % (cur, prev))
    fx_text = fetch(BASE + "/fixtures.csv", "fixtures.csv")
    if not fx_text:
        sys.exit("Could not download fixtures.csv and there is no cache.")
    fixtures = rows(fx_text)
    codes = sorted({r["Div"] for r in fixtures if r.get("Div")})
    print("fixtures.csv: %d rows, leagues: %s" % (len(fixtures), ", ".join(codes)))

    leagues = {}
    for code in codes:
        lg = League(code)
        t1 = fetch("%s/mmz4281/%s/%s.csv" % (BASE, cur, code), "%s_%s.csv" % (cur, code))
        lg.add(t1, WEIGHT_CUR)
        n_cur = len(lg.matches)
        t2 = fetch("%s/mmz4281/%s/%s.csv" % (BASE, prev, code), "%s_%s.csv" % (prev, code))
        lg.add(t2, WEIGHT_PREV)
        lg.build()
        lg.n_cur = n_cur
        leagues[code] = lg
        print("  %-4s current-season results %3d, previous %3d, corners data %s, phi %.2f" %
              (code, n_cur, len(lg.matches) - n_cur, "yes" if lg.has_corners else "no", lg.phi))

    horizon = now + timedelta(days=DAYS_AHEAD)
    out_fx, unmatched = [], []
    for r in fixtures:
        code = r.get("Div")
        lg = leagues.get(code)
        ko = parse_dt(r.get("Date", ""), r.get("Time", ""))
        if not lg or not ko or ko < now or ko > horizon:
            continue
        hn, an = lg.resolve(r["HomeTeam"].strip()), lg.resolve(r["AwayTeam"].strip())
        if hn is None: unmatched.append("%s: %s" % (code, r["HomeTeam"]))
        if an is None: unmatched.append("%s: %s" % (code, r["AwayTeam"]))
        out_fx.append(make_fixture(lg, code, ko, r, hn, an))

    out_fx.sort(key=lambda f: f["kickoff"])
    n_picks = sum(len(f["picks"]) for f in out_fx)

    markets = [
        dict(key="1x2", label="Match result (1X2)", boost=True, odds="Bet365"),
        dict(key="dc", label="Double chance", boost=False, odds="derived"),
        dict(key="dnb", label="Draw no bet", boost=False, odds="derived"),
        dict(key="btts", label="Both teams to score", boost=True, odds=None),
        dict(key="ou15", label="Over/Under 1.5 goals", boost=False, odds=None),
        dict(key="ou25", label="Over/Under 2.5 goals", boost=False, odds="Bet365"),
        dict(key="c85", label="Corners 8.5", boost=False, odds=None),
        dict(key="c95", label="Corners 9.5", boost=False, odds=None),
        dict(key="c105", label="Corners 10.5", boost=False, odds=None),
    ]
    data = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "generated_at_iso": now.isoformat(timespec="minutes"),
        "timezone": "Europe/London",
        "season": cur,
        "horizon_days": DAYS_AHEAD,
        "markets": markets,
        "leagues": [dict(code=c, name=LEAGUES.get(c, c)) for c in codes if any(f["league"] == c for f in out_fx)],
        "notes": [
            "Source: football-data.co.uk (fixtures.csv for upcoming matches and odds; season results CSVs for team strengths and corners). Free data, no paid APIs.",
            "Bet365 prices come from football-data's fixtures.csv snapshot and can be hours old. ALWAYS check the live price on Bet365 before placing.",
            "Bookmaker odds exist in the free data only for match result (1X2) and over/under 2.5 goals. For every other market 'odds' is null and fair odds are the model's estimate.",
            "Double chance and draw-no-bet 'implied' probabilities are derived from the 1X2 market; the real Bet365 price for those is not in the data.",
            "Estimated probabilities blend a Poisson (Dixon-Coles-lite) model with de-vigged Bet365/market odds where available (60% market, 40% model). Corner estimates are pure model (negative binomial).",
            "Tiers: S = 80%+ estimate AND positive edge against a real Bet365 price (so S is only possible for 1X2 and over/under 2.5 goals); A = 72%+; B = 63-72%; C = below 63%.",
            "Edge = estimated probability minus de-vigged bookmaker probability, in percentage points. EV = expected profit per 1 staked at the quoted Bet365 odds.",
            "Early season: few results per team, so strengths are shrunk towards the league average and last season counts at 35% weight. Line-ups, injuries, weather and referees are NOT modelled.",
            "Estimates are not guarantees. Accumulators multiply probabilities, so long accas almost always lose.",
        ],
        "stats": {"fixtures": len(out_fx), "picks": n_picks, "unmatched_teams": sorted(set(unmatched))},
        "fixtures": out_fx,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
    print("Wrote %s: %d fixtures, %d picks, %.0f KB" % (OUT, len(out_fx), n_picks, os.path.getsize(OUT) / 1024))
    if unmatched:
        print("Unmatched teams (league-average used): %s" % sorted(set(unmatched)))

def make_fixture(lg, code, ko, r, hn, an):
    home, away = r["HomeTeam"].strip(), r["AwayTeam"].strip()
    hk, ak = hn or home, an or away
    lh0, la0 = lg.goal_lambdas(hk, ak)

    # ---- market data
    b = {k: odds(r.get(k)) for k in ("B365H", "B365D", "B365A", "B365>2.5", "B365<2.5",
                                      "AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5", "PPH", "PPD", "PPA")}
    def pick_src(keys_by_src):
        for name, keys in keys_by_src:
            ps = devig([b[k] for k in keys])
            if ps:
                return name, ps
        return None, None
    # implied (shown, edge baseline): Bet365 first, then Pinnacle, then market average
    src12, imp12 = pick_src([("Bet365", ("B365H", "B365D", "B365A")), ("Pinnacle", ("PPH", "PPD", "PPA")), ("Market avg", ("AvgH", "AvgD", "AvgA"))])
    srcou, impou = pick_src([("Bet365", ("B365>2.5", "B365<2.5")), ("Market avg", ("Avg>2.5", "Avg<2.5"))])
    # market consensus used for blending: average of all available de-vigged 1X2
    cons = [p for p in (devig([b["B365H"], b["B365D"], b["B365A"]]), devig([b["PPH"], b["PPD"], b["PPA"]]), devig([b["AvgH"], b["AvgD"], b["AvgA"]])) if p]
    cons_ou = [p for p in (devig([b["B365>2.5"], b["B365<2.5"]]), devig([b["Avg>2.5"], b["Avg<2.5"]])) if p]
    lh, la = lh0, la0
    mkt_used = False
    if cons:
        m = np.mean(cons, axis=0)
        target = dict(H=m[0], D=m[1], A=m[2], o25=float(np.mean([c[0] for c in cons_ou])) if cons_ou else None)
        mh, ma = fit_lambdas(target)
        lh = math.exp((1 - MARKET_WEIGHT) * math.log(lh0) + MARKET_WEIGHT * math.log(mh))
        la = math.exp((1 - MARKET_WEIGHT) * math.log(la0) + MARKET_WEIGHT * math.log(ma))
        mkt_used = True
    o = outcomes(grid(lh, la))
    lo = outcomes(grid(lh0, la0))
    mr = {}
    if cons:
        mm = np.mean(cons, axis=0); mr = dict(H=float(mm[0]), D=float(mm[1]), A=float(mm[2]))

    hr, ar = lg.recent(hk, 8), lg.recent(ak, 8)
    hnm, anm = home, away
    low_sample = lg.gw.get(hk, 0) < 4 or lg.gw.get(ak, 0) < 4
    picks = []

    def add(m, s, label, p, odds_, book, implied, imp_src, reason, derived_implied=False):
        if p < MIN_P_KEEP:
            return
        edge = None if implied is None else (p - implied) * 100
        ev = None if odds_ is None else p * odds_ - 1
        t = tier(p, edge, odds_ is not None)
        if low_sample and t in ("S", "A"):
            t = "B"
        picks.append(dict(m=m, s=s, label=label, p=round(p, 4), fair=round(1 / p, 2),
                          odds=odds_, book=book, implied=None if implied is None else round(implied, 4),
                          imp_src=imp_src, edge=None if edge is None else round(edge, 1),
                          ev=None if ev is None else round(ev, 3), tier=t, reason=reason))

    xg = "xG %.1f-%.1f" % (lh, la)
    def mk(txt):  # market comparison text
        return txt
    # ---- 1X2
    names = {"H": ("home", hnm + " to win"), "D": ("draw", "Draw"), "A": ("away", awayn(anm))}
    bk = {"H": b["B365H"], "D": b["B365D"], "A": b["B365A"]}
    for i, k in enumerate("HDA"):
        imp = imp12[i] if imp12 else None
        why = "Model %s (%s)" % (pct(o[k]), xg)
        if imp is not None:
            why += " vs %s %s de-vigged" % (src12, pct(imp))
        add("1x2", names[k][0], names[k][1], o[k], bk[k], "Bet365" if bk[k] else None, imp, src12, why)

    # ---- double chance, DNB (implied derived from 1X2 market)
    dcs = {"1X": ("H", "D", hnm + " or draw"), "X2": ("D", "A", awayn(anm, True)), "12": ("H", "A", hnm + " or " + anm)}
    for key, (a_, b_, label) in dcs.items():
        p = o[a_] + o[b_]
        imp = (imp12[0 if a_ == "H" else 1] + imp12[1 if b_ == "D" else 2]) if imp12 else None
        why = "Model %s (%s)" % (pct(p), xg)
        if imp is not None:
            why += "; market-implied %s (derived from 1X2)" % pct(imp)
        add("dc", key, label + " (DC)", p, None, None, imp, "derived from " + src12 if src12 else None, why)
    for side, k, other, label in (("home", "H", "A", hnm), ("away", "A", "H", anm)):
        p = o[k] / (o["H"] + o["A"])
        imp = (imp12[0] / (imp12[0] + imp12[2]) if side == "home" else imp12[2] / (imp12[0] + imp12[2])) if imp12 else None
        why = "Model %s of non-draw outcomes (%s); stake back if draw" % (pct(p), xg)
        if imp is not None:
            why += "; market-implied %s (derived)" % pct(imp)
        add("dnb", side, label + " (DNB)", p, None, None, imp, "derived from " + src12 if src12 else None, why)

    # ---- BTTS
    hb, nb_ = rate(hr, lambda m: 1 if m["hg"] > 0 and m["ag"] > 0 else 0)
    ab, na_ = rate(ar, lambda m: 1 if m["hg"] > 0 and m["ag"] > 0 else 0)
    form = "BTTS in %d/%d of %s's and %d/%d of %s's last games" % (hb, nb_, hnm, ab, na_, anm)
    add("btts", "yes", "BTTS Yes", o["btts"], None, None, None, None, "Model %s (%s); %s" % (pct(o["btts"]), xg, form))
    add("btts", "no", "BTTS No", 1 - o["btts"], None, None, None, None, "Model %s (%s); %s" % (pct(1 - o["btts"]), xg, form))

    # ---- goals totals
    for key, line, po, lkey in (("ou15", 1.5, None, "o15"), ("ou25", 2.5, 1, "o25")):
        hg_, nh = rate(hr, lambda m: 1 if m["hg"] + m["ag"] > line else 0)
        ag_, na2 = rate(ar, lambda m: 1 if m["hg"] + m["ag"] > line else 0)
        form = "over %.1f in %d/%d (%s) and %d/%d (%s) recent games" % (line, hg_, nh, hnm, ag_, na2, anm)
        for side, p, i_ in (("over", o[lkey], 0), ("under", 1 - o[lkey], 1)):
            if key == "ou25":
                bo = b["B365>2.5"] if i_ == 0 else b["B365<2.5"]
                imp = impou[i_] if impou else None
                why = "Model %s (%s); %s" % (pct(p), xg, form)
                if imp is not None:
                    why += "; %s %s de-vigged" % (srcou, pct(imp))
                add(key, side, "%s %.1f goals" % (side.title(), line), p, bo, "Bet365" if bo else None, imp, srcou, why)
            else:
                add(key, side, "%s %.1f goals" % (side.title(), line), p, None, None, None, None, "Model %s (%s); %s" % (pct(p), xg, form))

    # ---- corners
    cm = lg.corner_mean(hk, ak) if lg.has_corners else None
    exp_c = None
    if cm:
        exp_c = round(cm, 1)
        pmf = nb_pmf(cm, lg.phi)
        cdf = np.cumsum(pmf)
        small = lg.cw.get(hk, 0) < 4 or lg.cw.get(ak, 0) < 4
        for key, line in (("c85", 8.5), ("c95", 9.5), ("c105", 10.5)):
            p_over = float(1 - cdf[int(line)])
            hc_, nh = rate(hr, lambda m: None if m["hc"] is None else (1 if m["hc"] + m["ac"] > line else 0))
            ac_, na2 = rate(ar, lambda m: None if m["hc"] is None else (1 if m["hc"] + m["ac"] > line else 0))
            form = "over %.1f in %d/%d (%s) and %d/%d (%s) recent games" % (line, hc_, nh, hnm, ac_, na2, anm)
            for side, p in (("over", p_over), ("under", 1 - p_over)):
                why = "Model expects %.1f corners; %s" % (cm, form)
                n0 = len(picks)
                add(key, side, "%s %.1f corners" % (side.title(), line), p, None, None, None, None, why)
                if small and len(picks) > n0 and picks[-1]["tier"] in ("A", "B"):
                    picks[-1]["tier"] = "C"; picks[-1]["reason"] += " (small sample, capped at C)"
    return dict(id="%s-%s-%s-%s" % (code, ko.strftime("%Y%m%d"), slug(home), slug(away)),
                league=code, kickoff=ko.isoformat(timespec="minutes"), home=home, away=away,
                xg=[round(lh, 2), round(la, 2)], exp_corners=exp_c, mkt=mkt_used,
                low_sample=low_sample, picks=picks)

def awayn(a, dc=False):
    return (a + " or draw") if dc else (a + " to win")

def slug(s):
    return "".join(c.lower() if c.isalnum() else "" for c in s)[:14]

if __name__ == "__main__":
    main()
