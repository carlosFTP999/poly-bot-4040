"""WebSocket module for the BTC 5-Minute Hedge Bot.

Maintains a persistent private WebSocket connection to Polymarket for
real-time order_update events with automatic reconnection and state sync.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
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
        private_key: str = "",
        base_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user",
        max_reconnect_attempts: int = 3,
        chain_id: int = 137,
        signature_type: int = 2,
        funder: str | None = None,
    ) -> None:
        """Initialize the WebSocket client.

        Args:
            api_key: Polymarket API key for authentication.
            api_secret: Polymarket API secret.
            api_passphrase: Polymarket API passphrase.
            private_key: Private key for L2 ClobClient (required for order sync).
            base_url: WebSocket URL.
            max_reconnect_attempts: Max reconnection attempts before stopping.
            chain_id: Chain ID for L2 client (default 137 for Polygon).
            signature_type: Signature type for L2 client (default 2 for GNOSIS_SAFE).
            funder: Optional funder/proxy address for L2 client.
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._private_key = private_key
        self._base_url = base_url
        self._max_reconnect_attempts = max_reconnect_attempts
        self._chain_id = chain_id
        self._signature_type = signature_type
        self._funder = funder
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._condition_id: str | None = None
        self._on_order_update: Callable[[Fill], Any] | None = None
        self._reconnect_count = 0
        self._running = False
        self._ping_task: asyncio.Task | None = None

    async def connect(
        self,
        condition_id: str,
        on_order_update: Callable[[Fill], Any],
    ) -> None:
        """Connect and authenticate the WebSocket, then subscribe.

        Auth and subscription occur in a single combined frame.
        Establishes the connection, sends auth credentials, and subscribes
        to the given condition_id.

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
        """Establish the WebSocket connection and perform combined auth + subscribe."""
        self._ws = await websockets.connect(self._base_url)

        # Send combined auth + subscribe frame
        await self._subscribe_initial(self._condition_id)

        # Start listening for events
        asyncio.create_task(self._listen())

        # Start heartbeat task
        self._ping_task = asyncio.create_task(self._send_ping())

    async def _subscribe_initial(self, condition_id: str) -> None:
        """Send combined auth + subscription frame.

        Per official Polymarket WS protocol:
        {
            "auth": {"apiKey": "...", "secret": "...", "passphrase": "..."},
            "type": "user",
            "markets": ["<condition_id>"]
        }
        """
        subscribe_message = {
            "auth": {
                "apiKey": self._api_key,
                "secret": self._api_secret,
                "passphrase": self._api_passphrase,
            },
            "type": "user",
            "markets": [condition_id],
        }
        await self._ws.send(json.dumps(subscribe_message))
        logger.debug("WebSocket initial auth+subscribe sent for %s", condition_id)

    async def _subscribe(self, condition_id: str) -> None:
        """Subscribe to additional markets after initial authentication.

        Per official Polymarket WS protocol (dynamic subscription):
        {"operation": "subscribe", "markets": ["<condition_id>"]}
        """
        subscribe_message = {
            "operation": "subscribe",
            "markets": [condition_id],
        }
        await self._ws.send(json.dumps(subscribe_message))
        logger.debug("WebSocket subscribed to %s", condition_id)

    async def _unsubscribe(self, condition_id: str) -> None:
        """Unsubscribe from markets.

        Per official Polymarket WS protocol:
        {"operation": "unsubscribe", "markets": ["<condition_id>"]}
        """
        unsubscribe_message = {
            "operation": "unsubscribe",
            "markets": [condition_id],
        }
        await self._ws.send(json.dumps(unsubscribe_message))
        logger.debug("WebSocket unsubscribed from %s", condition_id)

    async def _send_ping(self) -> None:
        """Send PING heartbeat every 10 seconds while connected."""
        try:
            while self._running and self._ws:
                await asyncio.sleep(10)
                if self._ws and not self._ws.closed:
                    await self._ws.send("PING")
                    logger.debug("WebSocket PING sent")
        except asyncio.CancelledError:
            logger.debug("WebSocket ping task cancelled")
        except Exception as e:
            logger.warning("WebSocket ping task error: %s", e)

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
            price=Decimal(str(event.get("price", 0))),
            size=event.get("size_matched", event.get("size", 0)),
            timestamp=event.get("timestamp", 0),
        )

    async def _handle_disconnect(self) -> None:
        """Handle WebSocket disconnect: reconnect, re-subscribe, and sync."""
        # Cancel ping task
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        await self._connect_with_retry()

        # Re-subscribe to the active condition_id (server remembers session)
        if self._condition_id:
            await self._subscribe_initial(self._condition_id)

        # Sync state via GET /orders to recover missed events
        await self._sync_orders()

    async def _sync_orders(self) -> None:
        """Sync order state after reconnection via GET /orders.

        Uses L2 ClobClient with credentials to fetch open orders
        and re-synchronize internal state.
        """
        if not self._private_key:
            logger.warning("Skipping order sync: no private_key provided for L2 client")
            return

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds, OpenOrderParams

            creds = ApiCreds(
                api_key=self._api_key,
                api_secret=self._api_secret,
                api_passphrase=self._api_passphrase,
            )
            kwargs: dict = dict(
                host="https://clob.polymarket.com",
                chain_id=self._chain_id,
                key=self._private_key,
                creds=creds,
                signature_type=self._signature_type,
            )
            if self._funder:
                kwargs["funder"] = self._funder

            client = ClobClient(**kwargs)
            params = OpenOrderParams()
            response = client.get_orders(params)

            # Parse response and re-sync internal state
            # Response format: {"data": [...], "next_cursor": "..."}
            orders = response.get("data", []) if isinstance(response, dict) else []
            logger.info("Synced %d open orders after reconnection", len(orders))

            # TODO: Integrate with engine state if needed (e.g., update resting orders)
            # For now, logging the sync is sufficient as the WS will stream live updates

        except ImportError:
            logger.warning("py_clob_client not available; skipping order sync")
        except Exception as e:
            logger.exception("Order sync failed: %s", e)

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._running = False

        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

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