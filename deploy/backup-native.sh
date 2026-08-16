#!/usr/bin/env bash
# 本地备份：SQLite 在线备份 API。
# 所有产物先写 .part，校验通过后原子改名，避免半成品挤占有效恢复点；
# 通过 flock/mkdir 锁防止备份任务重叠。
set -euo pipefail

umask 077
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${1:-${project_dir}/backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "${backup_dir}"

# ---------- 防重叠 ----------
if command -v flock >/dev/null 2>&1; then
  exec 9>"${backup_dir}/.backup.lock"
  flock -n 9 || { echo "另一备份任务正在运行，本轮未生成新备份。" >&2; exit 1; }
else
  lock_dir="${backup_dir}/.backup-lock"
  if ! mkdir "${lock_dir}" 2>/dev/null; then
    running_pid="$(cat "${lock_dir}/pid" 2>/dev/null || true)"
    if [[ "${running_pid}" =~ ^[0-9]+$ ]] && kill -0 "${running_pid}" 2>/dev/null; then
      echo "另一备份任务正在运行，本轮未生成新备份。" >&2
      exit 1
    fi
    rm -f "${lock_dir}/pid"
    rmdir "${lock_dir}" 2>/dev/null || true
    mkdir "${lock_dir}"
  fi
  printf '%s\n' "$$" >"${lock_dir}/pid"
  trap 'rm -f "${lock_dir}/pid"; rmdir "${lock_dir}" 2>/dev/null || true' EXIT
fi

cd "${project_dir}"

# ---------- SQLite ----------
sqlite_source="${project_dir}/data/vocabflow.db"
database_backup_created=false
if [[ -f "${sqlite_source}" ]]; then
  sqlite_backup="${backup_dir}/vocabflow-sqlite-${timestamp}.db"
  "${project_dir}/.venv/bin/python" -c \
    'import sqlite3, sys; source=sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True); target=sqlite3.connect(sys.argv[2]); source.backup(target); target.close(); source.close()' \
    "${sqlite_source}" "${sqlite_backup}.part"
  integrity="$("${project_dir}/.venv/bin/python" -c \
    'import sqlite3, sys; db=sqlite3.connect("file:" + sys.argv[1] + "?mode=ro&immutable=1", uri=True); print(db.execute("PRAGMA integrity_check").fetchone()[0]); db.close()' \
    "${sqlite_backup}.part")"
  if [[ "${integrity}" != "ok" ]]; then
    rm -f "${sqlite_backup}.part"
    echo "SQLite 备份完整性检查失败" >&2
    exit 1
  fi
  mv "${sqlite_backup}.part" "${sqlite_backup}"
  chmod 600 "${sqlite_backup}"
  database_backup_created=true
fi

if [[ "${database_backup_created}" != true ]]; then
  echo "备份失败：没有生成任何有效数据库备份" >&2
  exit 1
fi

tar \
  --exclude='data/*.db' \
  --exclude='data/*.db-wal' \
  --exclude='data/*.db-shm' \
  --exclude='data/temp_uploads' \
  --exclude='data/tts' \
  -czf "${backup_dir}/vocabflow-data-${timestamp}.tar.gz.part" \
  data
mv "${backup_dir}/vocabflow-data-${timestamp}.tar.gz.part" \
  "${backup_dir}/vocabflow-data-${timestamp}.tar.gz"

"${project_dir}/deploy/prune-backups.sh" "${backup_dir}"

echo "备份完成：${backup_dir}"
