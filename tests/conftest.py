import pytest
import asyncio
import sys
from uuid import uuid4
from tempfile import NamedTemporaryFile


@pytest.yield_fixture()
def event_loop():
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


def pytest_ignore_collect(path, config):
    if 'test_py35' in str(path):
        if sys.version_info < (3, 5, 0):
            return True
