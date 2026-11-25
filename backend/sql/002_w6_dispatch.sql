-- 002_w6_dispatch.sql — W6 调度策略完善增量变更
-- 依据：设计稿第三章「harvest_task 含调度窗口」+ W6「调度策略完善」。
-- 口径：sql/*.sql 是 DDL 唯一事实来源；001 为新建库的完整快照（已同步包含以下两列），
--       本文件只负责给「已存在的旧库」补齐列，因此全部 IF NOT EXISTS 幂等。
-- 0001_w2_baseline 迁移脚本本身不再改动，结构演进一律新增 revision。

BEGIN;

-- 限流时段：与 schedule_cron 共同构成调度策略；空串 = 不限时段
ALTER TABLE harvest_task
    ADD COLUMN IF NOT EXISTS time_window VARCHAR(32) NOT NULL DEFAULT '';

-- 最近一次调度执行时刻（到期判定与运维排障口径；成功/失败都写）
ALTER TABLE harvest_task
    ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMPTZ;

COMMIT;
