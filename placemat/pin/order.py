"""Ordering the hot set (DESIGN §6.3 step 3, §6.4 / P009) and writing order files.

``order`` is C3-style (Ottoni & Maher 2017, as in Amber's prototype): functions in decreasing
hotness are each appended to the cluster of their heaviest caller, unless the merged cluster
would exceed ``cluster_limit`` bytes or the caller's cluster is under 1/``density_ratio`` as dense
as the callee's; clusters are then sorted by density (hotness per byte).

``strategies`` maps a name to a function (binary, profile, selection, **options) -> function list,
for comparing orderings (P009): "c3", "hotness", "density", "pettis_hansen", "random" (a
control; option ``seed``).

Order files: ``write_order(path, names, style)`` with style 'ld64' (``-Wl,-order_file``: one
``_name`` per line), 'lld' (``--symbol-ordering-file``: one name per line), or 'gnu' (GNU ld
``--section-ordering-file``: a section-mapping fragment over the ``-ffunction-sections`` input
sections ``.text.name``).
"""
from __future__ import annotations

import random as _random
from pathlib import Path

from .. import binary as B
from .profile import hotness
from .select import Selection


def _sel(selection) -> list[str]:
    return list(selection.sel if isinstance(selection, Selection) else selection)


def _sizes(binary) -> dict[str, int]:
    return {f.name: f.size for f in B.functions(binary)}


def _edges(profile: dict, h: dict) -> dict[tuple[str, str], float]:
    E: dict[tuple[str, str], float] = {}
    for s in profile["edges"]:
        for k, v in profile["edges"][s].items():
            p = k.split(">")
            if len(p) == 2 and p[0] in h and p[1] in h and p[0] != p[1]:
                E[(p[0], p[1])] = E.get((p[0], p[1]), 0) + v
    return E


def c3_clusters(binary, profile: dict, selection, cluster_limit: int = 16384,
                density_ratio: float = 8.0) -> list[list[str]]:
    """C3 clusters, densest first. Selected functions missing from the binary are left out."""
    sel = _sel(selection)
    size = _sizes(binary)
    h = hotness(profile, names=sel)
    E = _edges(profile, h)
    fs = [f for f in sel if f in size]
    cl = {f: [f] for f in fs}
    of = {f: f for f in fs}

    def dens(c):
        return sum(h[x] for x in c) / max(1, sum(size[x] for x in c))
    for f in sorted(fs, key=lambda f: -h[f]):
        callers = sorted(((v, a) for (a, c), v in E.items() if c == f and a in of), reverse=True)
        if not callers:
            continue
        _, a = callers[0]
        ca, cf = of[a], of[f]
        if ca == cf:
            continue
        A, Bc = cl[ca], cl[cf]
        if sum(size[x] for x in A + Bc) > cluster_limit:
            continue
        if dens(A) < dens(Bc) / density_ratio:
            continue
        cl[ca] = A + Bc
        del cl[cf]
        for x in Bc:
            of[x] = ca
    return sorted(cl.values(), key=lambda c: -dens(c))


def order(binary, profile: dict, selection, cluster_limit: int = 16384) -> list[str]:
    """C3-style order of the selected functions (see the module docstring)."""
    return [f for c in c3_clusters(binary, profile, selection, cluster_limit) for f in c]


def by_hotness(binary, profile, selection, **_) -> list[str]:
    sel = [f for f in _sel(selection) if f in _sizes(binary)]
    h = hotness(profile, names=sel)
    return sorted(sel, key=lambda f: (-h[f], f))


def by_density(binary, profile, selection, **_) -> list[str]:
    size = _sizes(binary)
    sel = [f for f in _sel(selection) if f in size]
    h = hotness(profile, names=sel)
    return sorted(sel, key=lambda f: (-h[f] / max(1, size[f]), f))


def random_order(binary, profile, selection, seed: int = 0, **_) -> list[str]:
    sel = [f for f in _sel(selection) if f in _sizes(binary)]
    _random.Random(seed).shuffle(sel)
    return sel


def pettis_hansen(binary, profile, selection, **_) -> list[str]:
    """Pettis & Hansen (1990) function ordering: undirected call weights; repeatedly join the two
    chains linked by the heaviest remaining edge, in the orientation that puts that edge's two
    functions closest (in bytes); chains are then laid out by density."""
    size = _sizes(binary)
    sel = [f for f in _sel(selection) if f in size]
    h = hotness(profile, names=sel)
    w: dict[tuple[str, str], float] = {}
    for (a, c), v in _edges(profile, h).items():
        k = (a, c) if a < c else (c, a)
        w[k] = w.get(k, 0) + v
    chain = {f: [f] for f in sel}
    of = {f: f for f in sel}

    def dist(ch, u, v):
        i, j = sorted((ch.index(u), ch.index(v)))
        return sum(size[x] for x in ch[i + 1:j])
    for (u, v), _ in sorted(w.items(), key=lambda kv: (-kv[1], kv[0])):
        cu, cv = of[u], of[v]
        if cu == cv:
            continue
        A, C = chain[cu], chain[cv]
        best = min((A + C, A + C[::-1], A[::-1] + C, A[::-1] + C[::-1]), key=lambda ch: dist(ch, u, v))
        chain[cu] = best
        del chain[cv]
        for x in C:
            of[x] = cu

    def dens(c):
        return sum(h[x] for x in c) / max(1, sum(size[x] for x in c))
    return [f for c in sorted(chain.values(), key=lambda c: (-dens(c), c[0])) for f in c]


strategies = {
    "c3": lambda binary, profile, selection, cluster_limit=16384, **_: order(binary, profile, selection, cluster_limit),
    "hotness": by_hotness,
    "density": by_density,
    "pettis_hansen": pettis_hansen,
    "random": random_order,
}


GNU_PREFIXES = ("", "hot.", "unlikely.", "startup.", "exit.")


def order_text(names, style: str) -> str:
    """The text of an order file for ``names`` in ``style`` 'ld64', 'lld' or 'gnu'."""
    style = B._style(style)
    if style == "gnu":
        lines = [".text : {"]
        for n in names:
            lines.append("  *(" + " ".join(f".text.{p}{n}" for p in GNU_PREFIXES) + ")")
        lines.append("}")
        return "\n".join(lines) + "\n"
    return "".join(B.link_name(style, n) + "\n" for n in names)


def write_order(path, names, style: str) -> None:
    Path(path).write_text(order_text(names, style))
