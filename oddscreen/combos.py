"""Combo fair value and RFQ quote analysis for the log-only recorder.

Pure functions, no I/O.

Fair value: each leg is de-vigged on its own Kalshi market (YES ask and NO
ask of that market only, via devig.devig_market, worst case), and the combo
fair is the product of the leg fairs. The product is only valid when the
legs are independent, so it is computed only when every leg is from a
different game. Same-game combos are still recorded (their quotes feed the
quoter-consensus numbers) but get no fair value.

Quote sides: a quoter's `yes_bid` is what they pay for YES and `no_bid`
what they pay for NO. To the requester that means
    buy YES at 1 - no_bid      buy NO at 1 - yes_bid
(Kalshi's FIX docs: BUY accepts the maker's NO quote.)
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

from . import devig


@dataclass(frozen=True, slots=True)
class Leg:
    market_ticker: str
    event_ticker: str
    side: str                      # "yes" or "no"

    def as_api(self) -> dict:
        return {"market_ticker": self.market_ticker, "event_ticker": self.event_ticker, "side": self.side}


def game_key(event_ticker: str) -> str:
    """Game identifier shared by every event of one game.

    Kalshi event tickers are SERIES-GAMEID, e.g. KXNFLGAME-25OCT26KCLV and
    KXNFLTOTAL-25OCT26KCLV: the series differs per market type, the second
    segment identifies the game. Some props add a team suffix
    (KXNFLTEAMFIRSTTD-26OCT25PITNO-PIT), so later segments are dropped.
    """
    parts = event_ticker.split("-")
    return parts[1] if len(parts) > 1 else event_ticker


def independent(legs: Sequence[Leg]) -> bool:
    keys = [game_key(l.event_ticker) for l in legs]
    return len(set(keys)) == len(keys)


@dataclass(frozen=True, slots=True)
class LegFair:
    fair: float                    # worst-case fair of the leg's chosen side
    method: str
    yes_ask: float
    no_ask: float
    yes_size: float
    no_size: float


def leg_fair(side: str, yes_ask: Optional[float], no_ask: Optional[float],
             yes_size: float = 0.0, no_size: float = 0.0, min_size: float = 0.0,
             max_vig: Optional[float] = None) -> Optional[LegFair]:
    """De-vig one Kalshi binary market from its own two asks. None if unpriceable,
    or if yes_ask + no_ask - 1 exceeds max_vig (an empty side, e.g. NO at 0.99)."""
    if yes_ask is None or no_ask is None:
        return None
    if not (devig.valid_price(yes_ask) and devig.valid_price(no_ask)):
        return None
    if yes_size < min_size or no_size < min_size:
        return None
    if max_vig is not None and yes_ask + no_ask - 1.0 > max_vig + 1e-9:
        return None
    sides = devig.devig_market([yes_ask, no_ask])
    if sides is None:
        return None
    s = sides[0] if side == "yes" else sides[1]
    return LegFair(s.fair, s.method, yes_ask, no_ask, yes_size, no_size)


def combo_fair(leg_fairs: Sequence[Optional[LegFair]]) -> Optional[float]:
    if not leg_fairs or any(f is None for f in leg_fairs):
        return None
    return math.prod(f.fair for f in leg_fairs)


# --- fees -------------------------------------------------------------------

def taker_fee_per_contract(price: float, contracts: float, multiplier: float = 1.0) -> float:
    """Kalshi's general taker fee, ceil-to-cent on the whole trade, per contract.

    fee = roundup(0.07 * M * C * P * (1 - P)). Whether RFQ/combo fills are
    charged this way is NOT confirmed (docs/kalshi_api.md); pass
    multiplier=0 to log pre-fee numbers only.
    """
    if multiplier == 0 or contracts <= 0:
        return 0.0
    total = math.ceil(round(0.07 * multiplier * contracts * price * (1.0 - price) * 100.0, 9)) / 100.0
    return total / contracts


# --- quote analysis ---------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Quote:
    quote_id: str
    creator: str                   # creator_id as returned; may be anonymized
    yes_bid: Optional[float]       # dollars
    no_bid: Optional[float]
    first_seen_s: float            # seconds after the RFQ was created

    @property
    def yes_cost(self) -> Optional[float]:
        return None if self.no_bid is None else 1.0 - self.no_bid

    @property
    def no_cost(self) -> Optional[float]:
        return None if self.yes_bid is None else 1.0 - self.yes_bid

    @property
    def mid(self) -> Optional[float]:
        """Quoter's implied YES probability: midway between their YES bid and YES offer."""
        if self.yes_bid is None or self.no_bid is None:
            return None
        return (self.yes_bid + 1.0 - self.no_bid) / 2.0


@dataclass(slots=True)
class SideResult:
    cost: Optional[float] = None           # best price to buy this side
    quote_id: str = ""
    creator: str = ""
    fee: float = 0.0                       # per contract
    ev_pct_fair: Optional[float] = None    # vs leg-product fair, after fee
    ev_pct_fair_prefee: Optional[float] = None
    consensus: Optional[float] = None      # other quoters' median mid, for this side
    edge_vs_consensus_c: Optional[float] = None   # cents cheaper than consensus


@dataclass(slots=True)
class QuoteSummary:
    n_quotes: int
    n_quoters: int
    consensus_mid: Optional[float]         # median mid of all quotes (YES probability)
    mid_spread_c: Optional[float]          # max - min mid across quoters, cents
    yes: SideResult = field(default_factory=SideResult)
    no: SideResult = field(default_factory=SideResult)


def latest_per_creator(quotes: Sequence[Quote]) -> list[Quote]:
    """Keep each quoter's most recent quote (quoters can replace quotes)."""
    by: dict[str, Quote] = {}
    for q in sorted(quotes, key=lambda q: q.first_seen_s):
        by[q.creator or q.quote_id] = q
    return list(by.values())


def summarize(quotes: Sequence[Quote], fair: Optional[float], contracts: float,
              fee_multiplier: float = 1.0) -> QuoteSummary:
    live = latest_per_creator(quotes)
    mids = [q.mid for q in live if q.mid is not None]
    s = QuoteSummary(
        n_quotes=len(quotes),
        n_quoters=len(live),
        consensus_mid=statistics.median(mids) if mids else None,
        mid_spread_c=(max(mids) - min(mids)) * 100.0 if len(mids) >= 2 else None,
    )
    for name, side_fair in (("yes", fair), ("no", None if fair is None else 1.0 - fair)):
        res: SideResult = getattr(s, name)
        priced = [(getattr(q, f"{name}_cost"), q) for q in live]
        priced = [(c, q) for c, q in priced if c is not None and devig.valid_price(c)]
        if not priced:
            continue
        cost, best = min(priced, key=lambda t: t[0])
        res.cost, res.quote_id, res.creator = cost, best.quote_id, best.creator
        res.fee = taker_fee_per_contract(cost, contracts, fee_multiplier)
        if side_fair is not None:
            res.ev_pct_fair_prefee = devig.ev_pct(side_fair, cost)
            res.ev_pct_fair = devig.ev_pct(side_fair, cost + res.fee)
        # Leave-one-out consensus: does the best quote beat the OTHER quoters?
        others = [q.mid for q in live if q is not best and q.mid is not None]
        if others:
            m = statistics.median(others)
            res.consensus = m if name == "yes" else 1.0 - m
            res.edge_vs_consensus_c = (res.consensus - cost - res.fee) * 100.0
    return s
