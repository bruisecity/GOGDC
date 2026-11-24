// 纯逻辑测试（node --test）：不依赖浏览器与网络。
import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizeBbox, formatBytes, buildItemsQuery, buildSearchBody, buildAuditQuery,
} from "../src/api.js";

test("normalizeBbox 四项齐全且方向合法", () => {
  assert.deepEqual(normalizeBbox("-105", "39", "-104", "40"), [-105, 39, -104, 40]);
});

test("normalizeBbox 缺项/非数值/方向非法返回 null", () => {
  assert.equal(normalizeBbox("", "39", "-104", "40"), null);
  assert.equal(normalizeBbox("abc", "39", "-104", "40"), null);
  assert.equal(normalizeBbox("-104", "39", "-105", "40"), null);
});

test("formatBytes 常见量级", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(2048), "2.0 KB");
  assert.equal(formatBytes(1536 * 1024 * 1024), "1.5 GB");
});

test("buildItemsQuery limit 夹在 1..100", () => {
  assert.equal(buildItemsQuery("c", 500), "?limit=100");
  assert.equal(buildItemsQuery("c", 0), "?limit=1");
  assert.equal(buildItemsQuery("c", "abc"), "?limit=10");
});

test("buildSearchBody 只携带显式字段且 limit 夹取", () => {
  const body = buildSearchBody({ collections: ["usgs-ned"], bbox: [-105, 39, -104, 40], limit: "999" });
  assert.deepEqual(body, { collections: ["usgs-ned"], bbox: [-105, 39, -104, 40], limit: 100 });
  const empty = buildSearchBody({ limit: "" });
  assert.deepEqual(empty, { limit: 10 });
});

test("buildAuditQuery 过滤项与分页", () => {
  assert.equal(
    buildAuditQuery({ operation: "数据下载", traceId: "t-1", limit: "50", offset: 10 }),
    "?operation=" + encodeURIComponent("数据下载") + "&trace_id=t-1&limit=50&offset=10"
  );
  assert.equal(buildAuditQuery({}), "?limit=20&offset=0");
});
