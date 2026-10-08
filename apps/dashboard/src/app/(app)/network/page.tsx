"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { BAND_COLOR, BAND_LABEL, inr, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { BandTag, Banner, Card, Empty, Loading, PageHead, Segmented } from "@/components/ui";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false });
const DEFAULT = "cyber.cell.verify@bankb";

function NetworkView() {
  const router = useRouter();
  const sp = useSearchParams();
  const vpa = sp.get("vpa") || DEFAULT;
  const [hops, setHops] = useState("2");
  const [q, setQ] = useState(vpa);
  const [sel, setSel] = useState<any>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const { data, loading, error } = useApi<any>(`/v1/graph/neighbourhood?vpa=${encodeURIComponent(vpa)}&hops=${hops}`, [vpa, hops]);

  useEffect(() => { setQ(vpa); setSel(null); }, [vpa]);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: 560 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const graph = useMemo(() => {
    if (!data) return { nodes: [], links: [] };
    return {
      nodes: data.nodes.map((n: any) => ({ ...n })),
      links: data.edges.map((e: any) => ({ ...e })),
    };
  }, [data]);
  const centre = data?.nodes?.find((n: any) => n.centre);
  const shown = sel || centre;

  return (
    <>
      <PageHead title="Network" sub="Money flowing between UPI IDs, and devices they share. Solid arrows are payments; dashed lines are shared devices. Following forwarded money finds the mule chain without implicating its victims."
        right={<form className="row gap-2" onSubmit={(e) => { e.preventDefault(); router.push(`/network?vpa=${encodeURIComponent(q.trim().toLowerCase())}`); }}>
          <input className="input" style={{ width: 280 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="UPI ID" />
          <Segmented value={hops} onChange={setHops} options={[{ value: "1", label: "1 hop" }, { value: "2", label: "2 hops" }, { value: "3", label: "3 hops" }]} />
        </form>} />
      {error && <Banner kind="err">{error}</Banner>}
      <div className="grid g-main" style={{ gridTemplateColumns: "minmax(0, 2.2fr) minmax(0, 1fr)" }}>
        <Card flush>
          <div ref={wrap} style={{ height: 560, position: "relative" }}>
            {loading && !data ? <div style={{ padding: 20 }}><Loading /></div> : graph.nodes.length === 0 ? <Empty>No connections in this window.</Empty> : (
              <ForceGraph2D
                graphData={graph as any} width={size.w} height={size.h} backgroundColor="rgba(0,0,0,0)" cooldownTicks={120}
                nodeRelSize={5} nodeVal={(n: any) => (n.centre ? 6 : n.band === "REPORTED" ? 4 : 2)}
                nodeLabel={(n: any) => `${n.registered_name} (${n.id}) · ${BAND_LABEL[n.band] || n.band}`}
                linkColor={(l: any) => (l.type === "device" ? "rgba(56,214,255,0.55)" : "rgba(167,164,194,0.35)")}
                linkLineDash={(l: any) => (l.type === "device" ? [4, 4] : null)}
                linkWidth={(l: any) => (l.type === "device" ? 1 : Math.min(4, 0.6 + Math.log10(1 + (l.amount || 0)) * 0.5))}
                linkDirectionalArrowLength={(l: any) => (l.type === "payment" ? 4 : 0)} linkDirectionalArrowRelPos={1}
                onNodeClick={(n: any) => setSel(n)}
                nodeCanvasObject={(n: any, ctx, scale) => {
                  const r = n.centre ? 9 : n.band === "REPORTED" ? 7 : 5;
                  ctx.beginPath();
                  ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
                  ctx.fillStyle = BAND_COLOR[n.band] || "#8B88B5";
                  ctx.fill();
                  if (n.centre || (sel && sel.id === n.id)) {
                    ctx.lineWidth = 2 / scale + 1;
                    ctx.strokeStyle = "#ffffff";
                    ctx.stroke();
                  }
                  if (n.kind === "merchant") {
                    ctx.fillStyle = "#070614";
                    ctx.fillRect(n.x - 2, n.y - 2, 4, 4);
                  }
                  if (scale > 1.6 || n.centre || n.band === "REPORTED") {
                    ctx.font = `${Math.max(3.2, 11 / scale)}px sans-serif`;
                    ctx.fillStyle = "#C9C6E6";
                    ctx.textAlign = "center";
                    ctx.fillText(n.registered_name, n.x, n.y + r + 10 / scale + 2);
                  }
                }}
              />
            )}
            <div className="row wrap gap-2" style={{ position: "absolute", left: 14, bottom: 12 }}>
              {Object.entries(BAND_LABEL).map(([k, v]) => (<span key={k} className="caption row gap-1" style={{ gap: 5 }}><span className="dot" style={{ background: BAND_COLOR[k] }} />{v}</span>))}
              <span className="caption">■ merchant</span>
            </div>
            {data?.truncated && <div className="caption" style={{ position: "absolute", right: 14, top: 12 }}>Showing the first 300 UPI IDs</div>}
          </div>
        </Card>
        <div className="col gap-4">
          <Card title={sel ? "Selected" : "Centre"}>
            {!shown ? <p className="muted">Click a node.</p> : (
              <div className="col gap-2">
                <div className="row gap-2 wrap"><BandTag band={shown.band} /><span className="chip">{shown.kind}</span></div>
                <strong>{shown.registered_name}</strong>
                <div className="mono caption">{shown.id}</div>
                <dl className="kv" style={{ gridTemplateColumns: "140px minmax(0,1fr)" }}>
                  <dt>Downstream</dt><dd>{shown.downstream_hop != null && shown.downstream_hop < 9 ? `${shown.downstream_hop} step(s) from a reported account` : "—"}</dd>
                  <dt>Forwards</dt><dd>{shown.passthrough_ratio != null ? pct(shown.passthrough_ratio, 0) + " within 30 min" : "—"}</dd>
                  <dt>Community</dt><dd>{shown.community_id ?? "—"}</dd>
                </dl>
                <div className="row wrap gap-2">
                  <button className="btn btn-white btn-sm" onClick={() => router.push(`/network?vpa=${encodeURIComponent(shown.id)}`)}>Centre here</button>
                  <Link className="btn btn-ghost btn-sm" href={`/payees?vpa=${encodeURIComponent(shown.id)}`}>Open payee</Link>
                </div>
              </div>
            )}
          </Card>
          <Card title="Largest flows">
            {!data?.edges?.length ? <p className="muted">None.</p> : (
              <div className="col gap-2">
                {[...data.edges].filter((e: any) => e.type === "payment").sort((a: any, b: any) => b.amount - a.amount).slice(0, 7).map((e: any, i: number) => (
                  <div key={i} className="caption" style={{ fontWeight: 400 }}>
                    <span className="mono">{e.source.split("@")[0]}</span> → <span className="mono">{e.target.split("@")[0]}</span>
                    <span className="num" style={{ float: "right", color: "var(--c-text)" }}>{inr(e.amount)} · {e.count}×</span>
                  </div>
                ))}
              </div>
            )}
            {data?.graph_job?.last > 0 && <p className="caption faint" style={{ marginTop: 12 }}>Graph features recomputed every {data.graph_job.seconds != null ? "few minutes" : ""}; last run covered {data.graph_job.edges?.toLocaleString?.("en-IN") ?? "—"} payments.</p>}
          </Card>
        </div>
      </div>
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><NetworkView /></Suspense>;
}
