#!/usr/bin/env bash
# W7 备份恢复演练（业务表范围口径）。
#
# 范围界定：public schema 里的「业务表」= 普通表且不是任何扩展的成员对象
# （pg_depend deptype='e'），即排除 postgis 的 spatial_ref_sys 等平台托管表；
# pgstac schema 属编目平台托管，亦不在业务备份范围（pg_dump --schema=public）。
#
# 用法（需可用的 Postgres 与 psql/pg_dump）：
#   scripts/backup/backup_restore_drill.sh postgresql://user:pass@host:5432/geodata
#
# 判定：业务表「备份前 vs 恢复后」行数逐一相等且 DDL 全部重建 => exit 0（PASS）；
#       任何一步失败 => exit 1（FAIL），供 CI 门禁。
set -euo pipefail

DSN="${1:?用法: backup_restore_drill.sh <postgresql:// DSN>}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# 业务表清单（public 普通表，排除扩展成员对象）
BUSINESS_SQL="SELECT c.relname FROM pg_class c \
  JOIN pg_namespace n ON n.oid = c.relnamespace \
  WHERE n.nspname='public' AND c.relkind='r' \
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=c.oid AND d.deptype='e') \
  ORDER BY 1"
# 扩展成员表（如 spatial_ref_sys）——pg_dump 时逐一排除
EXT_SQL="SELECT c.relname FROM pg_class c \
  JOIN pg_namespace n ON n.oid = c.relnamespace \
  WHERE n.nspname='public' AND c.relkind='r' \
  AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=c.oid AND d.deptype='e') \
  ORDER BY 1"

snapshot() { # 表名列表 -> 行数清单
  local t n
  for t in $1; do
    n=$(psql "$DSN" -tAc "SELECT count(*) FROM public.\"$t\"")
    echo "$t $n"
  done
}

echo "== [1/5] 备份前快照（public 业务表行数） =="
TABLES=$(psql "$DSN" -tAc "$BUSINESS_SQL")
if [ -z "$TABLES" ]; then
  echo "::error::public 无业务表（迁移未执行？）"; exit 1
fi
before_counts="$WORKDIR/before.txt"
snapshot "$TABLES" > "$before_counts"
cat "$before_counts"
total_rows=$(awk '{s+=$2} END{print s}' "$before_counts")
echo "备份前业务总行数: $total_rows"
if [ "$total_rows" -eq 0 ]; then
  echo "::error::业务表全空，演练无意义（先造业务数据）"; exit 1
fi

echo "== [2/5] pg_dump 备份（public 业务表，排除扩展成员表） =="
EXCLUDES=()
while IFS= read -r t; do
  [ -n "$t" ] && EXCLUDES+=(--exclude-table="public.$t")
done < <(psql "$DSN" -tAc "$EXT_SQL")
BACKUP="$WORKDIR/geodata_backup.sql"
pg_dump "$DSN" --schema=public --no-owner --no-privileges \
  "${EXCLUDES[@]:-}" --format=plain --file="$BACKUP"
SIZE=$(wc -c < "$BACKUP")
created=$(grep -c '^CREATE TABLE public\.' "$BACKUP" || true)
expected=$(echo "$TABLES" | wc -l)
echo "备份文件: $BACKUP ($SIZE bytes，业务表 DDL $created/$expected)"
if [ "$SIZE" -lt 100 ]; then
  echo "::error::备份文件异常小（$SIZE bytes）"; exit 1
fi
if [ "$created" -ne "$expected" ]; then
  echo "::error::备份缺少业务表 DDL（$created/$expected）"; exit 1
fi

echo "== [3/5] 模拟灾难：DROP 全部业务表 =="
for t in $TABLES; do
  psql "$DSN" -q -c "DROP TABLE public.\"$t\" CASCADE"
done
left=$(psql "$DSN" -tAc "$BUSINESS_SQL" | wc -l)
if [ "$left" -ne 0 ]; then
  echo "::error::业务表未删净（剩余 $left）"; exit 1
fi
echo "灾难后业务表剩余: 0（扩展与 pgstac schema 不受影响）"

echo "== [4/5] 从备份恢复 =="
psql "$DSN" -q -v ON_ERROR_STOP=1 --file="$BACKUP"
echo "恢复完成"

echo "== [5/5] 恢复后行数核对 =="
after_counts="$WORKDIR/after.txt"
snapshot "$TABLES" > "$after_counts"
if diff -u "$before_counts" "$after_counts"; then
  echo "RESULT: PASS —— 备份前/恢复后行数一致（$expected 张业务表，共 $total_rows 行）"
  exit 0
else
  echo "::error::行数不一致，恢复演练失败"
  exit 1
fi
