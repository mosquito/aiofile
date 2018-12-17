import pytest
import asyncio
import sys
from uuid import uuid4
from tempfile import NamedTemporaryFile


try:
    from aiomisc.utils import new_event_loop
except ImportError:
    from asyncio import new_event_loop


@pytest.yield_fixture()
def event_loop():
    loop = new_event_loop()
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


def pytest_ignore_collect(path):
    if 'test_py35' in str(path):
        if sys.version_info < (3, 5, 0):
            return True
