import asyncio
import io
import typing
from collections.abc import AsyncIterable
from types import MappingProxyType

from .aio import AIOFile


ENCODING_MAP = MappingProxyType({
    "utf-8": 4,
    "utf-16": 8,
})


async def unicode_reader(
    afp: AIOFile, chunk_size: int, offset: int, encoding: str = 'utf-8'
) -> typing.Tuple[int, str]:

    last_error = None
    for retry in range(ENCODING_MAP.get(encoding, 4)):
        chunk_bytes = await afp.read_bytes(chunk_size + retry, offset)
        try:
            chunk = chunk_bytes.decode()
            break
        except UnicodeDecodeError as e:
            last_error = e
    else:
        raise last_error

    chunk_size = len(chunk_bytes)

    return chunk_size, chunk


class Reader(AsyncIterable):
    __slots__ = "_chunk_size", "__offset", "file", "__lock", "encoding"

    CHUNK_SIZE = 32 * 1024

    def __init__(self, aio_file: AIOFile, offset: int = 0,
                 chunk_size: int = CHUNK_SIZE):

        self.__lock = asyncio.Lock()
        self.__offset = int(offset)

        self._chunk_size = int(chunk_size)
        self.file = aio_file
        self.encoding = self.file.encoding

    async def read_chunk(self) -> typing.Union[str, bytes]:
        async with self.__lock:
            if self.file.mode.binary:
                chunk = await self.file.read_bytes(
                    self._chunk_size, self.__offset
                )
                chunk_size = len(chunk)
            else:
                chunk_size, chunk = await unicode_reader(
                    self.file, self._chunk_size, self.__offset,
                    encoding=self.encoding
                )
        self.__offset += chunk_size
        return chunk

    async def __anext__(self):
        chunk = await self.read_chunk()

        if not chunk:
            raise StopAsyncIteration(chunk)

        return chunk

    def __aiter__(self):
        return self


class Writer:
    __slots__ = "__chunk_size", "__offset", "__aio_file", "__lock"

    def __init__(self, aio_file: AIOFile, offset: int = 0):
        self.__offset = int(offset)
        self.__aio_file = aio_file
        self.__lock = asyncio.Lock()

    async def __call__(self, data):
        async with self.__lock:
            if isinstance(data, str):
                data = self.__aio_file.encode_bytes(data)

            await self.__aio_file.write_bytes(data, self.__offset)
            self.__offset += len(data)


class LineReader(AsyncIterable):
    CHUNK_SIZE = 4192

    def __init__(
        self, aio_file: AIOFile, offset: int = 0,
        chunk_size: int = CHUNK_SIZE, line_sep: str = "\n",
    ):
        self.__reader = Reader(aio_file, chunk_size=chunk_size, offset=offset)

        self._buffer = (
            io.BytesIO() if aio_file.mode.binary else io.StringIO()
        )   # type: typing.Any

        self.linesep = (
            aio_file.encode_bytes(line_sep)
            if aio_file.mode.binary
            else line_sep
        )

    async def readline(self) -> typing.Union[str, bytes]:
        while True:
            chunk = await self.__reader.read_chunk()

            if chunk:
                if self.linesep not in chunk:
                    self._buffer.write(chunk)
                    continue

                self._buffer.write(chunk)

            self._buffer.seek(0)
            line = self._buffer.readline()
            tail = self._buffer.read()

            self._buffer.seek(0)
            self._buffer.truncate(0)
            self._buffer.write(tail)

            return line

    async def __anext__(self) -> typing.Union[bytes, str]:
        line = await self.readline()

        if not line:
            raise StopAsyncIteration(line)

        return line

    def __aiter__(self) -> "LineReader":
        return self


class FileIOWrapperBase:
    def __init__(self, afp: AIOFile, *, offset: int = 0):
        self.__offset = offset
        self._lock = asyncio.Lock()
        self.file = afp

    def tell(self) -> int:
        return self.__offset

    async def close(self):
        await self.file.close()


class BinaryFileWrapper(FileIOWrapperBase):
    def __init__(self, afp: AIOFile):
        if not afp.mode.binary:
            raise ValueError("Expected file in binary mode")
        super().__init__(afp)

    async def read(self, length) -> bytes:
        async with self._lock:
            data = await self.file.read_bytes(length, self.__offset)
            self.__offset += len(data)
        return data

    async def write(self, data: bytes) -> None:
        async with self._lock:
            operation = self.file.write_bytes(data, self.__offset)
            self.__offset += len(data)
        await operation


class TextFileWrapper(FileIOWrapperBase):
    def __init__(self, afp: AIOFile):
        if afp.mode.binary:
            raise ValueError("Expected file in text mode")
        super().__init__(afp)
        self.encoding = self.file.encoding

    async def read(self, length) -> str:
        async with self._lock:
            chunk_size, chunk = await unicode_reader(
                self.file, length, self.__offset, self.encoding
            )
            self.__offset += chunk_size
        return chunk

    async def write(self, data: str) -> None:
        async with self._lock:
            data_bytes = data.encode(self.encoding)
            operation = self.file.write_bytes(data_bytes, self.__offset)
            self.__offset += len(data_bytes)

        await operation


__all__ = (
    "BinaryFileWrapper",
    "FileIOWrapperBase",
    "LineReader",
    "Reader",
    "TextFileWrapper",
    "Writer",
    "unicode_reader",
)
