"""Mule-network features (F-20). Pure functions over an edge list.

Used by the live graph job (edges from PostgreSQL) and by the bootstrap replay (edges in memory).

Direction matters: a victim who paid a mule is connected to it too. "Downstream" therefore follows
only *forwarding* payments - money sent on within an hour of being received - so the chain
mule -> next layer -> reported account is found, and a victim's ordinary payment is not.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict, deque
from typing import Iterable

FORWARD_WINDOW_S = 3600.0
PASSTHROUGH_WINDOW_S = 1800.0
DAY = 86400.0

Edge = tuple[float, str, str, float]     # (ts, payer_vpa, payee_vpa, amount)


def _forwarding_edges(edges: list[Edge]) -> list[Edge]:
    inbound: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ts, _src, dst, amt in edges:
        inbound[dst].append((ts, amt))
    for v in inbound.values():
        v.sort()
    out = []
    for ts, src, dst, amt in edges:
        ins = inbound.get(src)
        if not ins:
            continue
        times = [t for t, _ in ins]
        lo = bisect_left(times, ts - FORWARD_WINDOW_S)
        hi = bisect_left(times, ts)
        if hi > lo:
            received = sum(a for _, a in ins[lo:hi])
            if received > 0 and 0.5 * received <= amt <= 1.2 * received + 1:
                out.append((ts, src, dst, amt))
    return out


def downstream_hops(edges: list[Edge], reported: set[str], max_hops: int = 3) -> dict[str, int]:
    """Shortest forwarding path from each UPI ID to a reported one (1..max_hops)."""
    senders: dict[str, set[str]] = defaultdict(set)
    for _ts, src, dst, _amt in _forwarding_edges(edges):
        senders[dst].add(src)
    dist: dict[str, int] = {}
    q: deque = deque((r, 0) for r in reported)
    seen = set(reported)
    while q:
        node, d = q.popleft()
        if d >= max_hops:
            continue
        for src in senders.get(node, ()):
            if src not in seen:
                seen.add(src)
                dist[src] = d + 1
                q.append((src, d + 1))
    return dist


def passthrough_ratios(edges: list[Edge], now: float) -> dict[str, float]:
    """Over the last 24 hours: amount sent on within 30 minutes of an inbound payment / amount received."""
    recent = [e for e in edges if e[0] >= now - DAY]
    inbound: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ts, _src, dst, amt in recent:
        inbound[dst].append((ts, amt))
    received = {v: sum(a for _, a in lst) for v, lst in inbound.items()}
    forwarded: dict[str, float] = defaultdict(float)
    for ts, src, _dst, amt in recent:
        ins = inbound.get(src)
        if ins and any(ts - PASSTHROUGH_WINDOW_S <= t < ts for t, _ in ins):
            forwarded[src] += amt
    return {v: min(1.0, forwarded[v] / received[v]) for v in forwarded if received.get(v, 0) > 0}


def communities(edges: list[Edge], device_links: dict[str, Iterable[str]], exclude: set[str]) -> dict[str, int]:
    """Modularity communities on the undirected person-to-person graph plus shared-device links.
    Merchants are excluded: a busy shop would otherwise join every customer into one community."""
    import networkx as nx

    g = nx.Graph()
    for _ts, src, dst, _amt in edges:
        if src in exclude or dst in exclude or src == dst:
            continue
        g.add_edge(src, dst)
    by_fp: dict[str, list[str]] = defaultdict(list)
    for vpa, fps in device_links.items():
        for fp in fps:
            by_fp[fp].append(vpa)
    for vpas in by_fp.values():
        for other in vpas[1:]:
            g.add_edge(vpas[0], other)
    if g.number_of_nodes() == 0:
        return {}
    out: dict[str, int] = {}
    for i, comm in enumerate(nx.community.louvain_communities(g, seed=7)):
        if len(comm) >= 3:
            for v in comm:
                out[v] = i
    return out


def compute_graph_features(edges: list[Edge], device_links: dict[str, Iterable[str]], reported: set[str],
                           risky: set[str], now: float, comm: dict[str, int] | None = None) -> dict[str, dict]:
    """Return {vpa: {downstream_hop, shares_device_with_reported, passthrough_ratio, community_id,
    community_risk_share}} for every UPI ID that has something other than the defaults."""
    hops = downstream_hops(edges, reported)
    passthrough = passthrough_ratios(edges, now)

    reported_fps: set[str] = set()
    for vpa in reported:
        reported_fps.update(device_links.get(vpa, ()))
    shares = {vpa for vpa, fps in device_links.items()
              if vpa not in reported and any(fp in reported_fps for fp in fps)}

    risk_share: dict[str, float] = {}
    comm = comm or {}
    if comm:
        members: dict[int, list[str]] = defaultdict(list)
        for vpa, cid in comm.items():
            members[cid].append(vpa)
        for cid, vpas in members.items():
            share = sum(1 for v in vpas if v in risky or v in reported) / len(vpas)
            if share > 0:
                for v in vpas:
                    risk_share[v] = share

    out: dict[str, dict] = {}
    for vpa in set(hops) | set(passthrough) | shares | set(risk_share):
        out[vpa] = {
            "downstream_hop": hops.get(vpa, 9),
            "shares_device_with_reported": 1 if vpa in shares else 0,
            "passthrough_ratio": round(passthrough.get(vpa, 0.0), 4),
            "community_id": comm.get(vpa),
            "community_risk_share": round(risk_share.get(vpa, 0.0), 4),
        }
    return out


def graph_work(edges: list[Edge], device_links: dict[str, Iterable[str]], exclude: set[str], reported: set[str],
               risky: set[str], now: float, comm: dict[str, int] | None, recompute_communities: bool
               ) -> tuple[dict[str, dict], dict[str, int]]:
    """Everything the graph job computes, as one call that can run in another process."""
    if recompute_communities or not comm:
        comm = communities(edges, device_links, exclude)
    return compute_graph_features(edges, device_links, reported, risky, now, comm), comm
