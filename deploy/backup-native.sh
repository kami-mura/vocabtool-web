#!/usr/bin/env bash
# 本地备份：PostgreSQL（custom dump）或 SQLite（在线备份 API）。
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

database_url="${DATABASE_URL:-}"
if [[ -z "${database_url}" && -f "${project_dir}/.env" ]]; then
  database_url="$(grep -E '^DATABASE_URL=' "${project_dir}/.env" | tail -n1 | cut -d= -f2- | tr -d '"' || true)"
fi
postgres_backend=false
if [[ "${database_url}" == postgres* ]]; then
  postgres_backend=true
fi

# ---------- PostgreSQL ----------
postgres_backup="${backup_dir}/vocabflow-db-${timestamp}.dump"
database_backup_created=false

pg_args=(--dbname=vocabflow)
if [[ "${postgres_backend}" == true ]]; then
  # 从 DATABASE_URL 解析连接目标；解析不出数据库名时宁可失败，
  # 也不能 dump 到错误的库。
  url_body="${database_url#postgresql+psycopg2://}"
  url_body="${url_body#postgresql://}"
  dbname="${url_body#*/}"
  dbname="${dbname%%\?*}"
  if [[ -z "${dbname}" ]]; then
    echo "PostgreSQL 备份失败：无法从 DATABASE_URL 解析数据库名" >&2
    exit 1
  fi
  host_part="${url_body%%/*}"
  if [[ -n "${host_part}" ]]; then
    auth="${host_part%@*}"
    host_port="${host_part#*@}"
    if [[ "${host_part}" != "${host_port}" ]]; then
      export PGUSER="${auth%%:*}"
      export PGPASSWORD="${auth#*:}"
      export PGHOST="${host_port%%:*}"
      if [[ "${host_port}" == *:* ]]; then
        export PGPORT="${host_port##*:}"
      fi
    fi
  fi
  pg_args=(--dbname="${dbname}")
fi

if command -v pg_dump >/dev/null 2>&1; then
  if pg_dump "${pg_args[@]}" \
    --format=custom \
    --file="${postgres_backup}.part"; then
    if ! pg_restore --list "${postgres_backup}.part" >/dev/null; then
      rm -f "${postgres_backup}.part"
      echo "PostgreSQL 备份校验失败" >&2
      exit 1
    fi
    mv "${postgres_backup}.part" "${postgres_backup}"
    chmod 600 "${postgres_backup}"
    database_backup_created=true
  else
    rm -f "${postgres_backup}.part"
    if [[ "${postgres_backend}" == true ]]; then
      echo "PostgreSQL 后端备份失败，终止部署" >&2
      exit 1
    fi
    echo "提示：PostgreSQL 备份未生成，继续检查 SQLite。" >&2
  fi
else
  if [[ "${postgres_backend}" == true ]]; then
    echo "PostgreSQL 后端缺少 pg_dump，终止部署" >&2
    exit 1
  fi
  echo "提示：未安装 pg_dump，跳过 PostgreSQL 备份。" >&2
fi

# ---------- SQLite ----------
sqlite_source="${project_dir}/data/vocabflow.db"
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
