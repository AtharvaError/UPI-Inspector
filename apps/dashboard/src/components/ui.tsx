"use client";

import { AlertTriangle, Ban, CheckCircle2, Hand, Info, ShieldAlert } from "lucide-react";
import React from "react";
import { ACTION_LABEL, BAND_LABEL, TIER_LABEL } from "@/lib/format";

export function Card({ title, right, children, flush, className = "", style }: {
  title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; flush?: boolean; className?: string; style?: React.CSSProperties;
}) {
  return (
    <section className={`card ${flush ? "flush" : ""} ${className}`} style={style}>
      {(title || right) && (
        <div className="card-head">
          <h2>{title}</h2>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

const TIER_ICON: Record<string, React.ReactNode> = {
  SAFE: <CheckCircle2 size={13} />, WARNING: <AlertTriangle size={13} />, STEP_UP: <Hand size={13} />, BLOCK: <Ban size={13} />,
};

/** A tier is always an icon and a word as well as a colour. */
export function TierTag({ tier }: { tier: string }) {
  return (
    <span className={`chip t-${tier}`}>
      {TIER_ICON[tier]} {TIER_LABEL[tier] || tier}
    </span>
  );
}

export function ActionTag({ action, enforced = true }: { action: string; enforced?: boolean }) {
  const would: Record<string, string> = { ALLOW: "allow", WARN: "warn", STEP_UP: "step up", HOLD: "hold", DECLINE: "decline" };
  return <span className="chip">{enforced ? ACTION_LABEL[action] || action : `Shadow · would ${would[action] || action.toLowerCase()}`}</span>;
}

export function BandTag({ band }: { band: string }) {
  return <span className={`chip b-${band}`}>{band === "REPORTED" && <ShieldAlert size={13} />} {BAND_LABEL[band] || band}</span>;
}

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="card stat">
      <div className="k">{label}</div>
      <div className="v" style={tone ? { color: tone } : undefined}>{value}</div>
      {sub && <div className="caption" style={{ marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, disabled }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; disabled?: boolean;
}) {
  return (
    <div className="seg" role="tablist">
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={o.value === value} className={o.value === value ? "on" : ""}
          disabled={disabled} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Banner({ kind = "info", children }: { kind?: "info" | "warn" | "err" | "ok"; children: React.ReactNode }) {
  const icon = kind === "err" ? <ShieldAlert size={18} /> : kind === "warn" ? <AlertTriangle size={18} /> : kind === "ok" ? <CheckCircle2 size={18} /> : <Info size={18} />;
  return (
    <div className={`banner ${kind}`} role={kind === "err" ? "alert" : "status"}>
      <span style={{ flex: "0 0 auto", marginTop: 2 }}>{icon}</span>
      <div className="grow">{children}</div>
    </div>
  );
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div className="col gap-2" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 22, width: `${92 - i * 9}%` }} />
      ))}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Bar({ value, color }: { value: number; color?: string }) {
  return (
    <div className="bar">
      <i style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%`, background: color }} />
    </div>
  );
}

export function PageHead({ title, sub, right }: { title: string; sub?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="row between top wrap" style={{ marginBottom: 4 }}>
      <div>
        <h1>{title}</h1>
        {sub && <p className="muted" style={{ marginTop: 4, maxWidth: 760 }}>{sub}</p>}
      </div>
      {right && <div className="row wrap">{right}</div>}
    </div>
  );
}

export function Modal({ title, children, onClose, footer }: { title: string; children: React.ReactNode; onClose: () => void; footer?: React.ReactNode }) {
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 50, background: "rgba(3,2,10,0.72)", display: "grid", placeItems: "center", padding: 16 }}
      onClick={onClose} role="dialog" aria-modal="true" aria-label={title}>
      <div className="card raised" style={{ width: "min(520px, 100%)", boxShadow: "var(--shadow-card)" }} onClick={(e) => e.stopPropagation()}>
        <div className="card-head"><h2>{title}</h2></div>
        <div className="col">{children}</div>
        {footer && <div className="row" style={{ justifyContent: "flex-end", marginTop: 20 }}>{footer}</div>}
      </div>
    </div>
  );
}
