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
