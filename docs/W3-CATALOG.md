# W3 交付记录：采集落盘与 pgSTAC 入库（2026-11-23 ~ 11-29）

对应排期第 8 章 W3：「切片 0 下半程：STAC 编目 + 目录检索 + 下载」。
W3 验收口径（设计稿）：**端到端演示：一条 USGS 数据从采集到可检索、可下载**。

W3 验收口径与逐条落地：

| 验收项 | 状态 | 实现位置 |
| --- | --- | --- |
| 采集→落盘（汇聚节点落地） | ✅ | `app/services/storage.py`（`BlobStore` 协议 + `LocalBlobStore`，路径逃逸防护、sha256 校验和）+ `app/services/ingest_service.py`（`storage_key={source}/{item_id}/{filename}` 相对键、桶配额记账、`(bucket_id, key)` 幂等、审计「采集落盘」） |
| 安全检测（不通过进隔离区） | ✅ | `app/services/detection_service.py`：规则驱动（元数据完整性 / 病毒载荷），命中隔离规则 → `QuarantineSample` + 数据集 `已隔离`，否则 `检测通过`；审计「安全检测」 |
| STAC 编目（入库 pgSTAC） | ✅ | `app/services/catalog.py`（`PgstacCatalogClient`，SQL 契约逐条对齐 pypgstac 0.9.8 迁移源：`upsert_collection/upsert_items/all_collections/get_collection/search`）+ `app/services/catalog_service.py`（编目批次状态机：草稿→字段校验→预览→发布→归档，不跳步不回退） |
| 目录检索 | ✅ | `app/routes/stac.py` 重写为可注入客户端：配置 DSN 时透传 pgSTAC `search`（collections/ids/bbox/datetime/limit），未配置时保持 W2 空响应兼容 |
| 下载 | ✅ | `app/routes/datasets.py`：`GET /api/v1/datasets/{id}/download`——已隔离 403 / 未检测通过 409 / 配额校验与扣减（0=不限量，匿名可下载）/ 最后访问时间与审计「数据下载」 |
| 每一步落审计日志 | ✅ | 端到端测试断言完整审计链：采集落盘 → 安全检测 → STAC 编目 → 数据下载 |

## 端到端链路（切片 0 收口）

`app/services/pipeline.py` 把 W2 的采集 run 延伸为完整流水线：

```
USGS harvest（可注入 transport）
  → ingest_harvest_items（落盘 + Dataset/StorageObject + 桶记账）
  → run_detection（逐条安全检测，隔离或放行）
  → 编目批次发布（upsert_collection + upsert_items，回填 collection_id）
  → /stac/v1/collections/{id}/items 检索
  → /api/v1/datasets/{id}/下载（带配额与审计）
```

- `app/worker/tasks/harvest.py` 的 `run_usgs` 已切换为流水线口径：按
  `DEFAULT_CATEGORY_CODE` 找到分类后采集→落盘→检测一次跑完；分类缺失时
  降级为仅采集并告警（不阻断）。
- 编目发布强校验：`PGSTAC_DSN` 未配置时发布步骤抛 `BATCH_VALIDATION_FAILED`
  (422)，批次停在预览，可继续补环境后重试发布。
- 配置新增：`STORAGE_DIR`（落盘根目录）、`PGSTAC_DSN`、`PUBLIC_BASE_URL`
  （STAC asset 下载链接前缀）、`DEFAULT_CATEGORY_CODE`。compose 中 api/worker
  共享 `storage_data` 卷，DSN 统一注入。
- CI `migrate-smoke` 新增 pgSTAC 真库契约测试（`tests_pgstac/`）：以
  `GEODATA_PGSTAC_DSN` 直连容器内 pgstac，验证 upsert 幂等、检索
  numberMatched/属性透传——本地无 DSN 自动跳过，CI 上真库裁决。

## 已知边界与风险

- **pgSTAC 函数契约**以 pypgstac 0.9.8 迁移 SQL 为准（docker 镜像 v0.10.x
  同名函数兼容）；`update_item` 为 DELETE+INSERT 组合，未直接封装。
- 沙箱无 docker、USGS 不可达：落盘用 `LocalBlobStore`，payload 由注入的
  回调提供（测试合成字节、worker httpx 下载）。对象存储（S3/Ceph RGW）
  适配为 W5 存储分级切片，`BlobStore` 协议已预留。
- 敏感信息 / 坐标基准检测规则为 W4 占位（规则启用即逐条执行，当前仅注册
  元数据完整性与病毒载荷两条隔离规则）。
- `license` 字段在编目时统一 `various`，逐数据集授权核对归 W4 合规切片。

## 测试

- 全量 `python3 -m pytest tests -q` → **105 passed**（W2 66 例全保留，新增
  39 例：存储 4 / 落盘 4 / 检测 5 / 编目服务 9 / 下载 API 6 / 编目控制台 4 /
  STAC API 5 / 端到端 1 + e2e 补充）。
- 端到端用例 `tests/test_pipeline_e2e.py::test_end_to_end_usgs_to_download`
  即 W3 验收口径的自动化证据：假 transport 喂 USGS 条目 → 流水线 → 批次
  发布（RecordingExecutor 记录 upsert 载荷）→ TestClient 检索 STAC items →
  匿名下载 200 → 断言四步审计链。
- `tests_pgstac/test_pgstac_integration.py`：本地跳过（1 skipped），
  CI `migrate-smoke` 以真库执行。
