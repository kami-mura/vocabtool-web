#!/bin/bash
# macOS 每日备份：让 Linux 先生成并校验 SQLite 快照，再拉取到 Mac。
set -euo pipefail

umask 077

backup_dir="${VOCABFLOW_MAC_BACKUP_DIR:-${HOME}/vocabflow-backups}"
remote_dir="/opt/vocabflow/backups"
success_marker="${backup_dir}/.last-success"
failure_marker="${backup_dir}/.last-failure"
lock_dir="${backup_dir}/.backup-pull.lock"
today="$(date '+%Y-%m-%d')"

mkdir -p "${backup_dir}"

if ! mkdir "${lock_dir}" 2>/dev/null; then
  running_pid="$(cat "${lock_dir}/pid" 2>/dev/null || true)"
  if [[ "${running_pid}" =~ ^[0-9]+$ ]] && kill -0 "${running_pid}" 2>/dev/null; then
    echo "备份任务已在运行，跳过重复启动。"
    exit 0
  fi
  rm -f "${lock_dir}/pid"
  rmdir "${lock_dir}" 2>/dev/null || true
  mkdir "${lock_dir}"
fi
printf '%s\n' "$$" >"${lock_dir}/pid"
cleanup() {
  rm -f "${lock_dir}/pid"
  rmdir "${lock_dir}" 2>/dev/null || true
}
fail() {
  local status="${1:-1}"
  local message="${2:-}"
  printf '%s status=%s message=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" \
    "${status}" "${message}" >"${failure_marker}"
  if [[ -n "${message}" ]]; then
    echo "备份失败：${message}" >&2
  fi
  exit "${status}"
}
on_error() {
  status=$?
  printf '%s status=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "${status}" \
    >"${failure_marker}"
  cleanup
  exit "${status}"
}
trap on_error ERR INT TERM
trap cleanup EXIT

# 当天已经生成并验证成功时直接返回。LaunchAgent 可以一天多次触发，
# 既能在网络恢复后补偿重试，也不会每天制造多份重复快照。
if [[ -f "${success_marker}" ]]; then
  marker_day=""
  marker_file=""
  read -r marker_day marker_file <"${success_marker}" || true
  if [[ "${marker_day}" == "${today}" && -n "${marker_file}" && \
        -f "${backup_dir}/${marker_file}" ]]; then
    integrity="$(/usr/bin/sqlite3 "${backup_dir}/${marker_file}" 'PRAGMA integrity_check;')"
    if [[ "${integrity}" == "ok" ]]; then
      echo "今日备份已验证：${backup_dir}/${marker_file}"
      rm -f "${failure_marker}"
      exit 0
    fi
  fi
fi

ssh_options=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ConnectionAttempts=2
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=2
)
remote_candidates=(
  "vocab-server"
  "shangcunyu@192.168.10.171"
)

selected_remote=""
for remote in "${remote_candidates[@]}"; do
  if /usr/bin/ssh "${ssh_options[@]}" "${remote}" 'test -x /opt/vocabflow/deploy/backup-native.sh'; then
    selected_remote="${remote}"
    break
  fi
done
if [[ -z "${selected_remote}" ]]; then
  fail 1 "Tailscale 与局域网地址均无法连接。"
fi

echo "开始在 Linux 生成数据库快照：${selected_remote}"
remote_output="$(
  /usr/bin/ssh "${ssh_options[@]}" "${selected_remote}" \
    'cd /opt/vocabflow && ./deploy/backup-native.sh /opt/vocabflow/backups && find /opt/vocabflow/backups -maxdepth 1 -type f -name "vocabflow-sqlite-*.db" -printf "%f\n" | sort | tail -n 1'
)"
remote_file="${remote_output##*$'\n'}"
if [[ ! "${remote_file}" =~ ^vocabflow-sqlite-[0-9]{8}T[0-9]{6}Z\.db$ ]]; then
  fail 1 "服务器没有返回有效的 SQLite 快照文件名。"
fi

rsync_ssh="/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=2 -o ServerAliveInterval=10 -o ServerAliveCountMax=2"
/usr/bin/rsync -az -e "${rsync_ssh}" \
  "${selected_remote}:${remote_dir}/${remote_file}" "${backup_dir}/"

local_file="${backup_dir}/${remote_file}"
if [[ ! -s "${local_file}" ]]; then
  fail 1 "Mac 上没有收到有效文件 ${local_file}"
fi
integrity="$(/usr/bin/sqlite3 "${local_file}" 'PRAGMA integrity_check;')"
if [[ "${integrity}" != "ok" ]]; then
  fail 1 "Mac 上的 SQLite 完整性检查未通过。"
fi

marker_tmp="${success_marker}.tmp.$$"
printf '%s %s\n' "${today}" "${remote_file}" >"${marker_tmp}"
mv -f "${marker_tmp}" "${success_marker}"
rm -f "${failure_marker}"

# 只有本轮新备份成功并通过本地完整性校验后才清理 14 天前的旧快照。
find "${backup_dir}" -maxdepth 1 -type f -name 'vocabflow-sqlite-*.db' \
  -mtime +14 -delete

echo "备份成功：${local_file}（SQLite integrity_check=ok）"
