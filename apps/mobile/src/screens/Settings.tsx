// P-17 Settings: language, voice warnings, large text, security (F-35), and the demo controls.
import React, { useEffect, useState } from "react";
import { View } from "react-native";
import { API_BASE, adoptSession, api, ApiErr, saveLocale } from "../api";
import { Banner, Btn, Card, Chip, ErrorNote, Field, Label, Mono, Row, Screen, Segmented, T, Toggle } from "../components/ui";
import { BANK_NAMES, useT } from "../i18n";
import { deviceKeySupported, getDeviceKey } from "../secure";
import { signOut, useSecureScreen } from "../session";
import { speak } from "../speech";
import { actions, useAppDispatch, useAppSelector } from "../store";
import { C, S } from "../../theme";

export default function Settings() {
  const { t, locale } = useT();
  const dispatch = useAppDispatch();
  const me = useAppSelector((s) => s.session.me);
  const online = useAppSelector((s) => s.session.online);
  const demo = useAppSelector((s) => s.session.demoMode);
  const authMethod = useAppSelector((s) => s.session.authMethod);
  const [error, setError] = useState<any>(null);
  const [hours, setHours] = useState(12);
  const [enrolled, setEnrolled] = useState(false);

  // change-PIN form
  const [open, setOpen] = useState(false);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [pinError, setPinError] = useState<any>(null);
  const [done, setDone] = useState(false);
  useSecureScreen("settings-pin");

  useEffect(() => {
    api("/v1/auth/config", { auth: false }).then((c) => setHours(c?.session?.max_hours || 12)).catch(() => undefined);
    (async () => {
      const key = await getDeviceKey();
      if (!key) return;
      try {
        const r = await api("/v1/auth/devices");
        setEnrolled((r.devices || []).some((d: any) => d.key_id === key.key_id));
      } catch {
        /* shown as not enrolled */
      }
    })();
  }, []);

  const patch = async (body: any) => {
    setError(null);
    dispatch(actions.meLoaded(body));            // show the change at once; the server confirms it
    if (body.locale) saveLocale(body.locale);
    try {
      dispatch(actions.meLoaded(await api("/v1/users/me", { method: "PATCH", body })));
    } catch (e) {
      setError(e);
    }
  };

  const digits = (v: string) => v.replace(/\D/g, "").slice(0, 6);
  const changePin = async () => {
    if (!/^\d{6}$/.test(cur) || !/^\d{6}$/.test(next)) return setPinError(new ApiErr(t("auth.pinRule"), "VALIDATION_ERROR", 400));
    if (next !== again) return setPinError(new ApiErr(t("auth.pinMismatch"), "VALIDATION_ERROR", 400));
    setBusy(true);
    setPinError(null);
    try {
      // The server signs every other device out and gives this one a fresh session.
      const res = await api("/v1/auth/change-pin", { body: { current_pin: cur, new_pin: next } });
      const s = await adoptSession(res, res.user);
      dispatch(actions.tokenRenewed(s.token));
      setDone(true);
      setOpen(false);
    } catch (e) {
      setPinError(e);
    } finally {
      setCur("");
      setNext("");
      setAgain("");
      setBusy(false);
    }
  };

  return (
    <Screen title={t("nav.settings")}>
      <ErrorNote error={error} />
      <Card>
        <Label>{t("auth.language")}</Label>
        <Segmented value={locale as "en" | "hi"} onChange={(l) => patch({ locale: l })} options={[{ value: "en", label: "English" }, { value: "hi", label: "हिन्दी" }]} />
        <View style={{ height: S[3] }} />
        <Toggle label={t("settings.voice")} help={t("settings.voiceHelp")} value={!!me?.voice_warnings} onChange={(v) => patch({ voice_warnings: v })} />
        <Btn small variant="ghost" icon="volume-2" label={t("settings.testVoice")} full={false} style={{ marginTop: S[1], marginBottom: S[2] }}
          onPress={() => speak(t("settings.sample"), locale)} />
        <Toggle label={t("settings.large")} value={!!me?.large_text} onChange={(v) => patch({ large_text: v })} />
      </Card>

      <Card style={{ marginTop: S[2] }}>
        <Label>{t("settings.account")}</Label>
        <T v="heading">{me?.name}</T>
        <Mono>{me?.vpa}</Mono>
        <Row gap={6} wrap style={{ marginTop: S[1] }}>
          <Chip label={`${t("settings.bank")}: ${BANK_NAMES[me?.tenant_id || ""] || ""}`} />
          {me?.phone_last4 ? <Chip label={`${t("settings.phone")}: ${me.phone_last4}`} /> : null}
        </Row>
      </Card>

      <Card style={{ marginTop: S[2] }}>
        <Label>{t("settings.security")}</Label>
        {authMethod !== "pin" ? (
          <T v="body" color={C.textSecondary}>{t("settings.demoSession")}</T>
        ) : (
          <View>
            <T v="body" color={C.textSecondary}>{t("settings.lockInfo", { min: 5, hours })}</T>
            <T v="body" color={C.textSecondary} style={{ marginTop: S[1], marginBottom: S[2] }}>
              {t(deviceKeySupported && enrolled ? "settings.deviceOk" : "settings.deviceNo")}
            </T>
            {done ? <Banner tone="safe" icon="check" style={{ marginBottom: S[2] }}>{t("settings.pinChanged")}</Banner> : null}
            {open ? (
              <View>
                <ErrorNote error={pinError} />
                <Field label={t("settings.currentPin")} value={cur} onChangeText={(v) => setCur(digits(v))} keyboardType="number-pad" secureTextEntry maxLength={6} autoComplete="off" />
                <Field label={t("settings.newPin")} value={next} onChangeText={(v) => setNext(digits(v))} keyboardType="number-pad" secureTextEntry maxLength={6} autoComplete="off" hint={t("auth.pinRules")} />
                <Field label={t("settings.newPin2")} value={again} onChangeText={(v) => setAgain(digits(v))} keyboardType="number-pad" secureTextEntry maxLength={6} autoComplete="off" />
                <Btn label={t("settings.savePin")} icon="check" loading={busy} onPress={changePin} />
                <Btn variant="quiet" small label={t("common.cancel")} onPress={() => { setOpen(false); setPinError(null); }} />
              </View>
            ) : (
              <Btn variant="secondary" icon="key" label={t("settings.changePin")} onPress={() => { setOpen(true); setDone(false); }} />
            )}
            <Row gap={S[1]} style={{ marginTop: S[1] }}>
              <Btn small variant="ghost" icon="lock" label={t("settings.lockNow")} style={{ flex: 1 }} full={false} onPress={() => dispatch(actions.lockSet(true))} />
            </Row>
            <Btn variant="quiet" small label={t("settings.signoutAll")} onPress={() => signOut(true)} />
          </View>
        )}
      </Card>

      <Card style={{ marginTop: S[2] }}>
        <Label>{t("settings.demo")}</Label>
        {demo ? <T v="body" color={C.textSecondary} style={{ marginBottom: S[2] }}>{t("settings.timers")}</T> : null}
        <Row gap={6} wrap style={{ marginBottom: S[2] }}>
          <T v="caption" color={C.textMuted}>{t("settings.server")}</T>
          <Mono size={12}>{API_BASE}</Mono>
          <Chip label={online ? "live" : "offline"} color={online ? C.safe : C.warning} bg={online ? C.safeSubtle : C.warningSubtle} />
        </Row>
        <Btn variant="secondary" icon={demo && authMethod !== "pin" ? "users" : "log-out"} label={demo && authMethod !== "pin" ? t("settings.switch") : t("settings.signout")} onPress={() => signOut()} />
      </Card>
    </Screen>
  );
}
