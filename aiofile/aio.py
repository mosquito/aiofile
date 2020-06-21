import os
import asyncio
from collections import namedtuple
from functools import partial
from typing import Any, Union, Optional, Coroutine
from weakref import finalize

import caio
from caio.asyncio_base import AsyncioContextBase

AIO_FILE_NOT_OPENED = -1
AIO_FILE_CLOSED = -2


FileMode = namedtuple('FileMode', (
    'readable',
    'writable',
    'plus',
    'appending',
    'created',
    'flags',
    'binary',
))


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
        if m == 'x':
            rwa = True
            created = True
            writable = True
            flags |= os.O_EXCL | os.O_CREAT

        if m == 'r':
            if rwa:
                raise Exception('Bad mode')

            rwa = True
            readable = True

        if m == 'w':
            if rwa:
                raise Exception('Bad mode')

            rwa = True
            writable = True

            flags |= os.O_CREAT | os.O_TRUNC

        if m == 'a':
            if rwa:
                raise Exception('Bad mode')
            rwa = True
            writable = True
            appending = True
            flags |= os.O_APPEND | os.O_CREAT

        if m == '+':
            if plus:
                raise Exception('Bad mode')
            readable = True
            writable = True
            plus = True

        if m == 'b':
            binary = True
            if hasattr(os, 'O_BINARY'):
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
    def __init__(self, filename: str, mode: str = "r",
                 access_mode: int = 0o644, encoding: str = 'utf-8',
                 context: Optional[AsyncioContextBase] = None):

        self.__context = context or get_default_context()

        self.mode = parse_mode(mode)

        self.__fname = str(filename)
        self.__fileno = AIO_FILE_NOT_OPENED
        self.__access_mode = access_mode
        self.__encoding = encoding

    def _run_in_thread(
            self, func, *args, **kwargs
    ) -> Coroutine[Any, Any, Any]:
        return self.__context.loop.run_in_executor(
            None, partial(func, *args, **kwargs)
        )

    @property
    def name(self):
        return self.__fname

    @property
    def loop(self):
        return self.__context.loop

    async def open(self):
        if self.__fileno == AIO_FILE_CLOSED:
            raise asyncio.InvalidStateError('AIOFile closed')

        if self.__fileno != AIO_FILE_NOT_OPENED:
            return

        self.__fileno = await self._run_in_thread(
            os.open,
            self.__fname,
            flags=self.mode.flags,
            mode=self.__access_mode
        )

    def open_fd(self, fd: int):
        if self.__fileno == AIO_FILE_CLOSED:
            raise asyncio.InvalidStateError('AIOFile closed')

        if self.__fileno != AIO_FILE_NOT_OPENED:
            raise RuntimeError('Already opened')

        self.__fileno = fd

    def __repr__(self):
        return "<AIOFile: %r>" % self.__fname

    async def close(self):
        if self.__fileno < 0:
            return

        if self.mode.writable:
            await self.fsync()

        await self._run_in_thread(os.close, self.__fileno)
        self.__fileno = AIO_FILE_CLOSED

    def fileno(self):
        return self.__fileno

    def __await__(self):
        yield from self.open().__await__()
        return self

    async def __aenter__(self):
        await self.open()
        return self

    def __aexit__(self, *args):
        return asyncio.get_event_loop().create_task(self.close())

    async def read(self, size: int = -1, offset: int = 0) -> Union[bytes, str]:
        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        if size < -1:
            raise ValueError("Unsupported value %d for size" % size)

        if size == -1:
            size = (
                await self._run_in_thread(
                    os.stat,
                    self.__fileno
                )
            ).st_size

        data = await self.__context.read(size, self.__fileno, offset)
        return data if self.mode.binary else data.decode(self.__encoding)

    async def write(self, data: Union[str, bytes], offset: int = 0):
        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        if self.mode.binary:
            if not isinstance(data, bytes):
                raise ValueError("Data must be bytes in binary mode")
            bytes_data = data
        else:
            if not isinstance(data, str):
                raise ValueError("Data must be str in text mode")
            bytes_data = data.encode(self.__encoding)

        return await self.__context.write(bytes_data, self.__fileno, offset)

    async def fsync(self):
        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')
        return await self.__context.fdsync(self.__fileno)

    def truncate(self, length: int = 0):
        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        return self._run_in_thread(
            os.ftruncate,
            self.__fileno,
            length,
        )


DEFAULT_CONTEXT_STORE = {}


def create_context(
        max_requests=caio.AsyncioContext.MAX_REQUESTS_DEFAULT
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
