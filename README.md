# AIOFile

[![Github Actions](https://github.com/mosquito/aiofile/workflows/ci/badge.svg)](https://github.com/mosquito/aiofile/actions?query=workflow%3Aci) [![Latest Version](https://img.shields.io/pypi/v/aiofile.svg)](https://pypi.python.org/pypi/aiofile/) [![Python Versions](https://img.shields.io/pypi/pyversions/aiofile.svg)](https://pypi.python.org/pypi/aiofile/) [![License](https://img.shields.io/pypi/l/aiofile.svg)](https://pypi.python.org/pypi/aiofile/) [![Coverage Status](https://coveralls.io/repos/github/mosquito/aiofile/badge.svg?branch=master)](https://coveralls.io/github/mosquito/aiofile?branch=master)

Real asynchronous file operations with asyncio support.

## Features

* Since version 2.0.0, uses [caio](https://pypi.org/project/caio), which provides multiple
  async I/O backends:
  * **Linux io_uring** — the modern, high-performance Linux kernel AIO interface.
  * **Linux libaio** — the classic kernel AIO mechanism via [libaio](https://pagure.io/libaio).
  * **Thread-based (C)** — a [threadpool](https://github.com/mbrossard/threadpool/)-backed
    implementation for POSIX systems (macOS, Linux).
  * **Pure Python** — a thread-based fallback for any platform.
* The best available backend is chosen automatically based on system compatibility.
* `AIOFile` has no internal file pointer. Pass `offset` and `chunk_size` to each operation,
  or use the `Reader`/`Writer` helpers. For a file-like interface, use `async_open`.

## Limitations

* The Linux native AIO and io_uring backends cannot open special files.
  Asynchronous operations against special filesystems such as `/proc/` or `/sys/` are not
  supported by the kernel — this is neither an aiofile nor a caio issue.
  In such cases, switch to a thread-based implementation
  (see the [Troubleshooting](#troubleshooting) section).

## Code examples

All code examples require Python 3.11+.

### High-level API

#### `async_open` helper

This helper mimics Python's file-like objects, returning an object with
equivalent asynchronous methods.

Supported methods:

* `async def read(length=-1)` — reads a chunk from the file; `-1` reads to the end.
* `async def write(data)` — writes a chunk to the file.
* `def seek(offset)` — sets the file pointer position.
* `def tell()` — returns the current file pointer position.
* `async def readline(size=-1, newline="\n")` — reads until a newline or EOF.
  Since version 3.7.0, `__aiter__` returns a `LineReader`.
  This method is suboptimal for small lines because it does not reuse the read buffer —
  prefer `LineReader` when reading line by line.
* `def __aiter__() -> LineReader` — iterator over lines.
* `def iter_chunked(chunk_size: int = 32768) -> Reader` — iterator over chunks.
* `.file` — the underlying `AIOFile` object.

Basic example:

<!-- name: test_basic -->
```python
import asyncio
from pathlib import Path
from tempfile import gettempdir

from aiofile import async_open

tmp_filename = Path(gettempdir()) / "hello.txt"

async def main():
    async with async_open(tmp_filename, 'w+') as afp:
        await afp.write("Hello ")
        await afp.write("world")
        afp.seek(0)

        print(await afp.read())

        await afp.write("Hello from\nasync world")
        print(await afp.readline())
        print(await afp.readline())

asyncio.run(main())
```

Example without context manager:

<!-- name: test_basic_without_context_manager -->
```python
import asyncio
import atexit
import os
from tempfile import mktemp

from aiofile import async_open


TMP_NAME = mktemp()
atexit.register(os.unlink, TMP_NAME)


async def main():
    afp = await async_open(TMP_NAME, "w")
    await afp.write("Hello")
    await afp.close()


asyncio.run(main())
assert open(TMP_NAME, "r").read() == "Hello"
```

Concatenate example program (`cat`):

```python
import asyncio
import sys
from argparse import ArgumentParser
from pathlib import Path

from aiofile import async_open

parser = ArgumentParser(
    description="Read files line by line using asynchronous io API"
)
parser.add_argument("file_name", nargs="+", type=Path)

async def main(arguments):
    for src in arguments.file_name:
        async with async_open(src, "r") as afp:
            async for line in afp:
                sys.stdout.write(line)


asyncio.run(main(parser.parse_args()))
```

Copy file example program (`cp`):

```python
import asyncio
from argparse import ArgumentParser
from pathlib import Path

from aiofile import async_open

parser = ArgumentParser(
    description="Copying files using asynchronous io API"
)
parser.add_argument("source", type=Path)
parser.add_argument("dest", type=Path)
parser.add_argument("--chunk-size", type=int, default=65535)


async def main(arguments):
    async with async_open(arguments.source, "rb") as src, \
               async_open(arguments.dest, "wb") as dest:
        async for chunk in src.iter_chunked(arguments.chunk_size):
            await dest.write(chunk)


asyncio.run(main(parser.parse_args()))
```

Example with opening an already-open file pointer:

```python
import asyncio
from typing import IO, Any
from aiofile import async_open


async def main(fp: IO[Any]):
    async with async_open(fp) as afp:
        await afp.write("Hello from\nasync world")
        print(await afp.readline())


with open("test.txt", "w+") as fp:
    asyncio.run(main(fp))
```

Linux native AIO and io_uring do not support reading or writing special files
(procfs, sysfs, Unix pipes, etc.), so operations on these files require
a compatible context object.

```python
import asyncio
from aiofile import async_open
from caio import thread_aio_asyncio
from contextlib import AsyncExitStack


async def main():
    async with AsyncExitStack() as stack:

        # Custom context should be reused
        ctx = await stack.enter_async_context(
            thread_aio_asyncio.AsyncioContext()
        )

        # Open special file with custom context
        src = await stack.enter_async_context(
            async_open("/proc/cpuinfo", "r", context=ctx)
        )

        # Open regular file with default context
        dest = await stack.enter_async_context(
            async_open("/tmp/cpuinfo", "w")
        )

        # Copying file content line by line
        async for line in src:
            await dest.write(line)


asyncio.run(main())
```

### `clone` helper

An asynchronous context supports a limited number of concurrent operations at the low level,
regardless of how many file descriptors are open. `clone` lets you create a second file-like
object with its own independent offset from a single descriptor, without opening the file
multiple times.

```python
"""
This example counts multiple hash functions from the file passed as the first
argument. The hash functions are counted competitively, and the results are
printed in the order of hashing completion.
"""
import asyncio
import hashlib
import sys

import aiofile


async def hasher(name, hash_func, afp):
    loop = asyncio.get_running_loop()
    async for chunk in afp.iter_chunked(2 ** 20):
        await loop.run_in_executor(None, hash_func.update, chunk)
    print(name, hash_func.hexdigest())


async def main():
    async with aiofile.async_open(sys.argv[1], "rb") as source:
        hashers = [
            ("MD5", hashlib.md5()),
            ("SHA1", hashlib.sha1()),
            ("SHA256", hashlib.sha256()),
            ("SHA512", hashlib.sha512()),
        ]

        await asyncio.gather(*[
            hasher(name, hash_func, await aiofile.clone(source))
            for name, hash_func in hashers
        ])


asyncio.run(main())
```

> **Note:** This will likely perform poorly on Windows, so if that is
> your target platform, this optimization may not be worth it.

### Low-level API

The `AIOFile` class is a low-level interface for asynchronous file operations. Its `read` and
`write` methods accept an `offset=0` (in bytes) at which the operation is performed.

This allows many independent I/O operations on a single open file without an internal pointer.
For sequential reading and writing, use `Writer`, `Reader`, and `LineReader`. Note that
`async_open` is not the same as `AIOFile`: it wraps it to provide a file-like interface
similar to the built-in `open`.

```python
import asyncio
from aiofile import AIOFile


async def main():
    async with AIOFile("hello.txt", 'w+') as afp:
        payload = "Hello world\n"

        await asyncio.gather(
            *[afp.write(payload, offset=i * len(payload)) for i in range(10)]
        )

        await afp.fsync()

        assert await afp.read(len(payload) * 10) == payload * 10

asyncio.run(main())
```

The low-level API is essentially a lightly sugared `caio` API.

```python
import asyncio
from aiofile import AIOFile


async def main():
    async with AIOFile("/tmp/hello.txt", 'w+') as afp:
        await afp.write("Hello ")
        await afp.write("world", offset=7)
        await afp.fsync()

        print(await afp.read())


asyncio.run(main())
```

#### `Reader` and `Writer`

To read or write a file linearly, the following example may be helpful.

```python
import asyncio
from aiofile import AIOFile, Reader, Writer


async def main():
    async with AIOFile("/tmp/hello.txt", 'w+') as afp:
        writer = Writer(afp)
        reader = Reader(afp, chunk_size=8)

        await writer("Hello")
        await writer(" ")
        await writer("World")
        await afp.fsync()

        async for chunk in reader:
            print(chunk)


asyncio.run(main())
```

#### `LineReader` - read file line by line

`LineReader` is a helper for reading a file linearly, line by line. It maintains a buffer
and reads file fragments chunk by chunk, searching for line boundaries. The default chunk
size is 4KB.

```python
import asyncio
from aiofile import AIOFile, LineReader, Writer


async def main():
    async with AIOFile("/tmp/hello.txt", 'w+') as afp:
        writer = Writer(afp)

        await writer("Hello")
        await writer(" ")
        await writer("World")
        await writer("\n")
        await writer("\n")
        await writer("From async world")
        await afp.fsync()

        async for line in LineReader(afp):
            print(line)


asyncio.run(main())
```

To read a file line by line, prefer `LineReader` over `async_open`.

## More examples

### Async CSV Dict Reader

```python
import asyncio
import io
from csv import DictReader

from aiofile import AIOFile, LineReader


class AsyncDictReader:
    def __init__(self, afp, **kwargs):
        self.buffer = io.BytesIO()
        self.file_reader = LineReader(
            afp, line_sep=kwargs.pop('line_sep', '\n'),
            chunk_size=kwargs.pop('chunk_size', 4096),
            offset=kwargs.pop('offset', 0),
        )
        self.reader = DictReader(
            io.TextIOWrapper(
                self.buffer,
                encoding=kwargs.pop('encoding', 'utf-8'),
                errors=kwargs.pop('errors', 'replace'),
            ), **kwargs,
        )
        self.line_num = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.line_num == 0:
            header = await self.file_reader.readline()
            self.buffer.write(header)

        line = await self.file_reader.readline()

        if not line:
            raise StopAsyncIteration

        self.buffer.write(line)
        self.buffer.seek(0)

        try:
            result = next(self.reader)
        except StopIteration as e:
            raise StopAsyncIteration from e

        self.buffer.seek(0)
        self.buffer.truncate(0)
        self.line_num = self.reader.line_num

        return result


async def main():
    async with AIOFile('sample.csv', 'rb') as afp:
        async for item in AsyncDictReader(afp):
            print(item)


asyncio.run(main())
```

## Troubleshooting

The caio Linux backends (io_uring and libaio) work well on modern kernels and filesystems.
Problems are usually environment-specific and are not bugs. There are several ways to resolve them:

1. Upgrade the kernel.
2. Use a compatible filesystem.
3. Switch to a thread-based or pure-Python backend.

Since version 0.7.0, caio offers several ways to select the backend:

1. Set the `CAIO_IMPL` environment variable at runtime:

    * `uring` — Linux io_uring (requires kernel ≥ 5.1)
    * `linux` — Linux libaio
    * `thread` — C-based thread pool implementation
    * `python` — pure-Python thread-based fallback

2. The `default_implementation` file located next to `__init__.py` in the caio installation
   directory. Useful for distribution package maintainers. The file may contain comments
   (lines starting with `#`); the first non-comment line should be one of the values above.

3. Manage contexts manually:

```python
import asyncio

from aiofile import async_open
from caio import linux_aio_asyncio, thread_aio_asyncio


async def main():
    linux_ctx = linux_aio_asyncio.AsyncioContext()
    threads_ctx = thread_aio_asyncio.AsyncioContext()

    async with async_open("/tmp/test.txt", "w", context=linux_ctx) as afp:
        await afp.write("Hello")

    async with async_open("/tmp/test.txt", "r", context=threads_ctx) as afp:
        print(await afp.read())


asyncio.run(main())
```
