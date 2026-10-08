// Real-time channel to the API (Socket.IO, /user namespace). Events are notifications: the screens
// re-fetch over REST for the full record, so a missed event costs a refresh, never a wrong screen.
import { io, Socket } from "socket.io-client";
import { API_BASE, getToken } from "./api";
import { money, translate } from "./i18n";
import { actions, store } from "./store";

let socket: Socket | null = null;
type Listener = (data: any) => void;
const listeners: Record<string, Set<Listener>> = {};

/** Subscribe a screen to one event. Returns the unsubscribe function. */
export function onEvent(event: string, fn: Listener): () => void {
  (listeners[event] ||= new Set()).add(fn);
  return () => listeners[event]?.delete(fn);
}
function fan(event: string, data: any) {
  listeners[event]?.forEach((fn) => {
    try {
      fn(data);
    } catch {
      /* one bad listener must not stop the others */
    }
  });
}

export function connectSocket() {
  disconnectSocket();
  // The token is read each time the socket (re)connects, so a reconnect after a renewal uses the new one.
  const s = io(`${API_BASE}/user`, { auth: (cb) => cb({ token: getToken() || "" }), transports: ["websocket", "polling"], reconnectionDelayMax: 5000 });
  socket = s;
  const d = store.dispatch;
  const t = (key: string, vars?: Record<string, string | number>) => translate(store.getState().session.locale, key, vars);
  const amount = (n: any) => money(Math.round(Number(n || 0)));

  s.on("connect", () => d(actions.onlineSet(true)));
  s.on("disconnect", (reason) => {
    d(actions.onlineSet(false));
    // The server closes the socket when the PIN changes or a session is renewed elsewhere. If this device
    // still holds a session, connect again with the token it has now; a refused connection is not retried.
    if (reason === "io server disconnect" && getToken()) setTimeout(() => { if (socket === s && getToken()) s.connect(); }, 800);
  });

  s.on("call.incoming", (e) => {
    d(actions.overlay.callIncoming({ call_id: e.call_id, caller_label: e.caller_label || "+91 •••••", started_at: Number(e.started_at) || Date.now() / 1000, caller_risk: Number(e.caller_risk) || 0 }));
  });
  s.on("call.ended", () => d(actions.overlay.callCleared()));
  // A number other people have reported is calling (F-31).
  s.on("call.flagged", (e) => {
    d(actions.overlay.callFlagged({ call_id: e.call_id, risk: Number(e.risk) || 2 }));
    fan("call.flagged", e);
  });
  s.on("block.lifted", (e) => {
    d(actions.overlay.ticked());
    d(actions.overlay.toast({ tone: "info", text: t("toast.blockLifted", { vpa: e.vpa || "" }), action: { label: t("toast.open"), screen: "safety", params: { tab: "blocked" } } }));
    fan("block.lifted", e);
  });

  s.on("sms.simulated", (e) => {
    d(actions.overlay.smsArrived({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, kind: e.kind === "otp" ? "otp" : "message", text: String(e.text || ""), sender: e.sender, link: e.link }));
    fan("sms.simulated", e);
  });

  s.on("guardian.request", (e) => {
    d(actions.overlay.ticked());
    d(actions.overlay.toast({
      tone: e.kind === "APPROVAL" ? "warning" : "info",
      text: t(e.kind === "APPROVAL" ? "toast.guardianRequest" : "toast.guardianAlert", { name: e.protected_name || "", amount: amount(e.amount) }),
      action: { label: t("toast.open"), screen: "request", params: { request_id: e.request_id } },
    }));
    fan("guardian.request", e);
  });
  s.on("guardian.response", (e) => {
    d(actions.overlay.ticked());
    fan("guardian.response", e);
  });
  s.on("guardian.link", (e) => {
    d(actions.overlay.ticked());
    d(actions.overlay.toast({ tone: "info", text: t("toast.guardianLink"), action: { label: t("toast.open"), screen: "guardian" } }));
    fan("guardian.link", e);
  });
  s.on("hold.ready", (e) => {
    d(actions.overlay.ticked());
    d(actions.overlay.toast({ tone: "warning", text: t("toast.holdReady"), action: { label: t("toast.open"), screen: "holds" } }));
    fan("hold.ready", e);
  });
}

export function disconnectSocket() {
  if (socket) {
    socket.removeAllListeners();
    socket.disconnect();
    socket = null;
  }
}
