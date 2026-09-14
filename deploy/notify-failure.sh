#!/usr/bin/env bash
# 通过 Resend 发送运维告警邮件，由 health-watchdog.sh 调用。
# 配置放在 root-only 的 /etc/vocabflow-alert.env：
#   RESEND_API_KEY=...
#   EMAIL_FROM=...
#   ALERT_TO=...
set -uo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
subject="${1:-VocabTool 故障通知}"
message="${2:-}"

# 告警发不出去时不能完全无声：否则告警通道整体失效也无人察觉。
# 仍然 exit 0（不能影响看门狗本身的判断与重启逻辑），但留下审计痕迹。
skip() {
  local reason="$1"
  local stamp
  stamp="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "${stamp} 告警未发送：${reason}（subject=${subject}）" >&2
  local log_dir="${project_dir}/backups"
  if [[ -d "${log_dir}" && -w "${log_dir}" ]]; then
    echo "${stamp} 告警未发送：${reason}（subject=${subject}）" >>"${log_dir}/alert.log"
  fi
  exit 0
}

alert_config="${VOCABFLOW_ALERT_ENV:-/etc/vocabflow-alert.env}"
if [[ ! -f "${alert_config}" ]]; then
  skip "缺少告警配置 ${alert_config}"
fi

set -a
# shellcheck disable=SC1090
source "${alert_config}"
set +a

alert_to="${ALERT_TO:-}"
if [[ -z "${alert_to}" ]]; then
  skip "告警配置未设置 ALERT_TO"
fi

ALERT_TO="${alert_to}" SUBJECT="${subject}" MESSAGE="${message}" \
  "${project_dir}/.venv/bin/python" - <<'PY'
import json
import os
import sys
import urllib.request

api_key = os.environ.get("RESEND_API_KEY", "")
email_from = os.environ.get("EMAIL_FROM", "")
alert_to = os.environ.get("ALERT_TO", "")
subject = os.environ.get("SUBJECT", "VocabTool 故障通知")
message = os.environ.get("MESSAGE", "")
if not api_key or not email_from or "@" not in alert_to:
    print(
        "告警未发送：RESEND_API_KEY / EMAIL_FROM / ALERT_TO 配置不完整",
        file=sys.stderr,
    )
    sys.exit(0)

payload = {
    "from": email_from,
    "to": [alert_to],
    "subject": subject,
    "text": message or "VocabTool 服务器异常，请检查。",
}
request = urllib.request.Request(
    "https://api.resend.com/emails",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "VocabTool-Watchdog/1.0",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()
except Exception as exc:
    # 通知失败不能影响看门狗本身的判断与重启逻辑，但要留下痕迹，
    # 否则告警通道静默失效时无人知道。
    print(f"告警发送失败：{type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(0)
PY
