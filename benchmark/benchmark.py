import asyncio
import os
import logging
import time
from functools import wraps
from glob import glob
from pathlib import Path

from humanize import naturalsize
import aiomisc

import aiofile
import aiofiles
from multiprocessing.pool import ThreadPool


log = logging.getLogger(__name__)
DATA_PATH = Path("data")


def gen_data(nfiles, pool_size=32, chunk_size=64 * 1024, chunks=1024):
    with ThreadPool(pool_size) as pool:
        def generator(number):
            with open(DATA_PATH / "{}.bin".format(number), "wb") as fp:
                for chunk in range(chunks):
                    fp.write(os.urandom(chunk_size))

                return fp.tell()

        total_size = 0
        for size in pool.imap_unordered(generator, range(nfiles)):
            total_size += size

        return total_size


def measure(func):
    @wraps(func)
    async def wrap(*args, **kwargs):
        delta = -time.monotonic()
        try:
            return await func(*args, **kwargs)
        finally:
            delta += time.monotonic()
            log.info("Function %s took %.6f seconds", func, delta)

    return wrap


@measure
async def read_aiofile(files, chunk_size):
    async def reader(file_name):
        async with aiofile.AIOFile(file_name, "rb") as afp:
            reader = aiofile.Reader(afp, chunk_size=chunk_size)
            size = 0
            async for chunk in reader:
                size += len(chunk)
            return size

    return sum(
        await asyncio.gather(*[reader(fname) for fname in files])
    )


@measure
async def read_aiomisc_io(files, chunk_size):
    async def reader(file_name):
        async with aiomisc.io.async_open(file_name, "rb") as afp:
            size = 0
            data = await afp.read(chunk_size)
            while data:
                size += len(data)
                data = await afp.read(chunk_size)

            size += len(data)
            return size

    return sum(
        await asyncio.gather(*[reader(fname) for fname in files])
    )


@measure
async def read_aiofiles(files, chunk_size):
    async def reader(file_name):
        async with aiofiles.open(file_name, "rb") as afp:
            size = 0
            data = await afp.read(chunk_size)
            while data:
                size += len(data)
                data = await afp.read(chunk_size)

            size += len(data)
            return size

    return sum(
        await asyncio.gather(*[reader(fname) for fname in files])
    )


async def benchmark(files, chunk_size=8 * 1024):
    total_size = await read_aiofile(files, chunk_size)
    log.info("Total size is %d", total_size)

    total_size = await read_aiofiles(files, chunk_size)
    log.info("Total size is %d", total_size)

    total_size = await read_aiomisc_io(files, chunk_size)
    log.info("Total size is %d", total_size)


def main():
    number_of_files = 32

    with aiomisc.entrypoint() as loop:
        log.info("Generating random data")
        DATA_PATH.mkdir(exist_ok=True)
        total_bytes = gen_data(number_of_files)
        log.info(
            "Generated %s in %d files",
            naturalsize(total_bytes), number_of_files
        )

        log.info("Starting benchmark")
        files = glob(str(DATA_PATH / "*.bin"))
        loop.run_until_complete(benchmark(files))


if __name__ == '__main__':
    main()
