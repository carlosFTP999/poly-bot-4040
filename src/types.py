"""Type definitions for the BTC 5-Minute Hedge Bot.

All dataclasses use frozen=True for immutability.
All monetary values use Decimal for precision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
from typing import Optional


class OrderStatus(Enum):
    """Lifecycle status of an order."""

    PENDING = "PENDING"
    LIVE = "LIVE"
    MATCHED = "MATCHED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True)
class TokenInfo:
    """Information about a single token (YES or NO) in a market."""

    token_id: str
    outcome: str  # "Yes" or "No"
    condition_id: str


@dataclass(frozen=True)
class Order:
    """Represents a GTC limit order placed on the CLOB."""

    token_id: str
    side: str  # "BUY"
    price: Decimal
    size: int
    order_type: str = "GTC"
    expiration: int = 0  # 0 = GTC, no expiry
    status: OrderStatus = OrderStatus.PENDING


@dataclass(frozen=True)
class Fill:
    """Represents a filled order from the exchange."""

    token_id: str
    side: str  # "YES" | "NO"
    price: Decimal
    size: int
    timestamp: int


@dataclass(frozen=True)
class MarketInfo:
    """Discovered market information from the Gamma API."""

    condition_id: str
    token_yes_id: str
    token_no_id: str
    start_date: int  # unix seconds
    end_date: int  # unix seconds


@dataclass(frozen=True)
class WindowState:
    """Current state of a trading window."""

    window_ts: int
    phase: str  # "PHASE1" | "PHASE2" | "WAITING" | "DISCOVERING"
    market_info: Optional[MarketInfo] = None
    fills: tuple[Fill, ...] = ()
    orders_placed: int = 0
    orders_cancelled: int = 0
