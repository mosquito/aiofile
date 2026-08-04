#!/usr/bin/env python3
"""
aiofile benchmark worker -- one participant (a library, and for aiofile
one caio backend) per process invocation.

Launched by bench_runner.py, which sets CAIO_IMPL in this process's
environment *before* it starts (a real subprocess, not fork/spawn), so
the imports below see the right backend from the very first line -- no
deferred imports, no fork-safety questions. Engine architecture (sliding
window, fixed op count, sampled latency) ported from
../caio/benchmark/bench.py.

Sliding-window engine: exactly --concurrency-levels[i] ops in flight at
once, a new one spawned as soon as any completes, run for a fixed op
count (--ops) rather than a fixed duration -- a cell is always bounded
and never partial/cancelled mid-op. Only a sample of ops (SAMPLE_RATE)
are individually timed for latency; throughput always comes from wall
time over the full op count.

Pure throughput/latency measurement -- no content verification here
(that's stability/soak.py's job, on a different workload; hashing every
block would tax the very thing being measured). Each op just checks the
byte count the I/O call reports.

O_DIRECT is exercised for stdlib only (see the multi-library-throughput
PR description for why aiofile is excluded). A block size that isn't a
multiple of 4096 just skips the O_DIRECT case for that size.
"""
import argparse
import asyncio
import itertools
import logging
import mmap
import os
import random
import sys
import tempfile
import time
from pathlib import Path

import aiofiles
import aiomisc.io as aiomisc_io

from aiofile import AIOFile

log = logging.getLogger("bench")

DIRECT_ALIGN = 4096
SAMPLE_RATE = 0.1

# CAIO_IMPL value -> the module name prefix caio actually resolves it to.
CAIO_BACKENDS = {
    "uring": "linux_uring",
    "linux": "linux_aio",
    "thread": "thread_aio",
    "python": "python_aio",
}

COLUMNS = (
    "library", "backend", "mode", "block_size", "pattern", "op",
    "concurrency", "latency_us", "wall_s", "n_ops", "mib_s",
)


def pct(data, p):
    s = sorted(data)
    return s[min(int(len(s) * p), len(s) - 1)]


def aligned_buffer(size):
    buf = mmap.mmap(-1, size)
    buf[:] = os.urandom(size)
    return buf


def populate_file(path, cells, block_size, buf):
    fd = os.open(str(path), os.O_RDWR)
    try:
        for cell in range(cells):
            n = os.pwrite(fd, buf, cell * block_size)
            if n != block_size:
                raise RuntimeError(
                    f"populate: short write {n} != {block_size} "
                    f"at cell {cell}",
                )
        os.fsync(fd)
    finally:
        os.close(fd)


def make_offsets(cells, block_size, pattern):
    linear = list(range(0, cells * block_size, block_size))
    if pattern == "linear":
        return linear
    random_order = linear[:]
    random.Random(0).shuffle(random_order)
    return random_order


# -- sessions: one already-open handle, write_at/read_at must be safe to
# -- call concurrently from multiple in-flight ops against it -----------

class Session:
    def __init__(self, path, direct):
        self.path = path
        self.direct = direct

    async def write_at(self, offset, buffer):
        raise NotImplementedError

    async def read_at(self, offset, buffer, size):
        raise NotImplementedError


class StdlibSession(Session):
    """pwritev/preadv into a caller-owned buffer: correct under O_DIRECT
    and safe to call concurrently from multiple in-flight ops against
    one fd, since neither touches the fd's file position."""

    async def __aenter__(self):
        flags = os.O_RDWR | os.O_CREAT
        if self.direct:
            flags |= os.O_DIRECT
        self.fd = os.open(str(self.path), flags, 0o644)
        return self

    async def __aexit__(self, *exc):
        os.close(self.fd)

    async def write_at(self, offset, buffer):
        return await asyncio.to_thread(os.pwritev, self.fd, [buffer], offset)

    async def read_at(self, offset, buffer, size):
        return await asyncio.to_thread(os.preadv, self.fd, [buffer], offset)


class AIOFileSession(Session):
    """Buffered only. read_bytes/write_bytes take an explicit offset and
    AIOFile keeps no internal cursor, so one instance is safe to hit
    concurrently with no locking of our own."""

    async def __aenter__(self):
        self.afp = AIOFile(self.path, "rb+")
        await self.afp.open()
        return self

    async def __aexit__(self, *exc):
        await self.afp.close()

    async def write_at(self, offset, buffer):
        return await self.afp.write_bytes(buffer, offset)

    async def read_at(self, offset, buffer, size):
        return len(await self.afp.read_bytes(size, offset))


class AiofilesSession(Session):
    """One seek cursor per handle -- concurrent access has to be
    serialized by hand, there's no positional read/write in the API."""

    async def __aenter__(self):
        self.lock = asyncio.Lock()
        self.fp = await aiofiles.open(self.path, "rb+")
        return self

    async def __aexit__(self, *exc):
        await self.fp.close()

    async def write_at(self, offset, buffer):
        async with self.lock:
            await self.fp.seek(offset)
            return await self.fp.write(buffer)

    async def read_at(self, offset, buffer, size):
        async with self.lock:
            await self.fp.seek(offset)
            return len(await self.fp.read(size))


class AiomiscSession(Session):
    """Same limitation as aiofiles: one seek cursor per handle."""

    async def __aenter__(self):
        self.lock = asyncio.Lock()
        self.fp = aiomisc_io.async_open(self.path, "rb+")
        await self.fp.open()
        return self

    async def __aexit__(self, *exc):
        await self.fp.close()

    async def write_at(self, offset, buffer):
        async with self.lock:
            await self.fp.seek(offset)
            return await self.fp.write(buffer)

    async def read_at(self, offset, buffer, size):
        async with self.lock:
            await self.fp.seek(offset)
            return len(await self.fp.read(size))


def make_session(library, path, direct):
    if library == "stdlib":
        return StdlibSession(path, direct)
    if library == "aiofile":
        return AIOFileSession(path, direct=False)
    if library == "aiofiles":
        return AiofilesSession(path, direct=False)
    if library == "aiomisc":
        return AiomiscSession(path, direct=False)
    raise ValueError(library)


# -- engine: sliding window, fixed op count, sampled latency -- ported
# -- from ../caio/benchmark/bench.py's run() -----------------------------

async def run(session, concurrency, n, block_size, offsets, write, warmup):
    """Run exactly n reads or writes through `session`, `concurrency` in
    flight at most. Returns (sampled_latencies_in_seconds, wall_seconds).
    """
    offsets_cycle = itertools.cycle(offsets)
    free = [aligned_buffer(block_size) for _ in range(concurrency)]

    async def do_op(buf, offset, timed):
        t0 = time.perf_counter() if timed else 0.0
        if write:
            n_done = await session.write_at(offset, buf)
        else:
            n_done = await session.read_at(offset, buf, block_size)
        if n_done != block_size:
            kind = "write" if write else "read"
            raise RuntimeError(
                f"short {kind}: {n_done} != {block_size} at offset {offset}",
            )
        return buf, (time.perf_counter() - t0) if timed else None

    async def drain(pending, sampled, collect):
        done, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            buf, lat = task.result()
            free.append(buf)
            if collect is not None and task in sampled:
                collect.append(lat)
                sampled.discard(task)
        return pending

    if warmup:
        pending = set()
        left = warmup
        while left > 0 or pending:
            while free and left > 0:
                buf = free.pop()
                offset = next(offsets_cycle)
                pending.add(asyncio.create_task(do_op(buf, offset, False)))
                left -= 1
            if pending:
                pending = await drain(pending, set(), None)

    sample_mask = bytearray(
        1 if random.random() < SAMPLE_RATE else 0 for _ in range(n)
    )
    if n:
        sample_mask[0] = 1  # guarantee at least one latency sample

    latencies = []
    pending = set()
    sampled = set()
    idx = 0
    t0 = time.perf_counter()
    while idx < n or pending:
        while free and idx < n:
            buf = free.pop()
            offset = next(offsets_cycle)
            timed = bool(sample_mask[idx])
            task = asyncio.create_task(do_op(buf, offset, timed))
            if timed:
                sampled.add(task)
            pending.add(task)
            idx += 1
        if pending:
            pending = await drain(pending, sampled, latencies)
    wall = time.perf_counter() - t0

    return latencies, wall


# -- sweep -----------------------------------------------------------------

async def sweep(library, backend, args, out_fp):
    backend_label = backend or "n/a"
    if library == "aiofile":
        from aiofile.aio import get_default_context
        ctx = get_default_context()
        actual = type(ctx).__module__.rsplit(".", 1)[-1]
        actual = actual.removesuffix("_asyncio")
        if actual != CAIO_BACKENDS.get(backend):
            # Refuse to silently measure a fallback backend under the
            # requested backend's name -- that mislabels every row.
            raise RuntimeError(
                f"requested backend={backend!r} but caio resolved "
                f"{actual!r} instead (unavailable on this host?)",
            )
        backend_label = actual

    direct_capable = library == "stdlib"
    has_direct = hasattr(os, "O_DIRECT")

    for block_size in args.block_sizes:
        cells = args.file_size // block_size
        direct_ok = (
            direct_capable and has_direct and block_size % DIRECT_ALIGN == 0
        )
        modes = [False, True] if direct_ok else [False]

        for direct in modes:
            mode_label = "direct" if direct else "buffered"
            fd, tmp_name = tempfile.mkstemp(dir=args.dir, prefix="bench-")
            os.ftruncate(fd, cells * block_size)
            os.close(fd)
            path = Path(tmp_name)
            pbuf = aligned_buffer(block_size)
            try:
                populate_file(path, cells, block_size, pbuf)
                for pattern in ("linear", "random"):
                    offsets = make_offsets(cells, block_size, pattern)
                    for op, write in (("write", True), ("read", False)):
                        for conc in args.concurrency_levels:
                            eff_conc = min(conc, cells)
                            session_cm = make_session(library, path, direct)
                            async with session_cm as session:
                                lats, wall = await run(
                                    session, eff_conc, args.ops,
                                    block_size, offsets, write,
                                    args.warmup_ops,
                                )
                            mib_s = (
                                (args.ops * block_size / (1024 * 1024))
                                / wall
                            )
                            log.info(
                                "%s/%s %s bs=%d %s %s x%d: %.0f ops/s "
                                "%.1f MiB/s (%d samples)",
                                library, backend_label, mode_label,
                                block_size, pattern, op, eff_conc,
                                args.ops / wall, mib_s, len(lats),
                            )
                            for lat in lats:
                                row = (
                                    library, backend_label, mode_label,
                                    block_size, pattern, op, eff_conc,
                                    f"{lat * 1e6:.1f}", f"{wall:.4f}",
                                    args.ops, f"{mib_s:.3f}",
                                )
                                print(
                                    "\t".join(str(v) for v in row),
                                    file=out_fp, flush=True,
                                )
            finally:
                path.unlink(missing_ok=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--library", required=True,
        choices=("stdlib", "aiofile", "aiofiles", "aiomisc"),
    )
    parser.add_argument(
        "--backend", default=None, choices=tuple(CAIO_BACKENDS),
        help="caio backend -- aiofile only",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--file-size", type=int, default=16 * 1024 * 1024)
    parser.add_argument(
        "--block-sizes", type=int, nargs="+", default=[4096],
        metavar="BYTES", help="one or more block sizes (default: 4096)",
    )
    parser.add_argument(
        "--concurrency-levels", type=int, nargs="+", default=[1, 8],
        metavar="N", help="one or more concurrency levels (default: 1 8)",
    )
    parser.add_argument(
        "--ops", type=int, default=1000,
        help="timed ops per cell (default: 1000)",
    )
    parser.add_argument(
        "--warmup-ops", type=int, default=100,
        help="untimed ops before each cell (default: 100)",
    )
    parser.add_argument("--dir", type=Path, default=Path("./bench-data"))
    args = parser.parse_args()

    if args.library == "aiofile" and not args.backend:
        parser.error("--backend is required when --library aiofile")
    if args.library != "aiofile" and args.backend:
        parser.error("--backend only applies to --library aiofile")
    if min(args.file_size, args.ops) <= 0 or args.warmup_ops < 0:
        parser.error("--file-size/--ops must be positive, --warmup-ops >= 0")
    for bs in args.block_sizes:
        if bs <= 0:
            parser.error("--block-sizes entries must be positive")
        if args.file_size % bs:
            parser.error(f"--file-size must be a multiple of {bs}")
    for conc in args.concurrency_levels:
        if conc <= 0:
            parser.error("--concurrency-levels entries must be positive")

    return args


async def main_async():
    args = parse_args()
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO,
        format="%(asctime)s %(message)s",
    )
    args.dir.mkdir(exist_ok=True, parents=True)
    args.out.parent.mkdir(exist_ok=True, parents=True)

    with args.out.open("w") as out_fp:
        print("\t".join(COLUMNS), file=out_fp)
        await sweep(args.library, args.backend, args, out_fp)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
