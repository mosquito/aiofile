# %%cython -a -lrt
from libc.errno cimport *
from libc.stdlib cimport calloc, free
from libc.string cimport memcpy, strerror
from posix.unistd cimport close as cclose
from posix.fcntl cimport open as copen, O_RDWR, O_APPEND, O_CREAT, O_RDONLY, O_SYNC
from posix.signal cimport SIGEV_NONE
from posix.types cimport int
from posix.stat cimport fstat, struct_stat
from cpython cimport object, bytes, bool, str
from aio cimport aio_read, aio_write, aio_fsync


class LimitationError(Exception):
    pass


def _handle_cerrors():
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

    def __init__(self, str filename, str mode="r", int access_mode=0x644):
        cdef cmode = 0

        self._fname = filename
        self.binary = 'b' in mode

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

        return AIOOperation(LIO_READ, self, offset, size, priority)

    def write(self, data, unsigned int offset=0, int priority=0):
        if isinstance(data, str):
            bytes_data = data.encode()
        elif isinstance(data, bytes):
            bytes_data = data
        else:
            raise ValueError("Data must be str or bytes")

        op = AIOOperation(LIO_READ, self, offset, len(data), priority)
        op.buffer = bytes_data
        return op

    def flush(self, int priority=0):
        return AIOOperation(LIO_NOP, self, 0, 0, priority)


cdef class AIOOperation:
    cdef aiocb* _cb
    cdef unsigned int _buffer_size
    cdef bool _closed
    cdef int _state
    cdef object _result
    cdef bool _is_running

    def __init__(self, int state, AIOFile aio_file, unsigned int offset, int nbytes, int reqprio):

        self._state = state

        if state not in (LIO_READ, LIO_WRITE, LIO_NOP):
            raise ValueError("Invalid state")

        if self._state == LIO_READ:
            buffer_size = nbytes
        elif self._state == LIO_WRITE:
            buffer_size = nbytes
        elif self._state == LIO_NOP:
            buffer_size = 0

        self._result = None
        self._is_running = False
        self._cb = <aiocb*>calloc(1, sizeof(aiocb))
        self._buffer_size = buffer_size
        self._cb.aio_buf = <vvoid*>calloc(self._buffer_size, sizeof(char))
        self._cb.aio_fildes = aio_file.fd
        self._cb.aio_offset = offset
        self._cb.aio_nbytes = nbytes
        self._cb.aio_reqprio = reqprio
        self._cb.aio_sigevent.sigev_notify = SIGEV_NONE
        self._cb.aio_lio_opcode = state
        self._closed = False

    def run(self):
        cdef int result

        if self._is_running:
            raise RuntimeError("Operation already in progress")

        try:
            self._is_running = True

            if self._state == LIO_READ:
                result = aio_read(self._cb)
            elif self._state == LIO_WRITE:
                result = aio_write(self._cb)
            elif self._state == LIO_NOP:
                result = aio_fsync(O_SYNC, self._cb)
            else:
                raise RuntimeError('Oops...')

            _handle_cerrors()
        except LimitationError:
            self._is_running = False
            raise

    def _check_closed(self):
        if self._closed:
            raise RuntimeError("Can't perform operation in when closed object")

    def poll(self):
        self._check_closed()

        if not self._is_running:
            raise RuntimeError("Can't perform pool on not running operation")

        cdef int result = aio_error(self._cb)

        if result == EINPROGRESS:
            return True
        elif result == 0:
            return False
        else:
            return _handle_cerrors()

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

        if not self._is_running:
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
        self._check_closed()
        return self._cb.aio_nbytes

    @property
    def offset(self):
        self._check_closed()
        return self._cb.aio_offset

    @property
    def reqprio(self):
        self._check_closed()
        return self._cb.aio_reqprio

    @property
    def buffer(self):
        self._check_closed()
        return (<char*>self._cb.aio_buf)[:self._buffer_size]

    @buffer.setter
    def buffer(self, bytes data):
        self._check_closed()
        memcpy(self._cb.aio_buf, <vvoid*>(<char*>data), len(data))

    def result(self):
        cdef ssize_t result

        if self._result is None:
            if self.poll():
                raise RuntimeError("Operation in progress")

            result = aio_return(self._cb)
            self._result = (<char*>self._cb.aio_buf)[:result]

        return self._result

    @property
    def closed(self):
        return self._closed

    def close(self):
        self._check_closed()
        self._closed = True
        free(self._cb.aio_buf)
        free(self._cb)

    def __await__(self):
        while True:
            try:
                self.run()
                break
            except LimitationError:
                yield

        while self.poll():
            yield

        return self.result()

    __next__ = __await__

    def __iter__(self):
        return self

    def __dealloc__(self):
        if self._closed:
            return
        self.close()
