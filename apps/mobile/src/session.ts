// Ending a session on this device, and the screens that must not be captured.
import * as ScreenCapture from "expo-screen-capture";
import { useEffect } from "react";
import { Platform } from "react-native";
import { saveSession, serverSignOut, setToken } from "./api";
import { disconnectSocket } from "./socket";
import { actions, store, useAppSelector } from "./store";

/** Forget the session here. `notice` says why, for the sign-in screen ("expired"). */
export function signOutLocally(notice?: string) {
  disconnectSocket();
  setToken(null);
  saveSession(null);
  store.dispatch(actions.overlay.reset());
  store.dispatch(actions.flow.cleared());
  store.dispatch(actions.nav.reset(undefined));
  store.dispatch(actions.signedOut(notice));
}

/** Sign out properly: the server ends the session first, so the token stops working everywhere. */
export async function signOut(everywhere = false) {
  await serverSignOut(everywhere);
  signOutLocally();
}

/** Keep a screen that shows a PIN pad or a one-time code out of screenshots, screen recordings and the
 *  app switcher (Android FLAG_SECURE, the iOS equivalent). Left off in demo mode, where the phone's screen
 *  is usually being mirrored to a projector. No effect in a browser. */
export function useSecureScreen(key: string) {
  const demo = useAppSelector((s) => s.session.demoMode);
  useEffect(() => {
    if (Platform.OS === "web" || demo) return;
    ScreenCapture.preventScreenCaptureAsync(key).catch(() => undefined);
    return () => {
      ScreenCapture.allowScreenCaptureAsync(key).catch(() => undefined);
    };
  }, [key, demo]);
}
