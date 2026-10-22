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

Place returns 202 (submitted, not yet on book); getOrder/cancelOrder 404 until
it rests; claim 403 means claim from Portfolio in the app.

## Settled

- REST base URL: `https://api.empiresportsexchange.com` (key prefix `esx_live_`).
- Auth headers: `X-API-Key: <key>`, `Authorization: <login token>`.
- Login token: Firebase sign-in (email/password) or derived from the key
  (Google sign-in); the helper handles refresh.
- Keys are not scoped: the same key + token can trade. Display-only must be
  enforced in our code (no order/cancel/claim paths).

## Missing (needed before the ESX connector is written)

1. Market-data WebSocket URL; confirmation that the REST URL above is real-money
   (and what the paper URL / key prefix is).
2. Login token lifetime and refresh mechanics (exact header format, e.g.
   `Bearer <token>`), and for Google sign-in, the endpoint that exchanges the key.
3. Whether the market-data WebSocket needs the API key and/or login token.
4. WebSocket subscribe message, update format (snapshot vs incremental), heartbeat/ping.
5. Whether order book / WebSocket prices include size at each price (liquidity guard).
6. Market endpoints: list games, list markets, order book (paths + example
   responses), plus the full `GET /api/markets/{marketId}` response shape.
   The claim path is `/api/real/...` while the market path is `/api/markets/...`:
   confirm `/api/markets/*` returns REAL-money markets, not paper.
7. Rate limits.
8. Fee structure: flat per contract vs % of winnings, and how much.
