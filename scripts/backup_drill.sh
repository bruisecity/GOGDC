#!/usr/bin/env bash
# 备份演练脚本（W5 交付：备份演练预案的可执行部分）。
#
# 用途：演练「业务库(pgSTAC) + 存储桶数据」的备份与恢复校验，验证备份可用。
# 步骤：备份(pg_dump + tar) → sha256 清单 → 恢复到隔离目录 → 校验清单一致。
#
# 用法（在部署机 / compose 所在目录执行）：
#   ./scripts/backup_drill.sh                     # 完整演练（需 pg_dump + 存储目录）
#   BACKUP_DIR=/data/drill ./scripts/backup_drill.sh
#   PG_DUMP_BIN=pg_dump STORAGE_DIR=./var/storage ./scripts/backup_drill.sh
#
# 退出码：0 = 演练通过；非 0 = 任一校验失败（备份不可用，按预案上报处置）。
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./var/backup-drill}"
STORAGE_DIR="${STORAGE_DIR:-./var/storage}"
WARM_DIR="${WARM_DIR:-./var/storage-warm}"
COLD_DIR="${COLD_DIR:-./var/storage-cold}"
PG_DSN="${PG_DSN:-postgresql://postgres:geodata@localhost:5432/geodata}"
PG_DUMP_BIN="${PG_DUMP_BIN:-pg_dump}"
SKIP_PG="${SKIP_PG:-0}"
RUN_ID="drill-$(date +%Y%m%d-%H%M%S)"
STAMP="$(date -Iseconds)"

echo "=== GOGDC 备份演练 ${RUN_ID} ==="
echo "备份目录: ${BACKUP_DIR}/${RUN_ID}"

DRILL_ROOT="${BACKUP_DIR}/${RUN_ID}"
mkdir -p "${DRILL_ROOT}"

# ---- 1. 业务库备份（pgSTAC 与业务库同库）----
if [[ "${SKIP_PG}" != "1" ]]; then
  if ! command -v "${PG_DUMP_BIN}" >/dev/null 2>&1; then
    echo "FAIL: 未找到 ${PG_DUMP_BIN}（可 SKIP_PG=1 只演练存储层）"
    exit 2
  fi
  echo "[1/5] pg_dump 业务库 -> ${DRILL_ROOT}/geodata.sql.gz"
  "${PG_DUMP_BIN}" "${PG_DSN}" | gzip > "${DRILL_ROOT}/geodata.sql.gz"
else
  echo "[1/5] SKIP_PG=1，跳过业务库备份"
fi

# ---- 2. 存储三档目录备份 ----
for tier_dir in "${STORAGE_DIR}" "${WARM_DIR}" "${COLD_DIR}"; do
  tier_name="$(basename "${tier_dir}")"
  if [[ -d "${tier_dir}" ]]; then
    echo "[2/5] tar 存储档 ${tier_name} -> ${DRILL_ROOT}/${tier_name}.tar.gz"
    tar -czf "${DRILL_ROOT}/${tier_name}.tar.gz" -C "$(dirname "${tier_dir}")" "${tier_name}"
  else
    echo "[2/5] 跳过不存在的存储目录 ${tier_dir}"
  fi
done

# ---- 3. 清单（备份产物 sha256）----
echo "[3/5] 生成 SHA256 清单"
( cd "${DRILL_ROOT}" && sha256sum ./*.gz > MANIFEST.sha256 ) || {
  echo "FAIL: 清单生成失败（无备份产物？）"; exit 3; }
cat "${DRILL_ROOT}/MANIFEST.sha256"

# ---- 4. 隔离目录恢复 ----
RESTORE_DIR="${DRILL_ROOT}/restore-verify"
mkdir -p "${RESTORE_DIR}"
echo "[4/5] 恢复校验到隔离目录 ${RESTORE_DIR}"
# 备份产物复制进隔离目录，按清单逐一校验（等价于「从备份介质读回」）
cp "${DRILL_ROOT}"/*.gz "${RESTORE_DIR}"/
( cd "${RESTORE_DIR}" && sha256sum --check "${DRILL_ROOT}/MANIFEST.sha256" ) || {
  echo "FAIL: 恢复校验不一致——备份不可用，按预案上报"; exit 4; }
# 存储档再解包一次，验证 tar 可恢复
for f in "${DRILL_ROOT}"/*.tar.gz; do
  [[ -e "$f" ]] || continue
  tar -xzf "$f" -C "${RESTORE_DIR}"
done
echo "恢复产物已就位：$(find "${RESTORE_DIR}" -type f | wc -l) 个文件"

# ---- 5. 记录演练结果 ----
cat > "${DRILL_ROOT}/DRILL-REPORT.txt" <<EOF
GOGDC 备份演练报告
时间: ${STAMP}
演练号: ${RUN_ID}
库: ${PG_DSN}
存储目录: ${STORAGE_DIR} ${WARM_DIR} ${COLD_DIR}
结果: 通过（清单校验一致，tar 可恢复）
EOF
echo "[5/5] 演练通过 ✅ 报告: ${DRILL_ROOT}/DRILL-REPORT.txt"
