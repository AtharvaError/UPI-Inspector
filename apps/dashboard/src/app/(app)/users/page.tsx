"use client";

import { KeyRound, Search, UserPlus } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { ago, BANK } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import { useAppSelector } from "@/lib/store";
import { Banner, Card, Empty, Loading, Modal, PageHead } from "@/components/ui";

const ROLE_NOTE: Record<string, string> = {
  admin: "Everything: thresholds, enforce or shadow, accounts, audit log, demo control.",
  analyst: "Investigate, label decisions, resolve reviews, create complaint packs.",
  auditor: "Read-only access to every page, plus the audit log.",
};

export default function UsersAndRoles() {
  const me = useAppSelector((s) => s.session.staff);
  const tenant = useAppSelector((s) => s.session.tenant);
  const staff = useApi<any>("/v1/admin/staff");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const users = useApi<any>(`/v1/admin/users?q=${encodeURIComponent(query)}`, [query]);
  const act = useAction();
  const [adding, setAdding] = useState(false);
  const [resetFor, setResetFor] = useState<any>(null);
  const [form, setForm] = useState({ name: "", email: "", role: "analyst", password: "" });
  const [newPw, setNewPw] = useState("");
  const [note, setNote] = useState<string | null>(null);

  if (me?.role !== "admin") return <Banner kind="err">Only an admin can manage accounts.</Banner>;
  const patchStaff = (id: string, body: any) => act.run(() => api(`/v1/admin/staff/${id}`, { method: "PATCH", body })).then(() => staff.reload());

  return (
    <>
      <PageHead title="Users and roles" sub={`Accounts for ${BANK[tenant]}. What a person can see and do depends on their role. Every change here is written to the audit log.`}
        right={<button className="btn btn-primary" onClick={() => { setAdding(true); act.setError(null); }}><UserPlus size={16} /> Add staff member</button>} />
      {act.error && !adding && !resetFor && <Banner kind="err">{act.error}</Banner>}
      {note && <Banner kind="ok">{note}</Banner>}

      <div className="grid g3">
        {Object.entries(ROLE_NOTE).map(([r, n]) => (
          <div key={r} className="card"><span className="chip brand" style={{ textTransform: "capitalize" }}>{r}</span><p className="muted" style={{ marginTop: 8, fontSize: 14 }}>{n}</p></div>
        ))}
      </div>

      <Card flush title="Staff (dashboard)">
        {staff.loading && !staff.data ? <div style={{ padding: 20 }}><Loading /></div> : (
          <div className="scroll-x"><table className="table">
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead>
            <tbody>{(staff.data?.staff || []).map((s: any) => {
              const self = s.staff_id === me.staff_id;
              return (
                <tr key={s.staff_id}>
                  <td>{s.display_name}{self && <span className="chip" style={{ marginLeft: 8 }}>you</span>}</td>
                  <td className="mono caption">{s.email}</td>
                  <td>
                    <select className="input" style={{ width: 130, height: 32 }} value={s.role} disabled={self || act.busy} onChange={(e) => patchStaff(s.staff_id, { role: e.target.value })}
                      title={self ? "You cannot change your own role" : "Change role"}>
                      {["admin", "analyst", "auditor"].map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td><span className={`chip ${s.active ? "t-SAFE" : "t-BLOCK"}`}>{s.active ? "Active" : "Deactivated"}</span></td>
                  <td className="muted nowrap">{s.last_login_at ? ago(s.last_login_at) : "never"}</td>
                  <td className="right nowrap">
                    <span className="row gap-2" style={{ justifyContent: "flex-end" }}>
                      <button className="btn btn-ghost btn-sm" onClick={() => { setResetFor(s); setNewPw(""); act.setError(null); }}><KeyRound size={14} /> Reset password</button>
                      <button className="btn btn-ghost btn-sm" disabled={self || act.busy} title={self ? "You cannot deactivate your own account" : ""}
                        onClick={() => patchStaff(s.staff_id, { active: !s.active })}>{s.active ? "Deactivate" : "Reactivate"}</button>
                    </span>
                  </td>
                </tr>);
            })}</tbody>
          </table></div>)}
      </Card>

      <Card flush title={`App users${users.data ? ` (${users.data.total.toLocaleString("en-IN")} in this bank)` : ""}`} right={
        <form className="row gap-2" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
          <div style={{ position: "relative" }}><Search size={15} style={{ position: "absolute", left: 10, top: 12, color: "var(--c-text-muted)" }} />
            <input className="input" style={{ width: 240, paddingLeft: 32 }} placeholder="Name or UPI ID" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        </form>}>
        {users.loading && !users.data ? <div style={{ padding: 20 }}><Loading /></div> : !users.data?.users?.length ? <Empty>No app users match.</Empty> : (
          <div className="scroll-x"><table className="table">
            <thead><tr><th>Name</th><th>Phone</th><th>UPI ID</th><th>Segment</th><th>Language</th><th>Status</th><th></th></tr></thead>
            <tbody>{users.data.users.map((u: any) => (
              <tr key={u.user_id}>
                <td>{u.display_name}{u.is_demo && <span className="chip brand" style={{ marginLeft: 8 }}>demo</span>}{u.is_synthetic && <span className="chip" style={{ marginLeft: 8 }}>simulated</span>}</td>
                <td className="mono caption">{u.phone}</td>
                <td className="mono caption">{u.primary_vpa}</td>
                <td className="muted">{u.segment.replace("_", " ")} · {u.age_band}</td>
                <td className="muted">{u.locale === "hi" ? "Hindi" : "English"}</td>
                <td><span className={`chip ${u.active ? "t-SAFE" : "t-BLOCK"}`}>{u.active ? "Active" : "Deactivated"}</span></td>
                <td className="right"><button className="btn btn-ghost btn-sm" disabled={act.busy}
                  onClick={() => act.run(() => api(`/v1/admin/users/${u.user_id}`, { method: "PATCH", body: { active: !u.active } })).then(() => users.reload())}>{u.active ? "Deactivate" : "Reactivate"}</button></td>
              </tr>))}</tbody>
          </table></div>)}
      </Card>

      {adding && (
        <Modal title="Add a staff member" onClose={() => setAdding(false)} footer={<>
          <button className="btn btn-ghost" onClick={() => setAdding(false)}>Cancel</button>
          <button className="btn btn-primary" disabled={act.busy || !form.name || !form.email || form.password.length < 8}
            onClick={() => act.run(() => api("/v1/admin/staff", { body: form })).then((ok) => { if (ok) { setAdding(false); setForm({ name: "", email: "", role: "analyst", password: "" }); setNote("Staff member added. Give them their password yourself; it is not shown again."); staff.reload(); } })}>
            {act.busy ? "Adding…" : "Add"}</button>
        </>}>
          <label className="field">Name<input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field">Email<input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
          <label className="field">Role
            <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {["analyst", "auditor", "admin"].map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <span className="caption" style={{ fontWeight: 400 }}>{ROLE_NOTE[form.role]}</span>
          </label>
          <label className="field">Initial password (at least 8 characters)<input className="input" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
          {act.error && <Banner kind="err">{act.error}</Banner>}
        </Modal>
      )}
      {resetFor && (
        <Modal title={`Reset password for ${resetFor.display_name}`} onClose={() => setResetFor(null)} footer={<>
          <button className="btn btn-ghost" onClick={() => setResetFor(null)}>Cancel</button>
          <button className="btn btn-primary" disabled={act.busy || newPw.length < 8}
            onClick={() => act.run(() => api(`/v1/admin/staff/${resetFor.staff_id}/reset-password`, { body: { password: newPw } })).then((ok) => { if (ok) { setResetFor(null); setNote("Password reset. Any lock on the account was cleared."); } })}>
            {act.busy ? "Saving…" : "Reset password"}</button>
        </>}>
          <label className="field">New password (at least 8 characters)<input className="input" type="password" autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} /></label>
          {act.error && <Banner kind="err">{act.error}</Banner>}
        </Modal>
      )}
    </>
  );
}
