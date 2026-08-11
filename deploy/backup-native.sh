#!/usr/bin/env bash
set -euo pipefail

umask 077
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${1:-${project_dir}/backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "${backup_dir}"
cd "${project_dir}"

postgres_backup="${backup_dir}/vocabflow-db-${timestamp}.dump"
database_backup_created=false
if pg_dump \
  --dbname=vocabflow \
  --format=custom \
  --file="${postgres_backup}"; then
  if ! pg_restore --list "${postgres_backup}" >/dev/null; then
    rm -f "${postgres_backup}"
    echo "PostgreSQL 备份校验失败" >&2
    exit 1
  fi
  chmod 600 "${postgres_backup}"
  database_backup_created=true
else
  rm -f "${postgres_backup}"
  echo "提示：PostgreSQL 备份未生成，继续检查 SQLite。" >&2
fi

sqlite_source="${project_dir}/data/vocabflow.db"
if [[ -f "${sqlite_source}" ]]; then
  sqlite_backup="${backup_dir}/vocabflow-sqlite-${timestamp}.db"
  .venv/bin/python -c \
    'import sqlite3, sys; source=sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True); target=sqlite3.connect(sys.argv[2]); source.backup(target); target.close(); source.close()' \
    "${sqlite_source}" "${sqlite_backup}"
  chmod 600 "${sqlite_backup}"
  integrity="$(${project_dir}/.venv/bin/python -c \
    'import sqlite3, sys; db=sqlite3.connect("file:" + sys.argv[1] + "?mode=ro&immutable=1", uri=True); print(db.execute("PRAGMA integrity_check").fetchone()[0]); db.close()' \
    "${sqlite_backup}")"
  rm -f "${sqlite_backup}-shm" "${sqlite_backup}-wal"
  if [[ "${integrity}" != "ok" ]]; then
    echo "SQLite 备份完整性检查失败" >&2
    exit 1
  fi
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
  -czf "${backup_dir}/vocabflow-data-${timestamp}.tar.gz" \
  data

"${project_dir}/deploy/prune-backups.sh" "${backup_dir}"

echo "备份完成：${backup_dir}"
