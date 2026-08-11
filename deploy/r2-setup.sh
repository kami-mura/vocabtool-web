#!/usr/bin/env bash
# 在服务器上交互式配置 rclone 的 Cloudflare R2 remote。
# 密钥只写入服务器本地 rclone 配置文件，不进入仓库。
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${RCLONE_CONFIG:-${project_dir}/.rclone.conf}"
remote="${R2_REMOTE_NAME:-vocabflow-r2}"
bucket="${R2_BUCKET:-vocabflow-backups}"

rclone_bin="${RCLONE_BIN:-}"
if [[ -z "${rclone_bin}" && -x "${HOME}/.local/bin/rclone" ]]; then
  rclone_bin="${HOME}/.local/bin/rclone"
elif [[ -z "${rclone_bin}" ]]; then
  rclone_bin="rclone"
fi

if ! "${rclone_bin}" version >/dev/null 2>&1; then
  echo "请先安装 rclone（例如放入 ~/.local/bin/rclone）再运行本脚本。" >&2
  exit 1
fi

printf '输入 Cloudflare 账号 ID（R2 概览页可见）: '
read -r account_id
if [[ -z "${account_id}" ]]; then
  echo "账号 ID 不能为空" >&2
  exit 1
fi

printf '输入 R2 Access Key ID: '
read -r access_key_id
if [[ -z "${access_key_id}" ]]; then
  echo "Access Key ID 不能为空" >&2
  exit 1
fi

printf '输入 R2 Secret Access Key（输入时不显示）: '
read -r -s secret_access_key
echo
if [[ -z "${secret_access_key}" ]]; then
  echo "Secret Access Key 不能为空" >&2
  exit 1
fi

mkdir -p "$(dirname "${config}")"

RCLONE_CONFIG="${config}" "${rclone_bin}" config create \
  "${remote}" s3 \
  provider Cloudflare \
  access_key_id "${access_key_id}" \
  secret_access_key "${secret_access_key}" \
  endpoint "https://${account_id}.r2.cloudflarestorage.com" \
  region auto \
  --non-interactive

chmod 600 "${config}"

if RCLONE_CONFIG="${config}" "${rclone_bin}" lsf "${remote}:${bucket}" >/dev/null 2>&1; then
  echo "R2 连接成功：${remote}:${bucket}"
else
  RCLONE_CONFIG="${config}" "${rclone_bin}" mkdir "${remote}:${bucket}"
  echo "R2 bucket 已创建/确认：${remote}:${bucket}"
fi

echo "同步脚本默认使用 R2_REMOTE=${remote}:${bucket}，如 bucket 名不同请设置环境变量。"
