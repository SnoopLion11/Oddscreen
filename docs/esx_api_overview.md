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

## Missing (needed before the ESX connector is written)

1. Real-money REST base URL and market-data WebSocket URL (and how they differ from paper).
2. How the login token is obtained, how long it lasts, how it is refreshed.
3. Whether the market-data WebSocket needs the API key and/or login token.
4. WebSocket subscribe message, update format (snapshot vs incremental), heartbeat/ping.
5. Whether order book / WebSocket prices include size at each price (liquidity guard).
6. Rate limits.
7. Fee structure: flat per contract vs % of winnings, and how much.
