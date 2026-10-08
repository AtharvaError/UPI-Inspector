// P-00 Sign in and register, P-01 Demo accounts (F-30).
import React, { useEffect, useState } from "react";
import { ScrollView, View } from "react-native";
import { API_BASE, adoptSession, api, saveLocale, SessionGrant } from "../api";
import { Avatar, Banner, Btn, Card, Chip, ErrorNote, Field, Glow, Label, Loading, Mono, Row, Segmented, T } from "../components/ui";
import { BANK_NAMES, initials, money, useT } from "../i18n";
import { BrandMark, RailButton, RailFrame } from "../nav";
import { deviceLabel, ensureDeviceKey } from "../secure";
import { actions, useAppDispatch, useAppSelector } from "../store";
import { C, S } from "../../theme";

type Mode = "signin" | "register" | "demo";

export default function Auth() {
  const { t, locale } = useT();
  const dispatch = useAppDispatch();
  const demoMode = useAppSelector((s) => s.session.demoMode);
  const demoLogin = useAppSelector((s) => s.session.demoLogin);
  const notice = useAppSelector((s) => s.session.notice);
  const [mode, setMode] = useState<Mode>("signin");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);

  const [phone, setPhone] = useState("");
  const [pin, setPin] = useState("");
  const [pin2, setPin2] = useState("");
  const [name, setName] = useState("");
  const [bank, setBank] = useState("bank_a");
  const [age, setAge] = useState("26-59");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [demoUsers, setDemoUsers] = useState<any[] | null>(null);

  useEffect(() => {
    api("/v1/auth/config", { auth: false })
      .then((c) => {
        dispatch(actions.demoModeSet(!!c.demo_mode));
        // Passwordless demo sign-in is offered only where the server allows it (by default, the laptop itself).
        dispatch(actions.demoLoginSet(!!c.demo_login));
        if (c.demo_login) setMode("demo");
      })
      .catch((e) => setError(e));
  }, [dispatch]);

  useEffect(() => {
    setError(null);
    setFieldError(null);
    if (mode === "demo" && demoUsers === null) {
      api("/v1/auth/demo-users", { auth: false }).then((r) => setDemoUsers(r.users)).catch((e) => { setDemoUsers([]); setError(e); });
    }
  }, [mode, demoUsers]);

  const setLocale = (l: string) => {
    dispatch(actions.localeSet(l));
    saveLocale(l);
  };

  const finish = async (res: SessionGrant) => {
    const s = await adoptSession(res, res.user);
    dispatch(actions.signedIn({ token: s.token, me: res.user, authMethod: s.authMethod }));
  };

  // On a phone, signing in with the PIN also enrols the phone itself (docs/11, section 1.3).
  const device = async () => {
    const k = await ensureDeviceKey();
    return k ? { key_id: k.key_id, secret: k.secret, label: deviceLabel() } : undefined;
  };

  const run = async (fn: () => Promise<SessionGrant>) => {
    setBusy(true);
    setError(null);
    try {
      await finish(await fn());
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const digits = (s: string) => s.replace(/\D/g, "");
  const signIn = () => {
    if (digits(phone).length < 10) return setFieldError(t("auth.phoneRule"));
    if (!/^\d{6}$/.test(pin)) return setFieldError(t("auth.pinRule"));
    setFieldError(null);
    run(async () => api("/v1/auth/login", { auth: false, body: { phone: digits(phone), pin, device: await device() } }));
  };
  const register = () => {
    if (name.trim().length < 2) return setFieldError(t("auth.nameRule"));
    if (digits(phone).length < 10) return setFieldError(t("auth.phoneRule"));
    if (!/^\d{6}$/.test(pin)) return setFieldError(t("auth.pinRule"));
    if (pin !== pin2) return setFieldError(t("auth.pinMismatch"));
    setFieldError(null);
    run(async () => api("/v1/auth/register", { auth: false, body: { name: name.trim(), phone: digits(phone), pin, locale, tenant_id: bank, age_band: age, device: await device() } }));
  };

  return (
    <View style={{ flex: 1, flexDirection: "row", backgroundColor: C.bg }}>
      <RailFrame demo={demoMode}>
        <RailButton icon="log-in" label={t("auth.signin")} active={mode === "signin"} onPress={() => setMode("signin")} />
        <RailButton icon="user-plus" label={t("auth.register")} active={mode === "register"} onPress={() => setMode("register")} />
        {demoLogin ? <RailButton icon="users" label={t("auth.demo")} active={mode === "demo"} onPress={() => setMode("demo")} /> : null}
      </RailFrame>
      <View style={{ flex: 1 }}>
        <Glow />
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: S[3], paddingBottom: S[7] }}>
          <Row style={{ justifyContent: "space-between", marginBottom: S[4] }}>
            <Row gap={S[1]}>
              <BrandMark size={30} />
              <T v="heading">{t("app.name")}</T>
            </Row>
            <Segmented style={{ width: 132 }} value={locale as "en" | "hi"} onChange={setLocale} options={[{ value: "en", label: "EN" }, { value: "hi", label: "हिन्दी" }]} />
          </Row>
          <T v="display">{t(mode === "signin" ? "auth.signin" : mode === "register" ? "auth.register" : "auth.demo")}</T>
          <T v="body" color={C.textSecondary} style={{ marginBottom: S[3] }}>{t("auth.tagline")}</T>

          {notice === "expired" ? <Banner tone="info" icon="clock" style={{ marginBottom: S[2] }}>{t("auth.sessionEnded")}</Banner> : null}
          <ErrorNote error={error} />
          {fieldError ? <Banner tone="warning" style={{ marginBottom: S[2] }}>{fieldError}</Banner> : null}
          {demoMode && !demoLogin && mode === "signin" ? <Banner tone="info" icon="info" style={{ marginBottom: S[2] }}>{t("auth.demoOff")}</Banner> : null}

          {mode === "signin" ? (
            <View>
              <Field label={t("auth.phone")} value={phone} onChangeText={setPhone} keyboardType="phone-pad" maxLength={14} autoComplete="off" placeholder="98765 43210" />
              <Field label={t("auth.pin")} value={pin} onChangeText={(v) => setPin(digits(v).slice(0, 6))} keyboardType="number-pad" secureTextEntry maxLength={6} autoComplete="off"
                hint={t("auth.pinNote")} onSubmitEditing={signIn} />
              <Btn label={t("auth.signin")} onPress={signIn} loading={busy} icon="log-in" />
              <T v="caption" color={C.textMuted} style={{ marginTop: S[2] }}>{t("auth.lockNote")}</T>
            </View>
          ) : null}

          {mode === "register" ? (
            <View>
              <Field label={t("auth.name")} value={name} onChangeText={setName} autoCapitalize="words" maxLength={60} />
              <Field label={t("auth.phone")} value={phone} onChangeText={setPhone} keyboardType="phone-pad" maxLength={14} autoComplete="off" />
              <Field label={t("auth.pin")} value={pin} onChangeText={(v) => setPin(digits(v).slice(0, 6))} keyboardType="number-pad" secureTextEntry maxLength={6} autoComplete="off" hint={t("auth.pinRules")} />
              <Field label={t("auth.pin2")} value={pin2} onChangeText={(v) => setPin2(digits(v).slice(0, 6))} keyboardType="number-pad" secureTextEntry maxLength={6} autoComplete="off" />
              <Label>{t("auth.bank")}</Label>
              <Segmented value={bank} onChange={setBank} options={[{ value: "bank_a", label: BANK_NAMES.bank_a }, { value: "bank_b", label: BANK_NAMES.bank_b }]} style={{ marginBottom: S[2] }} />
              <Label>{t("auth.age")}</Label>
              <Segmented value={age} onChange={setAge} options={[{ value: "18-25", label: "18–25" }, { value: "26-59", label: "26–59" }, { value: "60+", label: "60+" }]} style={{ marginBottom: S[3] }} />
              <Btn label={t("auth.create")} onPress={register} loading={busy} icon="user-plus" />
              <T v="caption" color={C.textMuted} style={{ marginTop: S[2] }}>{t("auth.newAccount")}</T>
            </View>
          ) : null}

          {mode === "demo" ? (
            <View style={{ gap: S[1] }}>
              <T v="body" color={C.textSecondary} style={{ marginBottom: S[1] }}>{t("auth.demoHint")}</T>
              {demoUsers === null ? <Loading /> : null}
              {(demoUsers || []).map((u) => (
                <Card key={u.user_id} onPress={busy ? undefined : () => run(() => api("/v1/auth/demo-login", { auth: false, body: { user_id: u.user_id } }))}>
                  <Row gap={S[2]}>
                    <Avatar text={initials(u.name)} />
                    <View style={{ flex: 1 }}>
                      <T v="heading">{u.name}</T>
                      <Mono>{u.vpa}</Mono>
                      <Row wrap gap={6} style={{ marginTop: 6 }}>
                        <Chip label={BANK_NAMES[u.tenant_id] || u.tenant_id} />
                        <Chip label={String(u.segment || "").replace("_", " ")} />
                        <Chip label={u.locale === "hi" ? "हिन्दी" : "English"} />
                        <Chip label={`₹${money(u.balance)}`} />
                      </Row>
                    </View>
                  </Row>
                </Card>
              ))}
            </View>
          ) : null}

          <Row style={{ marginTop: S[5] }} gap={6}>
            <T v="caption" color={C.textMuted}>{t("auth.server")}</T>
            <Mono size={12}>{API_BASE}</Mono>
          </Row>
        </ScrollView>
      </View>
    </View>
  );
}
