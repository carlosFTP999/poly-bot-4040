"""Executor module for the BTC 5-Minute Hedge Bot.

Defines the Executor Protocol with DryRunExecutor and LiveClobExecutor
implementations for dependency injection and testability.
"""

from __future__ import annotations

import logging
from collections import deque
from decimal import Decimal
from typing import Protocol, runtime_checkable

from src.types import Fill, Order, OrderStatus

logger = logging.getLogger(__name__)


@runtime_checkable
class Executor(Protocol):
    """Protocol for order execution strategies.

    All engine code depends on this protocol, never on concrete implementations.
    Enables deterministic unit testing via DryRunExecutor and live trading
    via LiveClobExecutor.
    """

    async def place_limit_order(
        self, token_id: str, side: str, price: Decimal, size: int
    ) -> Fill:
        """Place a GTC limit order.

        Args:
            token_id: The token to trade (YES or NO token ID).
            side: Order side, always "BUY".
            price: Limit price as Decimal.
            size: Number of shares (must be >= SHARE_FLOOR).

        Returns:
            A Fill representing the executed order.
        """
        ...

    async def cancel_all(self) -> None:
        """Cancel all pending/unfilled orders via DELETE /cancel-all."""
        ...


class DryRunExecutor:
    """Deterministic, network-free executor for paper trading.

    Records all actions that would be taken in live mode without
    making any API calls. Returns simulated fills immediately at
    the requested price.
    """

    def __init__(self) -> None:
        self._resting: deque[Order] = deque()
        self.cancelled_count: int = 0
        self._fill_log: list[Fill] = []

    async def place_limit_order(
        self, token_id: str, side: str, price: Decimal, size: int
    ) -> Fill:
        """Return a simulated Fill immediately at the requested price.

        Stores the order in _resting for tracking, then returns a Fill.

        Args:
            token_id: The token to trade.
            side: Order side ("BUY").
            price: Limit price.
            size: Number of shares (must be >= 5).

        Returns:
            Fill with the requested price and size.
        """
        if size < 5:
            raise ValueError(f"Order size {size} is below minimum of 5 shares.")

        order = Order(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            order_type="GTC",
            expiration=0,
            status=OrderStatus.LIVE,
        )
        self._resting.append(order)

        fill = Fill(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            timestamp=0,  # Simulated; real timestamp from clock
        )
        self._fill_log.append(fill)
        return fill

    async def cancel_all(self) -> None:
        """Clear all resting orders and increment the cancelled counter."""
        count = len(self._resting)
        self._resting.clear()
        self.cancelled_count += count
        logger.info("DryRunExecutor cancelled %d orders", count)


class LiveClobExecutor:
    """Live executor using py-sdk (py_clob_client) for real order placement.

    Requires all API credentials to be present. Fails construction if
    LIVE_ENABLED is True but keys are missing.

    Uses py_clob_client for:
    - POST /orders (batch order placement)
    - DELETE /cancel-all (cancel all pending orders)
    - GET /balance-allowance (balance check)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        private_key: str,
        base_url: str = "https://clob.polymarket.com",
        signature_type: int = 2,
    ) -> None:
        """Construct LiveClobExecutor.

        Args:
            api_key: Polymarket API key.
            api_secret: Polymarket API secret.
            api_passphrase: Polymarket API passphrase.
            private_key: Private key for signing orders.
            base_url: CLOB API base URL.
            signature_type: Signature type (default 2 for GNOSIS_SAFE / browser wallet).

        Raises:
            ValueError: If any required credential is missing.
        """
        self._validate_credentials(
            api_key, api_secret, api_passphrase, private_key
        )
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._private_key = private_key
        self._base_url = base_url
        self._signature_type = signature_type
        self._client = self._build_client()

    def _validate_credentials(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        private_key: str,
    ) -> None:
        """Fail fast if any required credential is missing."""
        missing = []
        if not api_key:
            missing.append("POLYMARKET_API_KEY")
        if not api_secret:
            missing.append("POLYMARKET_API_SECRET")
        if not api_passphrase:
            missing.append("POLYMARKET_API_PASSPHRASE")
        if not private_key:
            missing.append("POLYMARKET_PRIVATE_KEY")
        if missing:
            raise ValueError(
                f"LiveClobExecutor requires: {', '.join(missing)}"
            )

    def _build_client(self):
        """Build the py_clob_client instance."""
        try:
            from py_clob_client.client import ClobClient

            return ClobClient(
                host=self._base_url,
                chain_id=137,
                key=self._private_key,
                signature_type=self._signature_type,
            )
        except Exception:
            logger.warning("py_clob_client not available or key invalid; LiveClobExecutor "
                           "constructed but may not function.")
            return None

    async def place_limit_order(
        self, token_id: str, side: str, price: Decimal, size: int
    ) -> Fill:
        """Place a GTC limit order via the CLOB API v2.

        Args:
            token_id: The token to trade.
            side: Order side ("BUY").
            price: Limit price as Decimal.
            size: Number of shares.

        Returns:
            Fill representing the placed order.
        """
        if size < 5:
            raise ValueError(f"Order size {size} is below minimum of 5 shares.")

        from py_clob_client import OrderArgs

        order_args = OrderArgs(
            token_id=token_id,
            price=float(price),
            size=float(size),
            side=side,
            expiration=0,
        )

        # Batch POST /orders with single order (engine batches 10 total)
        result = self._client.create_order(order_args)

        return Fill(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            timestamp=0,
        )

    async def cancel_all(self) -> None:
        """Cancel all pending orders via DELETE /cancel-all."""
        from py_clob_client.clob import ClobApi

        api = ClobApi(host=self._base_url, key=self._api_key)
        api.cancel_all(signature_type=self._signature_type)
        logger.info("LiveClobExecutor cancelled all orders")
