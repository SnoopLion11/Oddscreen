# ESX trading API: overview (as provided)

Source: ESX "Real trading API docs" overview, pasted by the user. This is the
overview only; it does not include endpoints, URLs, message formats, rate
limits or fees. See "Missing" below.

## Orders

- Place up to 50 orders in one request. Price is a whole number of cents from 1 to 99.
- List open orders, or fetch one order by id once it is on the book.
- Read filled orders in order history.
- Cancel one order, several orders, or every order.

## Markets

- List games for a sport and league.
- List markets, and filter by sport, league, and status.
- Read the chainOutcomeId, marketid and data about all markets.
- Read the order book: the buy and sell prices for every outcome.
- Get market ids and each outcome's chainOutcomeId, for placing orders.
- Compare the best prices at ESX to your own models instantly by connecting to our market data WebSocket.

## Account

- Create an API key in Settings. Every trading call needs that key and a login token for the same account.
- Get your balance, equity, and open positions.
- Claim every resolved market you can claim.
- Deposit or withdraw from the app, under Portfolio. There is no API for either.

## Authentication (as provided)

Create a key in Settings, then API Keys. `POST /api/api-keys` returns the secret
once. Each account can hold up to 5 keys.

Every trading call needs the API key and a login token for the same account:
key in `X-API-Key`, token in `Authorization`.

Email/password accounts: the helper signs in with `FIREBASE_EMAIL` and
`FIREBASE_PASSWORD` and refreshes the login token.

```
export ESX_API_KEY="esx_live_..."
export FIREBASE_EMAIL="you@example.com"
export FIREBASE_PASSWORD="..."
export ESX_API_URL="https://api.empiresportsexchange.com"
```

Google sign-in accounts: set `ESX_API_KEY` only; `googleAuth` gets a login
token from the key and caches it (`validateGoogleAuth`, `googleApiRequest`).

The docs follow the folder layout of a downloadable helper zip
(`scripts/api-functions/config`, `scripts/api-functions/googleAuth`).

## Trading flow (as provided; display-only tool uses step 1 only)

- `GET /api/markets/{marketId}`: market with `outcomes[]`; keep
  `chainOutcomeId` as a string. **Read-only; used by the screen.**
- `POST /api/blockchain-trading/place-order-v2`: **never called**
- `GET /api/blockchain-trading/get-active-orders-v2`: not needed
- `POST /api/blockchain-trading/cancel-all-orders-v2`: **never called**
- `POST /api/real/markets/{marketId}/claim`: **never called**

Place accepts 1-50 orders per request (`placeBuyOrder`, `placeSellOrder`,
`placeOrders`; body `{ orders: [...] }`); buys spend USDC, sells spend held
shares. `get-active-orders-v2` takes `status` (open | pending | partial |
filled | cancelled), `limit` (default 100), `offset`. The screen calls none of
these.

Place returns 202 (submitted, not yet on book); getOrder/cancelOrder 404 until
it rests; claim 403 means claim from Portfolio in the app.

## Market data REST (as provided; helper module `scripts/api-functions/marketData`)

- `GET /api/markets/games`: `listGames({ sport, league })`, both required
  (e.g. `sport: "football", league: "NFL"`). Each game has `gameId`, `startTime`.
- `GET /api/markets/{marketId}`: `getMarket(marketId)`; marketId may be the
  numeric id, `marketIdStr`, or slug. Returns `{ market: MarketDetail,
  game: GameInfo | null, outcomes: Outcome[] }`. `chainOutcomeId` is a string.
- `GET /api/markets/{marketId}/orderbook`: `getOrderbook(marketId, mode?)`,
  `mode` is `"real" | "paper"`. **The URL returns PAPER if mode is omitted**
  (the helper defaults to real). Returns `{ marketId: number, outcomes:
  Record<outcomeHashId, { buy: {price, quantity}[], sell: {price, quantity}[] }> }`.
  One snapshot; the WebSocket sends later changes.
- `GET /api/markets/ids`: `getMarketIds({ status?, sport?, league? })` returns
  `{ success, data: MarketIdInfo[] }`; each item has `marketId`, `title?`,
  `status`, `outcomes[]` with `chainOutcomeId`.

## Market-data WebSocket (as provided)

Connect `wss://<api-host>/v1/marketdata` with header `X-API-Key: esx_live_...`.
No login token is mentioned for this socket.

Subscribe with `marketIdStr` (from listMarkets / getMarket). Moneyline, each
spread, each total and each player prop is its own market. Symbols can be
added/removed on the same connection.

```
{ "op": "subscribe", "symbols": ["<marketIdStr>"] }
{ "op": "unsubscribe", "symbols": ["<marketIdStr>"] }
{ "op": "ping" }
```

Limits: 100 symbols per subscribe message, 500 symbols per connection,
5 connections per API key. Ping at least every 30 s or the server closes the
socket. Only open markets can be subscribed; when a market closes its
subscription ends, and if it was the only one the socket closes.

Messages: first `book` is the full set of bids; later `book` messages are a new
FULL book, sent only when a bid changes. `seq` starts at 1 and increments by 1;
on a gap, re-subscribe that symbol.

```
{ "type": "subscribed", "symbol": "<marketIdStr>" }
{ "type": "book", "symbol": "<marketIdStr>", "seq": 1, "ts": 1726339200123,
  "bids": {
    "<first outcomeId>": [ { "price": 52, "size": 1200 }, { "price": 51, "size": 400 } ],
    "<second outcomeId>": [ { "price": 46, "size": 800 } ]
  }
}
{ "type": "pong", "ts": 1726339200200 }
{ "type": "error", "code": "unknown_symbol", "message": "Unknown symbol EXAMPLE" }
{ "type": "error", "code": "market_closed", "symbol": "<marketIdStr>", "message": "Market is closed" }
```

Keys under `bids` are outcomeIds of that market; price is whole cents 1-99;
size is shares at that price. Both outcomes are included. An ask on one
outcome is a bid on the other at 100 minus that price.

### How the screen will use it

- Ask for outcome A = 100 - best bid on outcome B (and vice versa), the same
  rule as Kalshi. Size at that ask = size of B's best bid.
- Liquidity in dollars at the ask = shares x ask price.
- Stale detection: send `ping` every 10 s; feed is stale if nothing (book or
  pong) arrives for the configured number of seconds.
- Shard subscriptions across connections at 500 symbols each (max 5).
- Latency: `ts` in each book message gives exchange-to-us delay in addition
  to our own receive-to-screen time.

## Rate limits (as provided)

- Trading calls from one IP share 200 requests/minute (placing, cancelling,
  reading your orders).
- Each API key: 25 requests/second.
- 429 = limit passed; wait for the window, then retry.

How the screen uses this: prices come only from the WebSocket (no REST
polling). REST is used only for market discovery and a one-off orderbook
snapshot per market, through a client-side limiter capped at 20 req/s
(headroom under 25/s) with back-off on 429. It makes no trading calls, so the
200/min IP budget is untouched.

## Settled

- REST base URL: `https://api.empiresportsexchange.com` (key prefix `esx_live_`).
- Auth headers: `X-API-Key: <key>`, `Authorization: <login token>`.
- Login token: Firebase sign-in (email/password) or derived from the key
  (Google sign-in); the helper handles refresh.
- Market-data WebSocket: URL path, API-key-only auth, subscribe/ping format,
  full-book-on-change updates with `seq`, sizes included, connection limits.
- Rate limits: 25 req/s per key; 200/min per IP for trading calls.
- Keys are not scoped: the same key + token can trade. Display-only must be
  enforced in our code (no order/cancel/claim paths).

## Missing (needed before the ESX connector is written)

1. **Real money on the WebSocket.** The REST order book returns paper unless
   `mode=real` is passed. Does `/v1/marketdata` stream real-money books, and
   if it has a mode switch, what is it?
2. Exact way to pass `mode` to `/orderbook` (e.g. `?mode=real`).
3. `<api-host>` for the WebSocket: confirm it is `api.empiresportsexchange.com`.
4. Field shapes of `MarketDetail`, `GameInfo`, `Outcome`, and the listMarkets
   section (returns `marketIdStr`): market type, line, outcome names, sport,
   league, live status / game clock. And which outcome id is used as the key
   in WebSocket `bids` (`outcomeId`) vs REST orderbook (`outcomeHashId`) vs
   `chainOutcomeId`.
5. Whether REST market endpoints need the login token or just the key.
6. Fee structure: flat per contract vs % of winnings, and how much.
