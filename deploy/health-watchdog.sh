#!/usr/bin/env bash
# 由 root cron 每分钟运行：healthz 连续 3 次失败才重启服务并记录。
# 单次失败不动作，避免部署重启、瞬时变慢时误杀；连续失败则说明假死。
set -uo pipefail

log=/var/log/vocabflow-watchdog.log
state=/var/run/vocabflow-watchdog.fail
alert_state=/var/run/vocabflow-watchdog.alerted
health_url=http://127.0.0.1:8000/healthz
max_failures=3
alert_cooldown_seconds=3600
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_user="${VOCABFLOW_SERVICE_USER:-$(stat -c %U "${project_dir}")}"
service_name="vocabflow@${service_user}.service"

notify() {
  if [[ -f "${alert_state}" ]] && \
     (( $(date +%s) - $(stat -c %Y "${alert_state}") < alert_cooldown_seconds )); then
    return
  fi
  "${project_dir}/deploy/notify-failure.sh" "$1" "$2" >>"${log}" 2>&1 || true
  touch "${alert_state}"
}

if curl -fsS --max-time 8 "${health_url}" >/dev/null 2>&1; then
  rm -f "${state}"
  rm -f "${alert_state}"
  exit 0
fi

failures=0
if [[ -f "${state}" ]]; then
  failures=$(cat "${state}" 2>/dev/null || echo 0)
fi
failures=$((failures + 1))
echo "${failures}" >"${state}"

if (( failures < max_failures )); then
  echo "$(date -Is) healthz 连续 ${failures}/${max_failures} 次失败（${failures} 次等待下次）" >>"${log}"
  exit 0
fi

echo "$(date -Is) healthz 连续 ${failures} 次失败，重启 ${service_name}" >>"${log}"
{
  echo "$(date -Is) 重启前诊断快照"
  systemctl status "${service_name}" --no-pager
  journalctl -u "${service_name}" -n 200 --no-pager
  ps -o pid,ppid,stat,%cpu,%mem,rss,vsz,etime,cmd -C uvicorn
  df -h "${project_dir}"
} >>"${log}" 2>&1 || true
rm -f "${state}"
systemctl restart "${service_name}" >>"${log}" 2>&1
sleep 4

if curl -fsS --max-time 8 "${health_url}" >/dev/null 2>&1; then
  echo "$(date -Is) 重启后已恢复" >>"${log}"
else
  echo "$(date -Is) 重启后仍不健康，请人工介入" >>"${log}"
  notify "VocabTool 重启后仍不健康" "重启 ${service_name} 后 healthz 仍失败，请人工介入。"
fi
