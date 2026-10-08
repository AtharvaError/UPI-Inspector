"use client";

import { Network, Search, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ago, BANK, clock, inr, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { BandTag, Banner, Card, Empty, Loading, PageHead, Stat, TierTag } from "@/components/ui";

function Detail({ vpa }: { vpa: string }) {
  const rep = useAppSelector((s) => s.live.reputation);
  const { data, loading, error, reload } = useApi<any>(`/v1/payees/${encodeURIComponent(vpa)}`, [vpa], 10000);
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [rep]);
  const router = useRouter();
  if (loading && !data) return <Loading rows={8} />;
  if (error || !data) return <Banner kind="err">{error}</Banner>;
  const r = data.reputation;
  const hist = (data.history || []).map((h: any) => ({ t: new Date(h.at).getTime(), score: h.trust_score, band: h.band, cause: h.cause }));
  return (
    <>
      <Card>
        <div className="row between top wrap">
          <div className="col gap-2">
            <div className="row gap-2 wrap"><BandTag band={r.band} />{data.on_denylist && <span className="chip t-BLOCK"><ShieldAlert size={13} /> On denylist</span>}
              <span className="chip">{data.kind}{data.verified ? " · verified" : ""}</span><span className="chip">{BANK[data.home_tenant]} account</span></div>
            <h1>{data.registered_name}</h1>
            <div className="mono muted">{data.vpa}</div>
            {r.band_reason && <div className="caption">Why: {({ reported: "confirmed fraud reports", reports: "reported by users", fan_in: "many new payers recently", near_miss: "payers cancelled after warnings", activity: "unusual recent activity" } as any)[r.band_reason] || r.band_reason}</div>}
          </div>
          <div className="col gap-1" style={{ textAlign: "right" }}>
            <div className="caption">TRUST SCORE (this bank&apos;s view)</div>
            <div className="num" style={{ fontSize: 40, lineHeight: "44px", fontWeight: 700 }}>{r.trust_score}<span className="faint" style={{ fontSize: 18 }}> / 100</span></div>
            <Link className="btn btn-ghost btn-sm" href={`/network?vpa=${encodeURIComponent(data.vpa)}`} style={{ alignSelf: "flex-end" }}><Network size={15} /> View network</Link>
          </div>
        </div>
      </Card>
      <div className="grid g4">
        <Stat label="UPI ID first seen" value={ago(data.first_seen)} />
        <Stat label="Payers, last hour / day" value={`${r.fan_in_1h} / ${r.fan_in_24h}`} sub={`${pct(r.first_time_payer_ratio_24h, 0)} first-time`} />
        <Stat label="Bad-event rate (smoothed)" value={pct(r.p_bad, 1)} sub={`${r.reports_30d} reports · ${r.confirmed_90d} confirmed · ${r.near_miss_14d} near-misses`} tone={r.p_bad >= 0.02 ? "var(--c-warning)" : undefined} />
        <Stat label="Received, last 30 days" value={inr(data.stats_30d?.amount)} sub={`${data.stats_30d?.payments ?? 0} payments from ${data.stats_30d?.payers ?? 0} of our customers`} />
      </div>
      <div className="grid g-main">
        <Card title="Reputation history">
          {hist.length < 2 ? <Empty>No changes recorded yet. Cancel a warned payment to this UPI ID, or confirm fraud on one, and watch this move.</Empty> : (
            <div style={{ height: 220 }}>
              <ResponsiveContainer>
                <AreaChart data={hist} margin={{ left: -20, right: 8, top: 8 }}>
                  <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#6D4AFF" stopOpacity={0.5} /><stop offset="1" stopColor="#6D4AFF" stopOpacity={0} /></linearGradient></defs>
                  <CartesianGrid stroke="#221F3A" vertical={false} />
                  <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(t) => clock(new Date(t).toISOString())} stroke="#6F6C8F" fontSize={11} />
                  <YAxis domain={[0, 100]} stroke="#6F6C8F" fontSize={11} />
                  <Tooltip contentStyle={{ background: "#16142B", border: "1px solid #34315A", borderRadius: 12 }} labelFormatter={(t) => clock(new Date(Number(t)).toISOString(), true)}
                    formatter={(v: any, _n: any, p: any) => [`${v} (${p.payload.band.toLowerCase().replace("_", " ")}${p.payload.cause ? ", " + p.payload.cause.replace("_", " ") : ""})`, "Trust score"]} />
                  <Area type="stepAfter" dataKey="score" stroke="#6D4AFF" strokeWidth={2} fill="url(#g)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
        <Card title="Signals">
          <dl className="kv">
            <dt>Network</dt><dd>{data.graph ? `${data.graph.downstream_hop < 9 ? `${data.graph.downstream_hop} step(s) from a reported account` : "nothing downstream"}${data.graph.shares_device_with_reported ? " · shares a device with a reported account" : ""}` : "No flags"}</dd>
            <dt>Forwards within 30 min</dt><dd>{data.graph ? pct(data.graph.passthrough_ratio, 0) : "0%"}</dd>
            <dt>Community risk</dt><dd>{data.graph ? pct(data.graph.community_risk_share, 0) + " of its community is risky" : "—"}</dd>
            <dt>Other banks</dt><dd>{data.consortium?.length ? data.consortium.map((c: any) => `${BANK[c.source_tenant]}: ${c.band.toLowerCase()}${c.confirmed ? ", confirmed fraud" : ""} (${ago(c.updated_at)})`).join("; ") : "No consortium signal"}</dd>
          </dl>
          {data.consortium?.length > 0 && <p className="caption" style={{ marginTop: 10 }}>Received as a keyed hash of the UPI ID with aggregate counters. No payer or transaction data crossed between banks.</p>}
        </Card>
      </div>
      <div className="grid g2">
        <Card flush title="Recent decisions">
          {!data.recent_decisions?.length ? <Empty>None from this bank&apos;s customers.</Empty> : (
            <table className="table"><tbody>{data.recent_decisions.map((d: any) => (
              <tr key={d.decision_id} className="click" onClick={() => router.push(`/decisions/${d.decision_id}`)}>
                <td className="nowrap">{ago(d.created_at)}</td><td className="num">{inr(d.amount)}</td><td><TierTag tier={d.tier} /></td><td className="muted">{d.user_action || "—"}</td>
              </tr>))}</tbody></table>)}
        </Card>
        <Card flush title="Evidence log">
          {!data.events?.length && !data.reports?.length ? <Empty>No reports, confirmations or near-misses.</Empty> : (
            <table className="table"><tbody>
              {data.events.map((e: any, i: number) => (<tr key={i}><td className="nowrap">{ago(e.at)}</td><td>{({ confirmed: "Confirmed fraud", report: "User report", nearmiss: "Near-miss", flagged_message: "Named in flagged messages" } as any)[e.kind] || e.kind}</td><td className="right num">+{Number(e.weight).toFixed(1)}</td></tr>))}
            </tbody></table>)}
        </Card>
      </div>
    </>
  );
}

function Payees() {
  const router = useRouter();
  const vpa = useSearchParams().get("vpa");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const list = useApi<any>(vpa ? null : `/v1/payees?q=${encodeURIComponent(query)}`, [query, vpa]);
  return (
    <>
      <PageHead title={vpa ? "Payee" : "Payees"} sub={vpa ? undefined : "Look up any UPI ID: its registered name, this bank's reputation view, and its network."}
        right={<form className="row gap-2" onSubmit={(e) => { e.preventDefault(); const v = q.trim().toLowerCase(); if (v.includes("@")) router.push(`/payees?vpa=${encodeURIComponent(v)}`); else setQuery(v); }}>
          <div style={{ position: "relative" }}><Search size={15} style={{ position: "absolute", left: 10, top: 12, color: "var(--c-text-muted)" }} />
            <input className="input" style={{ width: 300, paddingLeft: 32 }} placeholder="UPI ID or name" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <button className="btn btn-white btn-sm" type="submit">Search</button>
          {vpa && <button type="button" className="btn btn-ghost btn-sm" onClick={() => router.push("/payees")}>All payees</button>}
        </form>} />
      {vpa ? <Detail vpa={vpa} /> : (
        <Card flush>
          {list.loading && !list.data ? <div style={{ padding: 20 }}><Loading /></div> : !list.data?.payees?.length ? <Empty>No payees match.</Empty> : (
            <div className="scroll-x"><table className="table">
              <thead><tr><th>Payee</th><th>Kind</th><th>Reputation</th><th className="right">Trust</th><th>Updated</th></tr></thead>
              <tbody>{list.data.payees.map((p: any) => (
                <tr key={p.vpa} className="click" onClick={() => router.push(`/payees?vpa=${encodeURIComponent(p.vpa)}`)}>
                  <td><div>{p.registered_name}</div><div className="caption mono">{p.vpa}</div></td>
                  <td className="muted">{p.kind}{p.verified ? " · verified" : ""}</td><td><BandTag band={p.band} /></td>
                  <td className="right num">{p.trust_score}</td><td className="nowrap muted">{ago(p.updated_at)}</td>
                </tr>))}</tbody>
            </table></div>)}
        </Card>
      )}
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Payees /></Suspense>;
}
