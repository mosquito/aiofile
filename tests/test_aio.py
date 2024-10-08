import asyncio
import hashlib
import json
import os
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from random import shuffle
from unittest.mock import Mock, call
from uuid import uuid4

import caio  # type: ignore
import pytest  # type: ignore

from aiofile import AIOFile
from aiofile.utils import (
    BinaryFileWrapper, LineReader, Reader, TextFileWrapper, Writer,
)

from .impl import split_by


class CoroutineMock(Mock):
    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)


@pytest.fixture
def temp_file(tmp_path):
    path = str(tmp_path / "file.bin")
    with open(path, "wb"):
        pass

    return path


@pytest.fixture
def uuid(tmp_path):
    return str(uuid4())


async def test_read(aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        f.write(uuid)

    aio_file = await aio_file_maker(temp_file, "r")

    data = await aio_file.read()

    assert data == uuid


async def test_read_write(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, "r")
    w_file = await aio_file_maker(temp_file, "w")

    await w_file.write(uuid)
    await w_file.fsync()

    data = await r_file.read()

    assert data == uuid


async def test_read_write_pathlib(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(Path(temp_file), "r")
    w_file = await aio_file_maker(Path(temp_file), "w")

    await w_file.write(uuid)
    await w_file.fsync()

    data = await r_file.read()

    assert data == uuid


@pytest.mark.parametrize("count", [2, 3, 5, 10, 20, 100])
async def test_read_offset(count, aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        for _ in range(count):
            f.write(uuid)

    aio_file = await aio_file_maker(temp_file, "r")

    data = await aio_file.read(
        offset=len(uuid),
        size=len(uuid),
    )

    assert data == uuid


@pytest.mark.parametrize("count", [2, 3, 5, 10, 20, 100])
async def test_read_write_offset(count, aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, "r")
    w_file = await aio_file_maker(temp_file, "w")

    for i in range(count):
        await w_file.write(uuid, offset=i * len(uuid))

    await w_file.fsync()

    data = await r_file.read(
        offset=len(uuid),
        size=len(uuid),
    )

    assert data == uuid


@pytest.mark.parametrize("count", [1, 2, 3, 5, 10, 20, 100])
async def test_reader_writer(count, aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, "rb")
    w_file = await aio_file_maker(temp_file, "wb")

    chunk_size = 16
    payload = os.urandom(chunk_size * count)
    writer = Writer(w_file)

    await writer(payload)
    await w_file.fsync()

    buff = BytesIO(payload)

    async for chunk in Reader(r_file, chunk_size=chunk_size):
        if not chunk:
            break

        assert chunk == buff.read(chunk_size)

    assert not buff.read()


@pytest.mark.parametrize("count", [1, 2, 3, 5, 10, 20, 100, 1000])
async def test_reader_writer2(count, aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, "r")
    w_file = await aio_file_maker(temp_file, "w")

    writer = Writer(w_file)

    for _ in range(count):
        await writer(uuid)

    await w_file.fsync()

    async for chunk in Reader(r_file, chunk_size=len(uuid)):
        assert chunk == uuid


async def test_parallel_writer(aio_file_maker, temp_file, uuid):
    w_file = await aio_file_maker(temp_file, "w")
    r_file = await aio_file_maker(temp_file, "r")

    futures = list()

    for i in range(1000):
        futures.append(w_file.write(uuid, i * len(uuid)))

    shuffle(futures)

    await asyncio.gather(*futures)
    await w_file.fsync()

    count = 0
    async for chunk in Reader(r_file, chunk_size=len(uuid)):
        assert chunk == uuid
        count += 1

    assert count == 1000


@pytest.mark.parametrize("count", [1, 2, 3, 5, 10, 20, 100, 1000])
async def test_parallel_writer_ordering(
    count, aio_file_maker, temp_file, uuid,
):
    w_file = await aio_file_maker(temp_file, "wb")
    r_file = await aio_file_maker(temp_file, "rb")

    chunk_size = 1024

    data = os.urandom(chunk_size * count)

    futures = list()

    for idx, chunk in enumerate(split_by(data, chunk_size)):
        futures.append(w_file.write(chunk, idx * chunk_size))

    shuffle(futures)

    await asyncio.gather(*futures)
    await w_file.fsync()

    result = b""

    async for chunk in Reader(r_file, chunk_size=chunk_size):
        result += chunk

    assert data == result


async def test_non_existent_file_ctx(aio_file_maker):
    with pytest.raises(FileNotFoundError):
        async with aio_file_maker("/c/windows/NonExistent.file", "r"):
            pass


async def test_sequential_open(aio_file_maker, temp_file):
    file = aio_file_maker(temp_file, "r")
    try:
        assert isinstance(await file.open(), int)
        assert await file.open() is None
    finally:
        await file.close()

    with pytest.raises(asyncio.InvalidStateError):
        await file.open()


async def test_parallel_open(aio_file_maker, temp_file):
    file = aio_file_maker(temp_file, "r")
    try:
        # open() returns a file descriptor if it has opened a file, otherwise
        # None. Only one of multiple parallel calls should actually open a file.
        opens = await asyncio.gather(*(file.open() for _ in range(3)))
        assert len([fd for fd in opens if fd is not None]) == 1
    finally:
        await file.close()


async def test_line_reader(aio_file_maker, temp_file, uuid):
    afp = await aio_file_maker(temp_file, "w+")

    writer = Writer(afp)

    max_length = 1000
    chunk = b64encode(os.urandom(max_length)).decode()
    lines = [chunk[:i] for i in range(max_length)]

    line: str | bytes

    for line in lines:
        await writer(line)
        await writer("\n")

    await afp.fsync()
    read_lines = []

    async for line in LineReader(afp):
        read_lines.append(line[:-1])

    def hash_data(data_lines):
        return hashlib.md5("\n".join(data_lines).encode()).hexdigest()

    assert hash_data(read_lines) == hash_data(lines)


@pytest.mark.parametrize("size", [1, 2, 3, 5, 10, 20, 100, 1000, 2000, 5000])
async def test_line_reader_one_line(size, aio_file_maker, temp_file):
    afp = await aio_file_maker(temp_file, "w+")

    writer = Writer(afp)

    payload = " ".join(uuid4().hex for _ in range(size))

    await writer(payload)

    read_lines = []

    async for line in LineReader(afp):
        read_lines.append(line)

    assert payload == read_lines[0]


async def test_truncate(aio_file_maker, temp_file):
    afp = await aio_file_maker(temp_file, "w+")

    await afp.write("hello")
    await afp.fsync()

    assert (await afp.read()) == "hello"

    await afp.truncate(0)

    assert (await afp.read()) == ""


@pytest.mark.parametrize("size", [1, 2, 3, 5, 10, 20, 100, 1000, 2000, 5000])
async def test_modes(size, aio_file_maker, tmpdir):
    tmpfile = tmpdir.join("test.txt")

    async with aio_file_maker(tmpfile, "w") as afp:
        await afp.write("foo")
        await afp.fsync()

    async with aio_file_maker(tmpfile, "r") as afp:
        assert await afp.read() == "foo"

    async with aio_file_maker(tmpfile, "a+") as afp:
        assert await afp.read() == "foo"

    async with aio_file_maker(tmpfile, "r+") as afp:
        assert await afp.read() == "foo"

    data = dict((str(i), i)for i in range(size))

    tmpfile = tmpdir.join("test.json")
    async with aio_file_maker(tmpfile, "w") as afp:
        await afp.write(json.dumps(data, indent=1))

    async with aio_file_maker(tmpfile, "r") as afp:
        result = json.loads(await afp.read())

    assert result == data


async def test_unicode_reader(aio_file_maker, temp_file):
    async with aio_file_maker(temp_file, "w+") as afp:
        await afp.write("한글")

    async with aio_file_maker(temp_file, "r") as afp:
        reader = Reader(afp, chunk_size=1)
        assert await reader.read_chunk() == "한"
        assert await reader.read_chunk() == "글"


async def test_unicode_writer(aio_file_maker, temp_file):
    async with aio_file_maker(temp_file, "w+") as afp:
        writer = Writer(afp)
        await writer("한")
        await writer("글")

    async with aio_file_maker(temp_file, "r") as afp:
        reader = Reader(afp, chunk_size=1)
        assert await reader.read_chunk() == "한"
        assert await reader.read_chunk() == "글"


@pytest.mark.parametrize(("mode", "data"), [("w+", ""), ("wb+", b"")])
async def test_write_read_nothing(aio_file_maker, temp_file, mode, data):
    async with aio_file_maker(temp_file, mode) as afp:
        assert await afp.write(data) == 0
        assert await afp.read() == data


async def test_partial_writes(temp_file, event_loop):
    ctx = Mock(caio.AbstractContext)
    ctx.loop = event_loop
    ctx.fdsync = CoroutineMock(return_value=None)
    ctx.write = CoroutineMock(side_effect=asyncio.InvalidStateError)

    async with AIOFile(temp_file, "w", context=ctx) as afp:
        # 1. writing first three bytes then last four
        return_iter = iter((3, 4))
        ctx.write.side_effect = lambda *_, **__: next(return_iter)
        await afp.write("aiofile", offset=0)
        # 2. then writing 12 bytes then one and the last 6.
        return_iter = iter((12, 1, 6))
        ctx.write.side_effect = lambda *_, **__: next(return_iter)
        await afp.write("test_partial_writes", offset=8)

        # compare all chunks against expected
        assert ctx.write.call_args_list == [
            # 1
            call(b"aiofile", afp.fileno(), 0),
            call(b"file", afp.fileno(), 3),
            # 2
            call(b"test_partial_writes", afp.fileno(), 8),
            call(b"_writes", afp.fileno(), 20),
            call(b"writes", afp.fileno(), 21),
        ]


async def test_write_returned_negative(temp_file, event_loop):
    ctx = Mock(caio.AbstractContext)
    ctx.loop = event_loop
    ctx.fdsync = CoroutineMock(return_value=None)
    ctx.write = CoroutineMock(side_effect=asyncio.InvalidStateError)

    async with AIOFile(temp_file, "w", context=ctx) as afp:
        return_iter = iter((3, -27))
        ctx.write.side_effect = lambda *_, **__: next(return_iter)
        with pytest.raises(OSError) as raises:
            await afp.write("aiofile")
        assert raises.value.errno == 27
        assert raises.value.filename == temp_file

        ctx.write.reset_mock(side_effect=True)
        ctx.write.return_value = -122
        with pytest.raises(OSError) as raises:
            await afp.write("aiofile")
        assert raises.value.errno == 122
        assert raises.value.filename == temp_file


async def test_write_returned_zero(temp_file, event_loop):
    ctx = Mock(caio.AbstractContext)
    ctx.loop = event_loop
    ctx.fdsync = CoroutineMock(return_value=None)
    ctx.write = CoroutineMock(side_effect=asyncio.InvalidStateError)

    async with AIOFile(temp_file, "w", context=ctx) as afp:
        return_iter = iter((3, 0))
        ctx.write.side_effect = lambda *_, **__: next(return_iter)
        with pytest.raises(RuntimeError, match="Write operation returned 0"):
            await afp.write("aiofile")

        ctx.write.reset_mock(side_effect=True)
        ctx.write.return_value = 0
        with pytest.raises(RuntimeError, match="Write operation returned 0"):
            await afp.write("aiofile")


async def test_text_io_wrapper(aio_file_maker, temp_file):
    async with aio_file_maker(temp_file, "w+") as afp:
        data = "💾💀"
        await afp.write(data * 5)

    with open(temp_file, "a+", encoding="utf-8") as fp:
        assert not fp.read(1)
        fp.seek(0)
        assert fp.read() == data * 5

        fp.seek(0)
        assert fp.read(1) == "💾"
        assert fp.tell() == 4

    async with TextFileWrapper(aio_file_maker(temp_file, "a+")) as fp:
        assert not await fp.read(1)
        fp.seek(0)
        chunk = await fp.read()
        assert chunk == data * 5

        fp.seek(0)
        assert await fp.read(1) == "💾"
        assert fp.tell() == 4


async def test_binary_io_wrapper(aio_file_maker, temp_file):
    async with aio_file_maker(temp_file, "wb+") as afp:
        data = b"\x01\x02\x03"
        await afp.write(data * 32)

    with open(temp_file, "ab+") as fp:
        assert not fp.read(1)
        fp.seek(0)
        assert fp.read() == data * 32

        fp.seek(0)
        assert fp.read(1) == b"\x01"
        assert fp.tell() == 1

    async with BinaryFileWrapper(aio_file_maker(temp_file, "ab+")) as fp:
        assert not await fp.read(1)
        fp.seek(0)
        assert await fp.read() == data * 32

        fp.seek(0)
        assert await fp.read(1) == b"\x01"
        assert fp.tell() == 1


async def test_async_open(async_open, temp_file):
    async with async_open(temp_file, "wb+") as afp:
        data = b"\x01\x02\x03" + "🦠📱".encode()
        await afp.write(data * 32)

    assert isinstance(async_open(temp_file, "ab+"), BinaryFileWrapper)

    async with async_open(temp_file, "ab+") as fp:
        assert not await fp.read(1)
        fp.seek(3)
        assert await fp.read(8) == "🦠📱".encode()

    assert isinstance(async_open(temp_file, "a+"), TextFileWrapper)

    async with async_open(temp_file, "a+") as fp:
        assert not await fp.read(1)
        fp.seek(3)
        assert await fp.read(2) == "🦠📱"


async def test_async_open_unicode(aio_file_maker, async_open, temp_file):
    async with aio_file_maker(temp_file, "w+") as afp:
        data = "🏁💾🏴‍☠️"
        await afp.write(data)

    async with async_open(temp_file, "a+") as afp:
        with open(temp_file, "a+", encoding="utf-8") as fp:
            assert not await afp.read(1)
            assert not fp.read(1)

            afp.seek(0)
            fp.seek(0)

            assert await afp.read(1) == fp.read(1)
            assert await afp.read(1) == fp.read(1)
            assert await afp.read(1) == fp.read(1)
            assert await afp.read(1) == fp.read(1)

            afp.seek(0)
            fp.seek(0)

            assert await afp.read(3) == fp.read(3)
            assert afp.tell() == fp.tell()


async def test_async_open_readline(aio_file_maker, async_open, temp_file):
    async with aio_file_maker(temp_file, "w+") as afp:
        data = "Hello\nworld\n" + ("h" * 10000)
        await afp.write(data)

    async with async_open(temp_file, "a+") as afp:
        with open(temp_file, "a+", encoding="utf-8") as fp:
            afp.seek(0)
            fp.seek(0)

            assert await afp.readline() == fp.readline()
            assert await afp.readline() == fp.readline()
            assert await afp.readline() == fp.readline()

    async with async_open(temp_file, "ab+") as afp:
        with open(temp_file, "ab+") as fp:
            assert not await afp.read(1)
            assert not fp.read(1)

            afp.seek(0)
            fp.seek(0)

            assert await afp.readline() == fp.readline()
            assert await afp.readline() == fp.readline()
            assert await afp.readline() == fp.readline()


async def test_async_open_fp(async_open, tmp_path: Path):
    data = "Hello\nworld\n"

    with open(tmp_path / "file.txt", "w+") as fp:
        async with async_open(fp) as afp:
            await afp.write(data)
            afp.seek(0)
            assert await afp.read() == data
            assert not afp.file.mode.binary

        assert not fp.closed

    with open(tmp_path / "file.txt", "rb") as fp:
        async with async_open(fp) as afp:
            assert afp.file.mode.binary

        assert fp.read().decode() == data


@pytest.mark.parametrize("sizes", [[1, 2], [2, 10], [10, 20], [100, 500]])
async def test_async_open_line_iter(sizes, async_open, tmp_path: Path):
    async with async_open(tmp_path / "file.txt", "w+") as afp:
        for i in range(*sizes):
            await afp.write(str(i))
            await afp.write("\n")

        afp.seek(0)
        idx = sizes[0]
        async for line in afp:
            assert line.endswith("\n")
            assert int(line.strip()) == idx
            idx += 1

    async with async_open(tmp_path / "file.bin", "wb+") as afp:
        for i in range(*sizes):
            await afp.write(str(i).encode())
            await afp.write(b"\n")

        afp.seek(0)
        idx = sizes[0]
        async for line in afp:
            assert line.endswith(b"\n")
            assert int(line.decode().strip()) == idx
            idx += 1


@pytest.mark.parametrize("size", [1, 2, 3, 5, 10, 20, 100, 1000, 2000, 5000])
async def test_async_open_iter_chunked(size, async_open, tmp_path: Path):
    src_path = tmp_path / "src.txt"
    dst_path = tmp_path / "dst.txt"

    async with async_open(src_path, "w") as afp:
        for i in range(0, size):
            await afp.write(str(i))
            await afp.write("\n")

    async with async_open(src_path, "r") as src, \
               async_open(dst_path, "w") as dest:
        async for chunk in src.iter_chunked():
            await dest.write(chunk)

    assert src_path.stat().st_size == dst_path.stat().st_size

    def hash_file(path):
        with open(path, "rb") as fp:
            hasher = hashlib.md5()
            chunk = fp.read(65535)
            while chunk:
                hasher.update(chunk)
                chunk = fp.read(65535)
            return hasher.hexdigest()

    assert hash_file(src_path) == hash_file(dst_path)


async def test_open_non_existent_file_with_append(
    async_open, tmp_path: Path,
):
    tmp_fpath = tmp_path / "numbers.txt"

    async with async_open(tmp_fpath, "a+") as afp:
        for i in range(10):
            await afp.write(str(i))
            await afp.write("\n")

    async with async_open(tmp_fpath, "a+") as afp:
        for i in range(10, 20):
            await afp.write(str(i))
            await afp.write("\n")

    numbers = []
    async with async_open(tmp_fpath, "r") as afp:
        async for line in afp:
            numbers.append(int(line.strip()))

    assert numbers == list(range(20))
