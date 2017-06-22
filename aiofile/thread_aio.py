import asyncio
import os
from functools import partial


IO_READ = 0
IO_WRITE = 1
IO_NOP = 2


class ThreadedAIOOperation:
    __slots__ = '__fd', '__offset', '__nbytes', '__reqprio', '__opcode', '__buffer', '__loop', '__state'

    def __init__(self, opcode: int, fd: int, offset: int, nbytes: int, reqprio: int,
                 loop: asyncio.AbstractEventLoop):

        if opcode not in (IO_READ, IO_WRITE, IO_NOP):
            raise ValueError('Invalid state')

        self.__loop = loop
        self.__fd = fd
        self.__offset = offset
        self.__nbytes = nbytes
        self.__reqprio = reqprio
        self.__opcode = opcode
        self.__buffer = b''
        self.__state = None

    @property
    def buffer(self):
        return self.__buffer

    @buffer.setter
    def buffer(self, data: bytes):
        self.__buffer = data

    def __iter__(self):
        if self.__state is not None:
            raise RuntimeError('Invalid state')

        self.__state = False

        if self.opcode == IO_READ:
            operation = partial(os.read, self.__fd, self.__nbytes)
        elif self.opcode == IO_WRITE:
            operation = partial(os.write, self.__fd, self.__buffer)
        elif self.opcode == IO_NOP:
            operation = partial(os.fsync, self.__fd)

        yield
        result = yield from self.__loop.run_in_executor(None, operation)
        self.__state = True
        return result

    def __await__(self):
        return self.__iter__()

    def done(self) -> bool:
        return self.__state is True

    def is_running(self) -> bool:
        return self.__state is False

    def close(self):
        pass

    @property
    def opcode(self):
        return self.__opcode

    @property
    def opcode_str(self):
        if self.opcode == IO_READ:
            return 'IO_READ'
        elif self.opcode == IO_WRITE:
            return 'IO_WRITE'
        elif self.opcode == IO_NOP:
            return 'IO_NOP'

    @property
    def fileno(self):
        return self.__fd

    @property
    def offset(self):
        return self.__offset

    @property
    def nbytes(self):
        return self.__nbytes

    @property
    def reqprio(self):
        return self.__reqprio

    def __repr__(self):
        return "<AIOThreadOperation[{!r}, fd={}, offset={}, nbytes={}, reqprio={}]>".format(
            self.opcode_str,
            self.fileno,
            self.offset,
            self.nbytes,
            self.reqprio,
        )
