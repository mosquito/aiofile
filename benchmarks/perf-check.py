# https://github.com/mosquito/aiofile/issues/18
import asyncio
import time
import random
import os
import sys
from uuid import uuid4

from aiofile import AIOFile, LineReader, Writer
from aiofiles import open as aio_open


_files = 10
_iters = 10 ** 4
_rand_max = 10


def read_sync(fname):
    freqs = [0] * _rand_max
    with open(fname, "r") as fp:
        for line in fp:
            num = int(line.strip())
            freqs[num] -= 1
    return freqs


def write_sync(fname):
    freqs = [0] * _rand_max
    with open(fname, "w") as fp:
        for _ in range(_iters):
            num = random.randrange(0, _rand_max)
            freqs[num] += 1
            fp.write(f"{num}\n")
    return freqs


def test_sync():
    fnames = [f"{uuid4()}.txt" for _ in range(_files)]

    freqs = map(write_sync, fnames)
    write_freqs = dict(zip(fnames, freqs))

    freqs = map(read_sync, fnames)
    read_freqs = dict(zip(fnames, freqs))

    return {
        name: [w + r for w, r in zip(write_freqs[name], read_freqs[name])]
        for name in fnames
    }


async def read_aiofile(fname):
    freqs = [0] * 10
    async with AIOFile(fname, "r") as fp:
        r = LineReader(fp)
        async for line in r:
            num = int(line.strip())
            freqs[num] -= 1
    return freqs


async def write_aiofile(fname):
    freqs = [0] * 10
    async with AIOFile(fname, "w") as fp:
        w = Writer(fp)
        for _ in range(_iters):
            num = random.randrange(0, 10)
            freqs[num] += 1
            await w(f"{num}\n")
    return freqs


async def read_aiofiles(fname):
    freqs = [0] * 10
    async with aio_open(fname, "r") as fp:
        async for line in fp:
            num = int(line.strip())
            freqs[num] -= 1
    return freqs


async def write_aiofiles(fname):
    freqs = [0] * 10
    async with aio_open(fname, "w") as fp:
        for _ in range(_iters):
            num = random.randrange(0, 10)
            freqs[num] += 1
            await fp.write(f"{num}\n")
    return freqs


async def test_async(reader, writer):
    fnames = [f"{uuid4()}.txt" for _ in range(_files)]

    freqs = await asyncio.gather(*map(writer, fnames))
    write_freqs = dict(zip(fnames, freqs))

    freqs = await asyncio.gather(*map(reader, fnames))
    read_freqs = dict(zip(fnames, freqs))

    return {
        name: [w + r for w, r in zip(write_freqs[name], read_freqs[name])]
        for name in fnames
    }


async def test_executor():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, test_sync)


async def test_multi_job_executor():
    async def print_and_sleep():
        while True:
            print(time.time(), file=sys.stderr)
            await asyncio.sleep(0.01)

    freqs, pending = await asyncio.wait(
        (
            asyncio.ensure_future(test_executor()),
            asyncio.ensure_future(print_and_sleep()),
        ),
        return_when=asyncio.FIRST_COMPLETED,
    )
    for co in pending:
        co.cancel()
    return await list(freqs)[0]


def test_sync_one():
    fname = f"{uuid4()}.txt"
    write_freq = write_sync(fname)
    read_freq = read_sync(fname)
    return (fname, [w + r for w, r in zip(write_freq, read_freq)])


async def test_executor_parallel():
    loop = asyncio.get_event_loop()
    return dict(
        await asyncio.gather(
            *(loop.run_in_executor(None, test_sync_one) for _ in range(_files))
        )
    )


async def time_coroutine(co):
    t = time.perf_counter()
    ret = await co
    print(time.perf_counter() - t)
    return ret


def time_callable(cb):
    t = time.perf_counter()
    ret = cb()
    print(time.perf_counter() - t)
    return ret


def check(freqs, name):
    if all(all(v == 0 for v in f) for f in freqs.values()):
        print(name, "passed")
    else:
        print(name, "failed")

    for fname in freqs:
        os.remove(fname)


async def run_async_tests():
    freqs = await time_coroutine(test_executor())
    check(freqs, "async (executor)")
    freqs = await time_coroutine(test_multi_job_executor())
    check(freqs, "async (executor w/ simultaneous coroutines)")
    freqs = await time_coroutine(test_executor_parallel())
    check(freqs, "async (multiple executors)")
    freqs = await time_coroutine(test_async(read_aiofiles, write_aiofiles))
    check(freqs, "async (aiofiles)")
    freqs = await time_coroutine(test_async(read_aiofile, write_aiofile))
    check(freqs, "async (aiofile)")


while _iters <= 10 ** 8:
    print("with", _iters, "numbers")

    # synchronous code as a baseline
    freqs = time_callable(lambda: test_sync())
    check(freqs, "sync")

    # run sync in executor – the "dumb way"
    freqs = time_callable(lambda: asyncio.run(test_executor()))
    check(freqs, "async (executor)")

    # make sure that async actually picks up the thread while i/o is happening
    freqs = time_callable(lambda: asyncio.run(test_multi_job_executor()))
    check(freqs, "async (executor w/ simultaneous coroutines)")

    # do multiple file i/o sequences in parallel
    freqs = time_callable(lambda: asyncio.run(test_executor_parallel()))
    check(freqs, "async (multiple executors)")

    # test Tinche/aiofiles
    freqs = time_callable(
        lambda: asyncio.run(test_async(read_aiofiles, write_aiofiles))
    )
    check(freqs, "async (aiofiles)")

    # test mosquito/aiofile
    freqs = time_callable(lambda: asyncio.run(test_async(read_aiofile, write_aiofile)))
    check(freqs, "async (aiofile)")

    asyncio.run(run_async_tests())

    _iters *= 10

