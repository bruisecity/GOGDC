-- 000_extensions.sql — 数据层基座扩展（W1：pgSTAC 连通性验证）
-- 运行镜像：ghcr.io/stac-utils/pgstac（内置 PostGIS + pgSTAC）
-- 注意：pgSTAC 在该镜像中由内置 initdb 脚本以原始 SQL 装载（999_pgstac.sql），
--       并非通过 CREATE EXTENSION 安装（镜像内无 pgstac.control），故此处不建该扩展。
-- 版本口径：pgSTAC v0.10.x（2026-11 检索时点最新，随镜像锁定具体小版本）
CREATE EXTENSION IF NOT EXISTS postgis;

-- 验证（W1 验收：pgSTAC 可连通）：
--   SELECT extversion FROM pg_extension WHERE extname = 'postgis';
--   SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'pgstac';
