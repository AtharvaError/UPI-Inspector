"use client";

import { AlertTriangle, CheckCircle2, Info, ShieldAlert } from "lucide-react";
import { clock, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { Banner, Card, Empty, Loading, PageHead, Stat } from "@/components/ui";

const STATUS: Record<string, { cls: string; label: string; icon: React.ReactNode }> = {
  ok: { cls: "t-SAFE", label: "In place", icon: <CheckCircle2 size={13} /> },
  info: { cls: "", label: "Demo setting", icon: <Info size={13} /> },
  warn: { cls: "t-WARNING", label: "Check", icon: <AlertTriangle size={13} /> },
  fail: { cls: "t-BLOCK", label: "Not safe outside a demo", icon: <ShieldAlert size={13} /> },
};

const EVENT: Record<string, string> = {
  "auth.login": "App sign-ins", "auth.demo_login": "App sign-ins without a PIN (demo)", "auth.login_failed": "Failed app sign-ins",
  "auth.staff_login_failed": "Failed staff sign-ins", "auth.lockout": "Accounts locked", "auth.logout": "Sign-outs",
  "auth.logout_all": "Sign-outs everywhere", "auth.pin_changed": "PIN changes", "auth.unlock_failed": "Wrong PINs on the app lock",
  "auth.change_pin_failed": "Wrong PINs while changing a PIN", "auth.change_password_failed": "Wrong passwords while changing a password",
  "auth.device_enrolled": "Phones enrolled", "auth.device_removed": "Phones removed", "auth.device_key_failed": "Wrong PINs while enrolling a phone",
  "staff.login": "Staff sign-ins", "staff.demo_login": "Staff sign-ins without a password (demo)", "staff.password_changed": "Staff password changes",
  "staff.reset_password": "Staff passwords reset by an admin", "user.register": "New app accounts", "caller.report": "Callers reported",
  "block.add": "UPI IDs added to never-pay lists", "block.remove_requested": "Unblock requests", "block.kept": "Unblocks cancelled",
};
// one event, as a row in the "most recent" table
const ONE: Record<string, string> = {
  "auth.login_failed": "Failed app sign-in", "auth.staff_login_failed": "Failed staff sign-in", "auth.lockout": "Account locked",
  "auth.logout": "Signed out", "auth.logout_all": "Signed out everywhere", "auth.pin_changed": "Changed app PIN",
  "auth.unlock_failed": "Wrong PIN on the app lock", "auth.change_pin_failed": "Wrong PIN while changing PIN",
  "auth.change_password_failed": "Wrong password while changing password", "auth.device_enrolled": "Enrolled a phone",
  "auth.device_removed": "Removed a phone", "auth.device_key_failed": "Wrong PIN while enrolling a phone",
  "staff.password_changed": "Changed own password", "staff.reset_password": "Password reset by an admin", "user.register": "New app account",
  "caller.report": "Reported a caller", "block.add": "Added to a never-pay list", "block.remove_requested": "Asked to unblock",
  "block.kept": "Kept a UPI ID blocked",
};
const BAD = ["auth.login_failed", "auth.staff_login_failed", "auth.lockout", "auth.unlock_failed", "auth.change_pin_failed", "auth.change_password_failed", "auth.device_key_failed"];

export default function Security() {
  const me = useAppSelector((s) => s.session.staff);
  const { data, loading, error } = useApi<any>("/v1/security/posture", [], 20000);
  if (me && me.role === "analyst") return <Banner kind="err">Only an admin or an auditor can open this page.</Banner>;
  const c = data?.counts || {};
  const events = Object.entries((data?.events_24h || {}) as Record<string, number>);
  const failed = events.filter(([k]) => BAD.includes(k)).reduce((n, [, v]) => n + v, 0);

  return (
    <>
      <PageHead title="Security" sub="How this deployment is set up, and what has been happening at the front door in the last 24 hours. The same checks run when the service starts and in “python manage.py security-check”." />
      {error && <Banner kind="err">{error}</Banner>}
      {data?.overall === "fail" && <Banner kind="err">One or more settings are not safe outside a demo. They are marked below. Outside demo mode the service refuses to start with them.</Banner>}
      {data?.overall === "warn" && <Banner kind="warn">One or more settings deserve a second look. They are marked below.</Banner>}

      <div className="grid g4">
        <Stat label="Failed sign-ins and wrong PINs, 24 h" value={num(failed)} tone={failed > 20 ? "var(--c-warning)" : undefined} sub="every one is in the audit log, with its address" />
        <Stat label="Accounts locked right now" value={num((c.locked_users || 0) + (c.locked_staff || 0))} sub={`${num(c.locked_users)} app users · ${num(c.locked_staff)} staff`} tone={(c.locked_users || 0) + (c.locked_staff || 0) ? "var(--c-warning)" : undefined} />
        <Stat label="Sessions signed out and still blocked" value={num(c.revoked_sessions)} sub="a signed-out token is refused until it would have expired" />
        <Stat label="App users with an enrolled phone" value={num(c.users_with_enrolled_device)} sub={`${num(c.caller_reports)} caller reports · ${num(c.never_pay_entries)} never-pay entries`} />
      </div>

      <Card flush title="Configuration">
        {loading && !data ? <div style={{ padding: 20 }}><Loading rows={6} /></div> : (
          <div className="scroll-x"><table className="table">
            <thead><tr><th style={{ width: 220 }}>Control</th><th style={{ width: 210 }}>Status</th><th>What is set</th></tr></thead>
            <tbody>{(data?.checks || []).map((r: any) => {
              const s = STATUS[r.status] || STATUS.info;
              return (
                <tr key={r.key}>
                  <td style={{ fontWeight: 600 }}>{r.label}</td>
                  <td><span className={`chip ${s.cls}`}>{s.icon} {s.label}</span></td>
                  <td className="muted" style={{ fontSize: 14 }}>{r.detail}</td>
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </Card>

      <div className="grid g2">
        <Card flush title="Last 24 hours">
          {!events.length ? <Empty>Nothing recorded.</Empty> : (
            <table className="table">
              <thead><tr><th>Event</th><th style={{ textAlign: "right" }}>Count</th></tr></thead>
              <tbody>{events.map(([k, v]) => (
                <tr key={k}>
                  <td>{EVENT[k] || k}<div className="caption mono">{k}</div></td>
                  <td className="num" style={{ textAlign: "right", color: BAD.includes(k) ? "var(--c-warning)" : undefined }}>{num(v)}</td>
                </tr>))}</tbody>
            </table>
          )}
        </Card>
        <Card flush title="Most recent, other than ordinary sign-ins">
          {!data?.recent?.length ? <Empty>Nothing recorded.</Empty> : (
            <div className="scroll-x"><table className="table">
              <thead><tr><th>When</th><th>What</th><th>Who</th><th>From</th></tr></thead>
              <tbody>{data.recent.map((e: any, i: number) => (
                <tr key={i}>
                  <td className="nowrap num">{clock(e.created_at, true)}</td>
                  <td style={{ color: BAD.includes(e.action) ? "var(--c-warning)" : undefined }}>{ONE[e.action] || EVENT[e.action] || e.action}</td>
                  <td className="caption">{e.actor_name || "—"}</td>
                  <td className="caption mono">{e.detail?.ip || "—"}</td>
                </tr>))}</tbody>
            </table></div>
          )}
        </Card>
      </div>
    </>
  );
}
