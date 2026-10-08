// P-06 Step-Up: a spoken warning, a pause, one question, then authentication (F-09, F-21).
// The server enforces the pause and decides what the answer means; this screen only walks through it.
import * as LocalAuthentication from "expo-local-authentication";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ScrollView, View } from "react-native";
import { api, ApiErr } from "../api";
import { Banner, Btn, ErrorNote, FadeIn, Field, IconBtn, IconCircle, Link, Reasons, Ring, Row, T, useCountdown } from "../components/ui";
import { finishFlow, proceedToPin, sendOutcome } from "../flow";
import { useT } from "../i18n";
import { useBackGuard, useNav } from "../nav";
import { deviceProof, getDeviceKey } from "../secure";
import { useSecureScreen } from "../session";
import { onEvent } from "../socket";
import { speak, stopSpeaking } from "../speech";
import { actions, useAppDispatch, useAppSelector } from "../store";
import { C, M, R, S, TIER, isWeb } from "../../theme";

export default function StepUp() {
  const { t, locale } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const d = useAppSelector((s) => s.flow.decision);
  const voice = useAppSelector((s) => !!s.session.me?.voice_warnings);
  const su = d?.step_up;
  const fam = d?.scam_family;
  const [step, setStep] = useState(1);
  const [speaking, setSpeaking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  const [until, setUntil] = useState<number | null>(null);
  const [answer, setAnswer] = useState<"yes" | "no" | null>(null);
  const [bio, setBio] = useState(false);
  const [mode, setMode] = useState<"bio" | "otp">("otp");
  const [sent, setSent] = useState(false);
  const [otp, setOtp] = useState("");
  const [bad, setBad] = useState<number | null>(null);
  const left = useCountdown(until);
  const gotSms = useRef(false);
  const [note, setNote] = useState<string | null>(null);
  useSecureScreen("stepup");

  const spoken = fam ? `${fam.headline}. ${fam.body}` : "";
  const say = useCallback(() => {
    speak(spoken, locale);
    setSpeaking(true);
  }, [spoken, locale]);
  const hush = useCallback(() => {
    stopSpeaking();
    setSpeaking(false);
  }, []);

  useEffect(() => {
    if (voice && spoken) say();
    return () => stopSpeaking();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fingerprint or face is offered only on a phone that holds a device key (enrolled when the PIN was used
  // to sign in) and has biometrics set up. The server has the last word: see `choose` below.
  useEffect(() => {
    if (isWeb) return;
    (async () => {
      try {
        const ok = (await LocalAuthentication.hasHardwareAsync()) && (await LocalAuthentication.isEnrolledAsync()) && !!(await getDeviceKey());
        setBio(ok);
        if (ok) setMode("bio");
      } catch {
        setBio(false);
      }
    })();
  }, []);

  useEffect(() => onEvent("sms.simulated", (e) => { if (e?.kind === "otp") gotSms.current = true; }), []);

  useBackGuard(useCallback(() => true, []));   // a tier screen is left by choosing, never by the Back key

  useEffect(() => {
    if (!d || !su) nav.home();
  }, [d, su, nav]);
  if (!d || !su) return null;

  const cancel = async () => {
    hush();
    await sendOutcome(d.decision_id, "STEPUP_ABANDONED");
    finishFlow(nav, { kind: "cancelled" });
  };

  const toCooloff = () => {
    setUntil(Date.now() + su.cooloff_s * 1000);
    setStep(2);
  };

  const choose = async (a: "yes" | "no") => {
    setBusy(true);
    setError(null);
    setAnswer(a);
    try {
      const res = await api("/v1/stepup/verify", { body: { challenge_id: su.challenge_id, answer: a } });
      if (res.result === "FAILED") {
        hush();
        dispatch(actions.flow.patched({ decision: { ...d, action: "DECLINE", tier: "BLOCK", scam_family: res.scam_family || d.scam_family, step_up: null } }));
        nav.replace("decline", { coached: true });
      } else {
        // the server says which ways of confirming it will accept for this account
        if (!(res.auth_methods || []).includes("biometric")) setMode("otp");
        setStep(4);
      }
    } catch (e: any) {
      if (e instanceof ApiErr && e.status === 425) {
        const wait = Number(e.details?.[0]?.retry_in_s || 2);
        setUntil(Date.now() + wait * 1000);
        setStep(2);
      } else setError(e);
    } finally {
      setBusy(false);
    }
  };

  const finish = async (auth: any) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api("/v1/stepup/verify", { body: { challenge_id: su.challenge_id, answer, auth } });
      if (res.result === "PASSED") {
        hush();
        dispatch(actions.overlay.otpCleared());      // the code has been used; take its banner away
        const next = { ...d, guardian: res.guardian ? { ...(d.guardian as any), ...res.guardian } : d.guardian };
        dispatch(actions.flow.patched({ decision: next }));
        proceedToPin(nav, next, "replace");
      } else if (res.result === "BAD_CODE") {
        setBad(res.tries_left);
        setOtp("");
      } else if (res.result === "LOCKED") {
        // three wrong attempts: the challenge is closed and the payment has to be started again
        hush();
        dispatch(actions.overlay.otpCleared());
        finishFlow(nav, { kind: "failed", message: t("stepup.locked") });
      } else if (res.result === "FAILED") {
        nav.replace("decline", { coached: true });
      }
    } catch (e: any) {
      if (e instanceof ApiErr && e.code === "BIOMETRIC_NOT_ENROLLED") {
        setMode("otp");
        setBio(false);
        setNote(t("stepup.bioFallback"));
      } else setError(e);
    } finally {
      setBusy(false);
    }
  };

  const sendCode = async () => {
    setBusy(true);
    setError(null);
    gotSms.current = false;
    try {
      const res = await api("/v1/stepup/challenge", { body: { challenge_id: su.challenge_id, method: "otp" } });
      setSent(true);
      setBad(null);
      // The code arrives as a simulated SMS over the live channel. If that channel is down, show it anyway.
      if (res.demo_code) {
        setTimeout(() => {
          if (!gotSms.current) {
            dispatch(actions.overlay.smsArrived({ id: `otp-${Date.now()}`, kind: "otp", sender: "UPI-INSP", text: `Your UPI Inspector code is ${res.demo_code}. Do not share it.` }));
          }
        }, 1500);
      }
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const biometric = async () => {
    try {
      const key = await getDeviceKey();
      if (!key) {
        setMode("otp");
        setBio(false);
        setNote(t("stepup.bioFallback"));
        return;
      }
      const r = await LocalAuthentication.authenticateAsync({ promptMessage: t("stepup.authTitle"), cancelLabel: t("common.cancel") });
      // The fingerprint unlocks nothing by itself: what the server accepts is a proof made with this phone's key.
      if (r.success) finish({ method: "biometric", key_id: key.key_id, proof: await deviceProof(key, su.challenge_id) });
    } catch (e) {
      setError(e);
    }
  };

  const look = TIER.STEP_UP;
  return (
    <View style={{ flex: 1, backgroundColor: C.surfaceSunken, justifyContent: "flex-end" }}>
      <FadeIn from={48} duration={M.slow} style={{ maxHeight: "96%", backgroundColor: C.surface, borderTopLeftRadius: R.xl, borderTopRightRadius: R.xl, borderWidth: 1, borderColor: look.color }}>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: S[4], paddingBottom: S[5] }}>
          <Row style={{ justifyContent: "space-between" }}>
            <T v="caption" color={C.textMuted}>{t("stepup.step", { n: step })}</T>
            {spoken ? <IconBtn icon={speaking ? "volume-2" : "volume-x"} label={speaking ? t("stepup.mute") : t("stepup.listen")} color={look.color} bg={look.subtle} onPress={speaking ? hush : say} /> : null}
          </Row>
          <View style={{ height: 4, backgroundColor: C.border, borderRadius: 2, marginTop: S[1], marginBottom: S[3] }}>
            <View style={{ height: 4, width: `${step * 25}%`, backgroundColor: look.color, borderRadius: 2 }} />
          </View>

          {step === 1 ? (
            <FadeIn key="s1">
              <IconCircle icon="pause-circle" color={look.color} bg={look.subtle} />
              <T v="caption" color={look.color} center style={{ marginTop: S[2], textTransform: "uppercase", letterSpacing: 1 }}>{t("tier.stepup.title")}</T>
              <T v="title" center style={{ marginTop: 4 }}>{fam?.headline}</T>
              <T v="bodyLarge" color={C.textSecondary} center style={{ marginTop: S[1], marginBottom: S[3] }}>{fam?.body}</T>
              <Reasons reasons={d.reasons} />
              <View style={{ height: S[4] }} />
              <Btn label={t("stepup.understand")} onPress={toCooloff} />
              <View style={{ height: S[1] }} />
              <Btn variant="ghost" label={t("cancel.payment")} onPress={cancel} />
            </FadeIn>
          ) : null}

          {step === 2 ? (
            <FadeIn key="s2">
              <Ring seconds={Math.max(0.1, ((until || Date.now()) - Date.now()) / 1000)} total={su.cooloff_s} color={look.color}>
                <T v="display" a11y={t("stepup.wait", { s: Math.ceil(left) })}>{Math.ceil(left)}</T>
              </Ring>
              <T v="bodyLarge" center style={{ marginTop: S[3] }}>{t("stepup.cooloff")}</T>
              <T v="body" color={C.textSecondary} center style={{ marginTop: S[1], marginBottom: S[4] }}>{fam?.body}</T>
              <Btn label={left > 0 ? t("stepup.wait", { s: Math.ceil(left) }) : t("common.continue")} disabled={left > 0} onPress={() => setStep(3)} />
              <View style={{ height: S[1] }} />
              <Btn variant="ghost" label={t("cancel.payment")} onPress={cancel} />
            </FadeIn>
          ) : null}

          {step === 3 ? (
            <FadeIn key="s3">
              <T v="title" center style={{ marginBottom: S[4] }}>{su.question.text}</T>
              <ErrorNote error={error} />
              <Btn variant="secondary" label={t("common.yes")} onPress={() => choose("yes")} disabled={busy} loading={busy && answer === "yes"} style={{ minHeight: 64 }} />
              <View style={{ height: S[2] }} />
              <Btn variant="secondary" label={t("common.no")} onPress={() => choose("no")} disabled={busy} loading={busy && answer === "no"} style={{ minHeight: 64 }} />
              <View style={{ height: S[4] }} />
              <Btn variant="ghost" label={t("cancel.payment")} onPress={cancel} />
            </FadeIn>
          ) : null}

          {step === 4 ? (
            <FadeIn key="s4">
              <IconCircle icon="lock" color={C.brandHover} bg={C.brandSubtle} size={60} />
              <T v="title" center style={{ marginTop: S[2], marginBottom: S[3] }}>{t("stepup.authTitle")}</T>
              <ErrorNote error={error} />
              {note ? <Banner tone="info" style={{ marginBottom: S[2] }}>{note}</Banner> : null}
              {mode === "bio" && bio ? (
                <View>
                  <Btn label={t("stepup.bio")} icon="unlock" onPress={biometric} loading={busy} />
                  <View style={{ alignItems: "center", marginTop: S[1] }}>
                    <Link label={t("stepup.useOtp")} onPress={() => setMode("otp")} />
                  </View>
                </View>
              ) : (
                <View>
                  {!sent ? (
                    <Btn label={t("stepup.send")} icon="message-square" onPress={sendCode} loading={busy} />
                  ) : (
                    <View>
                      {bad !== null ? <Banner tone="warning" style={{ marginBottom: S[2] }}>{t("stepup.bad", { n: bad })}</Banner> : null}
                      <Field label={t("stepup.otpHint")} value={otp} onChangeText={(v) => setOtp(v.replace(/\D/g, "").slice(0, 6))} keyboardType="number-pad" maxLength={6} autoFocus
                        autoComplete="off" inputStyle={{ fontSize: 26, letterSpacing: 8, textAlign: "center" }} onSubmitEditing={() => otp.length === 6 && finish({ method: "otp", otp })} />
                      <Btn label={t("stepup.verify")} disabled={otp.length !== 6} loading={busy} onPress={() => finish({ method: "otp", otp })} />
                      <View style={{ alignItems: "center", marginTop: S[1] }}>
                        <Link label={t("stepup.resend")} onPress={sendCode} />
                      </View>
                    </View>
                  )}
                </View>
              )}
              <View style={{ height: S[3] }} />
              <Btn variant="ghost" label={t("cancel.payment")} onPress={cancel} />
            </FadeIn>
          ) : null}
        </ScrollView>
      </FadeIn>
    </View>
  );
}
