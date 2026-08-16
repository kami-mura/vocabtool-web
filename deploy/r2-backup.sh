#!/usr/bin/env bash
# 将本地备份同步到 Cloudflare R2，并按天数清理 R2 上的旧对象。
# 默认 remote 为 vocabflow-r2:vocabflow-backups，可用 R2_REMOTE / R2_KEEP_DAYS 覆盖。
# 凭据优先读取 /etc/vocabflow/rclone.conf（root-only，发布同步永不触碰），
# 不存在时回退到应用目录，兼容旧配置。
# 远端清理只在本轮上传成功后执行，且始终保留最新 R2_KEEP_MIN 个对象，
# 连续失败时不会把最后一个有效异地备份删掉。
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${1:-${project_dir}/backups}"
remote="${R2_REMOTE:-vocabflow-r2:vocabflow-backups}"
keep_days="${R2_KEEP_DAYS:-7}"
keep_min="${R2_KEEP_MIN:-2}"

rclone_config="${RCLONE_CONFIG:-}"
if [[ -z "${rclone_config}" ]]; then
  if [[ -f /etc/vocabflow/rclone.conf ]]; then
    rclone_config="/etc/vocabflow/rclone.conf"
  else
    rclone_config="${project_dir}/.rclone.conf"
  fi
fi

rclone_bin="${RCLONE_BIN:-}"
if [[ -z "${rclone_bin}" && -x "${HOME}/.local/bin/rclone" ]]; then
  rclone_bin="${HOME}/.local/bin/rclone"
elif [[ -z "${rclone_bin}" ]]; then
  rclone_bin="rclone"
fi

if ! "${rclone_bin}" version >/dev/null 2>&1; then
  echo "R2 同步失败：服务器未安装 rclone" >&2
  exit 1
fi

if [[ ! -f "${rclone_config}" ]]; then
  echo "R2 同步失败：配置文件不存在 ${rclone_config}" >&2
  exit 1
fi

if [[ ! -d "${backup_dir}" ]]; then
  echo "R2 同步失败：备份目录不存在 ${backup_dir}" >&2
  exit 1
fi

include_args=(
  --include 'vocabflow-sqlite-*.db'
  --include 'vocabflow-data-*.tar.gz'
  --exclude '*'
)

"${rclone_bin}" --config "${rclone_config}" mkdir "${remote}" >/dev/null 2>&1 || true

if ! "${rclone_bin}" --config "${rclone_config}" lsf "${remote}" >/dev/null 2>&1; then
  echo "R2 同步失败：无法访问 ${remote}，请先运行 deploy/r2-setup.sh 并确认 bucket 存在" >&2
  exit 1
fi

# 上传成功后才允许远端清理；上传失败直接退出（set -e）。
"${rclone_bin}" --config "${rclone_config}" copy "${backup_dir}" "${remote}" \
  "${include_args[@]}" \
  --transfers 2 \
  --checkers 4 \
  --log-level INFO

# 只清理超过 keep_days 的旧对象，且跳过最新的 keep_min 个，
# 保证远端始终保有最低数量的恢复点。逐对象用 rclone 自带的
# --min-age 判断年龄，避免手工解析时间戳出错。
mapfile -t remote_files < <(
  "${rclone_bin}" --config "${rclone_config}" lsf "${remote}" \
    "${include_args[@]}" | sort -r || true
)
index=0
for name in "${remote_files[@]}"; do
  index=$((index + 1))
  if (( index <= keep_min )); then
    continue
  fi
  "${rclone_bin}" --config "${rclone_config}" delete "${remote}" \
    --include "${name}" \
    --exclude '*' \
    --min-age "${keep_days}d" >/dev/null 2>&1 || true
done

count="$("${rclone_bin}" --config "${rclone_config}" lsf "${remote}" \
  "${include_args[@]}" | wc -l | tr -d ' ')"
echo "R2 同步完成：${remote} 当前 ${count} 个备份对象（保留约 ${keep_days} 天）"
