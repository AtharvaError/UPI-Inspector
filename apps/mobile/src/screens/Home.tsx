// P-02 Home.
import React, { useCallback, useEffect, useState } from "react";
import { Pressable, ScrollView, View } from "react-native";
import { api } from "../api";
import { Avatar, Btn, Card, Chip, ErrorNote, Icon, IconName, Label, Mono, Row, Screen, T } from "../components/ui";
import { refreshMe, resolvePayee, startFlow } from "../flow";
import { BANK_NAMES, initials, money, useT } from "../i18n";
import { useNav } from "../nav";
import { ScreenName, useAppDispatch, useAppSelector } from "../store";
import { BAND, C, R, S } from "../../theme";

export default function Home() {
  const { t } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const me = useAppSelector((s) => s.session.me);
  const tick = useAppSelector((s) => s.overlay.tick);
  const [payees, setPayees] = useState<any[] | null>(null);
  const [trend, setTrend] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [opening, setOpening] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      await refreshMe(dispatch);
      const r = await api("/v1/users/me/payees");
      setPayees(r.payees);
      api("/v1/trends").then((x) => setTrend(x?.trends?.[0] || null)).catch(() => undefined);
    } catch (e) {
      setError(e);
      setPayees((p) => p || []);
    }
  }, [dispatch]);

  useEffect(() => {
    load();
  }, [load, tick]);

  const pay = () => {
    startFlow(dispatch, { entryMethod: "typed" });
    nav.push("payee");
  };
  const scan = () => {
    startFlow(dispatch, { entryMethod: "qr" });
    nav.push("scan");
  };
  const payContact = async (vpa: string) => {
    setOpening(vpa);
    try {
      const info = await resolvePayee(vpa);
      if (info.exists) {
        startFlow(dispatch, { payee: info, entryMethod: "contact" });
        nav.push("amount");
      }
    } catch (e) {
      setError(e);
    } finally {
      setOpening(null);
    }
  };

  const first = (me?.name || "").split(" ")[0];
  const quick: { icon: IconName; label: string; screen: ScreenName; badge?: number }[] = [
    { icon: "search", label: t("nav.safety"), screen: "safety" },
    { icon: "message-square", label: t("nav.message"), screen: "message" },
    { icon: "clock", label: t("nav.holds"), screen: "holds", badge: me?.active_holds || 0 },
    { icon: "shield", label: t("nav.guardian"), screen: "guardian", badge: me?.pending_guardian_requests || 0 },
  ];

  return (
    <Screen glow>
      <Row style={{ justifyContent: "space-between" }} align="flex-start">
        <View style={{ flex: 1 }}>
          <T v="title">{t("home.hello", { name: first })}</T>
          <Mono>{me?.vpa}</Mono>
        </View>
        <Chip label={BANK_NAMES[me?.tenant_id || ""] || ""} />
      </Row>

      <Card style={{ marginTop: S[3] }}>
        <T v="caption" color={C.textMuted}>{t("home.balance")}</T>
        <T v="amount">₹{money(me?.balance)}</T>
        {me?.guardian && me.guardian.status !== "PENDING" ? (
          <Chip style={{ marginTop: S[1] }} icon="shield" color={C.brandHover} bg={C.brandSubtle} label={t("home.protectedBy", { name: me.guardian.display_name })} />
        ) : null}
        <Row gap={S[1]} style={{ marginTop: S[3] }}>
          <Btn label={t("home.pay")} icon="send" onPress={pay} style={{ flex: 1, paddingHorizontal: S[2] }} full={false} />
          <Btn label={t("home.scan")} icon="maximize" variant="secondary" onPress={scan} style={{ flex: 1, paddingHorizontal: S[2] }} full={false} />
        </Row>
      </Card>

      <ErrorNote error={error} onRetry={load} />

      {(me?.active_holds || 0) > 0 ? (
        <Card tone={C.stepup} style={{ marginTop: S[2] }} onPress={() => nav.tab("holds")}>
          <Row gap={S[2]}>
            <Icon name="pause-circle" color={C.stepup} size={24} />
            <View style={{ flex: 1 }}>
              <T v="heading">{t("home.heldCard", { n: me!.active_holds! })}</T>
              <T v="caption" color={C.textSecondary}>{t("home.heldCardSub")}</T>
            </View>
            <Icon name="chevron-right" color={C.textMuted} />
          </Row>
        </Card>
      ) : null}

      {(me?.pending_guardian_requests || 0) > 0 ? (
        <Card tone={C.warning} style={{ marginTop: S[2] }} onPress={() => nav.tab("guardian", { tab: "protect" })}>
          <Row gap={S[2]}>
            <Icon name="shield" color={C.warning} size={24} />
            <View style={{ flex: 1 }}>
              <T v="heading">{t("home.requestsCard", { n: me!.pending_guardian_requests! })}</T>
              <T v="caption" color={C.textSecondary}>{t("home.requestsCardSub")}</T>
            </View>
            <Icon name="chevron-right" color={C.textMuted} />
          </Row>
        </Card>
      ) : null}

      {trend ? (
        <Card style={{ marginTop: S[2] }} pad={S[2]} onPress={() => nav.tab("safety", { tab: "trends" })}>
          <Row gap={S[2]}>
            <Icon name="trending-up" color={C.warning} size={22} />
            <View style={{ flex: 1 }}>
              <T v="body" weight={600}>{t("home.trend", { name: trend.name })}</T>
              <T v="caption" color={C.textSecondary}>{t("home.trendSub")}</T>
            </View>
            <Icon name="chevron-right" color={C.textMuted} />
          </Row>
        </Card>
      ) : null}

      <View style={{ marginTop: S[4] }}>
        <Label>{t("home.recent")}</Label>
        {payees && payees.length === 0 ? <T v="body" color={C.textMuted}>{t("home.noRecent")}</T> : null}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: S[2], paddingVertical: S[1] }}>
          {(payees || []).slice(0, 12).map((p) => {
            const look = BAND[(p.band as keyof typeof BAND) || "NO_HISTORY"] || BAND.NO_HISTORY;
            return (
              <Pressable key={p.vpa} accessibilityRole="button" accessibilityLabel={p.registered_name} onPress={() => payContact(p.vpa)}
                style={{ width: 76, alignItems: "center", opacity: opening === p.vpa ? 0.5 : 1 }}>
                <Avatar text={initials(p.registered_name)} size={52} color={look.color} bg={look.subtle} />
                <T v="caption" center numberOfLines={2} style={{ marginTop: 6 }}>{p.registered_name}</T>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      <View style={{ marginTop: S[3] }}>
        <Label>{t("home.more")}</Label>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: S[1] }}>
          {quick.map((q) => (
            <Pressable key={q.screen} accessibilityRole="button" onPress={() => nav.tab(q.screen)}
              style={({ pressed }) => ({ flexBasis: "48%", flexGrow: 1, backgroundColor: pressed ? C.surfaceHover : C.surface, borderRadius: R.lg, borderWidth: 1, borderColor: C.border, padding: S[2], minHeight: 76 })}>
              <Row style={{ justifyContent: "space-between" }}>
                <Icon name={q.icon} color={C.brandHover} />
                {q.badge ? <Chip label={String(q.badge)} color={C.blockOn} bg={C.block} /> : null}
              </Row>
              <T v="body" weight={500} style={{ marginTop: S[1] }}>{q.label}</T>
            </Pressable>
          ))}
        </View>
      </View>
    </Screen>
  );
}
