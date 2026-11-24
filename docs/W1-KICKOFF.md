# W1 启动清单（2026-11-23 ~ 11-27）

排期依据：《Geodata（GOGDC）平台轻量技术设计框架与垂直切片排期》第八章。
W1 交付：技术方案评审定稿、仓库与 CI 搭建、开发/测试环境；数据层基座并行启动。
W1 验收：方案签字；环境可部署 Hello 接口；pgSTAC 与存储层（对象/文件路线评审定稿）可连通。

## 本启动包已完成（W1 第 1 天可落地）

- [x] 仓库骨架：`backend/`（FastAPI + SQLAlchemy + Celery 骨架）、`docs/`；CI 配置于仓库根
- [x] Hello/健康接口：`GET /healthz`、`GET /hello`、`GET /readyz`（依赖探测桩，W2 补真实探测）
- [x] 数据层基座：`sql/000_extensions.sql`（PostGIS + pgSTAC 启用）+ `sql/001_core_schema.sql`
      （11 组核心实体完整 DDL，枚举 CHECK、索引、审计时间列，与文档第三章逐字段对齐）
- [x] 领域逻辑：采集任务状态机、隔离处置五步、编目发布五步（禁回退禁跳步）
- [x] RBAC 引擎：角色—资源—动作三段判定（8 资源 × 读/写/审批）
- [x] 测试 35 例全绿，含 **ORM ↔ DDL 一致性门禁**（tests/test_schema_sync.py，
      改 models 或 SQL 任一侧不同步另一侧即红）
- [x] CI：GitHub Actions 双 job——单测 + **真实 pgSTAC 镜像上的迁移冒烟**
      （.github/workflows/ci.yml，push 即自动执行）

## W1 剩余事项（按验收标准倒排）

- [ ] 周一：技术方案评审会定稿签字（第四章选型拍板：存储路线 object/file、pgstac 镜像版本锁定）
- [ ] 仓库落位：推送至 github.com/bruisecity/GOGDC（已配 GitHub Actions），push 后 CI 首跑必须绿
- [ ] 开发/测试环境部署：`docker compose up -d db redis && docker compose up api`，
      本机验证 /healthz 200、pgstac schema 已就绪（镜像 initdb 装载，非 CREATE EXTENSION）
- [ ] W1 否决项确认（技术方案第九章）：4 个承担单位节点网络环境是否支持出网采集；
      不支持则 W2 起通信方案滑期，需当周决策
- [ ] 15 类收储分类字典、5 角色命名与权限矩阵种子数据：业务确认后补 INSERT
      （SQL 中已留 TODO 标记，RBAC 起步矩阵待与设计稿逐格核对）
- [ ] pgstac 镜像 tag 从 latest 锁定为评审确认的具体版本

## W2 预告（数据层基座收口 + 切片 0 上半程）

数据层收口（/readyz 真实探测、Alembic 迁移框架引入）、认证与 RBAC API、
USGS 单源采集任务（harvest worker + retry_count 驱动的状态机接入）。
验收：STAC API 空响应可查；登录鉴权可用；采集任务可调度、可重试。
