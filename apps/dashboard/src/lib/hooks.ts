"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { io, Socket } from "socket.io-client";
import { API, api, getSession } from "./api";
import { bump, bumpReputation, driftAlert, pushDecision, setConnected, store, useAppSelector } from "./store";

/** Fetch with loading and error state. Refetches when `deps` change or `reload()` is called. */
export function useApi<T = any>(path: string | null, deps: any[] = [], pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const tenant = useAppSelector((s) => s.session.tenant);
  const alive = useRef(true);

  const load = useCallback(async (quiet = false) => {
    if (!path) return;
    if (!quiet) setLoading(true);
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e: any) {
      if (alive.current) setError(e.message || "Request failed");
    } finally {
      if (alive.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tenant, ...deps]);

  useEffect(() => {
    alive.current = true;
    load();
    let t: any;
    if (pollMs > 0) t = setInterval(() => load(true), pollMs);
    return () => {
      alive.current = false;
      if (t) clearInterval(t);
    };
  }, [load, pollMs]);

  return { data, error, loading, reload: () => load(true), setData };
}

let socket: Socket | null = null;

/** One socket for the dashboard. Events are notifications: pages refetch over REST. */
export function useLiveSocket(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const s = getSession();
    if (!s) return;
    // The token is read each time the socket (re)connects, so a reconnect after a renewal uses the new one.
    socket = io(`${API}/analyst`, { auth: (cb) => cb({ token: getSession()?.token || "" }), transports: ["websocket", "polling"], reconnectionDelayMax: 5000 });
    socket.on("connect", () => store.dispatch(setConnected(true)));
    socket.on("disconnect", () => store.dispatch(setConnected(false)));
    socket.on("connect_error", () => store.dispatch(setConnected(false)));
    socket.on("decision.created", (d) => store.dispatch(pushDecision(d)));
    socket.on("outcome.recorded", () => store.dispatch(bump()));
    socket.on("case.summary_ready", () => store.dispatch(bump()));
    socket.on("reputation.updated", () => store.dispatch(bumpReputation()));
    socket.on("drift.alert", (a) => store.dispatch(driftAlert(a)));
    return () => {
      socket?.disconnect();
      socket = null;
    };
  }, [enabled]);
}

export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e: any) {
      setError(e.message || "Something went wrong");
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}
