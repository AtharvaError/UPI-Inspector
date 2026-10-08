// The reputation chip (F-04) and the name-mismatch line (F-22): who the money would really go to,
// shown before any amount is typed.
import React from "react";
import { View } from "react-native";
import { initials, useT } from "../i18n";
import { PayeeInfo } from "../store";
import { BAND, C, S } from "../../theme";
import { Avatar, Banner, Card, Chip, Mono, Row, T } from "./ui";

export function PayeeCard({ info, compact }: { info: PayeeInfo; compact?: boolean }) {
  const { t } = useT();
  const band = info.band || "NO_HISTORY";
  const look = BAND[band];
  const label = t(`band.${band}`);
  const tone = band === "REPORTED" ? C.block : band === "CAUTION" ? C.warning : C.border;
  const paid = info.paid_before ?? 0;
  return (
    <View style={{ gap: S[1] }}>
      <Card tone={tone}>
        <Row gap={S[2]} align="flex-start">
          <Avatar text={initials(info.registered_name)} color={look.color} bg={look.subtle} />
          <View style={{ flex: 1 }}>
            <T v="caption" color={C.textMuted}>{t("payee.registered")}</T>
            <T v="title">{info.registered_name}</T>
            <Mono>{info.vpa}</Mono>
          </View>
        </Row>
        <Row wrap gap={6} style={{ marginTop: S[2] }}>
          {label ? <Chip label={label} color={look.color} bg={look.subtle} icon={look.icon as any} /> : null}
          {info.verified ? <Chip label={t("payee.verified")} color={C.safe} bg={C.safeSubtle} icon="check" /> : null}
          {!compact ? <Chip label={t(info.kind === "merchant" ? "payee.merchant" : "payee.person")} icon={info.kind === "merchant" ? "shopping-bag" : "user"} /> : null}
        </Row>
        {info.band_reason && (band === "CAUTION" || band === "REPORTED") ? (
          <T v="body" color={look.color} style={{ marginTop: S[1] }}>{info.band_reason}</T>
        ) : null}
        {!compact && info.paid_before !== undefined ? (
          <T v="caption" color={C.textSecondary} style={{ marginTop: S[1] }}>
            {paid === 0 ? t("payee.neverPaid") : paid === 1 ? t("payee.paidOnce") : t("payee.paidBefore", { n: paid })}
          </T>
        ) : null}
      </Card>
      {info.name_match?.mismatch ? <Banner tone="warning" icon="user-x">{t("payee.mismatch")}</Banner> : null}
      {info.blocked ? <Banner tone="block" icon="slash">{t("payee.blocked")}</Banner> : null}
    </View>
  );
}
