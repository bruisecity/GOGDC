# W4 交付记录：门户前端与合规检测（2026-11-23 ~ 11-29）

对应排期第 8 章 W4：「合规与接入：安全检测流水线（四类规则）+ 审计日志落库」，
本轮按用户指令扩为**门户前端 + 合规检测**双线交付（W3 收尾时 `docs/W3-CATALOG.md`
已预告 W4 范围，`detection_service.py` 中敏感信息/坐标基准占位本轮补齐）。

W4 验收口径与逐条落地：

| 验收项 | 状态 | 实现位置 |
| --- | --- | --- |
| 不合格数据进隔离区（四类规则） | ✅ | `app/services/detection_service.py`：新增**敏感信息**（正则扫描 blob 载荷，参数化 patterns + max_scan_bytes，blob 不可读即违规）与**坐标基准**（bbox 存在性 / 方向 / 经纬度范围，参数化 datum + lon/lat_range）两条规则；与 W3 的元数据完整性、病毒载荷合计**四类规则**全部实装。任一隔离动作规则命中 → `QuarantineSample` + 数据集 `已隔离` |
| 审计可查 | ✅ | `app/routes/audit.py`：`GET /api/console/audit-logs`（operation / trace_id / user_id / result 过滤 + limit/offset 分页 + id 倒序）；RBAC `audit:读`（admin / operator / auditor） |
| 检测规则可管 | ✅ | `app/routes/detection.py`：`GET /api/console/detection-rules` 列表 + `PATCH /{id}` 启停（`detection:写`：admin / operator；`detection:读` 可查） |
| 门户前端 | ✅ | `frontend/`：React 18 + Vite 5 门户（数据目录检索 + 审计与检测控制台两个视图，hash 路由）；API Key 走 sessionStorage，无密钥接口 403 |

## 合规检测（四类规则闭环）

- **规则模型**：`DetectionRule.rule_type` ∈（元数据完整性 / 病毒载荷 / 敏感信息 /
  坐标基准），`params` JSON 驱动、`action` ∈（隔离 / 放行 / 标记）；
  `run_detection` 逐条执行**启用中**的规则，命中隔离动作即 `security_status=已隔离`
  并落 `QuarantineSample`。
- **敏感信息**：`params={patterns:[{name, regex}], max_scan_bytes}` —— 经
  `blob_store.get()` 读载荷（utf-8 容错解码、截断到扫描上限）后逐模式
  `re.findall`；blob 不可读直接判违规；正则本身非法时报违规但不计命中。
- **坐标基准**：`params={datum, lon_range, lat_range}`（默认 WGS84 /
  [-180,180] / [-90,90]）—— 缺 bbox 违规、west>east 或 south>north 判
  方向非法、经纬度越界违规。
- 未知 rule_type 返回空（不误伤），规则库演进时旧数据不受影响。

## 门户前端（frontend/）

- **数据目录检索（STAC）**：集合下拉 + bbox 四项（缺项前端拦截提示）+ 时间
  窗口 + 每页，`POST /stac/v1/search` 检索或浏览集合 items；条目行展示
  格式/大小/采集时间与下载链接（asset href）。
- **审计与检测（控制台）**：API Key 输入（sessionStorage 持久化）→
  `GET /api/console/audit-logs` 按 操作 / trace_id / 分页 查询审计日志；
  `GET /api/console/detection-rules` 列出四类规则并可 `PATCH` 启停。
- **可测性**：`src/api.js` 把纯函数（`normalizeBbox` / `buildSearchBody` /
  `buildAuditQuery` / `formatBytes` 等）与 fetch 封装分离，`node --test`
  无浏览器即可跑单测（6 例）。
- **部署**：`frontend/Dockerfile`（node 构建 → nginx 托管 dist，`/api` `/stac`
  反代到 api 服务）+ `nginx.conf`；compose 新增 `portal` 服务（4173→80）。
  开发态 `vite dev/preview` 均已代理 `/api` `/stac` → `localhost:8000`。
- 门户视觉还原不在设计稿范围（设计稿明确排除），本轮交付功能完整门户。

## 验证证据

- 后端全量 `python3 -m pytest tests -q` → **118 passed**（W3 105 例全保留，
  新增 13 例：检测服务 7 —— 敏感信息命中/干净载荷/空模式、坐标越界/缺
  bbox/合法/方向非法；审计 API 3 —— 匿名 403、RBAC 矩阵、过滤分页倒序；
  检测规则 API 3 —— 列表 RBAC、启停权限、404）。
- 前端 `npm test` → 6/6；`npm run build` 产物 150.48 kB（gzip 48.64 kB）。
- **真实链路冒烟**（uvicorn :8000 + vite preview :4173 + SQLite 种子库）：
  无头 Chromium（puppeteer-core）实测 16/16 通过 —— 目录页渲染/空态/bbox
  四项校验与补齐后检索、审计页填 Key 查询 6 行日志、四类规则 4 行、
  规则启停切换（启用→停用→还原）、API Key 会话保持、控制台零报错。
- CI：backend job（118 例）+ 新增 frontend job（npm ci + node:test + vite
  build）+ migrate-smoke（pgSTAC 真库契约）。

## 已知边界与风险

- 敏感信息规则的正则库（手机号/身份证号等）以**参数化**形式由规则库下发，
  运营态扩充规则不需改代码；`max_scan_bytes` 默认值在规则 params 里维护。
- 病毒载荷规则沿用 W3 的签名匹配口径，真实病毒引擎（ClamAV 等）接入留待
  运营期评估。
- W2 遗留：本地 master 分支存在一个损坏松散对象（ebccceb，W2 记录），
  不影响 push-now 分支与远端 main，下轮顺手修复。

## 下一步（W5 预告）

存储分级（S3/Ceph RGW 对象存储适配 `BlobStore` 协议）+ 运营观测
（指标、告警）——按排期第 8 章推进。
