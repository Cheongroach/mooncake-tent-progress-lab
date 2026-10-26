"""Render measured API throughput; deliberately avoid extrapolated NIC utilization."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

p = argparse.ArgumentParser()
p.add_argument("summary", type=Path)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
d = json.loads(a.summary.read_text())
sizes = sorted({r["bytes"] for r in d["cells"]})
fig, axes = plt.subplots(2, len(sizes), figsize=(12, 6), squeeze=False)
for row, operation in enumerate(("write", "read")):
    for col, size in enumerate(sizes):
        ax = axes[row][col]
        for pattern, color, label in (
            ("pair", "#1565c0", "fixed target"),
            ("rotate", "#d65f00", "rotating target"),
        ):
            cells = sorted((r for r in d["cells"] if r["operation"] == operation and r["bytes"] == size and r["pattern"] == pattern), key=lambda r:r["window"])
            ax.plot([str(r["window"]) for r in cells], [r["per_initiator_GBps_median"] for r in cells], marker="o", color=color, label=label)
        size_label = f"{size // (1024 * 1024)} MiB" if size >= 1024 * 1024 else f"{size // 1024} KiB"
        ax.set_title(f"{operation.upper()} / {size_label}")
        ax.set_xlabel("Independent in-flight batches")
        ax.set_ylabel("Per-initiator median GB/s")
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", alpha=.2)
        ax.spines[["top", "right"]].set_visible(False)
axes[0][0].legend(frameon=False)
fig.suptitle(f"TENT RDMA: {d['world']} processes on {d['hosts']} hosts", fontsize=16)
fig.text(.5,.01,"API timing includes Python/ctypes submit, poll and free. Processes share NICs; this is not raw NIC bandwidth.",ha="center",fontsize=9)
fig.tight_layout(rect=(0,.05,1,.94))
fig.savefig(a.output, bbox_inches="tight")
