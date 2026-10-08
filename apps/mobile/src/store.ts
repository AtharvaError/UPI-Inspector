// App state. Server data is fetched by the screens; this holds what several screens share.
import { configureStore, createSlice, PayloadAction } from "@reduxjs/toolkit";
import { TypedUseSelectorHook, useDispatch, useSelector } from "react-redux";

// ------------------------------------------------------------------ session --
export type Me = {
  user_id: string;
  name: string;
  vpa: string;
  tenant_id: string;
  locale: string;
  voice_warnings: boolean;
  large_text: boolean;
  balance: number;
  phone_last4?: string;
  segment?: string;
  known_device_id?: string | null;
  guardian?: { mode: string; status: string; amount_threshold: number | null; display_name: string } | null;
  is_guardian?: boolean;
  pending_guardian_requests?: number;
  active_holds?: number;
  demo_mode?: boolean;
};

type SessionState = {
  ready: boolean; token: string | null; me: Me | null; locale: string; demoMode: boolean; online: boolean;
  authMethod: string;            // pin | demo: how this session was opened
  demoLogin: boolean;            // the server offers passwordless demo sign-in to this device
  locked: boolean;               // the app lock is showing (F-35)
  notice: string | null;         // why the person was signed out, shown once on the sign-in screen
};
const sessionInit: SessionState = {
  ready: false, token: null, me: null, locale: "en", demoMode: true, online: true, authMethod: "pin", demoLogin: false,
  locked: false, notice: null,
};

const session = createSlice({
  name: "session",
  initialState: sessionInit,
  reducers: {
    booted(s, a: PayloadAction<{ token: string | null; me: Me | null; locale?: string | null; authMethod?: string; locked?: boolean }>) {
      s.ready = true;
      s.token = a.payload.token;
      s.me = a.payload.me;
      s.locale = a.payload.me?.locale || a.payload.locale || s.locale;
      s.authMethod = a.payload.authMethod || "pin";
      s.locked = !!a.payload.locked;
    },
    signedIn(s, a: PayloadAction<{ token: string; me: Me; authMethod?: string }>) {
      s.token = a.payload.token;
      s.me = a.payload.me;
      s.locale = a.payload.me.locale || s.locale;
      s.authMethod = a.payload.authMethod || "pin";
      s.locked = false;
      s.notice = null;
    },
    tokenRenewed(s, a: PayloadAction<string>) {
      if (s.token) s.token = a.payload;
    },
    signedOut(s, a: PayloadAction<string | undefined>) {
      s.token = null;
      s.me = null;
      s.locked = false;
      s.notice = a.payload || null;
    },
    lockSet(s, a: PayloadAction<boolean>) {
      s.locked = a.payload && !!s.token;
    },
    noticeCleared(s) {
      s.notice = null;
    },
    demoLoginSet(s, a: PayloadAction<boolean>) {
      s.demoLogin = a.payload;
    },
    meLoaded(s, a: PayloadAction<Partial<Me>>) {
      s.me = { ...(s.me as Me), ...a.payload };
      if (a.payload.locale) s.locale = a.payload.locale;
      if (typeof a.payload.demo_mode === "boolean") s.demoMode = a.payload.demo_mode;
    },
    localeSet(s, a: PayloadAction<string>) {
      s.locale = a.payload === "hi" ? "hi" : "en";
    },
    demoModeSet(s, a: PayloadAction<boolean>) {
      s.demoMode = a.payload;
    },
    onlineSet(s, a: PayloadAction<boolean>) {
      s.online = a.payload;
    },
  },
});

// ------------------------------------------------------------------ navigation --
export type ScreenName =
  | "home" | "payee" | "scan" | "amount" | "stepup" | "hold" | "decline" | "pin" | "result" | "wait"
  | "holds" | "message" | "guardian" | "request" | "report" | "practice" | "rehearsal" | "history" | "settings"
  | "safety";

export const TABS: ScreenName[] = ["home", "safety", "message", "holds", "guardian", "practice", "history", "settings"];

export type Route = { key: string; name: ScreenName; params?: any };
let seq = 0;
const mk = (name: ScreenName, params?: any): Route => ({ key: `${name}-${++seq}`, name, params });

const nav = createSlice({
  name: "nav",
  initialState: { stack: [mk("home")] as Route[] },
  reducers: {
    push(s, a: PayloadAction<{ name: ScreenName; params?: any }>) {
      s.stack.push(mk(a.payload.name, a.payload.params));
    },
    replace(s, a: PayloadAction<{ name: ScreenName; params?: any }>) {
      s.stack[s.stack.length - 1] = mk(a.payload.name, a.payload.params);
    },
    pop(s) {
      if (s.stack.length > 1) s.stack.pop();
    },
    /** Top-level destination: Home is always underneath, so Back returns to it. */
    tab(s, a: PayloadAction<{ name: ScreenName; params?: any }>) {
      s.stack = a.payload.name === "home" ? [mk("home")] : [mk("home"), mk(a.payload.name, a.payload.params)];
    },
    reset(s, a: PayloadAction<{ name: ScreenName; params?: any } | undefined>) {
      s.stack = a.payload && a.payload.name !== "home" ? [mk("home"), mk(a.payload.name, a.payload.params)] : [mk("home")];
    },
  },
});

// ------------------------------------------------------------------ payment flow --
export type PayeeInfo = {
  vpa: string;
  exists: boolean;
  registered_name?: string;
  kind?: string;
  verified?: boolean;
  band?: "TRUSTED" | "NEUTRAL" | "NO_HISTORY" | "CAUTION" | "REPORTED";
  band_reason?: string;
  trust_score?: number;
  name_match?: { score: number; mismatch: boolean; institutional_claim: boolean };
  paid_before?: number;
  is_self?: boolean;
  blocked?: boolean;          // on the payer's never-pay list (F-33)
};

export type Reason = { code: string; group: string; message: string; evidence?: any };
export type Family = { code: string; name: string; headline: string; body: string; similarity?: number; audio_key?: string };
export type Decision = {
  decision_id: string;
  txn_id: string;
  score: number;
  tier: "SAFE" | "WARNING" | "STEP_UP" | "BLOCK";
  action: "ALLOW" | "WARN" | "STEP_UP" | "HOLD" | "DECLINE";
  enforced: boolean;
  reasons: Reason[];
  scam_family: Family | null;
  payee: any;
  step_up: { challenge_id: string; cooloff_s: number; question: { text: string; answers: string[] }; auth_methods: string[] } | null;
  hold: { hold_id: string; release_at: string; confirm_by: string } | null;
  guardian: { required: boolean; kind: string; request_id: string; guardian_name: string; status: string; expires_at: string | null } | null;
  links?: { calls: string[]; messages: string[] };
  user_block?: { blocked: boolean; unblock_at: string | null } | null;     // the payer's own never-pay list (F-33)
  degraded?: boolean;
};

export type EntryMethod = "typed" | "pasted" | "contact" | "qr" | "link";

type FlowState = {
  payee: PayeeInfo | null;
  claimedName: string;
  entryMethod: EntryMethod;
  amount: string;
  note: string;
  startedAt: number;          // ms; when the payment flow began
  appSwitches: number;        // times the app went to the background during the flow
  parentDecisionId: string | null;
  decision: Decision | null;
  result: { kind: "success" | "cancelled" | "failed"; reference?: string; message?: string; amount?: number; payeeName?: string; payeeVpa?: string } | null;
  active: boolean;
};
const flowInit: FlowState = {
  payee: null, claimedName: "", entryMethod: "typed", amount: "", note: "", startedAt: 0, appSwitches: 0,
  parentDecisionId: null, decision: null, result: null, active: false,
};

const flow = createSlice({
  name: "flow",
  initialState: flowInit,
  reducers: {
    started(_s, a: PayloadAction<Partial<FlowState> | undefined>) {
      return { ...flowInit, startedAt: Date.now(), active: true, ...(a.payload || {}) };
    },
    patched(s, a: PayloadAction<Partial<FlowState>>) {
      Object.assign(s, a.payload);
    },
    appSwitched(s) {
      if (s.active) s.appSwitches += 1;
    },
    ended(s, a: PayloadAction<FlowState["result"]>) {
      s.result = a.payload;
      s.active = false;
    },
    cleared() {
      return flowInit;
    },
  },
});

// ------------------------------------------------------------------ overlays (demo call, simulated SMS, toast) --
export type Sms = { id: string; kind: "otp" | "message"; text: string; sender?: string; link?: { vpa: string; name?: string | null; amount?: number; note?: string } };
type OverlayState = {
  call: { phase: "ringing" | "active"; call_id: string; caller_label: string; started_at: number; caller_risk?: number } | null;
  sms: Sms[];
  toast: { id: number; text: string; tone: "info" | "safe" | "warning" | "block"; action?: { label: string; screen: ScreenName; params?: any } } | null;
  tick: number;               // bumped by socket events so screens know to refetch
};
const overlay = createSlice({
  name: "overlay",
  initialState: { call: null, sms: [], toast: null, tick: 0 } as OverlayState,
  reducers: {
    callIncoming(s, a: PayloadAction<{ call_id: string; caller_label: string; started_at: number; caller_risk?: number }>) {
      s.call = { phase: "ringing", ...a.payload };
    },
    /** Other people have reported the number that is calling (F-31). */
    callFlagged(s, a: PayloadAction<{ call_id: string; risk: number }>) {
      if (s.call && s.call.call_id === a.payload.call_id) s.call.caller_risk = Math.max(s.call.caller_risk || 0, a.payload.risk);
    },
    callAccepted(s) {
      if (s.call) s.call.phase = "active";
    },
    callRestored(s, a: PayloadAction<{ call_id: string; started_at: number; caller_risk?: number }>) {
      if (!s.call) s.call = { phase: "active", caller_label: "+91 91111 111••", ...a.payload };
      else if (s.call.call_id === a.payload.call_id) s.call.caller_risk = Math.max(s.call.caller_risk || 0, a.payload.caller_risk || 0);
    },
    callCleared(s) {
      s.call = null;
    },
    smsArrived(s, a: PayloadAction<Sms>) {
      s.sms = [a.payload, ...s.sms].slice(0, 3);
    },
    smsDismissed(s, a: PayloadAction<string>) {
      s.sms = s.sms.filter((m) => m.id !== a.payload);
    },
    otpCleared(s) {
      s.sms = s.sms.filter((m) => m.kind !== "otp");
    },
    toast(s, a: PayloadAction<Omit<NonNullable<OverlayState["toast"]>, "id">>) {
      s.toast = { id: Date.now(), ...a.payload };
    },
    toastCleared(s) {
      s.toast = null;
    },
    ticked(s) {
      s.tick += 1;
    },
    reset() {
      return { call: null, sms: [], toast: null, tick: 0 };
    },
  },
});

export const actions = { ...session.actions, nav: nav.actions, flow: flow.actions, overlay: overlay.actions };

export const store = configureStore({
  reducer: { session: session.reducer, nav: nav.reducer, flow: flow.reducer, overlay: overlay.reducer },
  middleware: (getDefault) => getDefault({ serializableCheck: false }),
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
export const useAppDispatch: () => AppDispatch = useDispatch;
export const useAppSelector: TypedUseSelectorHook<RootState> = useSelector;
