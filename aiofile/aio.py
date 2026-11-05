import asyncio
import errno
import os
import stat
from collections import namedtuple
from concurrent.futures import Executor
from functools import partial, wraps
from os import strerror
from pathlib import Path
from typing import (
    Any, Awaitable, BinaryIO, Callable, Dict, Generator, Optional, Protocol,
    TextIO, TypeVar, Union, cast, runtime_checkable,
)
from weakref import finalize

import caio
from caio.asyncio_base import AsyncioContextBase


_T = TypeVar("_T")

AIO_FILE_NOT_OPENED = -1
AIO_FILE_CLOSED = -2

FileIOType = Union[TextIO, BinaryIO]

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


def parse_mode(mode: str) -> FileMode:    # noqa: C901
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


@runtime_checkable
class _FileOperations(Protocol):
    FILE_OPERATIONS_VERSION: int
    OPERATION_CLASS: Any

    async def submit(self, op: Any) -> Any:
        ...

    async def open(self, path: str, flags: int, mode: int = 0o666) -> int:
        ...

    async def stat(self, path: Union[str, int]) -> os.stat_result:
        ...

    async def truncate(self, fd: int, length: int = 0) -> None:
        ...


class AIOFile:
    _file_obj: Optional[FileIOType]
    _file_obj_owner: bool
    _encoding: str
    _executor: Optional[Executor]
    mode: FileMode
    __open_result: "Optional[asyncio.Future[FileIOType]]"

    def __init__(
        self, filename: Union[str, Path],
        mode: str = "r", encoding: str = "utf-8",
        context: Optional[AsyncioContextBase] = None,
        executor: Optional[Executor] = None,
    ):
        self.__context = context or get_default_context()
        self.__open_result = None
        self._file_context = (
            self.__context
            if isinstance(self.__context, _FileOperations)
            else None
        )
        if (
            self._file_context is not None
            and self._file_context.FILE_OPERATIONS_VERSION != 1
        ):
            raise ValueError("Unsupported native file operations contract")
        self._native_fd: Optional[int] = None
        self._native_ready = False
        self._native_finalizer: Optional[finalize] = None
        self._native_closed = False
        self._native_lock = asyncio.Lock()

        self._fname = str(filename)
        self._open_mode = mode

        self.mode = parse_mode(mode)

        self._file_obj = None
        self._file_obj_owner = True
        self._encoding = encoding
        self._executor = executor
        self._clone_lock = asyncio.Lock()
        self._clones = 0

    @classmethod
    def from_fp(cls, fp: FileIOType, **kwargs: Any) -> "AIOFile":
        afp = cls(fp.name, fp.mode, **kwargs)
        afp._file_obj = fp
        afp._open_mode = fp.mode
        afp._file_obj_owner = False
        return afp

    def _run_in_thread(
            self, func: "Callable[..., _T]", *args: Any, **kwargs: Any,
    ) -> "asyncio.Future[_T]":
        return self.__context.loop.run_in_executor(
            self._executor, partial(func, *args, **kwargs),
        )

    @property
    def name(self) -> str:
        return self._fname

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self.__context.loop

    @property
    def encoding(self) -> str:
        return self._encoding

    @property
    def closed(self) -> bool:
        if self._file_context is not None and self._file_obj is None:
            return self._native_fd is None
        return self._file_obj is None or self._file_obj.closed

    async def _open_native(self) -> Optional[int]:
        context = self._file_context
        if context is None:
            raise asyncio.InvalidStateError("Native file context missing")
        async with self._native_lock:
            if self._native_closed:
                raise asyncio.InvalidStateError("AIOFile closed")
            if self._native_fd is not None:
                if self._native_ready:
                    return None
                # A failed open whose cleanup close was rejected still owns
                # its descriptor: retry that cleanup before opening again.
                await self._complete_cleanup(
                    self._cleanup_failed_open(self._native_fd),
                )
            mode = self._open_mode
            if (
                any(char not in "axrwbt+" for char in mode)
                or sum(mode.count(char) for char in "axrw") != 1
                or mode.count("b") + mode.count("t") > 1
            ):
                raise ValueError("invalid mode: %r" % mode)
            fd = await context.open(self._fname, self.mode.flags | os.O_CLOEXEC)
            self._native_fd = fd
            try:
                self._native_finalizer = finalize(self, os.close, fd)
                result = await context.stat(fd)
                if stat.S_ISDIR(result.st_mode):
                    raise IsADirectoryError(
                        errno.EISDIR, strerror(errno.EISDIR), self._fname,
                    )
            except BaseException as error:
                await self._complete_cleanup(
                    self._cleanup_failed_open(fd),
                    cancellation=(
                        error if isinstance(error, asyncio.CancelledError)
                        else None
                    ),
                )
                raise
            self._native_ready = True
            return fd

    async def open(self) -> Optional[int]:
        if self._file_context is not None and self._file_obj is None:
            return await self._open_native()

        if self._file_obj is not None:
            if self._file_obj.closed:
                raise asyncio.InvalidStateError("AIOFile closed")
            return None

        if self.__open_result is None:
            self.__open_result = cast(
                "asyncio.Future[FileIOType]",
                self._run_in_thread(open, self._fname, self._open_mode),
            )
            self._file_obj = await self.__open_result
            self.__open_result = None
            return self._file_obj.fileno()

        await self.__open_result
        return None

    def __repr__(self) -> str:
        return "<AIOFile: %r>" % self._fname

    async def clone(self) -> "AIOFile":
        """Returns self with a ref-count bump; close() is deferred until all
        clones are released."""
        async with self._clone_lock:
            self._clones += 1
            return self

    async def _cleanup_failed_open(self, fd: int) -> None:
        await self._close_native_fd(fd)
        self._native_closed = False

    async def _complete_cleanup(
        self, operation: Awaitable[None], *,
        cancellation: asyncio.CancelledError | None = None,
    ) -> None:
        try:
            cleanup = asyncio.ensure_future(operation, loop=self.loop)
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError as exc:
                    if cancellation is None:
                        cancellation = exc
            cleanup.result()
        except BaseException as error:
            if cancellation is not None:
                raise cancellation from error
            raise
        if cancellation is not None:
            raise cancellation

    def _release_native_ownership(self) -> None:
        finalizer = self._native_finalizer
        self._native_finalizer = None
        self._native_fd = None
        self._native_ready = False
        self._native_closed = True
        if finalizer is not None:
            finalizer.detach()

    async def _close_native_fd(self, fd: int) -> None:
        context = self._file_context
        if context is None:
            raise asyncio.InvalidStateError("Native file context missing")
        try:
            if self.mode.writable:
                await self.__context.fdsync(fd)
        finally:
            # Own the operation so its fd_closed disposition stays readable
            # even if constructing CloseError or bookkeeping fails.
            op = context.OPERATION_CLASS.close(fd)
            try:
                await context.submit(op)
            finally:
                if op.fd_closed is not False:
                    self._release_native_ownership()

    async def close(self) -> None:
        if self._file_context is not None and self._file_obj is None:
            async with self._native_lock:
                if self._native_fd is None:
                    return
                async with self._clone_lock:
                    if self._clones > 0:
                        self._clones -= 1
                        return
                await self._complete_cleanup(
                    self._close_native_fd(self._native_fd),
                )
            return
        if (
            self._file_obj is None
            or not self._file_obj_owner
            or self._file_obj.closed
        ):
            return

        async with self._clone_lock:
            if self._clones > 0:
                self._clones -= 1
                return

        if self.mode.writable:
            await self.fdsync()

        await self._run_in_thread(self._file_obj.close)

    def fileno(self) -> int:
        if self._native_fd is not None:
            if not self._native_ready:
                raise asyncio.InvalidStateError(
                    "Native file open did not complete",
                )
            return self._native_fd
        if self._file_obj is None:
            raise asyncio.InvalidStateError("AIOFile closed")
        return self._file_obj.fileno()

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

    async def read_bytes(self, size: int = -1, offset: int = 0) -> bytes:
        if size < -1:
            raise ValueError("Unsupported value %d for size" % size)

        if size == -1:
            size = (await self.stat()).st_size

        return await self.__context.read(size, self.fileno(), offset)

    async def stat(self) -> os.stat_result:
        fd = self.fileno()
        if self._file_context is not None:
            return await self._file_context.stat(fd)
        return await self._run_in_thread(os.stat, fd)

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

    async def _truncate_native(self, length: int) -> None:
        context = self._file_context
        if context is None:
            raise asyncio.InvalidStateError("Native file context missing")
        await context.truncate(self.fileno(), length)

    def truncate(self, length: int = 0) -> Awaitable[None]:
        if self._file_context is not None:
            self.fileno()
            return self._truncate_native(length)
        return self._run_in_thread(
            os.ftruncate, self.fileno(), length,
        )


# Keyed by id(loop) so the keys themselves do not retain event loops. Values
# still retain their loops through caio contexts, so create_context() also
# installs synchronous cleanup into loop.close(). This is important for
# short-lived loops: waiting for garbage collection would keep native AIO
# resources (and io_uring SQPOLL threads) alive between loop instances.
ContextStoreType = Dict[int, caio.AsyncioContext]
DEFAULT_CONTEXT_STORE: ContextStoreType = {}


def _release_context(loop_id: int) -> None:
    context = DEFAULT_CONTEXT_STORE.pop(loop_id, None)
    if context is not None:
        context.close()


def _install_context_cleanup(
    loop: asyncio.AbstractEventLoop,
) -> None:
    original_close = loop.close
    loop_id = id(loop)

    @wraps(original_close)
    def close() -> None:
        if loop.is_running():
            original_close()
            return

        try:
            _release_context(loop_id)
        finally:
            original_close()

    try:
        setattr(loop, "close", close)
    except (AttributeError, TypeError):
        # Some third-party event loops do not allow instance attributes.
        # The finalizer below still provides interpreter-shutdown cleanup.
        pass


def create_context(
    max_requests: int = caio.AsyncioContext.MAX_REQUESTS_DEFAULT,
) -> caio.AsyncioContext:
    loop = asyncio.get_event_loop()
    context = caio.AsyncioContext(max_requests, loop=loop)

    _install_context_cleanup(loop)
    finalize(loop, _release_context, id(loop))
    DEFAULT_CONTEXT_STORE[id(loop)] = context
    return context


def get_default_context() -> caio.AsyncioContext:
    loop = asyncio.get_event_loop()
    context = DEFAULT_CONTEXT_STORE.get(id(loop))

    if context is not None:
        return context

    return create_context()
