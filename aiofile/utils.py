import asyncio

try:
    StopAsyncIteration
except NameError:
    StopAsyncIteration = StopIteration


class Reader:
    __slots__ = '__chunk_size', '__offset', '__aio_file', '__priority'

    def __init__(self, aio_file, offset: int = 0, chunk_size: int = 32 * 1024, priority: int = 0):
        self.__chunk_size = int(chunk_size)
        self.__offset = int(offset)
        self.__aio_file = aio_file
        self.__priority = int(priority)

    @asyncio.coroutine
    def __next(self, stop_iteration=StopIteration):
        chunk = yield from self.__aio_file.read(self.__chunk_size, self.__offset, self.__priority)
        chunk_size = len(chunk)
        self.__offset += chunk_size

        if chunk_size == 0:
            raise stop_iteration()

        return chunk

    def __anext__(self):
        return self.__next(StopAsyncIteration)

    def __aiter__(self):
        return self

    def __iter__(self):
        yield self.__next(StopAsyncIteration)


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
