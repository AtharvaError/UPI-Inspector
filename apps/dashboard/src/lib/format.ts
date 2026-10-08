export const inr = (n: number | null | undefined, digits = 0) =>
  n == null ? "—" : "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });

/** Large totals the Indian way: crore and lakh. */
export const inrCompact = (n: number | null | undefined) =>
  n == null ? "—" : n >= 1e7 ? `₹${(n / 1e7).toFixed(2)} Cr` : n >= 1e5 ? `₹${(n / 1e5).toFixed(2)} L` : inr(n);

export const pct = (x: number | null | undefined, digits = 1) => (x == null ? "—" : (x * 100).toFixed(digits) + "%");

export const num = (n: number | null | undefined) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));

export function ago(iso: string | number | null | undefined): string {
  if (!iso) return "—";
  const t = typeof iso === "number" ? iso * 1000 : new Date(iso).getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function clock(iso: string | number | null | undefined, withDate = false): string {
  if (!iso) return "—";
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  const opts: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
  if (withDate) Object.assign(opts, { day: "2-digit", month: "short" });
  return d.toLocaleString("en-IN", opts);
}

export const TIER_LABEL: Record<string, string> = { SAFE: "Safe", WARNING: "Warning", STEP_UP: "Step-Up", BLOCK: "Block" };
export const ACTION_LABEL: Record<string, string> = { ALLOW: "Allowed", WARN: "Warned", STEP_UP: "Step-up", HOLD: "Held", DECLINE: "Declined" };
export const BAND_LABEL: Record<string, string> = { TRUSTED: "Trusted", NEUTRAL: "Neutral", NO_HISTORY: "No history", CAUTION: "Caution", REPORTED: "Reported" };
export const BANK: Record<string, string> = { bank_a: "Bank A", bank_b: "Bank B" };
export const OUTCOME_LABEL: Record<string, string> = {
  PROCEEDED: "Paid", ABORTED: "Cancelled at warning", STEPUP_PASSED: "Passed step-up and paid", STEPUP_ABANDONED: "Cancelled in step-up",
  STEPUP_FAILED: "Admitted being coached", STEPUP_LOCKED: "Step-up locked after wrong codes", BLOCKED: "Declined", HOLD_CANCELLED: "Cancelled a held payment", HOLD_CONFIRMED: "Confirmed after hold",
  HOLD_EXPIRED: "Hold expired", GUARDIAN_DENIED: "Guardian refused", GUARDIAN_TIMEOUT: "Guardian did not answer", SHADOW_ALLOWED: "Shadow: allowed",
};
export const TIER_COLOR: Record<string, string> = { SAFE: "#34D399", WARNING: "#FBBF24", STEP_UP: "#FB923C", BLOCK: "#F43F5E" };
export const BAND_COLOR: Record<string, string> = { TRUSTED: "#34D399", NEUTRAL: "#8B88B5", NO_HISTORY: "#5C5980", CAUTION: "#FBBF24", REPORTED: "#F43F5E" };
export const FAMILY: Record<string, string> = {
  DIGITAL_ARREST: "Digital arrest", FAKE_REFUND: "Fake refund", MULE_TRANSFER: "Mule transfer", IMPERSONATION: "Impersonation",
  KYC_BILL_THREAT: "KYC or bill threat", INVESTMENT_TASK: "Investment or task scam", UNCLASSIFIED: "Unclassified",
};
export const RISK_WORD = ["not rated", "medium risk", "high risk", "very high risk"];
