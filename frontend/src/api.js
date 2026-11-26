// API 客户端与纯逻辑（纯函数单独导出供 node --test 测试）。

export function normalizeBbox(west, south, east, north) {
  const vals = [west, south, east, north]
    .map((v) => (v === "" || v === null || v === undefined ? null : Number(v)));
  if (vals.some((v) => v === null || Number.isNaN(v))) return null;
  const [w, s, e, n] = vals;
  if (w > e || s > n) return null;
  return [w, s, e, n];
}

export function formatBytes(n) {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 10 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function buildItemsQuery(collectionId, limit) {
  const n = Number(limit);
  const lim = Math.min(Math.max(Number.isFinite(n) ? n : 10, 1), 100);
  return `?limit=${lim}`;
}

export function buildSearchBody({ collections, ids, bbox, datetime, limit }) {
  const body = {};
  if (collections && collections.length) body.collections = collections;
  if (ids && ids.length) body.ids = ids;
  if (bbox) body.bbox = bbox;
  if (datetime) body.datetime = datetime;
  const raw = limit === "" || limit === null || limit === undefined ? undefined : Number(limit);
  const lim = Math.min(Math.max(Number.isFinite(raw) ? raw : 10, 1), 100);
  body.limit = lim;
  return body;
}

export function buildAuditQuery({ operation, traceId, limit, offset }) {
  const params = new URLSearchParams();
  if (operation) params.set("operation", operation);
  if (traceId) params.set("trace_id", traceId);
  const lim = Number(limit);
  params.set("limit", String(Math.min(Math.max(Number.isFinite(lim) ? lim : 20, 1), 100)));
  params.set("offset", String(Math.max(Number(offset) || 0, 0)));
  return `?${params.toString()}`;
}

// ---- 请求封装（依赖浏览器 fetch，不参与纯逻辑测试） ----

async function json(res) {
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).message || "";
    } catch {
      /* ignore */
    }
    throw new Error(`HTTP ${res.status}${detail ? `: ${detail}` : ""}`);
  }
  return res.json();
}

export async function fetchCollections() {
  const body = await json(await fetch("/stac/v1/collections"));
  return body.collections || [];
}

export async function fetchItems(collectionId, limit = 20) {
  const body = await json(
    await fetch(`/stac/v1/collections/${encodeURIComponent(collectionId)}/items${buildItemsQuery(collectionId, limit)}`)
  );
  return body.features || [];
}

export async function searchItems(search) {
  const body = await json(
    await fetch("/stac/v1/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildSearchBody(search)),
    })
  );
  return body.features || [];
}

// ---- 数据中台（W7）：/api/console/* 面板接口 ----

export function buildTasksQuery({ limit, offset }) {
  const lim = Number(limit);
  const l = Math.min(Math.max(Number.isFinite(lim) ? lim : 20, 1), 100);
  const off = Math.max(Number(offset) || 0, 0);
  return `?limit=${l}&offset=${off}`;
}

function consoleHeaders(apiKey) {
  return apiKey ? { "X-API-Key": apiKey } : {};
}

export async function fetchOverview(apiKey) {
  return json(await fetch("/api/console/overview", { headers: consoleHeaders(apiKey) }));
}

export async function fetchHarvestTasks(apiKey, page = {}) {
  return json(
    await fetch(`/api/console/harvest-tasks${buildTasksQuery(page)}`, {
      headers: consoleHeaders(apiKey),
    })
  );
}

export async function fetchNodes(apiKey) {
  return json(await fetch("/api/console/nodes", { headers: consoleHeaders(apiKey) }));
}

export async function fetchAuditLogs(apiKey, filters) {
  return json(
    await fetch(`/api/console/audit-logs${buildAuditQuery(filters)}`, {
      headers: apiKey ? { "X-API-Key": apiKey } : {},
    })
  );
}

export async function fetchDetectionRules(apiKey) {
  const body = await json(
    await fetch("/api/console/detection-rules", {
      headers: apiKey ? { "X-API-Key": apiKey } : {},
    })
  );
  return body.items || [];
}

export async function toggleDetectionRule(apiKey, ruleId, enabled) {
  return json(
    await fetch(`/api/console/detection-rules/${ruleId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
      body: JSON.stringify({ enabled }),
    })
  );
}
