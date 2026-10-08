// P-05 Amount and note. Tapping Pay asks for the risk decision before any PIN screen exists.
// A Warning is shown here, inline: one banner, the reasons on request, and the button changes.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Text, TextInput, View } from "react-native";
import { PayeeCard } from "../components/PayeeCard";
import { Banner, Btn, ErrorNote, FadeIn, Field, Link, Reasons, Row, Screen, T } from "../components/ui";
import { assessPayment, finishFlow, proceedToPin, routeDecision, sendOutcome } from "../flow";
import { money, useT } from "../i18n";
import { useBackGuard, useNav } from "../nav";
import { actions, Decision, useAppDispatch, useAppSelector } from "../store";
import { C, S, font, isWeb } from "../../theme";

export default function Amount() {
  const { t, locale, scale } = useT();
  const nav = useNav();
  const dispatch = useAppDispatch();
  const flow = useAppSelector((s) => s.flow);
  const me = useAppSelector((s) => s.session.me);
  const [amount, setAmount] = useState(flow.amount || "");
  const [note, setNote] = useState(flow.note || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<any>(null);
  const [warn, setWarn] = useState<Decision | null>(flow.decision?.action === "WARN" ? flow.decision : null);
  const [degraded, setDegraded] = useState(false);
  const [showWhy, setShowWhy] = useState(false);
  const openedAt = useRef(Date.now());
  const edits = useRef(0);
  const deleting = useRef(false);

  const value = Number(amount);
  const valid = amount !== "" && isFinite(value) && value > 0;
  const tooMuch = valid && value > (me?.balance ?? 0);

  // A decision belongs to one amount and one note. Changing either withdraws it.
  const dropWarning = useCallback(() => {
    if (warn) {
      sendOutcome(warn.decision_id, "ABORTED");
      setWarn(null);
      setShowWhy(false);
      dispatch(actions.flow.patched({ decision: null }));
    }
  }, [warn, dispatch]);

  const onAmount = (raw: string) => {
    let v = raw.replace(/[^0-9.]/g, "");
    const dot = v.indexOf(".");
    if (dot >= 0) v = v.slice(0, dot + 1) + v.slice(dot + 1).replace(/\./g, "").slice(0, 2);
    v = v.replace(/^0+(?=\d)/, "").slice(0, 10);
    // an "edit" is a burst of deleting after something was typed: the amount being changed on instruction
    if (v.length < amount.length) {
      if (!deleting.current) edits.current += 1;
      deleting.current = true;
    } else {
      deleting.current = false;
    }
    setAmount(v);
    dropWarning();
  };

  const pay = async () => {
    if (!valid || tooMuch || busy) return;
    setBusy(true);
    setError(null);
    dispatch(actions.flow.patched({ amount, note }));
    try {
      const d = await assessPayment({ timeOnPayScreenS: (Date.now() - openedAt.current) / 1000, amountEdits: edits.current });
      setDegraded(!!d.degraded);
      if (d.action === "WARN") setWarn(d);
      else routeDecision(nav, d);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (warn) await sendOutcome(warn.decision_id, "ABORTED");
    finishFlow(nav, { kind: "cancelled" });
  };

  const back = useCallback(() => {
    if (warn) sendOutcome(warn.decision_id, "ABORTED");
    dispatch(actions.flow.patched({ amount, note, decision: null }));
    nav.pop();
    return true;
  }, [warn, amount, note, dispatch, nav]);
  useBackGuard(back);

  useEffect(() => {
    if (!flow.payee) nav.home();
  }, [flow.payee, nav]);
  if (!flow.payee) return null;

  const fam = warn?.scam_family;
  return (
    <Screen title={t("amount.title")} onBack={back}
      footer={warn ? (
        <Row gap={S[1]}>
          <Btn variant="ghost" label={t("common.cancel")} onPress={cancel} style={{ flex: 1 }} full={false} />
          <Btn label={t("continue.anyway")} onPress={() => proceedToPin(nav, warn)} style={{ flex: 1.4 }} full={false} />
        </Row>
      ) : (
        <Btn label={busy ? t("amount.checking") : valid ? t("amount.payBtn", { amount: money(value) }) : t("amount.enter")} loading={busy} disabled={!valid || tooMuch} onPress={pay} icon={busy ? undefined : "lock"} />
      )}>
      <PayeeCard info={flow.payee} compact />

      <View style={{ alignItems: "center", marginTop: S[4], marginBottom: S[2] }}>
        <Row gap={4} style={{ justifyContent: "center" }}>
          <Text style={[font(700, locale), { color: amount ? C.text : C.textMuted, fontSize: 34 * scale }]}>₹</Text>
          <TextInput
            accessibilityLabel={t("amount.title")}
            value={amount}
            onChangeText={onAmount}
            keyboardType="decimal-pad"
            placeholder="0"
            placeholderTextColor={C.textMuted}
            selectionColor={C.brand}
            autoFocus={!flow.amount}
            maxLength={10}
            style={[font(700, locale), { color: C.text, fontSize: 40 * scale, minWidth: 60, textAlign: "center", paddingVertical: S[1] }, isWeb ? ({ outlineStyle: "none", width: Math.max(60, (amount.length || 1) * 26 * scale) } as any) : null]}
          />
        </Row>
        {tooMuch ? <T v="caption" color={C.block}>{t("amount.low")}</T> : null}
      </View>

      <Field label={`${t("amount.note")} (${t("common.optional")})`} placeholder={t("amount.noteHint")} value={note} maxLength={200}
        onChangeText={(v) => { setNote(v); dropWarning(); }} />

      <ErrorNote error={error} onRetry={pay} />
      {degraded && warn ? <Banner tone="info" style={{ marginBottom: S[2] }}>{t("amount.degraded")}</Banner> : null}

      {warn ? (
        <FadeIn>
          <Banner tone="warning" title={t("tier.warning.title")}>
            {fam?.headline ? <T v="bodyLarge" weight={600} style={{ marginTop: 2 }}>{fam.headline}</T> : null}
            {warn.reasons[0] ? <T v="bodyLarge" style={{ marginTop: 4 }}>{warn.reasons[0].message}</T> : null}
            {warn.reasons.length > 1 || fam?.body ? (
              <Link label={showWhy ? t("common.hide") : t("common.seeWhy")} icon={showWhy ? "chevron-up" : "chevron-down"} color={C.warning} onPress={() => setShowWhy((x) => !x)} />
            ) : null}
            {showWhy ? (
              <View style={{ gap: S[1] }}>
                {fam?.body ? <T v="body" color={C.textSecondary}>{fam.body}</T> : null}
                <Reasons reasons={warn.reasons.slice(1)} />
              </View>
            ) : null}
          </Banner>
        </FadeIn>
      ) : null}
    </Screen>
  );
}
