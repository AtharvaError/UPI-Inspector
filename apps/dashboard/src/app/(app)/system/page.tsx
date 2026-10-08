"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { num, pct, TIER_COLOR } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { Banner, Card, Loading, PageHead, Stat } from "@/components/ui";

const STAGE: Record<string, string> = { read: "Cache read", features: "Features", rules: "Rule engine", model: "Model inference", explain: "Reasons and family", total: "Total" };
const BUDGET: Record<string, number> = { read: 10, features: 26, rules: 2, model: 10, explain: 12, total: 150 };

export default function SystemHealth() {
  const { data, loading } = useApi<any>("/v1/metrics/summary", [], 3000);
  if (loading && !data) return <Loading rows={8} />;
  if (!data) return <Banner kind="err">Could not load metrics.</Banner>;
  const lat = data.latency_ms || {};
  const hours: Record<string, any> = {};
  for (const r of data.by_hour || []) {
    const k = r.h;
    hours[k] = hours[k] || { h: new Date(k).getHours() + ":00", SAFE: 0, WARNING: 0, STEP_UP: 0, BLOCK: 0 };
    hours[k][r.tier] = r.n;
  }
  const series = Object.values(hours);
  const env = data.environment || {};
  return (
    <>
      <PageHead title="System health" sub="The hot path's latency by stage against its budget, and the state of everything behind it. An outage never blocks a payment: decisions degrade to rules and are capped at a warning." />
      {(data.chaos?.cache_down || data.chaos?.model_down) && <Banner kind="warn">Failure test active: {data.chaos.cache_down ? "cache unavailable. " : ""}{data.chaos.model_down ? "model unavailable." : ""} Decisions are being marked degraded.</Banner>}
      <div className="grid g4">
        <Stat label="Latency p95 (total)" value={lat.total ? `${lat.total.p95} ms` : "—"} sub={lat.total ? `p50 ${lat.total.p50} · p99 ${lat.total.p99} · budget 150` : "no traffic yet"} tone={lat.total && lat.total.p95 > 150 ? "var(--c-block)" : "var(--c-safe)"} />
        <Stat label="Requests per second" value={data.rps_1m} sub={`${num(data.total)} since start`} />
        <Stat label="Degraded decisions" value={num(data.degraded)} sub={`${data.timeouts} timeouts · ${data.cache_fallbacks} cache fallbacks`} tone={data.degraded ? "var(--c-warning)" : undefined} />
        <Stat label="Event backlog" value={num(data.event_backlog)} sub={`${num(data.events_published)} published · ${data.event_handler_failures} handler failures`} />
      </div>
      <div className="grid g2">
        <Card flush title="Latency by stage">
          <table className="table">
            <thead><tr><th>Stage</th><th className="right">p50</th><th className="right">p95</th><th className="right">p99</th><th className="right">Budget (p95)</th></tr></thead>
            <tbody>{Object.keys(STAGE).filter((s) => lat[s]).map((s) => (
              <tr key={s} style={s === "total" ? { fontWeight: 600 } : undefined}>
                <td>{STAGE[s]}</td><td className="right num">{lat[s].p50}</td>
                <td className="right num" style={{ color: lat[s].p95 > BUDGET[s] ? "var(--c-warning)" : "var(--c-safe)" }}>{lat[s].p95}</td>
                <td className="right num">{lat[s].p99}</td><td className="right num muted">{BUDGET[s]} ms</td>
              </tr>))}
              {!lat.total && <tr><td colSpan={5} className="muted">No assessments yet in this process. Make a payment or start a replay.</td></tr>}
            </tbody>
          </table>
        </Card>
        <Card title="Decisions by hour, last 24 hours">
          <div style={{ height: 230 }}>
            <ResponsiveContainer>
              <AreaChart data={series as any[]} margin={{ left: -16, right: 8, top: 8 }}>
                <CartesianGrid stroke="#221F3A" vertical={false} />
                <XAxis dataKey="h" stroke="#6F6C8F" fontSize={11} /><YAxis stroke="#6F6C8F" fontSize={11} />
                <Tooltip contentStyle={{ background: "#16142B", border: "1px solid #34315A", borderRadius: 12, fontSize: 12 }} />
                {["SAFE", "WARNING", "STEP_UP", "BLOCK"].map((t) => <Area key={t} dataKey={t} stackId="1" stroke={TIER_COLOR[t]} fill={TIER_COLOR[t]} fillOpacity={0.35} isAnimationActive={false} />)}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>
      <div className="grid g2">
        <Card title="Runtime">
          <dl className="kv">
            <dt>Scoring model</dt><dd className="mono">{data.active_model}</dd>
            <dt>Rules</dt><dd className="mono">{data.rules_version}</dd>
            <dt>Text model</dt><dd className="mono">{data.text_model || "none"}</dd>
            <dt>Database</dt><dd>{env.database}</dd>
            <dt>Cache</dt><dd>{env.cache} <span className="caption">(rebuilt from the database in {data.cache_warm_seconds}s at start)</span></dd>
            <dt>Event bus</dt><dd>{env.event_bus}</dd>
            <dt>Uptime</dt><dd>{Math.floor(data.uptime_s / 60)} min</dd>
            <dt>Demo timing</dt><dd>{env.demo_mode ? "on (short hold, cool-off and guardian timers)" : "off"}</dd>
          </dl>
        </Card>
        <Card title="Background jobs">
          <dl className="kv">
            <dt>Network graph</dt><dd>{data.graph_job?.runs ? `${data.graph_job.runs} runs · last took ${data.graph_job.seconds}s over ${num(data.graph_job.edges)} payments · ${num(data.graph_job.nodes)} UPI IDs with flags` : "first run pending"}</dd>
            <dt>Cache fallback rate</dt><dd>{pct(data.fallback_rate, 2)}</dd>
            <dt>Tier mix since start</dt><dd>{Object.entries(data.tiers || {}).map(([k, v]) => `${k.toLowerCase().replace("_", "-")} ${v}`).join(" · ") || "—"}</dd>
          </dl>
        </Card>
      </div>
    </>
  );
}
