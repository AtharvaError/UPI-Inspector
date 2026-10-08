// P-22 Safety check: look up a UPI ID or a number (F-32), the never-pay list (F-33), and this week's
// scam trends with the official helplines (F-34).
import React, { useCallback, useEffect, useState } from "react";
import { Linking, View } from "react-native";
import { api, ApiErr, VPA_RE } from "../api";
import { Avatar, Banner, Btn, Card, Chip, Empty, ErrorNote, Field, Icon, IconName, Label, Link, Loading, Mono, Row, Screen, Segmented, T, Toggle } from "../components/ui";
import { startFlow } from "../flow";
import { duration, initials, span, useT } from "../i18n";
import { useNav } from "../nav";
import { onEvent } from "../socket";
import { actions, PayeeInfo, Route, useAppDispatch, useAppSelector } from "../store";
import { BAND, C, S } from "../../theme";
import { call1930 } from "./Tier";

type Tab = "lookup" | "blocked" | "trends";

export default function Safety({ route }: { route: Route }) {
  const { t } = useT();
  const [tab, setTab] = useState<Tab>((route.params?.tab as Tab) || "lookup");
  return (
    <Screen title={t("nav.safety")}>
      <Segmented value={tab} onChange={setTab} style={{ marginBottom: S[3] }}
        options={[{ value: "lookup", label: t("safety.tab.lookup") }, { value: "blocked", label: t("safety.tab.blocked") }, { value: "trends", label: t("safety.tab.trends") }]} />
      {tab === "lookup" ? <LookupTab initial={route.params?.q} /> : null}
      {tab === "blocked" ? <BlockedTab /> : null}
      {tab === "trends" ? <TrendsTab /> : null}
    </Screen>
  );
}

function Fact({ icon, label, value, color }: { icon: IconName; label: string; value?: string; color?: string }) {
  return (
    <Row gap={S[2]} align="flex-start" style={{ paddingVertical: 6 }}>
      <Icon name={icon} size={17} color={color || C.textMuted} style={{ marginTop: 2 }} />
      <T v="body" color={color || C.textSecondary} style={{ flex: 1 }}>{label}</T>
      {value ? <T v="body" weight={600} color={color || C.text}>{value}</T> : null}
    </Row>
  );
}

// ------------------------------------------------------------------ look up --
function LookupTab({ initial }: { initial?: string }) {
  const { t, locale } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const [q, setQ] = useState(initial || "");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [note, setNote] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  const run = useCallback(async (text: string) => {
    const query = text.trim();
    const digits = query.replace(/\D/g, "");
    if (!(query.includes("@") ? VPA_RE.test(query) : digits.length >= 10 && !/[a-z]/i.test(query))) {
      setError(new ApiErr(t("safety.lookup.bad"), "VALIDATION_ERROR", 400));
      setRes(null);
      return;
    }
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      setRes(await api(`/v1/lookup?q=${encodeURIComponent(query)}`));
    } catch (e) {
      setRes(null);
      setError(e);
    } finally {
      setBusy(false);
    }
  }, [t]);

  useEffect(() => {
    if (initial) run(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const act = async (fn: () => Promise<string | null>) => {
    setActing(true);
    setError(null);
    try {
      setNote(await fn());
      await run(q);
    } catch (e) {
      setError(e);
    } finally {
      setActing(false);
    }
  };

  const pay = () => {
    const info: PayeeInfo = {
      vpa: res.vpa, exists: true, registered_name: res.registered_name, kind: res.payee_kind, verified: res.verified, band: res.band,
      band_reason: res.band_reason, trust_score: res.trust_score, paid_before: res.you?.paid_before,
    };
    startFlow(dispatch, { payee: info, entryMethod: "typed" });
    nav.push("payee");
  };

  return (
    <View>
      <T v="body" color={C.textSecondary} style={{ marginBottom: S[2] }}>{t("safety.lookup.hint")}</T>
      <Field label={t("safety.lookup.label")} placeholder={t("safety.lookup.placeholder")} value={q} onChangeText={setQ} autoCapitalize="none" autoCorrect={false}
        autoComplete="off" mono maxLength={120} onSubmitEditing={() => run(q)} />
      <Btn label={t("safety.lookup.btn")} icon="search" loading={busy} disabled={q.trim().length < 3} onPress={() => run(q)} />
      <ErrorNote error={error} />
      {note ? <Banner tone="safe" style={{ marginTop: S[2] }}>{note}</Banner> : null}

      {res?.kind === "vpa" && !res.exists ? (
        <View style={{ marginTop: S[3], gap: S[1] }}>
          <Banner tone="block" icon="slash">{t("safety.lookup.notFound")}</Banner>
          {res.you?.blocked ? <Chip label={t("block.onList")} color={C.block} bg={C.blockSubtle} icon="slash" /> : null}
        </View>
      ) : null}

      {res?.kind === "vpa" && res.exists ? (() => {
        const band = (res.band || "NO_HISTORY") as keyof typeof BAND;
        const look = BAND[band];
        const com = res.community || {};
        const tone = band === "REPORTED" ? C.block : band === "CAUTION" ? C.warning : C.border;
        const paid = res.you?.paid_before || 0;
        return (
          <View style={{ marginTop: S[3], gap: S[2] }}>
            <Card tone={tone}>
              <Row gap={S[2]} align="flex-start">
                <Avatar text={initials(res.registered_name)} color={look.color} bg={look.subtle} />
                <View style={{ flex: 1 }}>
                  <T v="caption" color={C.textMuted}>{t("payee.registered")}</T>
                  <T v="title">{res.registered_name}</T>
                  <Mono>{res.vpa}</Mono>
                </View>
              </Row>
              <Row wrap gap={6} style={{ marginTop: S[2] }}>
                {t(`band.${band}`) ? <Chip label={t(`band.${band}`)} color={look.color} bg={look.subtle} icon={look.icon as any} /> : null}
                {res.verified ? <Chip label={t("payee.verified")} color={C.safe} bg={C.safeSubtle} icon="check" /> : null}
                <Chip label={t(res.payee_kind === "merchant" ? "payee.merchant" : "payee.person")} icon={res.payee_kind === "merchant" ? "shopping-bag" : "user"} />
                {res.you?.blocked ? <Chip label={t("block.onList")} color={C.block} bg={C.blockSubtle} icon="slash" /> : null}
              </Row>
              {res.band_reason ? <T v="body" color={look.color} style={{ marginTop: S[1] }}>{res.band_reason}</T> : null}
              <View style={{ marginTop: S[2] }}>
                <Fact icon="calendar" label={t("safety.age")} value={res.age_days < 1 ? t("safety.ageToday") : t("safety.ageValue", { age: span(res.age_days * 86400, locale) })} />
                <Fact icon="flag" label={t("safety.reports")} value={t(`safety.range.${com.reports_30d || "0"}`)} color={com.reports_30d && com.reports_30d !== "0" ? C.warning : undefined} />
                <Fact icon="x-circle" label={t("safety.stopped")} value={t(`safety.range.${com.stopped_14d || "0"}`)} color={com.stopped_14d && com.stopped_14d !== "0" ? C.warning : undefined} />
                {com.confirmed_fraud ? <Fact icon="alert-octagon" label={t("safety.confirmed")} color={C.block} /> : null}
                {com.other_bank ? <Fact icon="share-2" label={t("safety.otherBank")} color={C.warning} /> : null}
                <Fact icon="repeat" label={paid === 0 ? t("payee.neverPaid") : paid === 1 ? t("payee.paidOnce") : t("payee.paidBefore", { n: paid })} />
              </View>
            </Card>
            {(com.patterns || []).length ? (
              <Card>
                <Label>{t("safety.patterns")}</Label>
                {com.patterns.map((p: any) => (
                  <View key={p.code} style={{ marginBottom: S[1] }}>
                    <T v="heading">{p.name}</T>
                    <T v="body" color={C.textSecondary}>{p.advice}</T>
                  </View>
                ))}
              </Card>
            ) : null}
            {band === "NO_HISTORY" ? <Banner tone="info" icon="info">{t("safety.noHistory")}</Banner> : null}
            {res.you?.is_self ? <Banner tone="info">{t("safety.self")}</Banner> : (
              <View style={{ gap: S[1] }}>
                {res.you?.blocked ? (
                  <Btn variant="secondary" icon="list" label={t("block.openList")} onPress={() => nav.tab("safety", { tab: "blocked" })} />
                ) : (
                  <>
                    <Btn variant="danger" icon="slash" label={t("block.add")} loading={acting}
                      onPress={() => act(async () => { await api("/v1/users/me/blocks", { body: { vpa: res.vpa } }); return t("block.added"); })} />
                    <Btn variant="ghost" icon="send" label={t("safety.pay")} onPress={pay} />
                  </>
                )}
              </View>
            )}
          </View>
        );
      })() : null}

      {res?.kind === "phone" ? (() => {
        const risk = Math.max(0, Math.min(3, Number(res.risk) || 0));
        const color = risk >= 2 ? C.block : risk === 1 ? C.warning : C.textSecondary;
        const bg = risk >= 2 ? C.blockSubtle : risk === 1 ? C.warningSubtle : C.neutralChip;
        return (
          <View style={{ marginTop: S[3], gap: S[2] }}>
            <Card tone={risk >= 2 ? C.block : risk === 1 ? C.warning : C.border}>
              <Row gap={S[2]} align="flex-start">
                <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: bg, alignItems: "center", justifyContent: "center" }}>
                  <Icon name={risk >= 2 ? "phone-off" : "phone"} color={color} />
                </View>
                <View style={{ flex: 1 }}>
                  <T v="title" color={risk >= 1 ? color : C.text}>{t(`safety.phone.title.${risk}`)}</T>
                  <Mono>{t("safety.phone.ending", { n: res.phone_last4 })}</Mono>
                </View>
              </Row>
              <View style={{ marginTop: S[2] }}>
                {(res.sources || []).map((src: string) => <Fact key={src} icon="alert-triangle" label={t(`safety.source.${src}`)} color={color} />)}
                <Fact icon="users" label={t("safety.phone.people")} value={t(`safety.range.${res.reports || "0"}`)} />
                {res.called_you ? <Fact icon="phone-incoming" label={t("safety.phone.calledYou", { n: res.called_you.count_30d })} /> : null}
              </View>
            </Card>
            {risk === 0 ? <Banner tone="info" icon="info">{t("safety.phone.noProof")}</Banner> : null}
            {res.is_self ? <Banner tone="info">{t("safety.phone.self")}</Banner>
              : res.reported_by_you ? <Banner tone="safe" icon="check">{t("safety.phone.reportedByYou")}</Banner>
              : (
                <View>
                  <Btn variant="secondary" icon="flag" label={t("safety.phone.reportBtn")} loading={acting}
                    onPress={() => act(async () => { await api("/v1/callers/report", { body: { phone: q.replace(/\D/g, ""), category: "scam" } }); return t("call.reported"); })} />
                  <T v="caption" color={C.textMuted} style={{ marginTop: S[1] }}>{t("call.reportHelp")}</T>
                </View>
              )}
          </View>
        );
      })() : null}
    </View>
  );
}

// ------------------------------------------------------------ never-pay list --
function BlockedTab() {
  const { t, locale } = useT();
  const tick = useAppSelector((s) => s.overlay.tick);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [vpa, setVpa] = useState("");
  const [report, setReport] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [, setNow] = useState(0);

  const load = useCallback(async () => {
    try {
      setData(await api("/v1/users/me/blocks"));
    } catch (e) {
      setError(e);
      setData((d: any) => d || { blocks: [] });
    }
  }, []);
  useEffect(() => {
    load();
  }, [load, tick]);
  useEffect(() => onEvent("block.lifted", () => load()), [load]);

  const pending = (data?.blocks || []).some((b: any) => b.status === "UNBLOCK_PENDING");
  useEffect(() => {
    if (!pending) return;
    const id = setInterval(() => setNow((x) => x + 1), 1000);       // the countdown
    const poll = setInterval(load, 5000);
    return () => {
      clearInterval(id);
      clearInterval(poll);
    };
  }, [pending, load]);

  const call = async (key: string, fn: () => Promise<any>) => {
    setBusy(key);
    setError(null);
    setNote(null);
    try {
      const r = await fn();
      setData(r);
      return r;
    } catch (e) {
      setError(e);
      return null;
    } finally {
      setBusy(null);
    }
  };

  const add = async () => {
    const r = await call("add", () => api("/v1/users/me/blocks", { body: { vpa: vpa.trim().toLowerCase(), report } }));
    if (r) {
      setNote(r.exists === false ? t("safety.blocked.notInDirectory") : t("block.added"));
      setVpa("");
      setReport(false);
    }
  };

  const delay = data?.unblock_delay_s ? span(data.unblock_delay_s, locale) : "";
  return (
    <View>
      <T v="body" color={C.textSecondary} style={{ marginBottom: S[2] }}>{t("safety.blocked.hint")}</T>
      <Card>
        <Field label={t("safety.blocked.add")} placeholder={t("payee.vpaHint")} value={vpa} onChangeText={(v) => setVpa(v.trim().toLowerCase())} autoCapitalize="none"
          autoCorrect={false} autoComplete="off" keyboardType="email-address" mono maxLength={120} />
        <Toggle label={t("safety.blocked.alsoReport")} value={report} onChange={setReport} />
        <View style={{ height: S[2] }} />
        <Btn variant="danger" icon="slash" label={t("safety.blocked.addBtn")} disabled={!VPA_RE.test(vpa.trim())} loading={busy === "add"} onPress={add} />
      </Card>
      <ErrorNote error={error} onRetry={load} />
      {note ? <Banner tone="safe" style={{ marginTop: S[2] }}>{note}</Banner> : null}
      <View style={{ marginTop: S[3], gap: S[1] }}>
        {data === null ? <Loading /> : null}
        {data && data.blocks.length === 0 ? <Empty icon="shield" text={t("safety.blocked.empty")} /> : null}
        {(data?.blocks || []).map((b: any) => {
          const waiting = b.status === "UNBLOCK_PENDING";
          const left = waiting && b.unblock_at ? Math.max(0, (Date.parse(b.unblock_at) - Date.now()) / 1000) : 0;
          return (
            <Card key={b.vpa} pad={S[2]} tone={waiting ? C.warning : undefined}>
              <Row gap={S[2]} align="flex-start">
                <Icon name="slash" color={waiting ? C.warning : C.block} style={{ marginTop: 2 }} />
                <View style={{ flex: 1 }}>
                  {b.name ? <T v="body" weight={600} numberOfLines={1}>{b.name}</T> : null}
                  <Mono size={12.5}>{b.vpa}</Mono>
                  {b.reason ? <T v="caption" color={C.textMuted}>{b.reason}</T> : null}
                  {waiting ? <T v="caption" color={C.warning} style={{ marginTop: 4 }}>{t("safety.blocked.lifts", { time: duration(left, locale) })}</T> : null}
                </View>
              </Row>
              <View style={{ alignItems: "flex-start", marginTop: S[1] }}>
                {waiting ? (
                  <Btn small full={false} variant="secondary" icon="shield" label={t("safety.blocked.keep")} loading={busy === b.vpa}
                    onPress={() => call(b.vpa, () => api(`/v1/users/me/blocks/${encodeURIComponent(b.vpa)}/keep`, { method: "POST" }))} />
                ) : (
                  <Link label={t("safety.blocked.remove")} color={C.textSecondary}
                    onPress={() => call(b.vpa, () => api(`/v1/users/me/blocks/${encodeURIComponent(b.vpa)}`, { method: "DELETE" }))} />
                )}
              </View>
            </Card>
          );
        })}
      </View>
      {delay ? <T v="caption" color={C.textMuted} style={{ marginTop: S[3] }}>{t("safety.blocked.wait", { time: delay })}</T> : null}
    </View>
  );
}

// -------------------------------------------------------------------- trends --
const open = (url: string) => Linking.openURL(url).catch(() => undefined);

function TrendsTab() {
  const { t } = useT();
  const nav = useNav();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<any>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api("/v1/trends"));
    } catch (e) {
      setError(e);
      setData((d: any) => d || { trends: [] });
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const trendLook = (k: string) => (k === "up" || k === "new" ? { color: C.block, bg: C.blockSubtle, icon: "trending-up" as IconName }
    : k === "down" ? { color: C.safe, bg: C.safeSubtle, icon: "trending-down" as IconName } : { color: C.textSecondary, bg: C.neutralChip, icon: "minus" as IconName });

  return (
    <View>
      <Label>{t("safety.trends.title")}</Label>
      <ErrorNote error={error} onRetry={load} />
      {data === null ? <Loading /> : null}
      {data && data.trends.length === 0 ? <Empty icon="activity" text={t("safety.trends.empty")} /> : null}
      <View style={{ gap: S[1] }}>
        {(data?.trends || []).map((it: any) => {
          const look = trendLook(it.trend);
          return (
            <Card key={it.code}>
              <Row style={{ justifyContent: "space-between" }} gap={S[1]} align="flex-start">
                <T v="heading" style={{ flex: 1 }}>{it.name}</T>
                <Chip label={t(`safety.trends.${it.trend}`)} color={look.color} bg={look.bg} icon={look.icon} />
              </Row>
              <T v="caption" color={C.textMuted} style={{ marginTop: 2 }}>{t("safety.trends.seen", { n: it.seen })}</T>
              <T v="body" color={C.textSecondary} style={{ marginTop: S[1] }}>{it.advice}</T>
              <View style={{ alignItems: "flex-start", marginTop: S[1] }}>
                <Link label={t("safety.trends.practice")} icon="play-circle" onPress={() => nav.tab("practice", { family: it.code })} />
              </View>
            </Card>
          );
        })}
      </View>

      <View style={{ marginTop: S[4] }}>
        <Label>{t("safety.help.title")}</Label>
        <View style={{ gap: S[1] }}>
          <Card pad={S[2]} onPress={call1930}>
            <Row gap={S[2]}><Icon name="phone" color={C.block} /><T v="body" style={{ flex: 1 }}>{t("safety.help.1930")}</T></Row>
          </Card>
          <Card pad={S[2]} onPress={() => open("https://cybercrime.gov.in")}>
            <Row gap={S[2]}><Icon name="external-link" color={C.accentCyan} /><T v="body" style={{ flex: 1 }}>{t("safety.help.portal")}</T></Row>
          </Card>
          <Card pad={S[2]} onPress={() => open("https://sancharsaathi.gov.in")}>
            <Row gap={S[2]}><Icon name="external-link" color={C.accentCyan} /><T v="body" style={{ flex: 1 }}>{t("safety.help.sanchar")}</T></Row>
          </Card>
          <Card pad={S[2]}>
            <Row gap={S[2]}><Icon name="credit-card" color={C.textMuted} /><T v="body" color={C.textSecondary} style={{ flex: 1 }}>{t("safety.help.bank")}</T></Row>
          </Card>
        </View>
      </View>
    </View>
  );
}
