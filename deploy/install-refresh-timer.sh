#!/usr/bin/env bash
# 安装并启用「阅读卡例句每日轮换」systemd timer（每天凌晨 4 点）。
# 用法（在服务器上执行，需有 sudo）：
#   ./deploy/install-refresh-timer.sh <用户名>
# 示例：./deploy/install-refresh-timer.sh $(id -un)
set -euo pipefail

service_user="${1:-$(id -un)}"
unit="vocabflow-refresh-sentences@${service_user}"

echo "安装定时任务：${unit}（用户 ${service_user}）"
sudo cp "/opt/vocabflow/deploy/vocabflow-refresh-sentences@.service" "/etc/systemd/system/${unit}.service"
sudo cp "/opt/vocabflow/deploy/vocabflow-refresh-sentences@.timer" "/etc/systemd/system/${unit}.timer"
sudo systemctl daemon-reload
sudo systemctl enable --now "${unit}.timer"
sudo systemctl status "${unit}.timer" --no-pager | head -10
echo "完成。可用 sudo systemctl list-timers 查看下次执行时间。"
