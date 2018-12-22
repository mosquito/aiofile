import pytest

from aiofile import thread_aio, AIOFile


try:
    from aiofile import posix_aio
except ImportError:
    posix_aio = None


def thread_aio_file(name, mode, **kwargs):
    AIOFile.OPERATION_CLASS = thread_aio.ThreadedAIOOperation
    AIOFile.IO_READ = thread_aio.IO_READ
    AIOFile.IO_NOP = thread_aio.IO_NOP
    AIOFile.IO_WRITE = thread_aio.IO_WRITE

    return AIOFile(name, mode, **kwargs)


IMPLEMENTATIONS = [thread_aio_file]

if posix_aio:
    def posix_aio_file(name, mode, **kwargs):
        AIOFile.OPERATION_CLASS = posix_aio.AIOOperation
        AIOFile.IO_READ = posix_aio.IO_READ
        AIOFile.IO_NOP = posix_aio.IO_NOP
        AIOFile.IO_WRITE = posix_aio.IO_WRITE

        return AIOFile(name, mode, **kwargs)

    IMPLEMENTATIONS.append(posix_aio_file)


IMPLEMENTATIONS = tuple(IMPLEMENTATIONS)


def aio_impl(func):
    func = pytest.mark.asyncio(func)
    return pytest.mark.parametrize("aio_file_maker", IMPLEMENTATIONS)(func)


def split_by(seq, n):
    seq = seq
    while seq:
        yield seq[:n]
        seq = seq[n:]
