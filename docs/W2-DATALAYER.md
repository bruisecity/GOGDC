# W2 交付记录：数据层与 USGS 采集切片（2026-11-23 ~ 11-29）

对应排期第 8 章 W2：「数据层基座收口（pgSTAC、存储层、Redis、认证与 RBAC）+
切片 0 上半程：USGS 单源采集 → 汇聚落地 → 入库」。

W2 验收口径（设计稿）与逐条落地：

| 验收项 | 状态 | 实现位置 |
| --- | --- | --- |
| STAC API 空响应可查 | ✅ | `app/routes/stac.py`：`/stac/v1`（landing，conformsTo 声明 STAC 1.1.0）、`/stac/v1/collections`、`/stac/v1/collections/{id}/items`（空 FeatureCollection） |
| 登录鉴权可用 | ✅ | `app/routes/auth.py` + `app/core/auth.py`：注册（签发 `X-API-Key`，前缀 `gk_`）、登录（轮换 Key）、`/api/auth/me`；RBAC 矩阵判定挂到路由守卫 |
| 采集任务可调度、可重试 | ✅ | `app/routes/harvest.py`（GET/POST/PATCH `/api/console/harvest-tasks`，状态机校验）+ `app/services/harvest_service.py`（retry_count 驱动的闭环）+ `app/worker/tasks/harvest.py`（Celery 任务 `harvest.run_usgs`） |

## 数据层基座收口

- **`/readyz` 真实探测**（`app/core/probes.py`，替代 W1 的 pending 桩）：
  database（SELECT 1 + pgstac schema 存在性）、redis（PING，客户端未装时显式降级）、
  storage（S3 端点可达性，MinIO 根路径 403 视为在线）。任一失败 → HTTP 503。
- **Alembic 迁移框架**（`backend/alembic.ini` + `backend/migrations/`）：
  baseline revision 执行 `sql/000+001`（SQL 文件仍是唯一事实来源，与
  `tests/test_schema_sync.py` 一致性门禁对齐）；后续结构变更一律新增 revision。
  CI `migrate-smoke` 已切换为 `alembic upgrade head` 验证路径；docker-compose
  去掉 initdb 直灌，开发/CI/生产统一走迁移。
- **统一错误信封** `{code, message, trace_id}`（`app/core/errors.py`）：
  401/403/404/409/422/500 全覆盖，`trace_id` 由请求中间件生成并回写
  `X-Trace-Id` 响应头。
- **Celery 装配**：`celery_app.include` 接入 `app.worker.tasks.harvest`；
  `requirements.txt` 落地 celery/redis/alembic；compose 增加 `worker` 服务。

## USGS 采集切片（切片 0 上半程）

- 适配器 `app/services/usgs.py`：TNM Access API（`/tnmaccess/api/products`）
  客户端，**transport 可注入**——测试用假 transport 离线跑，生产由 worker
  注入 httpx 实现。失败分类严格对齐设计稿四类并给出可重试判定：
  - 连接超时（网络异常 / 5xx）→ 可重试
  - 源站限流（429 / 503）→ 可重试
  - 凭证失效（401 / 403）→ 不重试，不消耗 retry_count
  - 元数据校验（响应结构 / 条目缺下载地址）→ 不重试
- 执行服务 `app/services/harvest_service.py` 一次 run 的状态机闭环：
  `idle → running` → 成功回 `idle` 并清零重试口径 / 可重试失败且未耗尽
  `retry_count+1` 回 `idle` 等下一轮 / 耗尽或不可重试 → `failed`，
  `last_failure_class` 记录分类。

## 已知边界与风险

- **USGS 源站从开发沙箱不可达**（TNM 空响应、ScienceBase Cloudflare 拦截），
  与设计稿「汇聚节点出网能力」风险一致——生产采集在具备出网能力的汇聚节点
  执行；本切片以可注入 transport 保障逻辑可在离线环境验证。
- 汇聚落地（文件落盘）与 pgSTAC 入库为切片 0 下半程（W3）；当前
  `harvest.run_usgs` 采集目录条目并驱动状态机，不写 STAC。
- 登录为 X-API-Key 口径的会话起点，JWT/口令策略待评审；注册自建用户默认
  `guest`，其余角色由 admin 分配。

## 测试

- 全量 `python3 -m pytest tests -q` → **66 passed**（W1 35 例全保留，
  新增 31 例：STAC 4 / 认证 8 / 采集控制台 4 / USGS 适配器 10 / 采集服务 6 /
  readyz 2 拆分重写，含错误信封与 RBAC 矩阵断言）。
- 本地离线验证：pytest + compileall + `alembic upgrade head --sql`（离线
  SQL 生成）全绿；在线迁移冒烟由 CI `migrate-smoke` job 在真实 pgstac
  镜像上执行。
