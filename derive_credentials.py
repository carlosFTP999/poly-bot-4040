#!/usr/bin/env python3
"""Derive Polymarket CLOB API credentials from private key.

Run once. The output goes into your .env file.
Credentials never expire — you can re-derive anytime with the same key.
"""

import os
import sys
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

load_dotenv()

PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
PROXY_ADDRESS = os.environ.get("POLYMARKET_PROXY_ADDRESS", "")

if not PRIVATE_KEY:
    print("ERROR: POLYMARKET_PRIVATE_KEY not found in .env")
    sys.exit(1)

if not PROXY_ADDRESS:
    print("ERROR: POLYMARKET_PROXY_ADDRESS not found in .env")
    sys.exit(1)

print(f"Deriving CLOB credentials...")
print(f"  Private key: {PRIVATE_KEY[:10]}...{PRIVATE_KEY[-4:]}")
print(f"  Proxy (funder): {PROXY_ADDRESS}")
print(f"  Signature type: 2 (GNOSIS_SAFE)")
print()

try:
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=PRIVATE_KEY,
        signature_type=2,
        funder=PROXY_ADDRESS,
    )
    creds: ApiCreds = client.create_or_derive_api_creds()

    print("=" * 60)
    print("COPY THESE TO YOUR .env FILE:")
    print("=" * 60)
    print()
    print(f"POLYMARKET_API_KEY={creds.api_key}")
    print(f"POLYMARKET_API_SECRET={creds.api_secret}")
    print(f"POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
    print()
    print("=" * 60)
    print("Also update LIVE_ENABLED=true and DRY_RUN=false")
    print("=" * 60)

except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
