// P-03 Enter payee: the UPI ID, the name the payer was given, and the reputation chip before any amount.
import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { api, VPA_RE } from "../api";
import { PayeeCard } from "../components/PayeeCard";
import { Avatar, Banner, Btn, Card, ErrorNote, Field, Label, Mono, Row, Screen, T } from "../components/ui";
import { resolvePayee } from "../flow";
import { initials, useT } from "../i18n";
import { useNav } from "../nav";
import { actions, EntryMethod, PayeeInfo, useAppDispatch, useAppSelector } from "../store";
import { BAND, C, S } from "../../theme";

type Status = "idle" | "resolving" | "found" | "notfound" | "self" | "error";

export default function Payee() {
  const { t } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const flow = useAppSelector((s) => s.flow);
  const [vpa, setVpa] = useState(flow.payee?.vpa || "");
  const [claimed, setClaimed] = useState(flow.claimedName || "");
  const [entry, setEntry] = useState<EntryMethod>(flow.payee ? flow.entryMethod : "typed");
  const [info, setInfo] = useState<PayeeInfo | null>(flow.payee);
  const [status, setStatus] = useState<Status>(flow.payee ? "found" : "idle");
  const [error, setError] = useState<any>(null);
  const [saved, setSaved] = useState<any[]>([]);
  const seq = useRef(0);

  useEffect(() => {
    api("/v1/users/me/payees").then((r) => setSaved(r.payees)).catch(() => undefined);
  }, []);

  // Typing and pasting are both allowed; the app records which. A jump of several characters is a paste.
  const onVpa = (next: string) => {
    const clean = next.trim().toLowerCase();
    if (clean.length === 0) setEntry("typed");
    else if (clean.length - vpa.length >= 5) setEntry("pasted");
    else if (entry === "contact") setEntry("typed");
    setVpa(clean);
  };

  useEffect(() => {
    const v = vpa.trim();
    if (!VPA_RE.test(v)) {
      setInfo(null);
      setStatus("idle");
      return;
    }
    const my = ++seq.current;
    setStatus("resolving");
    setError(null);
    const id = setTimeout(async () => {
      try {
        const r = await resolvePayee(v, claimed);
        if (my !== seq.current) return;
        if (!r.exists) {
          setInfo(null);
          setStatus("notfound");
        } else if (r.is_self) {
          setInfo(null);
          setStatus("self");
        } else {
          setInfo(r);
          setStatus("found");
        }
      } catch (e) {
        if (my !== seq.current) return;
        setInfo(null);
        setStatus("error");
        setError(e);
      }
    }, 380);
    return () => clearTimeout(id);
  }, [vpa, claimed]);

  const pick = (p: any) => {
    setEntry("contact");
    setClaimed("");
    setVpa(p.vpa);
  };

  const next = () => {
    if (!info) return;
    dispatch(actions.flow.patched({ payee: info, claimedName: claimed.trim(), entryMethod: entry, decision: null }));
    nav.push("amount");
  };

  return (
    <Screen title={t("payee.title")} onBack={() => nav.pop()}
      footer={<Btn label={t("common.continue")} onPress={next} disabled={status !== "found"} icon="arrow-right" />}>
      <Field label={t("payee.vpa")} placeholder={t("payee.vpaHint")} value={vpa} onChangeText={onVpa} autoCapitalize="none" autoCorrect={false}
        autoComplete="off" keyboardType="email-address" mono
        right={status === "resolving" ? <ActivityIndicator color={C.brand} /> : null} />
      <Field label={`${t("payee.claimed")} (${t("common.optional")})`} hint={t("payee.claimedHint")} value={claimed} onChangeText={setClaimed} maxLength={120} autoComplete="off" />

      {status === "resolving" ? <T v="caption" color={C.textMuted}>{t("payee.resolving")}</T> : null}
      {status === "notfound" ? <Banner tone="block" icon="slash">{t("payee.notFound")}</Banner> : null}
      {status === "self" ? <Banner tone="warning">{t("payee.self")}</Banner> : null}
      {status === "error" ? <ErrorNote error={error} /> : null}
      {status === "found" && info ? <PayeeCard info={info} /> : null}

      {saved.length > 0 && status !== "found" ? (
        <View style={{ marginTop: S[3] }}>
          <Label>{t("payee.saved")}</Label>
          <View style={{ gap: S[1] }}>
            {saved.slice(0, 8).map((p) => {
              const look = BAND[(p.band as keyof typeof BAND) || "NO_HISTORY"] || BAND.NO_HISTORY;
              return (
                <Card key={p.vpa} pad={S[2]} onPress={() => pick(p)}>
                  <Row gap={S[2]}>
                    <Avatar text={initials(p.registered_name)} size={38} color={look.color} bg={look.subtle} />
                    <View style={{ flex: 1 }}>
                      <T v="body" weight={600} numberOfLines={1}>{p.registered_name}</T>
                      <Mono size={12.5}>{p.vpa}</Mono>
                    </View>
                  </Row>
                </Card>
              );
            })}
          </View>
        </View>
      ) : null}
    </Screen>
  );
}
