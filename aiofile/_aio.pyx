from libc.errno cimport *
from libc.stdlib cimport calloc, free
from libc.string cimport memcpy, strerror
from posix.unistd cimport close as cclose
from posix.fcntl cimport open as copen, O_RDWR, O_APPEND, O_CREAT, O_RDONLY, O_SYNC
from posix.signal cimport sigevent, SIGEV_NONE
from posix.types cimport off_t
from posix.stat cimport fstat, struct_stat
from cpython cimport object, bytes, bool
from cpython cimport version
# from posix.time cimport timespec

import asyncio


cdef bool PY_35 = version.PY_MAJOR_VERSION >=3 and version.PY_MINOR_VERSION >= 5


class LimitationError(Exception):
    pass


cdef extern from *:
     ctypedef int vvoid "volatile void"

cdef extern from "<aio.h>" nogil:

    # The order of these fields is implementation-dependent
    cdef struct aiocb:
        int             aio_fildes     # File descriptor
        off_t           aio_offset     # File offset
        vvoid           *aio_buf       # Location of buffer
        size_t          aio_nbytes     # Length of transfer
        int             aio_reqprio    # Request priority
        sigevent        aio_sigevent   # Notification method
        int             aio_lio_opcode # Operation to be performed; lio_listio() only

        # Various implementation-internal fields not shown

    enum:
        LIO_READ,
        LIO_WRITE,
        LIO_NOP

    cdef int aio_read(aiocb *aiocbp)
    cdef int aio_write(aiocb *aiocbp)
    cdef int aio_fsync(int op, aiocb *aiocbp)
    cdef int aio_error(const aiocb *aiocbp)
    cdef ssize_t aio_return(aiocb *aiocbp)
    # cdef int aio_suspend(const aiocb * const aiocb_list[], int nitems, const timespec *timeout)
    # cdef int aio_cancel(int fd, aiocb *aiocbp)
    # cdef int lio_listio(int mode, aiocb *const aiocb_list[], int nitems, sigevent *sevp)


IO_READ = LIO_READ
IO_WRITE = LIO_WRITE
IO_NOP = LIO_NOP


def _handle_errno():
    if errno == EAGAIN:
        raise LimitationError("Resource limitaion")
    elif errno == ENOSYS:
        raise SystemError("Syscall is not supported")
    elif errno != 0:
        raise RuntimeError(strerror(errno).decode())


cdef class AIOFile:
    cdef int fd
    cdef bool binary
    cdef str _fname
    cdef object loop

    def __init__(self, str filename, str mode="r", int access_mode=0x644, loop=None):

        cdef cmode = 0

        self._fname = filename
        self.binary = 'b' in mode

        self.loop = loop or asyncio.get_event_loop()

        if '+' in mode:
            cmode |= O_CREAT

        if "a" in mode:
            cmode |= O_RDWR
            cmode |= O_APPEND
        elif "w" in mode:
            cmode |= O_RDWR
        elif "r" in mode:
            cmode |= O_RDONLY

        self.fd = copen(filename.encode(), cmode, access_mode)

        if self.fd == -1:
            raise IOError('Couldn\'t open file "%s"' % filename)

    def __repr__(self):
        return "<AIOFile: %r>" % self._fname

    def __dealloc__(self):
        cclose(self.fd)

    def read(self, int size=-1, unsigned int offset=0, int priority=0):
        if size < -1:
            raise ValueError("Unsupported value %d for size" % size)

        cdef struct_stat stat
        if size == -1:
            fstat(self.fd, &stat)
            size = stat.st_size

        return AIOOperation(LIO_READ, self, offset, size, priority, loop=self.loop)

    def write(self, data, unsigned int offset=0, int priority=0):
        if isinstance(data, str):
            bytes_data = data.encode()
        elif isinstance(data, bytes):
            bytes_data = data
        else:
            raise ValueError("Data must be str or bytes")

        op = AIOOperation(LIO_WRITE, self, offset, len(data), priority, loop=self.loop)
        op.buffer = bytes_data
        return op

    def flush(self, int priority=0):
        return AIOOperation(LIO_NOP, self, 0, 0, priority, loop=self.loop)


cdef class AIOOperation:
    cdef aiocb* cb
    cdef unsigned int buffer_size
    cdef bool _closed
    cdef int _state
    cdef object _result
    cdef bool is_running
    cdef object loop

    def __init__(self, int state, AIOFile aio_file, unsigned int offset,
                 int nbytes, int reqprio, object loop):

        self._state = state
        self.loop = loop

        if state not in (LIO_READ, LIO_WRITE, LIO_NOP):
            raise ValueError("Invalid state")

        if self._state == LIO_READ:
            buffer_size = nbytes
        elif self._state == LIO_WRITE:
            buffer_size = nbytes
        elif self._state == LIO_NOP:
            buffer_size = 0

        self._result = None
        self.is_running = False
        self.cb = <aiocb*>calloc(1, sizeof(aiocb))
        self.buffer_size = buffer_size
        self.cb.aio_buf = <vvoid*>calloc(self.buffer_size, sizeof(char))
        self.cb.aio_fildes = aio_file.fd
        self.cb.aio_offset = offset
        self.cb.aio_nbytes = nbytes
        self.cb.aio_reqprio = reqprio
        self.cb.aio_sigevent.sigev_notify = SIGEV_NONE
        self.cb.aio_lio_opcode = state
        self._closed = False

    def run(self):
        cdef int result

        if self.is_running:
            raise RuntimeError("Operation already in progress")

        try:
            self.is_running = True

            if self._state == LIO_READ:
                result = aio_read(self.cb)
            elif self._state == LIO_WRITE:
                result = aio_write(self.cb)
            elif self._state == LIO_NOP:
                result = aio_fsync(O_SYNC, self.cb)
            else:
                raise RuntimeError('Oops...')

            _handle_errno()
        except LimitationError:
            self.is_running = False
            raise

    def __check_closed(self):
        if self._closed:
            raise RuntimeError("Can't perform operation in when closed object")

    def poll(self):
        self.__check_closed()

        if not self.is_running:
            raise RuntimeError("Can't perform pool on not running operation")

        cdef int result = aio_error(self.cb)

        if result == EINPROGRESS:
            return True
        elif result == 0:
            return False
        else:
            _handle_errno()

    def __len__(self):
        return self.nbytes

    def __repr__(self):
        if self._state == LIO_READ:
            op = 'read'
        elif self._state == LIO_WRITE:
            op = 'write'
        elif self._state == LIO_NOP:
            op = 'fsync'
        else:
            op = 'uknown'

        if not self.is_running:
            state = 'not started'
        elif self.poll():
            state = 'pending'
        elif self.closed:
            state = 'closed'
        else:
            state = 'done'

        return "<AIOOperation(%r): %s>" % (op, state)

    @property
    def nbytes(self):
        self.__check_closed()
        return self.cb.aio_nbytes

    @property
    def offset(self):
        self.__check_closed()
        return self.cb.aio_offset

    @property
    def reqprio(self):
        self.__check_closed()
        return self.cb.aio_reqprio

    @property
    def buffer(self):
        self.__check_closed()
        return (<char*>self.cb.aio_buf)[:self.buffer_size]

    @buffer.setter
    def buffer(self, bytes data):
        self.__check_closed()
        memcpy(self.cb.aio_buf, <vvoid*>(<char*>data), len(data))

    def result(self):
        cdef ssize_t result

        if self._result is None:
            if self.poll():
                raise RuntimeError("Operation in progress")

            result = aio_return(self.cb)
            self._result = (<char*>self.cb.aio_buf)[:result]

        return self._result

    @property
    def closed(self):
        return self._closed

    def close(self):
        self.__check_closed()
        self._closed = True
        free(self.cb.aio_buf)
        free(self.cb)

    def __dealloc__(self):
        if self._closed:
            return
        self.close()

    def __iter__(self):
        while True:
            try:
                self.run()
                break
            except LimitationError:
                yield

        while self.poll():
            yield

        return self.result()

    if PY_35:
        def __await__(self):
            return self.__iter__()
