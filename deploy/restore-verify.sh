#!/usr/bin/env bash
# 恢复演练：把最近一份备份恢复到隔离目录，校验完整性并冒烟启动，
# 用来证明「备份真的能恢复到可运行状态」，而不是只看到备份文件在增长。
# 对应 docs/审查整改清单.md P2-15（RPO/RTO 从未演练）。
#
# 安全边界：
#   - 只读备份文件；不触碰 data/vocabflow.db，不重启、不影响线上服务；
#   - 演练库、日志、临时实例全部落在 mktemp 目录与独立端口，退出即清理；
#   - 演练会在这份副本上执行迁移，这正是要验证的一环（旧库能否升级）。
#
# 用法：
#   ./deploy/restore-verify.sh [备份目录]      # 默认 ./backups
# 环境变量：DRILL_PORT（默认 8123）、DRILL_KEEP=1 保留临时目录便于排查
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${1:-${project_dir}/backups}"
port="${DRILL_PORT:-8123}"
python_bin="${project_dir}/.venv/bin/python"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/vocabflow-restore-XXXXXX")"
server_pid=""

cleanup() {
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
  fi
  if [[ "${DRILL_KEEP:-0}" == "1" ]]; then
    echo "保留演练目录：${work_dir}" >&2
  else
    rm -rf "${work_dir}"
  fi
}
trap cleanup EXIT

fail() {
  echo "恢复演练失败：$1" >&2
  exit 1
}

# ---------- 1. 选取最近一份备份 ----------
latest=""
if [[ -d "${backup_dir}" ]]; then
  latest="$(ls -1t "${backup_dir}"/vocabflow-sqlite-*.db 2>/dev/null | head -1 || true)"
fi
[[ -n "${latest}" ]] || fail "在 ${backup_dir} 找不到 vocabflow-sqlite-*.db 备份"
echo "使用备份：${latest}"

# ---------- 2. 复制到隔离目录 ----------
restored="${work_dir}/restored.db"
cp "${latest}" "${restored}"
chmod 600 "${restored}"

# ---------- 3. 完整性校验 ----------
integrity="$("${python_bin}" -c '
import sqlite3, sys
db = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro&immutable=1", uri=True)
print(db.execute("PRAGMA integrity_check").fetchone()[0])
db.close()
' "${restored}")"
[[ "${integrity}" == "ok" ]] || fail "integrity_check 未通过：${integrity}"
echo "integrity_check: ok"

fk_violations="$("${python_bin}" -c '
import sqlite3, sys
db = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro&immutable=1", uri=True)
print(len(db.execute("PRAGMA foreign_key_check").fetchall()))
db.close()
' "${restored}")"
echo "foreign_key_check 违规数：${fk_violations}"
[[ "${fk_violations}" == "0" ]] || fail "外键校验有 ${fk_violations} 条违规"

counts="$("${python_bin}" - "${restored}" <<'PY'
import sqlite3
import sys

db = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro&immutable=1", uri=True)
tables = [
    row[0]
    for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
]
parts = []
for name in ("users", "cards", "saved_words", "review_logs"):
    if name in tables:
        total = db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
        parts.append(name + "=" + str(total))
print("表数=" + str(len(tables)) + " " + " ".join(parts))
db.close()
PY
)"
echo "恢复内容：${counts}"

# ---------- 4. 起临时实例（独立库副本、独立端口） ----------
log="${work_dir}/server.log"
VOCABFLOW_SKIP_DOTENV=1 \
DATABASE_URL="sqlite:///${restored}" \
ALLOWED_HOSTS="127.0.0.1,localhost" \
  "${python_bin}" -m uvicorn app.main:app \
  --host 127.0.0.1 --port "${port}" >"${log}" 2>&1 &
server_pid=$!

started=false
for _ in $(seq 1 30); do
  if curl -fsS --connect-timeout 1 --max-time 3 \
    "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
    started=true
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    tail -20 "${log}" >&2
    fail "临时实例启动即退出（见上方日志）"
  fi
  sleep 1
done
[[ "${started}" == true ]] || { tail -20 "${log}" >&2; fail "临时实例 30 秒内未就绪"; }

# ---------- 5. 冒烟：三个关键入口 ----------
smoke_failed=0
for path in /healthz / /manifest.webmanifest; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    "http://127.0.0.1:${port}${path}" || echo "000")"
  if [[ "${code}" == "200" ]]; then
    echo "冒烟通过 ${path} -> 200"
  else
    echo "冒烟失败 ${path} -> ${code}" >&2
    smoke_failed=1
  fi
done

revision="$(curl -fsS --max-time 5 "http://127.0.0.1:${port}/healthz" 2>/dev/null || true)"
echo "演练实例 /healthz：${revision:-（无响应）}"

# ---------- 6. 汇总 ----------
stamp="$(date '+%Y-%m-%d %H:%M:%S')"
if [[ "${smoke_failed}" -eq 0 ]]; then
  echo "恢复演练通过：备份可恢复到可运行状态（${stamp}）"
  summary="恢复演练通过"
else
  summary="恢复演练失败：冒烟接口未全部返回 200"
  echo "${summary}（${stamp}）" >&2
fi
if [[ -d "${backup_dir}" && -w "${backup_dir}" ]]; then
  echo "${stamp} ${summary}（备份：$(basename "${latest}")，${counts}）" \
    >>"${backup_dir}/restore-verify.log"
fi

[[ "${smoke_failed}" -eq 0 ]]
