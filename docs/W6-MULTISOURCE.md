# W6 交付记录：多源扩展（USGS / GBIF / ESA）与调度策略完善

周期：12/28–01/01（元旦当周有效工作日约 4 天，调休以官方通知为准）

交付内容（设计稿原文）：**多源扩展收窄为 ESA / GBIF 两源（WorldPop 延至试运行期）+ 调度策略完善**
验收标准（设计稿原文）：**3 源并行采集稳定运行**

## 验收项对照

| 验收项 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- |
| 3 源并行采集 | 完成 | `app/services/dispatch_service.py`（ThreadPoolExecutor 并行拉取，线程内只做出网 IO） | `tests/test_multisource_e2e.py::test_three_sources_run_concurrently_and_land_state` 断言 `max_inflight >= 2` 且 3 源各取到条目 |
| 源收窄为 ESA / GBIF 两源 + USGS 存量 | 完成 | `app/services/sources/__init__.py`（`ACTIVE_SOURCES = ("usgs","gbif","esa")`，WorldPop 不在役） | `tests/test_sources_adapters.py::test_registry_covers_three_active_sources`；`tests/test_dispatch_service.py::test_due_tasks_picks_only_active_sources_and_due` 断言 worldpop 任务被排除 |
| 单源故障不拖垮其余源 | 完成 | `dispatch_service._fetch_one` 捕获异常归类，其余源照常落地 | `tests/test_dispatch_service.py::test_one_source_failure_does_not_block_others`（3 源 1 源 500 → 2 成功 1 重试） |
| 调度策略：CRON 到期判定 | 完成 | `app/services/cron_policy.py::is_due`（回看窗口逐分钟扫描，轮询间隔大于 1 分钟不漏跑） | `tests/test_cron_policy.py` 覆盖 `*/N`、区间、枚举、星期归一（7→0）、回看上限 |
| 调度策略：限流时段 | 完成 | `cron_policy.parse_time_window` / `in_time_window`；`harvest_task.time_window` 列 | `tests/test_multisource_e2e.py::test_outside_time_window_nothing_dispatched` 断言时段外零请求 |
| 配额前置检查 | 完成 | `dispatch_service.quota_exhausted`（`quota_limit>0 && used>=limit` 即跳过，不发请求） | `tests/test_dispatch_service.py::test_quota_exhausted_skips_without_request` 断言 `transport.calls == []` |
| 数据源健康度 | 完成 | `dispatch_service.record_health`：成功 healthy + last_success_at；可重试 degraded；不可重试/耗尽 down | `tests/test_dispatch_service.py::test_record_health_transitions` 与三条失败路径测试 |
| 失败率按四类原因统计 | 完成 | 每轮落「采集调度」审计，`detail.failure_classes` 四类恒有键 | `test_dispatching_three_sources_marks_success_and_health` 断言四类全 0；`test_retryable_failure_degrades_and_schedules_retry` 断言「源站限流」计 1 |
| 周期调度真正触发 | 完成 | `app/worker/celery_app.py` beat 每 5 分钟 `harvest.dispatch_round`；`docker-compose.yml` 新增 `beat` 服务 | `tests/test_worker_harvest.py::test_beat_schedule_contains_dispatch` |
| 存量任务名兼容 | 完成 | `harvest.run_usgs` 与 `harvest.run_source` 双注册同一实现 | `test_task_names_registered` |

## 三源契约（2026-11 实测核实，非记忆推断）

| 源 | 请求 | 落盘映射 |
| --- | --- | --- |
| USGS | `GET {base}/tnmaccess/api/products?outputFormat=JSON&datasets=...&max=N` | id=title 派生的 URL 键；size=sizeInBytes；bbox=boundingBox（沿用 W2 契约） |
| GBIF | `GET {base}/v1/occurrence/search?limit=N&hasCoordinate=true[&taxonKey=..]` | id=gbifID/key；url=`references` 或 `https://www.gbif.org/occurrence/{key}`；单点记录按 ±0.0001 生成极小 bbox；format=JSON；`size_bytes=None`（源无字节口径） |
| ESA | `GET {base}/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2'&$top=N&$orderby=ContentDate/Start desc` | id=Id(GUID)；url=`/odata/v1/Products({Id})/$value`；size=ContentLength；bbox=GeoFootprint 外接矩形；校验和取 Checksum 中 BLAKE3；format 按产品名前缀归一 |

失败仍统一映射设计稿四类：401/403→凭证失效（不重试）、429/503→源站限流（重试）、≥500→连接超时（重试）、其余非 200 / 结构不符→元数据校验（不重试）。

## 结构变更（schema）

`harvest_task` 新增两列，三处保持同步（`tests/test_schema_sync.py` 双向门禁）：

- `time_window VARCHAR(32) NOT NULL DEFAULT ''` —— 限流时段 `HH:MM-HH:MM`，空=不限
- `last_run_at TIMESTAMPTZ` —— 最近一次调度执行时刻（到期判定与排障口径）

落点：`sql/001_core_schema.sql`（新建库完整快照）、`sql/002_w6_dispatch.sql`（旧库幂等补列）、`migrations/versions/0002_w6_dispatch.py`（Alembic revision，`0001` 不改动）。

## 控制台 API

- `POST /api/console/harvest-tasks` 新增 `time_window`；创建/更新前置校验 cron 与时段格式，非法即 422（不让脏配置进库后被调度器静默跳过）
- `POST /api/console/harvest-tasks/dispatch/run`（harvest:写）—— 手动触发一轮，可不传 `task_ids`（按 cron + 时段挑到期任务）
- `GET  /api/console/harvest-tasks/dispatch/status`（harvest:读）—— 在役源、健康度、配额水位、当前到期任务
- `GET/POST /api/console/harvest-tasks` 出参新增 `time_window` / `last_run_at`

## 部署

`docker-compose.yml` 增 `beat` 服务（与 worker 同镜像、同 `celery_app`）：

```
celery -A app.worker.celery_app.celery_app beat --loglevel=info
```

beat 项：`harvest-dispatch-5min`（300s）、`storage-watermark-hourly`（3600s）、`storage-lifecycle-daily`（86400s）。

## 未覆盖 / 边界

- WorldPop 延至试运行期（设计稿明确收窄），不在 `ACTIVE_SOURCES`；接入时只需新增适配器并注册。
- 调度器不负责落盘与检测（那是 worker 流水线 `run_collect_pipeline` 的职责），`dispatch_round` 只做「该跑哪些、结果如何」。
- ESA / GBIF 真实出网未在本轮离线套件中跑（CI 无源站网络）；契约以 2026-11 实测响应结构为准，落盘字段口径已用假 transport 覆盖。
- 跨零点限流时段（如 `22:00-06:00`）不支持，会报配置错误；需跨零点时拆两条任务或留空。
