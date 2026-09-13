#!/usr/bin/env bash
# Poly-bot-4040 VPS Installer
# Run: curl -fsSL https://raw.githubusercontent.com/carlosFTP999/poly-bot-4040/master/install.sh | bash
# Or: bash <(curl -fsSL https://raw.githubusercontent.com/carlosFTP999/poly-bot-4040/master/install.sh)

set -euo pipefail

REPO_URL="https://github.com/carlosFTP999/poly-bot-4040.git"
INSTALL_DIR="/opt/poly-bot-4040"
SERVICE_USER="polybot"
SERVICE_NAME="poly-bot"
LOG_DIR="/var/log/poly-bot"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() { echo -e "${BLUE}[poly-bot]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (use sudo)"
fi

# Check OS
if [[ ! -f /etc/os-release ]]; then
    error "Cannot detect OS"
fi
source /etc/os-release
log "Detected OS: $PRETTY_NAME"

# Check Python version
if ! command -v python3 &> /dev/null; then
    error "Python 3 not found. Install it first: apt-get install python3 python3-venv"
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
REQUIRED_VERSION="3.11"
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    error "Python ${REQUIRED_VERSION}+ required, found ${PYTHON_VERSION}. Install: apt-get install python3.11 python3.11-venv"
fi
success "Python ${PYTHON_VERSION} OK"

# Create service user
if id "$SERVICE_USER" &>/dev/null; then
    log "User $SERVICE_USER already exists"
else
    useradd -r -m -s /usr/sbin/nologin "$SERVICE_USER"
    success "Created user: $SERVICE_USER"
fi

# Create directories
mkdir -p "$INSTALL_DIR" "$LOG_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$LOG_DIR"
success "Directories created"

# Clone or update repo (sparse checkout - only production files)
SPARSE_DIRS=(
    "src/"
    "requirements.txt"
    "deploy/"
    "derive_credentials.py"
    "scripts/simulate_fills.py"
)

if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Repository exists, pulling latest..."
    sudo -u "$SERVICE_USER" git -C "$INSTALL_DIR" pull origin master
else
    log "Cloning repository (sparse checkout)..."
    sudo -u "$SERVICE_USER" git clone --filter=blob:none --sparse "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    sudo -u "$SERVICE_USER" git sparse-checkout set "${SPARSE_DIRS[@]}"
fi
success "Repository ready"

# Setup virtual environment
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
    log "Creating virtual environment..."
    sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/.venv"
else
    log "Virtual environment exists"
fi

# Install dependencies
log "Installing Python dependencies..."
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
success "Dependencies installed"

# Install systemd service
log "Installing systemd service..."
cp "$INSTALL_DIR/deploy/poly-bot.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
success "Systemd service installed"

# Create .env template if not exists
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    log "Creating .env template..."
    cat > "$INSTALL_DIR/.env" << 'EOF'
# Poly-bot-4040 Configuration
# EDIT THIS FILE WITH YOUR CREDENTIALS BEFORE STARTING

# === MODE ===
LIVE_ENABLED=false
DRY_RUN=true

# === CREDENTIALS (derive with: python derive_credentials.py) ===
POLYMARKET_PRIVATE_KEY=
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_API_PASSPHRASE=
POLYMARKET_PROXY_ADDRESS=
FUNDER=

# === CONFIG ===
SIGNATURE_TYPE=2
CLOB_BASE_URL=clob.polymarket.com
GAMMA_BASE_URL=gamma-api.polymarket.com
WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/user
LOG_LEVEL=INFO
EOF
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    success ".env template created at $INSTALL_DIR/.env"
else
    log ".env already exists, skipping"
fi

# Final instructions
echo
echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
echo
echo "Next steps:"
echo "  1. Edit .env with your credentials:"
echo "     sudo -u $SERVICE_USER nano $INSTALL_DIR/.env"
echo
echo "  2. Set these REQUIRED values:"
echo "     LIVE_ENABLED=false"
echo "     DRY_RUN=true"
echo "     POLYMARKET_PRIVATE_KEY=0x...       # Your Phantom wallet private key"
echo "     POLYMARKET_PROXY_ADDRESS=0x...     # Your Gnosis Safe proxy"
echo "     FUNDER=0x...                       # Who pays gas (can be same as proxy)"
echo
echo "  3. Derive CLOB credentials:"
echo "     sudo -u $SERVICE_USER $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/derive_credentials.py"
echo "     # Copy output API_KEY/SECRET/PASSPHRASE to .env"
echo
echo "  4. Test dry-run (no funds):"
echo "     sudo -u $SERVICE_USER $INSTALL_DIR/.venv/bin/python -m src.main"
echo
echo "  5. Test paper-live (real creds, no orders):"
echo "     # Edit .env: LIVE_ENABLED=true, DRY_RUN=true"
echo "     sudo -u $SERVICE_USER $INSTALL_DIR/.venv/bin/python -m src.main"
echo
echo "  6. Go live (real orders):"
echo "     # Edit .env: LIVE_ENABLED=true, DRY_RUN=false"
echo "     sudo systemctl enable --now $SERVICE_NAME"
echo "     sudo journalctl -u $SERVICE_NAME -f"
echo
echo "Service commands:"
echo "  sudo systemctl start $SERVICE_NAME"
echo "  sudo systemctl stop $SERVICE_NAME"
echo "  sudo systemctl restart $SERVICE_NAME"
echo "  sudo systemctl status $SERVICE_NAME"
echo "  sudo journalctl -u $SERVICE_NAME -f"
echo
echo "Logs:"
echo "  /var/log/poly-bot/bot.log"
echo
echo "Documentation:"
echo "  $INSTALL_DIR/VPS_CHECKLIST.md"
echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"