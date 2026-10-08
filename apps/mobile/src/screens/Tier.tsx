// P-07 Hold and P-08 Decline: full-screen, clearly different from the normal flow, always with a way out.
import React, { useCallback, useEffect, useState } from "react";
import { Linking, ScrollView, View } from "react-native";
import { api, ApiErr } from "../api";
import { reportCaller } from "../components/Overlays";
import { Banner, Btn, Card, ErrorNote, FadeIn, Field, IconBtn, IconCircle, Link, Mono, Reasons, Row, T, useCountdown } from "../components/ui";
import { refreshMe } from "../flow";
import { clock, dateTime, duration, money, useT } from "../i18n";
import { useBackGuard, useNav } from "../nav";
import { speak, stopSpeaking } from "../speech";
import { actions, Route, useAppDispatch, useAppSelector } from "../store";
import { C, M, S, TIER } from "../../theme";

export function call1930() {
  Linking.openURL("tel:1930").catch(() => undefined);
}

// A warning is read aloud once per decision, not again each time the screen is returned to.
const spokenFor = new Set<string>();

function useSpokenWarning(text: string, key: string) {
  const { locale } = useT();
  const voice = useAppSelector((s) => !!s.session.me?.voice_warnings);
  const [speaking, setSpeaking] = useState(false);
  const say = useCallback(() => {
    speak(text, locale);
    setSpeaking(true);
  }, [text, locale]);
  const hush = useCallback(() => {
    stopSpeaking();
    setSpeaking(false);
  }, []);
  useEffect(() => {
    if (voice && text && key && !spokenFor.has(key)) {
      spokenFor.add(key);
      say();
    }
    return () => stopSpeaking();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return { speaking, say, hush };
}

function TierShell({ tone, icon, title, children, footer, voice }: {
  tone: { color: string; subtle: string }; icon: any; title: string; children: React.ReactNode; footer: React.ReactNode;
  voice: { speaking: boolean; say: () => void; hush: () => void } | null;
}) {
  const { t } = useT();
  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <View style={{ position: "absolute", top: 0, left: 0, right: 0, height: 6, backgroundColor: tone.color }} />
      <FadeIn from={0} duration={M.slow} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: S[4], paddingTop: S[5] }} keyboardShouldPersistTaps="handled">
          <Row style={{ justifyContent: "flex-end", minHeight: 44 }}>
            {voice ? <IconBtn icon={voice.speaking ? "volume-2" : "volume-x"} label={voice.speaking ? t("stepup.mute") : t("stepup.listen")} color={tone.color} bg={tone.subtle} onPress={voice.speaking ? voice.hush : voice.say} /> : null}
          </Row>
          <IconCircle icon={icon} color={tone.color} bg={tone.subtle} size={84} />
          <T v="display" center style={{ marginTop: S[2] }}>{title}</T>
          {children}
        </ScrollView>
        <View style={{ padding: S[3], gap: S[1], borderTopWidth: 1, borderTopColor: C.border }}>{footer}</View>
      </FadeIn>
    </View>
  );
}

function PaymentLine() {
  const flow = useAppSelector((s) => s.flow);
  if (!flow.payee) return null;
  return (
    <Card pad={S[2]} style={{ marginTop: S[3] }}>
      <Row style={{ justifyContent: "space-between" }} align="flex-start" gap={S[2]}>
        <View style={{ flex: 1 }}>
          <T v="heading" numberOfLines={2}>{flow.payee.registered_name}</T>
          <Mono size={12.5}>{flow.payee.vpa}</Mono>
        </View>
        <T v="title">₹{money(Number(flow.amount))}</T>
      </Row>
    </Card>
  );
}

/** F-31: after a payment made during or just after a call is stopped, ask once whether the caller was a scammer. */
function CallerReportCard({ decisionId }: { decisionId: string }) {
  const { t } = useT();
  const [status, setStatus] = useState<any>(null);
  const [state, setState] = useState<"ask" | "busy" | "done" | "hidden">("ask");
  useEffect(() => {
    api("/v1/users/me/call-status").then(setStatus).catch(() => undefined);
  }, []);
  if (!status || !(status.active || status.recent) || !status.can_report || status.caller?.known || state === "hidden") return null;
  if (status.reported_by_you || state === "done") return <Banner tone="safe" icon="check" style={{ marginTop: S[2] }}>{t("call.reported")}</Banner>;
  const send = async () => {
    setState("busy");
    try {
      await reportCaller(status.call_id, decisionId);
      setState("done");
    } catch {
      setState("ask");
    }
  };
  return (
    <Card style={{ marginTop: S[2] }}>
      <T v="heading">{t("call.reportQ")}</T>
      <T v="caption" color={C.textSecondary} style={{ marginTop: 2, marginBottom: S[2] }}>{t("call.reportHelp")}</T>
      <Row gap={S[1]}>
        <Btn small full={false} variant="secondary" icon="flag" label={t("call.report")} loading={state === "busy"} onPress={send} style={{ flex: 1.6 }} />
        <Btn small full={false} variant="ghost" label={t("common.no")} onPress={() => setState("hidden")} style={{ flex: 1 }} />
      </Row>
    </Card>
  );
}

/** F-33: one tap puts the payee on the payer's never-pay list. */
function NeverPay({ vpa }: { vpa: string }) {
  const { t } = useT();
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [error, setError] = useState<any>(null);
  if (state === "done") return <Banner tone="safe" icon="check" style={{ marginTop: S[2] }}>{t("block.added")}</Banner>;
  const add = async () => {
    setState("busy");
    setError(null);
    try {
      await api("/v1/users/me/blocks", { body: { vpa } });
      setState("done");
    } catch (e) {
      setError(e);
      setState("idle");
    }
  };
  return (
    <View style={{ marginTop: S[2] }}>
      <ErrorNote error={error} />
      <Btn variant="secondary" icon="slash" label={t("block.add")} loading={state === "busy"} onPress={add} />
    </View>
  );
}

// ------------------------------------------------------------------ P-07 Hold --
export function Hold() {
  const { t, locale } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const d = useAppSelector((s) => s.flow.decision);
  const fam = d?.scam_family;
  const voice = useSpokenWarning(fam ? `${t("tier.hold.title")}. ${fam.headline}. ${fam.body}` : "", d?.decision_id || "");
  const release = d?.hold ? Date.parse(d.hold.release_at) : null;
  const left = useCountdown(release);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  useBackGuard(useCallback(() => true, []));

  useEffect(() => {
    if (!d || !d.hold) nav.home();
    else refreshMe(dispatch);
  }, [d, nav, dispatch]);
  if (!d || !d.hold) return null;

  const leave = () => {
    voice.hush();
    dispatch(actions.flow.cleared());
    refreshMe(dispatch);
    nav.home();
  };

  const cancel = async () => {
    setBusy(true);
    setError(null);
    // the hold is written by a worker a moment after the decision; give it that moment
    for (let i = 0; i < 4; i++) {
      try {
        await api(`/v1/holds/${d.hold!.hold_id}/cancel`, { method: "POST" });
        voice.hush();
        dispatch(actions.flow.ended({ kind: "cancelled" }));
        refreshMe(dispatch);
        nav.replace("result");
        return;
      } catch (e) {
        if (e instanceof ApiErr && e.status === 404 && i < 3) await new Promise((r) => setTimeout(r, 600));
        else {
          setError(e);
          break;
        }
      }
    }
    setBusy(false);
  };

  const look = TIER.BLOCK;
  return (
    <TierShell tone={{ color: C.stepup, subtle: C.stepupSubtle }} icon="pause-circle" title={t("tier.hold.title")} voice={fam ? voice : null}
      footer={<>
        <Btn label={t("cancel.payment")} onPress={cancel} loading={busy} />
        <Row gap={S[1]}>
          <Btn variant="ghost" icon="flag" label={t("hold.report")} onPress={() => nav.push("report", { decision_id: d.decision_id })} style={{ flex: 1.3 }} full={false} small />
          <Btn variant="ghost" icon="phone" label={t("call.1930")} onPress={call1930} style={{ flex: 1 }} full={false} small />
        </Row>
        <Btn variant="quiet" label={t("hold.later")} onPress={leave} small />
      </>}>
      <T v="bodyLarge" color={C.textSecondary} center style={{ marginTop: 4 }}>{t("hold.noMoney")}</T>
      <PaymentLine />
      <ErrorNote error={error} />
      <Card tone={look.color} style={{ marginTop: S[2] }}>
        <T v="title">{fam?.headline}</T>
        <T v="bodyLarge" color={C.textSecondary} style={{ marginTop: 4, marginBottom: S[2] }}>{fam?.body}</T>
        <Reasons reasons={d.reasons} />
      </Card>
      <Card style={{ marginTop: S[2] }}>
        <T v="bodyLarge">{t("hold.body", { time: clock(release!) })}</T>
        <Row style={{ marginTop: S[2], justifyContent: "space-between" }}>
          <T v="caption" color={C.textMuted}>{t("hold.remaining")}</T>
          <T v="title" color={C.stepup}>{duration(left, locale)}</T>
        </Row>
      </Card>
      {d.guardian?.guardian_name ? (
        <Banner tone="info" icon="shield" style={{ marginTop: S[2] }}>{t("guardian.told", { name: d.guardian.guardian_name })}</Banner>
      ) : null}
      <CallerReportCard decisionId={d.decision_id} />
      <NeverPay vpa={d.payee?.vpa} />
    </TierShell>
  );
}

// ------------------------------------------------------------------ P-08 Decline --
export function Decline({ route }: { route: Route }) {
  const { t } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const d = useAppSelector((s) => s.flow.decision);
  const fam = d?.scam_family;
  const voice = useSpokenWarning(fam ? `${t("tier.decline.title")}. ${fam.headline}. ${fam.body}` : "", d?.decision_id || "");
  const [form, setForm] = useState(false);
  const [comment, setComment] = useState("");
  const [filed, setFiled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  useBackGuard(useCallback(() => true, []));

  useEffect(() => {
    if (!d) nav.home();
  }, [d, nav]);
  if (!d) return null;

  const leave = (to?: () => void) => {
    voice.hush();
    dispatch(actions.flow.cleared());
    if (to) to();
    else nav.home();
  };

  const review = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/v1/reports", { body: { kind: "FALSE_POSITIVE", decision_id: d.decision_id, comment: comment.trim() || null } });
      setFiled(true);
      setForm(false);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const look = TIER.BLOCK;
  // The payer's own never-pay list stopped this one: say so plainly; it is not a judgement about risk.
  if (d.user_block?.blocked) {
    return (
      <TierShell tone={look} icon="slash" title={t("block.declineTitle")} voice={null}
        footer={<>
          <Btn variant="secondary" icon="list" label={t("block.openList")} onPress={() => leave(() => nav.tab("safety", { tab: "blocked" }))} />
          <Btn variant="quiet" label={t("decline.home")} onPress={() => leave()} small />
        </>}>
        <T v="bodyLarge" color={C.textSecondary} center style={{ marginTop: 4 }}>{t("block.declineBody")}</T>
        <PaymentLine />
        {d.user_block.unblock_at ? (
          <Banner tone="info" icon="clock" style={{ marginTop: S[2] }}>{t("block.pendingUntil", { time: dateTime(d.user_block.unblock_at) })}</Banner>
        ) : null}
        {d.tier !== "SAFE" && d.reasons.length > 1 ? (
          <Card tone={look.color} style={{ marginTop: S[2] }}>
            {fam ? <T v="title">{fam.headline}</T> : null}
            <Reasons reasons={d.reasons.slice(1)} />
          </Card>
        ) : null}
      </TierShell>
    );
  }
  return (
    <TierShell tone={look} icon="x-octagon" title={t("tier.decline.title")} voice={fam ? voice : null}
      footer={<>
        <Btn variant="danger" icon="phone" label={t("call.1930")} onPress={call1930} />
        <Btn variant="ghost" icon="file-text" label={t("decline.pack")} onPress={() => nav.push("report", { decision_id: d.decision_id })} />
        <Btn variant="quiet" label={t("decline.home")} onPress={() => leave()} small />
      </>}>
      <T v="bodyLarge" color={C.textSecondary} center style={{ marginTop: 4 }}>{t("decline.noMoney")}</T>
      <PaymentLine />
      <Card tone={look.color} style={{ marginTop: S[2] }}>
        <T v="title">{fam?.headline}</T>
        <T v="bodyLarge" color={C.textSecondary} style={{ marginTop: 4, marginBottom: S[2] }}>{fam?.body}</T>
        <Reasons reasons={d.reasons} />
      </Card>
      <CallerReportCard decisionId={d.decision_id} />
      <NeverPay vpa={d.payee?.vpa} />
      {fam && fam.code !== "UNCLASSIFIED" ? (
        <Card style={{ marginTop: S[2] }} pad={S[2]} onPress={() => leave(() => nav.tab("practice", { family: fam.code }))}>
          <Row gap={S[2]}>
            <IconCircle icon="play-circle" color={C.brandHover} bg={C.brandSubtle} size={40} />
            <T v="body" weight={600} style={{ flex: 1 }}>{t("decline.learn")}</T>
          </Row>
        </Card>
      ) : null}
      <ErrorNote error={error} />
      {filed ? (
        <Banner tone="safe" style={{ marginTop: S[2] }}>{t("review.filed")}</Banner>
      ) : form ? (
        <Card style={{ marginTop: S[2] }}>
          <Field label={`${t("decline.comment")} (${t("common.optional")})`} value={comment} onChangeText={setComment} multiline maxLength={1000} />
          <Btn variant="secondary" label={t("decline.send")} onPress={review} loading={busy} />
        </Card>
      ) : (
        <View style={{ alignItems: "center", marginTop: S[2] }}>
          <Link label={t("not.a.scam")} color={C.textSecondary} onPress={() => setForm(true)} />
        </View>
      )}
    </TierShell>
  );
}
