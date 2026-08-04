# aiofile benchmark

Compares `aiofile` (once per `caio` backend) against the stdlib, `aiofiles`
and `aiomisc.io`. **Linux only.**

This is a separate uv project on purpose, so its dependencies don't leak
into the main package. Architecture ported from `../caio/benchmark/`
(`bench.py` + `bench_runner.py`), adapted to compare libraries built on
top of `caio` rather than `caio`'s own backends directly.

## What it measures

- **library**: stdlib, `aiofile`, `aiofiles`, `aiomisc.io`
- **backend** (`aiofile` only): `uring`/`linux`/`thread`/`python`
  (`CAIO_IMPL` values) -- whichever caio actually resolves each to is what
  gets written to the TSV, never the requested name
- **mode**: buffered, or `O_DIRECT`
- **block size**: one or more, via `--block-sizes` (space-separated)
- **pattern**: linear or random block order
- **op**: read or write
- **concurrency**: one or more levels, via `--concurrency-levels` -- all
  against the *same* open handle, not one handle each

Each *(participant, mode, block size)* gets one freshly created,
correctly sized file, populated once, then every pattern/op/concurrency
cell in that group reuses it.

Every cell runs a **fixed op count** (`--ops`, plus untimed `--warmup-ops`
first), never a fixed duration -- a cell always completes in full, never
gets cancelled partway through, and its result is never missing or
padded. Total run time is bounded by `--ops`, not by how slow a
participant happens to be.

`bench.py` runs one participant. It's a **sliding-window** engine (ported
from `../caio/benchmark/bench.py`): exactly `concurrency` ops in flight
at any time, a new one spawned the instant any completes -- no static
per-worker chunking, no idle workers waiting on a slower peer. Only a
sample of ops (10%) are individually timed for latency; throughput always
comes from wall time over the full op count.

`bench_runner.py` runs every participant, each **in its own subprocess**,
strictly one at a time (ported from `../caio/benchmark/bench_runner.py`).
A real subprocess, not fork/multiprocessing, so `CAIO_IMPL` -- set in
this process's own environment right before it starts the child -- is
what the child interpreter's very first `import caio` sees; no
deferred-import tricks needed in `bench.py`. If the requested backend
isn't actually available, `bench.py` raises rather than silently
measuring caio's fallback under the requested name, and the runner aborts
the whole run on the first non-zero exit code rather than merging a TSV
with a silent gap in it.

`O_DIRECT` is exercised for stdlib only, via `os.pwritev`/`os.preadv` into
a caller-owned page-aligned buffer, so alignment is preserved end to end.
`aiofile` is excluded from the `O_DIRECT` cases: `AIOFile.read_bytes` has
`caio` allocate the destination buffer with no alignment guarantee, and
there is no public API to supply one instead (see aiofile issue #100).
`aiofiles`/`aiomisc.io` open files through the stdlib `open()`, which has
no way to request `O_DIRECT` at all. A block size that isn't a multiple of
4096 just skips the `O_DIRECT` case for that size.

This tool does not verify content -- only that every read/write moved the
expected number of bytes. Hashing every block would tax the very thing
being measured. Correctness (including concurrent access to one handle)
is `stability/soak.py`'s job, on a different workload.

The timed metric is operation-completion time, not durable-write time: it
stops as soon as the read/write call returns, before the session's
`close()` (which for a writable `AIOFile` runs `fdsync`, and for a
buffered Python file object may flush on close).

## Running

```sh
cd benchmark
uv run python bench_runner.py
```

Everything after `bench_runner.py` is forwarded to every `bench.py`
invocation, e.g. `uv run python bench_runner.py --ops 5000 --block-sizes
4096 65536`. Flags (see `bench.py --help`): `--file-size`, `--block-sizes`
(one or more), `--concurrency-levels` (one or more, default `1 8`),
`--ops` (default 1000), `--warmup-ops` (default 100), `--dir`.

Output goes to `results/bench_<library>_<backend>.tsv` per participant,
merged into `results/aiofile-bench-results.tsv` at the end (set
`AIOFILE_BENCH_RESULTS` to change the output directory). Progress goes to
stdout/stderr as each participant runs; nothing is buffered until the end.

## Output

One row per **sampled** op (about 10% of `--ops`, at least one per cell)
-- `wall_s`/`n_ops`/`mib_s` are the whole cell's numbers and repeat across
every row for that cell, `latency_us` is that one op's own latency.
Compute mean/percentiles/whatever downstream from the raw `latency_us`
values; this script doesn't rank or aggregate anything itself.

| column | meaning |
|---|---|
| `library` | `stdlib`, `aiofile`, `aiofiles`, `aiomisc` |
| `backend` | resolved `caio` backend for `aiofile` rows, `n/a` otherwise |
| `mode` | `buffered` or `direct` |
| `block_size` | bytes per read/write op |
| `pattern` | `linear` or `random` block order |
| `op` | `write` or `read` |
| `concurrency` | in-flight ops for this cell |
| `latency_us` | this sampled op's own latency, in microseconds |
| `wall_s` | wall-clock time for the whole cell (`--ops` ops, `concurrency` in flight) |
| `n_ops` | `--ops` -- total ops in this cell (not just the sampled ones) |
| `mib_s` | `n_ops * block_size` / `wall_s`, in MiB/s |

## Reading the results

- `stdlib` and `aiofile` speed up under concurrency (`x1` vs `xN`)
  because both use true positional I/O (`os.pwritev`/`preadv`,
  `read_bytes`/`write_bytes` with an explicit offset) against one shared
  handle with no cursor to fight over. This is a measured outcome, not a
  guarantee -- it depends on the `caio` backend, executor/pool limits,
  filesystem, and workload size.
- `aiofiles`/`aiomisc.io` don't: their handles carry a single seek
  cursor, so concurrent access to the *same* handle has to be serialized
  with a lock in this script -- there's no positional API to use instead.
  Opening a separate handle per worker would sidestep that, but then
  you're no longer measuring access to the same open file.
- Don't average across `aiofile` backends into one "aiofile speed".
  `linux_uring` vs `linux_aio` compares two native kernel submission
  mechanisms; `thread_aio` vs `python_aio` compares two blocking-thread
  adapters; native vs thread-based compares different execution models
  entirely. Compare backends pairwise for a specific question (e.g. "which
  minimizes per-op overhead at this block size"), not as one ranking.
- Never take an explanation of *why* one backend beats another (eager vs
  batched submission, SQPOLL, etc.) on faith from a single run's numbers
  -- confirm the actual negotiated state (e.g. `context.sqpoll`) before
  asserting a cause, and design a paired experiment that holds everything
  else fixed. It's easy to be confidently wrong here.

None of this is a universal ranking. Results depend on the OS, filesystem,
storage device, and kernel. If you're benchmarking the `caio` backends
themselves rather than the libraries built on top, see `caio`'s own
`benchmark/` directory.

## Plotting

`plot_results.py` turns a merged TSV into one PNG report per distinct
block size found in it (throughput bars per op/pattern/concurrency, a
p50/p95/p99 latency footer under each, plus a concurrency-speedup chart)
-- different block sizes are different workloads, never averaged into one
chart:

```sh
uv run --with matplotlib --with numpy --with pillow python plot_results.py results/aiofile-bench-results.tsv
```
