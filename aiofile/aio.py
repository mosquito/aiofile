import os
import asyncio
from functools import partial
from typing import Generator, Any, Union

try:
    from .posix_aio import IO_NOP, IO_WRITE, IO_READ, AIOOperation
except ImportError:
    from .thread_aio import (
        IO_READ, IO_WRITE, IO_NOP, ThreadedAIOOperation as AIOOperation
    )

MODE_MAPPING = (
    ("x", (os.O_EXCL,)),
    ("r", (os.O_RDONLY, os.O_RDWR)),
    ("a", (os.O_APPEND, os.O_CREAT)),
    ("w", (os.O_RDWR, os.O_TRUNC, os.O_CREAT)),
    ("+", (os.O_RDWR,)),
)

AIO_FILE_NOT_OPENED = -1
AIO_FILE_CLOSED = -2


ReadResultType = Generator[Any, None, Union[bytes, str]]


def run_in_thread(func, *args, **kwargs) -> asyncio.Future:
    loop = kwargs.pop('loop')       # type: asyncio.AbstractEventLoop
    assert not loop.is_closed(), "Event loop is closed"
    assert loop.is_running(), "Event loop is not running"

    return loop.run_in_executor(None, partial(func, *args, **kwargs))


def mode_to_flags(mode: str):
    if len(set("awrb+") | set(mode)) > 5:
        raise ValueError('Invalid mode %s' % repr(mode))

    if len(set(mode) & set("awr")) > 1:
        raise ValueError(
            'must have exactly one of create/read/write/append mode'
        )

    flags = 0

    for m_mode, m_flags in MODE_MAPPING:
        if m_mode in mode:
            for flag in m_flags:
                flags |= flag

    flags |= os.O_NONBLOCK

    return flags


class AIOFile:
    __slots__ = (
        '__fileno', '__fname', '__mode',
        '__access_mode', '__loop', '__encoding',
        '__binary',
    )

    OPERATION_CLASS = AIOOperation
    IO_READ = IO_READ
    IO_WRITE = IO_WRITE
    IO_NOP = IO_NOP

    def __init__(self, filename: str, mode: str="r", access_mode: int=0o644,
                 loop=None, encoding: str='utf-8'):
        self.__loop = loop or asyncio.get_event_loop()
        self.__fname = str(filename)
        self.__mode = mode
        self.__access_mode = access_mode
        self.__binary = 'b' in self.__mode
        self.__fileno = AIO_FILE_NOT_OPENED
        self.__encoding = encoding

    @property
    def name(self):
        return self.__fname

    @property
    def binary(self):
        return self.__binary

    @property
    def loop(self):
        return self.__loop

    @asyncio.coroutine
    def open(self):
        if self.__fileno == AIO_FILE_CLOSED:
            raise asyncio.InvalidStateError('AIOFile closed')

        if self.__fileno != AIO_FILE_NOT_OPENED:
            return

        self.__fileno = yield from run_in_thread(
            os.open,
            self.__fname,
            loop=self.__loop,
            flags=mode_to_flags(self.__mode),
            mode=self.__access_mode
        )

    def __repr__(self):
        return "<AIOFile: %r>" % self.__fname

    @asyncio.coroutine
    def close(self):
        if self.__fileno < 0:
            return

        yield from self.fsync()
        yield from run_in_thread(os.close, self.__fileno, loop=self.__loop)
        self.__fileno = AIO_FILE_CLOSED

    def fileno(self):
        return self.__fileno

    def __iter__(self):
        yield from self.open()
        return self

    def __await__(self):
        yield from self.open()
        return self

    @asyncio.coroutine
    def __aenter__(self):
        yield from self.open()
        return self

    def __aexit__(self, *args):
        return self.__loop.create_task(self.close())

    @asyncio.coroutine
    def read(self, size: int=-1, offset: int=0) -> ReadResultType:

        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        if size < -1:
            raise ValueError("Unsupported value %d for size" % size)

        if size == -1:
            size = (
                yield from run_in_thread(
                    os.stat,
                    self.__fileno,
                    loop=self.loop
                )
            ).st_size

        data = yield from self.OPERATION_CLASS(
            self.IO_READ,
            self.__fileno,
            offset,
            size,
            self.__loop
        )

        return data if self.__binary else data.decode(self.__encoding)

    @asyncio.coroutine
    def write(self, data: (str, bytes), offset: int=0):
        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        if self.__binary:
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
        return (yield from op)

    @asyncio.coroutine
    def fsync(self):
        if self.__fileno < 0:
            raise asyncio.InvalidStateError('AIOFile closed')

        return (
            yield from self.OPERATION_CLASS(
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
