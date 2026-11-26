# GOGDC 部署指南

系统部署的流程说明与编排脚本。脚本封装见 `deploy/deploy.sh`，生产环境变量样例见
`deploy/env.prod.example`。

## 1. 部署形态

单机 / 联调口径为根目录 `docker-compose.yml` 的 7 个服务：

| 服务 | 镜像 / 构建 | 对外端口 | 职责 |
| --- | --- | --- | --- |
| db | `ghcr.io/stac-utils/pgstac` | 5432 | 业务库 + pgSTAC 编目（同库） |
| redis | `redis:7-alpine` | 6379 | Celery broker |
| minio | `minio/minio` | 9000 / 9001 | **仅 dev/联调**：三档桶宿主；生产换 Ceph RGW |
| api | `backend/` 构建 | 8000 | FastAPI 应用（`/healthz` 探活） |
| worker | 同 api 镜像 | — | Celery worker：采集 → 检测 → 编目 → 存储链路 |
| beat | 同 api 镜像 | — | Celery beat：每 5 分钟一轮多源派发 + 每日生命周期迁移 + 每小时水位巡检 |
| portal | `frontend/` 构建 | 4173 | 门户 + 中台前端（nginx 托管，`/api` `/stac` 反代到 api） |

api / worker / beat / portal 之间的依赖由 compose `depends_on` + healthcheck 门控；
脚本在每一步探活后再进入下一步，等价于把启动顺序显式化。

## 2. 环境要求

- Docker 与 compose v2（脚本会检查）。
- 建桶引导（bootstrap）在 api 镜像内执行，**不要求宿主机 Python 环境**。
- Alembic 迁移同样在 api 镜像内执行（`alembic` 已在 `backend/requirements.txt`）。

## 3. 首次部署（dev / 联调）

```bash
cp .env.example .env        # dev 样例；生产用 deploy/env.prod.example 填真实值
bash deploy/deploy.sh       # 全流程一键编排
```

脚本四步（也可单独执行同名子命令）：

1. **infra**：`docker compose up -d db redis minio` 并逐个探活
   （`pg_isready` / `redis-cli ping` / MinIO 存活探针）。
2. **migrate**：`docker compose run --rm --no-deps api alembic upgrade head`——
   开发/CI/生产同一迁移路径，幂等可重复执行。
   schema 由 Alembic 落库，`migrations/env.py` 已把 `search_path` 锁定为 `public`
   （W7 修复的 pgstac 镜像 search_path 劫持问题；**勿用 psql 手工灌 DDL**）。
3. **bootstrap**：建热/温/冷三桶（幂等）。脚本把 `scripts/s3_bootstrap.py`
   挂进 api 容器执行；未注入 `S3_*` 时继承 compose 里 api 服务的 dev MinIO 凭据。
4. **app**：`docker compose up -d api worker beat portal`，随后 **verify**：
   `curl http://localhost:8000/healthz` 与门户 `http://localhost:4173` 探活。

部署完成后的验收口径（对应 W7 验收）：

- `GET /healthz` 返回正常；门户与 `#/console` 数据中台可打开。
- 备份恢复演练：`bash backend/scripts/backup/backup_restore_drill.sh "postgresql://..."`
  期望 `RESULT: PASS`（CI 门禁同款脚本，见 W7 文档）。
- 压测工具：`backend/scripts/loadtest.py`（目录检索 P95 与下载并发两项指标）。

## 4. 生产部署要点

- **对象存储**：生产不起 minio 服务，`STORAGE_ROUTE=object` 直接指向 Ceph RGW；
  建桶时注入真实凭据：
  `S3_ENDPOINT=http://<rgw>:8080 S3_ACCESS_KEY=... S3_SECRET_KEY=... bash deploy/deploy.sh bootstrap`。
  联调步骤与 Ceph RGW 接入细节见 `docs/W5-STORAGE-LIFECYCLE.md` 的 runbook 部分。
- **环境变量**：全部「待填真实信息」字段见 `deploy/env.prod.example`，凭据经部署环境
  注入，不落仓库。
- **镜像锁定**：pgstac 镜像生产建议锁定具体小版本 tag（compose 当前 dev 用 `latest`，
  起环境前评审锁定）；api / worker / beat / portal 四服务共用同一份构建产物，
  必须同版本发布（beat 的周期任务签名与 worker 的消费端要求一致）。
- **备份演练排期**：`backup_restore_drill.sh` 的灾难口径是「DROP 全部业务表后从备份
  恢复、行数逐一核对」，建议低峰期定期执行；生产另需把 pg_dump 产物按异地留存策略归档。
- **升级流程**：先 `bash deploy/deploy.sh migrate`（幂等），再滚动重启
  `api worker beat portal`；worker/beat 停机窗口内采集任务由 Redis 队列与 cron 到期
  判定兜底（错过的周期下一轮补判）。

## 5. 每日健康巡检与故障告警

`deploy/healthcheck.sh`：部署环境巡检 + 故障告警，可手动运行，也可经 crontab 每日调度。

**巡检项**（默认 12 项，与部署形态一一对应）：

- db：`pg_isready`（经 compose exec）；redis：`ping` 应答 PONG；minio：存活探针
  （生产栈无 minio 时设 `HEALTHCHECK_SKIP_MINIO=1` 跳过）
- api：`/healthz` HTTP 探活；portal：门户首页 HTTP 探活
- 容器运行态：db/redis/minio/api/worker/beat/portal 全部在运行。
  运行≠健康：应用层水位巡检由 beat 内置的每小时任务覆盖（W5），本脚本补的是基础设施层。

**告警口径**（与 W5 存储水位告警同一套环境变量与语义）：

- `ALERT_WEBHOOK_URLS` 逗号分隔多路，逐路独立送达；每路最多 `ALERT_WEBHOOK_RETRIES`
  （默认 3）次，指数退避 0.5s/1s/1.5s…，HTTP 2xx 视为送达成功；
- 未配置 webhook 时仅落巡检日志（同 W5「审计日志兜底」口径，巡检不因告警通道不可用而失败）；
- 载荷 `event`（deploy-healthcheck-fail / -pass）、`source`（gogdc-deploy-healthcheck）、
  `occurred_at`、`checked`、`failed` 字段与 W5 webhook 消费方对齐。

**使用**：

```bash
bash deploy/healthcheck.sh                          # 手动巡检：exit 0=全通过 / 1=有故障
HEALTHCHECK_SKIP_MINIO=1 bash deploy/healthcheck.sh # 生产（无 minio）口径
HEALTHCHECK_NOTIFY_OK=1 bash deploy/healthcheck.sh   # 可选：全通过时也发 pass 事件
```

**每日调度**（完整示例见 `deploy/healthcheck.crontab`）：

```cron
30 8 * * * cd /opt/GOGDC && ALERT_WEBHOOK_URLS=<待填告警 webhook> bash deploy/healthcheck.sh >> var/healthcheck.log 2>&1
```

**告警后的处置建议**：容器态故障先 `docker compose ps` 看哪个服务退出，再
`docker compose logs <svc>` 定位；db/redis 连通性故障查基础设施宿主与容器重启记录；
api 探活失败看 uvicorn 日志；portal 失败先确认 nginx 容器与反代目标。

## 6. 环境变量清单

与 `backend/app/config.py` 读取项一一对应：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `APP_NAME` / `APP_ENV` | geodata-console / dev | 应用名与环境（dev / test / prod） |
| `DATABASE_URL` | 本地 5432 | SQLAlchemy 连接（Alembic 同用） |
| `REDIS_URL` | redis://localhost:6379/0 | Celery broker |
| `PGSTAC_DSN` | 空 | pgSTAC 编目直连（psycopg 原生 DSN） |
| `STORAGE_ROUTE` | object | object（Ceph RGW 主选）/ file（CephFS·NFS 备选） |
| `S3_ENDPOINT` 等 | localhost:9000 | 对象存储 endpoint / 桶三档 / 凭据 / region |
| `STORAGE_DIR` | ./var/storage | file 路线本地落盘目录（object 路线忽略） |
| `PUBLIC_BASE_URL` | 空 | 门户下载入口前缀 |
| `ALERT_WEBHOOK_URLS` / `ALERT_WEBHOOK_TIMEOUT` / `ALERT_WEBHOOK_RETRIES` | 空 / 10 / 3 | 水位告警 webhook 多路送达与重试 |
| `DEFAULT_CATEGORY_CODE` | elevation | 入库未指定分类时的兜底分类 |
| `ALERT_WEBHOOK_URLS` / `ALERT_WEBHOOK_TIMEOUT` / `ALERT_WEBHOOK_RETRIES` | 空 / 10 / 3 | 告警 webhook 多路送达与重试（应用内水位告警与部署巡检共用） |

## 7. 常见问题

- **迁移报 schema 异常 / 表建错 schema**：确认使用本仓库 `alembic upgrade head`
  而非手工 DDL；`migrations/env.py` 与 0001/0002 迁移已内置 `SET search_path TO public`。
- **建桶脚本报缺少 S3 凭据**：dev 下未继承 compose 环境时，直接
  `S3_ENDPOINT=http://localhost:9000 S3_ACCESS_KEY=geoadmin S3_SECRET_KEY=geodata-secret-123 bash deploy/deploy.sh bootstrap`。
- **重复执行**：`infra` 幂等；`migrate` 幂等；`bootstrap` 幂等（桶已存在视为成功）；
  `app` 重建不存在的容器、已在跑的不动。
