import asyncio
import os
import sys
from enum import IntEnum
from threading import RLock
from multiprocessing.pool import ThreadPool
from queue import PriorityQueue

try:
    from typing import Awaitable
except ImportError:
    from types import GeneratorType as Awaitable

PY_35 = sys.version_info >= (3, 5)


THREAD_POOL = ThreadPool()


class OperationState(IntEnum):
    READ = 0
    WRITE = 1
    NOP = 2


class AIOFile:
    def __init__(self, filename: str, mode: str="r",
                 access_mode: int=0x644, loop: asyncio.AbstractEventLoop=None):

        is_new = os.path.exists(filename)

        self.__file = open(filename, mode=mode)

        if is_new:
            os.chmod(filename, access_mode)

        self.__queue = PriorityQueue()
        self._lock = RLock()
        self.__loop = loop

    def __repr__(self):
        return "<AIOThreadFile: %r>" % self.__file.name

    def read(self, size: int=-1, offset: int=0, priority: int=0) -> AIOThreadOperation:
        return AIOThreadOperation(
            OperationState.READ, self, offset=offset,
            reqprio=priority, nbytes=size, loop=self.__loop
        )

    def write(self, data: (str, bytes), offset: int=0, priority: int=0) -> AIOThreadOperation:
        op = AIOThreadOperation(
            OperationState.WRITE, self, offset=offset,
            reqprio=priority, nbytes=len(data), loop=self.__loop
        )

        op.buffer = data
        return op

    def flush(self, priority:int=0) -> AIOThreadOperation:
        return AIOThreadOperation(OperationState.NOP, self, 0, 0, priority, self.__loop)


class AIOThreadOperation:
    __slots__ = ('__state', '__aio_file', '__offset',
                 '__nbytes', '__reqprio', '__loop',
                 '__buffer', '__closed', '__is_running')

    def __init__(self, state: int, aio_file: AIOFile, offset: int,
                 nbytes: int, reqprio: int, loop: asyncio.AbstractEventLoop):
        self.__state = state
        self.__aio_file = aio_file
        self.__offset = offset
        self.__nbytes = nbytes
        self.__reqprio = reqprio
        self.__buffer = b''
        self.__loop = loop
        self.__closed = False
        self.__is_running = False

    def run(self):
        pass

    def poll(self) -> bool:
        pass

    def result(self) -> bytes:
        pass

    def __len__(self) -> int:
        pass

    def __repr__(self) -> str:
        if self._state == OperationState.READ:
            op = 'read'
        elif self._state == OperationState.WRITE:
            op = 'write'
        elif self._state == OperationState.NOP:
            op = 'fsync'
        else:
            op = 'uknown'

        if not self.is_running:
            state = 'not started'
        elif self.poll():
            state = 'pending'
        elif self.closed:
            state = 'closed'
        else:
            state = 'done'

        return "<AIOThreadOperation(%r): %s>" % (op, state)

    def __check_closed(self):
        if self.__closed:
            raise RuntimeError("Can't perform operation on closed operation")

    @property
    def nbytes(self) -> int:
        return self.__nbytes

    @property
    def offset(self) -> int:
        return self.__offset

    @property
    def reqprio(self) -> int:
        return self.__reqprio

    @property
    def buffer(self) -> bytes:
        return self.__buffer

    @buffer.setter
    def buffer(self, data: bytes):
        self.__buffer = data

    @property
    def is_running(self):
        return self.__is_running

    def close(self):
        self.__closed = True
        self.__buffer = b''

    @property
    def closed(self) -> bool:
        return self.__closed

    def __iter__(self) -> "AIOThreadOperation":
        pass

    if PY_35:
        def __await__(self) -> Awaitable:
            pass

    __next__ = __await__
