import asyncio
import os
from collections import namedtuple
from functools import partial
from os import strerror
from pathlib import Path
from typing import Any, Coroutine, Optional, Union
from weakref import finalize

import caio
from caio.asyncio_base import AsyncioContextBase


AIO_FILE_NOT_OPENED = -1
AIO_FILE_CLOSED = -2


FileMode = namedtuple(
    "FileMode", (
        "readable",
        "writable",
        "plus",
        "appending",
        "created",
        "flags",
        "binary",
    ),
)


def parse_mode(mode: str) -> FileMode:
    """ Rewritten from `cpython fileno`_

    .. _cpython fileio: https://bit.ly/2JY2cnp
    """

    flags = os.O_RDONLY

    rwa = False
    writable = False
    readable = False
    plus = False
    appending = False
    created = False
    binary = False

    for m in mode:
        if m == "x":
            rwa = True
            created = True
            writable = True
            flags |= os.O_EXCL | os.O_CREAT

        if m == "r":
            if rwa:
                raise Exception("Bad mode")

            rwa = True
            readable = True

        if m == "w":
            if rwa:
                raise Exception("Bad mode")

            rwa = True
            writable = True

            flags |= os.O_CREAT | os.O_TRUNC

        if m == "a":
            if rwa:
                raise Exception("Bad mode")
            rwa = True
            writable = True
            appending = True
            flags |= os.O_APPEND | os.O_CREAT

        if m == "+":
            if plus:
                raise Exception("Bad mode")
            readable = True
            writable = True
            plus = True

        if m == "b":
            binary = True
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY

    if readable and writable:
        flags |= os.O_RDWR

    elif readable:
        flags |= os.O_RDONLY
    else:
        flags |= os.O_WRONLY

    return FileMode(
        readable=readable,
        writable=writable,
        plus=plus,
        appending=appending,
        created=created,
        flags=flags,
        binary=binary,
    )


class AIOFile:
    def __init__(
        self, filename: Union[str, Path],
        mode: str = "r", encoding: str = "utf-8",
        context: Optional[AsyncioContextBase] = None,
    ):

        self.__context = context or get_default_context()

        self.__fname = str(filename)
        self.__open_mode = mode

        self.mode = parse_mode(mode)

        self.__file_obj = None
        self.__encoding = encoding

    def _run_in_thread(
            self, func, *args, **kwargs
    ) -> Coroutine[Any, Any, Any]:
        return self.__context.loop.run_in_executor(
            None, partial(func, *args, **kwargs),
        )

    @property
    def name(self):
        return self.__fname

    @property
    def loop(self):
        return self.__context.loop

    @property
    def encoding(self):
        return self.__encoding

    async def open(self):
        if self.__file_obj is not None:
            return

        if self.__file_obj and self.__file_obj.closed:
            raise asyncio.InvalidStateError("AIOFile closed")

        self.__file_obj = await self._run_in_thread(
            open, self.__fname, self.__open_mode,
        )

        return self.fileno()

    def __repr__(self):
        return "<AIOFile: %r>" % self.__fname

    async def close(self):
        if self.__file_obj is None:
            return

        if self.mode.writable:
            await self.fsync()

        await self._run_in_thread(self.__file_obj.close)

    def fileno(self) -> int:
        if self.__file_obj is None:
            raise asyncio.InvalidStateError("AIOFile closed")
        return self.__file_obj.fileno()

    def __await__(self):
        yield from self.open().__await__()
        return self

    async def __aenter__(self):
        await self.open()
        return self

    def __aexit__(self, *args):
        return asyncio.get_event_loop().create_task(self.close())

    async def read(self, size: int = -1, offset: int = 0) -> Union[bytes, str]:
        data = await self.read_bytes(size, offset)
        return data if self.mode.binary else self.decode_bytes(data)

    async def read_bytes(self, size: int = -1, offset: int = 0) -> bytes:
        if size < -1:
            raise ValueError("Unsupported value %d for size" % size)

        if size == -1:
            size = (
                await self._run_in_thread(
                    os.stat,
                    self.fileno(),
                )
            ).st_size

        return await self.__context.read(size, self.fileno(), offset)

    async def write(self, data: Union[str, bytes], offset: int = 0):
        if self.mode.binary:
            if not isinstance(data, bytes):
                raise ValueError("Data must be bytes in binary mode")
            bytes_data = data
        else:
            if not isinstance(data, str):
                raise ValueError("Data must be str in text mode")
            bytes_data = self.encode_bytes(data)

        return await self.write_bytes(bytes_data, offset)

    def encode_bytes(self, data: str) -> bytes:
        return data.encode(self.__encoding)

    def decode_bytes(self, data: bytes) -> str:
        return data.decode(self.__encoding)

    async def write_bytes(self, data: bytes, offset: int = 0):
        data_size = len(data)
        if data_size == 0:
            return 0

        # data can be written partially, see write(2)
        # (https://www.man7.org/linux/man-pages/man2/write.2.html)
        # for example, it can happen when a disk quota or a resource limit
        # is exceeded (in that case subsequent call will return a
        # corresponding error) or write has been interrupted by
        # an incoming signal

        # behaviour here in regards to continue trying to write remaining data
        # corresponds to the behaviour of io.BufferedIOBase
        # (https://docs.python.org/3/library/io.html#io.BufferedIOBase.write)
        # which used by object returned open() with `buffering` argument >= 1
        # (effectively the default)

        written = 0
        while written < data_size:
            res = await self.__context.write(
                data[written:], self.fileno(), offset + written
            )
            if res == 0:
                raise RuntimeError(
                    'Write operation returned 0', self, offset, written
                )
            elif res < 0:
                # fix for linux_aio implementation bug in caio<=0.6.1
                # (https://github.com/mosquito/caio/pull/7)
                # and safeguard from future similar issues
                errno = -res
                raise OSError(errno, strerror(errno), self.__fname)

            written += res

        return written

    async def fsync(self):
        return await self.__context.fdsync(self.fileno())

    def truncate(self, length: int = 0):
        return self._run_in_thread(
            os.ftruncate, self.fileno(), length,
        )


DEFAULT_CONTEXT_STORE = {}


def create_context(
        max_requests=caio.AsyncioContext.MAX_REQUESTS_DEFAULT,
) -> caio.AsyncioContext:
    loop = asyncio.get_event_loop()
    context = caio.AsyncioContext(max_requests, loop=loop)
    finalize(loop, lambda *_: context.close())
    DEFAULT_CONTEXT_STORE[loop] = context
    return context


def get_default_context() -> caio.AsyncioContext:
    loop = asyncio.get_event_loop()
    context = DEFAULT_CONTEXT_STORE.get(loop)

    if context is not None:
        return context

    return create_context()
