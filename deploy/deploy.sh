#!/usr/bin/env bash
# GOGDC 一键部署编排（dev / 联调口径）——deploy/README.md 标准流程的脚本封装。
# 生产差异（Ceph RGW、凭据注入、镜像 tag 锁定、备份演练排期）见 deploy/README.md。
#
# 用法（在任意目录执行均可，脚本自行定位仓库根）：
#   bash deploy/deploy.sh             # 全流程：infra -> migrate -> bootstrap -> app -> verify
#   bash deploy/deploy.sh infra       # 仅起基础设施（db / redis / minio）并等健康
#   bash deploy/deploy.sh migrate     # 仅 Alembic 迁移（schema 初始化 / 升级，幂等）
#   bash deploy/deploy.sh bootstrap   # 仅对象存储建桶引导（幂等；生产 S3_* 注入真实凭据）
#   bash deploy/deploy.sh app         # 仅起应用服务（api / worker / beat / portal）
#   bash deploy/deploy.sh verify      # 仅健康验证
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

command -v docker >/dev/null 2>&1 || { echo "::error::缺少 docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "::error::缺少 docker compose v2"; exit 1; }

wait_for() { # 名称 探活命令...
  local name="$1"; shift
  local i
  for i in $(seq 1 60); do
    if "$@" >/dev/null 2>&1; then echo "  $name 就绪"; return 0; fi
    sleep 2
  done
  echo "::error::$name 在 120 秒内未就绪"; exit 1
}

step_infra() {
  echo "== [1/4] 基础设施：db(pgSTAC) / redis / minio =="
  docker compose up -d db redis minio
  wait_for "db(pg_isready)"      docker compose exec -T db pg_isready -U postgres -d geodata
  wait_for "redis(ping)"         docker compose exec -T redis redis-cli ping
  # MinIO 存活探针（compose 内 healthcheck 用 mc ready local，此处用其 HTTP 存活端点）
  wait_for "minio(健康探针)"     curl -fsS http://localhost:9000/minio/health/live
}

step_migrate() {
  echo "== [2/4] Alembic 迁移（开发/CI/生产同一迁移路径） =="
  # --no-deps：基础设施须先就绪（先跑 infra 或用全流程）。
  # search_path 已由 backend/migrations/env.py 锁定 public，勿用 psql 手工灌 DDL。
  docker compose run --rm --no-deps api alembic upgrade head
}

step_bootstrap() {
  echo "== [3/4] 对象存储建桶引导（热/温/冷三桶，幂等） =="
  # 建桶脚本在 api 镜像内执行（镜像自带依赖），无需宿主机 Python 环境；
  # 未注入 S3_* 时继承 compose 里 api 服务环境（dev=MinIO geoadmin）。
  # 生产：export S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY 指向 Ceph RGW 后执行。
  local passenv=()
  [ -n "${S3_ENDPOINT:-}" ]  && passenv+=(-e "S3_ENDPOINT=$S3_ENDPOINT")
  [ -n "${S3_ACCESS_KEY:-}" ] && passenv+=(-e "S3_ACCESS_KEY=$S3_ACCESS_KEY")
  [ -n "${S3_SECRET_KEY:-}" ] && passenv+=(-e "S3_SECRET_KEY=$S3_SECRET_KEY")
  docker compose run --rm --no-deps ${passenv[@]+"${passenv[@]}"} \
    -v "$ROOT/scripts/s3_bootstrap.py:/tmp/s3_bootstrap.py:ro" \
    api bash -c "cd /srv && PYTHONPATH=/srv python /tmp/s3_bootstrap.py"
}

step_app() {
  echo "== [4/4] 应用服务：api / worker / beat / portal =="
  docker compose up -d api worker beat portal
}

step_verify() {
  echo "== 健康验证 =="
  wait_for "api(/healthz)"     curl -fsS http://localhost:8000/healthz
  wait_for "portal(:4173)"     curl -fsS -o /dev/null http://localhost:4173/
  docker compose ps api worker beat portal
  echo "RESULT: PASS —— api http://localhost:8000/healthz 与 portal http://localhost:4173 均就绪"
}

case "${1:-all}" in
  all)       step_infra; step_migrate; step_bootstrap; step_app; step_verify ;;
  infra)     step_infra ;;
  migrate)   step_migrate ;;
  bootstrap) step_bootstrap ;;
  app)       step_app ;;
  verify)    step_verify ;;
  *) echo "用法: bash deploy/deploy.sh [all|infra|migrate|bootstrap|app|verify]"; exit 2 ;;
esac
