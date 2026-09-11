"""Unit tests for src/types.py dataclasses and enums."""

import pytest
from decimal import Decimal

from src.types import (
    Fill,
    MarketInfo,
    Order,
    OrderStatus,
    TokenInfo,
    WindowState,
)


class TestOrderStatus:
    """Test OrderStatus enum values."""

    def test_enum_values(self) -> None:
        assert OrderStatus.PENDING.value == "PENDING"
        assert OrderStatus.LIVE.value == "LIVE"
        assert OrderStatus.MATCHED.value == "MATCHED"
        assert OrderStatus.CANCELLED.value == "CANCELLED"
        assert OrderStatus.PARTIAL.value == "PARTIAL"


class TestOrder:
    """Test Order frozen dataclass."""

    def test_order_creation(self) -> None:
        order = Order(
            token_id="tok_yes_1",
            side="BUY",
            price=Decimal("0.40"),
            size=5,
        )
        assert order.token_id == "tok_yes_1"
        assert order.side == "BUY"
        assert order.price == Decimal("0.40")
        assert order.size == 5
        assert order.order_type == "GTC"
        assert order.expiration == 0
        assert order.status == OrderStatus.PENDING

    def test_order_immutable(self) -> None:
        order = Order(
            token_id="tok_1", side="BUY",
            price=Decimal("0.40"), size=5,
        )
        with pytest.raises(Exception):
            order.price = Decimal("0.50")  # type: ignore

    def test_order_decimal_price(self) -> None:
        order = Order(
            token_id="tok_1", side="BUY",
            price=Decimal("0.40"), size=5,
        )
        assert isinstance(order.price, Decimal)
        assert not isinstance(order.price, float)


class TestFill:
    """Test Fill frozen dataclass."""

    def test_fill_creation(self) -> None:
        fill = Fill(
            token_id="tok_yes_1",
            side="YES",
            price=Decimal("0.40"),
            size=5,
            timestamp=1694000400,
        )
        assert fill.token_id == "tok_yes_1"
        assert fill.side == "YES"
        assert fill.price == Decimal("0.40")
        assert fill.size == 5

    def test_fill_decimal_price(self) -> None:
        fill = Fill(
            token_id="tok_1", side="YES",
            price=Decimal("0.40"), size=5,
            timestamp=0,
        )
        assert isinstance(fill.price, Decimal)


class TestMarketInfo:
    """Test MarketInfo frozen dataclass."""

    def test_market_info_creation(self) -> None:
        market = MarketInfo(
            condition_id="cond_abc",
            token_yes_id="tok_yes",
            token_no_id="tok_no",
            start_date=1694000400,
            end_date=1694000700,
        )
        assert market.condition_id == "cond_abc"
        assert market.token_yes_id == "tok_yes"
        assert market.token_no_id == "tok_no"

    def test_market_info_optional_dates(self) -> None:
        market = MarketInfo(
            condition_id="cond_abc",
            token_yes_id="tok_yes",
            token_no_id="tok_no",
            start_date=0,
            end_date=0,
        )
        assert market.start_date == 0


class TestTokenInfo:
    """Test TokenInfo frozen dataclass."""

    def test_token_info_creation(self) -> None:
        token = TokenInfo(
            token_id="tok_yes_1",
            outcome="Yes",
            condition_id="cond_abc",
        )
        assert token.token_id == "tok_yes_1"
        assert token.outcome == "Yes"
        assert token.condition_id == "cond_abc"


class TestWindowState:
    """Test WindowState frozen dataclass."""

    def test_window_state_creation(self) -> None:
        ws = WindowState(window_ts=1694000400, phase="PHASE1")
        assert ws.window_ts == 1694000400
        assert ws.phase == "PHASE1"
        assert ws.market_info is None
        assert ws.fills == ()

    def test_window_state_with_fills(self) -> None:
        from src.types import Fill, MarketInfo

        fill = Fill(
            token_id="tok_1", side="YES",
            price=Decimal("0.40"), size=5, timestamp=0,
        )
        market = MarketInfo(
            condition_id="cond_abc",
            token_yes_id="tok_yes",
            token_no_id="tok_no",
            start_date=0, end_date=0,
        )
        ws = WindowState(
            window_ts=1694000400,
            phase="PHASE1",
            market_info=market,
            fills=(fill,),
        )
        assert len(ws.fills) == 1
