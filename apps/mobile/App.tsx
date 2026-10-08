// UPI Inspector payer app (a mock UPI app with the risk check built into the payment flow).
import { DMSans_400Regular, DMSans_500Medium, DMSans_600SemiBold, DMSans_700Bold } from "@expo-google-fonts/dm-sans";
import {
  NotoSansDevanagari_400Regular, NotoSansDevanagari_500Medium, NotoSansDevanagari_600SemiBold, NotoSansDevanagari_700Bold,
} from "@expo-google-fonts/noto-sans-devanagari";
import { Feather } from "@expo/vector-icons";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import React, { useEffect, useState } from "react";
import { ActivityIndicator, AppState, KeyboardAvoidingView, Platform, View, useWindowDimensions } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { Provider } from "react-redux";
import { api, loadLocale, loadSession, saveSession, setRefreshHandler, setToken, setTokenSource, setUnauthorizedHandler } from "./src/api";
import { CallOverlay, CallRiskBanner, InCallBar, SmsStack, ToastHost } from "./src/components/Overlays";
import { Navigator } from "./src/nav";
import Amount from "./src/screens/Amount";
import Auth from "./src/screens/Auth";
import { Pin, Result, Wait } from "./src/screens/Finish";
import Guardian, { GuardianRequest } from "./src/screens/Guardian";
import History from "./src/screens/History";
import Holds from "./src/screens/Holds";
import Home from "./src/screens/Home";
import Lock from "./src/screens/Lock";
import Message from "./src/screens/Message";
import Payee from "./src/screens/Payee";
import Practice, { Rehearsal } from "./src/screens/Practice";
import Report from "./src/screens/Report";
import Safety from "./src/screens/Safety";
import Scan from "./src/screens/Scan";
import Settings from "./src/screens/Settings";
import StepUp from "./src/screens/StepUp";
import { Decline, Hold } from "./src/screens/Tier";
import { signOut, signOutLocally } from "./src/session";
import { connectSocket, disconnectSocket } from "./src/socket";
import { actions, store, useAppDispatch, useAppSelector } from "./src/store";
import { C, R, setFontsReady } from "./theme";

const SCREENS = {
  home: Home, payee: Payee, scan: Scan, amount: Amount, stepup: StepUp, hold: Hold, decline: Decline, pin: Pin,
  result: Result, wait: Wait, holds: Holds, message: Message, guardian: Guardian, request: GuardianRequest,
  report: Report, practice: Practice, rehearsal: Rehearsal, history: History, settings: Settings, safety: Safety,
};

// The app lock (F-35): a session opened with a PIN locks when the app has been away or untouched this long.
const LOCK_AFTER_AWAY_MS = 60_000;
const LOCK_AFTER_IDLE_MS = 5 * 60_000;
let lastTouch = Date.now();

// Web only: dark form controls and slim dark scrollbars, so the browser's own chrome matches the app.
if (Platform.OS === "web" && typeof document !== "undefined") {
  const st = document.createElement("style");
  st.textContent = `html{color-scheme:dark;background:${C.surfaceSunken}}*{scrollbar-width:thin;scrollbar-color:${C.borderStrong} transparent}`;
  document.head.appendChild(st);
}

setTokenSource(() => store.getState().session.token);

function Root() {
  const dispatch = useAppDispatch();
  const ready = useAppSelector((s) => s.session.ready);
  const token = useAppSelector((s) => s.session.token);
  const authMethod = useAppSelector((s) => s.session.authMethod);
  const locked = useAppSelector((s) => s.session.locked);
  const signedIn = !!token;

  // restore the last session, and check with the server that it is still good
  useEffect(() => {
    setUnauthorizedHandler(() => signOutLocally("expired"));
    setRefreshHandler((s) => store.dispatch(actions.tokenRenewed(s.token)));
    (async () => {
      const [stored, locale] = await Promise.all([loadSession(), loadLocale()]);
      if (!stored?.token) return dispatch(actions.booted({ token: null, me: null, locale }));
      // A session opened with a PIN comes back locked: whoever reopens the app proves it is still them.
      const how = { authMethod: stored.authMethod, locked: stored.authMethod === "pin" };
      try {
        const me = await api("/v1/users/me", { timeoutMs: 6000 });
        dispatch(actions.booted({ token: stored.token, me, locale, ...how }));
        dispatch(actions.meLoaded(me));
      } catch (e: any) {
        if (e?.status === 401 || e?.status === 403 || e?.status === 404) {
          setToken(null);
          saveSession(null);
          dispatch(actions.booted({ token: null, me: null, locale }));
        } else {
          dispatch(actions.booted({ token: stored.token, me: stored.user, locale, ...how }));   // offline: open with what we had
        }
      }
    })();
  }, [dispatch]);

  // live channel, and a call that is still in progress (with what is known about the caller, F-31)
  useEffect(() => {
    if (!signedIn) return;
    connectSocket();
    api("/v1/users/me").then((me) => dispatch(actions.meLoaded(me))).catch(() => undefined);
    api("/v1/users/me/call-status")
      .then((c) => { if (c?.active) dispatch(actions.overlay.callRestored({ call_id: c.call_id, started_at: c.started_at, caller_risk: c.caller?.risk || 0 })); })
      .catch(() => undefined);
    return () => disconnectSocket();
  }, [signedIn, dispatch]);

  // the app lock: away for a minute, or open and untouched for five
  useEffect(() => {
    if (!signedIn || authMethod !== "pin") return;
    let awaySince = 0;
    const away = () => { awaySince = Date.now(); };
    const back = () => {
      if (awaySince && Date.now() - awaySince > LOCK_AFTER_AWAY_MS) dispatch(actions.lockSet(true));
      awaySince = 0;
      lastTouch = Date.now();
    };
    lastTouch = Date.now();
    const idle = setInterval(() => { if (Date.now() - lastTouch > LOCK_AFTER_IDLE_MS) dispatch(actions.lockSet(true)); }, 15000);
    if (Platform.OS === "web") {
      const onVis = () => (document.visibilityState === "hidden" ? away() : back());
      document.addEventListener("visibilitychange", onVis);
      return () => { clearInterval(idle); document.removeEventListener("visibilitychange", onVis); };
    }
    let prev = AppState.currentState;
    const sub = AppState.addEventListener("change", (s) => {
      if (s !== "active" && prev === "active") away();
      if (s === "active" && prev !== "active") back();
      prev = s;
    });
    return () => { clearInterval(idle); sub.remove(); };
  }, [signedIn, authMethod, dispatch]);

  // count the times the app is left during a payment: one of the coaching signals (F-18)
  useEffect(() => {
    if (Platform.OS === "web") {
      const onVis = () => { if (document.visibilityState === "hidden") dispatch(actions.flow.appSwitched()); };
      document.addEventListener("visibilitychange", onVis);
      return () => document.removeEventListener("visibilitychange", onVis);
    }
    let prev = AppState.currentState;
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "background" && prev !== "background") dispatch(actions.flow.appSwitched());
      prev = s;
    });
    return () => sub.remove();
  }, [dispatch]);

  if (!ready) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={C.brand} />
      </View>
    );
  }
  return (
    <View style={{ flex: 1, backgroundColor: C.bg }} onStartShouldSetResponderCapture={() => { lastTouch = Date.now(); return false; }}>
      {token ? <InCallBar /> : null}
      {token ? <CallRiskBanner /> : null}
      <View style={{ flex: 1 }}>
        {token ? <Navigator screens={SCREENS} /> : <Auth />}
        {token ? <SmsStack /> : null}
        {token ? <ToastHost /> : null}
      </View>
      {token ? <CallOverlay /> : null}
      {token && locked ? <Lock onSignOut={() => signOut()} /> : null}
    </View>
  );
}

/** On a wide browser window the app sits in a phone-sized frame, so a laptop demo looks like a phone. */
function Frame({ children }: { children: React.ReactNode }) {
  const { width, height } = useWindowDimensions();
  if (Platform.OS !== "web" || width <= 520) return <>{children}</>;
  return (
    <View style={{ flex: 1, backgroundColor: C.surfaceSunken, alignItems: "center", justifyContent: "center" }}>
      <View style={{ width: 400, height: Math.min(height - 32, 860), borderRadius: R.xl + 8, borderWidth: 1, borderColor: C.borderStrong, overflow: "hidden", backgroundColor: C.bg }}>
        {children}
      </View>
    </View>
  );
}

export default function App() {
  const [loaded, fontError] = useFonts({
    DMSans_400Regular, DMSans_500Medium, DMSans_600SemiBold, DMSans_700Bold,
    NotoSansDevanagari_400Regular, NotoSansDevanagari_500Medium, NotoSansDevanagari_600SemiBold, NotoSansDevanagari_700Bold,
    ...Feather.font,
  });
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setWaited(true), 5000);      // never wait on fonts for long: system fonts will do
    return () => clearTimeout(id);
  }, []);
  if (loaded) setFontsReady(true);
  if (!loaded && !fontError && !waited) return <View style={{ flex: 1, backgroundColor: C.bg }} />;

  return (
    <Provider store={store}>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <Frame>
          <SafeAreaView style={{ flex: 1, backgroundColor: C.bg }}>
            <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
              <Root />
            </KeyboardAvoidingView>
          </SafeAreaView>
        </Frame>
      </SafeAreaProvider>
    </Provider>
  );
}
