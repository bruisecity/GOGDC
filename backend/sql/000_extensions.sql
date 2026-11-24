-- 000_extensions.sql — 数据层基座扩展（W1：pgSTAC 连通性验证）
-- 运行镜像：ghcr.io/stac-utils/pgstac（内置 PostGIS + pgSTAC）
-- 版本口径：pgSTAC v0.10.x（2026-11 检索时点最新，随镜像锁定具体小版本）
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgstac;

-- 验证（W1 验收：pgSTAC 可连通）：
--   SELECT extversion FROM pg_extension WHERE extname = 'pgstac';
--   SELECT * FROM pgstac.get_setting('schema');
