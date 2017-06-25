import sys
from aiofile import AIOFile
from aiofile.utils import Reader, Writer
from aiofile.thread_aio import ThreadedAIOOperation, IO_WRITE, IO_NOP, IO_READ
from . import *


PY35 = sys.version_info >= (3, 5)


def thread_aio_file(name, mode):
    AIOFile.OPERATION_CLASS = ThreadedAIOOperation
    AIOFile.IO_READ = IO_READ
    AIOFile.IO_NOP = IO_NOP
    AIOFile.IO_WRITE = IO_WRITE

    return AIOFile(name, mode)


@pytest.mark.asyncio
def test_read(temp_file, uuid):
    with open(temp_file, "w") as f:
        f.write(uuid)

    aio_file = thread_aio_file(temp_file, 'r')

    data = yield from aio_file.read()
    data = data.decode()

    assert data == uuid


@pytest.mark.asyncio
def test_read_write(temp_file, uuid):
    r_file = thread_aio_file(temp_file, 'r')
    w_file = thread_aio_file(temp_file, 'w')

    yield from w_file.write(uuid)
    yield from w_file.fsync()

    data = yield from r_file.read()
    data = data.decode()

    assert data == uuid


@pytest.mark.asyncio
def test_read_offset(temp_file, uuid):
    with open(temp_file, "w") as f:
        for _ in range(10):
            f.write(uuid)

    aio_file = thread_aio_file(temp_file, 'r')

    data = yield from aio_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    data = data.decode()

    assert data == uuid


@pytest.mark.asyncio
def test_read_write_offset(temp_file, uuid):
    r_file = thread_aio_file(temp_file, 'r')
    w_file = thread_aio_file(temp_file, 'w')

    for i in range(10):
        yield from w_file.write(uuid, offset=i * len(uuid))

    yield from w_file.fsync()

    data = yield from r_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    data = data.decode()

    assert data == uuid


@pytest.mark.asyncio
def test_reader_writer(temp_file, uuid):
    r_file = thread_aio_file(temp_file, 'r')
    w_file = thread_aio_file(temp_file, 'w')

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
