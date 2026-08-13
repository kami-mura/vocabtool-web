#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_user="$(id -un)"
service_name="vocabflow@${service_user}.service"

cd "${project_dir}"
./deploy/backup-native.sh
.venv/bin/python -m pip install --upgrade "pip>=26.1.2"
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "from app.main import app; assert app"

sudo systemctl restart "${service_name}"

for attempt in $(seq 1 15); do
  if curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:8000/healthz >/dev/null; then
    echo "更新成功：${service_name}"
    exit 0
  fi
  sleep 1
done

echo "健康检查失败，请运行：sudo journalctl -u ${service_name} -n 80 --no-pager" >&2
exit 1
