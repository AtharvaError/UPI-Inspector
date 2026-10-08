"use client";

import { configureStore, createSlice, PayloadAction } from "@reduxjs/toolkit";
import { TypedUseSelectorHook, useDispatch, useSelector } from "react-redux";
import type { Staff } from "./api";

export type LiveDecision = {
  decision_id: string; at: number; amount: number; payee_vpa: string; payee_name?: string; tier: string; action: string;
  score: number; top_reason?: string | null; family?: string | null; family_code?: string | null; enforced: boolean;
  degraded?: boolean; latency_ms?: number; synthetic?: boolean; linked?: { call: boolean; message: boolean };
  guardian?: boolean; tenant_id: string; fresh?: boolean;
};

type SessionState = { staff: Staff | null; tenant: string; ready: boolean };
const session = createSlice({
  name: "session",
  initialState: { staff: null, tenant: "bank_a", ready: false } as SessionState,
  reducers: {
    setStaff(s, a: PayloadAction<{ staff: Staff | null; tenant: string }>) {
      s.staff = a.payload.staff;
      s.tenant = a.payload.tenant;
      s.ready = true;
    },
    setTenantState(s, a: PayloadAction<string>) {
      s.tenant = a.payload;
    },
  },
});

type LiveState = { items: LiveDecision[]; connected: boolean; tick: number; reputation: number; alerts: { feature: string; psi: number }[] };
const live = createSlice({
  name: "live",
  initialState: { items: [], connected: false, tick: 0, reputation: 0, alerts: [] } as LiveState,
  reducers: {
    pushDecision(s, a: PayloadAction<LiveDecision>) {
      s.items.unshift({ ...a.payload, fresh: true });
      if (s.items.length > 200) s.items.length = 200;
      s.tick += 1;
    },
    clearLive(s) {
      s.items = [];
    },
    setConnected(s, a: PayloadAction<boolean>) {
      s.connected = a.payload;
    },
    bump(s) {
      s.tick += 1;
    },
    bumpReputation(s) {
      s.reputation += 1;
    },
    driftAlert(s, a: PayloadAction<{ feature: string; psi: number }>) {
      s.alerts = [a.payload, ...s.alerts.filter((x) => x.feature !== a.payload.feature)].slice(0, 8);
    },
  },
});

const ui = createSlice({
  name: "ui",
  initialState: { collapsed: false },
  reducers: {
    toggleSide(s) {
      s.collapsed = !s.collapsed;
    },
  },
});

export const { setStaff, setTenantState } = session.actions;
export const { pushDecision, clearLive, setConnected, bump, bumpReputation, driftAlert } = live.actions;
export const { toggleSide } = ui.actions;

export const store = configureStore({ reducer: { session: session.reducer, live: live.reducer, ui: ui.reducer } });
export type RootState = ReturnType<typeof store.getState>;
export const useAppDispatch: () => typeof store.dispatch = useDispatch;
export const useAppSelector: TypedUseSelectorHook<RootState> = useSelector;
