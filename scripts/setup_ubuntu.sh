#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="senpilot-demo"
INSTALL_DIR="/opt/senpilot-demo"
REPO_URL="git@github.com:quanttrinh/senpilot-demo.git"
SERVICE_USER="${SUDO_USER:-$(id -un)}"
USER_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
SSH_KEY="${SSH_KEY:-$USER_HOME/.ssh/senpilot-demo-repo}"
GIT_SSH_COMMAND="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
BRANCH="${BRANCH:-main}"
UV_BIN="${UV_BIN:-}"

ACTION="install"
KEEP_FILES=0
FORCE=0

usage() {
  cat <<'EOF'
Usage: sudo bash setup_ubuntu.sh [--install | --uninstall] [--force] [--keep-files]

  --install      Install/refresh the service (default)
  --uninstall    Stop, disable, and remove the service, swapfile, and config
  --force        With --install, hard-align the deploy clone to origin (discards
                 local changes; keeps ignored files such as .env and .venv)
  --keep-files   With --uninstall, keep /opt/senpilot-demo (including .env)
  -h, --help     Show this help

Environment overrides: SERVICE_NAME, INSTALL_DIR, REPO_URL, SERVICE_USER,
SSH_KEY, BRANCH, UV_BIN, SKIP_SWAP.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --install) ACTION="install" ;;
    --uninstall) ACTION="uninstall" ;;
    --force) FORCE=1 ;;
    --keep-files) KEEP_FILES=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      usage
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Elevating with sudo..."
  exec sudo \
    SERVICE_NAME="$SERVICE_NAME" \
    INSTALL_DIR="$INSTALL_DIR" \
    REPO_URL="$REPO_URL" \
    SERVICE_USER="$SERVICE_USER" \
    SSH_KEY="$SSH_KEY" \
    UV_BIN="$UV_BIN" \
    bash "$0" "$@"
fi

resolve_uv() {
  if [ -n "$UV_BIN" ] && [ -x "$UV_BIN" ]; then
    return 0
  fi
  local candidate
  for candidate in \
    "$(command -v uv 2>/dev/null || true)" \
    "$USER_HOME/.local/bin/uv" \
    /usr/local/bin/uv \
    /usr/bin/uv; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      UV_BIN="$candidate"
      return 0
    fi
  done

  echo "==> uv not found; installing it locally for $SERVICE_USER"
  sudo -u "$SERVICE_USER" -H bash -c \
    "curl -LsSf https://astral.sh/uv/install.sh | sh"
  UV_BIN="$USER_HOME/.local/bin/uv"
  if [ ! -x "$UV_BIN" ]; then
    echo "ERROR: uv installation failed; pass UV_BIN=/path/to/uv." >&2
    return 1
  fi
  echo "==> Installed uv at $UV_BIN"
}

install_service() {
  if [ "$SERVICE_USER" = "root" ]; then
    echo "WARNING: running the service as root; set SERVICE_USER to a dedicated user." >&2
  fi

  echo "==> Installing system packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y git openssh-client curl ca-certificates xz-utils

  if [ "${SKIP_SWAP:-0}" != "1" ] && [ "$(swapon --show=NAME --noheadings | wc -l)" -eq 0 ]; then
    echo "==> No swap found; creating a 2G swapfile (set SKIP_SWAP=1 to skip)"
    fallocate -l 2G /swapfile 2>/dev/null || \
      dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || \
      echo '/swapfile none swap sw 0 0 # senpilot-demo' >> /etc/fstab
    sysctl -w vm.swappiness=10 >/dev/null
    echo 'vm.swappiness=10' > /etc/sysctl.d/99-senpilot.conf
  fi

  if [ -f "$SSH_KEY" ]; then
    if [ "$(stat -c '%a' "$SSH_KEY")" != "600" ]; then
      echo "==> Tightening permissions on $SSH_KEY (was $(stat -c '%a' "$SSH_KEY"))"
      chmod 600 "$SSH_KEY"
    fi
  fi

  echo "==> Fetching repository into $INSTALL_DIR"
  if [ -d "$INSTALL_DIR/.git" ]; then
    if [ "$FORCE" -eq 1 ]; then
      echo "==> Forcing $INSTALL_DIR to origin/$BRANCH"
      sudo -u "$SERVICE_USER" -H env GIT_SSH_COMMAND="$GIT_SSH_COMMAND" \
        bash -c "cd '$INSTALL_DIR' && git fetch origin '$BRANCH' && \
          git reset --hard 'origin/$BRANCH' && git clean -fd"
    else
      sudo -u "$SERVICE_USER" -H env GIT_SSH_COMMAND="$GIT_SSH_COMMAND" \
        git -C "$INSTALL_DIR" pull --ff-only
    fi
  else
    install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$INSTALL_DIR"
    sudo -u "$SERVICE_USER" -H env GIT_SSH_COMMAND="$GIT_SSH_COMMAND" \
      git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi

  resolve_uv
  echo "==> Using uv at $UV_BIN"
  sudo -u "$SERVICE_USER" -H bash -c "cd '$INSTALL_DIR' && '$UV_BIN' sync"

  echo "==> Installing Playwright OS dependencies"
  "$INSTALL_DIR/.venv/bin/playwright" install-deps chromium

  echo "==> Installing Chromium for $SERVICE_USER"
  sudo -u "$SERVICE_USER" -H "$INSTALL_DIR/.venv/bin/playwright" install chromium

  if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "==> Creating .env from .env.example"
    install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 600 \
      "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  fi

  ENV_READY=1
  if grep -q "your-" "$INSTALL_DIR/.env" 2>/dev/null; then
    ENV_READY=0
  fi

  echo "==> Writing systemd unit /etc/systemd/system/$SERVICE_NAME.service"
  cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=Senpilot Regulatory Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=notify
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/regulatory-agent
Restart=always
RestartSec=5
TimeoutStopSec=20
WatchdogSec=600
MemoryHigh=800M
MemoryMax=1G
NoNewPrivileges=yes
ProtectSystem=full
PrivateTmp=yes
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"

  if [ "$ENV_READY" = "1" ]; then
    echo "==> Starting $SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true
  else
    echo "==> Fill in $INSTALL_DIR/.env, then run:"
    echo "      sudo systemctl start $SERVICE_NAME"
  fi

  echo
  echo "Useful commands:"
  echo "  sudo systemctl status $SERVICE_NAME"
  echo "  sudo journalctl -u $SERVICE_NAME -f"
  echo "  sudo systemctl restart $SERVICE_NAME"
}

uninstall_service() {
  echo "==> Stopping and disabling $SERVICE_NAME"
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  systemctl disable "$SERVICE_NAME" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SERVICE_NAME.service"
  systemctl daemon-reload
  systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true

  if [ -e /swapfile ]; then
    echo "==> Removing /swapfile and its fstab entry"
    swapoff /swapfile 2>/dev/null || true
    rm -f /swapfile
    sed -i '\|^/swapfile[[:space:]]|d' /etc/fstab
  fi
  if [ -e /etc/sysctl.d/99-senpilot.conf ]; then
    echo "==> Removing /etc/sysctl.d/99-senpilot.conf"
    rm -f /etc/sysctl.d/99-senpilot.conf
  fi

  if [ "$KEEP_FILES" -eq 1 ]; then
    echo "==> Keeping $INSTALL_DIR (--keep-files)"
  else
    echo "==> Removing $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
  fi

  echo "Done. (Playwright browsers under $USER_HOME/.cache/ms-playwright were left in place.)"
}

if [ "$ACTION" = "uninstall" ]; then
  uninstall_service
else
  install_service
fi
