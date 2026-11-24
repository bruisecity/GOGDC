import React, { useEffect, useState } from "react";
import {
  fetchCollections, fetchItems, searchItems, fetchAuditLogs,
  fetchDetectionRules, toggleDetectionRule, formatBytes,
} from "./api.js";

function useHashRoute() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

function Nav() {
  const items = [
    ["#/", "数据目录"],
    ["#/audit", "审计与检测"],
  ];
  return (
    <nav className="nav">
      <span className="brand">Geodata · GOGDC</span>
      {items.map(([href, label]) => (
        <a key={href} href={href}>{label}</a>
      ))}
    </nav>
  );
}

function ItemRow({ item }) {
  const props = item.properties || {};
  const href = item.assets && item.assets.data && item.assets.data.href;
  return (
    <tr>
      <td>{item.id}</td>
      <td>{(props["geodata:format"] || "-") + ""}</td>
      <td>{formatBytes(props["geodata:size_bytes"] || 0)}</td>
      <td>{(props.datetime || "-").slice(0, 19).replace("T", " ")}</td>
      <td>
        {href ? (
          <a className="btn" href={href}>下载</a>
        ) : (
          <span className="muted">无资产</span>
        )}
      </td>
    </tr>
  );
}

function CatalogPage() {
  const [collections, setCollections] = useState([]);
  const [collection, setCollection] = useState("");
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [search, setSearch] = useState({ west: "", south: "", east: "", north: "", datetime: "", limit: "20" });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchCollections()
      .then((cs) => setCollections(cs))
      .catch((e) => setError(String(e.message)));
  }, []);

  async function loadItems(cid) {
    setLoading(true);
    setError("");
    try {
      setItems(await fetchItems(cid, Number(search.limit)));
    } catch (e) {
      setError(String(e.message));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  async function onSearch() {
    setError("");
    const body = { ...search, collections: collection ? [collection] : [] };
    if (search.west || search.south || search.east || search.north) {
      // bbox 全填才生效，四项不齐时提示
      if (!(search.west && search.south && search.east && search.north)) {
        setError("bbox 需 west/south/east/north 四项齐全");
        return;
      }
    }
    setLoading(true);
    try {
      setItems(await searchItems(body));
    } catch (e) {
      setError(String(e.message));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h2>数据目录检索（STAC）</h2>
      <p className="muted">
        集合 {collections.length} 个 · 条目 {items.length} 条
        {loading ? " · 加载中…" : ""}
      </p>
      <div className="row">
        <label>
          集合
          <select value={collection} onChange={(e) => setCollection(e.target.value)}>
            <option value="">（全部集合）</option>
            {collections.map((c) => (
              <option key={c.id} value={c.id}>{c.id}</option>
            ))}
          </select>
        </label>
        {(["west", "south", "east", "north"] ).map((k) => (
          <label key={k}>
            bbox {k}
            <input
              size="7"
              value={search[k]}
              onChange={(e) => setSearch({ ...search, [k]: e.target.value })}
              placeholder={k === "west" || k === "east" ? "-180 ~ 180" : "-90 ~ 90"}
            />
          </label>
        ))}
        <label>
          时间
          <input
            size="16"
            value={search.datetime}
            onChange={(e) => setSearch({ ...search, datetime: e.target.value })}
            placeholder="2020-01-01T00:00:00Z/2026-01-01T00:00:00Z"
          />
        </label>
        <label>
          每页
          <select value={search.limit} onChange={(e) => setSearch({ ...search, limit: e.target.value })}>
            {["10", "20", "50", "100"].map((n) => <option key={n}>{n}</option>)}
          </select>
        </label>
        <button onClick={onSearch}>检索</button>
        {collection ? <button className="ghost" onClick={() => loadItems(collection)}>浏览集合</button> : null}
      </div>
      {error ? <p className="error">{error}</p> : null}
      <table className="data">
        <thead>
          <tr><th>条目 ID</th><th>格式</th><th>大小</th><th>采集时间</th><th>下载</th></tr>
        </thead>
        <tbody>
          {items.map((it) => <ItemRow key={it.id} item={it} />)}
        </tbody>
      </table>
    </section>
  );
}

function AuditPage() {
  const [apiKey, setApiKey] = useState(sessionStorage.getItem("gogdc_key") || "");
  const [filters, setFilters] = useState({ operation: "", traceId: "", limit: "20" });
  const [logs, setLogs] = useState([]);
  const [rules, setRules] = useState([]);
  const [error, setError] = useState("");
  const [total, setTotal] = useState(0);

  function saveKey(v) {
    setApiKey(v);
    sessionStorage.setItem("gogdc_key", v);
  }

  async function loadLogs() {
    setError("");
    try {
      const body = await fetchAuditLogs(apiKey, filters);
      setLogs(body.items || []);
      setTotal(body.total || 0);
    } catch (e) {
      setError(String(e.message));
      setLogs([]);
      setTotal(0);
    }
  }

  async function loadRules() {
    try {
      setRules(await fetchDetectionRules(apiKey));
    } catch (e) {
      setError(String(e.message));
    }
  }

  async function onToggle(rule) {
    setError("");
    try {
      await toggleDetectionRule(apiKey, rule.id, !rule.enabled);
      await loadRules();
    } catch (e) {
      setError(String(e.message));
    }
  }

  return (
    <section>
      <h2>审计日志与检测规则（控制台）</h2>
      <div className="row">
        <label>
          API Key
          <input size="40" value={apiKey} onChange={(e) => saveKey(e.target.value)} placeholder="gk_…" />
        </label>
        <label>
          操作
          <select value={filters.operation} onChange={(e) => setFilters({ ...filters, operation: e.target.value })}>
            <option value="">（全部）</option>
            {["采集落盘", "安全检测", "STAC编目", "数据下载"].map((op) => (
              <option key={op}>{op}</option>
            ))}
          </select>
        </label>
        <label>
          trace_id
          <input size="14" value={filters.traceId} onChange={(e) => setFilters({ ...filters, traceId: e.target.value })} />
        </label>
        <label>
          每页
          <select value={filters.limit} onChange={(e) => setFilters({ ...filters, limit: e.target.value })}>
            {["20", "50", "100"].map((n) => <option key={n}>{n}</option>)}
          </select>
        </label>
        <button onClick={loadLogs}>查询审计</button>
        <button className="ghost" onClick={loadRules}>刷新规则</button>
      </div>
      {error ? <p className="error">{error}</p> : null}
      <p className="muted">审计 {total} 条（需 audit:读 权限：admin / operator / auditor）</p>
      <table className="data">
        <thead>
          <tr><th>ID</th><th>时间</th><th>操作</th><th>资源</th><th>结果</th><th>trace_id</th></tr>
        </thead>
        <tbody>
          {logs.map((l) => (
            <tr key={l.id}>
              <td>{l.id}</td>
              <td>{(l.created_at || "").slice(0, 19).replace("T", " ")}</td>
              <td>{l.operation}</td>
              <td>{l.resource || "-"}</td>
              <td>{l.result || "-"}</td>
              <td>{l.trace_id || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>检测规则（detection:写 可启停）</h3>
      <table className="data">
        <thead>
          <tr><th>ID</th><th>类型</th><th>动作</th><th>级别</th><th>版本</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.rule_type}</td>
              <td>{r.action}</td>
              <td>{r.severity}</td>
              <td>v{r.version}</td>
              <td>{r.enabled ? "启用" : "停用"}</td>
              <td><button className="ghost" onClick={() => onToggle(r)}>{r.enabled ? "停用" : "启用"}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function App() {
  const route = useHashRoute();
  return (
    <div className="app">
      <Nav />
      <main>
        {route.startsWith("#/audit") ? <AuditPage /> : <CatalogPage />}
      </main>
      <footer className="muted">
        Geodata（GOGDC）全球开放地理空间数据收储平台 · W4 门户前端
      </footer>
    </div>
  );
}
