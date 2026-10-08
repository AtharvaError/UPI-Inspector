"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ago, inr } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { Banner, Card, Empty, Loading, PageHead, Segmented, TierTag } from "@/components/ui";

export default function Reviews() {
  const router = useRouter();
  const role = useAppSelector((s) => s.session.staff?.role);
  const tick = useAppSelector((s) => s.live.tick);
  const [status, setStatus] = useState("OPEN");
  const { data, loading, reload } = useApi<any>(`/v1/reviews?status=${status}`, [status], 10000);
  const act = useAction();
  const canWrite = role === "admin" || role === "analyst";
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [tick]);
  const resolve = (id: string, resolution: string) => act.run(() => api(`/v1/reviews/${id}/resolve`, { body: { resolution } })).then(reload);

  return (
    <>
      <PageHead title="Review queue" sub="Payers who say a stop was wrong, and fraud reports waiting for a decision. Overturning clears the 24-hour stop; confirming fraud lowers the payee's reputation at once."
        right={<Segmented value={status} onChange={setStatus} options={[{ value: "OPEN", label: "Open" }, { value: "ALL", label: "All" }]} />} />
      {act.error && <Banner kind="err">{act.error}</Banner>}
      <Card flush>
        {loading && !data ? <div style={{ padding: 20 }}><Loading /></div> : !data?.reviews?.length ? <Empty>Nothing waiting. A payer can ask for a review from the “Payment stopped” screen.</Empty> : (
          <div className="scroll-x"><table className="table">
            <thead><tr><th>Received</th><th>Kind</th><th>From</th><th>Payee</th><th className="right">Amount</th><th>Decision</th><th>Comment</th><th>Status</th><th></th></tr></thead>
            <tbody>{data.reviews.map((r: any) => (
              <tr key={r.report_id}>
                <td className="nowrap">{ago(r.created_at)}</td>
                <td><span className={`chip ${r.kind === "FRAUD" ? "t-BLOCK" : "t-WARNING"}`}>{r.kind === "FRAUD" ? "Fraud report" : "Says not a scam"}</span></td>
                <td className="muted nowrap">{r.reporter_name || "—"}</td>
                <td className="mono caption">{r.payee_vpa || "—"}</td>
                <td className="right num">{r.amount != null ? inr(r.amount) : "—"}</td>
                <td>{r.tier ? <TierTag tier={r.tier} /> : "—"}</td>
                <td style={{ maxWidth: 260 }} className="muted">{r.comment || "—"}</td>
                <td><span className="chip">{r.status.toLowerCase()}</span></td>
                <td className="nowrap right">
                  <span className="row gap-2" style={{ justifyContent: "flex-end" }}>
                    {r.decision_id && <button className="btn btn-ghost btn-sm" onClick={() => router.push(`/decisions/${r.decision_id}`)}>Open</button>}
                    {canWrite && r.status === "OPEN" && (r.kind === "FALSE_POSITIVE" ? (<>
                      <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => resolve(r.report_id, "UPHELD")}>Uphold stop</button>
                      <button className="btn btn-white btn-sm" disabled={act.busy} onClick={() => resolve(r.report_id, "OVERTURNED")}>Overturn</button>
                    </>) : (<>
                      <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => resolve(r.report_id, "REJECTED")}>Reject</button>
                      <button className="btn btn-danger btn-sm" disabled={act.busy} onClick={() => resolve(r.report_id, "CONFIRMED")}>Confirm fraud</button>
                    </>))}
                  </span>
                </td>
              </tr>))}</tbody>
          </table></div>)}
      </Card>
    </>
  );
}
