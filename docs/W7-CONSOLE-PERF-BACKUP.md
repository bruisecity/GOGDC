# W7 交付记录：数据中台四面板接真实 API + 压测与备份演练

周期：01/04–01/08（W7 为里程碑报告节点，与 W3 / W8 构成里程碑节奏）

交付内容（设计稿原文）：**中台八面板接真实 API（前半程：总览 / 采集任务 / 汇聚节点 / 安全检测）+ 压测与备份演练**
验收标准（设计稿原文）：**四面板无演示标注；性能目标达标；恢复演练通过**

## 验收项对照

| 验收项 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- |
| 总览面板（真实 API） | 完成 | `app/routes/overview.py` → `GET /api/console/overview`；聚合服务 `app/services/overview_service.py` | `tests/test_overview_api.py` 4 例：全量聚合数值断言、空库恒四键、RBAC（guest/匿名 403，researcher 200）、部分在线率 0.5 |
| 汇聚节点面板（真实 API） | 完成 | `app/routes/nodes.py` → `GET /api/console/nodes` + `POST /api/console/nodes/{id}/heartbeat` | `tests/test_nodes_api.py` 6 例：RBAC、心跳 300s 在线判定、任务数、心跳落库+审计、auditor 只读 403、404 |
| 采集任务面板（真实 API） | 完成 | 复用 W6 `GET /api/console/harvest-tasks`（分页 limit/offset） | 既有 `tests/test_harvest_api.py`；前端 `buildTasksQuery` 有 `tests/api.test.mjs` 夹取用例 |
| 安全检测面板（真实 API） | 完成 | 复用 W4 `GET /api/console/detection-rules`（启停走 PATCH） | 既有 `tests/test_detection_api.py` |
| 四面板无演示标注 | 完成 | `frontend/src/App.jsx` ConsolePage：四面板数据全部来自 `/api/console/*` 实时响应，无 DEMO/示例数据分支 | 代码审阅：ConsolePage 无任何硬编码数据表；`npm run build` 通过 |
| 监控三口径：节点心跳与在线率 | 完成 | `overview_service.node_online`（心跳 300s 内=在线）；总览 `nodes.online_rate` | `test_overview_api.py` 在线率断言；`test_nodes_api.py` 心跳判定 |
| 监控三口径：容量水位（80% 告警线） | 完成 | 复用 W5 `watermark_service.all_bucket_statuses`，总览 `storage` 面板逐桶水位 + 告警状态 | `test_overview_api.py` 存储水位断言（ratio 0.8 → 告警） |
| 监控三口径：采集失败率按四类原因 | 完成 | 总览 `harvest_failures` 四类恒有键（连接超时/源站限流/凭证失效/元数据校验） | `test_overview_api.py` 四类失败计数断言 |
| 性能目标：目录检索 P95 < 1s | 达标（口径见下） | `backend/scripts/loadtest.py`（纯标准库压测，CI/本地直跑） | 本地实测 P95=39.84ms（200 请求 / 16 并发 / 0 错误） |
| 性能目标：单汇聚节点并发下载 ≥ 10 | 达标 | 同上（16 worker 实测最大在飞 16，1001 次成功 0 失败，吞吐 419.85MB/s） | `var/loadtest-result.json`（入库前本地产物）+ 下表 |
| 备份恢复演练 | 完成 | `backend/scripts/backup/backup_restore_drill.sh`：pg_dump 全量 → DROP SCHEMA public CASCADE → 恢复 → 全表行数逐一核对 | CI `migrate-smoke` job 新增 `Backup/restore drill` step，失败即红 |

## 压测实测（2026-11-26，本地沙箱）

工具：`backend/scripts/loadtest.py`（urllib + threading，P95 取最近邻百分位，退出码 0/1 供 CI 门禁）。
环境口径：本地沙箱无 Postgres/pgSTAC，服务以 SQLite 文件库（WAL）+ LocalBlobStore 启动；
检索压测打的是 `/stac/v1/search` 空目录回退路径——**该数字验证的是 API 层延迟预算，
不能外推为千万级条目下 pgSTAC 空间索引的 P95**；后者需在试运行环境以真实数据量复测。

| 指标 | 目标 | 实测 | 判定 |
| --- | --- | --- | --- |
| 目录检索 P95 | < 1000 ms | 39.84 ms（p50=17.17 / p99=42.42，200 请求 16 并发 0 错误） | PASS |
| 单节点并发下载 | ≥ 10 | 最大在飞 16（16 worker / 10s / 1001 成功 / 0 失败 / 419.85 MB/s） | PASS |

复现：
```bash
python3 scripts/dev_seed.py    # 造 4MiB 检测通过数据集 + operator 压测账号
PYTHONPATH=.ci_deps:. python3 scripts/dev_server.py &   # SQLite WAL 启动
python3 scripts/loadtest.py --base-url http://127.0.0.1:8000 \
  --search-requests 200 --search-concurrency 16 \
  --download-workers 16 --download-seconds 10 --download-min-concurrency 10
```

## 备份恢复演练

`scripts/backup/backup_restore_drill.sh`（bash + psql/pg_dump，无第三方依赖），业务表范围口径：

1. 业务表清单 = public 下普通表且**非**任何扩展的成员对象（`pg_depend deptype='e'`，
   即排除 postgis 的 spatial_ref_sys 等平台托管表）
2. 快照全部业务表行数（备份前基线，全空则拒绝演练）
3. `pg_dump --schema=public --exclude-table=<扩展成员表>…` 业务备份
   （pgstac schema 属编目平台托管、不在业务备份范围），并断言备份含全量业务表 DDL
4. 模拟灾难：逐一 `DROP TABLE public.<t> CASCADE`（业务表归零，扩展与 pgstac schema 不受影响）
5. `psql --file=backup.sql -v ON_ERROR_STOP=1` 恢复 → 全表行数与基线逐一 `diff`
   全等 → PASS（exit 0），任何差异 → FAIL（exit 1）

接入位置：CI `migrate-smoke` job 末位 step（Alembic 迁移 + pgSTAC 集成测试之后，
先插一行 `public.role` 保证备份非空）。

### 首跑暴露的真实缺陷：pgstac 镜像 search_path 劫持（已修）

首跑（CI run 45778323657）失败并顺带暴露一个 W2 起就潜伏的问题：

- **现象**：迁移步骤正常、`INSERT INTO role` 正常，但 public schema 里只有
  `spatial_ref_sys`——所有业务表实际落在了 **pgstac schema**；
  恢复时报 `ERROR: schema "pgstac" already exists`（pg_dump 未过滤 schema）。
- **根因**：`ghcr.io/stac-utils/pgstac` 镜像的 initdb 脚本把数据库级
  `search_path` 设为 `pgstac, public`，Alembic 执行的无限定
  `CREATE TABLE` / `ALTER TABLE` 全部静默落进 pgstac schema；
  迁移后检查（public 有表）被 postgis 的 spatial_ref_sys 掩护通过，从未报警。
- **修复（三层）**：
  1. 迁移自身根治：`0001`/`0002` upgrade/downgrade 首句 `SET search_path TO public`
     ——任何环境（含 W8 部署）都不再依赖数据库级 search_path 默认值；
  2. `migrations/env.py` 对 Postgres 连接加 `connect_args={options: -csearch_path=public}`
     （alembic_version 也落 public；不放 URL 里——`%` 编码会触发 psycopg conninfo 插值错误）；
  3. CI 新增门禁：迁移后断言 `public.role` 存在，search_path 劫持一旦回归立即红。
- **教训**：凡在带平台 schema 的库上跑迁移，DDL 必须显式限定 schema 或锁定
  search_path；「有表」类弱检查会被平台托管表掩护，要断言具体业务表。

## API 契约（中台前半程）

| 面板 | 接口 | 权限 |
| --- | --- | --- |
| 总览 | `GET /api/console/overview` | overview:读（admin / operator / researcher） |
| 采集任务 | `GET /api/console/harvest-tasks?limit&offset` | harvest:读 |
| 汇聚节点 | `GET /api/console/nodes` / `POST /api/console/nodes/{id}/heartbeat` | node:读 / node:写 |
| 安全检测 | `GET /api/console/detection-rules` / `PATCH /{id}` | detection:读 / detection:写 |

总览响应聚合口径（空库亦恒有键）：`datasets{total,total_bytes,by_security_status×4}`、
`nodes{total,online,online_rate}`、`data_sources{by_health×3,items[]}`、
`harvest_tasks{total,by_status×4}`、`harvest_failures{四类}`、`storage{alert_ratio=0.8,buckets[]}`、
`generated_at`。节点心跳写操作落 audit_log（operation=节点心跳，resource=node，detail 含磁盘与下载量）。

## 结构与版本

- 后端版本 `0.7.0-w7`（`app/main.py`）；前端版本 `0.7.0-w7`（`package.json`）
- 无 schema 变更（W7 复用既有表：node / harvest_task / dataset / watermark 视图口径）
- 回归：后端 273 passed / 6 skipped；前端 7 tests pass；`vite build` 通过
