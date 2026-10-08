"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ago, FAMILY, inr, OUTCOME_LABEL } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { BandTag, Card, Empty, Loading, PageHead, Segmented, TierTag } from "@/components/ui";

export default function NearMisses() {
  const router = useRouter();
  const [view, setView] = useState<"list" | "payee">("list");
  const tick = useAppSelector((s) => s.live.tick);
  const { data, loading, reload } = useApi<any>(`/v1/near-misses${view === "payee" ? "?group_by=payee" : ""}`, [view], 8000);
  useEffect(() => {
    const t = setTimeout(reload, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  return (
    <>
      <PageHead title="Near-misses" sub="Payments the payer abandoned after being warned. Each one is weak evidence against the payee and lowers its reputation within seconds."
        right={<Segmented value={view} onChange={setView} options={[{ value: "list", label: "Latest" }, { value: "payee", label: "By payee" }]} />} />
      <Card flush>
        {loading && !data ? <div style={{ padding: 20 }}><Loading /></div> : view === "list" ? (
          !data?.near_misses?.length ? <Empty>No near-misses yet. Cancel a warned payment in the app to see one appear here.</Empty> : (
            <div className="scroll-x"><table className="table">
              <thead><tr><th>When</th><th>Payee</th><th className="right">Amount</th><th>Tier</th><th>How it ended</th><th>Pattern</th><th className="right">Weight</th></tr></thead>
              <tbody>{data.near_misses.map((n: any) => (
                <tr key={n.decision_id} className="click" onClick={() => router.push(`/decisions/${n.decision_id}`)}>
                  <td className="nowrap">{ago(n.created_at)}</td>
                  <td><div>{n.payee_name || n.payee_vpa}</div><div className="caption mono">{n.payee_vpa}</div></td>
                  <td className="right num">{inr(n.amount)}</td>
                  <td><TierTag tier={n.tier} /></td>
                  <td>{OUTCOME_LABEL[n.user_action] || n.user_action}</td>
                  <td className="muted">{n.family_name || FAMILY[n.scam_family] || "—"}</td>
                  <td className="right num">{Number(n.near_miss_weight).toFixed(1)}</td>
                </tr>))}</tbody>
            </table></div>)
        ) : (
          !data?.payees?.length ? <Empty>No near-misses yet.</Empty> : (
            <div className="scroll-x"><table className="table">
              <thead><tr><th>Payee</th><th className="right">Near-misses</th><th className="right">Amount not sent</th><th>Reputation</th><th>Last</th><th></th></tr></thead>
              <tbody>{data.payees.map((p: any) => (
                <tr key={p.payee_vpa}>
                  <td><div>{p.payee_name || p.payee_vpa}</div><div className="caption mono">{p.payee_vpa}</div></td>
                  <td className="right num">{p.near_misses}</td>
                  <td className="right num">{inr(p.amount)}</td>
                  <td>{p.band ? <span className="row gap-2"><BandTag band={p.band} /><span className="caption">{p.trust_score}/100</span></span> : "—"}</td>
                  <td className="nowrap">{ago(p.last_at)}</td>
                  <td className="right"><Link href={`/payees?vpa=${encodeURIComponent(p.payee_vpa)}`}>Open payee</Link></td>
                </tr>))}</tbody>
            </table></div>)
        )}
      </Card>
    </>
  );
}
