# GOGDC-datahub

Geodata（GOGDC）数据中台

全球开放地理空间数据收储平台。设计依据：
[交互设计稿](https://xcnhob85kcys.feishu.cn/page/JsNZme8i0dNVD9af3IgcrRbCnCg) ·
[轻量技术设计框架与垂直切片排期](https://my.feishu.cn/docx/GoIfdo22poAbdKxSxYtchEvJnff)

8 周排期（2026-11-23 ~ 2027-01-15）已启动，当前：**W6 多源扩展（ESA / GBIF）与调度策略**。
逐周进展见 [docs/W1-KICKOFF.md](docs/W1-KICKOFF.md) ·
[docs/W2-DATALAYER.md](docs/W2-DATALAYER.md) ·
[docs/W3-CATALOG.md](docs/W3-CATALOG.md) ·
[docs/W4-PORTAL-COMPLIANCE.md](docs/W4-PORTAL-COMPLIANCE.md) / W5：[docs/W5-STORAGE-LIFECYCLE.md](docs/W5-STORAGE-LIFECYCLE.md)。

## 技术栈（技术方案第四章，待 W1 评审定稿）

- API：FastAPI（OpenAPI 3.1 契约，`api/openapi.yaml` 随仓版本化）
- 数据：PostgreSQL + pgSTAC（业务库与 STAC 目录同库；编目核心规范 STAC 1.1.0，
  对外 API STAC API 1.0.0）
- 异步：Celery + Redis（采集 → 检测 → 编目 → 存储链路解耦）
- 存储：路线解耦——对象（Ceph RGW 主选）/ 文件（CephFS·NFSv4+ZFS 备选），
  `storage_key` 始终存相对键
- 前端：React（W4 已接入：`frontend/` 门户，React 18 + Vite 5）

## 快速开始

```bash
cp .env.example .env
docker compose up -d db redis      # pgstac + redis（schema 经 Alembic 落库，见下）
cd backend && DATABASE_URL=postgresql+psycopg://postgres:geodata@localhost:5432/geodata \
  alembic upgrade head             # 初始化 schema（开发/CI/生产同一迁移路径）
docker compose up api              # http://localhost:8000/healthz
# 本地裸跑（不建镜像）：
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload
```

## 测试与 CI

```bash
bash scripts/ci-local.sh          # 本地 CI 等价脚本：装依赖 + 编译检查 + 66 例测试
```

- 远端 CI：GitHub Actions（`.github/workflows/ci.yml`）——单测 job + pgSTAC 镜像迁移冒烟 job，push 即自动执行
- 本地等价：`scripts/ci-local.sh` 跑同一套命令（当前全绿）

## 目录结构

```
backend/
  app/           # FastAPI 应用（main / config / db / models / domain / worker）
  sql/           # 迁移脚本：000 扩展（postgis）、001 核心实体 DDL（pgstac 由镜像内置装载）
  tests/         # pytest（含 schema 一致性门禁）
  scripts/       # ci-local.sh：本地 CI 等价脚本
docs/            # W1 启动清单等工程文档
.github/         # GitHub Actions CI（单测 + pgSTAC 镜像迁移冒烟）
```

## 约定

- 改 `app/models.py` 或 `sql/001_core_schema.sql` 任一侧，必须同步另一侧（CI 门禁拦截）
- 状态机与 RBAC 为纯逻辑模块（`app/domain/`），不依赖框架，先测后用
- 数据口径：真实值仅四项（15 类 / 8,327,144 条 / 1,536 TB / 400+），
  其余演示数据必须带 DEMO DATA 标注（沿设计稿口径纪律）
