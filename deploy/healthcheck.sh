#!/usr/bin/env bash
# GOGDC 部署环境每日健康巡检 + 故障告警（deploy/README.md 第 5 节）。
#
# 巡检口径（与部署形态一一对应）：
#   1. db     —— pg_isready（经 compose exec）
#   2. redis  —— redis-cli ping 应答 PONG
#   3. minio  —— 存活探针（仅 dev/联调栈；生产换 Ceph RGW 时设 HEALTHCHECK_SKIP_MINIO=1）
#   4. api    —— /healthz HTTP 探活
#   5. portal —— 门户首页 HTTP 探活
#   6. 容器态 —— docker compose ps：db/redis/minio/api/worker/beat/portal 全部在运行
#               （运行≠健康，应用层健康由 4/5 的探活与 beat 内置巡检覆盖）
#
# 告警口径（与 W5 存储水位告警同一套环境变量与语义）：
#   - ALERT_WEBHOOK_URLS：逗号分隔多路，逐路独立送达；每路上限 ALERT_WEBHOOK_RETRIES
#     次（默认 3），指数退避 0.5s/1s/1.5s…，HTTP 2xx 视为送达成功；
#   - 未配置 webhook 时仅落巡检日志（同 W5「审计日志兜底」口径）；
#   - 载荷字段 event/source/occurred_at 与 W5 对齐，source=gogdc-deploy-healthcheck。
#
# 退出码：0 = 全部通过（PASS）；1 = 存在故障（已按上述口径尝试告警）。
# 每日调度见 deploy/healthcheck.crontab（crontab 示例）。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE="${HEALTHCHECK_COMPOSE:-docker compose}"
API_URL="${HEALTHCHECK_API_URL:-http://localhost:8000/healthz}"
PORTAL_URL="${HEALTHCHECK_PORTAL_URL:-http://localhost:4173/}"
MINIO_URL="${HEALTHCHECK_MINIO_URL:-http://localhost:9000/minio/health/live}"

command -v curl >/dev/null 2>&1 || { echo "::error::缺少 curl"; exit 1; }

CHECKED=()
FAILURES=()

json_esc() { # JSON 字符串转义（引号/反斜杠/控制符）
  local s="$1"
  s=${s//\\/\\\\}; s=${s//\"/\\\"}
  s=${s//$'\n'/ }; s=${s//$'\t'/ }
  printf '%s' "$s"
}

check() { # 名称 命令...（命令退出非 0 即记故障）
  local name="$1"; shift
  CHECKED+=("$name")
  local out rc
  if out=$("$@" 2>&1); then
    echo "  [ok]   $name"
  else
    rc=$?
    echo "  [FAIL] $name — $(echo "$out" | head -c 200)"
    FAILURES+=("$(json_esc "$name: $out")")
  fi
}

send_alert() { # $1=event $2=detail；载荷与 W5 字段对齐，逐路重试
  local event="$1" detail="$2"
  local urls="${ALERT_WEBHOOK_URLS:-}" tmo="${ALERT_WEBHOOK_TIMEOUT:-10}" tries="${ALERT_WEBHOOK_RETRIES:-3}"
  local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local failed_list="" u
  for u in ${FAILURES[@]+"${FAILURES[@]}"}; do
    failed_list+="${failed_list:+,}\"$u\""
  done
  local checked_list="" c
  for c in "${CHECKED[@]}"; do
    checked_list+="${checked_list:+,}\"$(json_esc "$c")\""
  done
  local payload="{\"event\":\"$event\",\"source\":\"gogdc-deploy-healthcheck\",\
\"env\":\"${APP_ENV:-dev}\",\"occurred_at\":\"$ts\",\"detail\":\"$(json_esc "$detail")\",\
\"checked\":[$checked_list],\"failed\":[$failed_list]}"

  if [ -z "$urls" ]; then
    echo "  （未配置 ALERT_WEBHOOK_URLS，仅巡检日志通道）"
    return 0
  fi
  local url attempt code ok_all=0
  local IFS=','
  for url in $urls; do
    unset IFS
    for attempt in $(seq 1 "$tries"); do
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$tmo" \
        -H 'Content-Type: application/json' -d "$payload" "$url" || echo 000)
      if [ "$code" -ge 200 ] 2>/dev/null && [ "$code" -lt 300 ] 2>/dev/null; then
        echo "  [alert] $url 送达成功（第 $attempt 次尝试，HTTP $code）"
        continue 2
      fi
      echo "  [alert] $url 第 $attempt 次尝试失败（HTTP $code）"
      [ "$attempt" -lt "$tries" ] && sleep "$(awk "BEGIN{print 0.5*$attempt}")"
    done
    echo "  [alert] $url 重试 $tries 次仍失败，放弃该路（日志仍是兜底）"
    ok_all=1
  done
  return "$ok_all"
}

echo "== GOGDC 部署环境健康巡检 $(date '+%F %T') =="

check "db pg_isready"        $COMPOSE exec -T db pg_isready -U postgres -d geodata
check "redis ping"           bash -c "$COMPOSE exec -T redis redis-cli ping | grep -q PONG"
if [ "${HEALTHCHECK_SKIP_MINIO:-0}" != "1" ]; then
  check "minio 存活探针"     curl -fsS -o /dev/null "$MINIO_URL"
fi
check "api /healthz"         curl -fsS "$API_URL"
check "portal 首页"         curl -fsS -o /dev/null "$PORTAL_URL"

# 容器运行态：预期服务应全部出现在 `compose ps --status running` 清单里
EXPECTED_SERVICES=(db redis minio api worker beat portal)
[ "${HEALTHCHECK_SKIP_MINIO:-0}" = "1" ] && EXPECTED_SERVICES=(db redis api worker beat portal)
running=$( $COMPOSE ps --status running --format '{{.Service}}' 2>&1 )
for s in "${EXPECTED_SERVICES[@]}"; do
  CHECKED+=("容器运行态:$s")
  if printf '%s\n' "$running" | grep -qx "$s"; then
    echo "  [ok]   容器运行态:$s"
  else
    echo "  [FAIL] 容器运行态:$s — 未在运行清单中"
    FAILURES+=("容器运行态:$s 未在运行（$($COMPOSE ps --all --format '{{.Service}}={{.State}}' 2>/dev/null | tr '\n' ' ' ))")
  fi
done

total=${#CHECKED[@]}
fail=${#FAILURES[@]}
echo "巡检结果: $((total-fail))/$total 项通过，${fail} 项故障"

if [ "$fail" -eq 0 ]; then
  echo "RESULT: PASS"
  if [ "${HEALTHCHECK_NOTIFY_OK:-0}" = "1" ]; then
    send_alert "deploy-healthcheck-pass" "每日巡检 $total 项全部通过" || true
  fi
  exit 0
fi

echo "RESULT: FAIL —— 开始故障告警送达"
send_alert "deploy-healthcheck-fail" "每日巡检发现 $fail/$total 项故障" || true
exit 1
