import pytest
import asyncio
from uuid import uuid4
from tempfile import NamedTemporaryFile

from aiofile import AIOFile, thread_aio

try:
    from aiofile import posix_aio
except ImportError:
    posix_aio = None


@pytest.yield_fixture()
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.yield_fixture()
def temp_file():
    temp = NamedTemporaryFile()
    yield temp.name
    temp.close()


@pytest.fixture()
def uuid():
    return str(uuid4())


if posix_aio:
    def posix_aio_file(name, mode):
        AIOFile.OPERATION_CLASS = posix_aio.AIOOperation
        AIOFile.IO_READ = posix_aio.IO_READ
        AIOFile.IO_NOP = posix_aio.IO_NOP
        AIOFile.IO_WRITE = posix_aio.IO_WRITE

        return AIOFile(name, mode)


def thread_aio_file(name, mode):
    AIOFile.OPERATION_CLASS = thread_aio.ThreadedAIOOperation
    AIOFile.IO_READ = thread_aio.IO_READ
    AIOFile.IO_NOP = thread_aio.IO_NOP
    AIOFile.IO_WRITE = thread_aio.IO_WRITE

    return AIOFile(name, mode)


def aio_impl(func):
    lst = [thread_aio_file]

    if posix_aio:
        lst.append(posix_aio_file)

    return pytest.mark.parametrize("aio_file_maker", lst)(pytest.mark.asyncio(func))


def split_by(seq, n):
    seq = seq
    while seq:
        yield seq[:n]
        seq = seq[n:]
