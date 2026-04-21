#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

VENV_DIR="$HOME/.local/share/jcrefresher/venv"

echo "Creating venv at $VENV_DIR..."
mkdir -p "$HOME/.local/share/jcrefresher"
python3 -m venv "$VENV_DIR"

echo "Installing watchdog into venv..."
if ! "$VENV_DIR/bin/pip" install watchdog; then
    echo "Error: pip failed to install watchdog into venv" >&2
    exit 1
fi

mkdir -p ~/.config/systemd/user/

cp "$REPO_DIR/jcrefresher.service" ~/.config/systemd/user/jcrefresher.service

# Inject PYTHONPATH and venv PATH into the [Service] section of the copied unit file
sed -i "/^\[Service\]/a Environment=PYTHONPATH=$REPO_DIR" ~/.config/systemd/user/jcrefresher.service
sed -i "/^\[Service\]/a Environment=PATH=$HOME/.local/bin:$HOME/.local/share/jcrefresher/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" ~/.config/systemd/user/jcrefresher.service

systemctl --user daemon-reload

systemctl --user enable jcrefresher

systemctl --user start jcrefresher

echo "jcrefresher installed and started. Check status with: systemctl --user status jcrefresher"
