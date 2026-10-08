// A small stack navigator. The top-level destinations sit behind an icon rail on the left;
// the payment flow and the tier screens hide the rail so there is one thing to do per screen.
import { Feather } from "@expo/vector-icons";
import React, { ComponentType, useEffect, useMemo } from "react";
import { BackHandler, Pressable, ScrollView, Text, View } from "react-native";
import { FadeIn, IconName } from "./components/ui";
import { useT } from "./i18n";
import { actions, Route, ScreenName, TABS, useAppDispatch, useAppSelector } from "./store";
import { C, M, R, RAIL_WIDTH, S, font } from "../theme";

export function useNav() {
  const dispatch = useAppDispatch();
  return useMemo(
    () => ({
      push: (name: ScreenName, params?: any) => dispatch(actions.nav.push({ name, params })),
      replace: (name: ScreenName, params?: any) => dispatch(actions.nav.replace({ name, params })),
      pop: () => dispatch(actions.nav.pop()),
      tab: (name: ScreenName, params?: any) => dispatch(actions.nav.tab({ name, params })),
      home: () => dispatch(actions.nav.reset(undefined)),
    }),
    [dispatch],
  );
}

// A screen that must not be left with the hardware Back key (a tier screen, say) registers a guard.
let backGuard: (() => boolean) | null = null;
export function useBackGuard(fn: () => boolean) {
  useEffect(() => {
    backGuard = fn;
    return () => {
      if (backGuard === fn) backGuard = null;
    };
  }, [fn]);
}

const RAIL: { name: ScreenName; icon: IconName; label: string }[] = [
  { name: "home", icon: "home", label: "nav.home" },
  { name: "safety", icon: "search", label: "nav.safety" },
  { name: "message", icon: "message-square", label: "nav.message" },
  { name: "holds", icon: "clock", label: "nav.holds" },
  { name: "guardian", icon: "shield", label: "nav.guardian" },
  { name: "practice", icon: "play-circle", label: "nav.practice" },
  { name: "history", icon: "list", label: "nav.history" },
  { name: "settings", icon: "settings", label: "nav.settings" },
];

export function BrandMark({ size = 36 }: { size?: number }) {
  return (
    <View style={{ width: size, height: size, borderRadius: size * 0.3, backgroundColor: C.brand, alignItems: "center", justifyContent: "center" }}>
      <Feather name="shield" size={size * 0.56} color={C.brandOn} />
    </View>
  );
}

export function RailButton({ icon, label, active, badge, onPress }: { icon: IconName; label: string; active?: boolean; badge?: number; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={label}
      accessibilityState={{ selected: !!active }}
      onPress={onPress}
      style={({ pressed }) => ({
        width: 44, height: 44, borderRadius: R.md, alignItems: "center", justifyContent: "center",
        backgroundColor: active ? C.brandSubtle : pressed ? C.surfaceHover : "transparent",
      })}
    >
      <Feather name={icon} size={20} color={active ? C.brandHover : C.textMuted} />
      {badge ? (
        <View style={{ position: "absolute", top: 3, right: 3, minWidth: 17, height: 17, borderRadius: 9, backgroundColor: C.block, alignItems: "center", justifyContent: "center", paddingHorizontal: 3 }}>
          <Text style={{ color: C.blockOn, fontSize: 10.5, fontWeight: "700" }}>{badge > 9 ? "9+" : badge}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

export function RailFrame({ children, demo }: { children: React.ReactNode; demo?: boolean }) {
  const { t, locale } = useT();
  return (
    <View style={{ width: RAIL_WIDTH, backgroundColor: C.surface, borderRightWidth: 1, borderRightColor: C.border, alignItems: "center", paddingTop: S[2], paddingBottom: S[2] }}>
      <BrandMark />
      <View style={{ height: S[3] }} />
      <ScrollView accessibilityRole="tablist" style={{ flex: 1, alignSelf: "stretch" }} showsVerticalScrollIndicator={false}
        contentContainerStyle={{ gap: 6, alignItems: "center", paddingBottom: S[1] }}>{children}</ScrollView>
      {demo ? (
        <View style={{ backgroundColor: C.warningSubtle, borderRadius: R.sm, paddingHorizontal: 5, paddingVertical: 3 }}>
          <Text style={[font(600, locale), { color: C.warning, fontSize: 9.5, textAlign: "center" }]}>{t("demo.badge").split(" ")[0].toUpperCase()}</Text>
        </View>
      ) : null}
    </View>
  );
}

function Rail({ active }: { active: ScreenName }) {
  const { t } = useT();
  const nav = useNav();
  const me = useAppSelector((s) => s.session.me);
  const demo = useAppSelector((s) => s.session.demoMode);
  const badge = (name: ScreenName) => (name === "holds" ? me?.active_holds || 0 : name === "guardian" ? me?.pending_guardian_requests || 0 : 0);
  return (
    <RailFrame demo={demo}>
      {RAIL.map((it) => (
        <RailButton key={it.name} icon={it.icon} label={t(it.label)} active={it.name === active} badge={badge(it.name)} onPress={() => nav.tab(it.name)} />
      ))}
    </RailFrame>
  );
}

export function Navigator({ screens }: { screens: Record<ScreenName, ComponentType<{ route: Route }>> }) {
  const stack = useAppSelector((s) => s.nav.stack);
  const dispatch = useAppDispatch();
  const top = stack[stack.length - 1];
  const depth = stack.length;

  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (backGuard && backGuard()) return true;
      if (depth > 1) {
        dispatch(actions.nav.pop());
        return true;
      }
      return false;
    });
    return () => sub.remove();
  }, [depth, dispatch]);

  const Comp = screens[top.name];
  const rail = TABS.includes(top.name);
  return (
    <View style={{ flex: 1, flexDirection: "row", backgroundColor: C.bg }}>
      {rail ? <Rail active={top.name} /> : null}
      <FadeIn key={top.key} style={{ flex: 1 }} from={rail ? 6 : 14} duration={rail ? M.fast : M.base}>
        <Comp route={top} />
      </FadeIn>
    </View>
  );
}
