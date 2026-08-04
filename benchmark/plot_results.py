"""
Plot aiofile benchmark results (bench.py's TSV output).

Usage:
  uv run --with matplotlib --with numpy --with pillow python plot_results.py \
      [results.tsv]

Defaults to results/aiofile-bench-results.tsv next to this script. Produces
aiofile-bench-report.png in the same directory as the input TSV.
"""
import csv
import pathlib
import sys
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

SCRIPT_DIR = pathlib.Path(__file__).parent
DEFAULT_TSV = SCRIPT_DIR / "results" / "aiofile-bench-results.tsv"

# (library, backend, mode, display label)
PARTICIPANTS = [
    ("stdlib", "n/a", "buffered", "stdlib\nbuffered"),
    ("stdlib", "n/a", "direct", "stdlib\nO_DIRECT"),
    ("aiofile", "linux_uring", "buffered", "aiofile\nlinux_uring"),
    ("aiofile", "linux_aio", "buffered", "aiofile\nlinux_aio"),
    ("aiofile", "thread_aio", "buffered", "aiofile\nthread_aio"),
    ("aiofile", "python_aio", "buffered", "aiofile\npython_aio"),
    ("aiofiles", "n/a", "buffered", "aiofiles"),
    ("aiomisc", "n/a", "buffered", "aiomisc"),
]

CONC_COLORS = {1: "#3498db", 8: "#e74c3c", 16: "#2ecc71", 32: "#f39c12"}


def load_rows(path):
    with path.open(newline="") as fp:
        return list(csv.DictReader(fp, delimiter="\t"))


def group_by_mib(rows):
    """(library, backend, mode, pattern, op, concurrency) -> [mib_per_sec, ...]"""
    result = defaultdict(list)
    for r in rows:
        key = (
            r["library"], r["backend"], r["mode"], r["pattern"], r["op"],
            int(r["concurrency"]),
        )
        result[key].append(float(r["mib_per_sec"]))
    return result


def apply_style(ax, ylabel, title):
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def plot_throughput_grid(rows, out_path):
    grouped = group_by_mib(rows)
    conc_levels = sorted({int(r["concurrency"]) for r in rows})
    ops = ["write", "read"]
    patterns = ["linear", "random"]

    fig, axes = plt.subplots(
        len(ops), len(patterns),
        figsize=(6.5 * len(patterns), 4.6 * len(ops)),
    )
    x = np.arange(len(PARTICIPANTS))
    bar_w = 0.8 / len(conc_levels)

    for row_i, op in enumerate(ops):
        for col_i, pattern in enumerate(patterns):
            ax = axes[row_i][col_i]
            for ci, conc in enumerate(conc_levels):
                xs, means, lo, hi = [], [], [], []
                for i, p in enumerate(PARTICIPANTS):
                    key = (p[0], p[1], p[2], pattern, op, conc)
                    if key not in grouped:
                        continue
                    vals = grouped[key]
                    m = float(np.mean(vals))
                    xs.append(i)
                    means.append(m)
                    lo.append(m - min(vals))
                    hi.append(max(vals) - m)
                if not xs:
                    continue
                offset = (ci - (len(conc_levels) - 1) / 2) * bar_w
                ax.bar(
                    np.array(xs) + offset, means, width=bar_w * 0.9,
                    color=CONC_COLORS.get(conc, "#7f8c8d"), label=f"x{conc}",
                    yerr=[lo, hi], capsize=3,
                    error_kw={"linewidth": 1, "alpha": 0.6},
                )

            ax.set_yscale("log")
            apply_style(ax, "MiB/s (log, min-max whiskers)", f"{op} - {pattern}")
            ax.set_xticks(x)
            ax.set_xticklabels([p[3] for p in PARTICIPANTS], fontsize=7.5)
            ax.legend(fontsize=8, framealpha=0.7, title="concurrency")

    fig.suptitle(
        "aiofile benchmark -- throughput by participant",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out_path}")


def plot_concurrency_speedup(rows, out_path):
    grouped = group_by_mib(rows)
    conc_levels = sorted({int(r["concurrency"]) for r in rows})
    if len(conc_levels) < 2:
        return
    lo_conc, hi_conc = conc_levels[0], conc_levels[-1]

    fig, ax = plt.subplots(figsize=(9, 4.6))
    x = np.arange(len(PARTICIPANTS))
    speedups, present = [], []
    for i, p in enumerate(PARTICIPANTS):
        lo_key = (p[0], p[1], p[2], "linear", "write", lo_conc)
        hi_key = (p[0], p[1], p[2], "linear", "write", hi_conc)
        if lo_key not in grouped or hi_key not in grouped:
            continue
        lo_mean = float(np.mean(grouped[lo_key]))
        hi_mean = float(np.mean(grouped[hi_key]))
        speedups.append(hi_mean / lo_mean if lo_mean else 0)
        present.append(i)

    colors = ["#2ecc71" if s >= 1 else "#e74c3c" for s in speedups]
    ax.bar(present, speedups, color=colors, width=0.6)
    ax.axhline(1.0, color="#555555", linewidth=1, linestyle="-")
    apply_style(
        ax, f"x{hi_conc} / x1 throughput ratio",
        f"Concurrency speedup (linear write, x{lo_conc} -> x{hi_conc})",
    )
    ax.set_xticks(x)
    ax.set_xticklabels([p[3] for p in PARTICIPANTS], fontsize=7.5)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out_path}")


def combine_vertically(top_path, bottom_path, out_path):
    with Image.open(top_path) as top, Image.open(bottom_path) as bottom:
        width = max(top.width, bottom.width)
        height = top.height + bottom.height
        combined = Image.new("RGB", (width, height), "white")
        combined.paste(top.convert("RGB"), (0, 0))
        combined.paste(bottom.convert("RGB"), (0, top.height))
        combined.save(out_path)
    print(f"combined {out_path}")


def main():
    tsv_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TSV
    if not tsv_path.exists():
        raise SystemExit(f"Benchmark TSV not found: {tsv_path}")

    rows = load_rows(tsv_path)
    out_dir = tsv_path.parent
    throughput_png = out_dir / "throughput_by_participant.png"
    speedup_png = out_dir / "concurrency_speedup.png"
    report_png = out_dir / "aiofile-bench-report.png"

    plot_throughput_grid(rows, throughput_png)
    plot_concurrency_speedup(rows, speedup_png)
    combine_vertically(throughput_png, speedup_png, report_png)
    throughput_png.unlink()
    speedup_png.unlink()

    print(f"\nReport saved to {report_png.resolve()}")


if __name__ == "__main__":
    main()
