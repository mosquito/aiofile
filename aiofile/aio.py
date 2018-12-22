import os
import asyncio
from collections import namedtuple
from functools import partial
from typing import Generator, Any, Union

try:
    from .posix_aio import IO_NOP, IO_WRITE, IO_READ, AIOOperation
except ImportError:
    from .thread_aio import (
        IO_READ, IO_WRITE, IO_NOP, ThreadedAIOOperation as AIOOperation
    )


AIO_FILE_NOT_OPENED = -1
AIO_FILE_CLOSED = -2


ReadResultType = Generator[Any, None, Union[bytes, str]]


def run_in_thread(func, *args, **kwargs) -> asyncio.Future:
    loop = kwargs.pop('loop')       # type: asyncio.AbstractEventLoop
    assert not loop.is_closed(), "Event loop is closed"
    assert loop.is_running(), "Event loop is not running"

    return loop.run_in_executor(None, partial(func, *args, **kwargs))


FileMode = namedtuple('FileMode', (
    'readable',
    'writable',
    'plus',
    'appending',
    'created',
    'flags',
    'binary',
))


def parse_mode(mode: str):
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
    __slots__ = (
        '__fileno', '__fname', 'mode',
        '__access_mode', '__loop', '__encoding',
    )

    OPERATION_CLASS = AIOOperation
    IO_READ = IO_READ
    IO_WRITE = IO_WRITE
    IO_NOP = IO_NOP

    def __init__(self, filename: str, mode: str="r", access_mode: int=0o644,
                 loop=None, encoding: str='utf-8'):
        self.mode = parse_mode(mode)
        self.__loop = loop or asyncio.get_event_loop()
        self.__fname = str(filename)
        self.__access_mode = access_mode
        self.__fileno = AIO_FILE_NOT_OPENED
        self.__encoding = encoding

    @property
    def name(self):
        return self.__fname

    @property
    def loop(self):
        return self.__loop

    async def open(self):
        if self.__fileno == AIO_FILE_CLOSED:
            raise asyncio.InvalidStateError('AIOFile closed')

        if self.__fileno != AIO_FILE_NOT_OPENED:
            return

        self.__fileno = await run_in_thread(
            os.open,
            self.__fname,
            loop=self.__loop,
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

        await run_in_thread(os.close, self.__fileno, loop=self.__loop)
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
        return self.__loop.create_task(self.close())

    async def read(self, size: int=-1, offset: int=0) -> ReadResultType:

        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        if size < -1:
            raise ValueError("Unsupported value %d for size" % size)

        if size == -1:
            size = (
                await run_in_thread(
                    os.stat,
                    self.__fileno,
                    loop=self.loop
                )
            ).st_size

        data = await self.OPERATION_CLASS(
            self.IO_READ,
            self.__fileno,
            offset,
            size,
            self.__loop
        )

        return data if self.mode.binary else data.decode(self.__encoding)

    async def write(self, data: (str, bytes), offset: int=0):
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

        op = self.OPERATION_CLASS(
            self.IO_WRITE,
            self.__fileno,
            offset,
            len(bytes_data),
            self.__loop
        )

        op.buffer = bytes_data
        return (await op)

    async def fsync(self):
        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        return (
            await self.OPERATION_CLASS(
                self.IO_NOP,
                self.__fileno, 0, 0,
                self.__loop
            )
        )

    def truncate(self, length: int=0) -> asyncio.Future:
        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        return run_in_thread(
            os.ftruncate,
            self.__fileno,
            length,
            loop=self.__loop
        )
