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

alert_config="${VOCABFLOW_ALERT_ENV:-/etc/vocabflow-alert.env}"
if [[ ! -f "${alert_config}" ]]; then
  exit 0
fi

set -a
# shellcheck disable=SC1090
source "${alert_config}"
set +a

alert_to="${ALERT_TO:-}"
if [[ -z "${alert_to}" ]]; then
  exit 0
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
except Exception:
    # 通知失败不能影响看门狗本身的判断与重启逻辑。
    sys.exit(0)
PY
