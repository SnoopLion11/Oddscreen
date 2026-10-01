"""De-vig and edge math for the odds screen.

Hot-path code: every function here runs on each price update, so the
solvers use safeguarded Newton (Newton steps kept inside a shrinking
bisection bracket) with a fixed iteration cap. Per-call cost is a few
microseconds for 2- and 3-way markets.

Probabilities are floats in (0, 1). Prices on a platform are the ASK for
each side (price to buy), never last-trade or midpoint.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

METHODS = ("multiplicative", "additive", "power", "shin")

_MAX_ITER = 100   # safeguarded Newton converges in < 10 steps in practice
_TOL = 1e-15      # target |sum(fair) - 1|
_TIE = 1e-12      # results closer than this are treated as equal (earlier method wins)


# --- price -> implied probability -------------------------------------------

def implied_from_cents(cents: float) -> float:
    return cents / 100.0


def implied_from_american(american: float) -> float:
    if american > 0:
        return 100.0 / (american + 100.0)
    return -american / (-american + 100.0)


def valid_price(p: float) -> bool:
    """A buyable ask: strictly between 0 and 1 (0c < ask < 100c)."""
    return 0.0 < p < 1.0


def american_from_prob(p: float) -> float:
    """Display helper only. Never feeds back into a calculation."""
    if p >= 0.5:
        return -100.0 * p / (1.0 - p)
    return 100.0 * (1.0 - p) / p


# --- the four de-vig methods ------------------------------------------------
# Each takes the implied probabilities of ALL sides of one platform's market
# and returns fair probabilities for all sides, or None if the method has no
# valid solution for these inputs.

def multiplicative(p: Sequence[float]) -> list[float]:
    s = sum(p)
    return [x / s for x in p]


def additive(p: Sequence[float]) -> list[float]:
    s = sum(p)
    d = (s - 1.0) / len(p)
    return [x - d for x in p]


def power(p: Sequence[float]) -> Optional[list[float]]:
    """Solve k so that sum(p_i ** k) == 1; fair_i = p_i ** k."""
    logs = [math.log(x) for x in p]
    # g(k) = sum(p^k) - 1 is strictly decreasing and convex in k.
    lo, hi = 1e-6, 50.0
    k = 1.0
    for _ in range(_MAX_ITER):
        pk = [math.exp(k * l) for l in logs]
        g = sum(pk) - 1.0
        if abs(g) <= _TOL:
            break
        if g > 0:
            lo = k
        else:
            hi = k
        dg = sum(v * l for v, l in zip(pk, logs))
        nk = k - g / dg if dg != 0 else lo
        k = nk if lo < nk < hi else 0.5 * (lo + hi)
        if hi - lo <= 1e-15 * hi:
            break
    return [math.exp(k * l) for l in logs]


def shin(p: Sequence[float]) -> Optional[list[float]]:
    """Solve z so that sum(fair_i) == 1 with
    fair_i = (sqrt(z^2 + 4(1-z) p_i^2 / S) - z) / (2(1-z))."""
    s = sum(p)
    if s <= 1.0:
        # Shin's insider share z is only defined for an overround book.
        return list(p) if abs(s - 1.0) <= _TOL else None
    a = [4.0 * x * x / s for x in p]

    def fair_and_slope(z: float) -> tuple[float, float]:
        one_m = 1.0 - z
        den = 2.0 * one_m
        tot = 0.0
        dtot = 0.0
        for ai in a:
            r = math.sqrt(z * z + ai * one_m)
            num = r - z
            dnum = (2.0 * z - ai) / (2.0 * r) - 1.0
            tot += num / den
            dtot += (dnum * den + 2.0 * num) / (den * den)
        return tot, dtot

    # sum(fair) is decreasing in z on [0, 1): sqrt(S) > 1 at z = 0.
    lo, hi = 0.0, 0.999
    z = 0.0
    for _ in range(_MAX_ITER):
        tot, dtot = fair_and_slope(z)
        g = tot - 1.0
        if abs(g) <= _TOL:
            break
        if g > 0:
            lo = z
        else:
            hi = z
        nz = z - g / dtot if dtot != 0 else lo
        z = nz if lo < nz < hi else 0.5 * (lo + hi)
        if hi - lo <= 1e-16:
            break
    one_m = 1.0 - z
    return [(math.sqrt(z * z + ai * one_m) - z) / (2.0 * one_m) for ai in a]


_FUNCS = (multiplicative, additive, power, shin)


# --- worst case per platform ------------------------------------------------

@dataclass(frozen=True, slots=True)
class SideFair:
    fair: float                       # lowest valid method result
    method: str                       # which method set it
    by_method: dict[str, float]       # every valid method result (for hover)


def devig_market(p: Sequence[float]) -> Optional[list[SideFair]]:
    """De-vig ONE platform's market (its own prices for every side only).

    Returns the worst-case (lowest) fair probability for each side, or None
    when the inputs cannot be de-vigged (missing side, non-positive price).
    Any method result <= 0 is discarded for that side. Feeds must drop
    unbuyable quotes (see valid_price) before calling this.
    """
    if len(p) < 2 or any(not (x > 0.0) for x in p):
        return None
    results = [(name, fn(p)) for name, fn in zip(METHODS, _FUNCS)]
    out: list[SideFair] = []
    for i in range(len(p)):
        vals = {name: r[i] for name, r in results if r is not None and r[i] > 0.0}
        if not vals:
            return None
        best_name, best = None, math.inf
        for name in METHODS:                     # fixed order makes ties deterministic
            v = vals.get(name)
            if v is not None and v < best - _TIE:
                best_name, best = name, v
        out.append(SideFair(best, best_name, vals))
    return out


# --- combining platforms ----------------------------------------------------

def combine_fair(platform_fairs: Sequence[float], strict: bool = False) -> Optional[float]:
    """Combine each LIVE reference platform's worst-case fair for one side.

    Average (default) or Strict (lowest platform). Platforms are never mixed
    before de-vigging; this only combines already de-vigged results.
    """
    if not platform_fairs:
        return None
    if strict:
        return min(platform_fairs)
    return sum(platform_fairs) / len(platform_fairs)


# --- edge at ESX ------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FeeModel:
    """ESX fee. Exactly one mode applies; values are placeholders until the
    ESX fee schedule is confirmed from its docs.

    flat_cents:    fee per contract in cents, charged on purchase.
    pct_winnings:  fraction of net winnings charged on a winning contract.
    """
    flat_cents: float = 0.0
    pct_winnings: float = 0.0


@dataclass(frozen=True, slots=True)
class Edge:
    cents: float      # expected profit per $1 contract, in cents, after fees
    ev_pct: float     # expected return on money paid, in %


def edge(fair: float, esx_ask: float, fee: FeeModel) -> Edge:
    """Edge of buying one ESX contract at `esx_ask` (probability units).

    Flat fee (brief section 6/7):
        cost      = ask + fee
        cents     = (fair - ask) * 100 - fee_cents
        EV %      = (fair / cost - 1) * 100
    % of winnings: the fee is only paid when the contract wins, on the
    profit (1 - ask), so
        EV/contract = fair * (1 - ask) * (1 - f) - (1 - fair) * ask
        EV %        = EV / ask * 100
    """
    if fee.pct_winnings:
        ev = fair * (1.0 - esx_ask) * (1.0 - fee.pct_winnings) - (1.0 - fair) * esx_ask
        return Edge(ev * 100.0, ev / esx_ask * 100.0)
    fee_p = fee.flat_cents / 100.0
    cost = esx_ask + fee_p
    return Edge((fair - esx_ask) * 100.0 - fee.flat_cents, (fair / cost - 1.0) * 100.0)


def ev_pct(fair: float, cost: float) -> float:
    return (fair / cost - 1.0) * 100.0
