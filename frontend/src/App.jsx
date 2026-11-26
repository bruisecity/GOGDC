import React, { useEffect, useState } from "react";
import {
  fetchCollections, fetchItems, searchItems, fetchAuditLogs,
  fetchDetectionRules, toggleDetectionRule, formatBytes,
  fetchOverview, fetchHarvestTasks, fetchNodes,
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
    ["#/console", "数据中台"],
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

const FAILURE_CLASSES = ["连接超时", "源站限流", "凭证失效", "元数据校验"];
const TASK_STATUS = ["idle", "running", "failed", "paused"];
const SECURITY_STATUS = ["检测通过", "检测中", "待检测", "已隔离"];

function ConsoleKeyBar({ apiKey, onKey }) {
  return (
    <div className="row">
      <label>
        API Key
        <input
          size="40"
          value={apiKey}
          onChange={(e) => onKey(e.target.value)}
          placeholder="gk_…（中台接口均要求登录态）"
        />
      </label>
    </div>
  );
}

function OverviewPanel({ apiKey }) {
  const [ov, setOv] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      setOv(await fetchOverview(apiKey));
    } catch (e) {
      setError(String(e.message));
      setOv(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div className="row">
        <button onClick={load} disabled={loading}>{loading ? "加载中…" : "刷新总览"}</button>
        {ov ? <span className="muted">生成于 {(ov.generated_at || "").slice(0, 19).replace("T", " ")}</span> : null}
      </div>
      {error ? <p className="error">{error}</p> : null}
      {ov ? (
        <div>
          <h3>数据集</h3>
          <p>
            共 <b>{ov.datasets.total}</b> 个 · <b>{formatBytes(ov.datasets.total_bytes)}</b> · 分类 <b>{ov.dataset_categories}</b> 类
          </p>
          <p className="muted">
            安全检测口径：{SECURITY_STATUS.map((s) => `${s} ${ov.datasets.by_security_status[s] ?? 0}`).join(" · ")}
          </p>

          <h3>汇聚节点</h3>
          <p>
            在线 <b>{ov.nodes.online}/{ov.nodes.total}</b> · 在线率 <b>{(ov.nodes.online_rate * 100).toFixed(1)}%</b>
          </p>

          <h3>数据源健康</h3>
          <p className="muted">
            {["healthy", "degraded", "down"].map((h) => `${h} ${ov.data_sources.by_health[h] ?? 0}`).join(" · ")}
          </p>
          <table className="data">
            <thead>
              <tr><th>源</th><th>健康</th><th>配额（已用/上限）</th><th>最近成功</th></tr>
            </thead>
            <tbody>
              {ov.data_sources.items.map((s) => (
                <tr key={s.code}>
                  <td>{s.code}</td>
                  <td>{s.health_status}</td>
                  <td>{s.quota_used}/{s.quota_limit}</td>
                  <td>{(s.last_success_at || "-").slice(0, 19).replace("T", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>采集任务</h3>
          <p className="muted">{TASK_STATUS.map((s) => `${s} ${ov.harvest_tasks.by_status[s] ?? 0}`).join(" · ")}</p>
          <p className="muted">
            四类失败口径：{FAILURE_CLASSES.map((c) => `${c} ${ov.harvest_failures[c] ?? 0}`).join(" · ")}
          </p>

          <h3>存储水位</h3>
          <p>
            已用 <b>{formatBytes(ov.storage.total_used_bytes)}</b> / 配额 <b>{formatBytes(ov.storage.total_quota_bytes)}</b>
            （告警线 {(ov.storage.alert_ratio * 100).toFixed(0)}%）
          </p>
          <table className="data">
            <thead>
              <tr><th>桶</th><th>层级</th><th>已用</th><th>配额</th><th>水位</th><th>状态</th></tr>
            </thead>
            <tbody>
              {ov.storage.buckets.map((b) => (
                <tr key={b.name}>
                  <td>{b.name}</td>
                  <td>{b.tier}</td>
                  <td>{formatBytes(b.used_bytes)}</td>
                  <td>{formatBytes(b.quota_bytes)}</td>
                  <td>{b.ratio === null || b.ratio === undefined ? "-" : `${(b.ratio * 100).toFixed(1)}%`}</td>
                  <td>{b.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function HarvestPanel({ apiKey }) {
  const [tasks, setTasks] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const limit = 20;

  async function load(off = 0) {
    setError("");
    try {
      const body = await fetchHarvestTasks(apiKey, { limit, offset: off });
      setTasks(body.items || []);
      setTotal(body.total || 0);
      setOffset(off);
    } catch (e) {
      setError(String(e.message));
      setTasks([]);
    }
  }

  useEffect(() => { load(0); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div className="row">
        <button onClick={() => load(offset)}>刷新</button>
        {offset > 0 ? (
          <button className="ghost" onClick={() => load(Math.max(0, offset - limit))}>上一页</button>
        ) : null}
        {offset + limit < total ? (
          <button className="ghost" onClick={() => load(offset + limit)}>下一页</button>
        ) : null}
        <span className="muted">共 {total} 条（第 {offset + 1}–{Math.min(offset + limit, total)} 条）</span>
      </div>
      {error ? <p className="error">{error}</p> : null}
      <table className="data">
        <thead>
          <tr>
            <th>ID</th><th>源</th><th>节点</th><th>cron</th><th>时间窗</th>
            <th>重试</th><th>失败类别</th><th>状态</th><th>上次运行</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id}>
              <td>{t.id}</td>
              <td>{t.data_source_id}</td>
              <td>{t.node_id}</td>
              <td>{t.schedule_cron}</td>
              <td>{t.time_window || "-"}</td>
              <td>{t.retry_count}/{t.retry_max}</td>
              <td>{t.last_failure_class || "-"}</td>
              <td>{t.status}</td>
              <td>{(t.last_run_at || "-").slice(0, 19).replace("T", " ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NodesPanel({ apiKey }) {
  const [nodes, setNodes] = useState([]);
  const [online, setOnline] = useState(0);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    try {
      const body = await fetchNodes(apiKey);
      setNodes(body.items || []);
      setOnline(body.online || 0);
      setTotal(body.total || 0);
    } catch (e) {
      setError(String(e.message));
      setNodes([]);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div className="row">
        <button onClick={load}>刷新节点</button>
        <span className="muted">
          共 {total} 个 · 在线 {online}（心跳 5 分钟内视为在线）
        </span>
      </div>
      {error ? <p className="error">{error}</p> : null}
      <table className="data">
        <thead>
          <tr>
            <th>ID</th><th>编码</th><th>承担单位</th><th>公网出口</th><th>在线</th>
            <th>磁盘（已用/总量）</th><th>累计下载</th><th>任务数</th><th>心跳时间</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((n) => (
            <tr key={n.id}>
              <td>{n.id}</td>
              <td>{n.code}</td>
              <td>{n.org_name}</td>
              <td>{n.public_egress ? "是" : "否"}</td>
              <td>{n.online ? "在线" : "离线"}</td>
              <td>
                {formatBytes(n.disk_used)}/{formatBytes(n.disk_total)}
                {n.disk_total ? `（${((n.disk_used / n.disk_total) * 100).toFixed(1)}%）` : ""}
              </td>
              <td>{formatBytes(n.download_bytes_total)}</td>
              <td>{n.task_count}</td>
              <td>{(n.heartbeat_at || "-").slice(0, 19).replace("T", " ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DetectionPanel({ apiKey }) {
  const [rules, setRules] = useState([]);
  const [error, setError] = useState("");

  async function load() {
    setError("");
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
      await load();
    } catch (e) {
      setError(String(e.message));
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div className="row">
        <button onClick={load}>刷新规则</button>
        <span className="muted">detection:写 角色可启停（admin / auditor 审批线之外的操作口径）</span>
      </div>
      {error ? <p className="error">{error}</p> : null}
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
    </div>
  );
}

const CONSOLE_TABS = [
  ["overview", "总览"],
  ["tasks", "采集任务"],
  ["nodes", "汇聚节点"],
  ["detection", "安全检测"],
];

function ConsolePage() {
  const [apiKey, setApiKey] = useState(sessionStorage.getItem("gogdc_key") || "");
  const [tab, setTab] = useState("overview");

  function saveKey(v) {
    setApiKey(v);
    sessionStorage.setItem("gogdc_key", v);
  }

  return (
    <section>
      <h2>数据中台</h2>
      <ConsoleKeyBar apiKey={apiKey} onKey={saveKey} />
      <nav className="row">
        {CONSOLE_TABS.map(([id, label]) => (
          <button key={id} className={tab === id ? "" : "ghost"} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>
      {tab === "overview" ? <OverviewPanel apiKey={apiKey} /> : null}
      {tab === "tasks" ? <HarvestPanel apiKey={apiKey} /> : null}
      {tab === "nodes" ? <NodesPanel apiKey={apiKey} /> : null}
      {tab === "detection" ? <DetectionPanel apiKey={apiKey} /> : null}
    </section>
  );
}

export default function App() {
  const route = useHashRoute();
  return (
    <div className="app">
      <Nav />
      <main>
        {route.startsWith("#/audit") ? <AuditPage /> : null}
        {route.startsWith("#/console") ? <ConsolePage /> : null}
        {route === "#/" || route === "" ? <CatalogPage /> : null}
      </main>
      <footer className="muted">
        Geodata（GOGDC）全球开放地理空间数据收储平台 · W7 数据中台前端
      </footer>
    </div>
  );
}
