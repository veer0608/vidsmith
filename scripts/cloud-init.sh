#!/usr/bin/env bash
# Everything a fresh Ubuntu box needs, as one paste.
#
# Goes in the "User data" box when launching the instance, so the machine is
# ready by the time you can SSH to it. Runs as root, once, at first boot.
#
#   EC2:       Advanced details -> User data
#   Lightsail: Launch script
#
# It deliberately contains NO secrets. User data is readable from the instance
# metadata endpoint and visible in the console, so anything pasted here should
# be assumed public. Keys go in /home/ubuntu/vidsmith/.env afterwards, by hand.
#
# Progress and failures land in /var/log/vidsmith-setup.log. If the service is
# not up, read that file before touching anything else.
set -euxo pipefail
exec > >(tee -a /var/log/vidsmith-setup.log) 2>&1

USER_NAME="ubuntu"
APP_DIR="/home/${USER_NAME}/vidsmith"

echo "=== packages ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update
# ffmpeg from apt is built with libass. A build without it has no `subtitles`
# filter at all, and ffmpeg reports that as "No option name near <path>", which
# reads exactly like a quoting fault and is not one.
apt-get install -y --no-install-recommends \
    ffmpeg python3-venv python3-pip git curl fonts-dejavu-core

echo "=== swap ==="
# A 1080p encode is what runs out of memory on a 2 GB box, and an encode killed
# by the OOM reaper looks like a crash with no explanation.
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "=== application ==="
if [ ! -d "${APP_DIR}/.git" ]; then
    sudo -u "${USER_NAME}" git clone https://github.com/veer0608/vidsmith.git "${APP_DIR}"
fi
sudo -u "${USER_NAME}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${USER_NAME}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${USER_NAME}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements-web.txt"

# so the first boot has somewhere to write jobs, and .env exists to be edited
sudo -u "${USER_NAME}" mkdir -p "${APP_DIR}/jobs"
sudo -u "${USER_NAME}" touch "${APP_DIR}/.env"
chmod 600 "${APP_DIR}/.env"

echo "=== service ==="
# Binds to loopback on purpose. The reverse proxy is the only thing exposed,
# because the page passes its token as a query parameter for media links that
# cannot set a header, and that token is what stands in front of the API quota.
cat > /etc/systemd/system/vidsmith.service <<UNIT
[Unit]
Description=vidsmith
After=network.target

[Service]
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
Environment=VIDSMITH_MAX_MINUTES=3
Environment=VIDSMITH_JOBS=${APP_DIR}/jobs
ExecStart=${APP_DIR}/.venv/bin/uvicorn web.app:app --host 127.0.0.1 --port 8077 --timeout-keep-alive 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now vidsmith

echo "=== reverse proxy ==="
apt-get install -y --no-install-recommends caddy || {
    echo "caddy is not in this release's archive; installing from the official repo"
    apt-get install -y --no-install-recommends debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update
    apt-get install -y caddy
}

# Serves plain HTTP against the instance IP until a domain is pointed here.
# That is fine for the first health check and NOT fine for a link you hand out:
# the token would cross the network in cleartext. Replace :80 with your
# hostname and Caddy will get a certificate on its own.
cat > /etc/caddy/Caddyfile <<'PROXY'
:80 {
    reverse_proxy 127.0.0.1:8077
}
PROXY
systemctl restart caddy

echo "=== done ==="
"${APP_DIR}/.venv/bin/python" -m vidsmith doctor || true
echo "vidsmith is installed. Add keys to ${APP_DIR}/.env and:"
echo "  sudo systemctl restart vidsmith"
