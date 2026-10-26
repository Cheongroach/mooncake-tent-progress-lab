"""Bounded local rank launcher; terminates siblings if any rank fails."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--ranks", type=int, default=8)
p.add_argument("--nodes", type=int, default=2)
p.add_argument("--node-rank", type=int, required=True)
p.add_argument("--seconds", type=int, default=900)
p.add_argument("--output", type=Path, required=True)
a, extra = p.parse_known_args()
a.output.mkdir(parents=True, exist_ok=True)
script = Path(__file__).resolve().parents[1] / "benchmarks/tent_bench.py"
procs = []
try:
    for rank in range(a.ranks):
        global_rank = a.node_rank * a.ranks + rank
        cmd = [
            sys.executable,
            str(script),
            "--world",
            str(a.ranks * a.nodes),
            "--rank",
            str(global_rank),
            "--local-rank",
            str(rank),
            "--local-world",
            str(a.ranks),
            "--output",
            str(a.output),
            *extra,
        ]
        log = (a.output / f"rank-{global_rank}.log").open("w")
        procs.append(subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
        log.close()
    deadline = time.monotonic() + a.seconds
    while any(child.poll() is None for child in procs):
        if any(child.poll() not in (None, 0) for child in procs):
            raise RuntimeError("rank failed; see per-rank logs")
        if time.monotonic() > deadline:
            raise TimeoutError("rank launcher deadline")
        time.sleep(0.2)
    if any(child.returncode != 0 for child in procs):
        raise RuntimeError("rank failed")
finally:
    for child in procs:
        if child.poll() is None:
            os.killpg(child.pid, 15)
    for child in procs:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, 9)
            child.wait()
    (a.output / "rank-exits.json").write_text(json.dumps([c.returncode for c in procs]))
