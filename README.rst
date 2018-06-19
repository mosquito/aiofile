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

All code examples requires python 3.5+.

Write and Read
++++++++++++++

.. code-block:: python

    import asyncio
    from aiofile import AIOFile, Reader, Writer


    async def main():
        async with AIOFile("/tmp/hello.txt", 'w+') as afp:
            await afp.write("Hello ")
            await afp.write("world", offset=7)
            await afp.fsync()

            print(await afp.read())


    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())


Write and read with helpers
+++++++++++++++++++++++++++

.. code-block:: python

    import asyncio
    from aiofile import AIOFile, Reader, Writer


    async def main():
        async with AIOFile("/tmp/hello.txt", 'w+') as afp
            writer = Writer(afp)
            reader = Reader(afp, chunk_size=8)

            await writer("Hello")
            await writer(" ")
            await writer("World")
            await afp.flush()

            async for chunk in reader:
                print(chunk)


    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())


Read file line by line
++++++++++++++++++++++

.. code-block:: python

    import asyncio
    from aiofile import AIOFile, LineReader, Writer


    async def main():
        async with AIOFile("/tmp/hello.txt", 'w+') as afp
            writer = Writer(afp)

            await writer("Hello")
            await writer(" ")
            await writer("World")
            await writer("\n")
            await writer("\n")
            await writer("From async world")
            await afp.flush()

            async for line in LineReader(afp):
                print(line)


    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
