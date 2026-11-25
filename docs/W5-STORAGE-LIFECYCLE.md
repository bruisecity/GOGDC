# W5 交付记录：存储分层与生命周期、容量水位监控（2026-12-21 ~ 12-25）

对应排期第 8 章 W5：「存储分层与生命周期规则、容量水位监控；压测脚本与
备份演练预案」。验收标准：**热 / 温 / 冷迁移策略生效；水位告警可达**。

W5 验收任务拆分与逐条落地：

| 验收项（切片） | 状态 | 实现位置 |
| --- | --- | --- |
| A1 存储分层：S3/Ceph RGW 适配（W3 预留位补齐） | ✅ | `app/services/s3_store.py`：`S3CompatBlobStore`（path-style + AWS SigV4 签名，标准库实现不引 boto3；传输层与时钟可注入，离线可测）；`BlobStore` 协议增补 `delete`（迁移删源所需） |
| A2 热/温/冷迁移策略生效 | ✅ | `app/services/tiering.py`：`TieredBlobStores` 三档路由 + `migrate_blob`（写目标→回读校验→删源；校验不过绝不删源；目标同键同内容时幂等补删）；`validate_transition` 强制只向下迁移 |
| B1 生命周期规则 | ✅ | `app/services/lifecycle_service.py`：策略 JSON（`transitions: [{target_tier, bucket, after_days}]`）解析校验；按 `last_access_at`（缺失回退 `created_at`）算闲置天数、多条命中取最深一档；迁移改挂桶 + `used_bytes` 双向结转 + 审计 |
| B2 周期执行 | ✅ | `app/worker/tasks/storage.py`：`storage.lifecycle_pass`（每日）+ `storage.watermark_check`（每小时）celery beat 调度；控制台 `POST /api/console/storage/lifecycle/run` 可手动触发 |
| C1 容量水位监控 + 80% 告警线 | ✅ | `app/services/watermark_service.py`：水位 = used/quota，`quota_bytes` 即 80% 告警线口径；恰好 80% 即告警；幂等去重（仍告警不重复、回落落「恢复」、再越线再告警） |
| C2 水位告警可达 | ✅ | 告警/恢复落 `AuditLog`（operation=容量水位），经 W4 审计控制台 `GET /api/console/audit-logs?operation=容量水位` 检索到（测试 `test_watermark_alert_reachable_via_audit_console` 全链路断言）；存储控制台 `GET /api/console/storage/watermark` 快照 + `POST .../watermark/check` 巡检 |
| D1 压测脚本 | ✅ | `scripts/loadtest_api.py`：固定并发 × 时长压测（RPS / p50/p95/p99 / 错误数，可 `--json-out` 留档、`--api-key` 认证）；本地桩服务器实测 581.9 RPS / p95 3.9ms / 0 错误 |
| D2 备份演练预案 | ✅ | `scripts/backup_drill.sh`（本文件 §备份演练预案）+ 本预案文档；合成数据实测演练通过（清单校验 + tar 恢复 + 报告落盘） |

## 存储分层与迁移（切片 A）

- **路线解耦**：`storage_key` 全局相对键不变，tier 挂在桶上（W2 模型已预留
  `StorageBucket.tier/lifecycle_policy/quota_bytes`）。file 路线三档各一个
  目录（热档沿用 `STORAGE_DIR`，向后兼容 W3 落盘路径）；object 路线 S3 三桶
  （`S3_BUCKET` / `S3_WARM_BUCKET` / `S3_COLD_BUCKET`，`S3_ACCESS_KEY` /
  `S3_SECRET_KEY` 环境注入）。
- **迁移安全**：`migrate_blob` 先写目标、回读逐字节校验、再删源；目标已有
  同键不同内容对象时拒绝迁移保住源端。宁可冗余、不可丢数据。
- **SigV4**：canonical request + HMAC 链全标准库；`x-amz-date`/签名对注入
  时钟确定性可测（`test_signature_deterministic`）。

## 生命周期规则（切片 B）

- 策略配在桶上：`POST /api/console/storage/buckets/{id}/policy`
  （`storage:写`），目标档必须低于本桶层级（422 拒绝向上配置）。
- 闲置天数同时满足多条取最深一档：闲置 200 天直接 热→冷，不中途落温。
- 每轮迁移：blob 搬移 + `StorageObject.bucket_id` 改挂 + 两桶 `used_bytes`
  结转 + 一条「存储迁移」审计（含 from/to 层级、key、字节数）；幂等复跑
  已迁移对象不重复计数。
- 策略非法（解析失败 / 目标桶层级不符）不阻断其他桶：落「生命周期·失败」
  审计后继续。

## 容量水位监控（切片 C）

- `GET /api/console/storage/buckets`：桶清单带水位快照（`storage:读`）。
- `GET /api/console/storage/watermark`：水位只读快照（告警线 0.8）。
- `POST /api/console/storage/watermark/check`：巡检落告警/恢复审计
  （`storage:写`）；worker `storage.watermark_check` 每小时同口径巡检。
- RBAC：admin 全权；operator 读写；auditor 只读；researcher/guest 403。

## 压测脚本（切片 D1）

```bash
python3 scripts/loadtest_api.py --base-url http://localhost:8000 \
  --paths /healthz,/api/datasets --concurrency 8 --duration 30 \
  --api-key gk_xxx --json-out loadtest.json
```

用途：容量水位告警的配套验证——压测推高 `used_bytes` / 请求水位，观察
`POST /api/console/storage/watermark/check` 的告警落审计与审计控制台检索。

## 备份演练预案（切片 D2）

**目标**：验证「业务库（pgSTAC 同库）+ 存储三档目录」的备份可恢复。

**频率**：每季度一次完整演练；生产变更（迁移策略调整 / 存储扩容）后加练。

**步骤**（`scripts/backup_drill.sh`，退出码非 0 即演练失败）：
1. `pg_dump` 业务库 → `geodata.sql.gz`（`PG_DSN` 可配，`SKIP_PG=1` 跳过）；
2. 三档存储目录 tar.gz（`STORAGE_DIR` / `STORAGE_DIR_WARM` 同口径环境变量）；
3. 生成 SHA256 清单 `MANIFEST.sha256`；
4. 隔离目录「从备份介质读回」逐文件校验 + tar 解包恢复验证；
5. 落 `DRILL-REPORT.txt`（时间 / 库 / 目录 / 结果）。

**失败处置**：步骤 4 校验不一致 → 备份不可用 → 立即停对应写入（operator
在存储控制台暂停采集落盘）→ 上报管理员 → 重做备份并复练；演练产物保留
3 个季度供审计。

**RTO/RPO 口径**：全量备份每日一次（cron 与生命周期任务错峰），RPO=24h；
恢复演练目标 RTO=2h（pg 恢复 + 三档目录解包 + `readyz` 探活通过）。

## 验证证据

- 后端全量 `python3 -m pytest tests -q` → **159 passed**（W4 118 例全保留，
  新增 41 例：S3 兼容存储 7 —— SigV4 签名/确定性/get·exists·delete 行为/
  非法端点与键拦截/中文键编码；分层迁移 8 —— 迁移方向校验/跨档搬移/
  幂等重放/同键冲突保源/缺源报错/本地 delete；生命周期 12 —— 策略解析
  合法与 6 类非法/最深档选择/回退 created_at/迁移执行与审计/幂等/失败兜底/
  链式下沉/层级冲突；水位 4 —— 边界(恰 80% 告警)/告警一次落库/恢复再告警/
  正常静默；存储 API 10 —— RBAC 矩阵/水位快照/巡检/告警经审计控制台可达/
  策略配置校验/迁移触发端到端）。
- `scripts/loadtest_api.py` 桩服务器实测：581.88 RPS，p50 2.3ms /
  p95 3.9ms / p99 5.1ms，0 错误，JSON 报告落盘。
- `scripts/backup_drill.sh` 合成三档目录实测：清单校验 OK、tar 恢复 5 文件、
  演练报告落盘，退出码 0。
- CI：backend job（159 例）+ frontend job（6 例 + build）+ migrate-smoke
  （pgSTAC 真库契约，本轮无 schema 变更，门禁原样通过）。

## 已知边界与风险

- object 路线的 `S3CompatBlobStore` 已离线单测（签名/协议行为），真机
  Ceph RGW / MinIO 联调待部署环境（生产 STORAGE_ROUTE=object 时注入
  `S3_ACCESS_KEY/SECRET_KEY`）。
- 水位告警当前以审计日志为送达通道（控制台可检索）；IM/webhook 推送
  为 W6+ 运维告警通道扩展项。
- 冷档对象暂不归档压缩（冷档即低成本存储），归档压缩为后续优化项。
