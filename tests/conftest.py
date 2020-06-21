import pytest

from caio import python_aio_asyncio

try:
    from caio import thread_aio_asyncio
except ImportError:
    thread_aio_asyncio = None

try:
    from caio import linux_aio_asyncio
except ImportError:
    linux_aio_asyncio = None


from aiofile import AIOFile


IMPLEMENTATIONS = list(filter(None, [
    linux_aio_asyncio,
    thread_aio_asyncio,
    python_aio_asyncio,
]))


@pytest.fixture(scope="session", params=IMPLEMENTATIONS)
def aio_file_maker(request):
    class AIOFileCustom(AIOFile):
        CONTEXT_IMPL = request.param.AsyncioContext

    return AIOFileCustom
