// P-11 Held payments (F-23). A hold never sends money on a timer: when it is ready the payer is asked
// again, and saying yes runs a fresh assessment.
import React, { useCallback, useEffect, useState } from "react";
import { View } from "react-native";
import { api } from "../api";
import { Banner, Btn, Card, Chip, Empty, ErrorNote, Label, Loading, Mono, Row, Screen, T } from "../components/ui";
import { assessPayment, refreshMe, resolvePayee, routeDecision, startFlow } from "../flow";
import { dateTime, duration, money, useT } from "../i18n";
import { useNav } from "../nav";
import { useAppDispatch, useAppSelector } from "../store";
import { C, S } from "../../theme";

export default function Holds() {
  const { t, locale } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const tick = useAppSelector((s) => s.overlay.tick);
  const [holds, setHolds] = useState<any[] | null>(null);
  const [error, setError] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [, setNow] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await api("/v1/holds");
      setHolds(r.holds);
      setError(null);
      refreshMe(dispatch);
    } catch (e) {
      setError(e);
      setHolds((h) => h || []);
    }
  }, [dispatch]);

  useEffect(() => {
    load();
  }, [load, tick]);

  // tick the countdowns, and re-fetch while something is still paused so "ready" appears on its own
  useEffect(() => {
    const a = setInterval(() => setNow((x) => x + 1), 1000);
    const b = setInterval(() => { if ((holds || []).some((h) => h.status === "HELD")) load(); }, 5000);
    return () => {
      clearInterval(a);
      clearInterval(b);
    };
  }, [holds, load]);

  const cancel = async (h: any) => {
    setBusy(h.hold_id);
    try {
      await api(`/v1/holds/${h.hold_id}/cancel`, { method: "POST" });
    } catch (e) {
      setError(e);
    }
    setBusy(null);
    load();
  };

  const confirm = async (h: any) => {
    setBusy(h.hold_id);
    setError(null);
    try {
      const c = await api(`/v1/holds/${h.hold_id}/confirm`, { method: "POST" });
      const info = await resolvePayee(c.payee.vpa, c.payee.claimed_name || undefined);
      startFlow(dispatch, { payee: info, claimedName: c.payee.claimed_name || "", entryMethod: "typed", amount: String(c.amount), note: c.note || "", parentDecisionId: c.parent_decision_id });
      const d = await assessPayment({ timeOnPayScreenS: 20, amountEdits: 0 });
      if (d.action === "WARN") nav.push("amount");
      else routeDecision(nav, d);
    } catch (e) {
      setError(e);
      load();
    } finally {
      setBusy(null);
    }
  };

  const open = (holds || []).filter((h) => h.status === "HELD" || h.status === "READY");
  const past = (holds || []).filter((h) => h.status !== "HELD" && h.status !== "READY");

  return (
    <Screen title={t("nav.holds")}>
      <T v="body" color={C.textSecondary} style={{ marginBottom: S[2] }}>{t("holds.explain")}</T>
      <ErrorNote error={error} onRetry={load} />
      {holds === null ? <Loading /> : null}
      {holds && open.length === 0 ? <Empty icon="clock" text={t("holds.empty")} /> : null}
      <View style={{ gap: S[2] }}>
        {open.map((h) => {
          const ready = h.status === "READY";
          const left = Math.max(0, (Date.parse(h.release_at) - Date.now()) / 1000);
          const name = h.payee_name || h.payee_vpa;
          return (
            <Card key={h.hold_id} tone={ready ? C.warning : C.stepup}>
              <Row style={{ justifyContent: "space-between" }} align="flex-start" gap={S[2]}>
                <View style={{ flex: 1 }}>
                  <T v="heading" numberOfLines={2}>{name}</T>
                  <Mono size={12.5}>{h.payee_vpa}</Mono>
                </View>
                <T v="title">₹{money(h.amount)}</T>
              </Row>
              <Chip style={{ marginTop: S[1] }} icon={ready ? "bell" : "pause-circle"} color={ready ? C.warning : C.stepup} bg={ready ? C.warningSubtle : C.stepupSubtle} label={t(`holds.status.${h.status}`)} />
              {h.family?.headline ? <T v="body" color={C.textSecondary} style={{ marginTop: S[1] }}>{h.family.headline}</T> : null}
              {ready ? (
                <View style={{ marginTop: S[2], gap: S[1] }}>
                  <T v="bodyLarge" weight={600}>{t("hold.ready", { amount: money(h.amount), name })}</T>
                  <Btn variant="secondary" label={t("holds.yes")} loading={busy === h.hold_id} onPress={() => confirm(h)} />
                  <Btn label={t("holds.no")} disabled={busy === h.hold_id} onPress={() => cancel(h)} />
                </View>
              ) : (
                <View style={{ marginTop: S[2], gap: S[1] }}>
                  <Banner tone="stepup" icon="clock">{t("holds.until", { time: duration(left, locale) })}</Banner>
                  <Btn variant="ghost" label={t("cancel.payment")} loading={busy === h.hold_id} onPress={() => cancel(h)} small />
                </View>
              )}
            </Card>
          );
        })}
      </View>
      {past.length > 0 ? (
        <View style={{ marginTop: S[4] }}>
          <Label>{t("holds.earlier")}</Label>
          <View style={{ gap: S[1] }}>
            {past.slice(0, 10).map((h) => (
              <Card key={h.hold_id} pad={S[2]}>
                <Row style={{ justifyContent: "space-between" }} gap={S[2]}>
                  <View style={{ flex: 1 }}>
                    <T v="body" weight={600} numberOfLines={1}>{h.payee_name || h.payee_vpa}</T>
                    <T v="caption" color={C.textMuted}>{t(`holds.status.${h.status}`)} · {dateTime(h.created_at, locale)}</T>
                  </View>
                  <T v="body" color={C.textSecondary}>₹{money(h.amount)}</T>
                </Row>
              </Card>
            ))}
          </View>
        </View>
      ) : null}
    </Screen>
  );
}
