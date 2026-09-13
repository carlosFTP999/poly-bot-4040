# WebSocket Monitoring Specification

## Purpose

Maintain a persistent private WebSocket connection to Polymarket (`WS_URL`, default `wss://ws-subscriptions-clob.polymarket.com/ws/user`) for real-time `order_update` events (~88ms latency), with automatic reconnection, combined auth+subscribe frame via `_subscribe_initial()`, PING/PONG heartbeat every 10s, and order state synchronization via L2 ClobClient (`_sync_orders()`).

## Requirements

### Requirement: WebSocket URL Configuration

`WS_URL` SHALL be configurable via environment (`WS_URL`, default `wss://ws-subscriptions-clob.polymarket.com/ws/user`) and consumed by `PrivateWebSocket` / `connect_websocket`. `LOG_LEVEL` (default `INFO`) controls `logging.basicConfig` level.

### Requirement: Private WebSocket Authentication

The system SHALL authenticate the WebSocket connection using a combined auth+subscribe frame via `_subscribe_initial()`. The auth payload uses `{"auth": {"apiKey": "...", "secret": "...", "passphrase": "..."}, "type": "user", "markets": ["<condition_id>"]}`. Auth MUST occur in the same frame as the initial market subscription. Connection URL is `WS_URL`.

#### Scenario: Successful auth

- GIVEN valid Polymarket API credentials
- WHEN the WebSocket connects
- THEN the combined auth+subscribe frame is sent and the connection is authenticated

#### Scenario: Auth failure

- GIVEN invalid credentials
- WHEN the WebSocket connects and authenticates
- THEN the system logs an error and does not attempt order placement

### Requirement: Market Subscription

The system SHALL subscribe to the `condition_id` returned by market discovery. Both YES and NO tokens of the same market share one `condition_id` — subscribing to it receives events for both sides.

#### Scenario: Subscribe before placing orders

- GIVEN a discovered `condition_id` "abc123"
- WHEN the WebSocket sends `{"auth": {...}, "type": "user", "markets": ["abc123"]}`
- THEN subsequent `order_update` events for both YES and NO tokens of that market are received

### Requirement: PING/PONG Heartbeat

The system MUST send a PING message every 10 seconds to keep the WebSocket connection alive. The heartbeat task is started on connect and cancelled on close.

### Requirement: Order Update Event Processing

The system MUST process `order_update` events with `type` values: "PLACEMENT", "UPDATE", "CANCELLATION" and `status` values: "LIVE", "MATCHED", "DELAYED", "UNMATCHED", "CANCELED". Each event MUST update internal fill state.

#### Scenario: Fill received

- GIVEN an active WebSocket subscription for condition_id "abc123"
- WHEN an `order_update` event arrives with `status: "MATCHED"` and `size_matched: 5`
- THEN the fill is recorded with `size=5` at the order's limit price

#### Scenario: Cancellation event

- GIVEN an active WebSocket subscription
- WHEN an `order_update` event arrives with `type: "CANCELLATION"` and `status: "CANCELED"`
- THEN the order is marked as cancelled in internal state

### Requirement: Reconnection with Re-subscribe

On WebSocket disconnect, the system SHALL automatically reconnect and re-subscribe via `_subscribe_initial()`. After reconnection, the system MUST sync state via `_sync_orders()` using L2 ClobClient (`GET /orders` with API credentials) to recover any events missed during the disconnect.

#### Scenario: Disconnect and recover

- GIVEN an active WebSocket connection with subscription to "abc123"
- WHEN the WebSocket connection drops
- THEN the system reconnects, re-subscribes via `_subscribe_initial()`, and calls `_sync_orders()` to sync state

#### Scenario: Reconnect failure

- GIVEN reconnection fails after 3 attempts
- WHEN the system cannot re-establish the WebSocket
- THEN the system logs an error and stops (does not proceed to Phase 2)

### Requirement: Connection Lifecycle per Window

The WebSocket connection MUST be established AFTER market discovery succeeds and BEFORE Phase 1 order placement. The connection is torn down at window rotation.

#### Scenario: Window lifecycle

- GIVEN a new window starts
- WHEN market discovery returns a valid market
- THEN the WebSocket connects and subscribes before any orders are placed
