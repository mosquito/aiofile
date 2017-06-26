import asyncio
import sys
from .aio import AIOFile

PY_35 = sys.version_info >= (3, 5)


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


class FileWrapper:
    __slots__ = '__file', '__loop', '__offset', '__lock'

    CHUNK_SIZE = 32 * 1024

    def __init__(self, filename: str, mode: str = "r", access_mode: int = 0o644, loop=None):
        self.__loop = loop or asyncio.get_event_loop()
        self.__file = AIOFile(filename, mode, access_mode, self.__loop)
        self.__offset = 0
        self.__lock = asyncio.Lock(loop=self.__loop)

    @asyncio.coroutine
    def read(self, size=None):
        with (yield from self.__lock):
            data = yield from self.__file.read(size, offset=self.__offset)
            self.__offset += len(data)

        return data

    @asyncio.coroutine
    def write(self, data):
        with (yield from self.__lock):
            yield from self.__aio_file.write(data, self.__offset)
            self.__offset += len(data)

    @asyncio.coroutine
    def tell(self):
        return self.__offset

    @asyncio.coroutine
    def seek(self, value):
        self.__offset = value

    @asyncio.coroutine
    def close(self):
        self.__file.close()

    def __del__(self):
        self.__file.close()

    if PY_35:
        @asyncio.coroutine
        def __anext__(self):
            return self

        @asyncio.coroutine
        def __aexit__(self, exc_type, exc_val, exc_tb):
            yield from self.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__file.close()
