"""Executor module for the BTC 5-Minute Hedge Bot.

Defines the Executor Protocol with DryRunExecutor and LiveClobExecutor
implementations for dependency injection and testability.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from decimal import Decimal
from typing import Protocol, runtime_checkable

from src.types import Fill, Order, OrderStatus
from src.clock_sync import ClockSync
from py_clob_client.clob_types import OrderArgs
from py_clob_client.exceptions import PolyApiException

logger = logging.getLogger(__name__)


# Retry configuration constants
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1
RETRY_STATUS_CODES = {429, 425, 503}


async def _retry_with_backoff(
    func,
    *args,
    max_retries: int = MAX_RETRIES,
    base_backoff: float = BASE_BACKOFF_SECONDS,
    **kwargs,
):
    """Execute func with exponential backoff retry on specific HTTP status codes.

    Args:
        func: Async function to call.
        *args: Positional arguments for func.
        max_retries: Maximum number of retry attempts (default 3).
        base_backoff: Base backoff in seconds (default 1s). Sequence: 1s, 2s, 4s.
        **kwargs: Keyword arguments for func.

    Returns:
        Result of func call.

    Raises:
        Last exception if all retries exhausted.
    """
    last_exception = None
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except PolyApiException as e:
            last_exception = e
            status_code = getattr(e, "status_code", None)
            if status_code not in RETRY_STATUS_CODES:
                raise

            # Calculate backoff
            if status_code == 429:
                # Try to extract Retry-After from error message (headers not exposed in exception)
                retry_after = _extract_retry_after(getattr(e, "error_msg", ""))
                if retry_after is not None:
                    backoff = float(retry_after)
                else:
                    backoff = base_backoff * (2 ** attempt)
            else:
                # 425, 503: fixed exponential backoff
                backoff = base_backoff * (2 ** attempt)

            logger.warning(
                "Retry attempt %d/%d after %.1fs (status=%s): %s",
                attempt + 1,
                max_retries,
                backoff,
                status_code,
                e,
            )
            await asyncio.sleep(backoff)
        except Exception:
            # Non-retryable exception
            raise

    # All retries exhausted
    raise last_exception


def _extract_retry_after(error_msg: str | dict | None) -> float | None:
    """Extract Retry-After seconds from error message if present.

    The PolyApiException doesn't expose response headers directly,
    but the error message may contain header info.

    Args:
        error_msg: Error message from PolyApiException (can be str or dict).

    Returns:
        Retry-After seconds as float, or None if not found.
    """
    if error_msg is None:
        return None
    # Convert dict to string if needed
    if isinstance(error_msg, dict):
        error_msg = str(error_msg)

    # Try to find Retry-After in various formats
    patterns = [
        r'"Retry-After":\s*"?(\d+)"?',
        r"Retry-After:\s*(\d+)",
        r"retry-after[=:]\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, error_msg, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


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

    async def place_limit_orders_batch(
        self, order_args_list: list[OrderArgs]
    ) -> list[Fill]:
        """Place multiple GTC limit orders in a single batch request.

        Args:
            order_args_list: List of OrderArgs to place as a batch.

        Returns:
            List of Fill objects representing the placed orders.
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

    async def place_limit_orders_batch(
        self, order_args_list: list[OrderArgs]
    ) -> list[Fill]:
        """Simulate batch order placement by looping internally.

        This maintains compatibility with the Executor protocol while
        avoiding real network calls in dry-run mode.

        Args:
            order_args_list: List of OrderArgs to simulate placing.

        Returns:
            List of simulated Fill objects.
        """
        fills: list[Fill] = []
        for args in order_args_list:
            fill = await self.place_limit_order(
                token_id=args.token_id,
                side=args.side,
                price=Decimal(str(args.price)),
                size=int(args.size),
            )
            fills.append(fill)
        return fills


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
        chain_id: int = 137,
        signature_type: int = 2,
        funder: str | None = None,
    ) -> None:
        """Construct LiveClobExecutor.

        Args:
            api_key: Polymarket API key.
            api_secret: Polymarket API secret.
            api_passphrase: Polymarket API passphrase.
            private_key: Private key for signing orders.
            base_url: CLOB API base URL.
            chain_id: Chain ID (default 137 for Polygon).
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
        self._chain_id = chain_id
        self._signature_type = signature_type
        self._funder = funder
        self._client = self._build_client()
        if self._client is not None:
            self._clock = ClockSync(base_url=base_url)

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
        """Build the py_clob_client instance (L2: signer + ApiCreds)."""
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        creds = ApiCreds(
            api_key=self._api_key,
            api_secret=self._api_secret,
            api_passphrase=self._api_passphrase,
        )
        kwargs: dict = dict(
            host=self._base_url,
            chain_id=self._chain_id,
            key=self._private_key,
            creds=creds,
            signature_type=self._signature_type,
        )
        if self._funder:
            kwargs["funder"] = self._funder
        try:
            return ClobClient(**kwargs)
        except ImportError:
            raise
        except Exception as e:
            # Allow construction with dummy keys in tests (e.g. "priv")
            # but don't hide real failures silently - log and keep None
            # so tests can patch _client. Real invalid keys still fail on place.
            logger.warning("ClobClient build failed (key invalid?): %s", e)
            return None

    async def _post_with_clock_retry(self, fn):
        """Execute fn; on 401 timestamp error, re-sync clock and retry once."""
        try:
            return await fn()
        except Exception as e:
            if "expired" in str(e).lower() or "timestamp" in str(e).lower():
                logger.warning("Timestamp rejected — re-syncing clock")
                self._clock.force_recalibrate()
                return await fn()  # One retry
            raise

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

        from py_clob_client.clob_types import OrderArgs, OrderType

        order_args = OrderArgs(
            token_id=token_id,
            price=float(price),
            size=float(size),
            side=side,
        )

        async def _post_single_order():
            signed_order = self._client.create_order(order_args)
            # D6: Explicit orderType=GTC
            self._client.post_order(signed_order, orderType=OrderType.GTC)

        # D5+D10: Retry with backoff; on timestamp errors, re-sync clock and retry once
        async def _post_with_retry_and_sync():
            await _retry_with_backoff(_post_single_order)

        await self._post_with_clock_retry(_post_with_retry_and_sync)

        return Fill(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            timestamp=0,
        )

    async def place_limit_orders_batch(
        self, order_args_list: list[OrderArgs]
    ) -> list[Fill]:
        """Place multiple GTC limit orders in a single batch POST /orders request.

        Uses py-clob-client 0.34.6's post_orders endpoint to send all orders
        in one HTTP request instead of 10 individual calls.
        Includes exponential backoff retry (D5).

        Args:
            order_args_list: List of OrderArgs to place as a batch.

        Returns:
            List of Fill objects representing the placed orders.
        """
        if self._client is None:
            raise RuntimeError("ClobClient not initialized; cannot place orders")

        from py_clob_client.clob_types import PostOrdersArgs, OrderType

        # Validate all sizes first
        for args in order_args_list:
            if int(args.size) < 5:
                raise ValueError(f"Order size {args.size} is below minimum of 5 shares.")

        # Create signed orders for each OrderArgs
        signed_orders = [
            self._client.create_order(args) for args in order_args_list
        ]

        # Build PostOrdersArgs for batch request (D6: explicit orderType=GTC)
        post_args = [
            PostOrdersArgs(order=signed, orderType=OrderType.GTC, postOnly=False)
            for signed in signed_orders
        ]

        # D5+D10: Retry batch POST /orders with backoff and clock sync
        async def _post_batch():
            self._client.post_orders(post_args)

        async def _post_batch_with_retry_and_sync():
            await _retry_with_backoff(_post_batch)

        await self._post_with_clock_retry(_post_batch_with_retry_and_sync)

        # Build Fill objects from the original order args
        fills = [
            Fill(
                token_id=args.token_id,
                side=args.side,
                price=Decimal(str(args.price)),
                size=int(args.size),
                timestamp=0,
            )
            for args in order_args_list
        ]
        return fills

    async def cancel_all(self) -> None:
        """Cancel all pending orders via DELETE /cancel-all."""
        if self._client is None:
            raise RuntimeError("ClobClient not initialized; cannot cancel_all")
        try:
            self._client.cancel_all()
        except Exception:
            logger.exception("LiveClobExecutor cancel_all failed")
            raise
        logger.info("LiveClobExecutor cancelled all orders")

    async def get_balance_allowance(self) -> Decimal:
        """Check pUSD balance via get_balance_allowance on L2 CLOB.

        Uses BalanceAllowanceParams(asset_type=COLLATERAL, signature_type=...).

        Returns:
            Available pUSD balance as Decimal.
        """
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        if self._client is None:
            raise RuntimeError("ClobClient not initialized; cannot check balance")
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=self._signature_type,
        )
        response = self._client.get_balance_allowance(params)
        bal = response.get("balance", "0") if isinstance(response, dict) else getattr(response, "balance", "0")
        return Decimal(str(bal))


class PaperLiveExecutor:
    """Paper trading executor with LIVE infrastructure (real L2 client, real balance, real WS).

    Uses real credentials to build a full L2 ClobClient for:
    - Real balance checks via get_balance_allowance
    - Real WebSocket connections
    - Real Gamma market discovery

    But DOES NOT post real orders: place_limit_order and cancel_all only LOG
    what they would do and return simulated fills/counts. Zero financial risk.

    This is the PAPER_LIVE mode: LIVE_ENABLED=True + DRY_RUN=True.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        private_key: str,
        base_url: str = "https://clob.polymarket.com",
        chain_id: int = 137,
        signature_type: int = 2,
        funder: str | None = None,
    ) -> None:
        """Construct PaperLiveExecutor with real L2 credentials.

        Args:
            api_key: Polymarket API key.
            api_secret: Polymarket API secret.
            api_passphrase: Polymarket API passphrase.
            private_key: Private key for signing.
            base_url: CLOB API base URL.
            chain_id: Chain ID (default 137 for Polygon).
            signature_type: Signature type (default 2 for GNOSIS_SAFE).
            funder: Optional funder/proxy address.

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
        self._chain_id = chain_id
        self._signature_type = signature_type
        self._funder = funder
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
                f"PaperLiveExecutor requires: {', '.join(missing)}"
            )

    def _build_client(self):
        """Build the real py_clob_client instance (L2: signer + ApiCreds)."""
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        creds = ApiCreds(
            api_key=self._api_key,
            api_secret=self._api_secret,
            api_passphrase=self._api_passphrase,
        )
        kwargs: dict = dict(
            host=self._base_url,
            chain_id=self._chain_id,
            key=self._private_key,
            creds=creds,
            signature_type=self._signature_type,
        )
        if self._funder:
            kwargs["funder"] = self._funder
        try:
            return ClobClient(**kwargs)
        except ImportError:
            raise
        except Exception as e:
            logger.warning("ClobClient build failed (key invalid?): %s", e)
            return None

    async def place_limit_order(
        self, token_id: str, side: str, price: Decimal, size: int
    ) -> Fill:
        """LOG what would be placed, return simulated Fill (NO post_order call).

        Args:
            token_id: The token to trade.
            side: Order side ("BUY").
            price: Limit price as Decimal.
            size: Number of shares (must be >= 5).

        Returns:
            Simulated Fill with requested price and size.
        """
        if size < 5:
            raise ValueError(f"Order size {size} is below minimum of 5 shares.")

        logger.info("PAPER_LIVE: would place %s %s @ %s size=%s", side, token_id, price, size)

        return Fill(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            timestamp=0,
        )

    async def place_limit_orders_batch(
        self, order_args_list: list[OrderArgs]
    ) -> list[Fill]:
        """Simulate batch order placement by looping internally.

        PaperLiveExecutor logs what would be placed but does NOT call
        the real batch endpoint. Returns simulated fills.

        Args:
            order_args_list: List of OrderArgs to simulate placing.

        Returns:
            List of simulated Fill objects.
        """
        fills: list[Fill] = []
        for args in order_args_list:
            logger.info(
                "PAPER_LIVE: would place %s %s @ %s size=%s",
                args.side, args.token_id, args.price, args.size,
            )
            fill = Fill(
                token_id=args.token_id,
                side=args.side,
                price=Decimal(str(args.price)),
                size=int(args.size),
                timestamp=0,
            )
            fills.append(fill)
        return fills

    async def cancel_all(self) -> int:
        """LOG what would be cancelled, return count (NO cancel_all call).

        Returns:
            Number of orders that would have been cancelled.
        """
        logger.info("PAPER_LIVE: would cancel all")
        return 0  # No resting orders tracked in paper-live mode

    async def get_balance_allowance(self) -> Decimal:
        """Check pUSD balance via REAL L2 client.get_balance_allowance.

        Uses BalanceAllowanceParams(asset_type=COLLATERAL, signature_type=...).

        Returns:
            Available pUSD balance as Decimal from real CLOB.
        """
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        if self._client is None:
            raise RuntimeError("ClobClient not initialized; cannot check balance")
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=self._signature_type,
        )
        response = self._client.get_balance_allowance(params)
        bal = response.get("balance", "0") if isinstance(response, dict) else getattr(response, "balance", "0")
        return Decimal(str(bal))
