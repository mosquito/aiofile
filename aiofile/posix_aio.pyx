import sys

from cpython cimport bytes, bool
from libc.errno cimport EAGAIN, EBADF, EINVAL, ENOSYS, EOVERFLOW, errno
from libc.stdlib cimport calloc, free
from libc.stdint cimport uint32_t
from libc.string cimport memcpy
from posix.fcntl cimport O_DSYNC
from posix.signal cimport sigevent
from posix.signal cimport sigval, SIGEV_THREAD, SIGEV_NONE
from posix.types cimport off_t

import asyncio
import logging
import os
import platform
from errno import errorcode


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
    cdef int aio_cancel(int fd, aiocb *aiocbp)
    # cdef int aio_suspend(const aiocb * const aiocb_list[], int nitems, const timespec *timeout)
    # cdef int lio_listio(int mode, aiocb *const aiocb_list[], int nitems, sigevent *sevp)


IO_READ = LIO_READ
IO_WRITE = LIO_WRITE
IO_NOP = LIO_NOP


cpdef object log = logging.getLogger("aio")


cdef int get_supported_notify_method():
    if platform.system() == 'Darwin':
        return SIGEV_NONE
    elif platform.system() == 'Linux':
        return SIGEV_THREAD
    else:
        return SIGEV_NONE


cdef int SIGEV_TYPE = get_supported_notify_method()


cdef enum:
    AIO_OP_INIT,
    AIO_OP_RUN,
    AIO_OP_DONE,
    AIO_OP_CLOSED,


ctypedef void (*result_cb)()


cdef void on_event(sigval sv) with gil:
    cdef unsigned long long* val = <unsigned long long*>sv.sival_ptr

    op = OP_MAP.pop(val[0], None)

    if op:
        op._set_result()



cdef dict AIO_READ_ERRORS = {
    EAGAIN: "Because of system resource limitations, the request was not queued.",
    ENOSYS: "The aio_read() system call is not supported.",
    EBADF: "The aiocb.aio_fildes argument is invalid for reading.",
    EINVAL: (
        "The offset aiocb.aio_offset is not valid, "
        "the priority specified by aiocb.aio_reqprio is not a valid priority, or "
        "the number of bytes specified by aiocb.aio_nbytes is not valid."
    ),
    EOVERFLOW: (
        "The file is a regular file, aiocb.aio_nbytes is greater than zero, "
        "the starting offset in aiocb.aio_offset is before the end of the file, "
        "but is at or beyond the aiocb.aio_fildes offset maximum."
    ),
    # ECANCELED: "The request was explicitly cancelled via a call to aio_cancel().",
}


cdef dict AIO_WRITE_ERRORS = {
    EBADF: "The aiocb.aio_fildes argument is invalid, or is not opened for writing.",
    EINVAL: (
        "The offset aiocb.aio_offset is not valid. "
        "The priority specified by aiocb.aio_reqprio is not a valid priority. "
        "The number of bytes specified by aiocb.aio_nbytes is not valid. "
        "The constant in aiocb.aio_sigevent.sigev_notify is set "
        "to SIGEV_THREAD (SIGEV_THREAD is not supported)."
    ),
    EAGAIN: "Due to system resource limitations, the request was not queued.",
    ENOSYS: "The aio_write() system call is not supported.",
    # ECANCELED: "The request was explicitly canceled via a call to aio_cancel().",
}

cdef dict AIO_FSYNC_ERRORS = {
    EAGAIN: "Out of resources.",
    EBADF: "aio_fildes is not a valid file descriptor open for writing.",
    EINVAL: "Synchronized I/O is not supported for this file, or op is not O_SYNC or O_DSYNC.",
    ENOSYS: "aio_fsync() is not implemented."
}


cdef class SimpleSemaphore:
    cdef uint32_t value
    cdef uint32_t max_value

    def __cinit__(self, uint32_t max_value):
        self.value = 0
        self.max_value = max_value

    def acquire(self):
        yield

        while True:
            if self.value >= self.max_value:
                yield
            else:
                self.value += 1
                return

    cpdef set_max_value(self):
        self.max_value = self.value - 1

    cpdef release(self):
        if self.value > 0:
            self.value -= 1


cdef object semaphore = SimpleSemaphore(2 ** 31)
cdef dict OP_MAP = dict()


if sys.version_info < (3, 7):
    def _run_coro(coro):
        return coro.__iter__()
else:
    def _run_coro(coro):
        return coro.__await__()


cdef class AIOOperation:
    cdef aiocb* cb
    cdef char* __buffer
    cdef int size
    cdef int __state
    cdef unsigned long long cid
    cdef object loop
    cdef object event

    cpdef _set_result(self):
        self.loop.call_soon_threadsafe(self.event.set)

    def __cinit__(self, int opcode, int fd, off_t offset, int nbytes, loop):
        if opcode not in (LIO_READ, LIO_WRITE, LIO_NOP):
            raise ValueError("Invalid state")

        self.loop = loop
        self.event = asyncio.Event(loop=self.loop)
        self.cid = id(self)

        with nogil:
            self.__state = AIO_OP_INIT
            self.size = 0

            self.__buffer = <char*>calloc(nbytes + 1, sizeof(char))

            self.cb = <aiocb*>calloc(1, sizeof(aiocb))
            self.cb.aio_buf = <vvoid*> self.__buffer

            self.cb.aio_fildes = fd
            self.cb.aio_offset = offset
            self.cb.aio_nbytes = nbytes
            self.cb.aio_reqprio = 0
            self.cb.aio_lio_opcode = opcode
            self.cb.aio_sigevent.sigev_notify = SIGEV_TYPE

        if SIGEV_TYPE == SIGEV_THREAD:
            self.cb.aio_sigevent.sigev_notify_function = on_event
            self.cb.aio_sigevent.sigev_value.sival_ptr = <void*>&self.cid
            OP_MAP[self.cid] = self

    @property
    def buffer(self):
        if self.__buffer == NULL:
            raise RuntimeError('Null buffer access')

        return self.__buffer[:self.size]

    @buffer.setter
    def buffer(self, bytes data):
        if self.cb.aio_lio_opcode != LIO_WRITE:
            raise TypeError("Buffer should be set only in IO_WRITE mode")

        if self.done():
            raise RuntimeError("Invalid state")

        cdef int data_len = len(data)

        if data_len > self.cb.aio_nbytes:
            raise ValueError("Data too long")

        cdef char* cdata = <char*> data

        with nogil:
            memcpy(self.__buffer, cdata, data_len)
            self.cb.aio_nbytes = data_len
            self.size = data_len

    cpdef aio_read(self):
        cdef int result = 0
        cdef int error = 0

        with nogil:
            result = aio_read(self.cb)

            if result != 0:
                result = aio_error(self.cb)
            if result != 0:
                error = result
            error = errno

        if result == 0:
            return

        raise SystemError(
            errorcode[error],
            AIO_READ_ERRORS.get(error, os.strerror(error))
        )

    cpdef aio_write(self):
        cdef int result = 0
        cdef int error = 0

        with nogil:
            result = aio_write(self.cb)

            if result != 0:
                result = aio_error(self.cb)

            if result != 0:
                error = result

            error = errno

        if result == 0:
            return

        raise SystemError(
            errorcode[error],
            AIO_WRITE_ERRORS.get(error, os.strerror(error))
        )

    cpdef aio_fsync(self):
        cdef int result = 0
        cdef int error = 0

        with nogil:
            result = aio_fsync(O_DSYNC, self.cb)

            if result != 0:
                result = aio_error(self.cb)

            if result != 0:
                error = result

            error = errno

        if result == 0:
            return

        raise SystemError(
            errorcode[error],
            AIO_FSYNC_ERRORS.get(error, os.strerror(error))
        )

    cpdef aio_cancel(self):
        aio_cancel(self.cb.aio_fildes, self.cb)

    def __await__(self):
        cdef int result = 0
        cdef int error = 0

        try:
            yield from semaphore.acquire()

            if self.done():
                raise RuntimeError("Operation already done")

            if self.is_running():
                raise RuntimeError("Operation already in progress")

            self.__state = AIO_OP_RUN

            # Trying to detect maximum concurrent AIO operations
            while True:
                try:
                    if self.cb.aio_lio_opcode == LIO_READ:
                        self.aio_read()
                    elif self.cb.aio_lio_opcode == LIO_WRITE:
                        self.aio_write()
                    elif self.cb.aio_lio_opcode == LIO_NOP:
                        self.aio_fsync()

                    break
                except SystemError as e:
                    if e.args[0] == 'EINVAL':
                        semaphore.release()
                        semaphore.set_max_value()
                        yield from semaphore.acquire()
                    else:
                        raise

            # Awaiting callback when SIGEV_THREAD  (Linux)
            if self.cb.aio_sigevent.sigev_notify == SIGEV_THREAD:
                yield from _run_coro(self.event.wait())

                with nogil:
                    result = aio_error(self.cb)
                    error = errno

                if result != 0:
                    raise SystemError(
                        errorcode[result],
                        os.strerror(result)
                    )

            # Polling aio_error when SIGEV_NONE (Mac OS X)
            elif self.cb.aio_sigevent.sigev_notify == SIGEV_NONE:
                while True:
                    with nogil:
                        result = aio_error(self.cb)
                        error = errno

                    if result == 0:
                        break
                    elif result == -1:
                        raise SystemError(
                            errorcode[error],
                            os.strerror(error)
                        )
                    else:
                        yield

            self.__state = AIO_OP_DONE

            rresult = aio_return(self.cb)
            if rresult < 0:
                raise SystemError(errorcode[-rresult], os.strerror(-rresult))

            self.size = rresult
            return self.buffer
        except asyncio.CancelledError:
            if self.is_running():
                self.aio_cancel()
            raise
        finally:
            semaphore.release()

    cpdef bool done(self):
        return self.__state == AIO_OP_DONE

    cpdef bool is_running(self):
        return self.__state == AIO_OP_RUN

    cpdef void close(self):
        if self.__state == AIO_OP_CLOSED:
            return

        with nogil:
            self.__state = AIO_OP_CLOSED

            free(self.__buffer)
            free(self.cb)

            self.__buffer = NULL
            self.cb = NULL

    def __dealloc__(self):
        self.close()
        OP_MAP.pop(self.cid, None)

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
        if self.cb.aio_lio_opcode == LIO_READ:
            return "IO_READ"
        elif self.cb.aio_lio_opcode == LIO_WRITE:
            return "IO_WRITE"
        elif self.cb.aio_lio_opcode == LIO_NOP:
            return "IO_NOP"

    def __repr__(self):
        return "<AIOOperation[{!r}, fd={}, offset={}, nbytes={}, reqprio={}]>".format(
            self.opcode_str,
            self.fileno,
            self.offset,
            self.nbytes,
            self.reqprio,
        )
