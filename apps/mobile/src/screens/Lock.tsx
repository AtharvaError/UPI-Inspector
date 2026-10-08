// P-21 App lock (F-35). Shown over everything when a PIN session has been closed, left in the background or
// left unused. The PIN is checked by the server and counted like a sign-in: five wrong PINs lock the account.
import * as LocalAuthentication from "expo-local-authentication";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { api, ApiErr } from "../api";
import { Banner, Btn, ErrorNote, Icon, IconCircle, Link, Row, T } from "../components/ui";
import { useT } from "../i18n";
import { getDeviceKey } from "../secure";
import { actions, useAppDispatch, useAppSelector } from "../store";
import { C, R, S, font, isWeb } from "../../theme";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "<"];

/** A numeric keypad. `value` is held by the caller, so it can be cleared the moment it has been used. */
export function Keypad({ onKey, disabled }: { onKey: (k: string) => void; disabled?: boolean }) {
  return (
    <View style={{ flexDirection: "row", flexWrap: "wrap", justifyContent: "center", rowGap: S[1] }}>
      {KEYS.map((k, i) => (
        <View key={i} style={{ width: "33.33%", alignItems: "center" }}>
          {k ? (
            <Pressable accessibilityRole="button" accessibilityLabel={k === "<" ? "delete" : k} disabled={disabled} onPress={() => onKey(k)}
              style={({ pressed }) => ({ width: 72, height: 56, borderRadius: R.lg, alignItems: "center", justifyContent: "center", backgroundColor: pressed ? C.surfaceHover : C.surface, borderWidth: 1, borderColor: C.border, opacity: disabled ? 0.5 : 1 })}>
              {k === "<" ? <Icon name="delete" color={C.textSecondary} /> : <Text style={[font(600), { color: C.text, fontSize: 24 }]}>{k}</Text>}
            </Pressable>
          ) : null}
        </View>
      ))}
    </View>
  );
}

export function Dots({ n, total = 6 }: { n: number; total?: number }) {
  return (
    <Row gap={S[2]} style={{ height: 20, justifyContent: "center" }}>
      {Array.from({ length: total }, (_, i) => (
        <View key={i} style={{ width: 14, height: 14, borderRadius: 7, borderWidth: 1.5, borderColor: i < n ? C.brand : C.borderStrong, backgroundColor: i < n ? C.brand : "transparent" }} />
      ))}
    </Row>
  );
}

export default function Lock({ onSignOut }: { onSignOut: () => void }) {
  const { t } = useT();
  const dispatch = useAppDispatch();
  const me = useAppSelector((s) => s.session.me);
  const pin = useRef("");                 // never put in state: it must not end up in a render tree or a log
  const [n, setN] = useState(0);
  const [busy, setBusy] = useState(false);
  const [wrong, setWrong] = useState<number | null>(null);
  const [error, setError] = useState<any>(null);
  const [bio, setBio] = useState(false);

  const unlock = useCallback(() => dispatch(actions.lockSet(false)), [dispatch]);

  // Fingerprint or face can open the lock only on a phone that was enrolled with the PIN.
  useEffect(() => {
    if (isWeb) return;
    (async () => {
      try {
        const ok = (await LocalAuthentication.hasHardwareAsync()) && (await LocalAuthentication.isEnrolledAsync()) && !!(await getDeviceKey());
        setBio(ok);
      } catch {
        setBio(false);
      }
    })();
  }, []);

  const submit = async () => {
    const value = pin.current;
    pin.current = "";
    setN(0);
    setBusy(true);
    setError(null);
    try {
      await api("/v1/auth/verify-pin", { body: { pin: value } });
      unlock();
    } catch (e: any) {
      if (e instanceof ApiErr && e.code === "WRONG_PIN") setWrong(Number(e.details?.[0]?.tries_left ?? 0));
      else if (!(e instanceof ApiErr && e.status === 401)) setError(e);     // 401: the session has ended and the app is signing out
    } finally {
      setBusy(false);
    }
  };

  const press = (k: string) => {
    if (busy) return;
    if (k === "<") pin.current = pin.current.slice(0, -1);
    else if (pin.current.length < 6) pin.current += k;
    setN(pin.current.length);
    if (pin.current.length === 6) submit();
  };

  const biometric = async () => {
    try {
      const r = await LocalAuthentication.authenticateAsync({ promptMessage: t("lock.title"), cancelLabel: t("common.cancel") });
      if (r.success) unlock();
    } catch (e) {
      setError(e);
    }
  };

  return (
    <View accessibilityViewIsModal style={{ position: "absolute", top: 0, left: 0, right: 0, bottom: 0, zIndex: 100, backgroundColor: C.bg, padding: S[4], justifyContent: "space-between" }}>
      <View style={{ alignItems: "center", marginTop: S[5] }}>
        <IconCircle icon="lock" color={C.brandHover} bg={C.brandSubtle} size={72} />
        <T v="title" center style={{ marginTop: S[3] }}>{t("lock.title")}</T>
        {me?.name ? <T v="body" color={C.textSecondary} center>{me.name}</T> : null}
        <T v="body" color={C.textSecondary} center style={{ marginTop: S[1], marginBottom: S[3] }}>{t("lock.body")}</T>
        <Dots n={n} />
        {wrong !== null ? <Banner tone="warning" style={{ marginTop: S[3], alignSelf: "stretch" }}>{t("lock.wrong", { n: wrong })}</Banner> : null}
        <View style={{ alignSelf: "stretch" }}><ErrorNote error={error} /></View>
      </View>
      <View>
        <Keypad onKey={press} disabled={busy} />
        <View style={{ height: S[2] }} />
        {bio ? <Btn variant="secondary" icon="unlock" label={t("lock.bio")} onPress={biometric} /> : null}
        <View style={{ alignItems: "center", marginTop: S[2] }}>
          <Link label={t("lock.signout")} color={C.textSecondary} onPress={onSignOut} />
        </View>
        <T v="caption" color={C.textMuted} center style={{ marginTop: S[2] }}>{t("lock.why")}</T>
      </View>
    </View>
  );
}
