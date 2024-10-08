# AIOFile

[![Github Actions](https://github.com/mosquito/aiofile/workflows/tox/badge.svg)](https://github.com/mosquito/aiofile/actions?query=branch%3Amaster) [![Latest Version](https://img.shields.io/pypi/v/aiofile.svg)](https://pypi.python.org/pypi/aiofile/) [![Wheel](https://img.shields.io/pypi/wheel/aiofile.svg)](https://pypi.python.org/pypi/aiofile/) [![Python Versions](https://img.shields.io/pypi/pyversions/aiofile.svg)](https://pypi.python.org/pypi/aiofile/) [![License](https://img.shields.io/pypi/l/aiofile.svg)](https://pypi.python.org/pypi/aiofile/) [![Coverage Status](https://coveralls.io/repos/github/mosquito/aiofile/badge.svg?branch=master)](https://coveralls.io/github/mosquito/aiofile?branch=master)

Real asynchronous file operations with asyncio support.

## Status

Development - Stable

## Features

* Since version 2.0.0 using [caio](https://pypi.org/project/caio), which contains linux `libaio` and two
  thread-based implementations (c-based and pure-python).
* AIOFile has no internal pointer. You should pass `offset` and
  `chunk_size` for each operation or use helpers (Reader or Writer).
  The simplest way is to use `async_open` for creating object with
  file-like interface.
* For Linux using implementation based on [libaio](https://pagure.io/libaio).
* For POSIX (MacOS X and optional Linux) using implementation
  based on [threadpool](https://github.com/mbrossard/threadpool/).
* Otherwise using pure-python thread-based implementation.
* Implementation chooses automatically depending on system compatibility.

## Limitations

* Linux native AIO implementation is not able to open special files.
  Asynchronous operations against special fs like `/proc/` `/sys/` are not
  supported by the kernel. It's not a aiofile's or caio issue.
  In these cases, you might switch to thread-based implementations
  (see [Troubleshooting](#troubleshooting) section).
  However, when used on supported file systems, the linux implementation has a
  smaller overhead and is preferred but it's not a silver bullet.

## Code examples

All code examples require Python 3.6+.

### High-level API

#### `async_open` helper

Helper mimics python file-like objects, it returns file-like
objects with similar but async methods.

Supported methods:

* `async def read(length = -1)` - reading chunk from file, when length is
  `-1`, will be reading file to the end.
* `async def write(data)` - writing chunk to file
* `def seek(offset)` - setting file pointer position
* `def tell()` - returns current file pointer position
* `async def readline(size=-1, newline="\n")` - read chunks until
  newline or EOF. Since version 3.7.0 `__aiter__` returns `LineReader`.

  This method is suboptimal for small lines because it doesn't reuse read buffer.
  When you want to read file by lines please avoid using `async_open`
  use `LineReader` instead.
* `def __aiter__() -> LineReader` - iterator over lines.
* `def iter_chunked(chunk_size: int = 32768) -> Reader` - iterator over
  chunks.
* `.file` property contains AIOFile object

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

Example with opening already open file pointer:

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

Linux native aio doesn't support reading and writing special files
(e.g. procfs/sysfs/unix pipes/etc.), so you can perform operations with
these files using compatible context objects.

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

### Low-level API

The `AIOFile` class is a low-level interface for asynchronous file operations, and the read and write methods accept
an `offset=0` in bytes at which the operation will be performed.

This allows you to do many independent IO operations on a once open file without moving the virtual carriage.

For example, you may make 10 concurrent HTTP requests by specifying the `Range` header, and asynchronously write
one opened file, while the offsets must either be calculated manually, or use 10 instances of `Writer` with
specified initial offsets.

In order to provide sequential reading and writing, there is `Writer`, `Reader` and `LineReader`. Keep in mind
`async_open` is not the same as AIOFile, it provides a similar interface for file operations, it simulates methods
like read or write as it is implemented in the built-in open.

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

The Low-level API in fact is just little bit sugared `caio` API.

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

When you want to read or write file linearly following example
might be helpful.

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

LineReader is a helper that is very effective when you want to read a file
linearly and line by line.

It contains a buffer and will read the fragments of the file chunk by
chunk into the buffer, where it will try to find lines.

The default chunk size is 4KB.

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

When you want to read file by lines please avoid to use `async_open`
use `LineReader` instead.

## More examples

Useful examples with `aiofile`

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

The caio `linux` implementation works normal for modern linux kernel versions
and file systems. So you may have problems specific for your environment.
It's not a bug and might be resolved some ways:

1. Upgrade the kernel
2. Use compatible file systems
3. Use threads based or pure python implementation.

The caio since version 0.7.0 contains some ways to do this.

1. In runtime use the environment variable `CAIO_IMPL` with
possible values:

    * `linux` - use native linux kernels aio mechanism
    * `thread` - use thread based implementation written in C
    * `python` - use pure python implementation

2. File `default_implementation` located near `__init__.py` in caio
installation path. It's useful for distros package maintainers. This file
might contains comments (lines starts with `#` symbol) and the first line
should be one of `linux` `thread` or `python`.

3. You might manually manage contexts:

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
