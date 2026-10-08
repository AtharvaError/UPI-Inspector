"use client";

import {
  Activity, BadgeAlert, ClipboardCheck, FlaskConical, Gauge, KeyRound, LineChart, LockKeyhole, LogOut, Network, PanelLeftClose,
  PanelLeftOpen, PlayCircle, Radar, ScrollText, ShieldCheck, SlidersHorizontal, Users, UserSearch,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useEffect, useState } from "react";
import { adoptSession, api, getSession, getTenant, Grant, IDLE_MS, idleFor, noteActivity, setTenant, signOut as endSession } from "@/lib/api";
import { BANK } from "@/lib/format";
import { useAction, useApi, useLiveSocket } from "@/lib/hooks";
import { clearLive, setStaff, setTenantState, toggleSide, useAppDispatch, useAppSelector } from "@/lib/store";
import { Banner, Modal, Segmented } from "./ui";

type Item = { href: string; label: string; icon: React.ReactNode; roles?: string[] };
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "Monitor", items: [
    { href: "/feed", label: "Live feed", icon: <Activity size={18} /> },
    { href: "/near-misses", label: "Near-misses", icon: <BadgeAlert size={18} /> },
    { href: "/reviews", label: "Review queue", icon: <ClipboardCheck size={18} /> },
  ]},
  { title: "Investigate", items: [
    { href: "/payees", label: "Payees", icon: <UserSearch size={18} /> },
    { href: "/network", label: "Network", icon: <Network size={18} /> },
  ]},
  { title: "Control", items: [
    { href: "/policy", label: "Policy lab", icon: <SlidersHorizontal size={18} /> },
    { href: "/model", label: "Model health", icon: <LineChart size={18} /> },
    { href: "/system", label: "System health", icon: <Gauge size={18} /> },
  ]},
  { title: "Admin", items: [
    { href: "/users", label: "Users and roles", icon: <Users size={18} />, roles: ["admin"] },
    { href: "/security", label: "Security", icon: <LockKeyhole size={18} />, roles: ["admin", "auditor"] },
    { href: "/audit", label: "Audit log", icon: <ScrollText size={18} />, roles: ["admin", "auditor"] },
    { href: "/demo", label: "Demo control", icon: <PlayCircle size={18} />, roles: ["admin"] },
  ]},
];
const TITLES: Record<string, string> = Object.fromEntries(GROUPS.flatMap((g) => g.items.map((i) => [i.href, i.label])));

export default function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const dispatch = useAppDispatch();
  const { staff, tenant, ready } = useAppSelector((s) => s.session);
  const collapsed = useAppSelector((s) => s.ui.collapsed);
  const connected = useAppSelector((s) => s.live.connected);

  useEffect(() => {
    const s = getSession();
    if (!s) {
      router.replace("/login");
      return;
    }
    dispatch(setStaff({ staff: s.staff, tenant: s.staff.role === "admin" ? getTenant() : s.staff.tenant_id }));
  }, [dispatch, router]);

  useLiveSocket(ready && !!staff);
  const policy = useApi<any>(ready && staff ? "/v1/policy" : null, [], 15000);
  const [pwOpen, setPwOpen] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "", again: "" });
  const [pwDone, setPwDone] = useState(false);
  const act = useAction();

  // Sign out a dashboard nobody is using. The pages poll in the background, so "in use" means the keyboard
  // or the mouse, not requests (F-35).
  useEffect(() => {
    if (!ready || !staff) return;
    const seen = () => noteActivity();
    const events = ["keydown", "pointerdown", "wheel", "touchstart"] as const;
    events.forEach((e) => window.addEventListener(e, seen, { passive: true }));
    noteActivity();
    const timer = setInterval(async () => {
      if (idleFor() > IDLE_MS) {
        await endSession("idle");
        window.location.href = "/login";
      }
    }, 20000);
    return () => {
      events.forEach((e) => window.removeEventListener(e, seen));
      clearInterval(timer);
    };
  }, [ready, staff]);

  if (!ready || !staff) return <div className="login-wrap"><div className="skeleton" style={{ width: 240, height: 24 }} /></div>;

  const signOut = async () => {
    await endSession();
    router.replace("/login");
  };
  const changePassword = () => {
    if (pw.next !== pw.again) return act.setError("The two new passwords are not the same");
    act.run(async () => {
      // The server signs every other session of this account out and gives this tab a new one.
      const g = await api<Grant>("/v1/auth/change-password", { body: { current_password: pw.current, new_password: pw.next } });
      adoptSession(g, g.staff || staff);
      return true;
    }).then((ok) => {
      if (!ok) return;
      setPw({ current: "", next: "", again: "" });
      setPwOpen(false);
      setPwDone(true);
    });
  };
  const switchTenant = (t: string) => {
    setTenant(t);
    dispatch(setTenantState(t));
    dispatch(clearLive());
  };
  const title = TITLES["/" + (path.split("/")[1] || "")] || (path.startsWith("/decisions") ? "Decision" : "UPI Inspector");
  const mode = policy.data?.mode;

  return (
    <div className="shell">
      <aside className={`side ${collapsed ? "collapsed" : ""}`} aria-label="Main navigation">
        <div className="brand">
          <span className="logo"><ShieldCheck size={19} color="#fff" /></span>
          {!collapsed && <span>UPI Inspector</span>}
        </div>
        <nav>
          {GROUPS.map((g) => {
            const items = g.items.filter((i) => !i.roles || i.roles.includes(staff.role));
            if (!items.length) return null;
            return (
              <React.Fragment key={g.title}>
                <div className="group">{g.title}</div>
                {items.map((i) => (
                  <Link key={i.href} href={i.href} title={i.label}
                    className={`nav-item ${path === i.href || path.startsWith(i.href + "/") || (i.href === "/feed" && path.startsWith("/decisions")) ? "on" : ""}`}>
                    {i.icon}
                    {!collapsed && <span>{i.label}</span>}
                  </Link>
                ))}
              </React.Fragment>
            );
          })}
        </nav>
        <div className="foot col gap-2">
          {!collapsed && (
            <div style={{ padding: "0 8px" }}>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{staff.name}</div>
              <div className="row gap-2" style={{ marginTop: 4 }}>
                <span className="chip brand" style={{ textTransform: "capitalize" }}>{staff.role}</span>
                <span className="caption">{BANK[staff.tenant_id]}</span>
              </div>
            </div>
          )}
          <button className="nav-item" style={{ border: 0, background: "transparent", cursor: "pointer", width: "100%" }}
            onClick={() => { setPwOpen(true); setPwDone(false); act.setError(null); }} title="Change password">
            <KeyRound size={18} />{!collapsed && <span>Change password</span>}
          </button>
          <button className="nav-item" style={{ border: 0, background: "transparent", cursor: "pointer", width: "100%" }} onClick={signOut} title="Sign out">
            <LogOut size={18} />{!collapsed && <span>Sign out</span>}
          </button>
          <button className="nav-item" style={{ border: 0, background: "transparent", cursor: "pointer", width: "100%" }}
            onClick={() => dispatch(toggleSide())} title={collapsed ? "Expand navigation" : "Collapse navigation"}>
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}{!collapsed && <span>Collapse</span>}
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="row gap-2 grow">
            <Radar size={18} color="#a995ff" />
            <strong>{title}</strong>
          </div>
          <span className="row gap-2 caption" title={connected ? "Live updates connected" : "Live updates disconnected; polling"}>
            <span className={`dot ${connected ? "live" : "off"}`} /> {connected ? "Live" : "Polling"}
          </span>
          {mode && (
            <span className={`chip ${mode === "enforce" ? "t-SAFE" : "t-WARNING"}`} title="Whether decisions are applied to payments">
              {mode === "enforce" ? <ShieldCheck size={13} /> : <FlaskConical size={13} />} {mode === "enforce" ? "Enforcing" : "Shadow"}
            </span>
          )}
          <Segmented value={tenant} disabled={staff.role !== "admin"} onChange={switchTenant}
            options={[{ value: "bank_a", label: "Bank A" }, { value: "bank_b", label: "Bank B" }]} />
          <span className="chip" title="All data in this prototype is synthetic">Demo data</span>
        </header>
        <main className="content" key={tenant}>
          {pwDone && <Banner kind="ok">Password changed. Every other session of this account has been signed out.</Banner>}
          {children}
        </main>
      </div>
      {pwOpen && (
        <Modal title="Change password" onClose={() => setPwOpen(false)}
          footer={<>
            <button className="btn btn-ghost" onClick={() => setPwOpen(false)}>Cancel</button>
            <button className="btn btn-primary" disabled={act.busy || !pw.current || !pw.next} onClick={changePassword}>{act.busy ? "Saving…" : "Change password"}</button>
          </>}>
          <p className="muted" style={{ fontSize: 14 }}>At least 10 characters, with letters and digits or symbols. Changing it signs this account out everywhere else.</p>
          <label className="field">Current password
            <input className="input" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
          </label>
          <label className="field">New password
            <input className="input" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
          </label>
          <label className="field">New password again
            <input className="input" type="password" autoComplete="new-password" value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} />
          </label>
          {act.error && <Banner kind="err">{act.error}</Banner>}
        </Modal>
      )}
    </div>
  );
}
