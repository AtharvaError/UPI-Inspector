"use client";

import { useState } from "react";
import { clock } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { Banner, Card, Empty, Loading, PageHead } from "@/components/ui";

const LABEL: Record<string, string> = {
  "staff.login": "Signed in", "staff.create": "Added a staff member", "staff.update": "Changed a staff account",
  "staff.reset_password": "Reset a password", "user.update": "Changed an app user", "user.register": "App user registered",
  "policy.mode.update": "Switched enforce / shadow", "policy.thresholds.update": "Changed thresholds",
  "policy.block_mode.update": "Changed block mode", "decision.label": "Labelled a decision", "review.resolve": "Resolved a review",
  "pack.create": "Created a complaint pack", "model.reload": "Reloaded model and rules", "demo.scenario": "Staged a demo scenario",
  "demo.reset": "Reset the demo", "demo.chaos": "Toggled a failure test", "guardian.invite": "Invited a guardian",
  "guardian.accept": "Accepted a guardian invitation", "guardian.change": "Changed guardian settings",
  "guardian.remove": "Asked to remove a guardian", "guardian.undo": "Undid a guardian change", "guardian.approve": "Guardian approved a payment",
  "guardian.deny": "Guardian refused a payment", "guardian.dismiss": "Guardian dismissed an alert",
  "auth.login": "App user signed in", "auth.demo_login": "App user signed in (demo)", "auth.login_failed": "Failed app sign-in",
  "auth.staff_login_failed": "Failed staff sign-in", "auth.lockout": "Account locked after wrong attempts", "auth.logout": "Signed out",
  "auth.logout_all": "Signed out everywhere", "auth.pin_changed": "Changed app PIN", "auth.unlock_failed": "Wrong PIN on the app lock",
  "auth.change_pin_failed": "Wrong PIN while changing PIN", "auth.change_password_failed": "Wrong password while changing password",
  "auth.device_enrolled": "Enrolled a phone", "auth.device_removed": "Removed a phone", "auth.device_key_failed": "Wrong PIN while enrolling a phone",
  "staff.demo_login": "Signed in (demo)", "staff.password_changed": "Changed own password", "caller.report": "Reported a caller",
  "block.add": "Added a UPI ID to a never-pay list", "block.remove_requested": "Asked to unblock a UPI ID", "block.kept": "Kept a UPI ID blocked",
};

export default function AuditLog() {
  const [filter, setFilter] = useState("");
  const { data, loading, error } = useApi<any>(`/v1/audit?limit=200${filter ? `&action=${encodeURIComponent(filter)}` : ""}`, [filter], 15000);
  return (
    <>
      <PageHead title="Audit log" sub="Who changed thresholds, modes, labels, accounts and models, and when. Append-only: a demo reset does not clear it."
        right={<select className="input" style={{ width: 220 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Everything</option><option value="auth.">Sign-in and sessions</option><option value="policy">Policy changes</option><option value="staff">Staff accounts</option>
          <option value="user">App users</option><option value="decision">Labels</option><option value="review">Reviews</option>
          <option value="guardian">Guardians</option><option value="caller">Caller reports</option><option value="block">Never-pay lists</option>
          <option value="model">Model</option><option value="demo">Demo control</option>
        </select>} />
      {error && <Banner kind="err">{error}</Banner>}
      <Card flush>
        {loading && !data ? <div style={{ padding: 20 }}><Loading /></div> : !data?.entries?.length ? <Empty>No entries.</Empty> : (
          <div className="scroll-x"><table className="table">
            <thead><tr><th>When</th><th>Who</th><th>What</th><th>Subject</th><th>Detail</th></tr></thead>
            <tbody>{data.entries.map((e: any) => (
              <tr key={e.id}>
                <td className="nowrap num">{clock(e.created_at, true)}</td>
                <td className="nowrap">{e.actor_name || e.actor}<div className="caption">{e.actor.split(":")[0]}</div></td>
                <td>{LABEL[e.action] || e.action}<div className="caption mono">{e.action}</div></td>
                <td className="mono caption" style={{ maxWidth: 220, overflowWrap: "anywhere" }}>{e.subject}</td>
                <td className="caption mono" style={{ maxWidth: 420, overflowWrap: "anywhere", fontWeight: 400 }}>{e.detail && Object.keys(e.detail).length ? JSON.stringify(e.detail) : "—"}</td>
              </tr>))}</tbody>
          </table></div>)}
      </Card>
    </>
  );
}
