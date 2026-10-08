"use client";

import { ArrowLeft, Bot, CheckCircle2, FileText, MessageSquareWarning, PhoneCall, RefreshCw, ShieldAlert, Wallet } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, openProtected } from "@/lib/api";
import { BANK, clock, FAMILY, inr, OUTCOME_LABEL, RISK_WORD } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { ActionTag, BandTag, Banner, Card, Loading, Modal, TierTag } from "@/components/ui";

const GROUP_LABEL: Record<string, string> = {
  payee: "Payee reputation", network: "Payee network", call: "Call context", message: "Flagged message", amount: "Amount",
  relationship: "Payer and payee history", device: "Device", session: "Session (coaching signals)", name: "Name match", note: "Payment note", time: "Time of day",
};

export default function DecisionDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const role = useAppSelector((s) => s.session.staff?.role);
  const tick = useAppSelector((s) => s.live.tick);
  const { data, loading, error, reload } = useApi<any>(`/v1/decisions/${id}`, [id]);
  const act = useAction();
  const [labelling, setLabelling] = useState<null | "FRAUD" | "LEGITIMATE">(null);
  const [family, setFamily] = useState("");
  const [note, setNote] = useState("");
  const canWrite = role === "admin" || role === "analyst";

  useEffect(() => {
    const t = setTimeout(reload, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  if (loading && !data) return <Loading rows={10} />;
  if (error || !data) return <Banner kind="err">{error || "Not found"}</Banner>;
  const d = data.decision;
  const f = data.features || {};
  const contrib = Object.entries(d.contributions || {}).map(([k, v]) => [k, Number(v)] as [string, number]).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const maxC = Math.max(0.01, ...contrib.map(([, v]) => Math.abs(v)));
  const rep = data.payee?.reputation;
  const g = data.payee?.graph;

  const submitLabel = async () => {
    const ok = await act.run(() => api(`/v1/decisions/${id}/label`, { body: { label: labelling, family: family || null, note: note || null } }));
    if (ok) {
      setLabelling(null);
      reload();
    }
  };

  return (
    <>
      <div className="row between wrap">
        <button className="btn btn-ghost btn-sm" onClick={() => router.back()}><ArrowLeft size={15} /> Back</button>
        {canWrite && (
          <div className="row wrap">
            <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(async () => {
              const r = await api<any>("/v1/complaint-packs", { body: { decision_id: id } });
              await openProtected(r.pdf_url);
            })}><FileText size={15} /> Complaint pack</button>
            <button className="btn btn-ghost btn-sm" onClick={() => { setLabelling("LEGITIMATE"); setFamily(""); }}><CheckCircle2 size={15} /> Mark false positive</button>
            <button className="btn btn-danger btn-sm" onClick={() => { setLabelling("FRAUD"); setFamily(d.scam_family && d.scam_family !== "UNCLASSIFIED" ? d.scam_family : ""); }}><ShieldAlert size={15} /> Confirm fraud</button>
          </div>
        )}
      </div>
      {act.error && <Banner kind="err">{act.error}</Banner>}

      <Card>
        <div className="row between top wrap">
          <div className="col gap-2">
            <div className="row wrap gap-2">
              <TierTag tier={d.tier} /> <ActionTag action={d.action} enforced={d.enforced} />
              {d.degraded && <span className="chip t-WARNING">Degraded</span>}
              {data.label && <span className={`chip ${data.label.label === "FRAUD" ? "t-BLOCK" : "t-SAFE"}`}>Labelled {data.label.label === "FRAUD" ? "fraud" : "legitimate"}{data.label.by_name ? ` by ${data.label.by_name}` : ""}</span>}
              {data.transaction?.truth_is_fraud != null && <span className="chip" title="Ground truth from the simulator">Simulator truth: {data.transaction.truth_is_fraud ? `scam (${FAMILY[data.transaction.truth_family] || "?"})` : "legitimate"}</span>}
            </div>
            <div style={{ fontSize: 34, lineHeight: "40px", fontWeight: 700 }} className="num">{inr(d.amount)}</div>
            <div className="muted">{data.payer.name} <span className="mono">({data.payer.vpa})</span> → <strong style={{ color: "var(--c-text)" }}>{data.payee.registered_name || d.payee_vpa}</strong> <span className="mono">({d.payee_vpa})</span></div>
            {data.payee.claimed_name && <div className="caption">Name the payer was given: “{data.payee.claimed_name}”</div>}
          </div>
          <div className="col gap-1" style={{ textAlign: "right" }}>
            <div className="caption">SCORE</div>
            <div style={{ fontSize: 34, lineHeight: "40px", fontWeight: 700, color: `var(--c-${d.tier === "SAFE" ? "safe" : d.tier === "WARNING" ? "warning" : d.tier === "STEP_UP" ? "stepup" : "block"})` }} className="num">{d.score.toFixed(2)}</div>
            <div className="caption">{clock(d.created_at, true)} · {d.latency_ms} ms</div>
            <div className="caption mono">{d.model_version} · {d.rules_version}</div>
          </div>
        </div>
      </Card>

      <div className="grid g-main">
        <div className="col gap-4">
          {(data.summary || d.tier === "STEP_UP" || d.tier === "BLOCK") && (
            <Card title={<span className="row gap-2"><Bot size={17} color="#a995ff" /> Case summary</span>} right={
              <div className="row gap-2">
                {data.summary && <span className="chip brand">AI-generated · {data.summary.provider}</span>}
                {canWrite && <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(() => api(`/v1/decisions/${id}/summary/regenerate`, { method: "POST" })).then(reload)}><RefreshCw size={14} /> Regenerate</button>}
              </div>
            }>
              {data.summary ? (
                <div className="col gap-2">
                  <p>{data.summary.summary}</p>
                  {data.summary.key_evidence?.length > 0 && <ul style={{ margin: 0, paddingLeft: 18 }} className="muted">{data.summary.key_evidence.map((e: string, i: number) => <li key={i}>{e}</li>)}</ul>}
                  {data.summary.suggested_action && <div className="caption">{data.summary.suggested_action}</div>}
                  <div className="caption faint">Written after the decision from the facts on this page. It did not influence the decision.</div>
                </div>
              ) : <p className="muted">Not generated yet.</p>}
            </Card>
          )}

          <Card title="Reasons shown to the payer">
            {d.reasons?.length ? (
              <div className="col gap-2">
                {data.family_name && <div className="row gap-2"><span className="chip brand">{data.family_name}</span>{d.family_sim != null && <span className="caption">similarity {Number(d.family_sim).toFixed(2)}</span>}</div>}
                {(data.reasons_en || d.reasons).map((r: any, i: number) => (
                  <div key={i} className="row top gap-2">
                    <span className="chip" style={{ flex: "0 0 auto" }}>{r.code}</span>
                    <div><div>{r.message}</div>{data.payer.locale !== "en" && d.reasons[i] && <div className="caption">{d.reasons[i].message}</div>}</div>
                  </div>
                ))}
              </div>
            ) : <p className="muted">No reasons: a Safe decision is not explained to the payer.</p>}
          </Card>

          <Card title="Why this score">
            <div className="col gap-4">
              <div className="row wrap gap-4 caption">
                {Object.entries(data.score_parts || {}).filter(([, v]) => v != null).map(([k, v]) => (
                  <span key={k}><span className="faint">{({ p_xgb: "XGBoost", p_rf: "Random Forest", s_anom: "Anomaly", s_rule: "Rule score", raw: "Stacked" } as any)[k]}</span> <strong className="num" style={{ color: "var(--c-text)" }}>{Number(v).toFixed(3)}</strong></span>
                ))}
              </div>
              {contrib.length > 0 ? (
                <div className="col gap-2">
                  <div className="caption">MODEL CONTRIBUTIONS BY SIGNAL GROUP (log-odds; right raises risk)</div>
                  {contrib.map(([k, v]) => (
                    <div key={k} className="row gap-2">
                      <div style={{ flex: "0 0 200px" }} className="caption">{GROUP_LABEL[k] || k}</div>
                      <div className="grow" style={{ position: "relative", height: 14 }}>
                        <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "var(--c-border-strong)" }} />
                        <div style={{ position: "absolute", top: 2, height: 10, borderRadius: 999, background: v >= 0 ? "var(--c-block)" : "var(--c-safe)",
                          left: v >= 0 ? "50%" : `${50 - (Math.abs(v) / maxC) * 50}%`, width: `${(Math.abs(v) / maxC) * 50}%` }} />
                      </div>
                      <div style={{ flex: "0 0 60px" }} className="right num caption">{v >= 0 ? "+" : ""}{v.toFixed(2)}</div>
                    </div>
                  ))}
                </div>
              ) : <p className="muted">Contributions are computed only for decisions at Warning or above.</p>}
              <div>
                <div className="caption" style={{ marginBottom: 6 }}>RULES FIRED</div>
                {data.rule_effects?.length ? (
                  <div className="row wrap gap-2">
                    {data.rule_effects.map((r: any) => (
                      <span key={r.id} className="chip">{r.id}{r.floor != null ? ` · floor ${r.floor}` : ""}{r.cap != null ? ` · cap ${r.cap}` : ""}{r.bump ? ` · +${r.bump}` : ""}</span>
                    ))}
                    {d.rules_fired?.includes("PAIR_BLOCK") && <span className="chip t-BLOCK">Stopped earlier for this payer and payee</span>}
                  </div>
                ) : <span className="muted">None</span>}
              </div>
            </div>
          </Card>

          <Card title="Session and payment details">
            <dl className="kv">
              <dt>UPI ID entered by</dt><dd>{f.vpa_entry_method}</dd>
              <dt>App switches</dt><dd>{f.app_switch_count}</dd>
              <dt>Time on pay screen</dt><dd>{Math.round(f.time_on_pay_screen_s)} s</dd>
              <dt>Amount edits</dt><dd>{f.amount_edit_count}</dd>
              <dt>Payment note</dt><dd>{data.transaction?.note || "—"}{f.note_scam_prob > 0.3 && <span className="chip t-WARNING" style={{ marginLeft: 8 }}>scam wording {Math.round(f.note_scam_prob * 100)}%</span>}</dd>
              <dt>Name match</dt><dd>{data.payee.claimed_name ? `${Math.round((f.name_match_score ?? 1) * 100)}%${f.claimed_name_institutional ? " · institutional name on " + (f.payee_is_individual ? "a personal account" : "a merchant account") : ""}` : "No name was given"}</dd>
              <dt>New payee for payer</dt><dd>{f.is_new_payee ? "Yes" : `No, paid ${f.prior_txn_count} times before`}</dd>
              <dt>Amount vs payer&apos;s p95</dt><dd>{Number(f.amount_over_p95).toFixed(1)}×</dd>
              <dt>Device</dt><dd>{f.device_is_new ? "New device" : "Known device"}</dd>
            </dl>
          </Card>
        </div>

        <div className="col gap-4">
          <Card title="Timeline" right={<span className="caption">One connected risk event</span>}>
            <div className="timeline">
              {data.timeline.map((e: any, i: number) => (
                <div key={i} className={`ev ${e.type} ${e.linked ? "linked" : ""}`}>
                  <div className="caption num">{clock(e.at, true)}</div>
                  {e.type === "call" && (<div className="t"><PhoneCall size={14} style={{ verticalAlign: -2 }} /> Call from {e.caller_known ? "a known contact" : "an unknown number"} · {RISK_WORD[e.caller_risk]} {e.ended_at ? "" : "· still active"} {e.linked && <span className="chip t-STEP_UP" style={{ marginLeft: 6 }}>linked</span>}</div>)}
                  {e.type === "message" && (<div className="t"><MessageSquareWarning size={14} style={{ verticalAlign: -2 }} /> Message checked: {e.verdict.toLowerCase()} ({e.label.replaceAll("_", " ")}) {e.linked && <span className="chip cyan" style={{ marginLeft: 6 }}>linked</span>}<div className="caption" style={{ marginTop: 4, fontWeight: 400 }}>“{e.text}”</div></div>)}
                  {e.type === "payment" && (<div className="t"><Wallet size={14} style={{ verticalAlign: -2 }} /> Payment of {inr(e.amount)} assessed: <TierTag tier={e.tier} /></div>)}
                  {e.type === "outcome" && (<div className="t">Outcome: {OUTCOME_LABEL[e.user_action] || e.user_action}{e.final_status ? ` · ${e.final_status.toLowerCase().replaceAll("_", " ")}` : ""}</div>)}
                </div>
              ))}
            </div>
          </Card>

          <Card title="Payee" right={<Link href={`/payees?vpa=${encodeURIComponent(d.payee_vpa)}`} className="btn btn-ghost btn-sm">Open payee</Link>}>
            <div className="col">
              <div className="row gap-2 wrap"><BandTag band={rep?.band || data.payee.band || "NO_HISTORY"} /><span className="caption">Trust score {rep?.trust_score ?? data.payee.trust_score ?? "—"} / 100</span><span className="caption">{BANK[d.payee_vpa.endsWith("@banka") ? "bank_a" : "bank_b"]} account</span></div>
              <dl className="kv">
                <dt>Registered name</dt><dd>{data.payee.registered_name || "—"} <span className="caption">({data.payee.kind})</span></dd>
                <dt>UPI ID age</dt><dd>{Math.round(f.vpa_age_days)} days</dd>
                <dt>Payers, last hour / day</dt><dd>{f.fan_in_1h} / {f.fan_in_24h} <span className="caption">({Math.round((f.first_time_payer_ratio_24h ?? 0) * 100)}% first-time)</span></dd>
                <dt>Reports / confirmed / near-misses</dt><dd>{f.reports_30d} / {f.confirmed_90d} / {f.near_miss_14d}</dd>
                <dt>Network</dt><dd>{g ? `${g.downstream_hop < 9 ? `${g.downstream_hop} step(s) from a reported account` : "no reported account downstream"}${g.shares_device_with_reported ? " · shares a device with a reported account" : ""} · forwards ${Math.round(g.passthrough_ratio * 100)}% of receipts` : "No network flags"}</dd>
                <dt>Other banks</dt><dd>{f.consortium_reported ? `Reported at ${BANK[data.payee.consortium_source] || "another bank"}` : "No signal"}</dd>
              </dl>
              <Link href={`/network?vpa=${encodeURIComponent(d.payee_vpa)}`}>View in network →</Link>
            </div>
          </Card>

          <Card title="What happened next">
            <dl className="kv">
              <dt>Outcome</dt><dd>{data.outcome ? `${OUTCOME_LABEL[data.outcome.user_action] || data.outcome.user_action}${data.outcome.is_near_miss ? " · near-miss" : ""}` : "Not recorded yet"}</dd>
              {data.step_up && (<><dt>Step-up</dt><dd>{data.step_up.status.toLowerCase()}{data.step_up.auth_method ? ` · ${data.step_up.auth_method}` : ""}</dd></>)}
              {data.guardian && (<><dt>Guardian</dt><dd>{data.guardian.kind === "APPROVAL" ? "Approval" : "Alert"} · {data.guardian.status.toLowerCase()}</dd></>)}
              {data.hold && (<><dt>Hold</dt><dd>{data.hold.status.toLowerCase()} · ready at {clock(data.hold.release_at)}</dd></>)}
              {data.label?.note && (<><dt>Analyst note</dt><dd>{data.label.note}</dd></>)}
            </dl>
          </Card>
        </div>
      </div>

      {labelling && (
        <Modal title={labelling === "FRAUD" ? "Confirm fraud" : "Mark as false positive"} onClose={() => setLabelling(null)} footer={<>
          <button className="btn btn-ghost" onClick={() => setLabelling(null)}>Cancel</button>
          <button className={`btn ${labelling === "FRAUD" ? "btn-danger" : "btn-primary"}`} disabled={act.busy} onClick={submitLabel}>{act.busy ? "Saving…" : "Save label"}</button>
        </>}>
          <p className="muted">{labelling === "FRAUD"
            ? "This raises the payee's bad-event count by a confirmed fraud, updates its reputation within seconds, and becomes a training label."
            : "This records the payment as legitimate, clears the 24-hour stop for this payer and payee, and becomes a training label."}</p>
          {labelling === "FRAUD" && (
            <label className="field">Scam family
              <select className="input" value={family} onChange={(e) => setFamily(e.target.value)}>
                <option value="">Not sure</option>
                {Object.entries(FAMILY).filter(([k]) => k !== "UNCLASSIFIED").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
          )}
          <label className="field">Note (optional)<textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></label>
          {act.error && <Banner kind="err">{act.error}</Banner>}
        </Modal>
      )}
    </>
  );
}
