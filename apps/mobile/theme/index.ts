// The payer app's theme. Every visual value comes from the generated tokens (packages/design-tokens).
import { Platform, TextStyle } from "react-native";
import { tokens } from "./tokens";

export const C = tokens.color;
export const R = tokens.radius;
export const S = tokens.space; // [4, 8, 12, 16, 20, 24, 32, 40]
export const M = tokens.motion;

const latin = { 400: "DMSans_400Regular", 500: "DMSans_500Medium", 600: "DMSans_600SemiBold", 700: "DMSans_700Bold" } as const;
const deva = { 400: "NotoSansDevanagari_400Regular", 500: "NotoSansDevanagari_500Medium", 600: "NotoSansDevanagari_600SemiBold", 700: "NotoSansDevanagari_700Bold" } as const;

let fontsReady = false;
export function setFontsReady(v: boolean) {
  fontsReady = v;
}

/** Font family for a weight. Hindi text uses a Devanagari face with more line height. */
export function font(weight: 400 | 500 | 600 | 700 = 400, locale: string = "en"): TextStyle {
  if (!fontsReady) return { fontWeight: String(weight) as TextStyle["fontWeight"] };
  return { fontFamily: (locale === "hi" ? deva : latin)[weight] };
}

type TypeKey = keyof typeof tokens.type;
/** A type style from the token scale. `scale` is 1.3 in large-text mode. */
export function type(key: TypeKey, locale = "en", scale = 1): TextStyle {
  const t = tokens.type[key];
  const hi = locale === "hi" ? 1.18 : 1;
  return { ...font(t.weight as 400 | 500 | 600 | 700, locale), fontSize: t.size * scale, lineHeight: Math.round(t.line * scale * hi), color: C.text };
}

export const TIER = {
  SAFE: { color: C.safe, subtle: C.safeSubtle, on: C.safeOn, icon: "check-circle" },
  WARNING: { color: C.warning, subtle: C.warningSubtle, on: C.warningOn, icon: "alert-triangle" },
  STEP_UP: { color: C.stepup, subtle: C.stepupSubtle, on: C.stepupOn, icon: "pause-circle" },
  BLOCK: { color: C.block, subtle: C.blockSubtle, on: C.blockOn, icon: "x-octagon" },
} as const;

export const BAND = {
  TRUSTED: { color: C.safe, subtle: C.safeSubtle, icon: "check-circle" },
  NEUTRAL: { color: C.neutralChipText, subtle: C.neutralChip, icon: "user" },
  NO_HISTORY: { color: C.neutralChipText, subtle: C.neutralChip, icon: "help-circle" },
  CAUTION: { color: C.warning, subtle: C.warningSubtle, icon: "alert-triangle" },
  REPORTED: { color: C.block, subtle: C.blockSubtle, icon: "alert-octagon" },
} as const;

export const isWeb = Platform.OS === "web";
export const RAIL_WIDTH = 60;
