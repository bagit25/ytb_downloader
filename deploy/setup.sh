#!/usr/bin/env bash
# One-time bootstrap of an Oracle Cloud Ubuntu VM (ARM A1 or AMD micro).
#
# Usage (on the VM, as the "ubuntu" user):
#   bash setup.sh <git-clone-url> [domain]
# Examples:
#   bash setup.sh https://github.com/USER/ytb_downloader.git
#   bash setup.sh git@github.com:USER/ytb_downloader.git tube.example.com
set -euo pipefail

REPO_URL="${1:?Give the GitHub clone URL as first argument}"
DOMAIN="${2:-}"
APP_DIR="/home/ubuntu/ytb_downloader"

echo "==> System packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3-venv python3-pip nginx ffmpeg git curl unzip iptables-persistent

echo "==> Deno (JavaScript runtime yt-dlp needs for YouTube)"
if ! command -v deno >/dev/null; then
  curl -fsSL https://deno.land/install.sh | sudo DENO_INSTALL=/usr/local sh -s -- -y
fi
deno --version | head -1

echo "==> Swap (helps on 1 GB instances)"
if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Code"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
chmod +x deploy/*.sh

echo "==> Firewall inside the VM (Oracle images block 80/443 by default)"
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 6 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
sudo netfilter-persistent save

echo "==> systemd service"
sudo cp deploy/ytapp.service /etc/systemd/system/ytapp.service
sudo systemctl daemon-reload
sudo systemctl enable --now ytapp

echo "==> Nginx"
sudo sed "s/__SERVER_NAME__/${DOMAIN:-_}/" deploy/nginx.conf | sudo tee /etc/nginx/sites-available/ytapp >/dev/null
sudo ln -sf /etc/nginx/sites-available/ytapp /etc/nginx/sites-enabled/ytapp
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

if [ -n "$DOMAIN" ]; then
  echo "==> HTTPS certificate for $DOMAIN"
  sudo apt-get install -y certbot python3-certbot-nginx
  sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
    --register-unsafely-without-email --redirect || \
    echo "!! certbot failed: check that $DOMAIN points to this VM, then rerun: sudo certbot --nginx -d $DOMAIN"
fi

sleep 2
curl -fsS http://127.0.0.1:8000/healthz && echo
IP=$(curl -fsS https://ifconfig.me || echo "<public-ip>")
echo "Done. Open: http://${DOMAIN:-$IP}/"
