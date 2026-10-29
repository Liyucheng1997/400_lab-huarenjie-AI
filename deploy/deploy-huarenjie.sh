#!/usr/bin/env bash
# 服务器上的 /usr/local/bin/deploy-huarenjie：GitHub Actions 通过受限 SSH 钥匙只能执行这个脚本
set -Eeuo pipefail

exec 9>/home/ubuntu/.huarenjie-deploy.lock
if ! flock -n 9; then
  echo "Another huarenjie deployment is already running" >&2
  exit 1
fi

cd /opt/apps/huarenjie

git fetch --prune origin main
git merge --ff-only origin/main
.venv/bin/pip install --quiet -r requirements.txt
sudo -n systemctl restart app-huarenjie.service

for _ in {1..15}; do
  if curl --fail --silent --show-error http://127.0.0.1:4400/api/health >/dev/null; then
    git rev-parse --short HEAD
    exit 0
  fi
  sleep 1
done

echo "huarenjie health check failed after deployment" >&2
systemctl status app-huarenjie.service --no-pager -l >&2 || true
exit 1
