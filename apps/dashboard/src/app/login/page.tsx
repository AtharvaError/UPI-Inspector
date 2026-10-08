"use client";

import { ArrowRight, KeyRound, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { adoptSession, api, getSession, Grant, takeNotice } from "@/lib/api";
import { BANK } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { Banner } from "@/components/ui";

const ROLE_NOTE: Record<string, string> = {
  admin: "Everything, including thresholds, accounts and demo control",
  analyst: "Investigate, label decisions, resolve reviews",
  auditor: "Read-only, plus the audit log",
};

export default function Login() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [demo, setDemo] = useState<any[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const { busy, error, run } = useAction();

  useEffect(() => {
    if (getSession()) router.replace("/feed");
    setNotice(takeNotice());
    // One-click demo sign-in is offered only where the API allows it: by default, on the machine that runs it.
    api<any>("/v1/auth/demo-staff", { auth: false }).then((d) => setDemo(d.staff || [])).catch(() => setDemo([]));
  }, [router]);

  const done = (g: Grant | undefined) => {
    if (!g?.staff) return;
    adoptSession(g, g.staff);
    router.replace("/feed");
  };

  return (
    <div className="login-wrap">
      <div className="grid g2" style={{ width: "min(1040px, 100%)", alignItems: "center", gap: 40 }}>
        <div className="col gap-6">
          <div className="row gap-2">
            <span className="logo" style={{ width: 40, height: 40, borderRadius: 12, display: "grid", placeItems: "center", background: "linear-gradient(135deg, var(--c-brand), var(--c-accent))", boxShadow: "var(--shadow-brand)" }}>
              <ShieldCheck size={22} color="#fff" />
            </span>
            <strong style={{ fontSize: 18 }}>UPI Inspector</strong>
          </div>
          <h1 className="hero-title">Stop the scam <span style={{ color: "var(--c-accent-cyan)" }}>before</span> the money moves.</h1>
          <p className="muted" style={{ fontSize: 17, lineHeight: "26px", maxWidth: 460 }}>
            Every UPI payment scored in real time against the call, the message and the payee behind it, with a reason a person can read.
          </p>
          <div className="row wrap gap-2">
            <span className="chip brand">Real-time scoring</span>
            <span className="chip cyan">Call and message correlation</span>
            <span className="chip">Explainable decisions</span>
          </div>
        </div>

        <div className="card raised" style={{ padding: 28, boxShadow: "var(--shadow-card)" }}>
          <h2 style={{ fontSize: 20 }}>Sign in</h2>
          <p className="muted" style={{ marginTop: 4, marginBottom: 20 }}>Analyst dashboard. What you can see depends on your role.</p>
          <form className="col" onSubmit={(e) => {
            e.preventDefault();
            run(() => api<Grant>("/v1/auth/staff-login", { body: { email, password }, auth: false })).then(done);
          }}>
            <label className="field">Email
              <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@bank.example" required />
            </label>
            <label className="field">Password
              <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {notice && !error && <Banner kind="info">{notice === "idle" ? "You were signed out after 15 minutes without activity." : "Your session ended. Sign in again."}</Banner>}
            {error && <Banner kind="err">{error}</Banner>}
            <button className="btn btn-primary" disabled={busy} type="submit"><KeyRound size={16} /> {busy ? "Signing in…" : "Sign in"}</button>
          </form>

          {demo.length > 0 && (
            <div style={{ marginTop: 24, paddingTop: 20, borderTop: "1px solid var(--c-border)" }}>
              <div className="caption" style={{ marginBottom: 10 }}>DEMO ACCOUNTS · one-click sign-in (demo mode only)</div>
              <div className="col gap-2">
                {demo.map((s) => (
                  <button key={s.email} className="btn btn-ghost" style={{ justifyContent: "space-between", height: "auto", padding: "10px 14px", borderRadius: 12, textAlign: "left" }}
                    disabled={busy}
                    onClick={() => run(() => api<Grant>("/v1/auth/demo-staff-login", { body: { email: s.email }, auth: false })).then(done)}>
                    <span className="col gap-1" style={{ gap: 2 }}>
                      <span>{s.name} <span className="chip brand" style={{ marginLeft: 6, textTransform: "capitalize" }}>{s.role}</span> <span className="chip" style={{ marginLeft: 4 }}>{BANK[s.tenant_id]}</span></span>
                      <span className="caption" style={{ fontWeight: 400 }}>{ROLE_NOTE[s.role]}</span>
                    </span>
                    <ArrowRight size={16} />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
