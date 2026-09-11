"""Unit tests for src/executor.py Executor Protocol, DryRunExecutor, LiveClobExecutor."""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, AsyncMock

from src.executor import Executor, DryRunExecutor, LiveClobExecutor
from src.types import Fill, OrderStatus


class TestExecutorProtocol:
    """Test that executor implementations conform to the Executor Protocol."""

    def test_dryrun_conforms_to_protocol(self) -> None:
        """DryRunExecutor should be an instance of Executor protocol."""
        executor = DryRunExecutor()
        assert isinstance(executor, Executor)

    def test_protocol_has_required_methods(self) -> None:
        """Verify Executor protocol has place_limit_order and cancel_all."""
        assert hasattr(Executor, "place_limit_order")
        assert hasattr(Executor, "cancel_all")


class TestDryRunExecutor:
    """Test DryRunExecutor determinism and key validation."""

    @pytest.mark.asyncio
    async def test_place_limit_order_returns_fill(self) -> None:
        """Given a DryRunExecutor, place_limit_order returns a Fill immediately."""
        executor = DryRunExecutor()
        fill = await executor.place_limit_order(
            token_id="tok_yes",
            side="BUY",
            price=Decimal("0.40"),
            size=5,
        )
        assert isinstance(fill, Fill)
        assert fill.price == Decimal("0.40")
        assert fill.size == 5
        assert fill.token_id == "tok_yes"

    @pytest.mark.asyncio
    async def test_place_limit_order_stores_resting(self) -> None:
        """After placing an order, it should be in _resting."""
        executor = DryRunExecutor()
        await executor.place_limit_order(
            token_id="tok_yes", side="BUY",
            price=Decimal("0.40"), size=5,
        )
        assert len(executor._resting) == 1

    @pytest.mark.asyncio
    async def test_cancel_all_clears_state(self) -> None:
        """Given 3 resting orders, cancel_all clears them and increments counter."""
        executor = DryRunExecutor()
        for _ in range(3):
            await executor.place_limit_order(
                token_id="tok_1", side="BUY",
                price=Decimal("0.40"), size=5,
            )
        assert len(executor._resting) == 3
        await executor.cancel_all()
        assert len(executor._resting) == 0
        assert executor.cancelled_count == 3

    @pytest.mark.asyncio
    async def test_size_below_minimum_raises(self) -> None:
        """Order size < 5 should raise ValueError."""
        executor = DryRunExecutor()
        with pytest.raises(ValueError):
            await executor.place_limit_order(
                token_id="tok_1", side="BUY",
                price=Decimal("0.40"), size=3,
            )

    @pytest.mark.asyncio
    async def test_deterministic_fills(self) -> None:
        """Multiple calls return consistent fills."""
        executor = DryRunExecutor()
        fill1 = await executor.place_limit_order(
            token_id="tok_1", side="BUY",
            price=Decimal("0.40"), size=5,
        )
        fill2 = await executor.place_limit_order(
            token_id="tok_2", side="BUY",
            price=Decimal("0.40"), size=5,
        )
        assert fill1.price == fill2.price == Decimal("0.40")
        assert fill1.size == fill2.size == 5

    def test_cancelled_count_starts_at_zero(self) -> None:
        executor = DryRunExecutor()
        assert executor.cancelled_count == 0


class TestLiveClobExecutor:
    """Test LiveClobExecutor key validation."""

    def test_missing_keys_raises(self) -> None:
        """Missing any required key should raise ValueError."""
        with pytest.raises(ValueError, match="requires"):
            LiveClobExecutor(
                api_key="",
                api_secret="secret",
                api_passphrase="pass",
                private_key="priv",
            )

    def test_missing_api_key_raises(self) -> None:
        """Missing POLYMARKET_API_KEY should raise."""
        with pytest.raises(ValueError, match="POLYMARKET_API_KEY"):
            LiveClobExecutor(
                api_key="",
                api_secret="secret",
                api_passphrase="pass",
                private_key="priv",
            )

    def test_missing_all_keys_raises(self) -> None:
        """Missing all keys should raise."""
        with pytest.raises(ValueError, match="requires"):
            LiveClobExecutor(
                api_key="",
                api_secret="",
                api_passphrase="",
                private_key="",
            )

    def test_live_clob_accepts_all_keys(self) -> None:
        """LiveClobExecutor constructs when all keys provided."""
        # py_clob_client may not be installed; _build_client
        # catches ImportError and returns None
        try:
            executor = LiveClobExecutor(
                api_key="key", api_secret="secret",
                api_passphrase="pass", private_key="priv",
            )
            assert isinstance(executor, LiveClobExecutor)
        except ImportError:
            pytest.skip("py_clob_client not installed")

    @pytest.mark.asyncio
    async def test_size_validation(self) -> None:
        """size < 5 should raise regardless of executor type."""
        executor = DryRunExecutor()
        with pytest.raises(ValueError):
            await executor.place_limit_order(
                token_id="tok_1", side="BUY",
                price=Decimal("0.40"), size=1,
            )