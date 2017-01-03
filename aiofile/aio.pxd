from posix.signal cimport sigevent
from posix.types cimport off_t, int
from posix.time cimport timespec


cdef extern from *:
     ctypedef int vvoid "volatile void"


cdef extern from "<aio.h>" nogil:

    # The order of these fields is implementation-dependent
    cdef struct aiocb:
        int             aio_fildes     # File descriptor
        off_t           aio_offset     # File offset
        vvoid            *aio_buf        # Location of buffer
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
    cdef int aio_suspend(
        const aiocb * const aiocb_list[],
        int nitems,
        const timespec *timeout
    )

    cdef int aio_cancel(int fd, aiocb *aiocbp)
    cdef int lio_listio(
        int mode,
        aiocb *const aiocb_list[],
        int nitems,
        sigevent *sevp
    )
