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

Write and Read
++++++++++++++

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


Write and read with helpers
+++++++++++++++++++++++++++

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



Read file line by line
++++++++++++++++++++++

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


Reading and Writing for the unix pipe
+++++++++++++++++++++++++++++++++++++

.. code-block:: python

    import os
    import asyncio
    from aiofile import AIOFile, Reader, Writer


    async def reader(fname):
        print('Start reader')
        async with AIOFile(fname, 'r') as afp:
            while True:
                # Maximum expected chunk size, must be passed.
                # Otherwise will be read zero bytes
                # (because unix pipe has zero size)
                data = await afp.read(4096)
                print(data)


    async def writer(fname):
        print('Start writer')
        async with AIOFile(fname, 'w') as afp:
            while True:
                await asyncio.sleep(1)
                await afp.write('%06f' % loop.time())


    async def main():
        fifo_name = "/tmp/test.fifo"

        if os.path.exists(fifo_name):
            os.remove(fifo_name)

        os.mkfifo(fifo_name)

        # Starting two readers and one writer, but only one reader
        # will be reading at the same time.
        await asyncio.gather(
            reader(fifo_name),
            reader(fifo_name),
            writer(fifo_name),
        )


    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main())
    finally:
        # Shutting down and closing file descriptors after interrupt
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        print('Exited')


Read file line by line
++++++++++++++++++++++

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
