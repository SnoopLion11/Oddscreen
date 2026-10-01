import ast
import math
import random
import time
from pathlib import Path

import pytest

from oddscreen import devig, selftest

ROOT = Path(__file__).resolve().parent.parent


def _reference():
    """Load only the reference functions from verify_devig.py (not its tests)."""
    src = (ROOT / "verify_devig.py").read_text()
    tree = ast.parse(src)
    tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
    ns: dict = {}
    exec(compile(tree, "verify_devig.py", "exec"), ns)
    return ns


REF = _reference()


def _random_market(rng, n):
    q = [rng.random() + 0.05 for _ in range(n)]
    t = sum(q)
    margin = rng.uniform(0.0, 0.08)
    return [x / t * (1 + margin) for x in q]


def test_verify_devig_suite_passes_against_production():
    fails, out = selftest.run()
    assert fails == 0, out
    assert "FAILURES: 0" in out


@pytest.mark.parametrize("prod,ref", [
    (devig.multiplicative, "mult"),
    (devig.additive, "additive"),
    (devig.power, "power"),
    (devig.shin, "shin_m"),
])
def test_production_matches_reference(prod, ref):
    rng = random.Random(7)
    for _ in range(3000):
        p = _random_market(rng, rng.choice([2, 3]))
        if max(p) >= 0.99:
            continue
        a, b = prod(p), REF[ref](p)
        assert max(abs(x - y) for x, y in zip(a, b)) < 1e-12


def test_worst_case_method_matches_reference():
    rng = random.Random(11)
    for _ in range(3000):
        p = _random_market(rng, 2)
        for i in range(2):
            ref_val, _, ref_vals = REF["worst"](p, i)
            side = devig.devig_market(p)[i]
            assert abs(side.fair - ref_val) < 1e-12
            # Shin == Additive on 2-way markets, so their labels can tie;
            # otherwise the method must agree with the reference.
            if side.method not in ("additive", "shin"):
                assert ref_vals[side.method] <= min(ref_vals.values()) + 1e-12


def test_nonpositive_results_discarded():
    p = [0.88, 0.18, 0.015]  # additive goes negative on the longshot
    assert devig.additive(p)[2] < 0
    side = devig.devig_market(p)[2]
    assert "additive" not in side.by_method
    assert side.fair > 0


def test_invalid_inputs_rejected():
    assert devig.devig_market([0.5]) is None
    assert devig.devig_market([0.0, 0.99]) is None
    assert not devig.valid_price(1.0) and not devig.valid_price(0.0)
    assert devig.valid_price(0.01) and devig.valid_price(0.99)


def test_two_way_tie_prefers_earlier_method():
    p = [devig.implied_from_american(-300), devig.implied_from_american(240)]
    fav = devig.devig_market(p)[0]
    assert fav.method == "multiplicative"
    for side in devig.devig_market([0.55, 0.50]):
        assert side.method != "shin" or abs(side.by_method["additive"] - side.fair) > 1e-12


def test_conversions():
    assert devig.implied_from_cents(47) == pytest.approx(0.47)
    assert devig.implied_from_american(-110) == pytest.approx(110 / 210)
    assert devig.implied_from_american(125) == pytest.approx(100 / 225)
    assert devig.american_from_prob(devig.implied_from_american(-150)) == pytest.approx(-150)
    assert devig.american_from_prob(devig.implied_from_american(240)) == pytest.approx(240)


def test_combine_fair():
    assert devig.combine_fair([0.50, 0.54]) == pytest.approx(0.52)
    assert devig.combine_fair([0.50, 0.54], strict=True) == 0.50
    assert devig.combine_fair([]) is None


def test_edge_flat_fee_matches_brief():
    # ESX +300 (25c) with 1c fee, fair 26.7%: verify_devig.py quotes EV +2.7%
    p = [devig.implied_from_american(-300), devig.implied_from_american(240)]
    fair = devig.devig_market(p)[1].fair
    e = devig.edge(fair, 0.25, devig.FeeModel(flat_cents=1))
    assert round(e.ev_pct, 1) == 2.7
    assert e.cents == pytest.approx((fair - 0.25) * 100 - 1)


def test_edge_pct_of_winnings():
    # Fair 50%, buy at 40c, 10% of winnings: EV = .5*.6*.9 - .5*.4 = 0.07
    e = devig.edge(0.5, 0.40, devig.FeeModel(pct_winnings=0.10))
    assert e.cents == pytest.approx(7.0)
    assert e.ev_pct == pytest.approx(0.07 / 0.40 * 100)


def test_per_update_cost_is_microseconds():
    rng = random.Random(3)
    markets = [_random_market(rng, 2) for _ in range(2000)]
    t0 = time.perf_counter()
    for p in markets:
        devig.devig_market(p)
    per_call_us = (time.perf_counter() - t0) / len(markets) * 1e6
    assert per_call_us < 100, f"{per_call_us:.1f} us per market"
