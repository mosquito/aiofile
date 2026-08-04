#!/usr/bin/env python3
"""
Run each aiofile benchmark participant (stdlib, aiofile x every caio
backend, aiofiles, aiomisc) in its own subprocess, one at a time, then
merge their TSVs into one.

Ported from ../caio/benchmark/bench_runner.py. A real subprocess (not
fork/multiprocessing) so CAIO_IMPL set here, in this process's own
environment, is what the child's interpreter sees from its very first
import -- no deferred-import tricks needed in bench.py.

Usage:
  uv run python bench_runner.py [-- bench.py args, e.g. --ops 2000]
"""
import csv
import os
import pathlib
import subprocess
import sys

RESULTS_DIR = pathlib.Path(
    os.environ.get("AIOFILE_BENCH_RESULTS", "results"),
)
BENCH = pathlib.Path(__file__).parent / "bench.py"

CAIO_BACKENDS = ("uring", "linux", "thread", "python")

COLUMNS = (
    "library", "backend", "mode", "block_size", "pattern", "op",
    "concurrency", "latency_us", "wall_s", "n_ops", "mib_s",
)


def build_participants():
    participants = [("stdlib", None)]
    participants += [("aiofile", backend) for backend in CAIO_BACKENDS]
    participants += [("aiofiles", None), ("aiomisc", None)]
    return participants


def run_participant(library, backend, extra_args, out_dir):
    env = os.environ.copy()
    if backend:
        env["CAIO_IMPL"] = backend
    else:
        env.pop("CAIO_IMPL", None)

    out_path = out_dir / f"bench_{library}_{backend or 'na'}.tsv"
    title = f"participant: library={library} backend={backend or 'n/a'}"
    print(f"\n{'=' * len(title)}\n{title}\n{'=' * len(title)}", flush=True)

    proc = subprocess.run(
        [
            sys.executable, str(BENCH),
            "--library", library,
            *(["--backend", backend] if backend else []),
            "--out", str(out_path),
            *extra_args,
        ],
        env=env, cwd=BENCH.parent,
    )
    if proc.returncode != 0:
        sys.exit(
            f"participant library={library} backend={backend or 'n/a'} "
            f"exited with code {proc.returncode} -- aborting",
        )
    return out_path


def merge_results(paths, out_path):
    rows = []
    for path in paths:
        if not path.exists():
            sys.exit(f"missing expected output: {path}")
        with path.open(newline="") as fp:
            reader = csv.DictReader(fp, delimiter="\t")
            if tuple(reader.fieldnames or ()) != COLUMNS:
                sys.exit(
                    f"{path}: incompatible TSV header "
                    f"{reader.fieldnames!r}; expected {COLUMNS!r}",
                )
            rows.extend(reader)

    with out_path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nmerged {len(rows)} rows -> {out_path}")


def main():
    extra_args = sys.argv[1:]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    paths = [
        run_participant(library, backend, extra_args, RESULTS_DIR)
        for library, backend in build_participants()
    ]
    merge_results(paths, RESULTS_DIR / "aiofile-bench-results.tsv")


if __name__ == "__main__":
    main()
