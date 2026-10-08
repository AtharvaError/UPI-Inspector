// P-15 Report and complaint pack (F-25). The pack gathers the evidence; filing the complaint stays with the person.
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import React, { useState } from "react";
import { Linking, View } from "react-native";
import { API_BASE, api, apiBlob, getToken } from "../api";
import { Banner, Btn, Card, ErrorNote, Field, Icon, Label, Mono, Row, Screen, T } from "../components/ui";
import { dateTime, money, useT } from "../i18n";
import { useNav } from "../nav";
import { Route } from "../store";
import { C, S, isWeb } from "../../theme";
import { call1930 } from "./Tier";

const TYPE_ICON = { call: "phone-incoming", message: "message-square", payment: "send" } as const;

export default function Report({ route }: { route: Route }) {
  const { t, locale } = useT();
  const nav = useNav();
  const p = route.params || {};
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<any>(null);
  const [pack, setPack] = useState<any>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      setPack(await api("/v1/complaint-packs", { body: { decision_id: p.decision_id || null, txn_id: p.txn_id || null, check_id: p.check_id || null, description: desc.trim() || null }, timeoutMs: 20000 }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const openPdf = async () => {
    if (!pack) return;
    setOpening(true);
    setError(null);
    try {
      if (isWeb) {
        const blob = await apiBlob(pack.pdf_url);
        const url = URL.createObjectURL(blob);
        window.open(url, "_blank");
      } else {
        const file = `${FileSystem.cacheDirectory}complaint-pack-${String(pack.pack_id).slice(0, 8)}.pdf`;
        const res = await FileSystem.downloadAsync(API_BASE + pack.pdf_url, file, { headers: { Authorization: `Bearer ${getToken()}` } });
        if (await Sharing.isAvailableAsync()) await Sharing.shareAsync(res.uri, { mimeType: "application/pdf", UTI: "com.adobe.pdf" });
      }
    } catch (e) {
      setError(e);
    } finally {
      setOpening(false);
    }
  };

  const items = [
    p.check_id && !p.decision_id && !p.txn_id ? t("report.item.msg") : t("report.item.txn"),
    t("report.item.timeline"),
    ...(p.decision_id || p.txn_id ? [t("report.item.reasons")] : []),
  ];
  const pl = pack?.payload;

  return (
    <Screen title={t("report.title")} onBack={() => nav.pop()}>
      <ErrorNote error={error} />
      {!pack ? (
        <View style={{ gap: S[2] }}>
          <Banner tone="warning" icon="clock">{t("report.golden")}</Banner>
          <Card>
            <Label>{t("report.what")}</Label>
            <View style={{ gap: S[1] }}>
              {items.map((it) => (
                <Row key={it} gap={S[1]} align="flex-start">
                  <Icon name="check" size={16} color={C.safe} style={{ marginTop: 3 }} />
                  <T v="body" style={{ flex: 1 }}>{it}</T>
                </Row>
              ))}
            </View>
          </Card>
          <Field label={`${t("report.desc")} (${t("common.optional")})`} value={desc} onChangeText={setDesc} multiline maxLength={1000} />
          <Btn label={busy ? t("report.creating") : t("report.create")} icon="file-text" loading={busy} onPress={create} />
          <T v="caption" color={C.textMuted} center>{t("pack.disclaimer")}</T>
        </View>
      ) : (
        <View style={{ gap: S[2] }}>
          <Banner tone="safe" title={t("report.ready")}>
            <Row gap={6} style={{ marginTop: 2 }}>
              <T v="caption" color={C.textSecondary}>{t("report.ref")}</T>
              <Mono size={12}>{String(pack.pack_id).slice(0, 8)}</Mono>
            </Row>
          </Banner>

          {pl?.transaction ? (
            <Card>
              <Row style={{ justifyContent: "space-between" }} align="flex-start" gap={S[2]}>
                <View style={{ flex: 1 }}>
                  <T v="heading">{pl.transaction.payee_registered_name}</T>
                  <Mono size={12.5}>{pl.transaction.payee_upi_id}</Mono>
                  {pl.transaction.claimed_name ? <T v="caption" color={C.textMuted}>{t("payee.claimed")}: {pl.transaction.claimed_name}</T> : null}
                </View>
                <View style={{ alignItems: "flex-end" }}>
                  <T v="title">₹{money(pl.transaction.amount)}</T>
                  <T v="caption" color={C.textMuted}>{String(pl.transaction.status || "").toLowerCase()}</T>
                </View>
              </Row>
            </Card>
          ) : null}

          {pl?.timeline?.length ? (
            <Card>
              <Label>{t("report.timeline")}</Label>
              <View style={{ gap: S[2] }}>
                {pl.timeline.map((e: any, i: number) => (
                  <Row key={i} gap={S[2]} align="flex-start">
                    <Icon name={(TYPE_ICON as any)[e.type] || "circle"} size={16} color={e.linked ? C.warning : C.textMuted} style={{ marginTop: 3 }} />
                    <View style={{ flex: 1 }}>
                      <T v="caption" color={C.textMuted}>{dateTime(e.at, locale)}</T>
                      <T v="body">{e.detail}</T>
                      {e.text ? <T v="caption" color={C.textSecondary} numberOfLines={3}>“{e.text}”</T> : null}
                    </View>
                  </Row>
                ))}
              </View>
            </Card>
          ) : null}

          <Btn label={isWeb ? t("report.open") : t("report.share")} icon={isWeb ? "external-link" : "share-2"} loading={opening} onPress={openPdf} />
          <Row gap={S[1]}>
            <Btn variant="danger" icon="phone" label={t("call.1930")} onPress={call1930} style={{ flex: 1 }} full={false} small />
            <Btn variant="ghost" icon="globe" label={t("report.portal")} onPress={() => Linking.openURL("https://cybercrime.gov.in").catch(() => undefined)} style={{ flex: 1.4 }} full={false} small />
          </Row>
          <T v="caption" color={C.textMuted} center>{t("pack.disclaimer")}</T>
        </View>
      )}
    </Screen>
  );
}
