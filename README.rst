AIOFile
=======

.. image:: https://travis-ci.org/mosquito/aiofile.svg
    :target: https://travis-ci.org/mosquito/aiofile
    :alt: Travis CI

.. image:: https://img.shields.io/pypi/v/aiofile.svg
    :target: https://pypi.python.org/pypi/aiofile/
    :alt: Latest Version

.. image:: https://img.shields.io/pypi/wheel/aiofile.svg
    :target: https://pypi.python.org/pypi/aiofile/

.. image:: https://img.shields.io/pypi/pyversions/aiofile.svg
    :target: https://pypi.python.org/pypi/aiofile/

.. image:: https://img.shields.io/pypi/l/aiofile.svg
    :target: https://pypi.python.org/pypi/aiofile/


Asynchronous file operations.

Status
------

Development - BETA


Features
--------

* For POSIX (MacOS X and Linux) using C implementaion based on `aio.h`_.
* For non-POSIX systems using thread-based implementation (in development)

.. _aio.h: https://github.com/torvalds/linux/blob/master/include/linux/aio.h


Code examples
-------------

.. code-block:: python

    from aiofile import AIOFile, Reader, Writer

    async def main(loop):
        aio_file = AIOFile("/tmp/hello.txt", 'w+', loop=loop)

        writer = Writer(aio_file)
        reader = Reader(aio_file, chunk_size=8)

        await writer(b"Hello")
        await writer(b" ")
        await writer(b"World")
        await aio_file.flush()

        async for chunk in reader:
            print(chunk)
