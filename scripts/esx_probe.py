"""Read-only ESX probe: answers what the docs leave open, using your API key.

Run on your own machine:   python scripts/esx_probe.py [--market <marketIdStr>] [--seconds 30]

It answers:
  1. Does the market-data WebSocket stream REAL-money or PAPER books?
     (compares the WebSocket book with GET /orderbook?mode=real and ?mode=paper
      for the same market at the same moment)
  2. How mode is passed to /orderbook (tries ?mode=real / ?mode=paper / none).
  3. The fields on listMarkets / getMarket / games (market type, line, outcome
     names, sport, league, live status, clock).
  4. How WebSocket outcomeId keys relate to orderbook keys and chainOutcomeId.
  5. Whether market lookups work with the API key alone (no login token).
  6. Observed WebSocket message rate and exchange-to-local delay.

Safety: only GETs on the market-data allowlist and the market-data WebSocket.
Any other path raises before a request is made. Only ESX_API_KEY is read;
no password or login token is used. Raw responses are written to
probe_output/ (git-ignored) so you can send them back; they contain market
data only, never your key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import websockets
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "probe_output"

# Display-only: the ONLY REST paths this tool may touch.
_ALLOWED = [
    re.compile(r"^/api/markets/games$"),
    re.compile(r"^/api/markets/markets$"),
    re.compile(r"^/api/markets/ids$"),
    re.compile(r"^/api/markets/[^/]+$"),
    re.compile(r"^/api/markets/[^/]+/orderbook$"),
]


def check_allowed(method: str, path: str) -> None:
    if method != "GET" or not any(r.match(path) for r in _ALLOWED):
        raise PermissionError(f"refused: {method} {path} is not a read-only market-data endpoint")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


class Rest:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/")
        self.key = key
        self.calls = 0

    def get(self, path: str, params: dict | None = None) -> tuple[int, object, float]:
        check_allowed("GET", path)
        url = self.base + urllib.parse.quote(path)
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers={"X-API-Key": self.key, "Accept": "application/json"})
        self.calls += 1
        time.sleep(0.06)  # stay far below 25 req/s
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            body, status = e.read(), e.code
        ms = (time.perf_counter() - t0) * 1000
        try:
            data = json.loads(body)
        except ValueError:
            data = body.decode(errors="replace")[:500]
        return status, data, ms


def save(name: str, data) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, default=str))


def first_list_of_dicts(data):
    """Find the market/game list whatever the envelope ({data:[]}, {markets:[]}, [])."""
    if isinstance(data, list) and (not data or isinstance(data[0], dict)):
        return data
    if isinstance(data, dict):
        for v in data.values():
            found = first_list_of_dicts(v)
            if found:
                return found
    return []


def keys_summary(obj, depth=0, prefix="") -> list[str]:
    """Flat list of field paths with an example value, for reading the shape."""
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)) and depth < 3:
                lines += keys_summary(v, depth + 1, p)
            else:
                lines.append(f"  {p} = {json.dumps(v, default=str)[:80]}")
    elif isinstance(obj, list) and obj:
        lines += keys_summary(obj[0], depth + 1, prefix + "[0]")
    return lines


def best_bids_from_ws(book: dict) -> dict[str, tuple[int, int] | None]:
    out = {}
    for oid, levels in (book.get("bids") or {}).items():
        out[str(oid)] = max(((l["price"], l["size"]) for l in levels), default=None)
    return out


def best_buys_from_rest(ob) -> dict[str, tuple | None]:
    outcomes = ob.get("outcomes") if isinstance(ob, dict) else None
    if not isinstance(outcomes, dict):
        return {}
    out = {}
    for oid, side in outcomes.items():
        buys = (side or {}).get("buy") or []
        out[str(oid)] = max(((l.get("price"), l.get("quantity")) for l in buys), default=None)
    return out


async def ws_probe(ws_url: str, key: str, symbol: str, seconds: float) -> dict:
    result = {"connected": False, "messages": [], "books": 0, "delays_ms": [], "first_book": None, "error": None}
    try:
        async with connect(ws_url, additional_headers={"X-API-Key": key}, open_timeout=15) as ws:
            result["connected"] = True
            await ws.send(json.dumps({"op": "subscribe", "symbols": [symbol]}))
            end = time.time() + seconds
            last_ping = time.time()
            while time.time() < end:
                if time.time() - last_ping > 10:
                    await ws.send(json.dumps({"op": "ping"}))
                    last_ping = time.time()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                recv_ms = time.time() * 1000
                msg = json.loads(raw)
                if len(result["messages"]) < 50:
                    result["messages"].append(msg)
                if msg.get("type") == "book":
                    result["books"] += 1
                    if result["first_book"] is None:
                        result["first_book"] = msg
                    if isinstance(msg.get("ts"), (int, float)):
                        result["delays_ms"].append(recv_ms - msg["ts"])
    except Exception as e:  # report, don't hide
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", help="marketIdStr to probe (default: first open market found)")
    ap.add_argument("--sport", default=None)
    ap.add_argument("--league", default=None)
    ap.add_argument("--seconds", type=float, default=30.0, help="how long to listen on the WebSocket")
    args = ap.parse_args()

    env = load_env()
    key = env.get("ESX_API_KEY", "")
    if not key:
        print("ESX_API_KEY is not set (put it in .env).")
        return 2
    base = env.get("ESX_API_URL", "https://api.empiresportsexchange.com")
    host = urllib.parse.urlparse(base).netloc
    ws_url = env.get("ESX_WS_URL", f"wss://{host}/v1/marketdata")
    rest = Rest(base, key)
    report: dict = {"base": base, "ws_url": ws_url, "key_prefix": key[:9] + "..."}

    print(f"REST {base}\nWS   {ws_url}\nkey  {report['key_prefix']}\n")

    # --- listMarkets / games / ids, API key only -----------------------------
    st, markets_raw, ms = rest.get("/api/markets/markets", {"sport": args.sport, "league": args.league})
    save("list_markets", markets_raw)
    markets = first_list_of_dicts(markets_raw)
    print(f"[listMarkets]  HTTP {st}  {ms:.0f} ms  {len(markets)} markets")
    report["list_markets_status"] = st
    if markets:
        print("\n".join(keys_summary(markets[0])))

    st, ids_raw, ms = rest.get("/api/markets/ids", {"sport": args.sport, "league": args.league})
    save("market_ids", ids_raw)
    print(f"\n[getMarketIds] HTTP {st}  {ms:.0f} ms")
    report["market_ids_status"] = st

    if args.sport and args.league:
        st, games_raw, ms = rest.get("/api/markets/games", {"sport": args.sport, "league": args.league})
        save("list_games", games_raw)
        print(f"[listGames]    HTTP {st}  {ms:.0f} ms")
        games = first_list_of_dicts(games_raw)
        if games:
            print("\n".join(keys_summary(games[0])))

    if st in (401, 403) or report["list_markets_status"] in (401, 403):
        print("\nMarket lookups were refused with the API key alone: a login token is required.")

    # --- pick a market ---------------------------------------------------------
    symbol = args.market
    if not symbol:
        def is_open(m):
            s = str(m.get("status", "")).lower()
            return s in ("open", "active", "live", "trading", "")
        cand = [m for m in markets if is_open(m)] or markets
        for m in cand:
            symbol = m.get("marketIdStr") or m.get("slug") or m.get("marketId") or m.get("id")
            if symbol:
                break
    if not symbol:
        print("\nNo market found to probe. Re-run with --market <marketIdStr>.")
        save("report", report)
        return 1
    symbol = str(symbol)
    report["market"] = symbol
    print(f"\nProbing market {symbol}")

    st, detail, ms = rest.get(f"/api/markets/{symbol}")
    save("get_market", detail)
    print(f"[getMarket]    HTTP {st}  {ms:.0f} ms")
    print("\n".join(keys_summary(detail)))

    # --- WebSocket, then REST books immediately after ---------------------------
    print(f"\n[WebSocket] listening {args.seconds:.0f} s ...")
    ws = asyncio.run(ws_probe(ws_url, key, symbol, args.seconds))
    save("websocket", ws)
    books = {}
    for mode in ("real", "paper", None):
        st, ob, ms = rest.get(f"/api/markets/{symbol}/orderbook", {"mode": mode} if mode else None)
        name = mode or "no_mode"
        save(f"orderbook_{name}", ob)
        books[name] = best_buys_from_rest(ob) if st == 200 else None
        print(f"[orderbook ?mode={mode}]  HTTP {st}  {ms:.0f} ms  best buys: {books[name]}")

    report["ws"] = {k: ws[k] for k in ("connected", "books", "error")}
    if ws["error"]:
        print(f"WebSocket error: {ws['error']}")
    if ws["first_book"]:
        last_book = next((m for m in reversed(ws["messages"]) if m.get("type") == "book"), ws["first_book"])
        ws_best = best_bids_from_ws(last_book)
        print(f"WebSocket books received: {ws['books']}  latest best bids: {ws_best}")
        if ws["delays_ms"]:
            d = sorted(ws["delays_ms"])
            print(f"exchange ts -> local receive: median {d[len(d)//2]:.0f} ms "
                  f"(includes clock offset between ESX and this machine)")
        report["ws_outcome_keys"] = sorted(ws_best)
        for name, b in books.items():
            if b is None:
                continue
            same_keys = set(b) == set(ws_best)
            same_prices = same_keys and all((b[k] or (None,))[0] == (ws_best[k] or (None,))[0] for k in b)
            report[f"ws_matches_{name}"] = same_prices
            print(f"  WS vs orderbook {name}: keys {'match' if same_keys else 'DIFFER'}, "
                  f"best prices {'match' if same_prices else 'differ'}")
        report["get_market_outcome_ids"] = [
            {k: o.get(k) for k in o if "id" in k.lower()}
            for o in (detail.get("outcomes") or [] if isinstance(detail, dict) else [])
        ]
        print(f"getMarket outcome id fields: {report['get_market_outcome_ids']}")
        print("\nIf WS matches 'real' and differs from 'paper', the WebSocket is real-money.\n"
              "If real and paper books happen to be identical, re-run on a busier market.")
    else:
        print("No book message received; try a busier market or longer --seconds.")

    save("report", report)
    print(f"\nRaw responses saved in {OUT}/  (market data only, no key). REST calls made: {rest.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
