#!/usr/bin/env bash
# 备份保留策略：按文件名时间戳保留最近 BACKUP_KEEP（默认 7，至少 1）份，
# 更早的删除；同时清理超过 1 天的 .part 残留（原子改名失败/中断的半成品）。
set -euo pipefail

backup_dir="${1:-}"
keep="${BACKUP_KEEP:-7}"
if (( keep < 1 )); then
  keep=1
fi
if [[ -z "${backup_dir}" || ! -d "${backup_dir}" ]]; then
  echo "备份目录不存在：${backup_dir}" >&2
  exit 1
fi

pruned=0
for pattern in 'vocabflow-db-*.dump' 'vocabflow-sqlite-*.db' 'vocabflow-data-*.tar.gz'; do
  index=0
  while IFS= read -r -d '' file; do
    index=$((index + 1))
    if (( index > keep )); then
      rm -f -- "${file}"
      pruned=$((pruned + 1))
    fi
  done < <(find "${backup_dir}" -maxdepth 1 -type f -name "${pattern}" -print0 | sort -zr)
done

stale_pruned=0
while IFS= read -r -d '' file; do
  rm -f -- "${file}"
  stale_pruned=$((stale_pruned + 1))
done < <(find "${backup_dir}" -maxdepth 1 -type f -name '*.part' -mtime +1 -print0)

if (( pruned > 0 )); then
  echo "已清理旧备份 ${pruned} 份，保留最近 ${keep} 份。"
fi
if (( stale_pruned > 0 )); then
  echo "已清理过期 .part 残留 ${stale_pruned} 份。"
fi
