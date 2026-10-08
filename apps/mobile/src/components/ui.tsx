// The payer app's component kit. Every colour, radius, size and duration comes from the design tokens.
import { Feather } from "@expo/vector-icons";
import React, { ReactNode, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator, Animated, Easing, Platform, Pressable, ScrollView, StyleProp, StyleSheet, Switch, Text, TextInput,
  TextInputProps, TextStyle, View, ViewStyle,
} from "react-native";
import { useT } from "../i18n";
import { C, M, R, S, font, isWeb, type as typeStyle } from "../../theme";
import { tokens } from "../../theme/tokens";

export type IconName = React.ComponentProps<typeof Feather>["name"];
type TypeKey = keyof typeof tokens.type;
const native = Platform.OS !== "web";

// ------------------------------------------------------------------ text --
export function T({ v = "body", color, center, weight, style, children, numberOfLines, selectable, onPress, a11y }: {
  v?: TypeKey; color?: string; center?: boolean; weight?: 400 | 500 | 600 | 700; style?: StyleProp<TextStyle>;
  children?: ReactNode; numberOfLines?: number; selectable?: boolean; onPress?: () => void; a11y?: string;
}) {
  const { locale, scale } = useT();
  return (
    <Text
      onPress={onPress}
      selectable={selectable}
      numberOfLines={numberOfLines}
      accessibilityLabel={a11y}
      style={[typeStyle(v, locale, scale), weight ? font(weight, locale) : null, color ? { color } : null,
        center ? { textAlign: "center" } : null, style]}
    >
      {children}
    </Text>
  );
}

/** UPI IDs and reference numbers. */
export function Mono({ children, color = C.textSecondary, size = 13.5, style }: { children?: ReactNode; color?: string; size?: number; style?: StyleProp<TextStyle> }) {
  const { scale } = useT();
  return (
    <Text selectable style={[{ fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "ui-monospace, Menlo, Consolas, monospace" }),
      fontSize: size * scale, color, letterSpacing: 0.2 }, style]}>
      {children}
    </Text>
  );
}

export function Icon({ name, size = 20, color = C.text, style }: { name: IconName; size?: number; color?: string; style?: StyleProp<TextStyle> }) {
  return <Feather name={name} size={size} color={color} style={style} />;
}

// ------------------------------------------------------------------ buttons --
type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "safe" | "quiet";
export function Btn({ label, onPress, variant = "primary", icon, loading, disabled, small, full = true, style, a11y }: {
  label: string; onPress?: () => void; variant?: BtnVariant; icon?: IconName; loading?: boolean; disabled?: boolean;
  small?: boolean; full?: boolean; style?: StyleProp<ViewStyle>; a11y?: string;
}) {
  const { locale, scale } = useT();
  const off = disabled || loading;
  const look = {
    primary: { bg: C.brand, fg: C.brandOn, border: C.brand },
    secondary: { bg: C.white, fg: C.textInverse, border: C.white },
    ghost: { bg: "transparent", fg: C.text, border: C.borderStrong },
    danger: { bg: C.block, fg: C.blockOn, border: C.block },
    safe: { bg: C.safe, fg: C.safeOn, border: C.safe },
    quiet: { bg: "transparent", fg: C.textSecondary, border: "transparent" },
  }[variant];
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={a11y || label}
      accessibilityState={{ disabled: !!off, busy: !!loading }}
      disabled={off}
      onPress={onPress}
      style={({ pressed }) => [
        {
          minHeight: small ? 40 : 52, paddingHorizontal: small ? S[3] : S[4], borderRadius: R.pill, borderWidth: 1,
          backgroundColor: look.bg, borderColor: look.border, flexDirection: "row", alignItems: "center", justifyContent: "center",
          alignSelf: full ? "stretch" : "flex-start", opacity: off ? 0.45 : pressed ? 0.82 : 1,
        },
        variant === "primary" && !off ? ({ boxShadow: tokens.shadow.brand } as any) : null,
        style,
      ]}
    >
      {loading ? <ActivityIndicator size="small" color={look.fg} style={{ marginRight: S[1] }} /> : icon ? <Feather name={icon} size={small ? 16 : 18} color={look.fg} style={{ marginRight: S[1] }} /> : null}
      <Text numberOfLines={2} style={[font(600, locale), { color: look.fg, fontSize: (small ? 14 : 16) * scale, textAlign: "center", flexShrink: 1 }]}>{label}</Text>
    </Pressable>
  );
}

export function IconBtn({ icon, onPress, label, color = C.text, bg = "transparent", size = 44, badge }: {
  icon: IconName; onPress?: () => void; label: string; color?: string; bg?: string; size?: number; badge?: number;
}) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress} hitSlop={6}
      style={({ pressed }) => ({ width: size, height: size, borderRadius: R.md, alignItems: "center", justifyContent: "center", backgroundColor: bg, opacity: pressed ? 0.7 : 1 })}>
      <Feather name={icon} size={Math.round(size * 0.46)} color={color} />
      {badge ? (
        <View style={{ position: "absolute", top: 2, right: 2, minWidth: 18, height: 18, borderRadius: 9, backgroundColor: C.block, alignItems: "center", justifyContent: "center", paddingHorizontal: 4 }}>
          <Text style={{ color: C.blockOn, fontSize: 11, fontWeight: "700" }}>{badge > 9 ? "9+" : badge}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

export function Link({ label, onPress, color = C.accentCyan, icon }: { label: string; onPress?: () => void; color?: string; icon?: IconName }) {
  return (
    <Pressable accessibilityRole="link" onPress={onPress} hitSlop={8} style={({ pressed }) => ({ flexDirection: "row", alignItems: "center", paddingVertical: S[1], opacity: pressed ? 0.7 : 1, minHeight: 36 })}>
      {icon ? <Feather name={icon} size={15} color={color} style={{ marginRight: 6 }} /> : null}
      <T v="body" color={color} weight={500}>{label}</T>
    </Pressable>
  );
}

// ------------------------------------------------------------------ surfaces --
export function Card({ children, style, tone, onPress, pad = S[3] }: { children?: ReactNode; style?: StyleProp<ViewStyle>; tone?: string; onPress?: () => void; pad?: number }) {
  const base: ViewStyle = { backgroundColor: C.surface, borderRadius: R.lg, borderWidth: 1, borderColor: tone || C.border, padding: pad };
  if (onPress) {
    return (
      <Pressable accessibilityRole="button" onPress={onPress} style={({ pressed }) => [base, pressed ? { backgroundColor: C.surfaceHover } : null, style]}>
        {children}
      </Pressable>
    );
  }
  return <View style={[base, style]}>{children}</View>;
}

export function Chip({ label, color = C.neutralChipText, bg = C.neutralChip, icon, style }: { label: string; color?: string; bg?: string; icon?: IconName; style?: StyleProp<ViewStyle> }) {
  const { locale, scale } = useT();
  return (
    <View style={[{ flexDirection: "row", alignItems: "center", alignSelf: "flex-start", backgroundColor: bg, borderRadius: R.pill, paddingHorizontal: 10, paddingVertical: 4, maxWidth: "100%" }, style]}>
      {icon ? <Feather name={icon} size={13} color={color} style={{ marginRight: 5 }} /> : null}
      <Text style={[font(600, locale), { color, fontSize: 12.5 * scale, flexShrink: 1 }]}>{label}</Text>
    </View>
  );
}

export function Row({ children, style, gap = S[1], align = "center", wrap }: { children?: ReactNode; style?: StyleProp<ViewStyle>; gap?: number; align?: ViewStyle["alignItems"]; wrap?: boolean }) {
  return <View style={[{ flexDirection: "row", alignItems: align, gap, flexWrap: wrap ? "wrap" : "nowrap" }, style]}>{children}</View>;
}

export function Gap({ h = S[2] }: { h?: number }) {
  return <View style={{ height: h }} />;
}

export function Divider({ style }: { style?: StyleProp<ViewStyle> }) {
  return <View style={[{ height: 1, backgroundColor: C.border, marginVertical: S[2] }, style]} />;
}

export function Label({ children }: { children?: ReactNode }) {
  return <T v="caption" color={C.textMuted} style={{ textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 6 }}>{children}</T>;
}

export function Avatar({ text, size = 44, color = C.brand, bg = C.brandSubtle }: { text: string; size?: number; color?: string; bg?: string }) {
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: bg, alignItems: "center", justifyContent: "center" }}>
      <Text style={[font(700), { color, fontSize: size * 0.36 }]}>{text}</Text>
    </View>
  );
}

export function IconCircle({ icon, color, bg, size = 72 }: { icon: IconName; color: string; bg: string; size?: number }) {
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: bg, alignItems: "center", justifyContent: "center", alignSelf: "center" }}>
      <Feather name={icon} size={size * 0.46} color={color} />
    </View>
  );
}

// ------------------------------------------------------------------ inputs --
export function Field({ label, hint, error, right, style, inputStyle, mono, ...props }: TextInputProps & {
  label?: string; hint?: string; error?: string | null; right?: ReactNode; style?: StyleProp<ViewStyle>; inputStyle?: StyleProp<TextStyle>; mono?: boolean;
}) {
  const { locale, scale } = useT();
  const [focus, setFocus] = useState(false);
  return (
    <View style={[{ marginBottom: S[2] }, style]}>
      {label ? <T v="caption" color={C.textSecondary} style={{ marginBottom: 6 }}>{label}</T> : null}
      <View style={{ flexDirection: "row", alignItems: "center", backgroundColor: C.surfaceSunken, borderRadius: R.md, borderWidth: 1,
        borderColor: error ? C.block : focus ? C.borderStrong : C.border, paddingHorizontal: S[2], minHeight: props.multiline ? 120 : 52 }}>
        <TextInput
          placeholderTextColor={C.textMuted}
          selectionColor={C.brand}
          {...props}
          onFocus={(e) => { setFocus(true); props.onFocus?.(e); }}
          onBlur={(e) => { setFocus(false); props.onBlur?.(e); }}
          style={[
            mono ? { fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "ui-monospace, Menlo, Consolas, monospace" }) } : font(400, locale),
            { flex: 1, color: C.text, fontSize: 16 * scale, paddingVertical: props.multiline ? S[2] : 0, minHeight: props.multiline ? 104 : 50, textAlignVertical: props.multiline ? "top" : "center" },
            isWeb ? ({ outlineStyle: "none" } as any) : null,
            inputStyle,
          ]}
        />
        {right}
      </View>
      {error ? <T v="caption" color={C.block} style={{ marginTop: 6 }}>{error}</T> : hint ? <T v="caption" color={C.textMuted} style={{ marginTop: 6 }}>{hint}</T> : null}
    </View>
  );
}

export function Segmented<V extends string>({ options, value, onChange, style }: {
  options: { value: V; label: string; badge?: number }[]; value: V; onChange: (v: V) => void; style?: StyleProp<ViewStyle>;
}) {
  const { locale, scale } = useT();
  return (
    <View accessibilityRole="tablist" style={[{ flexDirection: "row", backgroundColor: C.surfaceRaised, borderRadius: R.pill, padding: 4, borderWidth: 1, borderColor: C.border }, style]}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable key={o.value} accessibilityRole="tab" accessibilityState={{ selected: on }} onPress={() => onChange(o.value)}
            style={{ flex: 1, minHeight: 40, borderRadius: R.pill, alignItems: "center", justifyContent: "center", flexDirection: "row", paddingHorizontal: 8, backgroundColor: on ? C.borderStrong : "transparent" }}>
            <Text numberOfLines={1} style={[font(on ? 600 : 500, locale), { color: on ? C.text : C.textSecondary, fontSize: 14 * scale }]}>{o.label}</Text>
            {o.badge ? (
              <View style={{ marginLeft: 6, minWidth: 18, height: 18, borderRadius: 9, backgroundColor: C.block, alignItems: "center", justifyContent: "center", paddingHorizontal: 4 }}>
                <Text style={{ color: C.blockOn, fontSize: 11, fontWeight: "700" }}>{o.badge}</Text>
              </View>
            ) : null}
          </Pressable>
        );
      })}
    </View>
  );
}

export function Toggle({ label, help, value, onChange }: { label: string; help?: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <Row style={{ justifyContent: "space-between", paddingVertical: S[1] }} gap={S[2]}>
      <View style={{ flex: 1 }}>
        <T v="body" weight={500}>{label}</T>
        {help ? <T v="caption" color={C.textMuted}>{help}</T> : null}
      </View>
      <Switch accessibilityLabel={label} value={value} onValueChange={onChange} trackColor={{ false: C.borderStrong, true: C.brand }} thumbColor={C.white}
        {...(isWeb ? ({ activeThumbColor: C.white } as any) : {})} />
    </Row>
  );
}

// ------------------------------------------------------------------ feedback --
export function Banner({ tone = "info", icon, title, children, style }: { tone?: "info" | "safe" | "warning" | "stepup" | "block"; icon?: IconName; title?: string; children?: ReactNode; style?: StyleProp<ViewStyle> }) {
  const look = {
    info: { c: C.accentCyan, bg: "rgba(56, 214, 255, 0.10)", i: "info" as IconName },
    safe: { c: C.safe, bg: C.safeSubtle, i: "check-circle" as IconName },
    warning: { c: C.warning, bg: C.warningSubtle, i: "alert-triangle" as IconName },
    stepup: { c: C.stepup, bg: C.stepupSubtle, i: "pause-circle" as IconName },
    block: { c: C.block, bg: C.blockSubtle, i: "x-octagon" as IconName },
  }[tone];
  return (
    <View accessibilityRole="alert" style={[{ backgroundColor: look.bg, borderRadius: R.md, borderWidth: 1, borderColor: look.c, padding: S[2], flexDirection: "row", gap: S[2] }, style]}>
      <Feather name={icon || look.i} size={20} color={look.c} style={{ marginTop: 2 }} />
      <View style={{ flex: 1 }}>
        {title ? <T v="heading" color={look.c}>{title}</T> : null}
        {typeof children === "string" ? <T v="body" color={C.text}>{children}</T> : children}
      </View>
    </View>
  );
}

export function Loading({ label }: { label?: string }) {
  const { t } = useT();
  return (
    <View style={{ padding: S[6], alignItems: "center" }}>
      <ActivityIndicator color={C.brand} />
      <T v="caption" color={C.textMuted} style={{ marginTop: S[1] }}>{label || t("common.loading")}</T>
    </View>
  );
}

export function Empty({ icon = "inbox", text }: { icon?: IconName; text: string }) {
  return (
    <View style={{ padding: S[6], alignItems: "center" }}>
      <Feather name={icon} size={28} color={C.textMuted} />
      <T v="body" color={C.textMuted} center style={{ marginTop: S[1] }}>{text}</T>
    </View>
  );
}

export function ErrorNote({ error, onRetry }: { error: any; onRetry?: () => void }) {
  const { t } = useT();
  if (!error) return null;
  const offline = error?.code === "OFFLINE" || error?.code === "TIMEOUT";
  return (
    <Banner tone={offline ? "warning" : "block"} icon={offline ? "wifi-off" : "alert-circle"} style={{ marginBottom: S[2] }}>
      <T v="body">{offline ? t("common.offline") : error?.message || t("common.error")}</T>
      {onRetry ? <Link label={t("common.retry")} onPress={onRetry} icon="refresh-cw" /> : null}
    </Banner>
  );
}

/** Reasons the app noticed: plain facts, one per line. */
export function Reasons({ reasons, color = C.textSecondary, max = 3 }: { reasons: { message: string; code?: string }[]; color?: string; max?: number }) {
  if (!reasons?.length) return null;
  return (
    <View style={{ gap: S[1] }}>
      {reasons.slice(0, max).map((r, i) => (
        <Row key={(r.code || "") + i} align="flex-start" gap={S[1]}>
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: color, marginTop: 9 }} />
          <T v="bodyLarge" style={{ flex: 1 }}>{r.message}</T>
        </Row>
      ))}
    </View>
  );
}

// ------------------------------------------------------------------ motion --
export function FadeIn({ children, style, from = 10, duration = M.base }: { children?: ReactNode; style?: StyleProp<ViewStyle>; from?: number; duration?: number }) {
  const v = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(v, { toValue: 1, duration, easing: Easing.bezier(0.2, 0, 0, 1), useNativeDriver: native }).start();
  }, [v, duration]);
  return (
    <Animated.View style={[style, { opacity: v, transform: [{ translateY: v.interpolate({ inputRange: [0, 1], outputRange: [from, 0] }) }] }]}>
      {children}
    </Animated.View>
  );
}

/** A countdown ring that sweeps smoothly from full to empty over `seconds`. */
export function Ring({ seconds, total, size = 148, stroke = 8, color = C.stepup, children }: {
  seconds: number; total: number; size?: number; stroke?: number; color?: string; children?: ReactNode;
}) {
  const p = useRef(new Animated.Value(total > 0 ? Math.min(1, seconds / total) : 0)).current;
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    Animated.timing(p, { toValue: 0, duration: Math.max(0, seconds) * 1000, easing: Easing.linear, useNativeDriver: native }).start();
  }, [p, seconds]);
  const right = p.interpolate({ inputRange: [0, 0.5, 1], outputRange: ["0deg", "180deg", "180deg"] });
  const left = p.interpolate({ inputRange: [0, 0.5, 1], outputRange: ["0deg", "0deg", "180deg"] });
  const half = size / 2;
  const ring: ViewStyle = { width: size, height: size, borderRadius: half, borderWidth: stroke, borderColor: color };
  return (
    <View style={{ width: size, height: size, alignSelf: "center" }}>
      <View style={[ring, { borderColor: C.border, position: "absolute" }]} />
      {/* first half of the sweep: shown through the right-hand window */}
      <View style={{ position: "absolute", left: half, width: half, height: size, overflow: "hidden" }}>
        <Animated.View style={{ position: "absolute", left: -half, width: size, height: size, transform: [{ rotate: right }] }}>
          <View style={{ width: half, height: size, overflow: "hidden" }}>
            <View style={ring} />
          </View>
        </Animated.View>
      </View>
      {/* second half: shown through the left-hand window */}
      <View style={{ position: "absolute", left: 0, width: half, height: size, overflow: "hidden" }}>
        <Animated.View style={{ position: "absolute", left: 0, width: size, height: size, transform: [{ rotate: left }] }}>
          <View style={{ position: "absolute", left: half, width: half, height: size, overflow: "hidden" }}>
            <View style={[ring, { position: "absolute", left: -half }]} />
          </View>
        </Animated.View>
      </View>
      <View style={[StyleSheet.absoluteFill, { alignItems: "center", justifyContent: "center" }]}>{children}</View>
    </View>
  );
}

/** Seconds left until a moment in time, updated twice a second. */
export function useCountdown(until: number | null | undefined): number {
  const calc = () => (until ? Math.max(0, (until - Date.now()) / 1000) : 0);
  const [left, setLeft] = useState(calc);
  useEffect(() => {
    setLeft(calc());
    if (!until) return;
    const id = setInterval(() => setLeft(calc()), 500);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [until]);
  return left;
}

// ------------------------------------------------------------------ page scaffolding --
export function Glow({ color = C.bgGlowViolet }: { color?: string }) {
  const img = `radial-gradient(ellipse at top, ${color} 0%, rgba(7, 6, 20, 0) 70%)`;
  return (
    <View style={[{ position: "absolute", top: 0, left: 0, right: 0, height: 320, pointerEvents: "none" },
      isWeb ? ({ backgroundImage: img } as any) : ({ experimental_backgroundImage: img } as any)]} />
  );
}

export function Header({ title, onBack, right, subtitle }: { title?: string; onBack?: () => void; right?: ReactNode; subtitle?: string }) {
  const { t } = useT();
  return (
    <Row style={{ paddingHorizontal: S[2], paddingTop: S[2], paddingBottom: S[1], minHeight: 56 }} gap={S[1]}>
      {onBack ? <IconBtn icon="chevron-left" label={t("common.back")} onPress={onBack} color={C.textSecondary} /> : null}
      <View style={{ flex: 1, paddingLeft: onBack ? 0 : S[1] }}>
        {title ? <T v="title" numberOfLines={2}>{title}</T> : null}
        {subtitle ? <T v="caption" color={C.textMuted}>{subtitle}</T> : null}
      </View>
      {right}
    </Row>
  );
}

export function Screen({ title, subtitle, onBack, right, children, footer, scroll = true, glow, pad = S[3] }: {
  title?: string; subtitle?: string; onBack?: () => void; right?: ReactNode; children?: ReactNode; footer?: ReactNode; scroll?: boolean; glow?: string | boolean; pad?: number;
}) {
  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      {glow ? <Glow color={typeof glow === "string" ? glow : undefined} /> : null}
      {title !== undefined || onBack || right ? <Header title={title} subtitle={subtitle} onBack={onBack} right={right} /> : null}
      {scroll ? (
        <ScrollView style={{ flex: 1 }} keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: pad, paddingBottom: S[6] }}>
          {children}
        </ScrollView>
      ) : (
        <View style={{ flex: 1, padding: pad }}>{children}</View>
      )}
      {footer ? <View style={{ padding: pad, paddingTop: S[2], gap: S[1], borderTopWidth: 1, borderTopColor: C.border, backgroundColor: C.bg }}>{footer}</View> : null}
    </View>
  );
}
