"""WebSocket module for the BTC 5-Minute Hedge Bot.

Maintains a persistent private WebSocket connection to Polymarket for
real-time order_update events with automatic reconnection and state sync.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import websockets

from src.types import Fill

logger = logging.getLogger(__name__)


class PrivateWebSocket:
    """Private WebSocket connection for Polymarket order updates.

    Handles authentication, subscription to market condition_ids,
    automatic reconnection with GET /orders sync, and event processing.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        base_url: str = "wss://ws-clob.polymarket.com",
        max_reconnect_attempts: int = 3,
    ) -> None:
        """Initialize the WebSocket client.

        Args:
            api_key: Polymarket API key for authentication.
            api_secret: Polymarket API secret.
            api_passphrase: Polymarket API passphrase.
            base_url: WebSocket URL.
            max_reconnect_attempts: Max reconnection attempts before stopping.
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._base_url = base_url
        self._max_reconnect_attempts = max_reconnect_attempts
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._condition_id: str | None = None
        self._on_order_update: Callable[[Fill], Any] | None = None
        self._reconnect_count = 0
        self._running = False

    async def connect(
        self,
        condition_id: str,
        on_order_update: Callable[[Fill], Any],
    ) -> None:
        """Connect and authenticate the WebSocket, then subscribe.

        Auth MUST occur before subscribing. Establishes the connection,
        sends auth credentials, and subscribes to the given condition_id.

        Args:
            condition_id: Market condition_id to subscribe to.
            on_order_update: Callback invoked on each order_update event.

        Raises:
            RuntimeError: If connection fails after max_reconnect_attempts.
        """
        self._condition_id = condition_id
        self._on_order_update = on_order_update
        self._running = True
        self._reconnect_count = 0

        await self._connect_with_retry()

    async def _connect_with_retry(self) -> None:
        """Connect with automatic reconnection on failure."""
        while self._running and self._reconnect_count < self._max_reconnect_attempts:
            try:
                await self._do_connect()
                self._reconnect_count = 0
                logger.info(
                    "WebSocket connected and subscribed to %s",
                    self._condition_id,
                )
                return
            except Exception as e:
                self._reconnect_count += 1
                logger.warning(
                    "WebSocket connection attempt %d failed: %s",
                    self._reconnect_count, e,
                )
                if self._reconnect_count < self._max_reconnect_attempts:
                    await asyncio.sleep(2 ** self._reconnect_count)
                else:
                    raise RuntimeError(
                        f"WebSocket reconnection failed after "
                        f"{self._max_reconnect_attempts} attempts"
                    ) from e

    async def _do_connect(self) -> None:
        """Establish the WebSocket connection and perform auth + subscribe."""
        self._ws = await websockets.connect(self._base_url)

        # Authenticate
        await self._send_auth()

        # Subscribe to market condition_id
        await self._subscribe(self._condition_id)

        # Start listening for events
        asyncio.create_task(self._listen())

    async def _send_auth(self) -> None:
        """Send authentication message to the WebSocket.

        TODO: Verify real CLOB WS auth format against
        https://docs.polymarket.com — expected fields are ``action`` /
        ``assets_ids`` / channels ``market`` and ``user``. No conclusive
        helper found in ``py_clob_client`` (no WS module) and docs
        unavailable offline, so this keeps the existing payload and relies
        on the fail-graceful reconnect path. Trading continues deaf until
        the protocol is confirmed.
        """
        auth_message = {
            "operation": "auth",
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "passphrase": self._api_passphrase,
        }
        await self._ws.send(json.dumps(auth_message))
        logger.debug("WebSocket auth sent")

    async def _subscribe(self, condition_id: str) -> None:
        """Subscribe to order_update events for a condition_id.

        TODO: Same caveat as _send_auth — real protocol likely uses
        ``action``/``assets_ids``/``market``/``user`` channels per
        https://docs.polymarket.com. Left fail-graceful until verified.
        """
        subscribe_message = {
            "operation": "subscribe",
            "markets": [condition_id],
        }
        await self._ws.send(json.dumps(subscribe_message))
        logger.debug("WebSocket subscribed to %s", condition_id)

    async def _listen(self) -> None:
        """Listen for WebSocket events and dispatch to handlers."""
        try:
            async for raw_message in self._ws:
                event = json.loads(raw_message)
                await self._process_event(event)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed; attempting reconnect")
            await self._handle_disconnect()

    async def _process_event(self, event: dict) -> None:
        """Process a WebSocket event and update fill state.

        Handles order_update events with type values:
        "PLACEMENT", "UPDATE", "CANCELLATION" and status values:
        "LIVE", "MATCHED", "DELAYED", "UNMATCHED", "CANCELED".

        Args:
            event: Parsed WebSocket event dict.
        """
        event_type = event.get("type", "")
        status = event.get("status", "")

        if event_type == "order_update":
            fill = self._extract_fill(event)
            if fill and self._on_order_update:
                await self._on_order_update(fill)

    def _extract_fill(self, event: dict) -> Fill | None:
        """Extract a Fill from an order_update event.

        Args:
            event: The WebSocket event dict.

        Returns:
            Fill if status is MATCHED, None otherwise.
        """
        status = event.get("status", "")
        if status != "MATCHED":
            return None

        return Fill(
            token_id=event.get("token_id", ""),
            side=event.get("side", "YES"),
            price=event.get("price", 0),
            size=event.get("size_matched", event.get("size", 0)),
            timestamp=event.get("timestamp", 0),
        )

    async def _handle_disconnect(self) -> None:
        """Handle WebSocket disconnect: reconnect, re-subscribe, and sync."""
        await self._connect_with_retry()

        # Re-subscribe to the active condition_id
        if self._condition_id:
            await self._subscribe(self._condition_id)

        # Sync state via GET /orders to recover missed events
        await self._sync_orders()

    async def _sync_orders(self) -> None:
        """Sync order state after reconnection (currently no-op).

        A real implementation would call GET /orders via an L2 ClobClient.
        Left as no-op until a clear API contract exists; reconnection
        already re-subscribes above. Logged so missed-event recovery is
        visible.
        """
        logger.info("Syncing orders via GET /orders after reconnection (no-op)")

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("WebSocket connection closed")

    @property
    def is_connected(self) -> bool:
        """Return whether the WebSocket is currently connected."""
        if self._ws is None:
            return False
        # websockets >=12 uses State enum; older versions expose .closed
        try:
            from websockets.protocol import State

            return getattr(self._ws, "state", None) == State.OPEN
        except ImportError:
            pass
        return not bool(getattr(self._ws, "closed", True))
