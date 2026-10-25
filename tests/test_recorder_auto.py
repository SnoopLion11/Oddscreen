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
