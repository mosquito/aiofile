import sys


def pytest_ignore_collect(path, config):
    if 'test_py35' in str(path):
        if sys.version_info < (3, 5, 0):
            return True


