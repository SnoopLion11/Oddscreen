"""Read-only Novig price feed, used to de-vig Kalshi player-prop legs.

Novig is an exchange. Its public catalog (docs.novig.com, /v3/public/...)
needs no key: each market lists its outcomes with the best resting bid.
The book holds only bids, so buying an outcome costs 1 - (best bid on the
other outcome). A two-way prop (Over/Under or Yes/No) therefore gives
    over_ask = 1 - under_bid      under_ask = 1 - over_bid
and those two asks are de-vigged exactly like a Kalshi market's YES/NO asks.

Safety: this client only sends GET requests to /v3/public/ paths. It has no
way to sign a request, place an order or touch an account.

Matching a Kalshi prop to a Novig market (Kalshi title "Isaiah Jackson: 20+
points", floor_strike 19.5):
  - same player (names normalised: accents, punctuation, Jr/Sr/II dropped),
  - same stat set (points; points+rebounds+assists; threes; ...),
  - Novig strike == Kalshi floor_strike,
  - Novig start time within 3 h of the Kalshi game start.
Kalshi YES ("20+") is Novig's Over/Yes outcome.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Optional

DEFAULT_BASE = "https://api.novig.com"

# Kalshi NBA prop series -> the stats it counts. Series not listed here
# (e.g. the *PROT variants, double/triple-doubles) keep Kalshi's own fair.
KALSHI_STATS = {
    "KXNBAPTS": frozenset({"PTS"}),
    "KXNBAREB": frozenset({"REB"}),
    "KXNBAAST": frozenset({"AST"}),
    "KXNBA3PT": frozenset({"3PT"}),
    "KXNBASTL": frozenset({"STL"}),
    "KXNBABLK": frozenset({"BLK"}),
    "KXNBAPRA": frozenset({"PTS", "REB", "AST"}),
    "KXNBAPR": frozenset({"PTS", "REB"}),
    "KXNBAPA": frozenset({"PTS", "AST"}),
    "KXNBARA": frozenset({"REB", "AST"}),
}

_SUFFIX = {"jr", "sr", "ii", "iii", "iv"}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]+", " ", s.replace("'", "").replace(".", ""))
    return " ".join(w for w in s.split() if w not in _SUFFIX)


def stats_in(text: str) -> frozenset:
    """Stats named in a Novig marketType or description, e.g.
    'PLAYER_POINTS_REBOUNDS_ASSISTS' or 'Points + Rebounds + Assists'."""
    t = text.upper().replace("-", " ").replace("_", " ")
    out = set()
    if re.search(r"THREE|3 ?PT|3PM|3 POINT|3PT", t):
        out.add("3PT")
        t = re.sub(r"THREE[ A-Z]*?(POINTERS?|POINTS?)?|3 ?POINT[A-Z]*|3 ?PT[A-Z]*|3PM", " ", t)
    for stat, pat in (("PTS", r"\bPOINTS?\b|\bPTS\b"), ("REB", r"REBOUND|\bREB\b"), ("AST", r"ASSIST|\bAST\b"),
                      ("STL", r"STEAL|\bSTL\b"), ("BLK", r"BLOCK|\bBLK\b")):
        if re.search(pat, t):
            out.add(stat)
    return frozenset(out)


def kalshi_prop(market_ticker: str, title: str, floor_strike) -> Optional[tuple[str, frozenset, float]]:
    """(player, stats, strike) of a Kalshi NBA prop, or None if not a supported prop."""
    series = market_ticker.split("-")[0]
    stats = KALSHI_STATS.get(series)
    if stats is None or floor_strike is None or ":" not in title:
        return None
    return norm_name(title.split(":")[0]), stats, float(floor_strike)


@dataclass(frozen=True, slots=True)
class PropPrice:
    market_id: str
    description: str
    over_ask: float                # cost to buy Over/Yes = 1 - under bid
    under_ask: float
    over_qty: float                # resting size behind those asks
    under_qty: float


def _side(name: str) -> Optional[str]:
    n = name.strip().lower()
    if n.startswith("over") or n == "yes":
        return "over"
    if n.startswith("under") or n == "no":
        return "under"
    return None


def prop_price(m: dict) -> Optional[PropPrice]:
    """Two-way asks from a Novig market's outcome bids; None if one side has no bid."""
    bids = {}
    for o in m.get("outcomes") or []:
        side = _side(o.get("name", ""))
        bid = o.get("bid") or {}
        if side and bid.get("price") is not None:
            bids[side] = (float(bid["price"]), float(bid.get("qty") or 0))
    if set(bids) != {"over", "under"}:
        return None
    (ob, oq), (ub, uq) = bids["over"], bids["under"]
    return PropPrice(str(m.get("marketId")), m.get("description", ""), 1.0 - ub, 1.0 - ob, uq, oq)


def match(markets: Iterable[dict], player: str, stats: frozenset, strike: float,
          game_start: Optional[float], window_s: float = 3 * 3600) -> Optional[dict]:
    """The open Novig market for this player/stat/line, or None (also None if ambiguous)."""
    hits = []
    for m in markets:
        if m.get("status", "OPEN") != "OPEN" or m.get("strike") is None:
            continue
        try:
            if abs(float(m["strike"]) - strike) > 1e-6:
                continue
        except (TypeError, ValueError):
            continue
        if game_start is not None and m.get("startsTs") and abs(m["startsTs"] / 1000 - game_start) > window_s:
            continue
        desc = m.get("description", "")
        if player not in norm_name(desc):
            continue
        if stats_in(m.get("marketType", "")) != stats and stats_in(desc) != stats:
            continue
        hits.append(m)
    return hits[0] if len(hits) == 1 else None


class Client:
    """GET-only client for Novig's public, unauthenticated catalog."""

    def __init__(self, base: str = DEFAULT_BASE, timeout: float = 10.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def get(self, path: str, params: Optional[dict] = None):
        if not path.startswith("/v3/public/"):
            raise PermissionError(f"refused: GET {path} (only /v3/public/ reads are allowed)")
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read() or b"{}")

    def markets(self, league: str = "NBA", max_pages: int = 20, **params) -> list[dict]:
        out, after = [], None
        for _ in range(max_pages):
            page = self.get("/v3/public/catalog/markets", {"league": league, "limit": 5000, "after": after, **params})
            out.extend(page.get("items") or [])
            after = page.get("next")
            if not after:
                break
        return out


class PropBook:
    """Novig NBA markets fetched once per run (refreshed after max_age_s).
    If Novig can't be reached, `error` is set and every lookup returns None,
    so callers fall back to Kalshi's own book."""

    def __init__(self, client: Optional[Client] = None, league: str = "NBA", max_age_s: float = 120.0):
        self.client = client or Client()
        self.league = league
        self.max_age_s = max_age_s
        self._markets: Optional[list[dict]] = None
        self._at = 0.0
        self.error: Optional[str] = None

    def markets(self) -> list[dict]:
        if self.error:
            return []
        if self._markets is None or time.monotonic() - self._at > self.max_age_s:
            try:
                self._markets = self.client.markets(self.league)
                self._at = time.monotonic()
            except (urllib.error.URLError, OSError, ValueError) as e:
                self.error = f"{type(e).__name__}: {e}"
                return []
        return self._markets

    def lookup(self, market_ticker: str, title: str, floor_strike, game_start: Optional[float]):
        """(PropPrice, novig market) for a Kalshi prop leg, or None."""
        p = kalshi_prop(market_ticker, title, floor_strike)
        if p is None:
            return None
        m = match(self.markets(), *p, game_start)
        if m is None:
            return None
        price = prop_price(m)
        return (price, m) if price else None
