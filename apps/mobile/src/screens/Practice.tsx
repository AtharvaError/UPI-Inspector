// P-16 Practice (F-28): rehearse a scam as a chat, choose replies, and see why each choice was safe or risky.
import React, { useEffect, useRef, useState } from "react";
import { ScrollView, View } from "react-native";
import { api } from "../api";
import { Banner, Btn, Card, Chip, Empty, ErrorNote, FadeIn, Icon, Label, Loading, Row, Screen, T } from "../components/ui";
import { useT } from "../i18n";
import { useNav } from "../nav";
import { Route } from "../store";
import { C, R, S } from "../../theme";

export default function Practice({ route }: { route: Route }) {
  const { t } = useT();
  const nav = useNav();
  const [scripts, setScripts] = useState<any[] | null>(null);
  const [error, setError] = useState<any>(null);
  const want = route.params?.family;

  const load = () => api("/v1/rehearsals").then((r) => { setScripts(r.scripts); setError(null); }).catch((e) => { setError(e); setScripts([]); });
  useEffect(() => {
    load();
  }, []);

  const list = [...(scripts || [])].sort((a, b) => Number(b.family === want) - Number(a.family === want));
  return (
    <Screen title={t("nav.practice")}>
      <Banner tone="info" icon="play-circle" style={{ marginBottom: S[2] }}>{t("practice.banner")}</Banner>
      <T v="body" color={C.textSecondary} style={{ marginBottom: S[2] }}>{t("practice.intro")}</T>
      <ErrorNote error={error} onRetry={load} />
      {scripts === null ? <Loading /> : null}
      {scripts && scripts.length === 0 && !error ? <Empty icon="play-circle" text="—" /> : null}
      <View style={{ gap: S[1] }}>
        {list.map((s) => (
          <Card key={s.code} tone={s.family === want ? C.brand : undefined} onPress={() => nav.push("rehearsal", { code: s.code })}>
            <Row gap={S[2]}>
              <View style={{ flex: 1 }}>
                <T v="heading">{s.title}</T>
                <Row gap={6} wrap style={{ marginTop: 6 }}>
                  <Chip label={s.family_name} />
                  <Chip icon="clock" label={t("practice.minutes", { n: s.minutes })} />
                  {s.completed ? <Chip icon="check" color={C.safe} bg={C.safeSubtle} label={t("practice.done", { a: s.best ?? 0, b: s.total ?? 0 })} /> : null}
                </Row>
              </View>
              <Icon name="chevron-right" color={C.textMuted} />
            </Row>
          </Card>
        ))}
      </View>
    </Screen>
  );
}

type Turn = { node: string; speaker: string; text: string; choice?: { index: number; label: string; verdict: string; feedback: string } };

export function Rehearsal({ route }: { route: Route }) {
  const { t } = useT();
  const nav = useNav();
  const code = route.params?.code;
  const [script, setScript] = useState<any>(null);
  const [error, setError] = useState<any>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);   // the node to show after the feedback is read
  const [score, setScore] = useState<{ safe_choices: number; total_choices: number } | null>(null);
  const scroller = useRef<ScrollView>(null);

  useEffect(() => {
    api(`/v1/rehearsals/${code}`)
      .then((r) => {
        setScript(r);
        setCurrent(r.script.start);
      })
      .catch(setError);
  }, [code]);

  useEffect(() => {
    const id = setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 80);
    return () => clearTimeout(id);
  }, [turns, current, pending, score]);

  const nodes = script?.script?.nodes || {};
  const node = current ? nodes[current] : null;
  const ended = !!node && (!node.options || node.options.length === 0);

  useEffect(() => {
    if (!ended || score || !script) return;
    const choices = turns.filter((x) => x.choice).map((x) => ({ node: x.node, option: x.choice!.index }));
    api(`/v1/rehearsals/${code}/runs`, { body: { choices } }).then(setScore).catch(() => {
      const safe = turns.filter((x) => x.choice?.verdict === "safe").length;
      setScore({ safe_choices: safe, total_choices: choices.length });
    });
  }, [ended, score, script, turns, code]);

  const choose = (i: number) => {
    if (!node || !current) return;
    const o = node.options[i];
    setTurns((x) => [...x, { node: current, speaker: node.speaker, text: node.text, choice: { index: i, label: o.label, verdict: o.verdict, feedback: o.feedback } }]);
    setCurrent(null);
    setPending(o.next);
  };
  const next = () => {
    setCurrent(pending);
    setPending(null);
  };

  const bubble = (speaker: string, text: string, key: string) => (
    <FadeIn key={key} style={{ alignSelf: "flex-start", maxWidth: "88%", marginBottom: S[1] }}>
      <T v="caption" color={C.textMuted} style={{ marginBottom: 2 }}>{speaker === "narrator" ? t("practice.narrator") : t("practice.scammer")}</T>
      <View style={{ backgroundColor: speaker === "narrator" ? C.surface : C.surfaceRaised, borderRadius: R.lg, borderTopLeftRadius: 6, borderWidth: 1, borderColor: C.border, padding: S[2] }}>
        <T v="bodyLarge">{text}</T>
      </View>
    </FadeIn>
  );

  return (
    <Screen title={script?.title || ""} onBack={() => nav.pop()} scroll={false} pad={0}>
      <View style={{ paddingHorizontal: S[3] }}>
        <Banner tone="info" icon="play-circle">{t("practice.banner")}</Banner>
        <ErrorNote error={error} />
      </View>
      {!script && !error ? <Loading /> : null}
      <ScrollView ref={scroller} style={{ flex: 1 }} contentContainerStyle={{ padding: S[3], paddingBottom: S[6] }}>
        {turns.map((x, i) => (
          <View key={i}>
            {bubble(x.speaker, x.text, `b${i}`)}
            {x.choice ? (
              <View>
                <View style={{ alignSelf: "flex-end", maxWidth: "88%", backgroundColor: C.brand, borderRadius: R.lg, borderTopRightRadius: 6, padding: S[2], marginBottom: S[1] }}>
                  <T v="bodyLarge" color={C.brandOn}>{x.choice.label}</T>
                </View>
                <Banner tone={x.choice.verdict === "safe" ? "safe" : "block"} title={t(x.choice.verdict === "safe" ? "practice.safe" : "practice.risky")} style={{ marginBottom: S[2] }}>
                  {x.choice.feedback}
                </Banner>
              </View>
            ) : null}
          </View>
        ))}

        {pending ? <Btn variant="secondary" label={t("practice.next")} icon="arrow-right" onPress={next} /> : null}

        {node && !ended ? (
          <View>
            {bubble(node.speaker, node.text, `cur-${current}`)}
            <Label>{t("practice.choose")}</Label>
            <View style={{ gap: S[1] }}>
              {node.options.map((o: any, i: number) => (
                <Btn key={i} variant="ghost" label={o.label} onPress={() => choose(i)} style={{ minHeight: 56 }} />
              ))}
            </View>
          </View>
        ) : null}

        {node && ended ? (
          <FadeIn>
            {bubble(node.speaker, node.text, "end")}
            <Card tone={C.brand} style={{ marginTop: S[2] }}>
              {score ? <T v="title">{t("practice.score", { a: score.safe_choices, b: score.total_choices })}</T> : null}
              <Label>{t("practice.signs")}</Label>
              <View style={{ gap: S[1] }}>
                {(script.script.signs || []).map((sg: string, i: number) => (
                  <Row key={i} align="flex-start" gap={S[1]}>
                    <Icon name="alert-triangle" size={16} color={C.warning} style={{ marginTop: 4 }} />
                    <T v="bodyLarge" style={{ flex: 1 }}>{sg}</T>
                  </Row>
                ))}
              </View>
            </Card>
            <View style={{ height: S[2] }} />
            <Btn label={t("practice.another")} onPress={() => nav.pop()} />
          </FadeIn>
        ) : null}
      </ScrollView>
    </Screen>
  );
}
