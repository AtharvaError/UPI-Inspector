// History: the payer's own payments, with a route to report any of them (P-15).
import React, { useCallback, useEffect, useState } from "react";
import { View } from "react-native";
import { api } from "../api";
import { Btn, Card, Chip, Empty, ErrorNote, Loading, Mono, Row, Screen, T } from "../components/ui";
import { dateTime, money, useT } from "../i18n";
import { useNav } from "../nav";
import { useAppSelector } from "../store";
import { C, S, TIER } from "../../theme";

function statusOf(p: any): { key: string; color: string; bg: string } {
  if (p.status === "SUCCESS") return { key: "SUCCESS", color: C.safe, bg: C.safeSubtle };
  if (p.action === "DECLINE" || p.status === "BLOCKED") return { key: "BLOCKED", color: C.block, bg: C.blockSubtle };
  if (p.action === "HOLD" && !p.user_action) return { key: "HELD", color: C.stepup, bg: C.stepupSubtle };
  if (p.status === "FAILED") return { key: "FAILED", color: C.block, bg: C.blockSubtle };
  if (p.user_action || p.status === "ABORTED") return { key: "ABORTED", color: C.neutralChipText, bg: C.neutralChip };
  return { key: "PENDING", color: C.neutralChipText, bg: C.neutralChip };
}

export default function History() {
  const { t, locale } = useT();
  const nav = useNav();
  const tick = useAppSelector((s) => s.overlay.tick);
  const [rows, setRows] = useState<any[] | null>(null);
  const [error, setError] = useState<any>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => api("/v1/users/me/payments?limit=60").then((r) => { setRows(r.payments); setError(null); }).catch((e) => { setError(e); setRows((x) => x || []); }), []);
  useEffect(() => {
    load();
  }, [load, tick]);

  return (
    <Screen title={t("nav.history")}>
      <ErrorNote error={error} onRetry={load} />
      {rows === null ? <Loading /> : null}
      {rows && rows.length === 0 && !error ? <Empty icon="list" text={t("history.empty")} /> : null}
      <View style={{ gap: S[1] }}>
        {(rows || []).map((p) => {
          const st = statusOf(p);
          const tier = p.tier && p.tier !== "SAFE" ? TIER[p.tier as keyof typeof TIER] : null;
          const expanded = open === p.txn_id;
          return (
            <Card key={p.txn_id} pad={S[2]} onPress={() => setOpen(expanded ? null : p.txn_id)}>
              <Row style={{ justifyContent: "space-between" }} align="flex-start" gap={S[2]}>
                <View style={{ flex: 1 }}>
                  <T v="body" weight={600} numberOfLines={1}>{p.payee_name || p.payee_vpa}</T>
                  <T v="caption" color={C.textMuted}>{dateTime(p.created_at, locale)}</T>
                </View>
                <T v="heading" color={p.status === "SUCCESS" ? C.text : C.textSecondary}>₹{money(p.amount)}</T>
              </Row>
              <Row gap={6} wrap style={{ marginTop: 6 }}>
                <Chip label={t(`history.status.${st.key}`)} color={st.color} bg={st.bg} />
                {tier ? <Chip icon={tier.icon as any} color={tier.color} bg={tier.subtle} label={t(p.tier === "WARNING" ? "tier.warning.title" : p.tier === "STEP_UP" ? "tier.stepup.title" : p.action === "HOLD" ? "tier.hold.title" : "tier.decline.title")} /> : null}
              </Row>
              {expanded ? (
                <View style={{ marginTop: S[2], gap: 6 }}>
                  <Mono size={12.5}>{p.payee_vpa}</Mono>
                  {p.note ? <T v="caption" color={C.textSecondary}>{t("history.note")}: {p.note}</T> : null}
                  <Mono size={11.5} color={C.textMuted}>{p.txn_id}</Mono>
                  <Btn small variant="ghost" icon="flag" label={t("history.report")} style={{ marginTop: S[1] }}
                    onPress={() => nav.push("report", p.decision_id ? { decision_id: p.decision_id } : { txn_id: p.txn_id })} />
                </View>
              ) : null}
            </Card>
          );
        })}
      </View>
    </Screen>
  );
}
