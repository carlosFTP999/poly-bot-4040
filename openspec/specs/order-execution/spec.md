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

Phase 1 SHALL send exactly 10 GTC limit orders in a single `POST /orders` batch request: 5 YES at $0.40 + 5 NO at $0.40. The batch MUST NOT exceed 15 orders (API maxItems constraint).

#### Scenario: Full batch of 10 orders

- GIVEN a market with valid token IDs for YES and NO
- WHEN Phase 1 executes
- THEN 10 orders are placed in one batch: 5 BUY YES at price 0.40, 5 BUY NO at price 0.40

#### Scenario: Batch size under API limit

- GIVEN the batch contains 10 orders
- WHEN the request is sent
- THEN the batch size (10) is strictly less than the API max (15)

### Requirement: GTC Order Type

All orders MUST be GTC (Good Till Cancelled) with `orderType: "GTC"` and `expiration: 0`. Orders SHALL NOT expire automatically.

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

### Requirement: LiveClobExecutor Key Validation

`LiveClobExecutor` MUST fail construction if `LIVE_ENABLED=true` but any required key (api_key, secret, passphrase, private_key) is missing.

#### Scenario: Missing keys in live mode

- GIVEN `LIVE_ENABLED=true` and `POLYMARKET_API_KEY` is unset
- WHEN `LiveClobExecutor` is constructed
- THEN a configuration error is raised before any API call
