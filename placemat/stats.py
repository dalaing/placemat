"""Estimators, intervals, permutation tests and false-discovery control (DESIGN §5.7).

Extracted from the Amber fork's pbt/ldesign.py and pbt/layouts.py, with Student's t quantiles computed
rather than tabulated (placemat needs 99.75% quantiles for the two data-dependence comparisons).
"""
from __future__ import annotations

import math
import random
import statistics

# ---- Student's t -------------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularised incomplete beta function (Lentz's method)."""
    tiny, eps = 1e-300, 3e-16
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """The regularised incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1 - x) / b


def t_cdf(t: float, df: float) -> float:
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    x = df / (df + t * t)
    p = 0.5 * betainc(df / 2, 0.5, x)
    return 1 - p if t > 0 else p


def t_sf2(t: float, df: float) -> float:
    """Two-sided p-value of |T| >= |t|."""
    return betainc(df / 2, 0.5, df / (df + t * t)) if df > 0 else 1.0


def f_sf(f: float, d1: float, d2: float) -> float:
    """P(F >= f) for Fisher's F with (d1, d2) degrees of freedom."""
    if f <= 0 or d1 <= 0 or d2 <= 0:
        return 1.0
    return betainc(d2 / 2, d1 / 2, d2 / (d2 + d1 * f))


_TQ: dict[tuple[int, float], float] = {}


def tq(df: int, q: float = 0.975) -> float:
    """The q quantile of Student's t with df degrees of freedom (q > 0.5)."""
    key = (df, q)
    if key in _TQ:
        return _TQ[key]
    if df < 1:
        return math.inf
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < q:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    _TQ[key] = (lo + hi) / 2
    return _TQ[key]


# ---- intervals ---------------------------------------------------------------------------------


def tci(xs: list[float], q: float = 0.975) -> tuple[float, float, float, float]:
    """(exp mean, lo, hi, half-width in logs): a t interval over log values xs (m - 1 df)."""
    mu = statistics.fmean(xs)
    h = tq(len(xs) - 1, q) * math.sqrt(statistics.variance(xs) / len(xs)) if len(xs) > 1 else math.inf
    return math.exp(mu), math.exp(mu - h), math.exp(mu + h), h


def paired_t(ds: list[float], alpha: float) -> tuple[float, float, bool]:
    """(mean, two-sided p, significant at alpha) of a one-sample t test that ds centre on zero."""
    n = len(ds)
    if n < 2:
        return (ds[0] if ds else 0.0), 1.0, False
    mu = statistics.fmean(ds)
    sd = statistics.stdev(ds)
    if sd == 0:
        return mu, (0.0 if mu else 1.0), bool(mu)
    t = mu / (sd / math.sqrt(n))
    p = t_sf2(t, n - 1)
    return mu, p, p < alpha


def bootstrap_p(ratios: list[float], n: int = 4000, seed: int = 1) -> float:
    """Two-sided bootstrap p-value that the median paired ratio is 1."""
    rnd = random.Random(seed)
    bs = [statistics.median(rnd.choices(ratios, k=len(ratios))) for _ in range(n)]
    below = sum(1 for b in bs if b <= 1) / n
    above = sum(1 for b in bs if b >= 1) / n
    return min(1.0, 2 * min(below, above) + 1 / n)


def bootstrap_median_ratio(ratios: list[float], n: int = 4000, seed: int = 1) -> tuple[float, float, float]:
    """The median of paired ratios and a percentile bootstrap 95% interval over rounds (the stock
    builds' own paired result, as the Amber stage computed it)."""
    rnd = random.Random(seed)
    est = statistics.median(ratios)
    bs = sorted(statistics.median(rnd.choices(ratios, k=len(ratios))) for _ in range(n))
    return est, bs[int(0.025 * n)], bs[int(0.975 * n)]


# ---- permutation tests -------------------------------------------------------------------------


def _t2(ds: list[float]) -> float:
    n = len(ds)
    mu = sum(ds) / n
    v = sum((x - mu) ** 2 for x in ds) / (n - 1) if n > 1 else 0.0
    return mu * mu / (v / n + 1e-12 * (mu * mu + 1e-12))


def signflip(contrasts: list[list[float]], rnd: random.Random, n: int = 20000) -> float:
    """p-value of a sign-flip permutation test that the per-round log differences of every contrast
    centre on zero (labels swapped within rounds); statistic the sum over contrasts of squared t."""
    contrasts = [c for c in contrasts if len(c) >= 3]
    if not contrasts:
        return 1.0
    obs = sum(_t2(c) for c in contrasts)
    tables = []
    for c in contrasts:
        r = len(c)
        if r <= 12:
            tables.append([_t2([x if (s >> j) & 1 else -x for j, x in enumerate(c)]) for s in range(1 << r)])
        else:
            tables.append([_t2([x if rnd.random() < .5 else -x for x in c]) for _ in range(4096)])
    tot = [0.0] * n
    for t in tables:
        for i, v in enumerate(rnd.choices(t, k=n)):
            tot[i] += v
    return (1 + sum(1 for x in tot if x >= obs * (1 - 1e-9))) / (n + 1)


def tmean(xs) -> float:
    """The mean without the smallest and largest value (with 5 or more): robust to one outlier, and
    unlike the median it does not saturate under permutation."""
    xs = sorted(xs)
    if len(xs) >= 5:
        xs = xs[1:-1]
    return sum(xs) / len(xs)


def spread_perm(blocks: list[list[list[float]]], rnd: random.Random, n: int = 2000) -> float:
    """p-value of the spread of variant trimmed means when variant labels are permuted within rounds.
    blocks: per batch, rows (one per round) of that batch's variants' log times. The statistic is the
    within-batch sum of squares, so drift between rounds and between batches does not count. Ten
    times the permutations when p < 0.01."""
    blocks = [[list(r) for r in b] for b in blocks if len(b) >= 3 and len(b[0]) >= 2]
    if not blocks:
        return 1.0

    def stat():
        s = 0.0
        for b in blocks:
            m = [tmean(c) for c in zip(*b)]
            s += statistics.pvariance(m) * len(m)
        return s
    obs = stat()
    hits = done = 0
    for target in (n, 10 * n):
        while done < target:
            for b in blocks:
                for r in b:
                    rnd.shuffle(r)
            hits += stat() >= obs * (1 - 1e-9)
            done += 1
        if (hits + 1) / (done + 1) >= 0.01:
            break
    return (hits + 1) / (done + 1)


def rank_perm(blocks: list[list[list[float]]], rnd: random.Random, n: int = 2000) -> float:
    """A Friedman-type permutation test of the code spread, robust to disturbed rounds: within each
    round the variants are ranked, the statistic is the sum over batches of the squared deviations of
    each variant's rank sum from its expectation, and labels are permuted within rounds. A round that
    ran slow throughout changes no rank (the Lua survey: this found a 2-5% code effect that the
    trimmed-mean test missed). blocks: per batch, rows (one per round) of the variants' log times."""
    blocks = [[list(r) for r in b] for b in blocks if len(b) >= 3 and len(b[0]) >= 2]
    if not blocks:
        return 1.0

    def ranks(row):
        order = sorted(range(len(row)), key=lambda i: row[i])
        rk = [0.0] * len(row)
        for pos, i in enumerate(order):
            rk[i] = float(pos)
        return rk
    rblocks = [[ranks(r) for r in b] for b in blocks]

    def stat(rb):
        tot = 0.0
        for b in rb:
            k = len(b[0])
            sums = [sum(r[i] for r in b) for i in range(k)]
            mean = len(b) * (k - 1) / 2
            tot += sum((x - mean) ** 2 for x in sums)
        return tot
    obs = stat(rblocks)
    hits = done = 0
    for target in (n, 10 * n):
        while done < target:
            for b in rblocks:
                for r in b:
                    rnd.shuffle(r)
            hits += stat(rblocks) >= obs * (1 - 1e-9)
            done += 1
        if (hits + 1) / (done + 1) >= 0.01:
            break
    return (hits + 1) / (done + 1)


def _group_z(gc):
    """(estimate, z) of the within-variant difference mean(category 1) - mean(category 0) over the
    variants of one group: [(log times, 0/1 labels)], each variant's difference weighted n1*n0/n."""
    num = den = var = 0.0
    for x, c in gc:
        n1 = sum(c)
        n0 = len(c) - n1
        if not n1 or not n0:
            continue
        w = n1 * n0 / len(c)
        a1 = [v for v, k in zip(x, c) if k]
        a0 = [v for v, k in zip(x, c) if not k]
        m1, m0 = sum(a1) / n1, sum(a0) / n0
        ss = sum((v - m1) ** 2 for v in a1) + sum((v - m0) ** 2 for v in a0)
        s2 = ss / (len(c) - 2) if len(c) > 2 else 0.0
        num += w * (m1 - m0)
        den += w
        var += w * w * s2 * (1 / n1 + 1 / n0)
    if not den:
        return 0.0, 0.0
    est = num / den
    return est, est / math.sqrt(var / den ** 2 + 1e-12 * est * est + 1e-18)


def run_test(groups: dict, rnd: random.Random, n: int = 2000) -> tuple[dict, float]:
    """The run-level covariate. groups = {data setting: [(log times, 0/1 category per run) per
    variant]}. Per setting, the within-variant difference between the categories ({setting:
    (estimate, z)}); one p-value for all settings (statistic the largest |z|, labels permuted within
    variants: a max-T test). Ten times the permutations when p < 0.01."""
    gs = {c: [(x, l) for x, l in g if 0 < sum(l) < len(l)] for c, g in groups.items()}
    gs = {c: g for c, g in gs.items() if g}
    if not gs:
        return {}, 1.0
    est = {c: _group_z(g) for c, g in gs.items()}
    obs = max(abs(z) for _, z in est.values())
    hits = done = 0
    for target in (n, 10 * n):
        while done < target:
            m = max(abs(_group_z([(x, rnd.sample(l, len(l))) for x, l in g])[1]) for g in gs.values())
            hits += m >= obs * (1 - 1e-9)
            done += 1
        if (hits + 1) / (done + 1) >= 0.01:
            break
    return est, (hits + 1) / (done + 1)


def bh(ps: dict, q: float) -> set:
    """Benjamini-Hochberg: the keys whose p-values are significant at false-discovery rate q."""
    items = sorted(ps.items(), key=lambda kv: kv[1])
    m, cut = len(items), 0
    for i, (_, p) in enumerate(items, 1):
        if p <= q * i / m:
            cut = i
    return {k for k, _ in items[:cut]}


def strat_ratio(a, b, fallback):
    """The log change b/a of one variant, post-stratified by the run-level category: a and b are
    [(value, category)] (category a bool: the covariate at its mode or not). Per category, the
    difference of the medians of the log times, weighted by the smaller count. It uses only the
    variant's own runs, so the variants stay independent (a pooled adjustment spreads its error
    across variants and narrows the t interval falsely)."""
    cats: dict = {}
    for i, g in ((0, a), (1, b)):
        for v, cat in g:
            cats.setdefault(cat, ([], []))[i].append(math.log(v))
    ds = [(statistics.median(lb) - statistics.median(la), min(len(la), len(lb)))
          for la, lb in cats.values() if la and lb]
    if not ds:
        return fallback
    return sum(d * w for d, w in ds) / sum(w for _, w in ds)


def slope(pts: list[tuple[float, float]]) -> float:
    """Least-squares slope of time against iterations."""
    mr = statistics.fmean(r for r, _ in pts)
    mt = statistics.fmean(t for _, t in pts)
    den = sum((r - mr) ** 2 for r, _ in pts)
    return sum((r - mr) * (t - mt) for r, t in pts) / den if den else 0.0


def geomean_ci(changes: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """Geometric mean of (estimate, lo, hi) ratio intervals, with a normal-approximation interval
    from each interval's width (the summary the Amber reports quoted over many cases)."""
    lg = [math.log(e) for e, _, _ in changes]
    se = [(math.log(h) - math.log(l)) / 3.92 for _, l, h in changes]
    g = sum(lg) / len(lg)
    s = math.sqrt(sum(x * x for x in se)) / len(se)
    return math.exp(g), math.exp(g - 1.96 * s), math.exp(g + 1.96 * s)
