"""Market discovery module for the BTC 5-Minute Hedge Bot.

Discovers active BTC 5-minute UP/DOWN markets via the Gamma API and
computes window-aligned timestamps.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from src.types import MarketInfo

logger = logging.getLogger(__name__)


def current_window_ts(now: int) -> int:
    """Compute the current trading window timestamp as (now // 300) * 300.

    Aligns the given Unix epoch to the nearest 300-second window boundary.

    Args:
        now: Unix epoch in seconds.

    Returns:
        The window-aligned timestamp (multiple of 300).
    """
    return (now // 300) * 300


async def discover(
    client: Callable[..., Any],
    window_ts: int,
    gamma_base_url: str = "https://gamma-api.polymarket.com",
) -> Optional[MarketInfo]:
    """Discover the active BTC 5-minute UP/DOWN market for a given window.

    Queries the Gamma API with slug filter `btc-updown-5m-{window_ts}`.

    Args:
        client: Injectable HTTP client callable that returns parsed JSON.
        window_ts: The window timestamp to search for.
        gamma_base_url: Base URL of the Gamma API.

    Returns:
        MarketInfo if found, None if no matching market exists.
    """
    slug = f"btc-updown-5m-{window_ts}"
    url = f"{gamma_base_url}/markets?slug={slug}"

    try:
        response = await client(url)
    except Exception as e:
        logger.error("Gamma API request failed for %s: %s", url, e)
        return None

    if not response:
        return None

    # Handle list or dict responses from Gamma API
    markets = _extract_markets(response)
    if not markets:
        return None

    market = markets[0]
    return _build_market_info(market)


def _extract_markets(response: Any) -> list[dict]:
    """Extract market dicts from Gamma API response."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        # Gamma may return {"markets": [...]} or the market directly
        if "markets" in response:
            return response["markets"]
        return [response]
    return []


def _build_market_info(market: dict) -> Optional[MarketInfo]:
    """Build a MarketInfo from a Gamma API market dict."""
    # Support both Gamma response shapes: tokens[] and clobTokenIds[]
    condition_id = market.get("condition_id") or market.get("conditionId", "")

    token_yes_id = ""
    token_no_id = ""

    tokens = market.get("tokens", [])
    if tokens:
        for token in tokens:
            outcome = token.get("outcome", "")
            token_id = token.get("token_id", "")
            if outcome == "Yes":
                token_yes_id = token_id
            elif outcome == "No":
                token_no_id = token_id
    else:
        # Real Gamma format for BTC 5m: clobTokenIds is JSON-stringified array
        import json as _json
        clob_ids = market.get("clobTokenIds", [])
        if isinstance(clob_ids, str):
            try:
                clob_ids = _json.loads(clob_ids)
            except Exception:
                clob_ids = []
        outcomes = market.get("outcomes", [])
        if isinstance(outcomes, str):
            try:
                outcomes = _json.loads(outcomes)
            except Exception:
                outcomes = []
        if len(clob_ids) >= 2:
            # Up=YES (index 0), Down=NO (index 1) per Polymarket convention
            token_yes_id, token_no_id = clob_ids[0], clob_ids[1]

    if not token_yes_id or not token_no_id or not condition_id:
        return None

    start_date = market.get("start_date", market.get("startDate", 0))
    end_date = market.get("end_date", market.get("endDate", 0))

    return MarketInfo(
        condition_id=condition_id,
        token_yes_id=token_yes_id,
        token_no_id=token_no_id,
        start_date=start_date,
        end_date=end_date,
    )
