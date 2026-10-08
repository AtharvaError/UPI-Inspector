// P-04 Scan QR. Reads the UPI ID, the name on the code and any note. The name printed on a code is only
// a claim: the chip shows the registered name, and a mismatch is called out before the amount screen.
import { CameraView, useCameraPermissions } from "expo-camera";
import React, { useRef, useState } from "react";
import { View } from "react-native";
import { parseUpiLink } from "../api";
import { PayeeCard } from "../components/PayeeCard";
import { Banner, Btn, Card, ErrorNote, Field, Label, Link, Loading, Mono, Row, Screen, T } from "../components/ui";
import { resolvePayee } from "../flow";
import { useT } from "../i18n";
import { useNav } from "../nav";
import { actions, PayeeInfo, useAppDispatch, useAppSelector } from "../store";
import { C, R, S, isWeb } from "../../theme";

const SAMPLES = [
  { key: "scan.sample.shop", code: "upi://pay?pa=freshmart@banka&pn=FreshMart%20Groceries&am=450&tn=groceries" },
  { key: "scan.sample.cafe", code: "upi://pay?pa=newcafe@banka&pn=Third%20Wave%20Cafe&am=320" },
  { key: "scan.sample.sticker", code: "upi://pay?pa=booking.advance@bankb&pn=FreshMart%20Groceries&am=1200" },
];

export default function Scan() {
  const { t } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const demo = useAppSelector((s) => s.session.demoMode);
  const [permission, requestPermission] = useCameraPermissions();
  const [parsed, setParsed] = useState<{ vpa: string; name?: string; amount?: string; note?: string } | null>(null);
  const [info, setInfo] = useState<PayeeInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  const [invalid, setInvalid] = useState(false);
  const [paste, setPaste] = useState("");
  const lock = useRef(false);

  const handle = async (raw: string) => {
    if (lock.current) return;
    const p = parseUpiLink(raw);
    if (!p) {
      setInvalid(true);
      return;
    }
    lock.current = true;
    setInvalid(false);
    setBusy(true);
    setError(null);
    try {
      const r = await resolvePayee(p.vpa, p.name);
      if (!r.exists || r.is_self) {
        setInvalid(true);
        lock.current = false;
      } else {
        setParsed(p);
        setInfo(r);
      }
    } catch (e) {
      setError(e);
      lock.current = false;
    } finally {
      setBusy(false);
    }
  };

  const again = () => {
    lock.current = false;
    setParsed(null);
    setInfo(null);
  };

  const next = () => {
    if (!info || !parsed) return;
    dispatch(actions.flow.patched({ payee: info, claimedName: parsed.name || "", entryMethod: "qr", amount: parsed.amount || "", note: parsed.note || "", decision: null }));
    nav.push("amount");
  };

  if (info && parsed) {
    return (
      <Screen title={t("scan.title")} onBack={again}
        footer={<><Btn label={t("common.continue")} onPress={next} icon="arrow-right" /><Btn variant="quiet" label={t("common.retry")} onPress={again} /></>}>
        {parsed.name ? (
          <Card style={{ marginBottom: S[2] }} pad={S[2]}>
            <T v="caption" color={C.textMuted}>{t("payee.claimed")}</T>
            <T v="heading">{parsed.name}</T>
          </Card>
        ) : null}
        <PayeeCard info={info} />
      </Screen>
    );
  }

  const camera = !isWeb && permission?.granted;
  return (
    <Screen title={t("scan.title")} onBack={() => nav.pop()}>
      {camera ? (
        <View style={{ height: 300, borderRadius: R.lg, overflow: "hidden", borderWidth: 1, borderColor: C.borderStrong, marginBottom: S[2] }}>
          <CameraView style={{ flex: 1 }} facing="back" barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
            onBarcodeScanned={({ data }) => handle(String(data))} />
          <View style={{ position: "absolute", top: 50, left: 50, right: 50, bottom: 50, borderWidth: 2, borderColor: C.brandHover, borderRadius: R.lg, pointerEvents: "none" }} />
        </View>
      ) : !isWeb ? (
        <Card style={{ marginBottom: S[2] }}>
          <T v="body" color={C.textSecondary} style={{ marginBottom: S[2] }}>{t("scan.permission")}</T>
          <Btn label={t("scan.allow")} icon="camera" onPress={() => requestPermission()} />
        </Card>
      ) : null}
      {camera ? <T v="caption" color={C.textMuted} center style={{ marginBottom: S[2] }}>{t("scan.hint")}</T> : null}

      {busy ? <Loading label={t("payee.resolving")} /> : null}
      {invalid ? <Banner tone="warning" style={{ marginBottom: S[2] }}>{t("scan.invalid")}</Banner> : null}
      <ErrorNote error={error} />

      {demo ? (
        <View style={{ marginBottom: S[3] }}>
          <Label>{t("scan.samples")}</Label>
          <View style={{ gap: S[1] }}>
            {SAMPLES.map((s) => (
              <Card key={s.key} pad={S[2]} onPress={() => handle(s.code)}>
                <Row gap={S[2]}>
                  <View style={{ flex: 1 }}>
                    <T v="body" weight={600}>{t(s.key)}</T>
                    <Mono size={11.5} color={C.textMuted}>{s.code}</Mono>
                  </View>
                  <T v="caption" color={C.accentCyan}>{t("scan.use")}</T>
                </Row>
              </Card>
            ))}
          </View>
        </View>
      ) : null}

      <Field label={t("scan.paste")} value={paste} onChangeText={setPaste} autoCapitalize="none" autoCorrect={false} mono placeholder="upi://pay?pa=…" onSubmitEditing={() => handle(paste)} />
      <Btn variant="ghost" label={t("common.continue")} disabled={!paste.trim()} onPress={() => handle(paste)} />
      <View style={{ alignItems: "center", marginTop: S[2] }}>
        <Link label={t("scan.enter")} icon="edit-2" onPress={() => nav.replace("payee")} />
      </View>
    </Screen>
  );
}
