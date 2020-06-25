import asyncio
import hashlib
import json
import os
from base64 import b64encode
from io import BytesIO
from random import shuffle
from uuid import uuid4

import pytest

from aiofile.utils import LineReader, Reader, Writer

from .impl import split_by


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


async def test_read_offset(aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        for _ in range(10):
            f.write(uuid)

    aio_file = await aio_file_maker(temp_file, "r")

    data = await aio_file.read(
        offset=len(uuid),
        size=len(uuid),
    )

    assert data == uuid


async def test_read_write_offset(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, "r")
    w_file = await aio_file_maker(temp_file, "w")

    for i in range(10):
        await w_file.write(uuid, offset=i * len(uuid))

    await w_file.fsync()

    data = await r_file.read(
        offset=len(uuid),
        size=len(uuid),
    )

    assert data == uuid


async def test_reader_writer(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, "rb")
    w_file = await aio_file_maker(temp_file, "wb")

    chunk_size = 16
    count = 10
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


async def test_reader_writer2(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, "r")
    w_file = await aio_file_maker(temp_file, "w")

    writer = Writer(w_file)

    for _ in range(100):
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


async def test_parallel_writer_ordering(aio_file_maker, temp_file, uuid):
    w_file = await aio_file_maker(temp_file, "wb")
    r_file = await aio_file_maker(temp_file, "rb")

    count = 1000
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


async def test_line_reader(aio_file_maker, temp_file, uuid):
    afp = await aio_file_maker(temp_file, "w+")

    writer = Writer(afp)

    max_length = 1000
    chunk = b64encode(os.urandom(max_length)).decode()
    lines = [chunk[:i] for i in range(max_length)]

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


async def test_line_reader_one_line(aio_file_maker, temp_file):
    afp = await aio_file_maker(temp_file, "w+")

    writer = Writer(afp)

    payload = " ".join(uuid4().hex for _ in range(1000))

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


async def test_modes(aio_file_maker, tmpdir):
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

    data = dict((str(i), i)for i in range(1000))

    tmpfile = tmpdir.join("test.json")
    async with aio_file_maker(tmpfile, "w") as afp:
        await afp.write(json.dumps(data, indent=1))

    async with aio_file_maker(tmpfile, "r") as afp:
        result = json.loads(await afp.read())

    assert result == data


async def test_unicode_reader(aio_file_maker, temp_file):
    async with aio_file_maker(temp_file, 'w+') as afp:
        await afp.write('한글')

    async with aio_file_maker(temp_file, 'r') as afp:
        reader = Reader(afp, chunk_size=1)
        assert await reader.read_chunk() == '한'
        assert await reader.read_chunk() == '글'


async def test_unicode_writer(aio_file_maker, temp_file):
    async with aio_file_maker(temp_file, 'w+') as afp:
        writer = Writer(afp)
        await writer('한')
        await writer('글')

    async with aio_file_maker(temp_file, 'r') as afp:
        reader = Reader(afp, chunk_size=1)
        assert await reader.read_chunk() == '한'
        assert await reader.read_chunk() == '글'
