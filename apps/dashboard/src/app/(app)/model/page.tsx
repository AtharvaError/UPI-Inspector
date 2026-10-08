"use client";

import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { ago, pct } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { Bar, Banner, Card, Empty, Loading, PageHead, Stat } from "@/components/ui";

const STATUS: Record<string, string> = { stable: "t-SAFE", watch: "t-WARNING", alert: "t-BLOCK" };
const DIM: Record<string, string> = { age_band: "Age band", segment: "Customer segment", locale: "Language" };
const SEG: Record<string, string> = { en: "English", hi: "Hindi", small_business: "Small business", salaried: "Salaried", student: "Student", elderly: "Elderly" };

export default function ModelHealth() {
  const role = useAppSelector((s) => s.session.staff?.role);
  const alerts = useAppSelector((s) => s.live.alerts);
  const models = useApi<any>("/v1/model-health/models");
  const drift = useApi<any>("/v1/model-health/drift", [alerts.length]);
  const fair = useApi<any>("/v1/model-health/fairness");
  const act = useAction();
  const active = models.data?.models?.find((m: any) => m.stage === "active") || models.data?.models?.[0];
  const m = active?.metrics || {};

  return (
    <>
      <PageHead title="Model health" sub="Which model is serving, how it scored on held-out synthetic data, whether live traffic still looks like its training data, and whether it treats groups of payers evenly."
        right={<>
          <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(async () => { drift.setData(await api("/v1/model-health/drift?refresh=true")); fair.setData(await api("/v1/model-health/fairness?refresh=true")); })}>
            <RefreshCw size={14} /> Recompute</button>
          {role === "admin" && <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(() => api("/v1/model-health/reload", { method: "POST" })).then(() => models.reload())}>Reload model and rules</button>}
        </>} />
      {act.error && <Banner kind="err">{act.error}</Banner>}
      <Banner kind="warn">These detection figures come from synthetic data that the team generated. They show the pipeline works end to end. They are not a claim about real-world accuracy.</Banner>

      {models.loading && !models.data ? <Loading /> : (
        <>
          <div className="grid g4">
            <Stat label="Serving" value={<span className="mono" style={{ fontSize: 17 }}>{models.data?.active}</span>} sub={active ? `trained ${ago(active.created_at)}` : "rules only"} />
            <Stat label="Scams caught at Step-Up or Block" value={pct(m.recall_stepup_plus)} sub={`unseen variants ${pct(m.recall_unseen)} · low-signal ${pct(m.recall_weak)}`} tone="var(--c-safe)" />
            <Stat label="Legitimate at Step-Up or Block" value={pct(m.legit_stepup_plus, 2)} sub={`blocked ${pct(m.legit_block, 2)} · warned ${pct(m.legit_warning, 2)}`} tone="var(--c-stepup)" />
            <Stat label="Family attribution" value={pct(m.attribution_accuracy_seen)} sub={`all scam variants ${pct(m.attribution_accuracy)}`} />
          </div>
          <Card flush title="Model registry">
            <div className="scroll-x"><table className="table">
              <thead><tr><th>Version</th><th>Stage</th><th className="right">PR-AUC</th><th className="right">Recall</th><th className="right">Legit flagged</th><th className="right">p95 scoring</th><th>Gates</th><th>Trained</th></tr></thead>
              <tbody>{(models.data?.models || []).map((x: any) => (
                <tr key={x.version}>
                  <td className="mono">{x.version}</td>
                  <td><span className={`chip ${x.stage === "active" ? "t-SAFE" : ""}`}>{x.stage}</span></td>
                  <td className="right num">{x.metrics.pr_auc?.toFixed(4)}</td>
                  <td className="right num">{pct(x.metrics.recall_stepup_plus)}</td>
                  <td className="right num">{pct(x.metrics.legit_stepup_plus, 2)}</td>
                  <td className="right num">{x.metrics.score_latency_ms_p95?.toFixed(1)} ms</td>
                  <td><span className="row wrap gap-2">{Object.entries(x.gates || {}).map(([k, v]) => <span key={k} className={`chip ${v ? "t-SAFE" : "t-BLOCK"}`} title={k}>{k.replaceAll("_", " ")}</span>)}</span></td>
                  <td className="nowrap muted">{ago(x.created_at)}</td>
                </tr>))}</tbody>
            </table></div>
          </Card>
        </>
      )}

      <div className="grid g2">
        <Card title="Drift against the training baseline" right={drift.data?.created_at && <span className="caption">{ago(drift.data.created_at)}</span>}>
          {drift.loading && !drift.data ? <Loading /> : !drift.data?.features?.length ? <Empty>{drift.data?.status === "no_baseline" ? "No trained model, so no baseline." : "Not enough recent decisions yet."}</Empty> : (
            <div className="col gap-2">
              <div className="caption">Population stability index. Below 0.1 stable · 0.1 to 0.2 watch · above 0.2 alert.</div>
              {drift.data.features.slice(0, 16).map((f: any) => (
                <div key={f.feature} className="row gap-2">
                  <div style={{ flex: "0 0 190px" }} className="mono caption">{f.feature === "__score__" ? "final score" : f.feature}</div>
                  <div className="grow"><Bar value={Math.min(1, f.psi / 0.4)} color={f.status === "alert" ? "var(--c-block)" : f.status === "watch" ? "var(--c-warning)" : "var(--c-safe)"} /></div>
                  <div style={{ flex: "0 0 50px" }} className="right num caption">{f.psi.toFixed(3)}</div>
                  <span className={`chip ${STATUS[f.status]}`} style={{ flex: "0 0 62px", justifyContent: "center" }}>{f.status}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Fairness: how often are legitimate payments interrupted?" right={fair.data?.created_at && <span className="caption">{ago(fair.data.created_at)}</span>}>
          {fair.loading && !fair.data ? <Loading /> : !fair.data?.dimensions?.length ? <Empty>Not enough labelled legitimate payments yet.</Empty> : (
            <div className="col gap-4">
              {fair.data.dimensions.map((d: any) => (
                <div key={d.dimension}>
                  <div className="row between" style={{ marginBottom: 6 }}>
                    <strong style={{ fontSize: 14 }}>{DIM[d.dimension] || d.dimension}</strong>
                    <span className={`chip ${d.flagged ? "t-BLOCK" : "t-SAFE"}`}>disparity ratio {d.disparity_ratio.toFixed(2)}{d.flagged ? " · over 1.5" : ""}</span>
                  </div>
                  <table className="table">
                    <thead><tr><th>Group</th><th className="right">Legit payments</th><th className="right">Warned</th><th className="right">Step-Up</th><th className="right">Blocked</th></tr></thead>
                    <tbody>{d.segments.map((s: any) => (
                      <tr key={s.segment}><td>{SEG[s.segment] || s.segment}{s.n_legit < 200 && <span className="caption faint"> (too few to compare)</span>}</td>
                        <td className="right num">{s.n_legit.toLocaleString("en-IN")}</td><td className="right num">{pct(s.share_warning, 2)}</td>
                        <td className="right num">{pct(s.share_stepup, 2)}</td><td className="right num">{pct(s.share_block, 2)}</td></tr>
                    ))}</tbody>
                  </table>
                </div>
              ))}
              <p className="caption faint">Elderly payers really are targeted more, so more flags for them is not in itself unfair. What is compared here is how often their legitimate payments are interrupted.</p>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
