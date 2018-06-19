import asyncio
import sys
from functools import partial


PY_35 = sys.version_info >= (3, 5)


def run_in_thread(func, *args, **kwargs) -> asyncio.Future:
    loop = kwargs.pop('loop', None) or asyncio.get_event_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


class Reader:
    __slots__ = '__chunk_size', '__offset', '__aio_file'

    def __init__(self, aio_file, offset: int = 0, chunk_size: int = 32 * 1024):
        self.__chunk_size = int(chunk_size)
        self.__offset = int(offset)
        self.__aio_file = aio_file

    if PY_35:
        @asyncio.coroutine
        def __anext__(self):
            chunk = yield from self.__aio_file.read(self.__chunk_size, self.__offset)
            chunk_size = len(chunk)
            self.__offset += chunk_size

            if chunk_size == 0:
                raise StopAsyncIteration(chunk)

            return chunk

        def __aiter__(self):
            return self

    @asyncio.coroutine
    def __next__(self):
        chunk = yield from self.__aio_file.read(self.__chunk_size, self.__offset)
        chunk_size = len(chunk)
        self.__offset += chunk_size

        return chunk

    def __iter__(self):
        return self


class Writer:
    __slots__ = '__chunk_size', '__offset', '__aio_file'

    def __init__(self, aio_file, offset: int = 0):
        self.__offset = int(offset)
        self.__aio_file = aio_file

    @asyncio.coroutine
    def __call__(self, data):
        yield from self.__aio_file.write(data, self.__offset)
        self.__offset += len(data)
