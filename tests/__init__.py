import pytest
import asyncio
from uuid import uuid4
from tempfile import NamedTemporaryFile


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
