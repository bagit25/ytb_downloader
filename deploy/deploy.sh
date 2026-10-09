#!/usr/bin/env bash
# Pull the latest code and restart. Run by GitHub Actions over SSH on every push to main
# (and weekly, to keep yt-dlp current). Safe to run by hand too.
set -euo pipefail
cd /home/ubuntu/ytb_downloader

git fetch --quiet origin main
git reset --hard origin/main

./venv/bin/pip install --quiet -r requirements.txt
./venv/bin/pip install --quiet --upgrade "yt-dlp[default]"

# Pick up changes to the service / nginx files if any.
sudo cp deploy/ytapp.service /etc/systemd/system/ytapp.service
sudo systemctl daemon-reload
sudo systemctl restart ytapp

for i in $(seq 1 15); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null; then
    echo "Deployed $(git rev-parse --short HEAD), yt-dlp $(./venv/bin/yt-dlp --version)"
    exit 0
  fi
  sleep 1
done
echo "App did not come back up:" >&2
sudo journalctl -u ytapp -n 40 --no-pager >&2
exit 1
