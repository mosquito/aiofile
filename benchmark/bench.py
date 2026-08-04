"""
Single-file read/write benchmark: stdlib, aiofile (across every available
caio backend), aiofiles, aiomisc.io.

Linux only. Every timed pass gets its own freshly created, correctly
sized temp file inside --dir (never a fixed path, never reused across
passes) so results aren't polluted by page-cache state or writeback left
behind by an earlier library/mode/pattern. Axes: linear vs random block
order, read vs write, buffered vs O_DIRECT, sequential vs concurrent
workers against one shared open handle.

O_DIRECT is exercised for stdlib only, via os.pwritev/os.preadv into a
caller-owned page-aligned buffer -- alignment is preserved end to end.
aiofile is excluded from O_DIRECT: AIOFile.read_bytes has caio allocate
the destination buffer with no alignment guarantee, and there is no
public API to supply one (see aiofile issue #100). aiofiles/aiomisc.io
open files through the stdlib `open()` with no way to pass O_DIRECT at
all.

aiofile is benchmarked once per caio backend available on this platform
(`caio.variants_asyncio`) using an explicitly constructed context per
backend, not the CAIO_IMPL environment variable -- so all backends run
in one process, one script invocation.

Every read/write is content-verified: the first 8 bytes of each block
are tagged with its offset on write and checked on read, after the timed
region so verification cost isn't counted as I/O time. A mismatch aborts
the run with a traceback instead of silently producing a bad number.

This script only writes: a TSV header, then one raw row per (library,
backend, mode, pattern, op, concurrency, round) to stdout -- no
aggregation, no ranking. Compute stdev/quantiles/whatever downstream.
Progress and environment info go to stderr via `logging`.
"""
import argparse
import asyncio
import logging
import mmap
import os
import platform
import random
import sys
import tempfile
import time
from pathlib import Path

import aiofiles
import aiomisc.io as aiomisc_io
import caio

from aiofile import AIOFile

log = logging.getLogger("bench")

DIRECT_ALIGN = 4096


def aligned_buffer(size):
    # mmap'd memory is page-aligned, which O_DIRECT needs; a plain
    # bytes/bytearray has no such guarantee.
    buf = mmap.mmap(-1, size)
    buf[:] = os.urandom(size)
    return buf


def tag(buffer, offset):
    buffer[0:8] = offset.to_bytes(8, "little")


def check_tag(data, offset, block_size, where):
    if len(data) != block_size:
        raise RuntimeError(
            f"{where}: short read {len(data)} != {block_size} "
            f"at offset {offset}",
        )
    got = int.from_bytes(data[:8], "little")
    if got != offset:
        raise RuntimeError(
            f"{where}: content mismatch at offset {offset}: tag={got}",
        )


def populate_file(path, file_size, block_size):
    """Untimed setup for read passes: write a correctly tagged block at
    every offset so the timed read has real, checkable data to see."""
    fd = os.open(str(path), os.O_RDWR)
    try:
        buf = aligned_buffer(block_size)
        for offset in range(0, file_size, block_size):
            tag(buf, offset)
            os.pwrite(fd, buf, offset)
        os.fsync(fd)
    finally:
        os.close(fd)


# -- sessions: one already-open handle to a file, write_at/read_at must
# -- be safe to call concurrently from multiple tasks against it --------

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
    (alignment preserved end to end, unlike pwrite/pread which can't take
    a caller destination buffer for reads) and safe to call concurrently
    from multiple threads against one fd, since neither touches the fd's
    file position."""

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
        n = await asyncio.to_thread(os.preadv, self.fd, [buffer], offset)
        return bytes(buffer[:n])


class AIOFileSession(Session):
    """Buffered only -- see module docstring for why aiofile is excluded
    from the O_DIRECT cases. read_bytes/write_bytes take an explicit
    offset and AIOFile keeps no internal cursor, so the same instance can
    be hit concurrently with no locking of our own."""

    def __init__(self, path, context):
        super().__init__(path, direct=False)
        self.context = context

    async def __aenter__(self):
        self.afp = AIOFile(self.path, "rb+", context=self.context)
        await self.afp.open()
        return self

    async def __aexit__(self, *exc):
        await self.afp.close()

    async def write_at(self, offset, buffer):
        return await self.afp.write_bytes(buffer, offset)

    async def read_at(self, offset, buffer, size):
        return await self.afp.read_bytes(size, offset)


class AiofilesSession(Session):
    """aiofiles wraps one stdlib file object with one seek cursor, so
    concurrent access to a single handle has to be serialized by hand --
    there's no positional read/write in its public API."""

    def __init__(self, path):
        super().__init__(path, direct=False)

    async def __aenter__(self):
        self.fp = await aiofiles.open(self.path, "rb+")
        self.lock = asyncio.Lock()
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
            return await self.fp.read(size)


class AiomiscSession(Session):
    """Same limitation as aiofiles: one seek cursor per handle."""

    def __init__(self, path):
        super().__init__(path, direct=False)

    async def __aenter__(self):
        self.fp = aiomisc_io.async_open(self.path, "rb+")
        await self.fp.open()
        self.lock = asyncio.Lock()
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
            return await self.fp.read(size)


async def run_pass(session_cm, offsets, block_size, write, concurrency):
    async with session_cm as session:
        buffers = [aligned_buffer(block_size) for _ in range(concurrency)]
        chunks = [offsets[i::concurrency] for i in range(concurrency)]

        async def worker(chunk, buffer):
            collected = []
            for offset in chunk:
                if write:
                    tag(buffer, offset)
                    written = await session.write_at(offset, buffer)
                    collected.append((offset, written))
                else:
                    data = await session.read_at(offset, buffer, block_size)
                    collected.append((offset, bytes(data)))
            return collected

        start = time.monotonic()
        worker_results = await asyncio.gather(
            *(worker(chunk, buffer) for chunk, buffer in zip(chunks, buffers)),
        )
        elapsed = time.monotonic() - start

        # Verification happens after the timer stops so it isn't counted
        # as I/O time -- see module docstring.
        for collected in worker_results:
            for offset, value in collected:
                if write:
                    if value != block_size:
                        raise RuntimeError(
                            f"write: short write {value} != {block_size} "
                            f"at offset {offset}",
                        )
                else:
                    check_tag(value, offset, block_size, "read")

        return elapsed


def build_participants(args, backend_contexts):
    participants = [
        (
            "stdlib", "n/a",
            lambda path, direct: StdlibSession(path, direct),
            True,
        ),
    ]
    for name, ctx in backend_contexts.items():
        participants.append((
            "aiofile", name,
            (lambda path, direct, ctx=ctx: AIOFileSession(path, ctx)),
            False,
        ))
    participants.append(
        ("aiofiles", "n/a", lambda path, direct: AiofilesSession(path), False),
    )
    participants.append(
        ("aiomisc", "n/a", lambda path, direct: AiomiscSession(path), False),
    )
    return participants


async def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--file-size", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--block-size", type=int, default=4096)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--dir", type=Path, default=Path("./bench-data"))
    parser.add_argument(
        "--max-requests", type=int, default=None,
        help="caio context max_requests (default: backend default)",
    )
    parser.add_argument(
        "--deferred", action="store_true",
        help="submit aiofile/caio operations in deferred (batched) mode",
    )
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(message)s",
    )

    if min(args.file_size, args.block_size, args.concurrency, args.rounds) <= 0:
        parser.error(
            "--file-size/--block-size/--concurrency/--rounds must be positive",
        )
    if args.file_size % args.block_size:
        parser.error("--file-size must be a multiple of --block-size")
    if args.block_size % DIRECT_ALIGN:
        parser.error(f"--block-size must be a multiple of {DIRECT_ALIGN}")

    args.dir.mkdir(exist_ok=True)

    block_count = args.file_size // args.block_size
    concurrency = min(args.concurrency, block_count)
    if concurrency != args.concurrency:
        log.info(
            "concurrency capped %d -> %d (only %d blocks)",
            args.concurrency, concurrency, block_count,
        )

    linear = list(range(0, args.file_size, args.block_size))
    random_order = linear[:]
    random.Random(0).shuffle(random_order)
    patterns = {"linear": linear, "random": random_order}

    has_direct = hasattr(os, "O_DIRECT")
    if not has_direct:
        log.info("O_DIRECT unavailable (not Linux?), skipping direct cases")

    backend_contexts = {
        module.__name__.rsplit(".", 1)[-1].removesuffix("_asyncio"):
            module.AsyncioContext(
                max_requests=args.max_requests, deferred=args.deferred,
            )
        for module in caio.variants_asyncio
    }
    log.info(
        "env: python=%s system=%s release=%s caio_backends=%s "
        "max_requests=%s deferred=%s",
        platform.python_version(), platform.system(), platform.release(),
        ",".join(backend_contexts) or "none", args.max_requests, args.deferred,
    )

    participants = build_participants(args, backend_contexts)

    columns = (
        "library", "backend", "mode", "pattern", "op", "concurrency",
        "round", "file_size", "block_size", "seconds", "mib_per_sec",
    )
    print("\t".join(columns), flush=True)

    try:
        for entry in participants:
            library, backend_name, make_session, direct_capable = entry
            direct_ok = direct_capable and has_direct
            modes = [False, True] if direct_ok else [False]
            for direct in modes:
                mode_label = "direct" if direct else "buffered"
                for pattern_name, offsets in patterns.items():
                    for op, write in (("write", True), ("read", False)):
                        for pass_concurrency in sorted({1, concurrency}):
                            for round_index in range(args.rounds):
                                fd, tmp_name = tempfile.mkstemp(
                                    dir=args.dir, prefix="bench-",
                                )
                                os.ftruncate(fd, args.file_size)
                                os.close(fd)
                                path = Path(tmp_name)
                                try:
                                    if not write:
                                        await asyncio.to_thread(
                                            populate_file, path,
                                            args.file_size, args.block_size,
                                        )
                                    session_cm = make_session(path, direct)
                                    seconds = await run_pass(
                                        session_cm, offsets, args.block_size,
                                        write, pass_concurrency,
                                    )
                                finally:
                                    path.unlink(missing_ok=True)

                                mib_per_sec = (
                                    (args.file_size / (1024 * 1024)) / seconds
                                )
                                log.info(
                                    "%s/%s %s %s %s x%d round %d: %.3fs",
                                    library, backend_name, mode_label,
                                    pattern_name, op, pass_concurrency,
                                    round_index, seconds,
                                )
                                row = (
                                    library, backend_name, mode_label,
                                    pattern_name, op, pass_concurrency,
                                    round_index, args.file_size,
                                    args.block_size, seconds, mib_per_sec,
                                )
                                print(
                                    "\t".join(str(v) for v in row),
                                    flush=True,
                                )
    finally:
        for ctx in backend_contexts.values():
            ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
