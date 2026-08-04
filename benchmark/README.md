# aiofile benchmark

Compares `aiofile` (across every `caio` backend available on this platform)
against the stdlib, `aiofiles` and `aiomisc.io`. **Linux only.**

This is a separate uv project on purpose, so its dependencies don't leak
into the main package.

## What it measures

Every axis combination gets its own freshly created, correctly sized temp
file inside `--dir` -- never a fixed path, never reused across passes --
so results aren't polluted by page-cache state or writeback left behind
by an earlier library/mode/pattern/round:

- **library**: stdlib, `aiofile`, `aiofiles`, `aiomisc.io`
- **backend** (`aiofile` only): every entry in `caio.variants_asyncio` on
  this platform (e.g. `linux_uring`, `linux_aio`, `thread_aio`,
  `python_aio`), each via an explicitly constructed `AsyncioContext`
  passed as `AIOFile(..., context=...)` -- not the `CAIO_IMPL` environment
  variable, so every backend runs in one process, one invocation
- **mode**: buffered, or `O_DIRECT`
- **pattern**: linear or random block order
- **op**: read or write
- **concurrency**: one worker, or `--concurrency` workers -- all against
  the *same* open handle, not one handle each

`O_DIRECT` is exercised for stdlib only, via `os.pwritev`/`os.preadv` into
a caller-owned page-aligned buffer, so alignment is preserved end to end.
`aiofile` is excluded from the `O_DIRECT` cases: `AIOFile.read_bytes` has
`caio` allocate the destination buffer with no alignment guarantee, and
there is no public API to supply one instead (see aiofile issue #100).
`aiofiles`/`aiomisc.io` open files through the stdlib `open()`, which has
no way to request `O_DIRECT` at all.

Every read and write is content-verified: the first 8 bytes of each block
are tagged with its offset on write and checked on read, *after* the
timed region stops so verification cost isn't counted as I/O time. A
mismatch or short read/write aborts the run with a traceback instead of
silently producing a bad number.

The timed metric is operation-completion time, not durable-write time: it
stops as soon as the read/write calls return, before the session's
`close()` (which for a writable `AIOFile` runs `fdsync`, and for a
buffered Python file object may flush on close). Two libraries closing
differently isn't reflected in the numbers.

## Running

```sh
cd benchmark
uv run python bench.py > results.tsv
```

stdout is *only* the TSV header and data rows -- nothing else, so it's
safe to redirect straight to a file. Progress and environment info
(Python/kernel version, which `caio` backends were found, effective
concurrency) go to stderr via `logging`, not stdout.

Flags (see `--help`): `--file-size`, `--block-size` (must be a multiple
of 4096, the `O_DIRECT` alignment this script assumes), `--concurrency`,
`--rounds`, `--dir`, `--max-requests` and `--deferred` (passed to every
`caio` `AsyncioContext`).

## Output

One raw row per `(library, backend, mode, pattern, op, concurrency,
round)` -- every round kept separate, nothing averaged or min'd. Compute
mean/stdev/quantiles/whatever downstream from the TSV; this script
doesn't rank or aggregate anything itself.

| column | meaning |
|---|---|
| `library` | `stdlib`, `aiofile`, `aiofiles`, `aiomisc` |
| `backend` | `caio` backend name for `aiofile` rows, `n/a` otherwise |
| `mode` | `buffered` or `direct` |
| `pattern` | `linear` or `random` block order |
| `op` | `write` or `read` |
| `concurrency` | worker count for this pass |
| `round` | 0-based round index |
| `file_size`, `block_size` | bytes |
| `seconds` | wall-clock time for the whole pass (all workers, all blocks) |
| `mib_per_sec` | `file_size` / `seconds`, in MiB/s |

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

None of this is a universal ranking. Results depend on the OS, filesystem,
storage device, and kernel. If you're benchmarking the `caio` backends
themselves rather than the libraries built on top, see `caio`'s own
`benchmark/` directory.

## Plotting

`plot_results.py` turns a TSV into a PNG report (grouped throughput bars
per op/pattern, plus a concurrency-speedup chart):

```sh
uv run --with matplotlib --with numpy --with pillow python plot_results.py results.tsv
```
