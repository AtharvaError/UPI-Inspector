// P-12 Check a message (F-16). Paste what you received; see the verdict, the red flags and what to do.
import React, { useEffect, useState } from "react";
import { Text, View } from "react-native";
import { api } from "../api";
import { Banner, Btn, Card, Chip, ErrorNote, Field, Icon, Label, Mono, Row, Screen, T } from "../components/ui";
import { resolvePayee, startFlow } from "../flow";
import { dateTime, useT } from "../i18n";
import { useNav } from "../nav";
import { Route, useAppDispatch, useAppSelector } from "../store";
import { C, R, S, font } from "../../theme";

type Result = {
  check_id: string; verdict: "SCAM" | "CAREFUL" | "FINE"; scam_prob: number; label: string;
  family: { code: string; name: string; headline: string } | null;
  red_flags: { start: number; end: number; kind: string; why: string }[];
  entities: { vpas: string[]; phones: string[]; urls: string[]; amounts: number[] };
  advice: string[]; flag_set: boolean;
};

const LOOK = {
  SCAM: { color: C.block, bg: C.blockSubtle, icon: "x-octagon" as const, key: "msg.verdict.scam" },
  CAREFUL: { color: C.warning, bg: C.warningSubtle, icon: "alert-triangle" as const, key: "msg.verdict.careful" },
  FINE: { color: C.safe, bg: C.safeSubtle, icon: "check-circle" as const, key: "msg.verdict.fine" },
};

function Highlighted({ text, flags, onPick, picked }: { text: string; flags: Result["red_flags"]; onPick: (i: number) => void; picked: number | null }) {
  const { locale, scale } = useT();
  // The server counts characters, not UTF-16 units, so slice by character (an emoji is one, not two).
  const chars = Array.from(text);
  const cut = (a: number, b?: number) => chars.slice(a, b).join("");
  const parts: React.ReactNode[] = [];
  let at = 0;
  flags.forEach((f, i) => {
    if (f.start > at) parts.push(<Text key={`t${i}`}>{cut(at, f.start)}</Text>);
    parts.push(
      <Text key={`f${i}`} onPress={() => onPick(i)} accessibilityRole="button"
        style={{ backgroundColor: picked === i ? C.block : C.blockSubtle, color: picked === i ? C.blockOn : C.text, fontWeight: "600" }}>
        {cut(f.start, f.end)}
      </Text>,
    );
    at = f.end;
  });
  if (at < chars.length) parts.push(<Text key="tail">{cut(at)}</Text>);
  return <Text selectable style={[font(400, locale), { color: C.text, fontSize: 16 * scale, lineHeight: 25 * scale }]}>{parts}</Text>;
}

export default function Message({ route }: { route: Route }) {
  const { t, locale } = useT();
  const nav = useNav();
  const [text, setText] = useState<string>(route.params?.text || "");
  const [checked, setChecked] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [history, setHistory] = useState<any[]>([]);
  const dispatch = useAppDispatch();
  const demo = useAppSelector((s) => s.session.demoMode);
  const link = route.params?.link as { vpa: string; name?: string | null; amount?: number; note?: string } | undefined;
  const [opening, setOpening] = useState(false);

  // Demo only: what happens if the payer follows the message anyway. The payment starts as a tapped link would.
  const followLink = async () => {
    if (!link) return;
    setOpening(true);
    setError(null);
    try {
      const info = await resolvePayee(link.vpa, link.name || undefined);
      if (info.exists) {
        startFlow(dispatch, { payee: info, claimedName: link.name || "", entryMethod: "link", amount: link.amount ? String(link.amount) : "", note: link.note || "" });
        nav.push("amount");
      }
    } catch (e) {
      setError(e);
    } finally {
      setOpening(false);
    }
  };

  const loadHistory = () => api("/v1/signals/messages").then((r) => setHistory(r.checks)).catch(() => undefined);
  useEffect(() => {
    loadHistory();
  }, []);

  const check = async (body?: string) => {
    const msg = (body ?? text).trim();
    if (msg.length < 3) return;
    setBusy(true);
    setError(null);
    setPicked(null);
    try {
      const r = await api<Result>("/v1/signals/message", { body: { text: msg, source: route.params?.text ? "sms" : "paste" } });
      setRes(r);
      setChecked(msg);
      loadHistory();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  // a message opened from the simulated SMS banner is checked straight away
  useEffect(() => {
    if (route.params?.text) check(route.params.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const again = () => {
    setRes(null);
    setText("");
    setChecked("");
  };

  const look = res ? LOOK[res.verdict] : null;
  const found = res ? [
    { label: t("msg.vpas"), items: res.entities.vpas },
    { label: t("msg.phones"), items: res.entities.phones },
    { label: t("msg.urls"), items: res.entities.urls },
  ].filter((x) => x.items?.length) : [];

  return (
    <Screen title={t("nav.message")}>
      <ErrorNote error={error} />
      {!res ? (
        <View>
          <Field value={text} onChangeText={setText} multiline placeholder={t("msg.placeholder")} maxLength={2000} hint={t("msg.privacy")} />
          <Btn label={t("msg.check")} icon="search" onPress={() => check()} loading={busy} disabled={text.trim().length < 3} />
        </View>
      ) : null}

      {res && look ? (
        <View style={{ gap: S[2] }}>
          <Card tone={look.color} style={{ backgroundColor: look.bg }}>
            <Row gap={S[2]}>
              <Icon name={look.icon} size={30} color={look.color} />
              <View style={{ flex: 1 }}>
                <T v="title" color={look.color}>{t(look.key)}</T>
                {res.family ? <T v="body">{res.family.headline}</T> : null}
              </View>
            </Row>
          </Card>

          <Card>
            <Highlighted text={checked} flags={res.red_flags} picked={picked} onPick={(i) => setPicked(picked === i ? null : i)} />
            {res.red_flags.length > 0 ? (
              picked !== null && res.red_flags[picked] ? (
                <View style={{ marginTop: S[2], backgroundColor: C.surfaceRaised, borderRadius: R.md, padding: S[2] }}>
                  <T v="body">{res.red_flags[picked].why}</T>
                </View>
              ) : (
                <T v="caption" color={C.textMuted} style={{ marginTop: S[2] }}>{t("msg.tapHint")}</T>
              )
            ) : null}
          </Card>

          {found.length > 0 ? (
            <Card>
              <Label>{t("msg.found")}</Label>
              {found.map((g) => (
                <View key={g.label} style={{ marginBottom: S[1] }}>
                  <T v="caption" color={C.textSecondary}>{g.label}</T>
                  {g.items.map((it) => <Mono key={it} color={C.text}>{it}</Mono>)}
                </View>
              ))}
            </Card>
          ) : null}

          <Card>
            <Label>{t("msg.advice")}</Label>
            <View style={{ gap: S[1] }}>
              {res.advice.map((a, i) => (
                <Row key={i} align="flex-start" gap={S[1]}>
                  <Icon name="chevron-right" size={16} color={look.color} style={{ marginTop: 4 }} />
                  <T v="bodyLarge" style={{ flex: 1 }}>{a}</T>
                </Row>
              ))}
            </View>
          </Card>

          {res.flag_set ? <Banner tone="info" icon="shield">{t("msg.flag.set")}</Banner> : null}

          {demo && link && checked === String(route.params?.text || "").trim() ? (
            <Btn variant="quiet" icon="external-link" label={t("msg.openLink")} loading={opening} onPress={followLink} small />
          ) : null}

          <Row gap={S[1]}>
            <Btn variant="ghost" label={t("msg.another")} onPress={again} style={{ flex: 1 }} full={false} />
            {res.verdict === "SCAM" ? (
              <Btn variant="danger" icon="flag" label={t("msg.report")} onPress={() => nav.push("report", { check_id: res.check_id })} style={{ flex: 1 }} full={false} />
            ) : null}
          </Row>
        </View>
      ) : null}

      {!res && history.length > 0 ? (
        <View style={{ marginTop: S[4] }}>
          <Label>{t("msg.previous")}</Label>
          <View style={{ gap: S[1] }}>
            {history.slice(0, 8).map((h) => {
              const l = LOOK[(h.verdict as keyof typeof LOOK) || "FINE"] || LOOK.FINE;
              return (
                <Card key={h.check_id} pad={S[2]} onPress={() => { setText(h.text); check(h.text); }}>
                  <Row gap={S[1]} style={{ marginBottom: 4 }}>
                    <Chip label={t(l.key)} color={l.color} bg={l.bg} icon={l.icon} />
                    <T v="caption" color={C.textMuted}>{dateTime(h.created_at, locale)}</T>
                  </Row>
                  <T v="body" color={C.textSecondary} numberOfLines={2}>{h.text}</T>
                </Card>
              );
            })}
          </View>
        </View>
      ) : null}
    </Screen>
  );
}
