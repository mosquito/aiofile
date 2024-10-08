from functools import partial
from types import ModuleType
from typing import List, Union

import pytest
from caio import python_aio_asyncio

from aiofile import AIOFile, async_open


try:
    from caio import thread_aio_asyncio
except ImportError:
    thread_aio_asyncio = None       # type: ignore

try:
    from caio import linux_aio_asyncio
except ImportError:
    linux_aio_asyncio = None        # type: ignore


IMPLEMENTATIONS: List[Union[ModuleType, None]] = list(
    filter(
        None, [
            linux_aio_asyncio,
            thread_aio_asyncio,
            python_aio_asyncio,
        ],
    ),
)

IMPLEMENTATIONS.insert(0, None)

IMPLEMENTATION_NAMES: List[str] = list(
    map(
        lambda x: x.__name__ if x is not None else "default",
        IMPLEMENTATIONS
    )
)


@pytest.fixture(params=IMPLEMENTATIONS, ids=IMPLEMENTATION_NAMES)
async def aio_context(request, event_loop):
    if request.param is None:
        yield None
        return

    async with request.param.AsyncioContext(loop=event_loop) as context:
        yield context


@pytest.fixture
def aio_file_maker(aio_context):
    return partial(AIOFile, context=aio_context)


@pytest.fixture(name="async_open")
def async_open_maker(aio_context):
    return partial(async_open, context=aio_context)
