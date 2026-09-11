"""Configuration module for the BTC 5-Minute Hedge Bot.

Loads strategy parameters and environment variables into a frozen Settings
dataclass. All monetary values use Decimal for precision.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable


@dataclass(frozen=True)
class Settings:
    """Immutable configuration for the hedge bot.

    All monetary parameters are Decimal. Strategy parameters and environment
    variables are loaded at construction time and cannot be mutated.
    """

    # Strategy parameters (Decimal for monetary precision)
    PRICE_THRESHOLD: Decimal = Decimal("0.40")
    MAX_PER_SIDE: Decimal = Decimal("2.00")
    TOTAL_CAP: Decimal = Decimal("4.00")
    SHARE_FLOOR: int = 5

    # Mode selection
    LIVE_ENABLED: bool = False
    DRY_RUN: bool = True

    # API configuration
    GAMMA_BASE_URL: str = "https://gamma-api.polymarket.com"
    CLOB_BASE_URL: str = "https://clob.polymarket.com"

    # API credentials (loaded from env)
    POLYMARKET_PRIVATE_KEY: str = ""
    POLYMARKET_API_KEY: str = ""
    POLYMARKET_API_SECRET: str = ""
    POLYMARKET_API_PASSPHRASE: str = ""
    POLYMARKET_PROXY_ADDRESS: str = ""

    # Signature type (0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE, 3=DEPOSIT_WALLET)
    # Browser wallets (MetaMask, Phantom) → type 2
    SIGNATURE_TYPE: int = 2

    def __post_init__(self) -> None:
        """Validate mode selection after construction."""
        if self.LIVE_ENABLED and self.DRY_RUN:
            raise ValueError(
                "LIVE_ENABLED and DRY_RUN cannot both be True. "
                "Select exactly one execution mode."
            )
        if not self.LIVE_ENABLED and not self.DRY_RUN:
            raise ValueError(
                "At least one of LIVE_ENABLED or DRY_RUN must be True."
            )
        if self.LIVE_ENABLED and not self._has_live_keys():
            raise ValueError(
                "LIVE_ENABLED requires all API keys: "
                "POLYMARKET_API_KEY, POLYMARKET_API_SECRET, "
                "POLYMARKET_API_PASSPHRASE, POLYMARKET_PRIVATE_KEY."
            )

    def _has_live_keys(self) -> bool:
        """Check that all required live-mode credentials are present."""
        return bool(
            self.POLYMARKET_API_KEY
            and self.POLYMARKET_API_SECRET
            and self.POLYMARKET_API_PASSPHRASE
            and self.POLYMARKET_PRIVATE_KEY
        )


def load_settings_from_env() -> Settings:
    """Load Settings from environment variables with sensible defaults.

    Reads all env vars listed in the config spec. Returns a fully-constructed
    Settings object that validates mode selection on creation.
    """
    live_enabled = _parse_bool(os.environ.get("LIVE_ENABLED", "false"))
    dry_run = _parse_bool(os.environ.get("DRY_RUN", "true"))

    # When loading from env, ensure exactly one mode is True
    if live_enabled and dry_run:
        dry_run = False  # Live takes precedence if both set
    if not live_enabled and not dry_run:
        dry_run = True  # Default to dry-run

    return Settings(
        LIVE_ENABLED=live_enabled,
        DRY_RUN=dry_run,
        GAMMA_BASE_URL=os.environ.get("GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
        CLOB_BASE_URL=os.environ.get("CLOB_BASE_URL", "https://clob.polymarket.com"),
        POLYMARKET_PRIVATE_KEY=os.environ.get("POLYMARKET_PRIVATE_KEY", ""),
        POLYMARKET_API_KEY=os.environ.get("POLYMARKET_API_KEY", ""),
        POLYMARKET_API_SECRET=os.environ.get("POLYMARKET_API_SECRET", ""),
        POLYMARKET_API_PASSPHRASE=os.environ.get("POLYMARKET_API_PASSPHRASE", ""),
        POLYMARKET_PROXY_ADDRESS=os.environ.get("POLYMARKET_PROXY_ADDRESS", ""),
        SIGNATURE_TYPE=int(os.environ.get("SIGNATURE_TYPE", "2")),
    )


def _parse_bool(value: str | None) -> bool:
    """Parse a string environment variable to bool."""
    if value is None:
        return False
    return value.strip().lower() in ("true", "1", "yes")
