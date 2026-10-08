"use client";

import { FlaskConical, RotateCcw, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import { inr, num, pct, inrCompact } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { Banner, Card, Loading, Modal, PageHead, Segmented, Stat } from "@/components/ui";

const WINDOWS = [{ value: "24", label: "24 hours" }, { value: "168", label: "7 days" }, { value: "360", label: "15 days" }];
const DEFAULTS = { t_warning: 0.3, t_stepup: 0.6, t_block: 0.85 };
const tip = { background: "#16142B", border: "1px solid #34315A", borderRadius: 12, fontSize: 12 };

function Slider({ label, value, onChange, color, min, max }: { label: string; value: number; onChange: (v: number) => void; color: string; min: number; max: number }) {
  return (
    <label className="field">
      <span className="row between"><span style={{ color }}>{label}</span>
        <input className="input num" style={{ width: 84, height: 32, textAlign: "right" }} type="number" step={0.01} min={min} max={max} value={value}
          onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value))))} /></span>
      <input type="range" min={0.01} max={0.94} step={0.01} value={value} onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value))))} style={{ accentColor: color }} />
    </label>
  );
}

export default function PolicyLab() {
  const role = useAppSelector((s) => s.session.staff?.role);
  const isAdmin = role === "admin";
  const policy = useApi<any>("/v1/policy");
  const [t, setT] = useState(DEFAULTS);
  const [hours, setHours] = useState("360");
  const [rate, setRate] = useState(0.05);
  const [sim, setSim] = useState<any>(null);
  const [simErr, setSimErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const act = useAction();
  const seq = useRef(0);

  useEffect(() => {
    if (policy.data) setT({ t_warning: policy.data.t_warning, t_stepup: policy.data.t_stepup, t_block: policy.data.t_block });
  }, [policy.data]);

  useEffect(() => {
    if (!(t.t_warning < t.t_stepup && t.t_stepup < t.t_block)) return;
    const my = ++seq.current;
    const h = setTimeout(() => {
      api<any>("/v1/policy/simulate", { body: { ...t, hours: Number(hours), fraud_rate: rate / 100 } })
        .then((d) => { if (my === seq.current) { setSim(d); setSimErr(null); } })
        .catch((e) => { if (my === seq.current) setSimErr(e.message); });
    }, 140);
    return () => clearTimeout(h);
  }, [t, hours, rate]);

  if (policy.loading && !policy.data) return <Loading rows={8} />;
  const cur = policy.data;
  const valid = t.t_warning < t.t_stepup && t.t_stepup < t.t_block && t.t_block < (cur?.t_decline ?? 0.95);
  const changed = cur && (t.t_warning !== cur.t_warning || t.t_stepup !== cur.t_stepup || t.t_block !== cur.t_block);
  const p = sim?.proposed, c = sim?.current;
  const rowsCmp: [string, (m: any) => string][] = [
    ["Scams caught at Step-Up or Block", (m) => pct(m.scams_caught)],
    ["Scams only warned", (m) => pct(m.scams_warned_only)],
    ["Scams missed", (m) => pct(m.scams_missed)],
    ["Legitimate payments warned", (m) => pct(m.legit_warning, 2)],
    ["Legitimate payments at Step-Up", (m) => pct(m.legit_stepup, 2)],
    ["Legitimate payments blocked", (m) => pct(m.legit_block, 2)],
    ["Interruptions per 1,000 payments", (m) => m.interruptions_per_1000.toFixed(1)],
    ["Rupees of fraud stopped", (m) => inr(m.rupees_protected)],
    ["Precision at Step-Up or above", (m) => pct(m.precision_stepup_plus, 1)],
  ];

  return (
    <>
      <PageHead title="Policy lab" sub="See what the system would have done at any thresholds before changing anything. Results come from stored decisions with known outcomes; rule floors and caps are already inside each score."
        right={<>
          <Segmented value={cur?.mode || "enforce"} disabled={!isAdmin || act.busy}
            onChange={(m) => act.run(() => api("/v1/policy/mode", { method: "PUT", body: { mode: m } })).then(() => policy.reload())}
            options={[{ value: "enforce", label: "Enforce" }, { value: "shadow", label: "Shadow" }]} />
        </>} />
      {cur?.mode === "shadow" && <Banner kind="warn"><strong>Shadow mode.</strong> Every payment is scored and stored, but nothing is enforced and payers see nothing. Use this to watch before switching on.</Banner>}
      {!isAdmin && <Banner>You can simulate. Only an admin can apply thresholds or switch between enforce and shadow.</Banner>}
      {act.error && <Banner kind="err">{act.error}</Banner>}
      {simErr && <Banner kind="warn">{simErr}</Banner>}

      <div className="grid g-main" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 2fr)" }}>
        <Card title="Thresholds" right={<Segmented value={hours} onChange={setHours} options={WINDOWS} />}>
          <div className="col gap-4">
            <Slider label="Warning from" value={t.t_warning} color="var(--c-warning)" min={0.01} max={t.t_stepup - 0.01} onChange={(v) => setT({ ...t, t_warning: v })} />
            <Slider label="Step-Up from" value={t.t_stepup} color="var(--c-stepup)" min={t.t_warning + 0.01} max={t.t_block - 0.01} onChange={(v) => setT({ ...t, t_stepup: v })} />
            <Slider label="Block from" value={t.t_block} color="var(--c-block)" min={t.t_stepup + 0.01} max={0.94} onChange={(v) => setT({ ...t, t_block: v })} />
            <label className="field">Real-world fraud rate used for precision and interruptions (%)
              <input className="input num" type="number" step={0.01} min={0.001} max={10} value={rate} onChange={(e) => setRate(Math.max(0.001, Number(e.target.value)))} />
            </label>
            <div className="caption">Hard decline stays at {cur?.t_decline} and above, or when a hard rule fires. Hold length now: {cur?.effective?.hold_seconds}s{cur?.demo_mode ? " (demo timing)" : ""}.</div>
            <div className="row wrap">
              <button className="btn btn-primary" disabled={!isAdmin || !valid || !changed || act.busy} onClick={() => setConfirm(true)}><ShieldCheck size={16} /> Apply</button>
              <button className="btn btn-ghost" onClick={() => setT(DEFAULTS)}><RotateCcw size={15} /> Deck defaults</button>
              {cur && changed && <button className="btn btn-ghost" onClick={() => setT({ t_warning: cur.t_warning, t_stepup: cur.t_stepup, t_block: cur.t_block })}>Revert</button>}
            </div>
            {!valid && <Banner kind="err">Thresholds must rise: warning &lt; step-up &lt; block &lt; {cur?.t_decline}.</Banner>}
          </div>
        </Card>

        <div className="col gap-4">
          <div className="grid g4">
            <Stat label="Scams caught" value={p ? pct(p.scams_caught) : "—"} sub={c ? `now ${pct(c.scams_caught)}` : ""} tone="var(--c-safe)" />
            <Stat label="Legit at Step-Up or Block" value={p ? pct(p.legit_stepup + p.legit_block, 2) : "—"} sub={c ? `now ${pct(c.legit_stepup + c.legit_block, 2)}` : ""} tone="var(--c-stepup)" />
            <Stat label="Interruptions / 1,000" value={p ? p.interruptions_per_1000.toFixed(1) : "—"} sub={c ? `now ${c.interruptions_per_1000.toFixed(1)}` : ""} />
            <Stat label="Fraud stopped" value={p ? inrCompact(p.rupees_protected) : "—"} sub={p ? `of ${inrCompact(p.rupees_at_risk)} attempted` : ""} tone="var(--c-brand-hover)" />
          </div>
          <Card title="Scams caught against payers interrupted" right={sim && <span className="caption">{num(sim.n_decisions)} decisions · {num(sim.n_fraud)} scams</span>}>
            {!sim ? <Loading /> : (
              <div style={{ height: 250 }}>
                <ResponsiveContainer>
                  <LineChart data={sim.curve.map((x: any) => ({ x: x.legit_interrupted * 100, y: x.scams_caught * 100, t: x.t_stepup }))} margin={{ left: -10, right: 16, top: 10, bottom: 14 }}>
                    <CartesianGrid stroke="#221F3A" />
                    <XAxis dataKey="x" type="number" domain={[0, "dataMax"]} stroke="#6F6C8F" fontSize={11} tickFormatter={(v) => `${Number(v).toFixed(1)}%`}
                      label={{ value: "Legitimate payments interrupted", position: "insideBottom", offset: -8, fill: "#6F6C8F", fontSize: 11 }} />
                    <YAxis domain={[0, 100]} stroke="#6F6C8F" fontSize={11} tickFormatter={(v) => `${v}%`} />
                    <Tooltip contentStyle={tip} formatter={(v: any) => `${Number(v).toFixed(1)}%`} labelFormatter={(v, pl: any) => `Step-Up at ${pl?.[0]?.payload?.t ?? ""} · ${Number(v).toFixed(2)}% interrupted`} />
                    <Line dataKey="y" name="Scams caught" stroke="#6D4AFF" strokeWidth={2.5} dot={{ r: 2.5 }} isAnimationActive={false} />
                    {c && <ReferenceDot x={(c.legit_stepup + c.legit_block) * 100} y={c.scams_caught * 100} r={7} fill="#A7A4C2" stroke="#fff" label={{ value: "Current", fill: "#A7A4C2", fontSize: 11, position: "right" }} />}
                    {p && <ReferenceDot x={(p.legit_stepup + p.legit_block) * 100} y={p.scams_caught * 100} r={7} fill="#38D6FF" stroke="#fff" label={{ value: "Proposed", fill: "#38D6FF", fontSize: 11, position: "left" }} />}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </Card>
          <div className="grid g2">
            <Card title="Score distribution">
              {!sim ? <Loading /> : (
                <div style={{ height: 210 }}>
                  <ResponsiveContainer>
                    <BarChart data={sim.histogram.map((h: any) => ({ x: h.lo.toFixed(2), Legitimate: h.legit, Scam: h.fraud }))} margin={{ left: -14, right: 8, top: 8 }}>
                      <CartesianGrid stroke="#221F3A" vertical={false} />
                      <XAxis dataKey="x" stroke="#6F6C8F" fontSize={10} interval={3} />
                      <YAxis scale="sqrt" stroke="#6F6C8F" fontSize={10} />
                      <Tooltip contentStyle={tip} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
                      <Bar dataKey="Legitimate" stackId="a" fill="#34D399" isAnimationActive={false} />
                      <Bar dataKey="Scam" stackId="a" fill="#F43F5E" isAnimationActive={false} />
                      <ReferenceLine x={(Math.floor(t.t_warning * 20) / 20).toFixed(2)} stroke="#FBBF24" strokeDasharray="4 3" />
                      <ReferenceLine x={(Math.floor(t.t_stepup * 20) / 20).toFixed(2)} stroke="#FB923C" strokeDasharray="4 3" />
                      <ReferenceLine x={(Math.floor(t.t_block * 20) / 20).toFixed(2)} stroke="#F43F5E" strokeDasharray="4 3" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
              <p className="caption faint">Square-root scale, so the few flagged payments are visible beside the many safe ones.</p>
            </Card>
            <Card flush title="Current against proposed">
              {!sim ? <div style={{ padding: 20 }}><Loading /></div> : (
                <table className="table">
                  <thead><tr><th></th><th className="right">Current</th><th className="right">Proposed</th></tr></thead>
                  <tbody>{rowsCmp.map(([label, fn]) => (
                    <tr key={label}><td>{label}</td><td className="right num muted">{fn(c)}</td><td className="right num" style={{ color: fn(c) !== fn(p) ? "var(--c-accent-cyan)" : undefined }}>{fn(p)}</td></tr>
                  ))}</tbody>
                </table>
              )}
            </Card>
          </div>
        </div>
      </div>

      {confirm && (
        <Modal title="Apply new thresholds?" onClose={() => setConfirm(false)} footer={<>
          <button className="btn btn-ghost" onClick={() => setConfirm(false)}>Cancel</button>
          <button className="btn btn-primary" disabled={act.busy} onClick={() => act.run(() => api("/v1/policy/thresholds", { method: "PUT", body: t })).then((ok) => { if (ok) { setConfirm(false); policy.reload(); } })}>
            <FlaskConical size={15} /> {act.busy ? "Applying…" : "Apply now"}</button>
        </>}>
          <p>Warning from <strong>{t.t_warning}</strong>, Step-Up from <strong>{t.t_stepup}</strong>, Block from <strong>{t.t_block}</strong>.</p>
          <p className="muted">This takes effect on the next payment and is written to the audit log.</p>
          {act.error && <Banner kind="err">{act.error}</Banner>}
        </Modal>
      )}
    </>
  );
}
