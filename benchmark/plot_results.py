"""
Plot aiofile benchmark results (bench_runner.py's merged TSV).

Usage:
  uv run --with matplotlib --with numpy --with pillow python plot_results.py \
      [results.tsv]

Defaults to results/aiofile-bench-results.tsv next to this script. Produces
one aiofile-bench-report-bs<N>.png per distinct block_size found in the
TSV, in the same directory as the input TSV -- different block sizes are
different workloads, never averaged together into one chart.
"""
import csv
import pathlib
import sys
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
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
SPEEDUP_UP_COLOR = "#2ecc71"
SPEEDUP_DOWN_COLOR = "#e74c3c"


def pct(data, p):
    s = sorted(data)
    return s[min(int(len(s) * p), len(s) - 1)]


def load_rows(path):
    with path.open(newline="") as fp:
        return list(csv.DictReader(fp, delimiter="\t"))


def group_by_cell(rows):
    """(library, backend, mode, block_size, pattern, op, concurrency) ->
    {"mib_s": float, "latencies_ms": [float, ...]}. block_size is part of
    the key on purpose -- different block sizes are different workloads,
    never averaged. mib_s is one wall-clock-derived value per cell (the
    TSV repeats it per sampled-latency row); latencies_ms holds every
    individually-timed op for that cell."""
    cells = defaultdict(lambda: {"mib_s": None, "latencies_ms": []})
    for r in rows:
        key = (
            r["library"], r["backend"], r["mode"], int(r["block_size"]),
            r["pattern"], r["op"], int(r["concurrency"]),
        )
        cell = cells[key]
        cell["mib_s"] = float(r["mib_s"])
        cell["latencies_ms"].append(float(r["latency_us"]) / 1000.0)
    return cells


def apply_style(ax, ylabel, title):
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def footer(ax, lines):
    """Compact aggregate summary below a subplot, caio-plot_results style."""
    ax.text(
        0.5, -0.30, "\n".join(lines), transform=ax.transAxes,
        ha="center", va="top", fontsize=6.5, color="#555555",
        linespacing=1.35,
    )


def conc_legend(ax, conc_levels):
    handles = [
        Patch(facecolor=CONC_COLORS.get(c, "#7f8c8d"), label=f"x{c}")
        for c in conc_levels
    ]
    ax.legend(
        handles=handles, fontsize=8, framealpha=0.7, title="concurrency",
        title_fontsize=8,
    )


def plot_throughput_grid(rows, block_size, out_path):
    cells = group_by_cell(rows)
    conc_levels = sorted({
        int(r["concurrency"]) for r in rows
        if int(r["block_size"]) == block_size
    })
    ops = ["write", "read"]
    patterns = ["linear", "random"]

    fig, axes = plt.subplots(
        len(ops), len(patterns),
        figsize=(6.5 * len(patterns), 4.6 * len(ops)),
        squeeze=False,
    )
    x = np.arange(len(PARTICIPANTS))
    bar_w = 0.8 / max(len(conc_levels), 1)

    for row_i, op in enumerate(ops):
        for col_i, pattern in enumerate(patterns):
            ax = axes[row_i][col_i]
            footer_lines = []
            for ci, conc in enumerate(conc_levels):
                xs, heights, lat_pool = [], [], []
                for i, p in enumerate(PARTICIPANTS):
                    key = (p[0], p[1], p[2], block_size, pattern, op, conc)
                    cell = cells.get(key)
                    if cell is None:
                        continue
                    xs.append(i)
                    heights.append(cell["mib_s"])
                    lat_pool.extend(cell["latencies_ms"])
                if not xs:
                    continue
                offset = (ci - (len(conc_levels) - 1) / 2) * bar_w
                ax.bar(
                    np.array(xs) + offset, heights, width=bar_w * 0.9,
                    color=CONC_COLORS.get(conc, "#7f8c8d"),
                )
                footer_lines.append(
                    f"x{conc}: latency p50 {pct(lat_pool, 0.50):.3f}ms  "
                    f"p95 {pct(lat_pool, 0.95):.3f}ms  p99 "
                    f"{pct(lat_pool, 0.99):.3f}ms (all participants, "
                    f"n={len(lat_pool)})",
                )

            ax.set_yscale("log")
            apply_style(ax, "MiB/s (log)", f"{op} - {pattern}")
            ax.set_xticks(x)
            ax.set_xticklabels([p[3] for p in PARTICIPANTS], fontsize=7.5)
            if conc_levels:
                conc_legend(ax, conc_levels)
            if footer_lines:
                footer(ax, footer_lines)

    fig.suptitle(
        f"aiofile benchmark -- throughput by participant (block_size="
        f"{block_size}B)",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.96), h_pad=3.5)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out_path}")


def plot_concurrency_speedup(rows, block_size, out_path):
    cells = group_by_cell(rows)
    conc_levels = sorted({
        int(r["concurrency"]) for r in rows
        if int(r["block_size"]) == block_size
    })
    if len(conc_levels) < 2:
        print(
            f"skipped speedup chart for block_size={block_size}: only one "
            f"concurrency level ({conc_levels}) in the data",
        )
        return False
    lo_conc, hi_conc = conc_levels[0], conc_levels[-1]

    fig, ax = plt.subplots(figsize=(9, 4.6))
    x = np.arange(len(PARTICIPANTS))
    speedups, present = [], []
    for i, p in enumerate(PARTICIPANTS):
        lo_key = (p[0], p[1], p[2], block_size, "linear", "write", lo_conc)
        hi_key = (p[0], p[1], p[2], block_size, "linear", "write", hi_conc)
        lo_cell, hi_cell = cells.get(lo_key), cells.get(hi_key)
        if lo_cell is None or hi_cell is None:
            continue
        lo_mib = lo_cell["mib_s"]
        speedups.append(hi_cell["mib_s"] / lo_mib if lo_mib else 0)
        present.append(i)

    colors = [
        SPEEDUP_UP_COLOR if s >= 1 else SPEEDUP_DOWN_COLOR for s in speedups
    ]
    ax.bar(present, speedups, color=colors, width=0.6)
    ax.axhline(1.0, color="#555555", linewidth=1, linestyle="-")
    apply_style(
        ax, f"x{hi_conc} / x1 throughput ratio",
        f"Concurrency speedup (linear write, block_size={block_size}B, "
        f"x{lo_conc} -> x{hi_conc})",
    )
    ax.set_xticks(x)
    ax.set_xticklabels([p[3] for p in PARTICIPANTS], fontsize=7.5)
    ax.legend(
        handles=[
            Patch(facecolor=SPEEDUP_UP_COLOR, label=f"x{hi_conc} >= x1"),
            Patch(facecolor=SPEEDUP_DOWN_COLOR, label=f"x{hi_conc} < x1"),
            Line2D(
                [0], [0], color="#555555", linewidth=1,
                label="no speedup (ratio = 1)",
            ),
        ],
        fontsize=8, framealpha=0.7,
    )

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out_path}")
    return True


def combine_vertically(top_path, bottom_path, out_path):
    with Image.open(top_path) as top, Image.open(bottom_path) as bottom:
        width = max(top.width, bottom.width)
        height = top.height + bottom.height
        combined = Image.new("RGB", (width, height), "white")
        combined.paste(top.convert("RGB"), (0, 0))
        combined.paste(bottom.convert("RGB"), (0, top.height))
        combined.save(out_path)
    print(f"combined {out_path}")


def render_block_size(rows, block_size, out_dir):
    throughput_png = out_dir / f"throughput-bs{block_size}.png"
    speedup_png = out_dir / f"speedup-bs{block_size}.png"
    report_png = out_dir / f"aiofile-bench-report-bs{block_size}.png"

    plot_throughput_grid(rows, block_size, throughput_png)
    has_speedup = plot_concurrency_speedup(rows, block_size, speedup_png)

    if has_speedup:
        combine_vertically(throughput_png, speedup_png, report_png)
        speedup_png.unlink()
        throughput_png.unlink()
    else:
        throughput_png.replace(report_png)

    print(f"report: {report_png.resolve()}")


def main():
    tsv_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TSV
    if not tsv_path.exists():
        raise SystemExit(f"Benchmark TSV not found: {tsv_path}")

    rows = load_rows(tsv_path)
    out_dir = tsv_path.parent
    block_sizes = sorted({int(r["block_size"]) for r in rows})
    if not block_sizes:
        raise SystemExit("no rows in TSV")

    for block_size in block_sizes:
        render_block_size(rows, block_size, out_dir)


if __name__ == "__main__":
    main()
