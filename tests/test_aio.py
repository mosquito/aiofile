from aiofile._aio import AIOFile
from aiofile.utils import Reader, Writer
from sys import version_info
from . import *


PY35 = version_info < (3, 5)


@pytest.mark.asyncio
def test_read(temp_file, uuid):
    with open(temp_file, "w") as f:
        f.write(uuid)

    aio_file = AIOFile(temp_file, 'r')

    data = yield from aio_file.read()
    data = data.decode()

    assert data == uuid


@pytest.mark.asyncio
def test_read_write(temp_file, uuid):
    r_file = AIOFile(temp_file, 'r')
    w_file = AIOFile(temp_file, 'w')

    yield from w_file.write(uuid)
    yield from w_file.flush()

    data = yield from r_file.read()
    data = data.decode()

    assert data == uuid


@pytest.mark.asyncio
def test_read_offset(temp_file, uuid):
    with open(temp_file, "w") as f:
        for _ in range(10):
            f.write(uuid)

    aio_file = AIOFile(temp_file, 'r')

    data = yield from aio_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    data = data.decode()

    assert data == uuid


@pytest.mark.asyncio
def test_read_write_offset(temp_file, uuid):
    r_file = AIOFile(temp_file, 'r')
    w_file = AIOFile(temp_file, 'w')

    for i in range(10):
        yield from w_file.write(uuid, offset=i * len(uuid))

    yield from w_file.flush()

    data = yield from r_file.read(
        offset=len(uuid),
        size=len(uuid)
    )

    data = data.decode()

    assert data == uuid


@pytest.mark.asyncio
def test_reader_writer(temp_file, uuid):
    r_file = AIOFile(temp_file, 'r')
    w_file = AIOFile(temp_file, 'w')

    writer = Writer(w_file)

    for _ in range(100):
        yield from writer(uuid)

    yield from w_file.flush()

    count = 0
    for async_chunk in Reader(r_file, chunk_size=len(uuid)):
        chunk = yield from async_chunk
        assert chunk.decode() == uuid
        count += 1

    assert count == 100


if PY35:
    @pytest.mark.skipif(PY35, reason="requires python3.5")
    @pytest.mark.asyncio
    async def test_reader_writer(loop, temp_file, uuid):
        r_file = AIOFile(temp_file, 'r')
        w_file = AIOFile(temp_file, 'w')

        writer = Writer(w_file)

        for _ in range(100):
            await writer(uuid)

        await w_file.flush()

        async for chunk in Reader(r_file, chunk_size=len(uuid)):
            assert chunk.decode() == uuid
