// Demo-only staging: a fake incoming call (P-18), a simulated SMS banner (P-19), and in-app notices.
// None of this is part of the product; each piece carries a visible "Demo" mark.
import React, { useEffect, useState } from "react";
import { Pressable, View } from "react-native";
import { api } from "../api";
import { resolvePayee, startFlow } from "../flow";
import { mmss, useT } from "../i18n";
import { useNav } from "../nav";
import { actions, Sms, useAppDispatch, useAppSelector } from "../store";
import { C, R, S } from "../../theme";
import { tokens } from "../../theme/tokens";
import { Banner, Btn, Chip, FadeIn, Icon, IconBtn, Link, Row, T } from "./ui";

async function endCall() {
  try {
    await api("/v1/demo/my-call/end", { method: "POST" });
  } catch {
    /* the bar is cleared locally either way */
  }
}

export function CallOverlay() {
  const { t } = useT();
  const dispatch = useAppDispatch();
  const call = useAppSelector((s) => s.overlay.call);
  if (!call || call.phase !== "ringing") return null;
  return (
    <View accessibilityViewIsModal style={{ position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: C.bg, zIndex: 50, padding: S[5], justifyContent: "space-between" }}>
      <View style={{ alignItems: "center", marginTop: S[6] }}>
        <Chip label={t("demo.badge")} color={C.warning} bg={C.warningSubtle} style={{ alignSelf: "center" }} />
        <T v="caption" color={C.textMuted} style={{ marginTop: S[4], textTransform: "uppercase", letterSpacing: 1 }}>{t("call.incoming")}</T>
        <View style={{ width: 104, height: 104, borderRadius: 52, backgroundColor: C.surfaceRaised, borderWidth: 1, borderColor: C.borderStrong, alignItems: "center", justifyContent: "center", marginTop: S[4] }}>
          <Icon name="user" size={46} color={C.textSecondary} />
        </View>
        <T v="display" center style={{ marginTop: S[4] }}>{call.caller_label}</T>
        <T v="bodyLarge" color={C.textSecondary} center>{t("call.unknown")}</T>
        {(call.caller_risk || 0) >= 2 ? (
          <View style={{ marginTop: S[3], alignSelf: "stretch" }}>
            <Banner tone="block" icon="alert-octagon" title={t("call.flaggedTitle")}>{t("call.flaggedBody")}</Banner>
          </View>
        ) : null}
      </View>
      <View>
        <Row style={{ justifyContent: "space-around", marginBottom: S[4] }}>
          <View style={{ alignItems: "center", gap: S[1] }}>
            <Pressable accessibilityRole="button" accessibilityLabel={t("call.decline")}
              onPress={() => { dispatch(actions.overlay.callCleared()); endCall(); }}
              style={{ width: 72, height: 72, borderRadius: 36, backgroundColor: C.block, alignItems: "center", justifyContent: "center" }}>
              <Icon name="phone-off" size={28} color={C.blockOn} />
            </Pressable>
            <T v="caption" color={C.textSecondary}>{t("call.decline")}</T>
          </View>
          <View style={{ alignItems: "center", gap: S[1] }}>
            <Pressable accessibilityRole="button" accessibilityLabel={t("call.accept")}
              onPress={() => dispatch(actions.overlay.callAccepted())}
              style={{ width: 72, height: 72, borderRadius: 36, backgroundColor: C.safe, alignItems: "center", justifyContent: "center" }}>
              <Icon name="phone" size={28} color={C.safeOn} />
            </Pressable>
            <T v="caption" color={C.textSecondary}>{t("call.accept")}</T>
          </View>
        </Row>
        <T v="caption" color={C.textMuted} center>{t("call.demo")}</T>
      </View>
    </View>
  );
}

export function InCallBar() {
  const { t } = useT();
  const dispatch = useAppDispatch();
  const call = useAppSelector((s) => s.overlay.call);
  const [, setTick] = useState(0);
  const active = !!call && call.phase === "active";
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
  if (!call || !active) return null;
  const secs = Math.max(0, Date.now() / 1000 - call.started_at);
  const flagged = (call.caller_risk || 0) >= 2;
  return (
    <Row style={{ backgroundColor: flagged ? C.warning : C.safe, paddingHorizontal: S[2], paddingVertical: 6, justifyContent: "space-between" }} gap={S[1]}>
      <Row gap={S[1]} style={{ flex: 1 }}>
        <Icon name={flagged ? "alert-triangle" : "phone-call"} size={16} color={flagged ? C.warningOn : C.safeOn} />
        <T v="caption" color={flagged ? C.warningOn : C.safeOn} weight={700} numberOfLines={1} style={{ flexShrink: 1 }}>
          {t("call.onCall")} · {call.caller_label} · {mmss(secs)}
        </T>
      </Row>
      <Pressable accessibilityRole="button" accessibilityLabel={t("call.end")}
        onPress={() => { dispatch(actions.overlay.callCleared()); endCall(); }}
        style={{ backgroundColor: C.block, borderRadius: R.pill, paddingHorizontal: 12, paddingVertical: 4, flexDirection: "row", alignItems: "center", gap: 4 }}>
        <Icon name="phone-off" size={13} color={C.blockOn} />
        <T v="caption" color={C.blockOn} weight={700}>{t("call.end")}</T>
      </Pressable>
    </Row>
  );
}

const TIER_SCREENS = ["stepup", "hold", "decline", "pin", "result", "wait"];
const reportedCalls = new Set<string>();      // calls this person has already reported, so they are not asked twice

/** Report the caller of the current or last call. The server works out who that was; the app never holds the number. */
export async function reportCaller(callId?: string, decisionId?: string) {
  const res = await api("/v1/callers/report", { body: { call_id: callId || null, decision_id: decisionId || null, category: "scam" } });
  if (callId) reportedCalls.add(callId);
  return res;
}

/** F-31: while a number other people have reported is on the line, every screen carries this warning. */
export function CallRiskBanner() {
  const { t } = useT();
  const dispatch = useAppDispatch();
  const call = useAppSelector((s) => s.overlay.call);
  const screen = useAppSelector((s) => s.nav.stack[s.nav.stack.length - 1]?.name);
  const [, setReported] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!call || call.phase !== "active" || (call.caller_risk || 0) < 2) return null;
  // The screens that stop or finish a payment carry their own warning and need the room.
  if (screen && TIER_SCREENS.includes(screen)) return null;
  const done = reportedCalls.has(call.call_id);
  const report = async () => {
    setBusy(true);
    try {
      await reportCaller(call.call_id);
      setReported(call.call_id);
      dispatch(actions.overlay.toast({ tone: "safe", text: t("call.reported") }));
    } catch {
      /* the banner stays; the payer can try again */
    } finally {
      setBusy(false);
    }
  };
  return (
    <View accessibilityRole="alert" style={{ backgroundColor: C.blockSubtle, borderBottomWidth: 1, borderBottomColor: C.block, paddingHorizontal: S[2], paddingVertical: S[1] }}>
      <Row gap={S[1]} align="flex-start">
        <Icon name="alert-octagon" size={18} color={C.block} style={{ marginTop: 2 }} />
        <View style={{ flex: 1 }}>
          <T v="body" weight={700} color={C.block}>{t("call.flaggedTitle")}</T>
          <T v="caption" color={C.text}>{t("call.flaggedBody")}</T>
          {done ? null : (
            <View style={{ alignItems: "flex-start", marginTop: 2 }}>
              <Link label={busy ? t("common.loading") : t("call.report")} icon="flag" color={C.block} onPress={busy ? undefined : report} />
            </View>
          )}
        </View>
      </Row>
    </View>
  );
}

function SmsCard({ sms }: { sms: Sms }) {
  const { t } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const [busy, setBusy] = useState(false);
  const dismiss = () => dispatch(actions.overlay.smsDismissed(sms.id));
  useEffect(() => {
    if (sms.kind !== "otp") return;
    const id = setTimeout(dismiss, 25000);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sms.id]);

  const openLink = async () => {
    if (!sms.link) return;
    setBusy(true);
    try {
      const info = await resolvePayee(sms.link.vpa, sms.link.name || undefined);
      if (info.exists) {
        startFlow(dispatch, { payee: info, claimedName: sms.link.name || "", entryMethod: "link", amount: sms.link.amount ? String(sms.link.amount) : "", note: sms.link.note || "" });
        dismiss();
        nav.push("amount");
      }
    } catch {
      /* stay on the banner; the presenter can tap again */
    } finally {
      setBusy(false);
    }
  };

  return (
    <FadeIn from={-12} style={{ backgroundColor: C.surfaceRaised, borderRadius: R.lg, borderWidth: 1, borderColor: C.borderStrong, padding: S[2], marginBottom: S[1], ...({ boxShadow: tokens.shadow.card } as any) }}>
      <Row gap={S[1]} align="flex-start">
        <View style={{ width: 32, height: 32, borderRadius: 8, backgroundColor: C.brandSubtle, alignItems: "center", justifyContent: "center" }}>
          <Icon name="message-square" size={16} color={C.brandHover} />
        </View>
        <View style={{ flex: 1 }}>
          <Row style={{ justifyContent: "space-between" }}>
            <T v="caption" color={C.textSecondary}>{sms.sender || t("sms.new")} · {t("demo.badge")}</T>
          </Row>
          <T v="body" selectable numberOfLines={sms.kind === "otp" ? 3 : 6}>{sms.text}</T>
        </View>
        <IconBtn icon="x" label={t("common.close")} onPress={dismiss} size={32} color={C.textMuted} />
      </Row>
      {sms.kind === "message" ? (
        <Row gap={S[1]} wrap style={{ marginTop: S[1] }}>
          <Btn small full={false} variant="secondary" icon="search" label={t("sms.check")} onPress={() => { dismiss(); nav.tab("message", { text: sms.text, link: sms.link }); }} />
          {sms.link ? <Btn small full={false} variant="ghost" icon="external-link" label={t("sms.openLink")} loading={busy} onPress={openLink} /> : null}
        </Row>
      ) : null}
    </FadeIn>
  );
}

export function SmsStack() {
  const list = useAppSelector((s) => s.overlay.sms);
  if (!list.length) return null;
  return (
    <View style={{ position: "absolute", top: S[1], left: S[1], right: S[1], zIndex: 40 }}>
      {list.map((m) => <SmsCard key={m.id} sms={m} />)}
    </View>
  );
}

export function ToastHost() {
  const nav = useNav();
  const dispatch = useAppDispatch();
  const toast = useAppSelector((s) => s.overlay.toast);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => dispatch(actions.overlay.toastCleared()), 9000);
    return () => clearTimeout(id);
  }, [toast, dispatch]);
  if (!toast) return null;
  const color = { info: C.accentCyan, safe: C.safe, warning: C.warning, block: C.block }[toast.tone];
  return (
    <FadeIn key={toast.id} from={12} style={{ position: "absolute", left: S[2], right: S[2], bottom: S[3], zIndex: 45, backgroundColor: C.surfaceRaised, borderRadius: R.lg, borderWidth: 1, borderColor: color, padding: S[2] }}>
      <Row gap={S[1]}>
        <Icon name="bell" size={18} color={color} />
        <T v="body" style={{ flex: 1 }}>{toast.text}</T>
        {toast.action ? (
          <Btn small full={false} variant="secondary" label={toast.action.label}
            onPress={() => { const a = toast.action!; dispatch(actions.overlay.toastCleared()); if (a.screen === "request") nav.push(a.screen, a.params); else nav.tab(a.screen, a.params); }} />
        ) : null}
        <IconBtn icon="x" label="Close" size={32} color={C.textMuted} onPress={() => dispatch(actions.overlay.toastCleared())} />
      </Row>
    </FadeIn>
  );
}
