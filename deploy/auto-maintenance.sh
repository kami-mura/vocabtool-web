#!/usr/bin/env bash
set -uo pipefail

project_dir="/opt/vocabflow"
log="${project_dir}/backups/maintenance.log"
stamp="$(date '+%Y-%m-%d %H:%M:%S')"

if [[ -f "${log}" ]]; then
  tail -n 2000 "${log}" > "${log}.tmp" 2>/dev/null || true
  mv -f "${log}.tmp" "${log}" 2>/dev/null || true
fi

{
  echo "=== ${stamp} ==="
  if "${project_dir}/deploy/backup-native.sh" "${project_dir}/backups" \
    >/tmp/vocabflow-backup.out 2>&1; then
    echo "BACKUP_OK"
  else
    echo "BACKUP_FAIL"
    tail -5 /tmp/vocabflow-backup.out
  fi
  if [[ -x "${project_dir}/deploy/r2-backup.sh" ]] && \
     [[ -f "${project_dir}/.rclone.conf" ]]; then
    if "${project_dir}/deploy/r2-backup.sh" "${project_dir}/backups" \
      >/tmp/vocabflow-r2.out 2>&1; then
      echo "R2_OK"
    else
      echo "R2_FAIL"
      tail -5 /tmp/vocabflow-r2.out
    fi
  else
    echo "R2_SKIP（未配置 .rclone.conf）"
  fi
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "HEALTH_OK"
  else
    echo "HEALTH_FAIL"
  fi
  if "${project_dir}/.venv/bin/python" -m pip check >/dev/null 2>&1; then
    echo "PIP_CHECK_OK"
  else
    echo "PIP_CHECK_FAIL"
  fi
} >>"${log}" 2>&1
