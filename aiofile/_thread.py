import asyncio
import os

try:
    from typing import Awaitable
except ImportError:
    from types import GeneratorType as Awaitable


class AIOFile:
    def __init__(self, filename: str, mode: str="r",
                 access_mode: int=0x644, loop: asyncio.AbstractEventLoop=None):

        is_new = os.path.exists(filename)

        self.__file = open(filename, mode=mode)

        if is_new:
            os.chmod(filename, access_mode)

        self.__loop = loop


    def __repr__(self):
        pass

    def read(self, size: int=-1, offset: int=0, priority: int=0) -> AIOOperation:
        pass

    def write(self, data: (str, bytes), offset:int=0, priority:int=0) -> AIOOperation:
        pass

    def flush(self, priority:int=0) -> AIOOperation:
        pass


class AIOOperation:
    def __init__(self, state: int, aio_file: AIOFile, offset: int, nbytes: int, reqprio: int):
        pass

    def run(self):
        pass

    def poll(self) -> bool:
        pass

    def result(self) -> bytes:
        pass

    def close(self):
        pass

    def __len__(self) -> int:
        pass

    def __repr__(self) -> str:
        pass

    @property
    def nbytes(self) -> int:
        pass

    @property
    def offset(self) -> int:
        pass

    @property
    def reqprio(self) -> int:
        pass

    @property
    def buffer(self) -> bytes:
        pass

    @buffer.setter
    def buffer(self, data: bytes):
        pass

    @property
    def closed(self) -> bool:
        pass

    def __await__(self) -> Awaitable:
        pass

    def __iter__(self) -> "AIOOperation":
        pass

    __next__ = __await__
