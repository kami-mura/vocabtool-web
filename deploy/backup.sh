#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${1:-${project_dir}/backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -f "${project_dir}/docker-compose.yml" ]]; then
  echo "找不到 docker-compose.yml，停止备份。" >&2
  exit 1
fi

mkdir -p "${backup_dir}"
cd "${project_dir}"

# SQLite 在线备份：在 web 容器内用 backup API 落到 bind mount 的 .part 文件，
# 校验通过后由宿主原子改名，避免半成品挤占有效恢复点。
sqlite_backup="${backup_dir}/vocabflow-sqlite-${timestamp}.db"
part_in_data="data/backup-part.db"
rm -f "${part_in_data}"
if ! docker compose exec -T web python -c \
  'import sqlite3; source=sqlite3.connect("file:/app/data/vocabflow.db?mode=ro", uri=True); target=sqlite3.connect("/app/data/backup-part.db"); source.backup(target); target.close(); source.close()'; then
  rm -f "${part_in_data}"
  echo "SQLite 备份失败" >&2
  exit 1
fi
integrity="$(docker compose exec -T web python -c \
  'import sqlite3; db=sqlite3.connect("file:/app/data/backup-part.db?mode=ro&immutable=1", uri=True); print(db.execute("PRAGMA integrity_check").fetchone()[0]); db.close()')"
if [[ "${integrity}" != "ok" ]]; then
  rm -f "${part_in_data}"
  echo "SQLite 备份完整性检查失败" >&2
  exit 1
fi
mv "${part_in_data}" "${sqlite_backup}"
chmod 600 "${sqlite_backup}"

tar \
  --exclude='data/*.db' \
  --exclude='data/*.db-wal' \
  --exclude='data/*.db-shm' \
  --exclude='data/temp_uploads' \
  --exclude='data/tts' \
  -czf "${backup_dir}/vocabflow-data-${timestamp}.tar.gz" \
  data

"${project_dir}/deploy/prune-backups.sh" "${backup_dir}"

echo "备份完成："
echo "${sqlite_backup}"
echo "${backup_dir}/vocabflow-data-${timestamp}.tar.gz"
