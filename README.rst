AIOFile
=======

.. image:: https://github.com/mosquito/aiofile/workflows/tox/badge.svg
    :target: https://github.com/mosquito/aiofile/actions?query=branch%3Amaster
    :alt: Github Actions

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

* Since version 2.0.0 using `caio`_, is contain linux libaio and two
  thread-based implementations (c-based and pure-python).
* AIOFile has no internal pointer. You should pass ``offset`` and
  ``chunk_size`` for each operation or use helpers (Reader or Writer).
* For Linux using implementation based on `libaio`_.
* For POSIX (MacOS X and optional Linux) using implementation
  using on `threadpool`_.
* Otherwise using pure-python thread-based implementation.
* Implementation chooses automatically depending on system compatibility.

.. _caio: https://pypi.org/project/caio
.. _libaio: https://pagure.io/libaio
.. _threadpool: https://github.com/mbrossard/threadpool/


Code examples
-------------

All code examples requires python 3.6+.

High-level API
++++++++++++++

``async_open`` helper
~~~~~~~~~~~~~~~~~~~~~

The ``async_open`` helper creates file like object with file-like methods:

.. code-block:: python

    import asyncio
    from aiofile import async_open


    async def main():
        async with async_open("/tmp/hello.txt", 'w+') as afp:
            await afp.write("Hello ")
            await afp.write("world")
            afp.seek(0)

            print(await afp.read())

            await afp.write("Hello from\nasync world")
            print(await afp.readline())
            print(await afp.readline())

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())


Suboptimal for small lines because doesn't reuse read buffer.
When you want to read file by lines please avoid to use ``async_open``
use ``LineReader`` instead.

Supported methods:

* ``async def read(length = -1)`` - reading chunk from file, when length is
  ``-1`` will be read file to the end.
* ``async def write(data)`` - write chunk to file
* ``def seek(offset)`` - set file pointer position
* ``def tell()`` - returns current file pointer position
* ``async def readline(size=-1, newline="\n")`` - read chunks until
  newline or EOF.


``Reader`` and ``Writer``
~~~~~~~~~~~~~~~~~~~~~~~~~

When you want to read or write file linearly following example
might be helpful.

.. code-block:: python

    import asyncio
    from aiofile import AIOFile, Reader, Writer


    async def main():
        async with AIOFile("/tmp/hello.txt", 'w+') as afp:
            writer = Writer(afp)
            reader = Reader(afp, chunk_size=8)

            await writer("Hello")
            await writer(" ")
            await writer("World")
            await afp.fsync()

            async for chunk in reader:
                print(chunk)


    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())



``LineReader`` - read file line by line
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

LineReader is a helper that is very effective when you want to read a file
linearly and line by line.

It contains a buffer and will read the fragments of the file chunk by
chunk into the buffer, where it will try to find lines.

The default chunk size is 4KB.

.. code-block:: python

    import asyncio
    from aiofile import AIOFile, LineReader, Writer


    async def main():
        async with AIOFile("/tmp/hello.txt", 'w+') as afp:
            writer = Writer(afp)

            await writer("Hello")
            await writer(" ")
            await writer("World")
            await writer("\n")
            await writer("\n")
            await writer("From async world")
            await afp.fsync()

            async for line in LineReader(afp):
                print(line)


    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())


When you want to read file by lines please avoid to use ``async_open``
use ``LineReader`` instead.

Low-level API
+++++++++++++

Following API is just little bit sugared ``caio`` API.

Write and Read
~~~~~~~~~~~~~~

.. code-block:: python

    import asyncio
    from aiofile import AIOFile


    async def main():
        async with AIOFile("/tmp/hello.txt", 'w+') as afp:
            await afp.write("Hello ")
            await afp.write("world", offset=7)
            await afp.fsync()

            print(await afp.read())


    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())



Read file line by line
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    import asyncio
    from aiofile import AIOFile, LineReader, Writer


    async def main():
        async with AIOFile("/tmp/hello.txt", 'w') as afp:
            writer = Writer(afp)

            for i in range(10):
                await writer("%d Hello World\n" % i)

            await writer("Tail-less string")


        async with AIOFile("/tmp/hello.txt", 'r') as afp:
            async for line in LineReader(afp):
                print(line[:-1])


    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

More examples
-------------

Useful examples with ``aiofile``

Async CSV Dict Reader
+++++++++++++++++++++

.. code-block:: python

    import asyncio
    import io
    from csv import DictReader

    from aiofile import AIOFile, LineReader


    class AsyncDictReader:
        def __init__(self, afp, **kwargs):
            self.buffer = io.BytesIO()
            self.file_reader = LineReader(
                afp, line_sep=kwargs.pop('line_sep', '\n'),
                chunk_size=kwargs.pop('chunk_size', 4096),
                offset=kwargs.pop('offset', 0),
            )
            self.reader = DictReader(
                io.TextIOWrapper(
                    self.buffer,
                    encoding=kwargs.pop('encoding', 'utf-8'),
                    errors=kwargs.pop('errors', 'replace'),
                ), **kwargs,
            )
            self.line_num = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.line_num == 0:
                header = await self.file_reader.readline()
                self.buffer.write(header)

            line = await self.file_reader.readline()

            if not line:
                raise StopAsyncIteration

            self.buffer.write(line)
            self.buffer.seek(0)

            try:
                result = next(self.reader)
            except StopIteration as e:
                raise StopAsyncIteration from e

            self.buffer.seek(0)
            self.buffer.truncate(0)
            self.line_num = self.reader.line_num

            return result


    async def main():
        async with AIOFile('sample.csv', 'rb') as afp:
            async for item in AsyncDictReader(afp, line_sep='\r'):
                print(item)


    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


    try:
        loop.run_until_complete(main())
    finally:
        # Shutting down and closing file descriptors after interrupt
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
