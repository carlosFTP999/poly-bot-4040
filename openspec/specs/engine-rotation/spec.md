# Engine Rotation Specification

## Purpose

Orchestrate the 300-second window lifecycle: discover → connect WS → place orders (Phase 1) → wait → cancel-all (Phase 2) → rotate. The engine is the central coordinator that drives the bot through each trading window.

## Requirements

### Requirement: Phase 1 Order Dispatch

Phase 1 MUST send 10 GTC limit orders in a single batch via the Executor. The engine SHALL call `executor.place_limit_order()` for each of the 10 orders (5 YES + 5 NO at $0.40).

#### Scenario: Full Phase 1 execution

- GIVEN a valid market with condition_id "abc123"
- WHEN Phase 1 starts
- THEN 10 orders are placed via the executor and the engine enters wait state

### Requirement: Mandatory Window Wait

After Phase 1, the engine MUST wait until the current 300-second window expires. The engine SHALL NOT proceed to Phase 2 early regardless of fill status. The wait is calculated as `window_end - now` where `window_end = window_ts + 300`.

#### Scenario: Wait full 300s

- GIVEN Phase 1 completes at t=0s of a 300s window
- WHEN the engine computes remaining wait time
- THEN it waits until `window_ts + 300` (full 300 seconds from window start)

#### Scenario: Partial fills do not shorten wait

- GIVEN 3 of 10 orders are filled at t=60s
- WHEN the engine checks fill count
- THEN it still waits until window expiry (does not proceed early)

### Requirement: Phase 2 Cancel-All

At window expiry, Phase 2 MUST execute `executor.cancel_all()` to cancel ALL pending/unfilled GTC orders via `DELETE /cancel-all`.

#### Scenario: Cancel pending orders

- GIVEN 7 of 10 orders remain unfilled at window end
- WHEN Phase 2 executes
- THEN `cancel_all()` is called and all pending orders are cancelled

### Requirement: Window Rotation

After Phase 2 completes, the engine MUST advance the window by +300s and restart the cycle (discover → Phase 1 → wait → Phase 2).

#### Scenario: Successful rotation

- GIVEN Phase 2 completes for window 1694000400
- WHEN rotation occurs
- THEN the engine computes next window 1694000700 and begins discovery

### Requirement: Late Entry Guard (MAX_LATE_S)

If the engine enters a window more than `MAX_LATE_S = 15s` after `window_ts`, it MUST skip that window and rotate to `window_ts + 300` without placing orders.

#### Scenario: Late entry skip

- GIVEN `now - window_ts = 20s` and `MAX_LATE_S = 15s`
- WHEN the engine evaluates entry
- THEN it logs a warning and rotates without Phase 1

#### Scenario: On-time entry

- GIVEN `now - window_ts = 10s`
- WHEN the engine evaluates entry
- THEN Phase 1 proceeds normally

### Requirement: Balance Check Before Phase 1

Before placing orders, the engine MUST verify pUSD balance >= `TOTAL_CAP` (20.00) via L2 `ClobClient.get_balance_allowance(BalanceAllowanceParams(asset_type=COLLATERAL, signature_type=...))` (`GET /balance-allowance` with `asset_type=pUSD`). If balance is insufficient, the engine MUST skip the window and rotate. In `DRY_RUN` the check is skipped (assumed sufficient).

#### Scenario: Sufficient balance

- GIVEN pUSD balance is $25.00 (>= TOTAL_CAP 20.00)
- WHEN the engine checks balance before Phase 1
- THEN Phase 1 proceeds with order placement

#### Scenario: Insufficient balance

- GIVEN pUSD balance is $15.00 (< TOTAL_CAP 20.00)
- WHEN the engine checks balance before Phase 1
- THEN Phase 1 is skipped and the engine rotates to the next window

### Requirement: Injected Dependencies

The engine MUST accept injectable `executor`, `discover`, `ws_connect`, `now`, and `balance_check` callables. This enables unit testing the full lifecycle without network calls.

#### Scenario: Engine test with mocks

- GIVEN mock executor, discover, ws_connect, now, and balance_check callables
- WHEN the engine runs a full cycle
- THEN no real API calls are made and the lifecycle completes deterministically
