"use client";

import { MessageSquareWarning, PhoneCall, Search, UserRoundCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ago, clock, FAMILY, inr, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { ActionTag, Card, Empty, Loading, PageHead, Segmented, Stat, TierTag } from "@/components/ui";

const TIERS = [
  { value: "", label: "All" }, { value: "WARNING,STEP_UP,BLOCK", label: "Flagged" }, { value: "WARNING", label: "Warning" },
  { value: "STEP_UP", label: "Step-Up" }, { value: "BLOCK", label: "Block" },
];

export default function Feed() {
  const router = useRouter();
  const tenant = useAppSelector((s) => s.session.tenant);
  const liveItems = useAppSelector((s) => s.live.items);
  const tick = useAppSelector((s) => s.live.tick);
  const connected = useAppSelector((s) => s.live.connected);
  const [tier, setTier] = useState("");
  const [scope, setScope] = useState<"live" | "all">("all");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");

  const params = new URLSearchParams({ limit: "80" });
  if (tier) params.set("tier", tier);
  if (scope === "live") params.set("live", "true");
  if (query) params.set("q", query);
  // refetch a moment after each live event (so outcomes and labels stay fresh), and poll if the socket is down
  const { data, loading, error, reload } = useApi<any>(`/v1/decisions?${params}`, [tier, scope, query], connected ? 0 : 5000);
  const summary = useApi<any>("/v1/metrics/summary", [], 5000);
  useEffect(() => {
    const t = setTimeout(reload, 600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  const fresh = useMemo(() => new Set(liveItems.filter((i) => i.tenant_id === tenant).slice(0, 5).map((i) => i.decision_id)), [liveItems, tenant]);
  const rows: any[] = data?.decisions || [];
  const hour = summary.data?.last_hour;
  const lat = summary.data?.latency_ms?.total;

  return (
    <>
      <PageHead title="Live feed" sub="Every payment decision as it happens. Rows marked Shadow were scored but not enforced." />
      <div className="grid g4">
        <Stat label="Decisions, last hour" value={hour ? Number(hour.n).toLocaleString("en-IN") : "—"} sub={`${summary.data?.rps_1m ?? 0} per second now`} />
        <Stat label="Flagged, last hour" value={hour && hour.n ? pct((hour.warning + hour.step_up + hour.block) / hour.n) : "—"}
          sub={hour ? `${hour.warning} warning · ${hour.step_up} step-up · ${hour.block} block` : ""} tone="var(--c-warning)" />
        <Stat label="Latency p95" value={lat ? `${lat.p95} ms` : "—"} sub="Budget: 150 ms" tone={lat && lat.p95 > 150 ? "var(--c-block)" : "var(--c-safe)"} />
        <Stat label="Degraded decisions" value={summary.data ? summary.data.degraded : "—"} sub={`Model ${summary.data?.active_model ?? "…"}`} />
      </div>

      <Card flush title="Decisions" right={
        <div className="row wrap">
          <form className="row gap-2" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
            <div style={{ position: "relative" }}>
              <Search size={15} style={{ position: "absolute", left: 10, top: 12, color: "var(--c-text-muted)" }} />
              <input className="input" style={{ width: 220, paddingLeft: 32 }} placeholder="Search UPI ID" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          </form>
          <Segmented value={scope} onChange={setScope} options={[{ value: "all", label: "All" }, { value: "live", label: "Live only" }]} />
          <Segmented value={tier} onChange={setTier} options={TIERS} />
        </div>
      }>
        {error && <div style={{ padding: 20 }} className="muted">{error}</div>}
        {loading && !data ? <div style={{ padding: 20 }}><Loading rows={8} /></div> : rows.length === 0 ? (
          <Empty>No decisions match. Make a payment in the app, or start a replay from Demo control.</Empty>
        ) : (
          <div className="scroll-x">
            <table className="table">
              <thead><tr><th>Time</th><th>Payer</th><th>Payee</th><th className="right">Amount</th><th>Tier</th><th>Action</th><th className="right">Score</th><th>Why</th><th>Signals</th></tr></thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.decision_id} className={`click ${fresh.has(d.decision_id) ? "fresh" : ""}`} onClick={() => router.push(`/decisions/${d.decision_id}`)}>
                    <td className="nowrap"><span className="num">{clock(d.created_at)}</span><div className="caption">{ago(d.created_at)}</div></td>
                    <td className="nowrap muted">{d.payer || "—"}</td>
                    <td><div>{d.payee_name || d.payee_vpa}</div><div className="caption mono">{d.payee_vpa}</div></td>
                    <td className="right num nowrap">{inr(d.amount)}</td>
                    <td><TierTag tier={d.tier} /></td>
                    <td className="nowrap"><ActionTag action={d.action} enforced={d.enforced} />{d.user_action && <div className="caption" style={{ marginTop: 3 }}>{d.is_near_miss ? "Near-miss" : ""}</div>}</td>
                    <td className="right num">{d.score.toFixed(2)}</td>
                    <td style={{ maxWidth: 340 }}>
                      {d.tier === "SAFE" ? <span className="faint">—</span> : (<>
                        <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={d.top_reason}>{d.top_reason}</div>
                        <div className="caption">{d.family_name || FAMILY[d.scam_family] || ""}</div>
                      </>)}
                    </td>
                    <td className="nowrap">
                      <span className="row gap-2">
                        {d.call_linked && <span title="A call is linked to this payment"><PhoneCall size={15} color="var(--c-stepup)" /></span>}
                        {d.message_linked && <span title="A flagged message is linked to this payment"><MessageSquareWarning size={15} color="var(--c-accent-cyan)" /></span>}
                        {d.has_guardian && <span title="A guardian was involved"><UserRoundCheck size={15} color="#a995ff" /></span>}
                        {d.label && <span className={`chip ${d.label === "FRAUD" ? "t-BLOCK" : "t-SAFE"}`}>{d.label === "FRAUD" ? "Fraud" : "Legit"}</span>}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
