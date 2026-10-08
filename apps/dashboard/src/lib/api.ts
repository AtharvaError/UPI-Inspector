"use client";

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Staff = { staff_id: string; name: string; email: string; role: "admin" | "analyst" | "auditor"; tenant_id: string };
/** What the API returns with every new token (docs/07, section 1.1). */
export type Grant = { token: string; expires_in_s: number; session_ends_at: number; auth_method: string; staff?: Staff };
export type Session = { token: string; staff: Staff; expiresAt: number; sessionEndsAt: number; authMethod: string; life: number };

// The session lives in sessionStorage: it belongs to this tab and is gone when the tab is closed, so a
// shared computer does not stay signed in. The bank an admin last looked at is only a preference.
const KEY = "upi.staff.session.v2";
const TENANT = "upi.staff.tenant";
const NOTICE = "upi.staff.notice";
/** Sign out after this long without a key press, click or scroll, even though the pages keep polling. */
export const IDLE_MS = 15 * 60_000;

let lastActivity = Date.now();
let refreshing: Promise<void> | null = null;

/** The person did something. Background polling does not count, so an unattended dashboard still lapses. */
export function noteActivity() {
  lastActivity = Date.now();
}
export function idleFor(): number {
  return Date.now() - lastActivity;
}

export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    window.localStorage.removeItem("upi.staff.session");       // the old copy, which outlived the tab
    const raw = window.sessionStorage.getItem(KEY);
    const s = raw ? (JSON.parse(raw) as Session) : null;
    if (s && s.expiresAt <= Date.now()) {
      window.sessionStorage.removeItem(KEY);
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

/** Start using a token the API has just issued. */
export function adoptSession(g: Grant, staff: Staff): Session {
  const life = Math.max(30, g.expires_in_s) * 1000;
  const s: Session = { token: g.token, staff, expiresAt: Date.now() + life, sessionEndsAt: g.session_ends_at * 1000, authMethod: g.auth_method, life };
  window.sessionStorage.setItem(KEY, JSON.stringify(s));
  if (!window.localStorage.getItem(TENANT)) window.localStorage.setItem(TENANT, staff.tenant_id);
  noteActivity();
  return s;
}

export function clearSession(notice?: string) {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(KEY);
  window.localStorage.removeItem(TENANT);
  if (notice) window.sessionStorage.setItem(NOTICE, notice);
}

/** Why the last session ended, read once by the sign-in page. */
export function takeNotice(): string | null {
  if (typeof window === "undefined") return null;
  const n = window.sessionStorage.getItem(NOTICE);
  if (n) window.sessionStorage.removeItem(NOTICE);
  return n;
}

/** End the session on the server, so the token stops working everywhere, then forget it here. */
export async function signOut(notice?: string) {
  const s = getSession();
  if (s) {
    try {
      await fetch(`${API}/v1/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${s.token}` }, cache: "no-store" });
    } catch {
      /* the session is forgotten here either way */
    }
  }
  clearSession(notice);
}

export function getTenant(): string {
  if (typeof window === "undefined") return "bank_a";
  return window.localStorage.getItem(TENANT) || getSession()?.staff.tenant_id || "bank_a";
}

export function setTenant(t: string) {
  window.localStorage.setItem(TENANT, t);
}

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** A token lasts an hour. While someone is working it is renewed in the background; an unattended tab is
 *  not renewed, so it lapses by itself. One renewal at a time, however many requests are waiting. */
function renewIfDue(): Promise<void> | null {
  const s = getSession();
  if (!s) return null;
  if (s.expiresAt - Date.now() > Math.min(10 * 60_000, s.life / 3)) return null;
  if (idleFor() > IDLE_MS) return null;
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(`${API}/v1/auth/refresh`, { method: "POST", headers: { Authorization: `Bearer ${s.token}` }, cache: "no-store" });
        if (res.ok && getSession()?.token === s.token) {
          const last = lastActivity;
          adoptSession((await res.json()) as Grant, s.staff);
          lastActivity = last;                       // renewing is not something the person did
        }
      } catch {
        /* offline: carry on with the token we have */
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: any; auth?: boolean } = {}): Promise<T> {
  if (opts.auth !== false) await renewIfDue();
  const headers: Record<string, string> = { Accept: "application/json" };
  const s = getSession();
  if (opts.auth !== false && s) {
    headers.Authorization = `Bearer ${s.token}`;
    headers["X-Tenant"] = getTenant();
  }
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "NETWORK", `Cannot reach the API at ${API}. Is it running?`);
  }
  if (res.status === 401 && opts.auth !== false) {
    clearSession("expired");
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) window.location.href = "/login";
  }
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const e = data?.error || {};
    throw new ApiError(res.status, e.code || "ERROR", e.message || `Request failed (${res.status})`);
  }
  return data as T;
}

function safeJson(t: string) {
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
}

/** Opens a protected file (the complaint-pack PDF) in a new tab. */
export async function openProtected(path: string) {
  const s = getSession();
  const res = await fetch(`${API}${path}`, { headers: s ? { Authorization: `Bearer ${s.token}`, "X-Tenant": getTenant() } : {} });
  if (!res.ok) throw new ApiError(res.status, "ERROR", "Could not open the file");
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank", "noopener");
}
