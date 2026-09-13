#!/usr/bin/env python3
"""
Standalone CLI script to simulate order fills from historical price data.

This script is completely separate from production code (src/).
It fetches historical prices from CLOB API and simulates which orders
would have filled during a given time window.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from decimal import Decimal
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: python-dotenv not installed. Run: pip install python-dotenv", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FillRecord:
    """A single simulated fill."""
    token_id: str
    side: str  # "YES" or "NO"
    price: Decimal
    size: int
    timestamp: int


@dataclass
class SimulationResult:
    """Complete simulation results."""
    condition_id: str
    window_start: int
    window_end: int
    price_threshold: Decimal
    order_size: int
    yes_token_id: str
    no_token_id: str
    fills: list[FillRecord]
    price_source: str
    data_points: int


# ---------------------------------------------------------------------------
# HTTP utilities
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = 30) -> Optional[dict]:
    """Simple HTTP GET with JSON response parsing."""
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'Mozilla/5.0 (Polymarket Fill Simulator)'}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode()
            return json.loads(data)
    except urllib.error.HTTPError as e:
        logging.debug("HTTP %s for %s: %s", e.code, url, e.read().decode()[:200])
        return None
    except Exception as e:
        logging.debug("Request failed for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Gamma API: resolve token IDs from condition_id
# ---------------------------------------------------------------------------

def resolve_tokens_from_gamma(
    condition_id: str,
    gamma_base_url: str = "https://gamma-api.polymarket.com"
) -> tuple[str, str] | None:
    """
    Query Gamma API to get YES/NO token IDs for a condition_id.
    Returns (yes_token_id, no_token_id) or None if not found.
    """
    url = f"{gamma_base_url}/markets?condition_id={condition_id}"
    data = http_get(url)
    if not data:
        return None

    # Gamma returns either a list or {"markets": [...]}
    markets = data if isinstance(data, list) else data.get("markets", [])
    if not markets:
        return None

    market = markets[0]

    # Extract token IDs - same logic as src/market.py
    tokens = market.get("tokens", [])
    if tokens:
        yes_token = ""
        no_token = ""
        for token in tokens:
            outcome = token.get("outcome", "")
            token_id = token.get("token_id", "")
            if outcome == "Yes":
                yes_token = token_id
            elif outcome == "No":
                no_token = token_id
        if yes_token and no_token:
            return (yes_token, no_token)

    # Fallback: clobTokenIds + outcomes (JSON-stringified arrays)
    import json as _json
    clob_ids = market.get("clobTokenIds", [])
    outcomes = market.get("outcomes", [])
    if isinstance(clob_ids, str):
        try:
            clob_ids = _json.loads(clob_ids)
        except Exception:
            clob_ids = []
    if isinstance(outcomes, str):
        try:
            outcomes = _json.loads(outcomes)
        except Exception:
            outcomes = []

    if len(clob_ids) >= 2 and len(outcomes) >= 2:
        # Standard: first is YES/Up, second is NO/Down
        return (clob_ids[0], clob_ids[1])

    return None


# ---------------------------------------------------------------------------
# CLOB API: price data sources (in order of preference)
# ---------------------------------------------------------------------------

def fetch_prices_endpoint(
    clob_base_url: str,
    condition_id: str,
    start: int,
    end: int,
    fidelity: int = 60
) -> list[dict] | None:
    """
    Try GET /prices?market={condition_id}&start={start}&end={end}&fidelity={fidelity}
    Returns list of price points or None if endpoint unavailable.
    """
    url = f"{clob_base_url}/prices?market={condition_id}&start={start}&end={end}&fidelity={fidelity}"
    data = http_get(url)
    if data and isinstance(data, list):
        return data
    return None


def fetch_trades_endpoint(
    clob_base_url: str,
    condition_id: str,
    start: int,
    end: int
) -> list[dict] | None:
    """
    Try GET /trades?market={condition_id}&start={start}&end={end}
    Returns list of trades or None if endpoint unavailable.
    """
    url = f"{clob_base_url}/trades?market={condition_id}&start={start}&end={end}"
    data = http_get(url)
    if data and isinstance(data, list):
        return data
    return None


def fetch_books_snapshots(
    clob_base_url: str,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
    start: int,
    end: int,
    interval: int = 30
) -> list[dict]:
    """
    Fallback: poll GET /book?token_id={token_id} every `interval` seconds
    during the window. Returns list of book snapshots with timestamps.
    
    NOTE: This only captures current book state at poll time, NOT historical
    order book snapshots. The CLOB API does not provide historical book data.
    This is a LIMITATION - we only get point-in-time snapshots during the
    polling window, not the full historical order book.
    """
    snapshots = []
    now = int(time.time())
    
    # If window is in the past, we CANNOT get historical books
    # We can only poll current state (which is useless for past windows)
    if end < now:
        logging.warning(
            "Book snapshots unavailable for past windows (end=%d < now=%d). "
            "CLOB /book endpoint only returns current state, not historical snapshots.",
            end, now
        )
        return []
    
    # If window is current/future, we could poll - but for simulation
    # we typically query past windows, so this is documented as a limitation
    logging.info(
        "Book polling would require waiting for future window. "
        "Returning empty - use prices/trades endpoints for historical data."
    )
    return []


# ---------------------------------------------------------------------------
# Price data normalization
# ---------------------------------------------------------------------------

def normalize_price_points(
    raw_data: list[dict],
    source: str,
    yes_token_id: str,
    no_token_id: str
) -> list[tuple[int, Decimal, str]]:
    """
    Normalize various API responses to a common format:
    List of (timestamp, price, token_id) tuples.
    
    Returns prices for BOTH tokens, tagged with which token they belong to.
    """
    points = []
    
    if source == "prices":
        # Expected format: [{"token_id": "...", "price": "0.45", "timestamp": 1234567890}, ...]
        for item in raw_data:
            token_id = item.get("token_id") or item.get("tokenId")
            price_str = item.get("price") or item.get("p")
            ts = item.get("timestamp") or item.get("t") or item.get("ts")
            if token_id and price_str and ts:
                try:
                    points.append((int(ts), Decimal(str(price_str)), token_id))
                except Exception:
                    pass
                    
    elif source == "trades":
        # Expected format: [{"token_id": "...", "price": "0.45", "timestamp": 1234567890, "side": "BUY"}, ...]
        for item in raw_data:
            token_id = item.get("token_id") or item.get("tokenId") or item.get("asset_id")
            price_str = item.get("price") or item.get("p")
            ts = item.get("timestamp") or item.get("t") or item.get("ts")
            if token_id and price_str and ts:
                try:
                    points.append((int(ts), Decimal(str(price_str)), token_id))
                except Exception:
                    pass
                    
    elif source == "books":
        # Books format: {"bids": [["0.44", "100"]], "asks": [["0.46", "100"]], "timestamp": ...}
        # For books, we use midpoint as price proxy
        for item in raw_data:
            ts = item.get("timestamp") or item.get("t")
            bids = item.get("bids", [])
            asks = item.get("asks", [])
            token_id = item.get("token_id")
            if not (ts and token_id and bids and asks):
                continue
            try:
                best_bid = Decimal(str(bids[0][0]))
                best_ask = Decimal(str(asks[0][0]))
                mid = (best_bid + best_ask) / Decimal("2")
                points.append((int(ts), mid, token_id))
            except Exception:
                pass
                
    return points


# ---------------------------------------------------------------------------
# Fill simulation logic
# ---------------------------------------------------------------------------

def simulate_fills(
    price_points: list[tuple[int, Decimal, str]],
    yes_token_id: str,
    no_token_id: str,
    price_threshold: Decimal,
    order_size: int
) -> list[FillRecord]:
    """
    Simulate fills from price points.
    
    Logic:
    - YES side: we BUY YES at price_threshold (0.40).
      Fill when market price <= price_threshold (i.e., YES is cheap enough).
    - NO side: we BUY NO at price_threshold (0.40).
      Fill when market price <= price_threshold for NO token.
      Since YES + NO = 1.0, this is equivalent to YES price >= (1 - price_threshold).
    """
    fills = []
    
    for ts, price, token_id in price_points:
        if token_id == yes_token_id:
            # YES token: fill if price <= threshold (cheap YES)
            if price <= price_threshold:
                fills.append(FillRecord(
                    token_id=yes_token_id,
                    side="YES",
                    price=price,
                    size=order_size,
                    timestamp=ts
                ))
        elif token_id == no_token_id:
            # NO token: fill if price <= threshold (cheap NO)
            if price <= price_threshold:
                fills.append(FillRecord(
                    token_id=no_token_id,
                    side="NO",
                    price=price,
                    size=order_size,
                    timestamp=ts
                ))
    
    return fills


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_summary(result: SimulationResult) -> str:
    """Format the summary log output."""
    yes_fills = [f for f in result.fills if f.side == "YES"]
    no_fills = [f for f in result.fills if f.side == "NO"]
    
    yes_prices = [f.price for f in yes_fills] if yes_fills else []
    no_prices = [f.price for f in no_fills] if no_fills else []
    
    # Estimated PnL: each YES fill at price p means we bought at p, worth 1.0 at expiry
    # So profit per YES share = 1.0 - p. For NO: profit = 1.0 - p (since we buy NO at p)
    # Total estimated gain = sum(1.0 - price) * size for all fills
    total_gain = sum((Decimal("1.0") - f.price) * f.size for f in result.fills)
    
    lines = [
        f"Ventana: {result.window_start}-{result.window_end} (condition_id: {result.condition_id})",
        f"YES: {len(yes_fills)} fills simulados "
        f"({'precio min ' + str(min(yes_prices)) + ', max ' + str(max(yes_prices)) if yes_prices else 'sin fills'})",
        f"NO: {len(no_fills)} fills "
        f"({'precio min ' + str(min(no_prices)) + ', max ' + str(max(no_prices)) if no_prices else 'sin fills'})",
        f"Ganancia estimada: +${total_gain:.2f} ({len(yes_fills)} YES + {len(no_fills)} NO ganan)",
        f"Fuente de precios: {result.price_source} ({result.data_points} puntos de datos)",
    ]
    return "\n".format(*lines) if False else "\n".join(lines)


def write_json_output(result: SimulationResult, output_path: str = "fills_simulados.json") -> None:
    """Write simulation results to JSON file."""
    # Convert FillRecord to serializable dict
    fills_data = []
    for f in result.fills:
        fills_data.append({
            "token_id": f.token_id,
            "side": f.side,
            "price": str(f.price),
            "size": f.size,
            "timestamp": f.timestamp,
        })
    
    output = {
        "condition_id": result.condition_id,
        "window_start": result.window_start,
        "window_end": result.window_end,
        "price_threshold": str(result.price_threshold),
        "order_size": result.order_size,
        "yes_token_id": result.yes_token_id,
        "no_token_id": result.no_token_id,
        "price_source": result.price_source,
        "data_points": result.data_points,
        "fills": fills_data,
    }
    
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    
    logging.info("JSON output written to %s", output_path)


# ---------------------------------------------------------------------------
# Main simulation flow
# ---------------------------------------------------------------------------

def run_simulation(args: argparse.Namespace) -> SimulationResult:
    """Run the complete fill simulation."""
    
    # Load environment
    load_dotenv()
    
    clob_base_url = os.environ.get("CLOB_BASE_URL", "https://clob.polymarket.com")
    gamma_base_url = os.environ.get("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    
    # Validate required args
    condition_id = args.condition_id
    if not condition_id.startswith("0x"):
        condition_id = "0x" + condition_id
    
    # Resolve window timestamps
    now = int(time.time())
    window_start = args.window_start
    if window_start is None:
        # Try to extract from condition_id (last 8 hex chars often encode timestamp)
        # But this is unreliable - default to current window
        window_start = (now // 300) * 300
        logging.info("No --window-start provided, using current window: %d", window_start)
    
    window_end = args.window_end or (window_start + 300)
    
    price_threshold = Decimal(str(args.price_threshold))
    order_size = args.size
    
    # Resolve token IDs
    yes_token_id = args.yes_token_id
    no_token_id = args.no_token_id
    
    if not yes_token_id or not no_token_id:
        logging.info("Resolving token IDs via Gamma API for condition_id=%s", condition_id)
        tokens = resolve_tokens_from_gamma(condition_id, gamma_base_url)
        if tokens:
            yes_token_id, no_token_id = tokens
            logging.info("Resolved: YES=%s... NO=%s...", yes_token_id[:12], no_token_id[:12])
        else:
            raise ValueError(
                f"Could not resolve token IDs for condition_id={condition_id}. "
                f"Provide --yes-token-id and --no-token-id explicitly."
            )
    
    # Fetch price data (try in order of preference)
    price_points = []
    price_source = "none"
    
    # 1. Try /prices endpoint
    logging.info("Trying /prices endpoint...")
    prices_data = fetch_prices_endpoint(clob_base_url, condition_id, window_start, window_end)
    if prices_data:
        price_points = normalize_price_points(prices_data, "prices", yes_token_id, no_token_id)
        price_source = "prices"
        logging.info("Got %d price points from /prices", len(price_points))
    
    # 2. Try /trades endpoint
    if not price_points:
        logging.info("Trying /trades endpoint...")
        trades_data = fetch_trades_endpoint(clob_base_url, condition_id, window_start, window_end)
        if trades_data:
            price_points = normalize_price_points(trades_data, "trades", yes_token_id, no_token_id)
            price_source = "trades"
            logging.info("Got %d price points from /trades", len(price_points))
    
    # 3. Try /books snapshots (fallback - limited for historical)
    if not price_points:
        logging.info("Trying /books snapshots (fallback)...")
        books_data = fetch_books_snapshots(
            clob_base_url, condition_id, yes_token_id, no_token_id,
            window_start, window_end
        )
        if books_data:
            price_points = normalize_price_points(books_data, "books", yes_token_id, no_token_id)
            price_source = "books"
            logging.info("Got %d price points from /books", len(price_points))
        else:
            logging.warning(
                "Book snapshots unavailable for historical windows. "
                "CLOB /book only returns current state, not historical data."
            )
    
    if not price_points:
        raise RuntimeError(
            "No price data available from any endpoint. "
            "Check condition_id, window timestamps, and API availability."
        )
    
    # Sort by timestamp
    price_points.sort(key=lambda x: x[0])
    
    # Simulate fills
    fills = simulate_fills(
        price_points, yes_token_id, no_token_id,
        price_threshold, order_size
    )
    
    # Build result
    result = SimulationResult(
        condition_id=condition_id,
        window_start=window_start,
        window_end=window_end,
        price_threshold=price_threshold,
        order_size=order_size,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        fills=fills,
        price_source=price_source,
        data_points=len(price_points),
    )
    
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulate order fills from historical CLOB price data",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    
    parser.add_argument(
        "--condition-id", required=True,
        help="Market condition ID (e.g., 0xabc123...)"
    )
    parser.add_argument(
        "--window-start", type=int,
        help="Window start timestamp (epoch seconds). Default: derived from condition_id or current window"
    )
    parser.add_argument(
        "--window-end", type=int,
        help="Window end timestamp (epoch seconds). Default: window_start + 300"
    )
    parser.add_argument(
        "--price-threshold", type=float, default=0.40,
        help="Price threshold for fills (YES fills when price <= threshold, NO fills when price <= threshold)"
    )
    parser.add_argument(
        "--size", type=int, default=5,
        help="Order size (shares per fill)"
    )
    parser.add_argument(
        "--yes-token-id",
        help="YES token ID (optional, will resolve via Gamma if not provided)"
    )
    parser.add_argument(
        "--no-token-id",
        help="NO token ID (optional, will resolve via Gamma if not provided)"
    )
    parser.add_argument(
        "--output", default="fills_simulados.json",
        help="Output JSON file path"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    
    return parser


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.Formatter.converter = time.gmtime
    
    try:
        result = run_simulation(args)
    except Exception as e:
        logging.error("Simulation failed: %s", e)
        return 1
    
    # Output summary to stdout
    print("\n" + "=" * 60)
    print(format_summary(result))
    print("=" * 60 + "\n")
    
    # Write JSON output
    write_json_output(result, args.output)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())