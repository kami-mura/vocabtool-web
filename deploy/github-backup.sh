#!/usr/bin/env bash
# 将本地备份同步到 GitHub 私有仓库（异地备份，不依赖 Mac）。
# 凭据读取 root-only 的 /etc/vocabflow-github.env：
#   GITHUB_TOKEN=<Personal Access Token，repo 权限>
#   GITHUB_REPO=<owner/repo>
# 远端始终只有最近 KEEP 份备份、单提交历史（force push），仓库不会无限膨胀。
# 可选 GITHUB_LOCAL_KEEP：同步后清理本地备份目录，只保留最近 N 份（默认不清理）。
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${1:-${project_dir}/backups}"
keep="${GITHUB_KEEP:-6}"
local_keep="${GITHUB_LOCAL_KEEP:-}"
env_file="${VOCABFLOW_GITHUB_ENV:-/etc/vocabflow-github.env}"
work_dir="${GITHUB_WORK_DIR:-${project_dir}/.github-backup-work}"

if [[ ! -f "${env_file}" ]]; then
  echo "GitHub 备份失败：配置文件不存在 ${env_file}" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "${env_file}"

if [[ -z "${GITHUB_TOKEN:-}" || -z "${GITHUB_REPO:-}" ]]; then
  echo "GitHub 备份失败：缺少 GITHUB_TOKEN / GITHUB_REPO" >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "GitHub 备份失败：未安装 git" >&2
  exit 1
fi

if [[ ! -d "${backup_dir}" ]]; then
  echo "GitHub 备份失败：备份目录不存在 ${backup_dir}" >&2
  exit 1
fi

latest="$(ls -t "${backup_dir}"/vocabflow-sqlite-*.db 2>/dev/null | head -1 || true)"
if [[ -z "${latest}" ]]; then
  echo "GitHub 备份失败：没有可推送的备份文件" >&2
  exit 1
fi

remote_url="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"

mkdir -p "${work_dir}"
cd "${work_dir}"
if [[ ! -d .git ]]; then
  git init -q
  git config user.email "vocabflow-backup@localhost"
  git config user.name "vocabflow-backup"
  git remote add origin "${remote_url}" 2>/dev/null || git remote set-url origin "${remote_url}"
else
  git config remote.origin.url "${remote_url}"
fi

rm -rf "${work_dir}"/vocabflow-sqlite-*.db
cp "${latest}" "${work_dir}/"
echo "${latest}" > "${work_dir}/.latest-source"

# 清理本地工作目录，只保留最近 keep 份。
ls -t "${backup_dir}"/vocabflow-sqlite-*.db 2>/dev/null | tail -n +"$((keep + 1))" | while read -r f; do :; done
files=()
for f in $(ls -t "${backup_dir}"/vocabflow-sqlite-*.db 2>/dev/null | head -n "${keep}"); do
  files+=("${f}")
done
for f in "${files[@]:1}"; do
  cp -f "${f}" "${work_dir}/"
done

git add -A
if git diff --cached --quiet; then
  echo "GitHub 同步跳过：无新备份"
  exit 0
fi
git commit -qm "backup $(basename "${latest}")"

# force push 单分支，远端历史始终只有一个提交，避免仓库无限膨胀。
if ! git push -q --force origin HEAD:main 2>/tmp/vocabflow-gh-push.err; then
  echo "GitHub 备份失败：push 失败" >&2
  cat /tmp/vocabflow-gh-push.err >&2
  exit 1
fi

count="$(ls "${work_dir}"/vocabflow-sqlite-*.db 2>/dev/null | wc -l | tr -d ' ')"
echo "GitHub 同步完成：${GITHUB_REPO} 当前 ${count} 份备份（保留 ${keep} 份）"

# 可选：清理本地备份目录，只保留最近 local_keep 份。
if [[ -n "${local_keep}" ]]; then
  old_count=0
  for f in $(ls -t "${backup_dir}"/vocabflow-sqlite-*.db 2>/dev/null | tail -n +"$((local_keep + 1))"); do
    rm -f "${f}"
    old_count=$((old_count + 1))
  done
  if [[ "${old_count}" -gt 0 ]]; then
    echo "已清理本地旧备份 ${old_count} 份（保留最近 ${local_keep} 份）"
  fi
fi