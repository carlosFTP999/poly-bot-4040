"""Integration-style tests for LiveClobExecutor — all mocked, zero network.

Verifies that LiveClobExecutor:
- Constructs with correct credentials and builds ClobClient properly
- Builds OrderArgs with correct fields and GTC order type
- Calls post_order / post_orders / create_order / cancel_all / get_balance_allowance
  with the exact expected parameters
- Rejects missing keys at construction time
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, AsyncMock

from py_clob_client.clob_types import ApiCreds, OrderArgs, AssetType, BalanceAllowanceParams, PostOrdersArgs, OrderType
from py_clob_client.client import ClobClient

from src.executor import LiveClobExecutor


# ---------------------------------------------------------------------------
# Test 1 — Construction validates credentials and builds client
# ---------------------------------------------------------------------------

class TestLiveClobExecutorConstruction:
    """Test LiveClobExecutor constructor and internal client setup."""

    def test_live_executor_construction(self) -> None:
        """Construct with all credentials → _client not None and _client._creds == ApiCreds."""
        creds = ApiCreds(api_key="test", api_secret="test", api_passphrase="test")
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_instance = MagicMock()
            mock_instance._creds = creds
            mock_clob_cls.return_value = mock_instance

            executor = LiveClobExecutor(
                api_key="test",
                api_secret="test",
                api_passphrase="test",
                private_key="0x" + "a" * 64,
                chain_id=137,
                signature_type=2,
                funder="0x" + "b" * 40,
            )

            assert executor._client is not None
            assert executor._client._creds == ApiCreds("test", "test", "test")
            # Verify ClobClient was called with the right kwargs
            mock_clob_cls.assert_called_once()
            call_kwargs = mock_clob_cls.call_args.kwargs
            assert call_kwargs["chain_id"] == 137
            assert call_kwargs["signature_type"] == 2
            assert call_kwargs["funder"] == "0x" + "b" * 40
            assert call_kwargs["host"] == "https://clob.polymarket.com"
            assert call_kwargs["key"] == "0x" + "a" * 64
            assert call_kwargs["creds"] == creds

    def test_live_executor_rejects_missing_keys(self) -> None:
        """Constructor without private_key (empty) should raise ValueError."""
        with pytest.raises(ValueError, match="requires"):
            LiveClobExecutor(
                api_key="test",
                api_secret="test",
                api_passphrase="test",
                private_key="",
            )


# ---------------------------------------------------------------------------
# Test 2 — place_limit_order constructs correct OrderArgs
# ---------------------------------------------------------------------------

class TestPlaceLimitOrder:
    """Test that place_limit_order builds OrderArgs and calls post_order correctly."""

    @pytest.mark.asyncio
    async def test_place_limit_order_constructs_correct_args(self) -> None:
        """place_limit_order should build OrderArgs(token_id, price, size, side) and call post_order with order_type='GTC'."""
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client

            # create_order returns a mock signed_order
            signed_order = MagicMock()
            mock_client.create_order.return_value = signed_order

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            from decimal import Decimal
            result = await executor.place_limit_order(
                token_id="token_yes", side="BUY",
                price=Decimal("0.40"), size=5,
            )

            # Verify create_order was called with correct OrderArgs
            mock_client.create_order.assert_called_once()
            call_args = mock_client.create_order.call_args[0][0]
            assert isinstance(call_args, OrderArgs)
            assert call_args.token_id == "token_yes"
            assert call_args.price == 0.40
            assert call_args.size == 5.0
            assert call_args.side == "BUY"

            # Verify post_order was called with the signed_order and orderType='GTC'
            mock_client.post_order.assert_called_once()
            post_call_args = mock_client.post_order.call_args
            assert post_call_args[0][0] is signed_order
            # orderType defaults to OrderType.GTC which is 'GTC'
            if len(post_call_args[0]) > 1:
                assert post_call_args[0][1] == "GTC" or post_call_args[0][1].value == "GTC"
            # Verify result is a Fill
            assert result.token_id == "token_yes"
            assert result.price == Decimal("0.40")
            assert result.size == 5


# ---------------------------------------------------------------------------
# Test 3 — 10 sequential orders
# ---------------------------------------------------------------------------

class TestPlaceOrderSequence:
    """Test that multiple orders are placed sequentially (not batched)."""

    @pytest.mark.asyncio
    async def test_place_10_orders_sequence(self) -> None:
        """Call place_limit_order 10 times (5 YES + 5 NO) → post_order called exactly 10 times."""
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client
            mock_client.create_order.return_value = MagicMock()

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            for i in range(5):
                await executor.place_limit_order(
                    token_id=f"token_yes_{i}", side="BUY",
                    price=Decimal("0.40"), size=5,
                )
            for i in range(5):
                await executor.place_limit_order(
                    token_id=f"token_no_{i}", side="BUY",
                    price=Decimal("0.55"), size=5,
                )

            # post_order is called once per place_limit_order call
            assert mock_client.post_order.call_count == 10
            # create_order is also called 10 times
            assert mock_client.create_order.call_count == 10


# ---------------------------------------------------------------------------
# Test 4 — cancel_all uses the existing client
# ---------------------------------------------------------------------------

class TestCancelAll:
    """Test that cancel_all delegates to the existing client, not a new one."""

    @pytest.mark.asyncio
    async def test_cancel_all_uses_client(self) -> None:
        """cancel_all should call self._client.cancel_all(), not create a new client."""
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            await executor.cancel_all()

            # Verify cancel_all was called on the client instance, not the class
            mock_client.cancel_all.assert_called_once()
            # Ensure no new ClobClient instances were created during cancel_all
            assert mock_clob_cls.call_count == 1  # Only from __init__

    @pytest.mark.asyncio
    async def test_cancel_all_raises_if_no_client(self) -> None:
        """cancel_all should raise RuntimeError if _client is None."""
        executor = LiveClobExecutor.__new__(LiveClobExecutor)
        executor._client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            await executor.cancel_all()


# ---------------------------------------------------------------------------
# Test 5 — balance_check_uses_l2
# ---------------------------------------------------------------------------

class TestBalanceCheck:
    """Test that get_balance_allowance uses BalanceAllowanceParams with COLLATERAL."""

    @pytest.mark.asyncio
    async def test_balance_check_uses_l2(self) -> None:
        """get_balance_allowance should pass BalanceAllowanceParams(asset_type=COLLATERAL, signature_type=2) and return Decimal('100.00')."""
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client
            mock_client.get_balance_allowance.return_value = {
                "balance": "100.00",
                "allowance": "100.00",
            }

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            result = await executor.get_balance_allowance()

            # Verify get_balance_allowance was called with correct params
            mock_client.get_balance_allowance.assert_called_once()
            call_args = mock_client.get_balance_allowance.call_args[0][0]
            assert isinstance(call_args, BalanceAllowanceParams)
            assert call_args.asset_type == AssetType.COLLATERAL
            assert call_args.signature_type == 2

            assert result == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_balance_zero_returns_decimal(self) -> None:
        """When balance is '0', get_balance_allowance should return Decimal('0')."""
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client
            mock_client.get_balance_allowance.return_value = {"balance": "0"}

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            result = await executor.get_balance_allowance()
            assert result == Decimal("0")

    # ---------------------------------------------------------------------------
# Test 6 — batch order placement (post_orders)
# ---------------------------------------------------------------------------

class TestPlaceLimitOrdersBatch:
    """Test that place_limit_orders_batch uses post_orders for batch placement."""

    @pytest.mark.asyncio
    async def test_place_limit_orders_batch_calls_post_orders_once(self) -> None:
        """place_limit_orders_batch should call post_orders exactly once with 2 PostOrdersArgs."""
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client
            mock_client.create_order.side_effect = [
                MagicMock() for _ in range(2)
            ]

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            from py_clob_client.clob_types import OrderArgs
            from decimal import Decimal

            order_args_list = [
                OrderArgs(
                    token_id="token_yes",
                    price=0.40,
                    size=5.0,
                    side="BUY",
                ),
                OrderArgs(
                    token_id="token_no",
                    price=0.40,
                    size=5.0,
                    side="BUY",
                ),
            ]

            result = await executor.place_limit_orders_batch(order_args_list)

            # Verify post_orders was called exactly once
            mock_client.post_orders.assert_called_once()
            post_call_args = mock_client.post_orders.call_args[0][0]
            assert len(post_call_args) == 2

            # Verify each element is a PostOrdersArgs with correct fields
            from py_clob_client.clob_types import PostOrdersArgs, OrderType
            for post_arg in post_call_args:
                assert isinstance(post_arg, PostOrdersArgs)
                assert post_arg.orderType == OrderType.GTC
                assert post_arg.postOnly is False

            # Verify create_order was called 2 times (once per order)
            assert mock_client.create_order.call_count == 2

            # Verify result contains 2 Fill objects
            assert len(result) == 2
            assert result[0].token_id == "token_yes"
            assert result[0].price == Decimal("0.40")
            assert result[0].size == 5
            assert result[0].side == "BUY"
            assert result[1].token_id == "token_no"
            assert result[1].price == Decimal("0.40")
            assert result[1].size == 5
            assert result[1].side == "BUY"

    @pytest.mark.asyncio
    async def test_place_limit_orders_batch_raises_if_no_client(self) -> None:
        """place_limit_orders_batch should raise RuntimeError if _client is None."""
        executor = LiveClobExecutor.__new__(LiveClobExecutor)
        executor._client = None
        from py_clob_client.clob_types import OrderArgs
        with pytest.raises(RuntimeError, match="not initialized"):
            await executor.place_limit_orders_batch([OrderArgs(token_id="t", price=0.4, size=5.0, side="BUY")])

    @pytest.mark.asyncio
    async def test_place_limit_orders_batch_validates_size(self) -> None:
        """place_limit_orders_batch should reject orders with size < 5."""
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            from py_clob_client.clob_types import OrderArgs
            with pytest.raises(ValueError, match="below minimum of 5 shares"):
                await executor.place_limit_orders_batch([
                    OrderArgs(token_id="token_yes", price=0.40, size=3.0, side="BUY")
                ])


# ---------------------------------------------------------------------------
# Test 6 — place_limit_orders_batch uses explicit orderType=GTC
# ---------------------------------------------------------------------------

class TestPlaceLimitOrdersBatch:
    """Test that place_limit_orders_batch builds PostOrdersArgs with explicit orderType=GTC."""

    @pytest.mark.asyncio
    async def test_place_limit_orders_batch_explicit_gtc(self) -> None:
        """place_limit_orders_batch should call post_orders with PostOrdersArgs(orderType=OrderType.GTC)."""
        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client

            # create_order returns mock signed orders
            signed_order_1 = MagicMock()
            signed_order_2 = MagicMock()
            mock_client.create_order.side_effect = [signed_order_1, signed_order_2]

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            # Build OrderArgs list (2 orders for simplicity)
            order_args_list = [
                OrderArgs(token_id="token_yes", price=0.40, size=5.0, side="BUY"),
                OrderArgs(token_id="token_no", price=0.60, size=5.0, side="BUY"),
            ]

            result = await executor.place_limit_orders_batch(order_args_list)

            # Verify create_order was called twice
            assert mock_client.create_order.call_count == 2

            # Verify post_orders was called once with PostArgs list
            mock_client.post_orders.assert_called_once()
            post_call_args = mock_client.post_orders.call_args[0][0]
            assert isinstance(post_call_args, list)
            assert len(post_call_args) == 2

            # Verify each PostOrdersArgs has orderType=OrderType.GTC (explicit)
            for post_arg in post_call_args:
                assert isinstance(post_arg, PostOrdersArgs)
                assert post_arg.orderType == OrderType.GTC
                assert post_arg.postOnly is False

            # Verify result is list of Fills
            assert len(result) == 2
            assert result[0].token_id == "token_yes"
            assert result[0].price == Decimal("0.40")
            assert result[0].size == 5
            assert result[1].token_id == "token_no"
            assert result[1].price == Decimal("0.60")
            assert result[1].size == 5

    @pytest.mark.asyncio
    async def test_place_limit_orders_batch_retry_on_429(self) -> None:
        """place_limit_orders_batch should retry on HTTP 429 with exponential backoff."""
        from py_clob_client.exceptions import PolyApiException
        import httpx

        with patch("py_clob_client.client.ClobClient") as mock_clob_cls:
            mock_client = MagicMock()
            mock_clob_cls.return_value = mock_client

            # First call raises 429, second succeeds
            mock_client.post_orders.side_effect = [
                PolyApiException(httpx.Response(429, json={"error": "rate limited"})),
                None,  # Success on retry
            ]
            mock_client.create_order.return_value = MagicMock()

            executor = LiveClobExecutor(
                api_key="test", api_secret="test",
                api_passphrase="test", private_key="0x" + "a" * 64,
            )

            order_args_list = [
                OrderArgs(token_id="token_yes", price=0.40, size=5.0, side="BUY"),
            ]

            # Should succeed after retry
            result = await executor.place_limit_orders_batch(order_args_list)

            # post_orders should be called twice (initial + retry)
            assert mock_client.post_orders.call_count == 2
            assert len(result) == 1
