"""Reject incomplete multi-rank runs, then summarize measured API completion times."""
import argparse
from collections import defaultdict
from itertools import product
import json
import math
import statistics
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("directory", type=Path)
p.add_argument("--world", type=int, required=True)
p.add_argument("--hosts", type=int, default=2)
p.add_argument("--sizes", default="4096,65536,1048576")
p.add_argument("--windows", default="1,32,128")
p.add_argument("--patterns", default="pair,rotate")
p.add_argument("--ops", type=int, default=128)
p.add_argument("--repeats", type=int, default=3)
a = p.parse_args()
assert a.world >= 2 and a.world % 2 == 0
sizes = [int(x) for x in a.sizes.split(",")]
windows = [int(x) for x in a.windows.split(",")]
patterns = a.patterns.split(",")
assert min(sizes + windows + [a.ops, a.repeats]) > 0
assert set(patterns) <= {"pair", "rotate"}
expected = set(product(patterns, ("read", "write"), sizes, windows, range(a.repeats + 1)))
a.cases = len(expected)
rows = []
files = list(a.directory.rglob("samples-*.jsonl"))
assert len(files) == a.world, (len(files), a.world)
ranks = set()
for f in files:
    local = [json.loads(s) for s in f.read_text().splitlines()]
    assert len(local) == a.cases, (str(f), len(local))
    rank = local[0]["rank"]
    assert rank not in ranks
    ranks.add(rank)
    keys = set()
    for r in local:
        assert r["rank"] == rank and r["verified"] is True
        key = tuple(r[k] for k in ("pattern", "operation", "size", "window", "repeat"))
        assert key not in keys
        keys.add(key)
        assert r["warmup"] == (r["repeat"] == 0) and r["ops"] == a.ops
        if rank < a.world // 2:
            assert r["seconds"] is not None and math.isfinite(r["seconds"]) and r["seconds"] > 0
        else:
            assert r["seconds"] is None
    assert keys == expected, (str(f), "missing", expected - keys, "unexpected", keys - expected)
    log = f.with_name(f"rank-{rank}.log").read_text()
    events = [json.loads(s) for s in log.splitlines() if s.startswith('{"event"')]
    assert any(e["event"] == "complete" and e["rank"] == rank and e["cases"] == a.cases for e in events)
    rows.extend(local)
assert ranks == set(range(a.world))
exits = [json.loads(f.read_text()) for f in a.directory.rglob("rank-exits.json")]
assert sum(len(x) for x in exits) == a.world and all(c == 0 for x in exits for c in x)
topologies = [json.loads(f.read_text()) for f in a.directory.rglob("topology-*.json")]
assert len(topologies) == a.world and all(t == topologies[0] for t in topologies)
assert [r["rank"] for r in topologies[0]] == list(range(a.world))
assert len({r["boot_id"] for r in topologies[0]}) == a.hosts
transports = {r["transport"] for r in rows}
assert len(transports) == 1 and transports == {r["transport"] for r in topologies[0]}
if a.hosts == 2:
    left = {r["boot_id"] for r in topologies[0][:a.world // 2]}
    right = {r["boot_id"] for r in topologies[0][a.world // 2:]}
    assert len(left) == len(right) == 1 and left.isdisjoint(right)
initiators = [r for r in rows if r["seconds"] is not None]
assert {r["rank"] for r in initiators} == set(range(a.world // 2))
groups = defaultdict(list)
for r in initiators:
    if not r["warmup"]:
        assert r["seconds"] > 0
        groups[tuple(r[k] for k in ("pattern", "operation", "size", "window"))].append(r)
cells = []
for (pattern, operation, size, window), samples in sorted(groups.items()):
    cells.append(dict(pattern=pattern, operation=operation, bytes=size, window=window,
        per_initiator_GBps_median=statistics.median(r["ops"]*size/r["seconds"]/1e9 for r in samples),
        per_initiator_round_ms_median=statistics.median(r["seconds"]*1000 for r in samples),
        samples=len(samples)))
summary = dict(world=a.world, hosts=a.hosts, transport=next(iter(transports)),
    rank_rows=len(rows), cases_per_rank=a.cases,
    transfers=sum(r["ops"] for r in initiators),
    payload_bytes=sum(r["ops"]*r["size"] for r in initiators),
    integrity="all scheduled ranges verified", rank_exits=exits,
    note="Independent samples share physical NICs. Per-initiator API timing includes Python/ctypes submission, polling and freeing; excludes registration and verification. Not raw NIC bandwidth.",
    cells=cells)
(a.directory / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps({k:v for k,v in summary.items() if k != "cells"}, indent=2))
