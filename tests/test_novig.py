import pytest

from oddscreen import combos, novig

START = 1793840400.0  # a game start, unix seconds


def mkt(desc, strike, mtype="PLAYER_POINTS", over=0.45, under=0.50, starts=START, status="OPEN",
        names=("Over", "Under")):
    return {"marketId": desc, "description": desc, "marketType": mtype, "strike": str(strike),
            "status": status, "startsTs": int(starts * 1000),
            "outcomes": [{"outcomeId": "o", "name": names[0], "bid": {"price": str(over), "qty": 50}},
                         {"outcomeId": "u", "name": names[1], "bid": {"price": str(under), "qty": 40}}]}


def test_norm_name():
    assert novig.norm_name("Luka Dončić") == "luka doncic"
    assert novig.norm_name("Jaren Jackson Jr.") == "jaren jackson"
    assert novig.norm_name("De'Aaron Fox") == "deaaron fox"


def test_stats_in():
    assert novig.stats_in("PLAYER_POINTS") == {"PTS"}
    assert novig.stats_in("Points + Rebounds + Assists") == {"PTS", "REB", "AST"}
    assert novig.stats_in("PLAYER_THREE_POINTERS_MADE") == {"3PT"}
    assert novig.stats_in("3-Pointers Made") == {"3PT"}
    assert novig.stats_in("Rebounds") == {"REB"}


def test_kalshi_prop():
    assert novig.kalshi_prop("KXNBAPTS-26NOV04MIALAC-LACIJACKSON23-20", "Isaiah Jackson: 20+ points", 19.5) == \
        ("isaiah jackson", frozenset({"PTS"}), 19.5)
    assert novig.kalshi_prop("KXNBAPTSPROT-26NOV04X-Y", "A B: 15+ points", 14.5) is None
    assert novig.kalshi_prop("KXNBASPREAD-26NOV04X-Y", "Team wins by 5+", 4.5) is None


def test_prop_price_asks_cross_other_side():
    p = novig.prop_price(mkt("Isaiah Jackson Points", 19.5, over=0.45, under=0.50))
    assert p.over_ask == pytest.approx(0.50)   # 1 - under bid
    assert p.under_ask == pytest.approx(0.55)  # 1 - over bid
    assert (p.over_qty, p.under_qty) == (40, 50)


def test_prop_price_needs_both_bids():
    m = mkt("x", 19.5)
    del m["outcomes"][1]["bid"]
    assert novig.prop_price(m) is None


def test_match_player_stat_line_and_time():
    ms = [mkt("Isaiah Jackson Points", 19.5), mkt("Isaiah Jackson Points", 20.5),
          mkt("Isaiah Jackson Rebounds", 19.5, "PLAYER_REBOUNDS"),
          mkt("Isaiah Jackson Points + Rebounds + Assists", 19.5, "PLAYER_PRA"),
          mkt("Isaiah Jackson Points", 19.5, starts=START + 86400)]
    hit = novig.match(ms, "isaiah jackson", frozenset({"PTS"}), 19.5, START)
    assert hit is ms[0]
    assert novig.match(ms, "isaiah jackson", frozenset({"PTS", "REB", "AST"}), 19.5, START) is ms[3]
    assert novig.match(ms, "kris dunn", frozenset({"PTS"}), 19.5, START) is None


def test_match_ambiguous_is_none():
    ms = [mkt("Isaiah Jackson Points", 19.5), mkt("Isaiah Jackson Points alt", 19.5)]
    assert novig.match(ms, "isaiah jackson", frozenset({"PTS"}), 19.5, START) is None


def test_match_skips_closed():
    ms = [mkt("Isaiah Jackson Points", 19.5, status="CLOSED")]
    assert novig.match(ms, "isaiah jackson", frozenset({"PTS"}), 19.5, START) is None


def test_client_refuses_non_public_paths():
    c = novig.Client()
    for path in ("/v3/orders", "/v3/catalog/markets", "/v3/account/balance"):
        with pytest.raises(PermissionError):
            c.get(path)


class FakeClient:
    def __init__(self, markets=None, fail=False):
        self._m, self.fail, self.calls = markets or [], fail, 0

    def markets(self, league):
        self.calls += 1
        if self.fail:
            raise OSError("blocked")
        return self._m


def test_propbook_lookup_and_fair():
    book = novig.PropBook(FakeClient([mkt("Isaiah Jackson Points", 19.5, over=0.45, under=0.50)]))
    pp, m = book.lookup("KXNBAPTS-26NOV04MIALAC-LACIJACKSON23-20", "Isaiah Jackson: 20+ points", 19.5, START)
    f = combos.leg_fair("yes", pp.over_ask, pp.under_ask)
    assert 0.45 < f.fair < 0.50


def test_propbook_unreachable_falls_back():
    fc = FakeClient(fail=True)
    book = novig.PropBook(fc)
    assert book.lookup("KXNBAPTS-26NOV04MIALAC-LACIJACKSON23-20", "Isaiah Jackson: 20+ points", 19.5, START) is None
    assert book.error and book.lookup("KXNBAPTS-X-Y", "A B: 1+ points", 0.5, START) is None
    assert fc.calls == 1   # no retry storm once it failed
