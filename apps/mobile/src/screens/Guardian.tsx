// P-13 Guardian settings and P-14 Guardian inbox (F-17). A guardian is an ordinary user of the same app.
import React, { useCallback, useEffect, useState } from "react";
import { View } from "react-native";
import { api } from "../api";
import { Avatar, Banner, Btn, Card, Chip, Empty, ErrorNote, Field, Icon, Label, Loading, Mono, Reasons, Row, Screen, Segmented, T, useCountdown } from "../components/ui";
import { refreshMe } from "../flow";
import { dateTime, duration, initials, money, useT } from "../i18n";
import { useNav } from "../nav";
import { onEvent } from "../socket";
import { Route, useAppDispatch, useAppSelector } from "../store";
import { C, S, TIER } from "../../theme";

type Mode = "notify" | "co_approve";

export default function Guardian({ route }: { route: Route }) {
  const { t, locale } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const tick = useAppSelector((s) => s.overlay.tick);
  const pending = useAppSelector((s) => s.session.me?.pending_guardian_requests || 0);
  const [tab, setTab] = useState<"mine" | "protect">(route.params?.tab === "protect" ? "protect" : "mine");
  const [data, setData] = useState<any>(null);
  const [requests, setRequests] = useState<any[]>([]);
  const [error, setError] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  // add / change form
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(false);
  const [who, setWho] = useState("");
  const [mode, setMode] = useState<Mode>("co_approve");
  const [threshold, setThreshold] = useState("20000");
  const [confirm, setConfirm] = useState<null | "remove" | "loosen">(null);

  const load = useCallback(async () => {
    try {
      const [g, r] = await Promise.all([api("/v1/guardians"), api("/v1/guardian/requests")]);
      setData(g);
      setRequests(r.requests);
      setError(null);
      refreshMe(dispatch);
    } catch (e) {
      setError(e);
    }
  }, [dispatch]);

  useEffect(() => {
    load();
  }, [load, tick]);

  const act = async (fn: () => Promise<any>, after?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      if (r && "as_protected" in r) setData(r);
      after?.();
      load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const mine = data?.as_protected;
  const name = mine?.guardian_name || "";
  const modeOpts = [{ value: "notify" as Mode, label: t("g.mode.notify") }, { value: "co_approve" as Mode, label: t("g.mode.co_approve") }];

  const invite = () => {
    const v = who.trim();
    if (!v) return;
    const guardian = v.includes("@") ? { vpa: v.toLowerCase() } : { phone: v.replace(/\D/g, "") };
    act(() => api("/v1/guardians", { body: { guardian, mode, amount_threshold: mode === "co_approve" ? Number(threshold) || 0 : null } }), () => { setAdding(false); setWho(""); });
  };

  const startEdit = () => {
    setMode(mine.mode);
    setThreshold(String(Math.round(Number(mine.amount_threshold || 0))));
    setEditing(true);
  };
  const loosening = () => {
    if (!mine) return false;
    const oldT = Number(mine.amount_threshold || 0);
    const newT = Number(threshold) || 0;
    return (mine.mode === "co_approve" && mode === "notify") || (mine.mode === "co_approve" && mode === "co_approve" && (newT > oldT || (oldT > 0 && newT === 0)));
  };
  const saveEdit = () => {
    if (loosening() && confirm !== "loosen") return setConfirm("loosen");
    act(() => api(`/v1/guardians/${mine.guardian_link_id}`, { method: "PATCH", body: { mode, amount_threshold: mode === "co_approve" ? Number(threshold) || 0 : null } }), () => { setEditing(false); setConfirm(null); });
  };

  return (
    <Screen title={t("nav.guardian")}>
      <Segmented value={tab} onChange={setTab} style={{ marginBottom: S[3] }}
        options={[{ value: "mine", label: t("g.tab.mine") }, { value: "protect", label: t("g.tab.protect"), badge: pending || undefined }]} />
      <ErrorNote error={error} onRetry={load} />
      {data === null && !error ? <Loading /> : null}

      {tab === "mine" && data ? (
        <View style={{ gap: S[2] }}>
          {!mine ? (
            <>
              <Card>
                <Icon name="shield" size={28} color={C.brandHover} />
                <T v="bodyLarge" style={{ marginTop: S[1] }}>{t("g.explain")}</T>
              </Card>
              {!adding ? (
                <Btn label={t("g.add")} icon="user-plus" onPress={() => setAdding(true)} />
              ) : (
                <Card>
                  <Field label={t("g.who")} value={who} onChangeText={setWho} autoCapitalize="none" autoCorrect={false} autoComplete="off" placeholder="arjun@banka" />
                  <Label>{t("g.mode")}</Label>
                  <Segmented value={mode} onChange={setMode} options={modeOpts} />
                  <T v="caption" color={C.textSecondary} style={{ marginTop: S[1], marginBottom: S[2] }}>{t(`g.mode.${mode}.help`)}</T>
                  {mode === "co_approve" ? <Field label={t("g.threshold")} value={threshold} onChangeText={(v) => setThreshold(v.replace(/\D/g, "").slice(0, 8))} keyboardType="number-pad" /> : null}
                  <Btn label={t("g.invite")} onPress={invite} loading={busy} disabled={!who.trim()} />
                  <Btn variant="quiet" label={t("common.cancel")} onPress={() => setAdding(false)} small />
                </Card>
              )}
            </>
          ) : null}

          {mine ? (
            <Card>
              <Row gap={S[2]}>
                <Avatar text={initials(name)} />
                <View style={{ flex: 1 }}>
                  <T v="heading">{mine.status === "PENDING" ? t("g.pending", { name }) : t("g.active", { name })}</T>
                  <Mono size={12.5}>{mine.guardian_vpa}</Mono>
                </View>
              </Row>
              {mine.status !== "PENDING" ? (
                <View style={{ marginTop: S[2], gap: 6 }}>
                  <Chip icon="shield" color={C.brandHover} bg={C.brandSubtle} label={t(`g.mode.${mine.mode}`)} />
                  <T v="body" color={C.textSecondary}>{t(`g.mode.${mine.mode}.help`)}</T>
                  {mine.mode === "co_approve" && Number(mine.amount_threshold) > 0 ? (
                    <T v="body" color={C.textSecondary}>{t("g.thresholdLine", { amount: money(mine.amount_threshold) })}</T>
                  ) : null}
                </View>
              ) : null}

              {mine.status === "REMOVAL_PENDING" ? (
                <Banner tone="warning" icon="clock" style={{ marginTop: S[2] }}>
                  <T v="body">{t("g.removal", { time: dateTime(mine.change_effective_at, locale) })}</T>
                  <Btn small full={false} variant="secondary" label={t("g.undo")} loading={busy} style={{ marginTop: S[1] }}
                    onPress={() => act(() => api(`/v1/guardians/${mine.guardian_link_id}/undo`, { method: "POST" }))} />
                </Banner>
              ) : null}
              {mine.status === "ACTIVE" && mine.pending_change ? (
                <Banner tone="warning" icon="clock" style={{ marginTop: S[2] }}>
                  <T v="body">{t("g.changePending", { time: dateTime(mine.change_effective_at, locale) })}</T>
                  <Btn small full={false} variant="secondary" label={t("g.undo")} loading={busy} style={{ marginTop: S[1] }}
                    onPress={() => act(() => api(`/v1/guardians/${mine.guardian_link_id}/undo`, { method: "POST" }))} />
                </Banner>
              ) : null}

              {mine.status === "PENDING" ? (
                <Btn variant="ghost" label={t("g.cancelInvite")} loading={busy} style={{ marginTop: S[2] }}
                  onPress={() => act(() => api(`/v1/guardians/${mine.guardian_link_id}`, { method: "DELETE" }))} />
              ) : null}

              {mine.status === "ACTIVE" && !editing && confirm !== "remove" ? (
                <Row gap={S[1]} style={{ marginTop: S[3] }}>
                  <Btn variant="secondary" label={t("g.change")} onPress={startEdit} style={{ flex: 1 }} full={false} small />
                  <Btn variant="ghost" label={t("g.remove")} onPress={() => setConfirm("remove")} style={{ flex: 1.4 }} full={false} small />
                </Row>
              ) : null}

              {editing ? (
                <View style={{ marginTop: S[3] }}>
                  <Label>{t("g.mode")}</Label>
                  <Segmented value={mode} onChange={(m) => { setMode(m); setConfirm(null); }} options={modeOpts} />
                  <View style={{ height: S[2] }} />
                  {mode === "co_approve" ? <Field label={t("g.threshold")} value={threshold} onChangeText={(v) => { setThreshold(v.replace(/\D/g, "").slice(0, 8)); setConfirm(null); }} keyboardType="number-pad" /> : null}
                  {confirm === "loosen" ? <Banner tone="warning" style={{ marginBottom: S[2] }}>{t("guardian.remove.delay", { name })}</Banner> : null}
                  <Row gap={S[1]}>
                    <Btn variant="ghost" label={confirm === "loosen" ? t("g.keep") : t("common.cancel")} onPress={() => { setEditing(false); setConfirm(null); }} style={{ flex: 1 }} full={false} small />
                    <Btn label={confirm === "loosen" ? t("g.confirm") : t("g.save")} onPress={saveEdit} loading={busy} style={{ flex: 1 }} full={false} small />
                  </Row>
                </View>
              ) : null}

              {confirm === "remove" ? (
                <View style={{ marginTop: S[3] }}>
                  <Banner tone="warning" style={{ marginBottom: S[2] }}>{t("guardian.remove.delay", { name })}</Banner>
                  <Row gap={S[1]}>
                    <Btn label={t("g.keep")} onPress={() => setConfirm(null)} style={{ flex: 1.3 }} full={false} small />
                    <Btn variant="ghost" label={t("g.confirm")} loading={busy} style={{ flex: 1 }} full={false} small
                      onPress={() => act(() => api(`/v1/guardians/${mine.guardian_link_id}`, { method: "DELETE" }), () => setConfirm(null))} />
                  </Row>
                </View>
              ) : null}
            </Card>
          ) : null}
        </View>
      ) : null}

      {tab === "protect" && data ? (
        <View style={{ gap: S[2] }}>
          {data.invitations.length > 0 ? (
            <View>
              <Label>{t("g.invitations")}</Label>
              <View style={{ gap: S[1] }}>
                {data.invitations.map((inv: any) => (
                  <Card key={inv.guardian_link_id} tone={C.warning}>
                    <T v="heading">{t("g.inviteFrom", { name: inv.protected_name })}</T>
                    <Chip style={{ marginTop: 6 }} label={t(`g.mode.${inv.mode}`)} icon="shield" />
                    <T v="caption" color={C.textSecondary} style={{ marginTop: 6 }}>{t(`g.mode.${inv.mode}.help`)}</T>
                    <Row gap={S[1]} style={{ marginTop: S[2] }}>
                      <Btn variant="ghost" label={t("g.decline")} disabled={busy} style={{ flex: 1 }} full={false} small
                        onPress={() => act(() => api(`/v1/guardians/${inv.guardian_link_id}/decline`, { method: "POST" }))} />
                      <Btn label={t("g.accept")} loading={busy} style={{ flex: 1 }} full={false} small
                        onPress={() => act(() => api(`/v1/guardians/${inv.guardian_link_id}/accept`, { method: "POST" }))} />
                    </Row>
                  </Card>
                ))}
              </View>
            </View>
          ) : null}

          <View>
            <Label>{t("g.wards")}</Label>
            {data.as_guardian.length === 0 ? <T v="body" color={C.textMuted}>{t("g.noWards")}</T> : null}
            <View style={{ gap: S[1] }}>
              {data.as_guardian.map((w: any) => (
                <Card key={w.guardian_link_id} pad={S[2]}>
                  <Row gap={S[2]}>
                    <Avatar text={initials(w.protected_name)} size={38} />
                    <View style={{ flex: 1 }}>
                      <T v="body" weight={600}>{w.protected_name}</T>
                      <T v="caption" color={C.textMuted}>{t(`g.mode.${w.mode}`)}{w.status === "REMOVAL_PENDING" ? ` · ${t("g.removal", { time: dateTime(w.change_effective_at, locale) })}` : ""}</T>
                    </View>
                  </Row>
                </Card>
              ))}
            </View>
          </View>

          <View>
            <Label>{t("g.requests")}</Label>
            {requests.length === 0 ? <Empty icon="inbox" text={t("g.noRequests")} /> : null}
            <View style={{ gap: S[1] }}>
              {requests.map((r) => {
                const open = r.status === "PENDING";
                const tone = open ? (r.kind === "APPROVAL" ? C.warning : C.accentCyan) : C.border;
                return (
                  <Card key={r.request_id} pad={S[2]} tone={tone} onPress={() => nav.push("request", { request_id: r.request_id })}>
                    <Row style={{ justifyContent: "space-between" }} gap={S[1]} align="flex-start">
                      <View style={{ flex: 1 }}>
                        <T v="body" weight={600}>{t("g.req.title", { name: r.summary?.protected_name || "", amount: money(r.summary?.amount) })}</T>
                        <T v="caption" color={C.textMuted} numberOfLines={1}>{r.summary?.payee?.registered_name} · {dateTime(r.created_at, locale)}</T>
                      </View>
                      <Icon name="chevron-right" color={C.textMuted} />
                    </Row>
                    <Row gap={6} wrap style={{ marginTop: 6 }}>
                      <Chip label={t(`g.req.kind.${r.kind}`)} color={r.kind === "APPROVAL" ? C.warning : C.accentCyan} bg={r.kind === "APPROVAL" ? C.warningSubtle : "rgba(56, 214, 255, 0.10)"} />
                      <Chip label={t(`g.req.status.${r.status}`)} color={r.status === "APPROVED" ? C.safe : r.status === "DENIED" ? C.block : C.neutralChipText}
                        bg={r.status === "APPROVED" ? C.safeSubtle : r.status === "DENIED" ? C.blockSubtle : C.neutralChip} />
                    </Row>
                  </Card>
                );
              })}
            </View>
          </View>
        </View>
      ) : null}
    </Screen>
  );
}

// ------------------------------------------------------------------ P-14 request detail --
export function GuardianRequest({ route }: { route: Route }) {
  const { t, locale } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const id = route.params?.request_id;
  const [r, setR] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const expires = r?.expires_at ? Date.parse(r.expires_at) : null;
  const left = useCountdown(r?.status === "PENDING" ? expires : null);

  const load = useCallback(async () => {
    try {
      setR(await api(`/v1/guardian/requests/${id}`));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [id]);

  useEffect(() => {
    load();
    const off = onEvent("guardian.response", load);
    const timer = setInterval(load, 5000);
    return () => {
      off();
      clearInterval(timer);
    };
  }, [load]);

  const respond = async (decision: "approve" | "deny" | "dismiss") => {
    setBusy(decision);
    setError(null);
    try {
      await api(`/v1/guardian/requests/${id}/respond`, { body: { decision } });
      await load();
      refreshMe(dispatch);
    } catch (e) {
      setError(e);
      load();
    } finally {
      setBusy(null);
    }
  };

  const s = r?.summary;
  const open = r?.status === "PENDING";
  const tierLook = TIER[(s?.tier as keyof typeof TIER) || "WARNING"] || TIER.WARNING;
  const who = (s?.protected_name || "").split(" ")[0];

  return (
    <Screen title={r ? t(`g.req.kind.${r.kind}`) : ""} onBack={() => nav.pop()}
      footer={r && open ? (
        r.kind === "APPROVAL" ? (
          <Row gap={S[1]}>
            <Btn variant="danger" label={t("g.req.deny")} loading={busy === "deny"} disabled={!!busy} onPress={() => respond("deny")} style={{ flex: 1 }} full={false} />
            <Btn variant="safe" label={t("g.req.approve")} loading={busy === "approve"} disabled={!!busy} onPress={() => respond("approve")} style={{ flex: 1 }} full={false} />
          </Row>
        ) : (
          <Btn variant="secondary" label={t("g.req.dismiss")} loading={busy === "dismiss"} onPress={() => respond("dismiss")} />
        )
      ) : undefined}>
      <ErrorNote error={error} onRetry={load} />
      {!r && !error ? <Loading /> : null}
      {r && s ? (
        <View style={{ gap: S[2] }}>
          <T v="title">{t("g.req.title", { name: s.protected_name, amount: money(s.amount) })}</T>
          <Row gap={6} wrap>
            <Chip label={t(`g.req.status.${r.status}`)} color={r.status === "APPROVED" ? C.safe : r.status === "DENIED" ? C.block : C.neutralChipText}
              bg={r.status === "APPROVED" ? C.safeSubtle : r.status === "DENIED" ? C.blockSubtle : C.neutralChip} />
            {r.created_at ? <Chip label={dateTime(r.created_at, locale)} icon="clock" /> : null}
          </Row>
          {open && r.kind === "APPROVAL" && expires ? (
            <Banner tone="warning" icon="clock">
              <Row style={{ justifyContent: "space-between" }}>
                <T v="body">{t("g.req.timeLeft")}</T>
                <T v="heading" color={C.warning}>{duration(left, locale)}</T>
              </Row>
            </Banner>
          ) : null}
          <Card>
            <Label>{t("g.req.paying")}</Label>
            <T v="heading">{s.payee?.registered_name}</T>
            <Mono>{s.payee?.vpa}</Mono>
            <T v="amount" style={{ marginTop: S[1] }}>₹{money(s.amount)}</T>
          </Card>
          <Card tone={tierLook.color}>
            <Label>{t("g.req.why")}</Label>
            {s.scam_family?.headline ? <T v="title">{s.scam_family.headline}</T> : null}
            {s.scam_family?.body ? <T v="bodyLarge" color={C.textSecondary} style={{ marginTop: 4, marginBottom: S[2] }}>{s.scam_family.body}</T> : null}
            <Reasons reasons={(s.reasons || []).map((m: string) => ({ message: m }))} />
          </Card>
          <Banner tone="info" icon="phone">{t("g.req.callTip", { name: who })}</Banner>
          <T v="caption" color={C.textMuted} center>{t("g.req.privacy")}</T>
        </View>
      ) : null}
    </Screen>
  );
}
