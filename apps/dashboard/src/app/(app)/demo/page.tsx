"use client";

import { Activity, MessageSquare, PhoneCall, PhoneOff, Play, RotateCcw, TriangleAlert, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { BANK, inr } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import { bump, clearLive, useAppDispatch } from "@/lib/store";
import { Banner, Card, Loading, PageHead } from "@/components/ui";

export default function DemoControl() {
  const dispatch = useAppDispatch();
  const { data, loading, error } = useApi<any>("/v1/demo/scenarios");
  const metrics = useApi<any>("/v1/metrics/summary", [], 4000);
  const [user, setUser] = useState("");
  const [staged, setStaged] = useState<any>(null);
  const [note, setNote] = useState<string | null>(null);
  const act = useAction();
  useEffect(() => { if (data?.users?.length && !user) setUser(data.users[0].user_id); }, [data, user]);
  const chaos = metrics.data?.chaos || {};

  if (loading && !data) return <Loading rows={8} />;
  if (error) return <Banner kind="err">{error}</Banner>;
  const stage = (key: string) => act.run(() => api<any>("/v1/demo/scenario", { body: { scenario: key, user_id: user } })).then((r) => { if (r) { setStaged(r); setNote(null); } });
  const u = data.users.find((x: any) => x.user_id === user);

  return (
    <>
      <PageHead title="Demo control" sub="Stage a scenario for a demo user, then make the payment on their phone. This page exists only in demo mode and is not part of the product."
        right={<button className="btn btn-danger" disabled={act.busy} onClick={() => act.run(() => api<any>("/v1/demo/reset", { method: "POST" })).then((r) => { if (r) { dispatch(clearLive()); dispatch(bump()); setStaged(null); setNote(`Demo reset in ${r.seconds}s. The seeded state is restored.`); } })}>
          <RotateCcw size={16} /> Reset demo</button>} />
      {act.error && <Banner kind="err">{act.error}</Banner>}
      {note && <Banner kind="ok">{note}</Banner>}

      <Card title="1. Who is paying?">
        <div className="row wrap">
          {data.users.map((x: any) => (
            <button key={x.user_id} className={`btn ${user === x.user_id ? "btn-white" : "btn-ghost"}`} onClick={() => setUser(x.user_id)}>
              {x.display_name} <span className="caption" style={{ color: "inherit", opacity: 0.7 }}>{BANK[x.tenant_id]} · {x.locale === "hi" ? "Hindi" : "English"}</span>
            </button>
          ))}
        </div>
      </Card>

      <Card title="2. Stage a scenario">
        <div className="grid g3">
          {data.scenarios.map((s: any) => (
            <div key={s.key} className="card raised col gap-2" style={{ padding: 16 }}>
              <div className="row between"><strong>{s.title}</strong>
                <span className="row gap-2">{s.has_call && <span title="Stages a call"><PhoneCall size={15} color="var(--c-stepup)" /></span>}{s.has_message && <span title="Delivers a message"><MessageSquare size={15} color="var(--c-accent-cyan)" /></span>}</span></div>
              <div className="caption" style={{ fontWeight: 400 }}>Pay <span className="mono">{s.payee}</span> {inr(s.amount)}{s.claimed_name ? ` as “${s.claimed_name}”` : ""}</div>
              <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start", marginTop: 4 }} disabled={!user || act.busy} onClick={() => stage(s.key)}><Play size={14} /> Stage for {u?.display_name?.split(" ")[0] || "user"}</button>
            </div>
          ))}
        </div>
      </Card>

      {staged && (
        <Card title={`Staged: ${staged.scenario.replaceAll("_", " ")} for ${staged.user}`}>
          <div className="grid g2">
            <div className="col gap-2">
              <div className="caption">WHAT WAS SET UP</div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>{staged.staged.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
              {staged.message && <div className="banner info" style={{ marginTop: 6 }}><div className="caption" style={{ fontWeight: 400 }}>“{staged.message}”</div></div>}
            </div>
            <div className="col gap-2">
              <div className="caption">NOW, ON THE PHONE</div>
              <ol style={{ margin: 0, paddingLeft: 18 }}>{staged.steps.map((x: string, i: number) => <li key={i}>{x}</li>)}</ol>
              <dl className="kv" style={{ marginTop: 6 }}>
                <dt>UPI ID</dt><dd className="mono">{staged.suggested_payment.payee_vpa}</dd>
                <dt>Amount</dt><dd>{inr(staged.suggested_payment.amount)}</dd>
                {staged.suggested_payment.claimed_name && (<><dt>Name given</dt><dd>{staged.suggested_payment.claimed_name}</dd></>)}
                {staged.suggested_payment.note && (<><dt>Note</dt><dd>{staged.suggested_payment.note}</dd></>)}
              </dl>
            </div>
          </div>
        </Card>
      )}

      <div className="grid g2">
        <Card title="Individual triggers">
          <div className="col">
            <div className="row wrap">
              {[3, 2, 1, 0].map((r) => (
                <button key={r} className="btn btn-ghost btn-sm" disabled={!user || act.busy} onClick={() => act.run(() => api("/v1/demo/call", { body: { user_id: user, action: "start", caller_risk: r } })).then(() => setNote(`Incoming call sent to the phone (caller risk ${r}).`))}>
                  <PhoneCall size={14} /> Call, risk {r}</button>
              ))}
              <button className="btn btn-ghost btn-sm" disabled={!user || act.busy} onClick={() => act.run(() => api("/v1/demo/call", { body: { user_id: user, action: "end" } })).then(() => setNote("Call ended."))}><PhoneOff size={14} /> End call</button>
            </div>
            <div className="row wrap">
              {data.scenarios.filter((s: any) => s.has_message).map((s: any) => (
                <button key={s.key} className="btn btn-ghost btn-sm" disabled={!user || act.busy} onClick={() => act.run(() => api("/v1/demo/message", { body: { user_id: user, template: s.key } })).then(() => setNote("Message delivered to the phone."))}>
                  <MessageSquare size={14} /> {s.title} message</button>
              ))}
            </div>
          </div>
        </Card>
        <Card title="Traffic and failure tests">
          <div className="col">
            <div className="row wrap">
              <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(() => api<any>("/v1/demo/replay", { body: { rate_per_s: 5, duration_s: 30 } })).then((r) => r && setNote(`Replaying ${r.payments} simulated payments at ${r.rate_per_s} per second. Watch the live feed.`))}><Activity size={14} /> Replay traffic, 30 s</button>
              <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(() => api<any>("/v1/demo/drift", { body: { n: 400 } })).then((r) => r && setNote(`Sent ${r.sent} payments with shifted amounts and hours. Open Model health to see the drift alerts.`))}><Zap size={14} /> Inject shifted traffic</button>
            </div>
            <div className="row wrap">
              <button className={`btn btn-sm ${chaos.cache_down ? "btn-danger" : "btn-ghost"}`} disabled={act.busy} onClick={() => act.run(() => api("/v1/demo/chaos", { body: { cache_down: !chaos.cache_down } })).then(() => metrics.reload())}><TriangleAlert size={14} /> {chaos.cache_down ? "Restore cache" : "Fail the cache"}</button>
              <button className={`btn btn-sm ${chaos.model_down ? "btn-danger" : "btn-ghost"}`} disabled={act.busy} onClick={() => act.run(() => api("/v1/demo/chaos", { body: { model_down: !chaos.model_down } })).then(() => metrics.reload())}><TriangleAlert size={14} /> {chaos.model_down ? "Restore model" : "Fail the model"}</button>
            </div>
            <p className="caption">While a failure is active, decisions are marked degraded and capped at Warning. No payment is blocked because of the outage.</p>
          </div>
        </Card>
      </div>
    </>
  );
}
