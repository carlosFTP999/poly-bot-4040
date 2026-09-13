# VPS Deployment Checklist — Poly-bot-4040

## Prerequisites

- [ ] VPS provisioned (Linux, ideally near Amsterdam for Polymarket latency)
- [ ] SSH access configured
- [ ] Python 3.11+ installed
- [ ] Git installed

## Step 1: System Setup

```bash
# Create polybot user
sudo useradd -r -m -s /usr/sbin/nologin polybot

# Create directories
sudo mkdir -p /opt/poly-bot-4040 /var/log/poly-bot
sudo chown polybot:polybot /opt/poly-bot-4040 /var/log/poly-bot
```

## Step 2: Deploy Code

```bash
# Clone repo
sudo -u polybot git clone https://github.com/carlosFTP999/poly-bot-4040.git /opt/poly-bot-4040

# Create venv
sudo -u polybot python3 -m venv /opt/poly-bot-4040/.venv
sudo -u polybot /opt/poly-bot-4040/.venv/bin/pip install -r /opt/poly-bot-4040/requirements.txt
```

## Step 3: Verify NTP (CRITICAL)

```bash
timedatectl status
# MUST show: "System clock synchronized: yes"
# If not:
sudo timedatectl set-ntp true
```

**Why**: Polymarket CLOB rejects orders with timestamps >60s off. Our ClockSync mitigates this, but NTP prevents it.

## Step 4: Configure Environment

```bash
sudo -u polybot nano /opt/poly-bot-4040/.env
```

### Required Variables

```bash
# === MODE ===
LIVE_ENABLED=false          # START WITH FALSE
DRY_RUN=true                # START WITH TRUE

# === CREDENTIALS ===
POLYMARKET_PRIVATE_KEY=0x...    # Your Phantom wallet private key
POLYMARKET_API_KEY=...          # From derive_credentials.py
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
POLYMARKET_PROXY_ADDRESS=0x...  # Your Gnosis Safe proxy address
FUNDER=0x...                    # Who pays gas (can be same as proxy)

# === CONFIG ===
SIGNATURE_TYPE=2                # GNOSIS_SAFE (for browser wallets)
CLOB_BASE_URL=clob.polymarket.com
GAMMA_BASE_URL=gamma-api.polymarket.com
WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/user
LOG_LEVEL=INFO
```

### Derive CLOB Credentials (one-time)

```bash
sudo -u polybot /opt/poly-bot-4040/.venv/bin/python /opt/poly-bot-4040/derive_credentials.py
# Output: API_KEY, API_SECRET, API_PASSPHRASE
# Copy these to .env
```

```bash
sudo chmod 600 /opt/poly-bot-4040/.env
sudo chown polybot:polybot /opt/poly-bot-4040/.env
```

## Step 5: Test Dry-Run (No Funds)

```bash
# Test with simulated fills (no API calls)
sudo -u polybot /opt/poly-bot-4040/.venv/bin/python -m src.main
```

**Expected behavior:**
- Bot discovers market via Gamma API
- Logs "DRY_RUN mode" and simulated fills
- Does NOT connect to WebSocket (dry-run skips WS)
- Does NOT place real orders

**Verify in logs:**
- [ ] Market discovery successful
- [ ] Phase 1 executed (simulated)
- [ ] Wait 300s
- [ ] Phase 2 cancel (simulated)
- [ ] Rotation to next window

## Step 6: Test Paper-Live (Real Credentials, No Orders)

```bash
# Edit .env: LIVE_ENABLED=true, DRY_RUN=true
# This activates PaperLiveExecutor
sudo -u polybot /opt/poly-bot-4040/.venv/bin/python -m src.main
```

**Expected behavior:**
- LiveClobExecutor builds with L2 credentials
- Connects to real WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/user`)
- `GET /balance-allowance` returns real pUSD balance
- `ClockSync` calibrates via `GET /time`
- Orders are LOGGED but NOT posted (PaperLiveExecutor)

**Verify in logs:**
- [ ] WebSocket connected successfully
- [ ] Auth frame sent: `{"auth":{...}, "type":"user"}`
- [ ] PING/PONG heartbeat active (every 10s)
- [ ] Balance check passed (or skipped if < TOTAL_CAP)
- [ ] Clock sync offset logged (<30s is good)
- [ ] Orders logged (not posted)
- [ ] Phase 2 cancel logged
- [ ] No exceptions/crashes

## Step 7: Go Live (Real Orders)

```bash
# Edit .env: LIVE_ENABLED=true, DRY_RUN=false
sudo -u polybot /opt/poly-bot-4040/.venv/bin/python -m src.main
```

**⚠️ WARNING: First order = first real dollar. No testnet exists.**

**Expected behavior:**
- Same as Paper-Live, but orders ARE posted to CLOB
- `POST /orders` with 2 orders (1 YES + 1 NO)
- WebSocket receives real fills
- `DELETE /cancel-all` at end of window

**Verify in logs:**
- [ ] Orders posted successfully (HTTP 200)
- [ ] WebSocket fill events received
- [ ] Balance updated after fills
- [ ] Cancel-all executed at window end
- [ ] No orphan orders after rotation

## Step 8: Setup Systemd Service

```bash
sudo cp /opt/poly-bot-4040/deploy/poly-bot.service /etc/systemd/system/poly-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now poly-bot
sudo journalctl -u poly-bot -f
```

**Verify:**
- [ ] Service starts without errors
- [ ] Logs appear in `/var/log/poly-bot/bot.log`
- [ ] Service restarts on failure (`Restart=always`)

## Troubleshooting

### WebSocket Won't Connect

```bash
# Test connectivity
curl -s https://clob.polymarket.com/time
# Should return Unix timestamp

# Check WS URL
# Correct: wss://ws-subscriptions-clob.polymarket.com/ws/user
# Wrong:   wss://ws-clob.polymarket.com
```

### Clock Drift Warning

```bash
# Check NTP
timedatectl status

# Check bot logs for offset
# "Clock drift vs Polymarket server: 23.5 seconds"
# This means NTP is not running. Fix:
sudo timedatectl set-ntp true
```

### Balance Check Fails

```bash
# Verify pUSD balance
curl -s "https://clob.polymarket.com/balance-allowance?asset_type=pUSD" \
  -H "POLY_ADDRESS: 0x..." \
  -H "POLY_API_KEY: ..." \
  -H "POLY_PASSPHRASE: ..." \
  -H "POLY_TIMESTAMP: ..." \
  -H "POLY_SIGNATURE: ..."
```

### Orders Rejected (401 Unauthorized)

```bash
# Re-derive credentials
python derive_credentials.py

# Verify SIGNATURE_TYPE=2 for browser wallets
# Verify FUNDER matches your proxy address
```

## Rollback

If something goes wrong:

```bash
# Stop bot
sudo systemctl stop poly-bot

# Revert to DRY_RUN
sudo sed -i 's/LIVE_ENABLED=true/LIVE_ENABLED=false/' /opt/poly-bot-4040/.env
sudo sed -i 's/DRY_RUN=false/DRY_RUN=true/' /opt/poly-bot-4040/.env

# Restart
sudo systemctl start poly-bot
```

## Success Criteria

- [ ] Bot runs 24h without crashes
- [ ] Clock sync offset <30s
- [ ] WebSocket stays connected (reconnects on disconnect)
- [ ] Orders execute as expected (fills received)
- [ ] Cancel-all works at window end
- [ ] No orphan orders in `GET /orders`
