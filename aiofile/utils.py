import asyncio
import sys


PY_35 = sys.version_info >= (3, 5)


class Reader:
    __slots__ = '__chunk_size', '__offset', '__aio_file', '__priority'

    def __init__(self, aio_file, offset: int = 0, chunk_size: int = 32 * 1024, priority: int = 0):
        self.__chunk_size = int(chunk_size)
        self.__offset = int(offset)
        self.__aio_file = aio_file
        self.__priority = int(priority)

    if PY_35:
        @asyncio.coroutine
        def __anext__(self):
            chunk = yield from self.__aio_file.read(self.__chunk_size, self.__offset, self.__priority)
            chunk_size = len(chunk)
            self.__offset += chunk_size

            if chunk_size == 0:
                raise StopAsyncIteration(chunk)

            return chunk

        def __aiter__(self):
            return self

    @asyncio.coroutine
    def __next__(self):
        chunk = yield from self.__aio_file.read(self.__chunk_size, self.__offset, self.__priority)
        chunk_size = len(chunk)
        self.__offset += chunk_size

        return chunk

    def __iter__(self):
        return self


class Writer:
    __slots__ = '__chunk_size', '__offset', '__aio_file', '__priority'

    def __init__(self, aio_file, offset: int = 0, priority: int = 0):
        self.__offset = int(offset)
        self.__aio_file = aio_file
        self.__priority = int(priority)

    @asyncio.coroutine
    def __call__(self, data):
        yield from self.__aio_file.write(data, self.__offset, self.__priority)
        self.__offset += len(data)
