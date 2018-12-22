import pytest
import asyncio
from uuid import uuid4
from tempfile import NamedTemporaryFile


@pytest.fixture()
def event_loop():
    asyncio.get_event_loop().close()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        yield loop
    finally:
        loop.close()


@pytest.yield_fixture()
def temp_file():
    temp = NamedTemporaryFile()
    try:
        yield temp.name
    finally:
        temp.close()


@pytest.fixture()
def uuid():
    return str(uuid4())
