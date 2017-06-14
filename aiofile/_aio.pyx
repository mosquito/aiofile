from posix.signal cimport sigval, SIGEV_THREAD
from libc.errno cimport errno, EAGAIN
from libc.stdlib cimport calloc, free
from libc.string cimport memcpy, strerror
from posix.unistd cimport close as cclose
from posix.fcntl cimport open as copen, O_RDWR, O_APPEND, O_CREAT, O_RDONLY, O_SYNC, O_NONBLOCK
from posix.stat cimport fstat, struct_stat
from errno import errorcode
from cpython cimport bytes, bool

from posix.types cimport off_t
from posix.signal cimport sigevent


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


cdef enum:
    AIO_OP_INIT,
    AIO_OP_RUN,
    AIO_OP_DONE,


cdef void on_event(sigval sv):
    cdef int* val = <int*>sv.sival_ptr
    val[0] = AIO_OP_DONE


cpdef handle_errno(code=None):
    cdef str error_code
    cdef str error_st
    cdef object exc_class

    if not code:
        code = errno

    exc_class = SystemError

    if code == EAGAIN:
        exc_class = PermissionError

    if code in errorcode:
        error_code = errorcode[code]
        error_str = strerror(errno).decode()

        raise exc_class(error_code, error_str)

    return code


cdef int cfile_mode(str mode):
    if len(set(mode) & set("awr")) > 1:
        raise ValueError('must have exactly one of create/read/write/append mode')

    cdef int cmode = 0

    cmode |= O_NONBLOCK

    if '+' in mode:
        cmode |= O_CREAT
    if "a" in mode:
        cmode |= O_RDWR
        cmode |= O_APPEND
    elif "w" in mode:
        cmode |= O_RDWR
    elif "r" in mode:
        cmode |= O_RDONLY

    return cmode


cdef class AIOFile:
    cdef int __fileno
    cdef char* _fname

    def __cinit__(self, str filename, str mode="r", int access_mode=0o644):
        bfilename = filename.encode()
        self._fname = bfilename

        self.__fileno = copen(self._fname, cfile_mode(mode), access_mode)

        if self.__fileno == -1:
            raise IOError('Couldn\'t open file "%s"' % filename)

    def __repr__(self):
        return "<AIOFile: %r>" % self._fname

    cpdef void close(self):
        if self.__fileno == -2:
            return

        cclose(self.__fileno)
        self.__fileno = -2

    cpdef int fileno(self):
        return self.__fileno

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __dealloc__(self):
        self.close()

    def read(self, int size=-1, unsigned int offset=0, int priority=0):
        if size < -1:
            raise ValueError("Unsupported value %d for size" % size)

        cdef struct_stat stat
        if size == -1:
            fstat(self.__fileno, &stat)
            size = stat.st_size

        return AIOOperation(LIO_READ, self.__fileno, offset, size, priority)

    def write(self, data, unsigned int offset=0, int priority=0):
        if isinstance(data, str):
            bytes_data = data.encode()
        elif isinstance(data, bytes):
            bytes_data = data
        else:
            raise ValueError("Data must be str or bytes")

        op = AIOOperation(LIO_WRITE, self.__fileno, offset, len(data), priority)
        op.buffer = bytes_data
        return op

    def flush(self, int priority=0):
        return AIOOperation(LIO_NOP, self.__fileno, 0, 0, priority)


cdef class AIOOperation:
    cdef aiocb* cb
    cdef int state
    cdef size_t buffer_size
    cdef int offset
    cdef int __state

    def __cinit__(self, int state, int fd, unsigned int offset, int nbytes, int reqprio):
        if state not in (IO_READ, IO_WRITE, IO_NOP):
            raise ValueError("Invalid state")

        with nogil:
            self.__state = AIO_OP_INIT
            self.cb = <aiocb*>calloc(1, sizeof(aiocb))

        if self.state in (IO_READ, IO_WRITE):
            self.init_buffer(nbytes)
        else:
            self.init_buffer(0)

        with nogil:
            self.cb.aio_fildes = fd
            self.cb.aio_offset = offset
            self.cb.aio_nbytes = nbytes
            self.cb.aio_reqprio = reqprio
            self.cb.aio_lio_opcode = state
            self.cb.aio_sigevent.sigev_notify = SIGEV_THREAD
            self.cb.aio_sigevent.sigev_signo = 0
            self.cb.aio_sigevent.sigev_notify_function = on_event
            self.cb.aio_sigevent.sigev_value.sival_ptr = &self.__state
            self.cb.aio_sigevent.sigev_signo = 0

    def __iter__(self):
        if self.done():
            raise RuntimeError("Operation already done")

        if self.is_running():
            raise RuntimeError("Operation already in progress")

        self.__state = AIO_OP_RUN

        cdef int result
        cdef int error

        if self.cb.aio_lio_opcode == LIO_READ:
            result = aio_read(self.cb)
        elif self.cb.aio_lio_opcode == LIO_WRITE:
            result = aio_write(self.cb)
        elif self.cb.aio_lio_opcode == LIO_NOP:
            result = aio_fsync(O_SYNC, self.cb)

        error = errno
        handle_errno(error)

        while not self.done():
            yield

        self.buffer_size = aio_return(self.cb)

        return self.buffer

    cdef void init_buffer(self, size_t size):
        self.free_buffer()
        with nogil:
            self.buffer_size = size
            self.cb.aio_buf = <vvoid*>calloc(size, sizeof(char))

    cdef void set_buffer(self, bytes data):
        cdef char* cdata = <char*> data
        self.init_buffer(len(data) + 1)
        memcpy(self.cb.aio_buf, <vvoid*>(cdata), len(data))

    cdef void free_buffer(self):
        with nogil:
            if self.buffer_size:
                free(self.cb.aio_buf)
            self.buffer_size = 0

    @property
    def buffer(self):
        cdef char* buf = <char*>self.cb.aio_buf
        return buf[:self.buffer_size]

    @buffer.setter
    def buffer(self, bytes data):
        if self.cb.aio_lio_opcode != LIO_WRITE:
            raise TypeError("Buffer should be set only in IO_WRITE mode")

        if self.done():
            raise RuntimeError("Invalid state")

        self.set_buffer(data)

    cpdef bool done(self):
        return self.__state == AIO_OP_DONE

    cpdef bool is_running(self):
        return self.__state == AIO_OP_RUN

    def __await__(self):
        return self.__iter__()

    def __delloc__(self):
        self.free_buffer()
        free(self.cb)

    @property
    def fileno(self):
        return self.cb.aio_fildes

    @property
    def offset(self):
        return self.cb.aio_offset

    @property
    def nbytes(self):
        return self.cb.aio_nbytes

    @property
    def reqprio(self):
        return self.cb.aio_reqprio

    @property
    def opcode(self):
        return self.cb.aio_lio_opcode

    @property
    def opcode_str(self):
        if self.opcode == LIO_READ:
            return "IO_READ"
        elif self.opcode == LIO_WRITE:
            return "IO_WRITE"
        elif self.opcode == LIO_NOP:
            return "IO_NOP"

    def __repr__(self):
        return "<AIOOperation[{!r}, fd={}, offset={}, nbytes={}, reqprio={}]>".format(
            self.opcode_str,
            self.fileno,
            self.offset,
            self.nbytes,
            self.reqprio,
        )
