# Engine Rotation Specification

## Purpose

Orchestrate the 300-second window lifecycle: discover → connect WS → place orders (Phase 1) → wait → cancel-all (Phase 2) → rotate. The engine is the central coordinator that drives the bot through each trading window.

## Requirements

### Requirement: Phase 1 Order Dispatch

Phase 1 MUST send 2 GTC limit orders in a single batch via the Executor. The engine SHALL call `executor.place_limit_order()` for each of the 2 orders (1 YES + 1 NO at $0.40), with retry on 429/425/503 (1s/2s/4s backoff).

#### Scenario: Full Phase 1 execution

- GIVEN a valid market with condition_id "abc123"
- WHEN Phase 1 starts
- THEN 2 orders are placed via the executor and the engine enters wait state

### Requirement: Mandatory Window Wait

After Phase 1, the engine MUST wait until the current 300-second window expires. The engine SHALL NOT proceed to Phase 2 early regardless of fill status. The wait is calculated as `window_end - now` where `window_end = window_ts + 300`.

#### Scenario: Wait full 300s

- GIVEN Phase 1 completes at t=0s of a 300s window
- WHEN the engine computes remaining wait time
- THEN it waits until `window_ts + 300` (full 300 seconds from window start)

#### Scenario: Partial fills do not shorten wait

- GIVEN 1 of 2 orders are filled at t=60s
- WHEN the engine checks fill count
- THEN it still waits until window expiry (does not proceed early)

### Requirement: Phase 2 Cancel-All

At window expiry, Phase 2 MUST execute `executor.cancel_all()` to cancel ALL pending/unfilled GTC orders via `DELETE /cancel-all`.

#### Scenario: Cancel pending orders

- GIVEN 1 of 2 orders remain unfilled at window end
- WHEN Phase 2 executes
- THEN `cancel_all()` is called and all pending orders are cancelled

### Requirement: Window Rotation

After Phase 2 completes, the engine MUST advance the window by +300s and restart the cycle (discover → Phase 1 → wait → Phase 2).

#### Scenario: Successful rotation

- GIVEN Phase 2 completes for window 1694000400
- WHEN rotation occurs
- THEN the engine computes next window 1694000700 and begins discovery

### Requirement: Balance Check Before Phase 1

Before placing orders, the engine MUST verify pUSD balance >= $4.00 via `GET /balance-allowance` with `asset_type=pUSD` and `signature_type=2`. If balance is insufficient, the engine MUST skip the window and rotate.

#### Scenario: Sufficient balance

- GIVEN pUSD balance is $5.00
- WHEN the engine checks balance before Phase 1
- THEN Phase 1 proceeds with order placement

#### Scenario: Insufficient balance

- GIVEN pUSD balance is $3.00
- WHEN the engine checks balance before Phase 1
- THEN Phase 1 is skipped and the engine rotates to the next window

### Requirement: Injected Dependencies

The engine MUST accept injectable `executor`, `discover`, `ws_connect`, `now`, and `balance_check` callables. This enables unit testing the full lifecycle without network calls.

#### Scenario: Engine test with mocks

- GIVEN mock executor, discover, ws_connect, now, and balance_check callables
- WHEN the engine runs a full cycle
- THEN no real API calls are made and the lifecycle completes deterministically
