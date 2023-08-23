import asyncio
import os
import sys
import warnings
from concurrent.futures import Executor
from functools import partial
from os import strerror
from pathlib import PurePath
from typing import (
    IO, Any, Awaitable, BinaryIO, Callable, Dict, Generator, NamedTuple,
    Optional, TextIO, TypeVar, Union,
)
from weakref import finalize

import caio
from caio.asyncio_base import AsyncioContextBase


_T = TypeVar("_T")

AIO_FILE_NOT_OPENED = -1
AIO_FILE_CLOSED = -2

FileIOType = Union[TextIO, BinaryIO, IO]


class FileMode(NamedTuple):
    readable: bool
    writable: bool
    plus: bool
    appending: bool
    created: bool
    flags: int
    binary: bool

    @classmethod
    def parse(cls, mode: str) -> "FileMode":    # noqa: C901
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
                flags |= os.O_CREAT | os.O_APPEND

            if m == "+":
                if plus:
                    raise Exception("Bad mode")
                readable = True
                writable = True
                plus = True

            if m == "b":
                binary = True

        if hasattr(os, "O_BINARY"):
            # always add the binary flag because the asynchronous
            # API only works with bytes, we must always open the
            # file in binary mode.
            flags |= os.O_BINARY

        if readable and writable:
            flags |= os.O_RDWR

        elif readable:
            flags |= os.O_RDONLY
        else:
            flags |= os.O_WRONLY

        return cls(
            readable=readable,
            writable=writable,
            plus=plus,
            appending=appending,
            created=created,
            flags=flags,
            binary=binary,
        )


class AIOFile:
    _fileno: int
    _encoding: str
    _executor: Optional[Executor]
    mode: FileMode

    def __init__(
        self, file_specifier: Union[str, PurePath, FileIOType],
        mode: str = "r", encoding: str = sys.getdefaultencoding(),
        context: Optional[AsyncioContextBase] = None,
        executor: Optional[Executor] = None,
    ):
        self.__context = context or get_default_context()

        self.__file_specifier = file_specifier
        self.__is_fp = all((
            hasattr(self.__file_specifier, "name"),
            hasattr(self.__file_specifier, "mode"),
            hasattr(self.__file_specifier, "fileno"),
        ))

        if self.__is_fp:
            self._fname = self.__file_specifier.name
            self.mode = FileMode.parse(self.__file_specifier.mode)
        else:
            self._fname = self.__file_specifier
            self.mode = FileMode.parse(mode)

        self._fileno = -1
        self._encoding = encoding
        self._executor = executor
        self._lock = asyncio.Lock()
        self._clones = 0

    @property
    def name(self) -> str:
        return self._fname

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self.__context.loop

    @property
    def encoding(self) -> str:
        return self._encoding

    @classmethod
    def from_fp(cls, fp: FileIOType) -> "AIOFile":
        warnings.warn(
            "Classmethod is deprecated. Do not use this anymore. "
            "Just pass file-like as a first argument",
            DeprecationWarning
        )
        return cls(fp, mode=fp.mode)

    if hasattr(os, 'O_BINARY'):
        # In windows, the file may already be opened in text mode, and you
        # will have to reopen it in binary mode.
        # Unlike unix windows does not allow you to delete an already
        # opened file, so it is relatively safe to open a file by name.
        def _open_fp(self, fp: FileIOType) -> int:
            return os.open(fp.name, self.mode.flags)
    else:
        def _open_fp(self, fp: FileIOType) -> int:
            return os.dup(fp.fileno())

    def _run_in_thread(
            self, func: "Callable[..., _T]", *args: Any, **kwargs: Any
    ) -> "asyncio.Future[_T]":
        return self.__context.loop.run_in_executor(
            self._executor, partial(func, *args, **kwargs),
        )

    def __open(self) -> int:
        if self.__is_fp:
            result = self._open_fp(self.__file_specifier)
            # remove linked object after first open
            self.__file_specifier = self._fname
            self.__is_fp = False
            return result
        return os.open(self._fname, self.mode.flags)

    async def open(self) -> Optional[int]:
        async with self._lock:
            if self._fileno > 0:
                return None
            self._fileno = await self._run_in_thread(self.__open)
            return self._fileno

    def __repr__(self) -> str:
        return "<AIOFile: %r>" % self._fname

    async def close(self) -> None:
        async with self._lock:
            if self._fileno < 0:
                return

            if self._clones > 0:
                self._clones -= 1
                return

            if self.mode.writable:
                await self.fdsync()

            await self._run_in_thread(os.close, self._fileno)
            self._fileno = -1

    async def clone(self) -> "AIOFile":
        """
        Increases the clone count by one, as long as the clone
        count is greater than zero, all ``self.close()``
        calls will only decrease the clone count without
        really closing anything.
        """
        async with self._lock:
            self._clones += 1
            return self

    def fileno(self) -> int:
        if self._fileno < 0:
            raise asyncio.InvalidStateError(f"Not opened {self.__class__.__name__}")
        return self._fileno

    def __await__(self) -> Generator[None, Any, "AIOFile"]:
        yield from self.open().__await__()
        return self

    async def __aenter__(self) -> "AIOFile":
        await self.open()
        return self

    def __aexit__(self, *args: Any) -> Awaitable[Any]:
        return asyncio.get_event_loop().create_task(self.close())

    async def read(self, size: int = -1, offset: int = 0) -> Union[bytes, str]:
        data = await self.read_bytes(size, offset)
        return data if self.mode.binary else self.decode_bytes(data)

    async def stat(self) -> os.stat_result:
        return await self._run_in_thread(os.fstat, self.fileno())

    async def read_bytes(self, size: int = -1, offset: int = 0) -> bytes:
        if size < -1:
            raise ValueError("Unsupported value %d for size" % size)

        if size == -1:
            stat = await self.stat()
            size = stat.st_size

        return await self.__context.read(size, self.fileno(), offset)

    async def write(self, data: Union[str, bytes], offset: int = 0) -> int:
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
        return data.encode(self._encoding)

    def decode_bytes(self, data: bytes) -> str:
        return data.decode(self._encoding)

    async def write_bytes(self, data: bytes, offset: int = 0) -> int:
        data_size = len(data)
        if data_size == 0:
            return 0

        # data can be written partially, see write(2)
        # (https://www.man7.org/linux/man-pages/man2/write.2.html)
        # for example, it can happen when a disk quota or a resource limit
        # is exceeded (in that case subsequent call will return a
        # corresponding error) or write has been interrupted by
        # an incoming signal

        # behaviour here in regard to continue trying to write remaining data
        # corresponds to the behaviour of io.BufferedIOBase
        # (https://docs.python.org/3/library/io.html#io.BufferedIOBase.write)
        # which used by object returned open() with `buffering` argument >= 1
        # (effectively the default)

        written = 0
        while written < data_size:
            res = await self.__context.write(
                data[written:], self.fileno(), offset + written,
            )
            if res == 0:
                raise RuntimeError(
                    "Write operation returned 0", self, offset, written,
                )
            elif res < 0:
                # fix for linux_aio implementation bug in caio<=0.6.1
                # (https://github.com/mosquito/caio/pull/7)
                # and safeguard against future similar issues
                errno = -res
                raise OSError(errno, strerror(errno), self._fname)

            written += res

        return written

    async def fsync(self) -> None:
        return await self.__context.fsync(self.fileno())

    async def fdsync(self) -> None:
        return await self.__context.fdsync(self.fileno())

    def truncate(self, length: int = 0) -> Awaitable[None]:
        return self._run_in_thread(
            os.ftruncate, self.fileno(), length,
        )

    def __del__(self) -> None:
        if self._fileno > 0:
            os.close(self._fileno)


ContextStoreType = Dict[asyncio.AbstractEventLoop, caio.AsyncioContext]
DEFAULT_CONTEXT_STORE: ContextStoreType = {}


def create_context(
    max_requests: int = caio.AsyncioContext.MAX_REQUESTS_DEFAULT,
) -> caio.AsyncioContext:
    loop = asyncio.get_event_loop()
    context = caio.AsyncioContext(max_requests, loop=loop)

    def finalizer() -> None:
        context.close()
        DEFAULT_CONTEXT_STORE.pop(context, None)

    finalize(loop, finalizer)
    DEFAULT_CONTEXT_STORE[loop] = context
    return context


def get_default_context() -> caio.AsyncioContext:
    loop = asyncio.get_event_loop()
    context = DEFAULT_CONTEXT_STORE.get(loop)

    if context is not None:
        return context

    return create_context()
