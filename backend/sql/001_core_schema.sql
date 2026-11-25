-- 001_core_schema.sql — 核心实体建库迁移（W1 数据层基座）
-- 依据：技术方案第三章「字段级设计（核心实体）」；与 backend/app/models.py 由
--       CI 一致性测试（tests/test_schema_sync.py）对齐，改一处必须同步另一处。
-- 口径：主键 BIGINT 自增；时间 TIMESTAMPTZ（UTC）；枚举 VARCHAR + CHECK；扩展 JSONB。
-- 注意：collection / item 不建表 —— 直接用 pgSTAC 原生结构，附加属性随 STAC JSON 存放。

BEGIN;

-- ── 数据源 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_source (
    id              BIGSERIAL PRIMARY KEY,
    code            VARCHAR(32)  NOT NULL UNIQUE,
    base_url        VARCHAR(512) NOT NULL,
    credential_ref  VARCHAR(128),                          -- 密钥管理引用，不存明文
    quota_limit     BIGINT       NOT NULL DEFAULT 0,       -- 字节口径
    quota_used      BIGINT       NOT NULL DEFAULT 0,
    health_status   VARCHAR(16)  NOT NULL DEFAULT 'healthy'
                    CHECK (health_status IN ('healthy','degraded','down')),
    last_success_at TIMESTAMPTZ
);

-- ── 汇聚节点 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS node (
    id                  BIGSERIAL PRIMARY KEY,
    code                VARCHAR(32)  NOT NULL UNIQUE,
    org_name            VARCHAR(128) NOT NULL,
    public_egress       BOOLEAN      NOT NULL DEFAULT FALSE, -- 决定代理通信方案
    heartbeat_at        TIMESTAMPTZ,                         -- 超时阈值判离线
    disk_total          BIGINT       NOT NULL DEFAULT 0,
    disk_used           BIGINT       NOT NULL DEFAULT 0,
    download_bytes_total BIGINT      NOT NULL DEFAULT 0
);

-- ── 采集任务 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS harvest_task (
    id                 BIGSERIAL PRIMARY KEY,
    data_source_id     BIGINT      NOT NULL REFERENCES data_source(id),
    node_id            BIGINT      NOT NULL REFERENCES node(id),
    schedule_cron      VARCHAR(64) NOT NULL DEFAULT '',
    time_window        VARCHAR(32) NOT NULL DEFAULT '',   -- W6 限流时段 "HH:MM-HH:MM"，空=不限
    retry_max          SMALLINT    NOT NULL DEFAULT 3,
    retry_count        SMALLINT    NOT NULL DEFAULT 0,
    last_failure_class VARCHAR(32)
                       CHECK (last_failure_class IS NULL OR last_failure_class IN
                       ('连接超时','源站限流','凭证失效','元数据校验')),
    status             VARCHAR(16) NOT NULL DEFAULT 'idle'
                       CHECK (status IN ('idle','running','failed','paused')),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_run_at        TIMESTAMPTZ                        -- W6 最近一次调度执行时刻
);

-- ── 收储分类字典（15 类）──────────────────────────────────
-- TODO(W1 评审)：15 类目清单以业务确认为准后补 INSERT 种子数据。
CREATE TABLE IF NOT EXISTS dataset_category (
    id    SMALLSERIAL PRIMARY KEY,
    code  VARCHAR(32) NOT NULL UNIQUE,
    name  VARCHAR(64) NOT NULL
);

-- ── 数据集 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dataset (
    id              BIGSERIAL PRIMARY KEY,
    category_id     SMALLINT      NOT NULL REFERENCES dataset_category(id),
    format          VARCHAR(16)   NOT NULL DEFAULT '',
    size_bytes      BIGINT        NOT NULL DEFAULT 0,
    license         VARCHAR(64),                        -- SPDX 表达式或 NULL
    security_status VARCHAR(16)   NOT NULL DEFAULT '待检测'
                    CHECK (security_status IN ('检测通过','检测中','待检测','已隔离')),
    bbox_west       NUMERIC(9,5),   -- WGS84，[west, south, east, north] 四列
    bbox_south      NUMERIC(9,5),
    bbox_east       NUMERIC(9,5),
    bbox_north      NUMERIC(9,5),
    temporal_start  DATE,
    temporal_end    DATE,
    storage_key     VARCHAR(1024) NOT NULL,             -- 相对键，与存储路线解耦
    collection_id   VARCHAR(64),                        -- 编目后回填（pgSTAC collection）
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dataset_category   ON dataset(category_id);
CREATE INDEX IF NOT EXISTS idx_dataset_security   ON dataset(security_status);
CREATE INDEX IF NOT EXISTS idx_dataset_collection ON dataset(collection_id);

-- ── 检测规则 / 隔离样本 ──────────────────────────────────
CREATE TABLE IF NOT EXISTS detection_rule (
    id        BIGSERIAL PRIMARY KEY,
    rule_type VARCHAR(32) NOT NULL
              CHECK (rule_type IN ('敏感信息','坐标基准','元数据完整性','病毒载荷')),
    params    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    severity  VARCHAR(16)  NOT NULL CHECK (severity IN ('low','medium','high')),
    action    VARCHAR(16)  NOT NULL CHECK (action IN ('隔离','放行','标记')),
    version   INTEGER      NOT NULL DEFAULT 1,
    enabled   BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS quarantine_sample (
    id             BIGSERIAL PRIMARY KEY,
    dataset_id     BIGINT      NOT NULL REFERENCES dataset(id),
    rule_id        BIGINT      NOT NULL REFERENCES detection_rule(id),
    evidence       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    disposal_state VARCHAR(16) NOT NULL DEFAULT '待处置'
                   CHECK (disposal_state IN
                   ('待处置','人工复核','执行处置','二次确认','归档')),
    operator_id    BIGINT,
    closed_at      TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_quarantine_state ON quarantine_sample(disposal_state);

-- ── 编目批次 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS catalog_batch (
    id              BIGSERIAL PRIMARY KEY,
    batch_no        VARCHAR(32) NOT NULL UNIQUE,
    scope           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    publish_state   VARCHAR(16) NOT NULL DEFAULT '草稿'
                    CHECK (publish_state IN ('草稿','字段校验','预览','发布','归档')),
    version         INTEGER     NOT NULL DEFAULT 1,
    parent_batch_id BIGINT      REFERENCES catalog_batch(id),
    item_count      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_catalog_state ON catalog_batch(publish_state);

-- ── 存储（对象/文件路线通用语义）─────────────────────────
CREATE TABLE IF NOT EXISTS storage_bucket (
    id                 BIGSERIAL PRIMARY KEY,
    name               VARCHAR(128) NOT NULL UNIQUE,
    tier               VARCHAR(8)   NOT NULL CHECK (tier IN ('热','温','冷')),
    lifecycle_policy   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    quota_bytes        BIGINT       NOT NULL DEFAULT 0,   -- 80% 告警线
    used_bytes         BIGINT       NOT NULL DEFAULT 0,
    versioning_enabled BOOLEAN      NOT NULL DEFAULT FALSE,
    object_lock_days   INTEGER
);

CREATE TABLE IF NOT EXISTS storage_object (
    id             BIGSERIAL PRIMARY KEY,
    bucket_id      BIGINT        NOT NULL REFERENCES storage_bucket(id),
    key            VARCHAR(1024) NOT NULL,
    etag           VARCHAR(64),
    checksum       VARCHAR(128),                          -- 下载完整性校验
    size_bytes     BIGINT        NOT NULL DEFAULT 0,
    last_access_at TIMESTAMPTZ,                           -- 冷热分层依据
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_storage_object_key UNIQUE (bucket_id, key)
);
CREATE INDEX IF NOT EXISTS idx_storage_object_last_access ON storage_object(last_access_at);

-- ── 账号 / 角色 / 权限 / 审计 ────────────────────────────
CREATE TABLE IF NOT EXISTS role (
    id   SMALLSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(64) NOT NULL
);
-- TODO(W1 评审)：5 角色命名与设计稿权限矩阵对齐后补 INSERT 种子数据。

CREATE TABLE IF NOT EXISTS app_user (
    id             BIGSERIAL PRIMARY KEY,
    email          VARCHAR(255) NOT NULL UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,
    api_key_hash   VARCHAR(255) UNIQUE,                   -- API Key 与密码双通道
    role_id        SMALLINT     NOT NULL REFERENCES role(id),
    download_quota BIGINT       NOT NULL DEFAULT 0,       -- 字节
    req_quota      INTEGER      NOT NULL DEFAULT 0,       -- 频次
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permission (
    id       BIGSERIAL PRIMARY KEY,
    role_id  SMALLINT    NOT NULL REFERENCES role(id),
    resource VARCHAR(32) NOT NULL,   -- 8 资源
    action   VARCHAR(8)  NOT NULL,   -- 读 / 写 / 审批
    allowed  BOOLEAN     NOT NULL,
    CONSTRAINT uq_permission UNIQUE (role_id, resource, action)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT,
    operation  VARCHAR(64) NOT NULL,
    resource   VARCHAR(32),
    result     VARCHAR(16),
    detail     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    trace_id   VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_trace   ON audit_log(trace_id);

COMMIT;
