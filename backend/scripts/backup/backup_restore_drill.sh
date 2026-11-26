#!/usr/bin/env bash
# W7 备份恢复演练：pg_dump 全量备份 → 模拟灾难（清空 public schema）→ 恢复 → 行数核对。
#
# 用法（需可用的 Postgres 与 psql/pg_dump）：
#   scripts/backup/backup_restore_drill.sh postgresql://user:pass@host:5432/geodata
#
# 判定：public schema 所有表「备份前 vs 恢复后」行数逐一相等 => exit 0（PASS）；
#       任何一步失败或行数不一致 => exit 1（FAIL），供 CI 门禁。
set -euo pipefail

DSN="${1:?用法: backup_restore_drill.sh <postgresql:// DSN>}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "== [1/5] 备份前快照（public 全表行数） =="
TABLES=$(psql "$DSN" -tAc \
  "SELECT table_name FROM information_schema.tables \
   WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
if [ -z "$TABLES" ]; then
  echo "::error::public schema 无表（迁移未执行？）"; exit 1
fi
before_counts="$WORKDIR/before.txt"
: > "$before_counts"
total_rows=0
for t in $TABLES; do
  n=$(psql "$DSN" -tAc "SELECT count(*) FROM public.\"$t\"")
  echo "$t $n" >> "$before_counts"
  total_rows=$((total_rows + n))
done
cat "$before_counts"
echo "备份前总行数: $total_rows"
if [ "$total_rows" -eq 0 ]; then
  echo "::error::所有表均为空，演练无意义（先造业务数据）"; exit 1
fi

echo "== [2/5] pg_dump 全量备份 =="
BACKUP="$WORKDIR/geodata_backup.sql"
pg_dump "$DSN" --no-owner --no-privileges --format=plain --file="$BACKUP"
SIZE=$(wc -c < "$BACKUP")
echo "备份文件: $BACKUP ($SIZE bytes)"
if [ "$SIZE" -lt 100 ]; then
  echo "::error::备份文件异常小（$SIZE bytes）"; exit 1
fi

echo "== [3/5] 模拟灾难：DROP SCHEMA public CASCADE =="
psql "$DSN" -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
after_drop=$(psql "$DSN" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
echo "灾难后 public 剩余表数: $after_drop"

echo "== [4/5] 从备份恢复 =="
psql "$DSN" -q -v ON_ERROR_STOP=1 --file="$BACKUP"
echo "恢复完成"

echo "== [5/5] 恢复后行数核对 =="
after_counts="$WORKDIR/after.txt"
: > "$after_counts"
for t in $TABLES; do
  n=$(psql "$DSN" -tAc "SELECT count(*) FROM public.\"$t\"")
  echo "$t $n" >> "$after_counts"
done
if diff -u "$before_counts" "$after_counts"; then
  echo "RESULT: PASS —— 备份前/恢复后行数一致（$(echo "$TABLES" | wc -l) 张表，共 $total_rows 行）"
  exit 0
else
  echo "::error::行数不一致，恢复演练失败"
  exit 1
fi
