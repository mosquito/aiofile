from functools import partial

import pytest
from caio import python_aio_asyncio

from aiofile import AIOFile


try:
    from caio import thread_aio_asyncio
except ImportError:
    thread_aio_asyncio = None

try:
    from caio import linux_aio_asyncio
except ImportError:
    linux_aio_asyncio = None


class DefaultContext:
    __name__ = "default"


IMPLEMENTATIONS = list(
    filter(
        None, [
            linux_aio_asyncio,
            thread_aio_asyncio,
            python_aio_asyncio,
            DefaultContext(),
        ],
    ),
)

IMPLEMENTATION_NAMES = map(lambda x: x.__name__, IMPLEMENTATIONS)


@pytest.fixture(params=IMPLEMENTATIONS, ids=IMPLEMENTATION_NAMES)
async def aio_context(request, loop):
    if isinstance(request.param, DefaultContext):
        yield None
        return

    async with request.param.AsyncioContext(loop=loop) as context:
        yield context


@pytest.fixture
def aio_file_maker(aio_context):
    return partial(AIOFile, context=aio_context)
