import os
import asyncio
from random import shuffle
from uuid import uuid4

from aiofile.utils import Reader, Writer, LineReader
from aiofile.posix_aio import AIOOperation, IO_WRITE, IO_NOP, IO_READ
from .. import *


def posix_aio_file(name, mode):
    AIOFile.OPERATION_CLASS = AIOOperation
    AIOFile.IO_READ = IO_READ
    AIOFile.IO_NOP = IO_NOP
    AIOFile.IO_WRITE = IO_WRITE

    return AIOFile(name, mode)


@aio_impl
async def test_read(aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        f.write(uuid)

    aio_file = await aio_file_maker(temp_file, 'r')

    data = await aio_file.read()

    assert data == uuid


@aio_impl
async def test_read_write(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, 'r')
    w_file = await aio_file_maker(temp_file, 'w')

    await w_file.write(uuid)
    await w_file.fsync()

    data = await r_file.read()

    assert data == uuid


@aio_impl
async def test_read_offset(aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        for _ in range(10):
            f.write(uuid)

    aio_file = await aio_file_maker(temp_file, 'r')

    data = await aio_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    assert data == uuid


@aio_impl
async def test_read_write_offset(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, 'r')
    w_file = await aio_file_maker(temp_file, 'w')

    for i in range(10):
        await w_file.write(uuid, offset=i * len(uuid))

    await w_file.fsync()

    data = await r_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    assert data == uuid


@aio_impl
async def test_reader_writer(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, 'r')
    w_file = await aio_file_maker(temp_file, 'w')

    writer = Writer(w_file)

    for _ in range(100):
        await writer(uuid)

    await w_file.fsync()

    count = 0
    for async_chunk in Reader(r_file, chunk_size=len(uuid)):
        chunk = await async_chunk
        assert chunk == uuid
        count += 1

    assert count == 100


@aio_impl
async def test_reader_writer(aio_file_maker, temp_file, uuid):
    r_file = await aio_file_maker(temp_file, 'r')
    w_file = await aio_file_maker(temp_file, 'w')

    writer = Writer(w_file)

    for _ in range(100):
        await writer(uuid)

    await w_file.fsync()

    async for chunk in Reader(r_file, chunk_size=len(uuid)):
        assert chunk == uuid


@aio_impl
async def test_parallel_writer(aio_file_maker, temp_file, uuid):
    w_file = await aio_file_maker(temp_file, 'w')
    r_file = await aio_file_maker(temp_file, 'r')

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


@aio_impl
async def test_parallel_writer_ordering(aio_file_maker, temp_file, uuid):
    w_file = await aio_file_maker(temp_file, 'wb')
    r_file = await aio_file_maker(temp_file, 'rb')

    count = 1000
    chunk_size = 1024

    data = os.urandom(chunk_size * count)

    futures = list()

    for idx, chunk in enumerate(split_by(data, chunk_size)):
        futures.append(w_file.write(chunk, idx * chunk_size))

    shuffle(futures)

    await asyncio.gather(*futures)
    await w_file.fsync()

    result = b''

    async for chunk in Reader(r_file, chunk_size=chunk_size):
        result += chunk

    assert data == result


@aio_impl
async def test_non_existent_file_ctx(aio_file_maker):
    with pytest.raises(FileNotFoundError):
        async with AIOFile("/c/windows/NonExistent.file", 'r'):
            pass


@aio_impl
async def test_line_reader(aio_file_maker, temp_file, uuid):
    afp = await aio_file_maker(temp_file, 'w+')

    writer = Writer(afp)

    lines = [uuid4().hex for _ in range(1000)]

    for line in lines:
        await writer(line)
        await writer('\n')

    read_lines = []

    async for line in LineReader(afp):
        read_lines.append(line[:-1])

    assert lines == read_lines


@aio_impl
async def test_line_reader_one_line(aio_file_maker, temp_file):
    afp = await aio_file_maker(temp_file, 'w+')

    writer = Writer(afp)

    payload = " ".join(uuid4().hex for _ in range(1000))

    await writer(payload)

    read_lines = []

    async for line in LineReader(afp):
        read_lines.append(line)

    assert payload == read_lines[0]


@aio_impl
async def test_truncate(aio_file_maker, temp_file):
    afp = await aio_file_maker(temp_file, 'w+')

    await afp.write('hello')
    await afp.fsync()

    assert (await afp.read()) == 'hello'

    await afp.truncate(0)

    assert (await afp.read()) == ''


@aio_impl
async def test_modes(aio_file_maker, event_loop, tmpdir):
    tmpfile = tmpdir.join('test.txt')

    async with aio_file_maker(tmpfile, 'w', loop=event_loop) as afp:
        await afp.write('foo')

    async with aio_file_maker(tmpfile, 'r', loop=event_loop) as afp:
        assert await afp.read() == 'foo'

    async with aio_file_maker(tmpfile, 'a+', loop=event_loop) as afp:
        assert await afp.read() == 'foo'

    async with aio_file_maker(tmpfile, 'r+', loop=event_loop) as afp:
        assert await afp.read() == 'foo'
