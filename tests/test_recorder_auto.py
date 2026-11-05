import importlib.util
from pathlib import Path

from oddscreen import combos

_spec = importlib.util.spec_from_file_location(
    "kalshi_rfq_recorder", Path(__file__).resolve().parent.parent / "scripts" / "kalshi_rfq_recorder.py")
rec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rec)

NOW = rec._ts("2026-10-24T12:00:00Z")
EVENTS = [
    {"ticker": "KXNFLGAME-26OCT25PITNO", "is_yes_only": False, "size_max": 1, "active_quoters": []},
    {"ticker": "KXNFLTOTAL-26OCT25PITNO", "is_yes_only": False, "size_max": 1, "active_quoters": []},
    {"ticker": "KXNFLGAME-26NOV01BALBUF", "is_yes_only": False, "size_max": 1, "active_quoters": []},
]


def mkt(ticker, event, when):
    return {"ticker": ticker, "event_ticker": event, "expected_expiration_time": when,
            "close_time": "2026-11-20T00:00:00Z"}


MARKETS = [
    mkt("KXNFLGAME-26OCT25PITNO-PIT", "KXNFLGAME-26OCT25PITNO", "2026-10-25T20:00:00Z"),
    mkt("KXNFLTOTAL-26OCT25PITNO-44", "KXNFLTOTAL-26OCT25PITNO", "2026-10-25T20:00:00Z"),
    mkt("KXNFLGAME-26NOV01BALBUF-BUF", "KXNFLGAME-26NOV01BALBUF", "2026-11-01T21:00:00Z"),
    mkt("KXNFLGAME-26OCT26DALPHI-DAL", "KXNFLGAME-26OCT26DALPHI", "2026-10-27T03:00:00Z"),  # not in collection
]


def test_game_key_drops_team_suffix_on_props():
    assert combos.game_key("KXNFLTEAMFIRSTTD-26OCT25PITNO-PIT") == combos.game_key("KXNFLGAME-26OCT25PITNO")


def test_group_by_game_keeps_only_collection_events():
    g = rec.group_by_game(EVENTS, MARKETS, now=NOW)
    assert set(g) == {"26OCT25PITNO", "26NOV01BALBUF"}
    assert len(g["26OCT25PITNO"]) == 2


def test_within_hours_uses_expected_expiration_not_close_time():
    g = rec.group_by_game(EVENTS, MARKETS, within_hours=48, now=NOW)
    assert set(g) == {"26OCT25PITNO"}


class FakeClient:
    def __init__(self):
        self.calls = []

    def collection(self, t):
        return {"size_min": 2, "size_max": 0, "associated_events": EVENTS}

    def markets(self, **params):
        self.calls.append(params)
        return [m for m in MARKETS if m["ticker"].startswith(params["series_ticker"] + "-")]


def test_auto_combos_fetches_per_series_and_builds_cross_game_combos():
    c = FakeClient()
    out = rec.auto_combos(c, "COL", 3, 2, rec.random.Random(1), series=["KXNFLGAME", "KXNFLTOTAL"])
    assert c.calls == [{"series_ticker": "KXNFLGAME", "status": "open"},
                       {"series_ticker": "KXNFLTOTAL", "status": "open"}]
    assert len(out) == 3
    for _, legs in out:
        assert combos.independent(legs)



def test_quotes_are_filtered_to_our_own_rfqs():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from oddscreen import kalshi
    c = kalshi.Client(kalshi.Signer("k", ed25519.Ed25519PrivateKey.generate()))
    calls = []
    c.request = lambda method, path, params=None, body=None: calls.append((method, path, params)) or {"quotes": [{"id": "q1"}]}
    assert c.quotes("r1") == [{"id": "q1"}]
    assert calls == [("GET", "/communications/quotes", {"rfq_id": "r1", "rfq_user_filter": "self"})]


def test_zero_bid_means_side_not_quoted():
    raw = {"yes_bid_dollars": "0.0910", "no_bid_dollars": "0.0000"}
    assert rec.quote_bid(raw, "yes_bid") == 0.091
    assert rec.quote_bid(raw, "no_bid") is None


def test_same_game_puts_k_legs_in_one_game_from_different_events():
    g = rec.group_by_game(EVENTS, MARKETS, now=NOW)
    for seed in range(20):
        legs = rec.sample_legs(g, 3, 2, rec.random.Random(seed))
        keys = [combos.game_key(l.event_ticker) for l in legs]
        assert keys.count("26OCT25PITNO") == 2 and "26NOV01BALBUF" in keys
        assert len({l.event_ticker for l in legs}) == 3
        assert not combos.independent(legs)


def test_csv_log_path_skips_file_with_old_columns(tmp_path):
    (tmp_path / "combo_rfqs_20261024.csv").write_text("ts,old\n")
    assert rec.csv_log_path(tmp_path, "20261024").name == "combo_rfqs_20261024_2.csv"
    (tmp_path / "combo_rfqs_20261024_2.csv").write_text(",".join(rec.CSV_FIELDS) + "\n")
    assert rec.csv_log_path(tmp_path, "20261024").name == "combo_rfqs_20261024_2.csv"


def test_pregame_filter_uses_close_time_minus_48h():
    ms = [dict(m, close_time=c) for m, c in zip(MARKETS[:3], [
        "2026-10-26T12:10:00Z",     # starts 12:10 on the 24th: 10 min away
        "2026-10-26T13:00:00Z",     # starts in 1 h
        "2026-11-03T21:00:00Z"])]   # next week
    g = rec.group_by_game(EVENTS, ms, now=NOW, pregame_minutes=20)
    assert [m["ticker"] for _, m in g["26OCT25PITNO"]] == ["KXNFLTOTAL-26OCT25PITNO-44"]
    assert "26NOV01BALBUF" in g


def test_report_groups_by_how_many_legs_share_a_game():
    spec = importlib.util.spec_from_file_location(
        "rfq_report", Path(__file__).resolve().parent.parent / "scripts" / "rfq_report.py")
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)
    legs = [{"event_ticker": e} for e in ("KXNBAGAME-26OCT24MIACHA", "KXNBATOTAL-26OCT24MIACHA", "KXNBAPTS-26OCT24WASCHI")]
    assert rep.group_label(legs) == "3 legs, 2 from one game"
    assert rep.group_label(legs[:2]) == "2 legs, same game"
    assert rep.sport(legs) == "NBA"
    assert rep.costs([{"creator_id": "a", "yes_bid_dollars": "0.30", "no_bid_dollars": "0.0000"}]) == {"yes": {}, "no": {"a": 0.7}}


def test_expand_series_wildcard():
    evs = [{"ticker": "KXNBAPTS-26NOV02A"}, {"ticker": "KXNBAGAME-26NOV02A"}, {"ticker": "KXNFLGAME-26NOV02B"}]
    assert rec.expand_series(["KXNBA*", "KXNFLGAME"], evs) == ["KXNBAGAME", "KXNBAPTS", "KXNFLGAME"]


def test_alerts_flag_cheap_quoter_and_fair_edge():
    spec = importlib.util.spec_from_file_location(
        "rfq_report", Path(__file__).resolve().parent.parent / "scripts" / "rfq_report.py")
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)
    q = lambda c, nb: {"creator_id": c, "yes_bid_dollars": "0.0000", "no_bid_dollars": nb}
    rec_ = {"quotes": [q("a", "0.80"), q("b", "0.75"), q("c", "0.75"), q("d", "0.74")],
            "row": {"yes_ev_pct": "12.0", "yes_cost": "0.2", "yes_creator": "a", "no_ev_pct": "-3"}}
    kinds = {(a["kind"], a["side"]) for a in rep.alerts(rec_)}
    assert kinds == {("fair", "yes"), ("quoter", "yes")}      # 0.20 vs others 0.25: 5c, 20%
    rec_["quotes"][0] = q("a", "0.76")                          # 0.24 vs 0.25: 1c only
    rec_["row"]["yes_ev_pct"] = "2"
    assert rep.alerts(rec_) == []


def test_group_by_game_skips_paused_markets():
    ev = {"ticker": "KXNBAPTS-26NOV02WASORL"}
    ms = [{"ticker": "A", "event_ticker": ev["ticker"], "status": "inactive"},
          {"ticker": "B", "event_ticker": ev["ticker"], "status": "active"}]
    g = rec.group_by_game([ev], ms)
    assert [m["ticker"] for _, m in g["26NOV02WASORL"]] == ["B"]


class _FakeKalshi:
    def orderbook(self, t):
        return {"orderbook_fp": {"yes_dollars": [["0.40", "10"]], "no_dollars": [["0.55", "10"]]}}

    def market(self, t):
        return {"title": "Isaiah Jackson: 20+ points", "floor_strike": 19.5,
                "close_time": "2026-11-07T00:00:00Z"}


def test_price_legs_uses_novig_for_props():
    from oddscreen import novig
    start = rec._ts("2026-11-05T00:00:00Z")
    nm = {"marketId": "n1", "description": "Isaiah Jackson Points", "marketType": "PLAYER_POINTS",
          "strike": "19.5", "status": "OPEN", "startsTs": int(start * 1000),
          "outcomes": [{"name": "Over", "bid": {"price": "0.30", "qty": 5}},
                       {"name": "Under", "bid": {"price": "0.66", "qty": 5}}]}

    class FakeNovig:
        def markets(self, league):
            return [nm]

    prop = combos.Leg("KXNBAPTS-26NOV04MIALAC-LACIJACKSON23-20", "KXNBAPTS-26NOV04MIALAC", "yes")
    other = combos.Leg("KXNBASPREAD-26NOV04TORDEN-DEN5", "KXNBASPREAD-26NOV04TORDEN", "yes")
    fairs, books = rec.price_legs(_FakeKalshi(), [prop, other], 0.0, 0.06, novig.PropBook(FakeNovig()))
    assert [b["fair_src"] for b in books] == ["novig", "kalshi"]
    assert books[0]["novig"]["over_ask"] == 1 - 0.66
    assert 0.30 < fairs[0].fair < 0.34          # from Novig's 0.34 / 0.70, not Kalshi's 0.45 / 0.60
    kalshi_only, _ = rec.price_legs(_FakeKalshi(), [prop], 0.0, 0.06, None)
    assert kalshi_only[0].fair > 0.40


def test_novig_edge_legs_keeps_gaps_with_side():
    from oddscreen import novig
    start = rec._ts("2026-11-05T00:00:00Z")

    def nv(desc, strike, over_bid, under_bid):
        return {"marketId": desc, "description": desc, "marketType": "POINTS", "strike": str(strike),
                "status": "OPEN", "startsTs": int(start * 1000),
                "outcomes": [{"name": f"Over {strike}", "bid": {"price": str(over_bid), "qty": 5}},
                             {"name": f"Under {strike}", "bid": {"price": str(under_bid), "qty": 5}}]}

    class FakeNovig:
        def markets(self, league):
            return [nv("Ann Lee 19.5 POINTS", 19.5, 0.58, 0.40),     # Novig ~0.59 vs Kalshi ~0.50
                    nv("Bo Ray 9.5 POINTS", 9.5, 0.48, 0.50)]        # Novig ~0.49 vs Kalshi ~0.50

    def km(ticker, title, strike, ya, na):
        return {"ticker": ticker, "title": title, "floor_strike": strike, "close_time": "2026-11-07T00:00:00Z",
                "yes_ask_dollars": str(ya), "no_ask_dollars": str(na)}

    ev = {"ticker": "KXNBAPTS-26NOV04AAABBB"}
    by_game = {"26NOV04AAABBB": [(ev, km("KXNBAPTS-26NOV04AAABBB-AALEE1-20", "Ann Lee: 20+ points", 19.5, 0.52, 0.50)),
                                 (ev, km("KXNBAPTS-26NOV04AAABBB-BBRAY2-10", "Bo Ray: 10+ points", 9.5, 0.52, 0.50)),
                                 (ev, km("KXNBASPREAD-26NOV04AAABBB-AAA5", "AAA wins by 5+", 4.5, 0.5, 0.52))]}
    out = rec.novig_edge_legs(by_game, novig.PropBook(FakeNovig()), 0.02, 0.06)
    kept = [m for _, m in out["26NOV04AAABBB"]]
    assert [m["ticker"] for m in kept] == ["KXNBAPTS-26NOV04AAABBB-AALEE1-20"]
    assert kept[0]["_side"] == "yes" and kept[0]["_gap"] > 0.05
    leg = rec.sample_legs({"g": [(ev, kept[0])]}, 1, 1, __import__("random").Random(0))[0]
    assert leg.side == "yes"


def test_fair_alert_threshold_separate_from_quoter_pct():
    import importlib.util as u
    sp = u.spec_from_file_location("rfq_report", Path(__file__).resolve().parent.parent / "scripts" / "rfq_report.py")
    rr = u.module_from_spec(sp)
    sp.loader.exec_module(rr)
    rec_ = {"row": {"yes_ev_pct": 6.0, "yes_cost": 0.2, "yes_creator": "abc"}, "quotes": []}
    assert rr.alerts(rec_, 10.0) == []
    assert [a["kind"] for a in rr.alerts(rec_, 10.0, fair_pct=5.0)] == ["fair"]


def test_fair_sgp_alert_uses_leg_product():
    import importlib.util as u
    sp = u.spec_from_file_location("rfq_report", Path(__file__).resolve().parent.parent / "scripts" / "rfq_report.py")
    rr = u.module_from_spec(sp)
    sp.loader.exec_module(rr)
    rec_ = {"row": {"fair": "", "naive_fair": 0.30, "yes_cost": 0.25, "no_cost": 0.78, "contracts": 10}, "quotes": []}
    a = rr.alerts(rec_, 10.0, fair_pct=5.0)
    assert [(x["kind"], x["side"]) for x in a] == [("fair_sgp", "yes")]


def test_track_sgp_flags_tracked_quoter_near_leg_product():
    import importlib.util as u
    sp = u.spec_from_file_location("rfq_report", Path(__file__).resolve().parent.parent / "scripts" / "rfq_report.py")
    rr = u.module_from_spec(sp)
    sp.loader.exec_module(rr)
    legs = [{"market_ticker": "KXNBAPTS-26NOV04AAABBB-X-10", "event_ticker": "KXNBAPTS-26NOV04AAABBB", "side": "yes"},
            {"market_ticker": "KXNBATOTAL-26NOV04AAABBB-230", "event_ticker": "KXNBATOTAL-26NOV04AAABBB", "side": "no"}]
    q = lambda creator, no_bid: {"id": creator, "creator_id": creator, "yes_bid_dollars": "0.0000",
                                 "no_bid_dollars": f"{no_bid:.4f}"}
    rec_ = {"legs": legs, "row": {"fair": "", "naive_fair": 0.30},
            "quotes": [q("99516d7e-aaaa", 0.67), q("other-bbbb", 0.60)]}      # 99516d7e YES at 0.33 = 1.10x
    kinds = [a["kind"] for a in rr.alerts(rec_)]
    assert "track_sgp" in kinds
    rec_["quotes"][0] = q("99516d7e-aaaa", 0.60)                              # 0.40 = 1.33x: not tracked
    assert "track_sgp" not in [a["kind"] for a in rr.alerts(rec_)]
