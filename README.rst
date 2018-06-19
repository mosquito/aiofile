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


Real asynchronous file operations with asyncio support.


Status
------

Development - Stable


Features
--------

* AIOFile has no internal pointer. You should pass ``offset`` and ``chunk_size`` for each operation or use helpers (Reader or Writer).
* For POSIX (MacOS X and Linux) using implementaion based on `aio.h`_ (with `Cython`_).
* For non-POSIX systems using thread-based implementation

.. _aio.h: https://github.com/torvalds/linux/blob/master/include/linux/aio.h
.. _Cython: http://cython.org


Code examples
-------------

Totally async read and write:

.. code-block:: python

    import asyncio
    from aiofile import AIOFile, Reader, Writer

    async def main(loop):
        aio_file = await aio_open("/tmp/hello.txt", 'w+', loop=loop)

        await aio_file.write(b"Hello ")
        await aio_file.write(b"world", offset=7)
        await aio_file.fsync()

        print(await aio_file.read())

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(loop))


Write and read with helpers:

.. code-block:: python

    import asyncio
    from aiofile import aio_open, Reader, Writer

    async def main(loop):
        aio_file = await aio_open("/tmp/hello.txt", 'w+', loop=loop)

        writer = Writer(aio_file)
        reader = Reader(aio_file, chunk_size=8)

        await writer(b"Hello")
        await writer(b" ")
        await writer(b"World")
        await aio_file.flush()

        async for chunk in reader:
            print(chunk)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(loop))


or use async context manager:

.. code-block:: python

    import asyncio
    from aiofile import AIOFile, Reader, Writer

    async def main(loop):
        async with AIOFile("/tmp/hello.txt", 'w+', loop=loop) as aio_file:
            writer = Writer(aio_file)
            reader = Reader(aio_file, chunk_size=8)

            await writer(b"Hello")
            await writer(b" ")
            await writer(b"World")
            await aio_file.flush()

            async for chunk in reader:
                print(chunk)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(loop))
