# Market Discovery Specification

## Purpose

Discover the active BTC 5-minute UP/DOWN market on Polymarket via the Gamma API, aligned to 300-second time windows. Extract the condition_id and token IDs required for order placement and WebSocket subscription.

## Requirements

### Requirement: Window-Aligned Timestamp Calculation

The system MUST compute the current trading window timestamp as `(now // 300) * 300` where `now` is a Unix epoch in seconds.

#### Scenario: Exact window boundary

- GIVEN current Unix time is 1694000447
- WHEN `current_window_ts(1694000447)` is called
- THEN the result is 1694000400

#### Scenario: Midnight-aligned window

- GIVEN current Unix time is 1694000400 (exact multiple of 300)
- WHEN `current_window_ts(1694000400)` is called
- THEN the result is 1694000400 (unchanged)

### Requirement: Gamma API Market Discovery

The system SHALL query the Gamma API at `{GAMMA_BASE_URL}/markets` with slug filter `btc-updown-5m-{window_ts}` to locate the active BTC 5-min market.

#### Scenario: Market found

- GIVEN a valid window timestamp 1694000400
- WHEN `discover()` is called
- THEN the system returns a Market object containing `condition_id`, `token_yes_id`, `token_no_id`, `start_date`, and `end_date`

#### Scenario: Market not found

- GIVEN a valid window timestamp with no matching Gamma market
- WHEN `discover()` is called
- THEN the system returns `None`

### Requirement: Token ID Extraction

The system MUST extract both YES and NO token IDs from the Gamma API response's `tokens` array. Each token has an `outcome` ("Yes" or "No") and a `token_id`.

#### Scenario: Both tokens present

- GIVEN Gamma API returns a market with two tokens (Yes, No)
- WHEN tokens are extracted
- THEN `token_yes_id` matches the "Yes" outcome and `token_no_id` matches the "No" outcome

### Requirement: Rotation on Missing Market

When `discover()` returns `None`, the system MUST advance the window by +300s and retry discovery for the next window.

#### Scenario: Skip to next window

- GIVEN `discover()` returns `None` for window 1694000400
- WHEN rotation occurs
- THEN the system attempts discovery for window 1694000700

### Requirement: Injected HTTP Client

The `discover()` function MUST accept an injectable HTTP client callable for testability. The client callable returns parsed JSON.

#### Scenario: Mock client in tests

- GIVEN a mock client returning a fixture market JSON
- WHEN `discover()` is called with the mock client
- THEN no real HTTP request is made and the fixture data is returned
