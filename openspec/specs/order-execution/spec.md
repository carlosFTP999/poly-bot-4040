# Order Execution Specification

## Purpose

Place batch GTC limit orders via the Polymarket CLOB API v2 and manage order lifecycle through an Executor Protocol with DI for testability (DryRunExecutor / LiveClobExecutor).

## Requirements

### Requirement: Executor Protocol

The system SHALL define an `Executor` protocol with two methods: `place_limit_order(token_id, side, price, size)` and `cancel_all()`. All engine code MUST depend on this protocol, never on concrete implementations.

#### Scenario: Protocol compliance

- GIVEN a `DryRunExecutor` instance
- WHEN it is passed to the engine as an `Executor`
- THEN `place_limit_order` and `cancel_all` are callable without type errors

### Requirement: Batch Order Placement

Phase 1 SHALL send exactly 2 GTC limit orders in a single `POST /orders` batch request: 1 YES at $0.40 + 1 NO at $0.40. The batch includes retry with exponential backoff (1s/2s/4s) on 429/425/503.

#### Scenario: Full batch of 2 orders

- GIVEN a market with valid token IDs for YES and NO
- WHEN Phase 1 executes
- THEN 2 orders are placed in one batch: 1 BUY YES at price 0.40, 1 BUY NO at price 0.40

#### Scenario: Retry on rate limit

- GIVEN a batch POST returns 429
- WHEN the retry logic activates
- THEN the request is retried up to 3 times with 1s/2s/4s backoff

### Requirement: GTC Order Type

All orders MUST be GTC (Good Till Cancelled) with explicit `orderType=OrderType.GTC` and `expiration: 0`. Orders SHALL NOT expire automatically.

#### Scenario: GTC persistence

- GIVEN a GTC order is placed at price $0.40
- WHEN the order is not filled and not cancelled
- THEN the order remains active in the order book

### Requirement: Minimum Order Size

Every order MUST specify a `size` of at least 5 shares (Polymarket minimum for GTC/GTD).

#### Scenario: Size validation

- GIVEN `SHARE_FLOOR = 5`
- WHEN an order is constructed
- THEN `size >= 5` is guaranteed

### Requirement: DryRunExecutor Determinism

`DryRunExecutor.place_limit_order()` MUST return a `Fill` immediately at the requested price without any network call. `cancel_all()` MUST clear internal resting orders and increment a cancelled counter.

#### Scenario: Simulated fill

- GIVEN a `DryRunExecutor` with no prior state
- WHEN `place_limit_order(token_yes, "BUY", 0.40, 5)` is called
- THEN a `Fill` is returned with `price=0.40`, `size=5`, `token_id=token_yes`

#### Scenario: Cancel clears state

- GIVEN a `DryRunExecutor` with 3 resting orders
- WHEN `cancel_all()` is called
- THEN resting orders count is 0 and `cancelled_count` is 3

### Requirement: L2 Executor (LiveClobExecutor)

`LiveClobExecutor` MUST be an L2 executor built on `py_clob_client.ClobClient` with `host=CLOB_BASE_URL`, `chain_id=137`, `key=POLYMARKET_PRIVATE_KEY`, `creds=ApiCreds(...)`, `signature_type=SIGNATURE_TYPE` (default 2), and optional `funder=POLYMARKET_FUNDER` (or fallback to `POLYMARKET_PROXY_ADDRESS`). Order placement SHALL use `OrderArgs` + `create_order`/`post_order` with explicit `orderType=OrderType.GTC`; batch placement uses `post_orders(PostOrdersArgs)`. Cancel SHALL use `cancel_all()` (`DELETE /cancel-all`). Includes `_post_with_clock_retry()` for 401 timestamp recovery.

### Requirement: Retry with Exponential Backoff

All order placement and cancel operations MUST retry up to 3 times with exponential backoff (1s, 2s, 4s) on HTTP 429 (rate limit), 425 (too early), and 503 (service unavailable). On 429, Retry-After header is extracted when available.

### Requirement: Clock Synchronization

`LiveClobExecutor` MUST use `ClockSync` for offset-based clock synchronization with the CLOB `/time` endpoint. The clock recalibrates every 5 minutes. On 401 timestamp errors, `force_recalibrate()` is called and the operation is retried once.

### Requirement: LiveClobExecutor Key Validation

`LiveClobExecutor` MUST fail construction if `LIVE_ENABLED=true` but any required key (api_key, secret, passphrase, private_key) is missing.

#### Scenario: Missing keys in live mode

- GIVEN `LIVE_ENABLED=true` and `POLYMARKET_API_KEY` is unset
- WHEN `LiveClobExecutor` is constructed
- THEN a configuration error is raised before any API call
