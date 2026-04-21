#!/usr/bin/env bash
set -euo pipefail

systemctl --user stop jcrefresher || true

systemctl --user disable jcrefresher || true

rm -f ~/.config/systemd/user/jcrefresher.service

rm -rf ~/.local/share/jcrefresher/

systemctl --user daemon-reload

echo "jcrefresher uninstalled."
