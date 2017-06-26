from random import shuffle

from aiofile.utils import Reader, Writer
from . import *


@aio_impl
def test_read(aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        f.write(uuid)

    aio_file = aio_file_maker(temp_file, 'r')

    data = yield from aio_file.read()
    data = data.decode()

    assert data == uuid


@aio_impl
def test_read_write(aio_file_maker, temp_file, uuid):
    r_file = aio_file_maker(temp_file, 'r')
    w_file = aio_file_maker(temp_file, 'w')

    yield from w_file.write(uuid)
    yield from w_file.fsync()

    data = yield from r_file.read()
    data = data.decode()

    assert data == uuid


@aio_impl
def test_read_offset(aio_file_maker, temp_file, uuid):
    with open(temp_file, "w") as f:
        for _ in range(10):
            f.write(uuid)

    aio_file = aio_file_maker(temp_file, 'r')

    data = yield from aio_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    data = data.decode()

    assert data == uuid


@aio_impl
def test_read_write_offset(aio_file_maker, temp_file, uuid):
    r_file = aio_file_maker(temp_file, 'r')
    w_file = aio_file_maker(temp_file, 'w')

    for i in range(10):
        yield from w_file.write(uuid, offset=i * len(uuid))

    yield from w_file.fsync()

    data = yield from r_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    data = data.decode()

    assert data == uuid


@aio_impl
def test_reader_writer(aio_file_maker, temp_file, uuid):
    r_file = aio_file_maker(temp_file, 'r')
    w_file = aio_file_maker(temp_file, 'w')

    writer = Writer(w_file)

    for _ in range(100):
        yield from writer(uuid)

    yield from w_file.fsync()

    count = 0
    for async_chunk in Reader(r_file, chunk_size=len(uuid)):
        chunk = yield from async_chunk

        if not chunk:
            break

        assert chunk.decode() == uuid
        count += 1

    assert count == 100


@aio_impl
def test_parallel_writer(aio_file_maker, temp_file, uuid):
    w_file = aio_file_maker(temp_file, 'w')
    r_file = aio_file_maker(temp_file, 'r')

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

        assert chunk.decode() == uuid
        count += 1

    assert count == 1000
