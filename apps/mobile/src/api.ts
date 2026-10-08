// HTTP client for the UPI Inspector API. One base URL, one bearer token, one error shape.
import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import { Platform } from "react-native";
import { secureGet, secureSet } from "./secure";

function resolveBase(): string {
  const env = process.env.EXPO_PUBLIC_API_URL;
  if (env) return env.replace(/\/+$/, "");
  // Web: the API runs on the same machine as the page, on port 8000.
  if (Platform.OS === "web" && typeof window !== "undefined" && window.location?.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  // Expo Go on a phone: the laptop that serves the bundle also serves the API.
  const hostUri: string =
    (Constants.expoConfig as any)?.hostUri || (Constants as any).expoGoConfig?.debuggerHost || (Constants as any).linkingUri || "";
  const host = hostUri.replace(/^[a-z]+:\/\//, "").split(/[:/]/)[0];
  return `http://${host || "localhost"}:8000`;
}

export const API_BASE = resolveBase();

export class ApiErr extends Error {
  code: string;
  status: number;
  details: any;
  constructor(message: string, code: string, status: number, details?: any) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

let token: string | null = null;
let tokenSource: (() => string | null) | null = null;
let onUnauthorized: (() => void) | null = null;
let tokenExp = 0;                 // ms: when the token in use expires
let tokenLife = 0;                // ms: how long it was issued for
let refreshing: Promise<void> | null = null;
let onRefreshed: ((s: StoredSession) => void) | null = null;
let current: StoredSession | null = null;

export function setToken(t: string | null) {
  token = t;
  if (!t) {
    tokenExp = 0;
    tokenLife = 0;
    current = null;
  }
}
/** Called with the new session each time the token is renewed, so the app's state can follow. */
export function setRefreshHandler(fn: (s: StoredSession) => void) {
  onRefreshed = fn;
}
/** The app's store is the source of truth for the session; this lets the client read the token from it. */
export function setTokenSource(fn: () => string | null) {
  tokenSource = fn;
}
export function getToken() {
  return token ?? tokenSource?.() ?? null;
}
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

type Opts = { method?: string; body?: any; timeoutMs?: number; auth?: boolean };

/** What the server returns with every new token (docs/07, section 1.1). */
export type SessionGrant = { token: string; expires_in_s: number; session_ends_at: number; auth_method: string; user?: any };

/** Start using a token the server has just issued, and remember it for the next launch. */
export async function adoptSession(grant: SessionGrant, user: any): Promise<StoredSession> {
  const now = Date.now();
  const s: StoredSession = {
    token: grant.token, user, expiresAt: now + Math.max(30, grant.expires_in_s) * 1000,
    sessionEndsAt: grant.session_ends_at * 1000, authMethod: grant.auth_method || "pin",
  };
  token = s.token;
  tokenExp = s.expiresAt;
  tokenLife = Math.max(30, grant.expires_in_s) * 1000;
  current = s;
  await saveSession(s);
  return s;
}

/** A token is short-lived on purpose. While the app is in use it is renewed in the background; left idle
 *  it lapses, and the next request signs the person out. One renewal at a time, however many calls ask. */
function renewIfDue(): Promise<void> | null {
  if (!token || !tokenExp || !current) return null;
  const left = tokenExp - Date.now();
  if (left > Math.min(10 * 60_000, tokenLife / 3)) return null;
  if (!refreshing) {
    const using = token;
    refreshing = (async () => {
      try {
        const res = await fetch(API_BASE + "/v1/auth/refresh", { method: "POST", headers: { Accept: "application/json", Authorization: `Bearer ${using}` } });
        if (res.ok && token === using && current) {
          const s = await adoptSession((await res.json()) as SessionGrant, current.user);
          onRefreshed?.(s);
        }
      } catch {
        /* offline: carry on with the token we have until it runs out */
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  if (opts.auth !== false && !path.startsWith("/v1/auth/logout")) await renewIfDue();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 12000);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const bearer = opts.auth === false ? null : getToken();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  try {
    const res = await fetch(API_BASE + path, {
      method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: ctrl.signal,
    });
    const text = await res.text();
    let data: any = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    if (!res.ok) {
      const e = data?.error;
      if (res.status === 401 && bearer) onUnauthorized?.();
      throw new ApiErr(e?.message || `Request failed (${res.status})`, e?.code || "ERROR", res.status, e?.details);
    }
    return data as T;
  } catch (e: any) {
    if (e instanceof ApiErr) throw e;
    if (e?.name === "AbortError") throw new ApiErr("The server took too long to answer", "TIMEOUT", 0);
    throw new ApiErr("Cannot reach the server", "OFFLINE", 0);
  } finally {
    clearTimeout(timer);
  }
}

/** Fetch a protected file (the complaint-pack PDF) as a blob. Web only. */
export async function apiBlob(path: string): Promise<Blob> {
  const bearer = getToken();
  const res = await fetch(API_BASE + path, { headers: bearer ? { Authorization: `Bearer ${bearer}` } : {} });
  if (!res.ok) throw new ApiErr(`Download failed (${res.status})`, "ERROR", res.status);
  return res.blob();
}

// ------------------------------------------------------------------ persisted session --
// On the web, two windows of the same site share storage, so both would be the same user. A window opened
// with ?slot=2 keeps its own session: that is how two "phones" run side by side on one laptop.
function slot(): string {
  if (Platform.OS !== "web" || typeof window === "undefined") return "";
  try {
    const s = new URLSearchParams(window.location.search).get("slot") || "";
    return s ? `.${s.replace(/[^a-z0-9_-]/gi, "").slice(0, 16)}` : "";
  } catch {
    return "";
  }
}
const KEY = `upi-inspector.session.v2${slot()}`;
const USER_KEY = `upi-inspector.user.v2${slot()}`;
const LOCALE_KEY = `upi-inspector.locale.v1${slot()}`;

export type StoredSession = { token: string; user: any; expiresAt: number; sessionEndsAt: number; authMethod: string };

/** The token is kept in the device's secure storage; the profile (nothing secret) in ordinary storage. */
export async function loadSession(): Promise<StoredSession | null> {
  try {
    AsyncStorage.removeItem(`upi-inspector.session.v1${slot()}`).catch(() => undefined);   // the old, unprotected copy
    const raw = await secureGet(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Omit<StoredSession, "user">;
    if (!s.token || !s.expiresAt || s.expiresAt <= Date.now() + 5000) {
      await saveSession(null);                       // it lapsed while the app was closed
      return null;
    }
    const user = JSON.parse((await AsyncStorage.getItem(USER_KEY)) || "null");
    const full: StoredSession = { ...s, user };
    token = full.token;
    tokenExp = full.expiresAt;
    tokenLife = Math.max(60_000, full.expiresAt - Date.now());
    current = full;
    return full;
  } catch {
    return null;
  }
}
export async function saveSession(s: StoredSession | null) {
  try {
    if (s) {
      const { user, ...secret } = s;
      await secureSet(KEY, JSON.stringify(secret));
      await AsyncStorage.setItem(USER_KEY, JSON.stringify(user ?? null));
    } else {
      await secureSet(KEY, null);
      await AsyncStorage.removeItem(USER_KEY);
    }
  } catch {
    /* storage is a convenience; the app works without it */
  }
}
/** Tell the server this session is over, so the token stops working everywhere at once. */
export async function serverSignOut(everywhere = false) {
  if (!getToken()) return;
  try {
    await api(everywhere ? "/v1/auth/logout-all" : "/v1/auth/logout", { method: "POST", timeoutMs: 4000 });
  } catch {
    /* signing out on this device still happens */
  }
}
export async function loadLocale(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(LOCALE_KEY);
  } catch {
    return null;
  }
}
export async function saveLocale(l: string) {
  try {
    await AsyncStorage.setItem(LOCALE_KEY, l);
  } catch {
    /* ignore */
  }
}

// ------------------------------------------------------------------ helpers --
export function newTxnId(): string {
  const r = Math.random().toString(36).slice(2, 10).toUpperCase();
  return `TXN-${Date.now().toString(36).toUpperCase()}-${r}`;
}

export const VPA_RE = /^[a-z0-9][a-z0-9._-]{1,60}@[a-z0-9]{2,20}$/i;

/** Parse a UPI deep link or QR payload: upi://pay?pa=...&pn=...&am=...&tn=... */
export function parseUpiLink(raw: string): { vpa: string; name?: string; amount?: string; note?: string } | null {
  const s = (raw || "").trim();
  if (VPA_RE.test(s)) return { vpa: s.toLowerCase() };
  const m = /^upi:\/\/pay\?(.*)$/i.exec(s);
  if (!m) return null;
  const q: Record<string, string> = {};
  for (const part of m[1].split("&")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    try {
      q[part.slice(0, i).toLowerCase()] = decodeURIComponent(part.slice(i + 1).replace(/\+/g, " "));
    } catch {
      q[part.slice(0, i).toLowerCase()] = part.slice(i + 1);
    }
  }
  if (!q.pa || !VPA_RE.test(q.pa)) return null;
  return { vpa: q.pa.toLowerCase(), name: q.pn || undefined, amount: q.am || undefined, note: q.tn || undefined };
}
