import asyncio
import os
from uuid import uuid4
from random import shuffle

from aiofile.utils import Reader, Writer, LineReader
from . import *


@aio_impl
def test_read(aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        f.write(uuid)

    aio_file = yield from aio_file_maker(temp_file, 'r')

    data = yield from aio_file.read()
    assert data == uuid


@aio_impl
def test_read_write(aio_file_maker, temp_file, uuid):
    r_file = yield from aio_file_maker(temp_file, 'r')
    w_file = yield from aio_file_maker(temp_file, 'w')

    yield from w_file.write(uuid)
    yield from w_file.fsync()

    data = yield from r_file.read()
    assert data == uuid


@aio_impl
def test_read_offset(aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        for _ in range(10):
            f.write(uuid)

    aio_file = yield from aio_file_maker(temp_file, 'r')

    data = yield from aio_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    assert data == uuid


@aio_impl
def test_read_write_offset(aio_file_maker, temp_file, uuid):
    r_file = yield from aio_file_maker(temp_file, 'r')
    w_file = yield from aio_file_maker(temp_file, 'w')

    for i in range(10):
        yield from w_file.write(uuid, offset=i * len(uuid))

    yield from w_file.fsync()

    data = yield from r_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    assert data == uuid


@aio_impl
def test_reader_writer(aio_file_maker, temp_file, uuid):
    r_file = yield from aio_file_maker(temp_file, 'r')
    w_file = yield from aio_file_maker(temp_file, 'w')

    writer = Writer(w_file)

    for _ in range(100):
        yield from writer(uuid)

    yield from w_file.fsync()

    count = 0
    for async_chunk in Reader(r_file, chunk_size=len(uuid)):
        chunk = yield from async_chunk

        if not chunk:
            break

        assert chunk == uuid
        count += 1

    assert count == 100


@aio_impl
def test_parallel_writer(aio_file_maker, temp_file, uuid):
    w_file = yield from aio_file_maker(temp_file, 'w')
    r_file = yield from aio_file_maker(temp_file, 'r')

    futures = list()

    for i in range(1000):
        futures.append(w_file.write(uuid, i * len(uuid)))

    shuffle(futures)

    yield from asyncio.gather(*futures)
    yield from w_file.fsync()

    count = 0
    for async_chunk in Reader(r_file, chunk_size=len(uuid)):
        chunk = yield from async_chunk

        if not chunk:
            break

        assert chunk == uuid
        count += 1

    assert count == 1000


@aio_impl
def test_parallel_writer_ordering(aio_file_maker, temp_file, uuid):
    w_file = yield from aio_file_maker(temp_file, 'wb')
    r_file = yield from aio_file_maker(temp_file, 'rb')

    count = 1000
    chunk_size = 1024

    data = os.urandom(chunk_size * count)

    futures = list()

    for idx, chunk in enumerate(split_by(data, chunk_size)):
        futures.append(w_file.write(chunk, idx * chunk_size))

    shuffle(futures)

    yield from asyncio.gather(*futures)
    yield from w_file.fsync()

    result = b''

    for async_chunk in Reader(r_file, chunk_size=chunk_size):
        chunk = yield from async_chunk

        if not chunk:
            break

        result += chunk

    assert data == result


@aio_impl
@asyncio.coroutine
def test_non_existent_file(aio_file_maker):
    with pytest.raises(FileNotFoundError):
        yield from aio_file_maker("/c/windows/NonExistent.file", 'r')


@aio_impl
def test_line_reader(aio_file_maker, temp_file, uuid):
    afp = yield from aio_file_maker(temp_file, 'w+')

    writer = Writer(afp)

    lines = [uuid4().hex for _ in range(1000)]

    for line in lines:
        yield from writer(line)
        yield from writer('\n')

    read_lines = []

    for async_chunk in LineReader(afp):
        line = yield from async_chunk

        if not line:
            break

        read_lines.append(line[:-1])

    assert lines == read_lines


@aio_impl
def test_line_reader_one_line(aio_file_maker, temp_file):
    afp = yield from aio_file_maker(temp_file, 'w+')

    writer = Writer(afp)

    payload = " ".join(uuid4().hex for _ in range(1000))

    yield from writer(payload)

    read_lines = []

    for line_async in LineReader(afp):
        line = yield from line_async

        if not line:
            break

        read_lines.append(line)

    assert payload == read_lines[0]


@aio_impl
def test_truncate(aio_file_maker, temp_file):
    afp = yield from aio_file_maker(temp_file, 'w+')

    yield from afp.write('hello')
    yield from afp.fsync()

    assert (yield from afp.read()) == 'hello'

    yield from afp.truncate(0)

    assert (yield from afp.read()) == ''


@aio_impl
def test_modes(aio_file_maker, event_loop, tmpdir):
    tmpfile = str(tmpdir.join('test.txt'))

    afp = yield from aio_file_maker(tmpfile, 'w', loop=event_loop)
    yield from afp.write('foo')

    afp = yield from aio_file_maker(tmpfile, 'r', loop=event_loop)
    assert (yield from afp.read()) == 'foo'

    afp = yield from aio_file_maker(tmpfile, 'a+', loop=event_loop)
    assert (yield from afp.read()) == 'foo'

    afp = yield from aio_file_maker(tmpfile, 'r+', loop=event_loop)
    assert (yield from afp.read()) == 'foo'
