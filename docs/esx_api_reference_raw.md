# ESX API reference: sections as pasted by the user (verbatim)

Source: https://www.esxtrade.com/api-docs/v2 (JavaScript-rendered; pasted into
the build session section by section). The working summary used by the code,
with what is settled and what is still missing, is in `esx_api.md`.

---

Real trading API docs for ESX

## Overview

### Orders

- Place up to 50 orders in one request. Price is a whole number of cents from 1 to 99.
- List open orders, or fetch one order by id once it is on the book.
- Read filled orders in order history.
- Cancel one order, several orders, or every order.

### Markets

- List games for a sport and league.
- List markets, and filter by sport, league, and status.
- Read the chainOutcomeId, marketid and data about all markets.
- Read the order book: the buy and sell prices for every outcome.
- Get market ids and each outcome's chainOutcomeId, for placing orders.
- Compare the best prices at ESX to your own models instantly by connecting to our market data WebSocket.

### Account

- Create an API key in Settings. Every trading call needs that key and a login token for the same account.
- Get your balance, equity, and open positions.
- Claim every resolved market you can claim.
- Deposit or withdraw from the app, under Portfolio. There is no API for either.

---

## Authentication Setup

Create a key in Settings, then API Keys. POST /api/api-keys returns the secret once. Each account can hold up to 5 keys.

Every trading call needs your API key and a login token for the same account. Send the key in X-API-Key and the token in Authorization.

The helper signs in with FIREBASE_EMAIL and FIREBASE_PASSWORD and refreshes the login token for you.

```
export ESX_API_KEY="esx_live_..."
export FIREBASE_EMAIL="you@example.com"
export FIREBASE_PASSWORD="..."
export ESX_API_URL="https://api.empiresportsexchange.com"
```

```
import { getAuthStatus } from './scripts/api-functions/config'

const status = getAuthStatus()
// hasApiKey, hasFirebaseCredentials, isFullyAuthenticated, hasToken
```

### Google sign-in

If you signed in with Google, set ESX_API_KEY only. Use googleAuth instead of the email and password helper. It gets a login token from your key and caches it.

```
export ESX_API_KEY="esx_live_..."
export ESX_API_URL="https://api.empiresportsexchange.com"
```

```
import { validateGoogleAuth, googleApiRequest } from './scripts/api-functions/googleAuth'

await validateGoogleAuth()
// googleApiRequest sends X-API-Key and the cached login token
```

Download the helper zip. These docs follow that folder layout.

---

## Trading Flow

Look up a market, place an order, then cancel all if you change your mind before it is on the book. After it is on the book, open orders shows resting or filled. Claim after the market resolves.

```
const { outcomes } = await getMarket(marketId)
const onchainOutcomeId = outcomes[0].chainOutcomeId!
const placed = await placeBuyOrder({
    marketId,
    price,
    amount,
    onchainOutcomeId,
})
// 202 submitted. getOrder and cancelOrder return 404 until
// the order is on the book. cancelAll covers that window.
const open = await getActiveOrders({ status: 'open' })
await cancelAllOrders(marketId)
// after the market resolves:
await claimMarket(marketId)
```

API ENDPOINTS

- GET /api/markets/{marketId}
- POST /api/blockchain-trading/place-order-v2
- GET /api/blockchain-trading/get-active-orders-v2
- POST /api/blockchain-trading/cancel-all-orders-v2
- POST /api/real/markets/{marketId}/claim

### Type Reference

#### Typical flow

steps

- `1` getMarket — Read outcomes and keep chainOutcomeId as a string
- `2` placeBuyOrder — HTTP 202: submitted, not yet on the book. getOrder and cancelOrder 404 until it is.
- `3` getActiveOrders — Lists orders already on the book. Empty right after place.
- `4` cancelAllOrders — Cancels pending and open orders. After a buy, wait for the balance to update.
- `5` claimMarket — Claim winnings after the market resolves. If this returns 403, claim from Portfolio in the app.

---

## WebSocket

Subscribe to markets and receive the current bids whenever they change.

## Connect

Connect to wss://<api-host>/v1/marketdata. Send your API key in the X-API-Key header. The key starts with esx_live_ and comes from your account.

```
wss://<api-host>/v1/marketdata
X-API-Key: esx_live_...
```

## Subscribe

Subscribe with marketIdStr from Market Data. The moneyline, each spread, each total, and each player prop is its own market. listMarkets and getMarket return marketIdStr. You can add or remove symbols on the same connection.

```
{ "op": "subscribe", "symbols": ["<marketIdStr>"] }
{ "op": "unsubscribe", "symbols": ["<marketIdStr>"] }
{ "op": "ping" }
```

One subscribe message accepts at most 100 symbols. One connection accepts at most 500 symbols. One API key accepts at most 5 connections. Send ping at least every 30 seconds or the server closes the socket. A market can be subscribed only while it is open. When it closes, that subscription ends. If it was the only market on the connection, the socket closes.

## What you receive

The first book message is the full set of bids. Later messages are a new full book, sent only when a bid changes. seq starts at 1 and goes up by 1. If seq skips a number, subscribe to that symbol again.

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

The keys under bids are outcomeId from that market. price is a whole number of cents from 1 to 99. size is the number of shares at that price. Both outcomes are included. An ask on one outcome is a bid on the other outcome at 100 minus that price.

---

## List games

Lists games for a sport and league. Both arguments are required.

```
import { listGames } from './scripts/api-functions/marketData'

const games = await listGames({ sport: "football", league: "NFL" })
```

### Type Reference

#### listGames(params)

Lists games for a sport and league. Both arguments are required.

input

- `sport` string required
- `league` string required
output

- `(return)` Record<string, any>[] — Each game includes gameId and startTime.
API ENDPOINTS

- GET /api/markets/games

---

## Get a market

Full details for one market. chainOutcomeId on each outcome is the string you pass when placing an order.

```
import { getMarket } from './scripts/api-functions/marketData'

const detail = await getMarket(marketId)
// chainOutcomeId is a string. Do not pass it through Number.
const onchainOutcomeId = detail.outcomes[0].chainOutcomeId
```

### Type Reference

#### getMarket(marketId)

Full details for one market, including outcomes and the chainOutcomeIds used for trading.

input

- `marketId` string required — Numeric id, marketIdStr, or slug
output: getmarketresponse

- `market` MarketDetail
- `game` GameInfo | null
- `outcomes` Outcome[]
API ENDPOINTS

- GET /api/markets/{marketId}

---

## Get order book

One snapshot of the buy and sell book for every outcome. The WebSocket stream sends later changes.

```
import { getOrderbook } from './scripts/api-functions/marketData'

const book = await getOrderbook(marketId)
```

### Type Reference

#### getOrderbook(marketId, mode?)

One snapshot of the buy and sell book for every outcome. The WebSocket stream sends later changes.

input

- `marketId` string required
- `mode` "real" | "paper" — Helper default is "real". The URL returns paper if mode is omitted.
output: getorderbookresponse

- `marketId` number
- `outcomes` Record<string, OutcomeOrderbook> — Keyed by outcomeHashId. Each value has buy[] and sell[] arrays of { price, quantity }.
API ENDPOINTS

- GET /api/markets/{marketId}/orderbook

---

## Get market ids

Market ids and each outcome chainOutcomeId, for placing orders. Keep chainOutcomeId a string.

```
import { getMarketIds } from './scripts/api-functions/marketData'

const ids = await getMarketIds({ status: "active", league: "NFL" })
```

### Type Reference

#### getMarketIds(params?)

Market ids and each outcome's chainOutcomeId, for placing orders.

input

- `status` string
- `sport` string
- `league` string
output: getmarketidsresponse

- `success` boolean
- `data` MarketIdInfo[] — Each item has marketId, title?, status, and outcomes[] with chainOutcomeId
API ENDPOINTS

- GET /api/markets/ids

---

## Place an order

Place one order or up to 50 in the same request. Price is a whole number of cents from 1 to 99. Amount is how many shares. Use the same account for the API key and the login. Copy chainOutcomeId from the market and pass that string unchanged.

The helper returns HTTP 202 when the node accepts the hash. The order is not on the book yet. Open orders and get-order stay empty until it is. Use cancel all in that window. After it is on the book, open orders shows resting or filled.

```
import { placeBuyOrder, placeOrders } from './scripts/api-functions/placeOrder'

const buy = await placeBuyOrder({
    marketId,
    price: 55,
    amount: 2,
    onchainOutcomeId,
})
// { success, orderId, orderIds, orders, transactionHash, status: 'submitted' }

const batch = await placeOrders([
    { marketId, price: 52, amount: 2, onchainOutcomeId, isBuy: true },
    { marketId, price: 55, amount: 2, onchainOutcomeId, isBuy: false },
])
// Same shape. One transaction.
```

### Type Reference

#### placeBuyOrder(params) / placeSellOrder(params) / placeOrders(orders)

Places one buy, one sell, or up to 50 orders. A buy spends USDC. A sell spends shares you already hold.

input — one order (placeorderparams)

- `marketId` string required
- `price` number required — Whole number of cents from 1 to 99.
- `amount` number required — How many shares.
- `onchainOutcomeId` string required — chainOutcomeId from market data
http body — post /place-order-v2

- `orders` array required — One to 50 orders. Same fields each.
output

- `success` boolean
- `status` 'submitted' — HTTP 202. The node accepted the hash. The order is not on the book yet.
- `orderId` string — The first order id. Look up or cancel by id only after it is on the book.
- `orderIds` string[]
- `orders` SubmittedPlaceOrder[] — One item per order, each status submitted.
- `transactionHash` string
- `title` string — Present on a one-order place.
- `message` string — Buy or Sell, the outcome, the price, and confirming on-chain — or how many orders were submitted.
API ENDPOINTS

- POST /api/blockchain-trading/place-order-v2

---

## Get open orders

Lists orders already on the book. A just-submitted order will not appear yet. Filter with status, or leave it out.

```
import { getActiveOrders } from './scripts/api-functions/orderManagement'

const open = await getActiveOrders({ status: 'open', limit: 100 })
```

### Type Reference

#### getActiveOrders(params?)

Lists orders already on the book. A just-submitted order will not appear yet. Filter with status, or leave it out. Default limit is 100.

query — get /get-active-orders-v2

- `status` string — "open" | "pending" | "partial" | "filled" | "cancelled". Exact match. Omit to return every active order.
- `limit` number — Default 100
- `offset` number — Default 0
output — getactiveordersresponse

- `success` boolean
- `orders` Order[]
- `total` number
- `walletAddress` string
API ENDPOINTS

- GET /api/blockchain-trading/get-active-orders-v2

---

## Get an order

Look up one order by id. Returns 404 until the order is on the book.

```
import { getOrder } from './scripts/api-functions/orderManagement'

const one = await getOrder(orderId)
```

### Type Reference

#### getOrder(orderId)

Fetches one order by id. Returns 404 until the order is on the book. The helper accepts a number or a numeric string.

query — get /get-order-v2

- `orderId` string required
output — getorderresponse

- `success` boolean
- `order` Order
API ENDPOINTS

- GET /api/blockchain-trading/get-order-v2

---

## Get order history

Read finished orders. Limit defaults to 100 and cannot be higher than 500.

```
import { getOrderHistory } from './scripts/api-functions/orderManagement'

const done = await getOrderHistory({ limit: 50 })
```

### Type Reference

#### getOrderHistory(params?)

Lists finished orders. Limit defaults to 100 and cannot be higher than 500.

query — get /get-order-history-v2

- `status` string — filled, cancelled, or another finished status
- `limit` number — Default 100, max 500
- `offset` number — Default 0
output

- `success` boolean
- `orders` Order[]
- `count` number
- `walletAddress` string
API ENDPOINTS

- GET /api/blockchain-trading/get-order-history-v2

---

## Cancel an order

Cancel one order that is already on the book. If you just placed it, this returns 404. If it is in the book but not on-chain yet, this returns 400. Use cancel all for those windows. After you cancel a buy, wait until your balance updates before you spend that USDC again.

```
import { cancelOrder } from './scripts/api-functions/orderManagement'

await cancelOrder(orderId)
```

### Type Reference

#### cancelOrder(orderId)

Cancels one order that is already on the book. Returns 404 if you just placed it, and 400 if it is in the book but not on-chain yet. Use cancel all for those windows. The body is { orderId: number }.

body — post /cancel-order-v2

- `orderId` number required — Positive integer. A JSON string is rejected.
output

- `success` boolean
- `cancelledOrderIds` string[]
- `message` string
API ENDPOINTS

- POST /api/blockchain-trading/cancel-order-v2

---

## Cancel multiple orders

Cancel many orders that are already on the book. Just-submitted ids land in failedOrderIds. Use cancel all for that window. After you cancel a buy, wait until your balance updates before you spend that USDC again.

```
import { cancelOrders } from './scripts/api-functions/orderManagement'

await cancelOrders([123, 456])
```

### Type Reference

#### cancelOrders(orderIds)

Cancels orders that are already on the book. Just-submitted ids land in failedOrderIds. Use cancel all for that window. Each id is a JSON number.

body — post /cancel-orders-v2

- `orderIds` number[] required
output

- `success` boolean
- `cancelledOrderIds` string[]
- `dbOnlyCancelledOrderIds` string[] — Cancelled, but they were not in the open book.
- `failedOrderIds` string[]
- `message` string
API ENDPOINTS

- POST /api/blockchain-trading/cancel-orders-v2

---

## Cancel all orders

Cancels orders still confirming after place, and orders already on the book. confirmingOrderIds are not cancelled yet — retry. After you cancel a buy, wait until your balance updates before you spend that USDC again.

```
import { cancelAllOrders } from './scripts/api-functions/orderManagement'

await cancelAllOrders()
```

### Type Reference

#### cancelAllOrders(marketId?)

Cancels orders still confirming after place, and orders already on the book. Optional marketId limits the scope.

body — post /cancel-all-orders-v2

- `marketId` string — Optional. Cancel for one market only.
output

- `success` boolean
- `cancelledOrderIds` string[]
- `confirmingOrderIds` string[] — Still confirming. USDC still locked. Retry cancel all.
- `dbOnlyCancelledOrderIds` string[] — Cancelled, but they were not in the open book.
- `failedOrderIds` string[]
- `totalOpen` number — Pending plus open at the start of the request.
- `message` string
API ENDPOINTS

- POST /api/blockchain-trading/cancel-all-orders-v2

---

## Get positions

Your balance, equity, and the shares you hold. If this call returns 403, open Portfolio in the app.

```
import {
    getRealSummary,
    getOpenPositions,
} from './scripts/api-functions/realSummary'

const summary = await getRealSummary()
const positions = await getOpenPositions()
```

### Type Reference

#### getRealSummary()

Balance, equity, and the shares you hold. If this returns 403, open Portfolio in the app.

input

- `(none)` — — Uses your login.
output — getrealsummaryresponse

- `success` boolean
- `data` RealSummaryData

#### getOpenPositions()

Open positions only. Calls getRealSummary and returns data.openPositions.

output — openposition[]

- `marketId` number
- `outcomeId` number
- `quantity` number
- `reservedQuantity` number
- `avgPriceCents` number | null
- `realizedPnlCents` number
- `markPriceCents` number
- `unrealizedPnlCents` number
API ENDPOINTS

- GET /api/real/summary

---

## Claim all

Claim every resolved market you can claim. If a market returns 403, claim that one from Portfolio in the app.

```
import { claimAll } from './scripts/api-functions/claimAll'

const all = await claimAll()
// { totalMarkets, succeededCount, failedCount, totalClaimedCents, results }
```

### Type Reference

#### claimAll()

Claims every resolved market you can claim.

input

- `(none)` — — Uses your API key and login.
output — claimallresponse

- `success` boolean
- `totalMarkets` number
- `succeededCount` number
- `failedCount` number
- `totalClaimedCents` number
- `results` ClaimResult[]
API ENDPOINTS

- POST /api/blockchain-trading/claim-all-v2

---

## Deposit/Withdraw

Deposit or withdraw from the app, because we do not offer APIs for deposit or withdraw.

---

## Rate limits

Trading calls from one IP share 200 requests per minute. That includes placing an order, cancelling, and reading your orders.

Each API key is also limited to 25 requests per second.

A 429 response means you passed a limit. Wait for the window to pass, then try again.

---

## Common Errors

Error messages and how to fix them.

| Error | Fix |
|---|---|
| API key required / Invalid API key | Create a key in Settings, then API Keys, and set ESX_API_KEY. |
| Token required / Token expired | Email login: set FIREBASE_EMAIL and FIREBASE_PASSWORD. Google: set ESX_API_KEY and use googleAuth. The helper refreshes the login. |
| API key does not belong to user | The API key and the login must be the same account. |
| Invalid price | Price must be a whole number from 1 to 99 cents. |
| Self-trade | The place still submits. If it crossed your own order, it is cancelled and refunded after that. Cancel the other side first if you want the new order to rest. |
| Order not found | get-order and cancel-order return 404 until the order is on the book. Wait, or use cancel all. |
| Order is not on-chain yet | The order is in the book but not on-chain yet. Wait and retry cancel, or use cancel all. |
| Still confirming | cancel-all returned confirmingOrderIds. Those are not cancelled yet and USDC is still locked. Retry cancel all. |
| Not enough USDC | Deposit more USDC. If you just cancelled a buy, wait until that balance is available again. |
| Market is closed | That market is not open for trading. |
| Cancel rejected the order id | Send orderId as a number. The helper does this for you. |
| Wrong outcome id | Copy chainOutcomeId as a string. Do not convert it with Number(). |
| 403 on positions or claim | Open Portfolio in the app to see shares and claim winnings. |

---

## Input/Output Reference

Quick reference for the helpers.

API ENDPOINTS

- POST /api/blockchain-trading/place-order-v2
- GET /api/blockchain-trading/get-active-orders-v2
- GET /api/blockchain-trading/get-order-v2
- GET /api/blockchain-trading/get-order-history-v2
- POST /api/blockchain-trading/cancel-order-v2
- POST /api/blockchain-trading/cancel-orders-v2
- POST /api/blockchain-trading/cancel-all-orders-v2
- POST /api/blockchain-trading/claim-all-v2
- POST /api/real/markets/:marketId/claim
- GET /api/real/summary
- POST /api/blockchain-trading/sync-balance
- GET /api/markets/{marketId}
- GET /api/markets/{marketId}/orderbook
- GET /api/markets/markets
- GET /api/markets/games
- GET /api/markets/ids

(Transcribed from the screenshot of the reference table.)

| Function | Inputs | Outputs |
|---|---|---|
| placeBuyOrder / placeSellOrder / placeOrders | one order, or orders[] max 50: marketId, price, amount, isBuy, onchainOutcomeId | HTTP 202, status submitted, orderId, orderIds[], orders[], transactionHash |
| getActiveOrders | status?, limit?, offset? | orders[] already on the book, total, walletAddress |
| getOrder | orderId | order, or 404 until it is on the book |
| getOrderHistory | status?, limit? (max 500), offset? | orders[], count, walletAddress |
| cancelOrder | { orderId: number } | cancelledOrderIds[], or 404/400 until it is on-chain |
| cancelOrders | orderIds as a number array | cancelledOrderIds[], failedOrderIds[] (includes just-submitted ids) |
| cancelAllOrders | marketId? | cancelledOrderIds[], confirmingOrderIds[], failedOrderIds[], totalOpen |
| claimMarket | marketId | totalClaimedCents, transactionHash, positionsUpdated |
| claimAll | none | totalMarkets, succeededCount, failedCount, results[] |
| syncBalance | none | synced, updated, balance |
| getRealSummary | none | data.balance, data.openPositions[], data.equityCents |
| getMarket | marketId | market, game, outcomes[].chainOutcomeId |
| getOrderbook | marketId, mode? (real \| paper) | outcomes keyed by outcome id, each with buy[] and sell[] |
| listMarkets / listGames | sport?, league?, status? / sport, league | markets[] or games[] |
| getMarketIds | status?, sport?, league? | data[] with marketId and outcomes[].chainOutcomeId |
