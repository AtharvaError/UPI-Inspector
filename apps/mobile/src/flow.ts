// The payment flow: resolve the payee, ask for a risk decision before the PIN, then follow the decision.
import { api, newTxnId } from "./api";
import { actions, AppDispatch, Decision, EntryMethod, PayeeInfo, RootState, store } from "./store";

type Nav = { push: (n: any, p?: any) => void; replace: (n: any, p?: any) => void; home: () => void };

export async function resolvePayee(vpa: string, claimedName?: string): Promise<PayeeInfo> {
  const q = claimedName && claimedName.trim() ? `?claimed_name=${encodeURIComponent(claimedName.trim())}` : "";
  return api<PayeeInfo>(`/v1/payee/${encodeURIComponent(vpa.trim().toLowerCase())}/reputation${q}`);
}

export function startFlow(dispatch: AppDispatch, init?: { payee?: PayeeInfo | null; claimedName?: string; entryMethod?: EntryMethod; amount?: string; note?: string; parentDecisionId?: string | null }) {
  dispatch(actions.flow.started({
    payee: init?.payee ?? null, claimedName: init?.claimedName ?? "", entryMethod: init?.entryMethod ?? "typed",
    amount: init?.amount ?? "", note: init?.note ?? "", parentDecisionId: init?.parentDecisionId ?? null,
  }));
}

/** Ask for the decision. Session signals are what the app observed; none of them is typed content. */
export async function assessPayment(extra: { timeOnPayScreenS: number; amountEdits: number }): Promise<Decision> {
  const s: RootState = store.getState();
  const { me, locale } = s.session;
  const f = s.flow;
  if (!me || !f.payee) throw new Error("no payee");
  const decision = await api<Decision>("/v1/risk/assess", {
    body: {
      txn_id: newTxnId(),
      payer: { user_id: me.user_id, vpa: me.vpa, device_id: me.known_device_id || null },
      payee: { vpa: f.payee.vpa, claimed_name: f.claimedName.trim() || null },
      amount: Number(f.amount),
      note: f.note.trim() || null,
      session: {
        vpa_entry_method: f.entryMethod,
        app_switch_count: f.appSwitches,
        time_on_pay_screen_s: Math.round(extra.timeOnPayScreenS),
        amount_edit_count: extra.amountEdits,
        flow_started_at: new Date(f.startedAt || Date.now()).toISOString(),
      },
      parent_decision_id: f.parentDecisionId,
      locale,
    },
    timeoutMs: 8000,
  });
  store.dispatch(actions.flow.patched({ decision }));
  return decision;
}

/** Where a decision sends the payer. Warning stays on the amount screen, so it is not routed here. */
export function routeDecision(nav: Nav, d: Decision, how: "push" | "replace" = "push") {
  const go = how === "push" ? nav.push : nav.replace;
  if (d.action === "STEP_UP") return go("stepup");
  if (d.action === "HOLD") return go("hold");
  if (d.action === "DECLINE") return go("decline");
  return proceedToPin(nav, d, how);
}

/** The payment is allowed to continue: a guardian's approval first if one is required, then the PIN. */
export function proceedToPin(nav: Nav, d: Decision, how: "push" | "replace" = "push") {
  const go = how === "push" ? nav.push : nav.replace;
  if (d.guardian?.required) return go("wait");
  return go("pin");
}

export async function sendOutcome(decisionId: string, userAction: "PROCEEDED" | "ABORTED" | "STEPUP_ABANDONED") {
  try {
    await api("/v1/risk/outcome", { body: { decision_id: decisionId, user_action: userAction, final_status: "NOT_ATTEMPTED" } });
  } catch {
    /* the outcome is telemetry; a failure here must not trap the payer on a screen */
  }
}

export function finishFlow(nav: Nav, result: NonNullable<RootState["flow"]["result"]>) {
  const f = store.getState().flow;
  store.dispatch(actions.flow.ended({
    amount: Number(f.amount) || undefined, payeeName: f.payee?.registered_name, payeeVpa: f.payee?.vpa, ...result,
  }));
  nav.replace("result");
}

export async function refreshMe(dispatch: AppDispatch) {
  try {
    const me = await api("/v1/users/me");
    dispatch(actions.meLoaded(me));
    return me;
  } catch {
    return null;
  }
}
