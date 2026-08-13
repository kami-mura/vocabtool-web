#!/usr/bin/env bash
# 每日维护：备份（本地 + R2）、健康检查、依赖检查。
# 任一关键步骤失败：跳过依赖它的后续步骤、调用 notify-failure.sh 告警，
# 并以非零退出码结束（fail closed），让 cron/监控能够感知失败。
set -uo pipefail

project_dir="/opt/vocabflow"
log="${project_dir}/backups/maintenance.log"
stamp="$(date '+%Y-%m-%d %H:%M:%S')"

if [[ -f "${log}" ]]; then
  tail -n 2000 "${log}" > "${log}.tmp" 2>/dev/null || true
  mv -f "${log}.tmp" "${log}" 2>/dev/null || true
fi

overall_fail=0

notify() {
  "${project_dir}/deploy/notify-failure.sh" "$1" "$2" >/dev/null 2>&1 || true
}

{
  echo "=== ${stamp} ==="

  # 防重叠：维护任务与部署触发的备份串行执行。
  mkdir -p "${project_dir}/backups"
  lock_file="${project_dir}/backups/.maintenance.lock"
  if command -v flock >/dev/null 2>&1; then
    exec 9>"${lock_file}"
    flock -n 9 || {
      echo "MAINTENANCE_SKIP（另一维护/备份任务正在运行）"
      exit 0
    }
  else
    lock_dir="${project_dir}/backups/.maintenance-lock"
    if ! mkdir "${lock_dir}" 2>/dev/null; then
      running_pid="$(cat "${lock_dir}/pid" 2>/dev/null || true)"
      if [[ "${running_pid}" =~ ^[0-9]+$ ]] && kill -0 "${running_pid}" 2>/dev/null; then
        echo "MAINTENANCE_SKIP（另一维护/备份任务正在运行）"
        exit 0
      fi
      rm -f "${lock_dir}/pid"
      rmdir "${lock_dir}" 2>/dev/null || true
      mkdir "${lock_dir}"
    fi
    printf '%s\n' "$$" >"${lock_dir}/pid"
    trap 'rm -f "${lock_dir}/pid"; rmdir "${lock_dir}" 2>/dev/null || true' EXIT
  fi

  if "${project_dir}/deploy/backup-native.sh" "${project_dir}/backups" \
    >/tmp/vocabflow-backup.out 2>&1; then
    echo "BACKUP_OK"
  else
    echo "BACKUP_FAIL"
    tail -5 /tmp/vocabflow-backup.out
    overall_fail=1
    notify "VocabTool 本地备份失败" "backup-native.sh 失败，请检查 ${project_dir}/backups/maintenance.log。"
  fi

  rclone_config=""
  if [[ -f /etc/vocabflow/rclone.conf ]]; then
    rclone_config="/etc/vocabflow/rclone.conf"
  elif [[ -f "${project_dir}/.rclone.conf" ]]; then
    rclone_config="${project_dir}/.rclone.conf"
  fi

  # 本地备份失败时跳过 R2 同步与远端清理：不能让滚动删除把
  # 最后一个有效异地备份也清掉。
  if [[ "${overall_fail}" -eq 1 ]]; then
    echo "R2_SKIP（本地备份失败，本轮不执行远端同步与清理）"
  elif [[ -x "${project_dir}/deploy/r2-backup.sh" ]] && [[ -n "${rclone_config}" ]]; then
    if RCLONE_CONFIG="${rclone_config}" "${project_dir}/deploy/r2-backup.sh" "${project_dir}/backups" \
      >/tmp/vocabflow-r2.out 2>&1; then
      echo "R2_OK"
    else
      echo "R2_FAIL"
      tail -5 /tmp/vocabflow-r2.out
      overall_fail=1
      notify "VocabTool R2 同步失败" "r2-backup.sh 失败，请检查 ${project_dir}/backups/maintenance.log。"
    fi
  else
    echo "R2_SKIP（未配置 rclone 配置文件）"
  fi

  if curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "HEALTH_OK"
  else
    echo "HEALTH_FAIL"
    overall_fail=1
  fi

  if "${project_dir}/.venv/bin/python" -m pip check >/dev/null 2>&1; then
    echo "PIP_CHECK_OK"
  else
    echo "PIP_CHECK_FAIL"
    overall_fail=1
    notify "VocabTool 依赖检查失败" "pip check 失败，请检查 ${project_dir}/backups/maintenance.log。"
  fi
} >>"${log}" 2>&1

exit "${overall_fail}"
