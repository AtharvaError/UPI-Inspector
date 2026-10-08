// P-09 PIN entry (mock), P-10 Result, P-20 Waiting for guardian.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { api } from "../api";
import { Banner, Btn, Card, ErrorNote, Icon, IconCircle, Mono, Ring, Row, Screen, T, useCountdown } from "../components/ui";
import { finishFlow, refreshMe, sendOutcome } from "../flow";
import { duration, money, useT } from "../i18n";
import { useBackGuard, useNav } from "../nav";
import { useSecureScreen } from "../session";
import { onEvent } from "../socket";
import { actions, useAppDispatch, useAppSelector } from "../store";
import { C, R, S, font } from "../../theme";

// ------------------------------------------------------------------ P-09 PIN (mock) --
const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "<"];

export function Pin() {
  const { t } = useT();
  const nav = useNav();
  const flow = useAppSelector((s) => s.flow);
  const d = flow.decision;
  const [n, setN] = useState(0);            // how many digits were entered; the digits themselves are never kept
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  useSecureScreen("upi-pin");

  const cancel = useCallback(() => {
    if (d) sendOutcome(d.decision_id, "ABORTED");
    finishFlow(nav, { kind: "cancelled" });
    return true;
  }, [d, nav]);
  useBackGuard(cancel);

  useEffect(() => {
    if (!d || !flow.payee) nav.home();
  }, [d, flow.payee, nav]);
  if (!d || !flow.payee) return null;

  const press = (k: string) => {
    if (busy) return;
    if (k === "<") setN((x) => Math.max(0, x - 1));
    else if (k) setN((x) => Math.min(6, x + 1));
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      // This is a mock PSP: any digits are accepted, so a placeholder is sent and what was typed stays on the device.
      const res = await api("/mock-psp/v1/pay", { body: { txn_id: d.txn_id, decision_id: d.decision_id, pin: "0".repeat(Math.max(4, n)) } });
      if (res.status === "SUCCESS") finishFlow(nav, { kind: "success", reference: res.reference, amount: res.amount, payeeName: res.payee_name || flow.payee!.registered_name });
      // a decision clears a payment only for a while; after that it has to be checked again
      else finishFlow(nav, { kind: "failed", message: res.expired ? t("pin.expired") : res.reason });
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <Screen title={t("pin.title")} onBack={cancel} scroll={false}>
      <Banner tone="warning" icon="alert-triangle">{t("pin.demo")}</Banner>
      <View style={{ alignItems: "center", marginTop: S[4] }}>
        <T v="caption" color={C.textMuted}>{t("pin.paying", { name: flow.payee.registered_name || "" })}</T>
        <T v="amount">₹{money(Number(flow.amount))}</T>
        <Row gap={S[2]} style={{ marginTop: S[4], height: 20 }}>
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <View key={i} style={{ width: 14, height: 14, borderRadius: 7, borderWidth: 1.5, borderColor: i < n ? C.brand : C.borderStrong, backgroundColor: i < n ? C.brand : "transparent" }} />
          ))}
        </Row>
        <T v="caption" color={C.textMuted} style={{ marginTop: S[1] }}>{t("pin.any")}</T>
      </View>
      <ErrorNote error={error} />
      <View style={{ flex: 1 }} />
      <View style={{ flexDirection: "row", flexWrap: "wrap", justifyContent: "center", rowGap: S[1] }}>
        {KEYS.map((k, i) => (
          <View key={i} style={{ width: "33.33%", alignItems: "center" }}>
            {k ? (
              <Pressable accessibilityRole="button" accessibilityLabel={k === "<" ? "delete" : k} onPress={() => press(k)}
                style={({ pressed }) => ({ width: 72, height: 56, borderRadius: R.lg, alignItems: "center", justifyContent: "center", backgroundColor: pressed ? C.surfaceHover : C.surface, borderWidth: 1, borderColor: C.border })}>
                {k === "<" ? <Icon name="delete" color={C.textSecondary} /> : <Text style={[font(600), { color: C.text, fontSize: 24 }]}>{k}</Text>}
              </Pressable>
            ) : null}
          </View>
        ))}
      </View>
      <View style={{ height: S[2] }} />
      <Btn label={t("amount.payBtn", { amount: money(Number(flow.amount)) })} icon="check" disabled={n < 4} loading={busy} onPress={submit} />
    </Screen>
  );
}

// ------------------------------------------------------------------ P-10 Result --
export function Result() {
  const { t } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const flow = useAppSelector((s) => s.flow);
  const r = flow.result;

  const done = useCallback(() => {
    dispatch(actions.flow.cleared());
    refreshMe(dispatch);
    nav.home();
    return true;
  }, [dispatch, nav]);
  useBackGuard(done);

  useEffect(() => {
    if (!r) nav.home();
    else refreshMe(dispatch);
  }, [r, nav, dispatch]);
  if (!r) return null;

  const look = r.kind === "success" ? { icon: "check-circle" as const, color: C.safe, bg: C.safeSubtle, title: t("result.success") }
    : r.kind === "cancelled" ? { icon: "slash" as const, color: C.textSecondary, bg: C.neutralChip, title: t("result.cancelled") }
    : { icon: "alert-circle" as const, color: C.block, bg: C.blockSubtle, title: t("result.failed") };

  return (
    <Screen scroll={false} footer={<>
      {r.kind === "failed" ? <Btn variant="secondary" label={t("common.retry")} onPress={() => { dispatch(actions.flow.patched({ result: null, decision: null, active: true })); nav.replace("amount"); }} /> : null}
      <Btn label={t("common.done")} onPress={done} />
    </>}>
      <View style={{ flex: 1, justifyContent: "center" }}>
        <IconCircle icon={look.icon} color={look.color} bg={look.bg} size={96} />
        <T v="title" center style={{ marginTop: S[3] }}>{look.title}</T>
        {r.message ? <T v="bodyLarge" color={C.textSecondary} center style={{ marginTop: S[1] }}>{r.message}</T> : null}
        {r.kind === "success" ? (
          <Card style={{ marginTop: S[4] }}>
            <T v="amount" center>₹{money(r.amount ?? Number(flow.amount))}</T>
            <Row style={{ justifyContent: "space-between", marginTop: S[2] }} align="flex-start" gap={S[2]}>
              <T v="caption" color={C.textMuted}>{t("result.to")}</T>
              <View style={{ flex: 1, alignItems: "flex-end" }}>
                <T v="body" weight={600}>{r.payeeName}</T>
                <Mono size={12.5}>{r.payeeVpa}</Mono>
              </View>
            </Row>
            <Row style={{ justifyContent: "space-between", marginTop: S[1] }}>
              <T v="caption" color={C.textMuted}>{t("result.ref")}</T>
              <Mono size={12.5}>{r.reference}</Mono>
            </Row>
          </Card>
        ) : null}
      </View>
    </Screen>
  );
}

// ------------------------------------------------------------------ P-20 Waiting for guardian --
export function Wait() {
  const { t, locale } = useT();
  const nav = useNav();
  const flow = useAppSelector((s) => s.flow);
  const d = flow.decision;
  const g = d?.guardian;
  const name = g?.guardian_name || "";
  const expires = g?.expires_at ? Date.parse(g.expires_at) : null;
  const left = useCountdown(expires);
  const total = useRef(expires ? Math.max(1, (expires - Date.now()) / 1000) : 1).current;
  const settled = useRef(false);

  const settle = useCallback((status: string) => {
    if (settled.current || !d) return;
    if (status === "APPROVED") {
      settled.current = true;
      nav.replace("pin");
    } else if (status === "DENIED") {
      settled.current = true;
      finishFlow(nav, { kind: "cancelled", message: t("guardian.denied", { name }) });
    } else if (status === "EXPIRED") {
      settled.current = true;
      finishFlow(nav, { kind: "cancelled", message: t("guardian.expired", { name }) });
    }
  }, [d, nav, t, name]);

  useEffect(() => {
    if (!g) return;
    const check = () => api(`/v1/guardian/requests/${g.request_id}`).then((r) => settle(r.status)).catch(() => undefined);
    check();
    const id = setInterval(check, 3000);
    const off = onEvent("guardian.response", (e) => { if (e?.request_id === g.request_id) settle(e.status); });
    return () => {
      clearInterval(id);
      off();
    };
  }, [g, settle]);

  const cancel = useCallback(() => {
    if (d && !settled.current) {
      settled.current = true;
      sendOutcome(d.decision_id, "ABORTED");
      finishFlow(nav, { kind: "cancelled" });
    }
    return true;
  }, [d, nav]);
  useBackGuard(cancel);

  useEffect(() => {
    if (!d || !g) nav.home();
  }, [d, g, nav]);
  if (!d || !g) return null;

  return (
    <Screen scroll={false} footer={<Btn variant="ghost" label={t("cancel.payment")} onPress={cancel} />}>
      <View style={{ flex: 1, justifyContent: "center" }}>
        <Ring seconds={Math.max(0.1, left || total)} total={total} color={C.brand}>
          <Icon name="shield" size={34} color={C.brandHover} />
          <T v="heading" style={{ marginTop: 4 }}>{duration(left, locale)}</T>
        </Ring>
        <T v="title" center style={{ marginTop: S[4] }}>{t("guardian.waiting", { name })}</T>
        <T v="bodyLarge" color={C.textSecondary} center style={{ marginTop: S[1] }}>{t("wait.hint")}</T>
        <Card pad={S[2]} style={{ marginTop: S[4] }}>
          <Row style={{ justifyContent: "space-between" }} align="flex-start" gap={S[2]}>
            <View style={{ flex: 1 }}>
              <T v="heading" numberOfLines={2}>{flow.payee?.registered_name}</T>
              <Mono size={12.5}>{flow.payee?.vpa}</Mono>
            </View>
            <T v="title">₹{money(Number(flow.amount))}</T>
          </Row>
        </Card>
      </View>
    </Screen>
  );
}
