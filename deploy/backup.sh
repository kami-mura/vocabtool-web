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

postgres_backup="${backup_dir}/vocabflow-db-${timestamp}.dump"
if ! docker compose exec -T db pg_dump \
  --username=vocabflow \
  --dbname=vocabflow \
  --format=custom \
  > "${postgres_backup}"; then
  rm -f "${postgres_backup}"
  echo "PostgreSQL 备份失败" >&2
  exit 1
fi
if ! docker compose exec -T db pg_restore --list < "${postgres_backup}" >/dev/null; then
  rm -f "${postgres_backup}"
  echo "PostgreSQL 备份校验失败" >&2
  exit 1
fi
chmod 600 "${postgres_backup}"

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
echo "${backup_dir}/vocabflow-db-${timestamp}.dump"
echo "${backup_dir}/vocabflow-data-${timestamp}.tar.gz"
