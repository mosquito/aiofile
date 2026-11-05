"""Native file lifecycle through caio's io_uring AsyncioContext.

These tests need a caio whose io_uring AsyncioContext implements the
native file operations contract (FILE_OPERATIONS_VERSION == 1). They are
skipped when io_uring is unavailable or caio is too old.
"""

import asyncio
import builtins
import errno
import gc
import os
import traceback
import weakref
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import caio
import pytest
from caio.python_aio_asyncio import AsyncioContext as PythonContext

from aiofile import AIOFile, aio as aio_module, async_open, clone
from aiofile.aio import get_default_context


@pytest.fixture
def native():
    try:
        from caio import linux_uring_asyncio
    except ImportError as exc:
        pytest.skip(f"caio io_uring backend is unavailable: {exc}")
    if getattr(linux_uring_asyncio.AsyncioContext,
               "FILE_OPERATIONS_VERSION", None) != 1:
        pytest.skip("caio lacks native io_uring file operations")
    from caio.abstract import CloseError
    return SimpleNamespace(
        module=linux_uring_asyncio,
        AsyncioContext=linux_uring_asyncio.AsyncioContext,
        Operation=linux_uring_asyncio.Operation,
        Context=linux_uring_asyncio.Context,
        CloseError=CloseError,
    )


@pytest.fixture
async def context(native):
    async with native.AsyncioContext(sqpoll=False) as ctx:
        yield ctx


@pytest.fixture
async def no_executor():
    """Fail if the running loop hands any work to an executor."""
    with mock.patch.object(
        asyncio.get_running_loop(), "run_in_executor",
        side_effect=AssertionError("native path used an executor"),
    ):
        yield


@contextmanager
def reject_workload_io(root):
    """Fail on synchronous open/stat/ftruncate of anything under root."""
    def guard(operation, name):
        def guarded(path, *args, **kwargs):
            if (
                isinstance(path, int)
                or Path(os.fsdecode(path)).is_relative_to(root)
            ):
                raise AssertionError(f"workload used synchronous {name}")
            return operation(path, *args, **kwargs)
        return guarded

    with ExitStack() as stack:
        for module, name in [
            (builtins, "open"), (os, "stat"), (os, "ftruncate"),
        ]:
            operation = getattr(module, name)
            stack.enter_context(mock.patch.object(
                module, name, side_effect=guard(operation, name),
            ))
        yield


def patch_close(context, replacement):
    """Route close operations through replacement(op, real_submit)."""
    original = context.submit

    async def submit(op):
        if getattr(op, "closes_file", False):
            return await replacement(op, original)
        return await original(op)

    return mock.patch.object(context, "submit", side_effect=submit)


def assert_closed(fd):
    with pytest.raises(OSError) as caught:
        os.fstat(fd)
    assert caught.value.errno == errno.EBADF


def reopen_same_fd(path, fd):
    """Open path again; Linux hands out the lowest free descriptor."""
    replacement = os.open(path, os.O_RDONLY)
    assert replacement == fd
    return replacement


async def test_stat_and_truncate(aio_file_maker, tmp_path):
    path = tmp_path / "file.bin"
    async with aio_file_maker(path, "w+b") as afp:
        await afp.write_bytes(b"0123456789")
        await afp.fsync()
        assert (await afp.stat()).st_size == 10
        await afp.truncate(4)
        assert (await afp.stat()).st_size == 4
        assert await afp.read_bytes() == b"0123"
    assert path.read_bytes() == b"0123"


async def test_append_starts_at_end_of_file(async_open, tmp_path):
    path = tmp_path / "append.txt"
    path.write_text("original")
    async with async_open(path, "a") as afp:
        assert afp.tell() == 8
        await afp.write("!")
    assert path.read_text() == "original!"


async def test_binary_lifecycle_has_no_sync_io(
    native, context, no_executor, tmp_path,
):
    path = tmp_path / "native.bin"
    with reject_workload_io(tmp_path):
        afp = AIOFile(path, "w+b", context=context)
        assert afp.closed
        async with afp:
            assert not afp.closed
            assert await afp.write_bytes(b"native payload") == 14
            assert await afp.read_bytes() == b"native payload"
            assert (await afp.stat()).st_size == 14
            await afp.truncate(6)
            assert await afp.read_bytes() == b"native"
            await afp.fsync()
            await afp.fdsync()
        assert afp.closed
        await afp.close()
    assert path.read_bytes() == b"native"


async def test_text_append_wrapper_has_no_sync_io(
    native, context, no_executor, tmp_path,
):
    path = tmp_path / "unicode.txt"
    path.write_text("héllo", encoding="utf-8")
    with reject_workload_io(tmp_path):
        async with async_open(path, "a", context=context) as afp:
            assert afp.tell() == 6
            assert await afp.write(" world") == 6
            assert afp.tell() == 12
        afp = await async_open(path, "r", context=context)
        try:
            assert await afp.read() == "héllo world"
        finally:
            await afp.close()
    assert path.read_text(encoding="utf-8") == "héllo world"


async def test_borrowed_descriptor_is_not_closed(native, context, tmp_path):
    with open(tmp_path / "borrowed", "w+b") as raw:
        afp = AIOFile.from_fp(raw, context=context)
        await afp.open()
        await afp.write_bytes(b"borrowed")
        assert await afp.read_bytes() == b"borrowed"
        await afp.close()
        assert not raw.closed
        assert os.fstat(raw.fileno()).st_size == 8


async def test_borrowed_append_reads_from_eof_unless_seeked(
    native, context, tmp_path,
):
    with open(tmp_path / "borrowed", "a+b") as raw:
        raw.write(b"existing\n")
        raw.flush()
        first = async_open(raw, context=context)
        assert await first.read() == b""
        second = async_open(raw, context=context)
        second.seek(0)
        assert await second.readline() == b"existing\n"
        await first.close()
        await second.close()
        assert not raw.closed


async def test_repeated_close_spares_reused_descriptor(
    native, context, tmp_path,
):
    path = tmp_path / "closed.bin"
    afp = AIOFile(path, "w+b", context=context)
    fd = await afp.open()
    assert isinstance(fd, int)
    await afp.close()
    replacement = reopen_same_fd(path, fd)
    try:
        await afp.close()
        assert os.fstat(replacement).st_size == 0
        with pytest.raises(asyncio.InvalidStateError):
            await afp.open()
    finally:
        os.close(replacement)


async def test_concurrent_open_and_close_are_idempotent(
    native, context, tmp_path,
):
    afp = AIOFile(tmp_path / "shared.bin", "w+b", context=context)
    results = await asyncio.gather(afp.open(), afp.open())
    assert sum(result is not None for result in results) == 1
    await asyncio.gather(afp.close(), afp.close())
    assert afp.closed


async def test_clone_keeps_source_descriptor_alive(native, context, tmp_path):
    afp = await async_open(tmp_path / "cloned.bin", "w+b", context=context)
    cloned = await clone(afp)
    await afp.close()
    assert not cloned.closed
    await cloned.write(b"clone")
    cloned.seek(0)
    assert await cloned.read() == b"clone"
    await cloned.close()
    assert afp.closed


async def test_open_error_has_filename_and_allows_retry(
    native, context, tmp_path,
):
    path = tmp_path / "missing.bin"
    afp = AIOFile(path, "rb", context=context)
    with pytest.raises(FileNotFoundError) as caught:
        await afp.open()
    assert caught.value.filename == str(path)
    assert afp.closed
    path.write_bytes(b"retry")
    async with afp:
        assert await afp.read_bytes() == b"retry"


@pytest.mark.parametrize("mode", ["wbq", "bb", "wbb", "wbt"])
async def test_open_rejects_invalid_modes(native, context, tmp_path, mode):
    afp = AIOFile(tmp_path / "invalid", mode, context=context)
    with pytest.raises(ValueError):
        await afp.open()
    assert afp.closed


async def test_open_rejects_directory_without_leaking(
    native, context, tmp_path,
):
    afp = AIOFile(tmp_path, "rb", context=context)
    with pytest.raises(IsADirectoryError) as caught:
        await afp.open()
    assert caught.value.filename == str(tmp_path)
    assert afp.closed


async def test_cancelled_open_owns_no_descriptor(native, tmp_path):
    async with native.AsyncioContext(sqpoll=False, max_requests=1) as ctx:
        await ctx.semaphore.acquire()
        afp = AIOFile(tmp_path / "cancelled", "w+b", context=ctx)
        started = asyncio.Event()
        original_open = ctx.open

        async def observed_open(path, flags, mode=0o666):
            started.set()
            return await original_open(path, flags, mode)

        with mock.patch.object(ctx, "open", side_effect=observed_open):
            task = asyncio.ensure_future(afp.open())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        ctx.semaphore.release()
        assert afp.closed
        async with afp:
            assert await afp.write_bytes(b"retry") == 5


async def test_cancelled_close_settles_before_releasing_ownership(
    native, tmp_path,
):
    async with native.AsyncioContext(sqpoll=False, max_requests=1) as ctx:
        afp = AIOFile(tmp_path / "cancelled-close", "w+b", context=ctx)
        await afp.open()
        fd = afp.fileno()
        await ctx.semaphore.acquire()

        syncing = asyncio.Event()
        original_fdsync = ctx.fdsync

        async def observed_fdsync(value):
            syncing.set()
            return await original_fdsync(value)

        # Each cancellation makes the cleanup loop shield the close again.
        first_retry = asyncio.Event()
        second_retry = asyncio.Event()
        original_shield = asyncio.shield
        calls = 0

        def observed_shield(future):
            nonlocal calls
            calls += 1
            if calls == 2:
                first_retry.set()
            elif calls == 3:
                second_retry.set()
            return original_shield(future)

        with mock.patch.object(
            ctx, "fdsync", side_effect=observed_fdsync,
        ), mock.patch("asyncio.shield", side_effect=observed_shield):
            task = asyncio.ensure_future(afp.close())
            await syncing.wait()
            task.cancel()
            await first_retry.wait()
            task.cancel()
            await second_retry.wait()
            assert not task.done()
            assert not afp.closed
            ctx.semaphore.release()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert afp.closed
        assert_closed(fd)


@pytest.mark.parametrize("released", [False, True])
async def test_cancellation_keeps_cleanup_error_as_cause(
    native, context, tmp_path, released,
):
    afp = AIOFile(tmp_path / "cancel-error", "w+b", context=context)
    await afp.open()
    fd = afp.fileno()
    started = asyncio.Event()
    release = asyncio.Event()

    async def failed_close(op, submit):
        started.set()
        await release.wait()
        if released:
            await submit(op)
        raise native.CloseError(
            OSError(errno.EIO, "cleanup failed"), fd_closed=released,
        )

    with patch_close(context, failed_close):
        task = asyncio.ensure_future(afp.close())
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await task
    assert task.cancelled()
    assert isinstance(cancelled.value.__cause__, native.CloseError)
    assert afp.closed == released
    if released:
        assert_closed(fd)
    else:
        assert os.fstat(fd).st_size == 0
        await afp.close()


async def test_sync_failure_still_closes_descriptor(native, context, tmp_path):
    afp = AIOFile(tmp_path / "sync-failure", "w+b", context=context)
    await afp.open()
    fd = afp.fileno()
    with mock.patch.object(
        context, "fdsync", side_effect=OSError("sync failed"),
    ):
        with pytest.raises(OSError, match="sync failed"):
            await afp.close()
    assert afp.closed
    assert_closed(fd)


async def test_cancelled_stat_during_open_releases_descriptor(
    native, context, tmp_path,
):
    afp = AIOFile(tmp_path / "cancelled-stat", "w+b", context=context)
    started = asyncio.Event()
    release = asyncio.Event()
    descriptors = []
    original_stat = context.stat

    async def observed_stat(fd):
        descriptors.append(fd)
        started.set()
        await release.wait()
        return await original_stat(fd)

    with mock.patch.object(context, "stat", side_effect=observed_stat):
        task = asyncio.ensure_future(afp.open())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert afp.closed
    assert len(descriptors) == 1
    assert_closed(descriptors[0])


async def test_finalizer_registration_failure_closes_descriptor(
    native, context, tmp_path,
):
    afp = AIOFile(tmp_path / "registration-failure", "w+b", context=context)
    descriptors = []
    original_open = context.open

    async def observed_open(path, flags, mode=0o666):
        fd = await original_open(path, flags, mode)
        descriptors.append(fd)
        return fd

    failure = MemoryError("finalizer registration failed")
    with mock.patch.object(
        context, "open", side_effect=observed_open,
    ), mock.patch.object(aio_module, "finalize", side_effect=failure):
        with pytest.raises(MemoryError) as caught:
            await afp.open()
    assert caught.value is failure
    assert len(descriptors) == 1
    assert afp.closed
    assert_closed(descriptors[0])
    async with afp:
        assert await afp.write_bytes(b"retry") == 5


async def test_finalizer_detach_failure_releases_ownership(
    native, context, tmp_path,
):
    path = tmp_path / "detach-failure"
    afp = AIOFile(path, "w+b", context=context)
    await afp.open()
    fd = afp.fileno()
    finalizer = afp._native_finalizer
    assert finalizer is not None
    original_detach = weakref.finalize.detach
    failure = MemoryError("finalizer detach failed")

    def detach_then_fail(instance):
        result = original_detach(instance)
        if instance is finalizer:
            raise failure
        return result

    with mock.patch.object(
        weakref.finalize, "detach", autospec=True,
        side_effect=detach_then_fail,
    ):
        with pytest.raises(MemoryError) as caught:
            await afp.close()
    assert caught.value is failure
    assert afp.closed
    assert afp._native_finalizer is None
    assert not finalizer.alive

    replacement = reopen_same_fd(path, fd)
    try:
        await afp.close()
        del afp
        gc.collect()
        assert os.fstat(replacement).st_size == 0
    finally:
        os.close(replacement)


@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("append", [False, True])
async def test_cancelled_initial_stat_keeps_cleanup_failure(
    native, context, tmp_path, append, released,
):
    stream = async_open(tmp_path / "stat-cancel", "a+b", context=context)
    afp = stream.file
    started = asyncio.Event()
    blocked = asyncio.Event()
    cancellations = []
    descriptors = []
    original_stat = context.stat
    calls = 0
    failure = native.CloseError(
        OSError(errno.EIO, "failed cleanup"), fd_closed=released,
    )

    async def held_stat(fd):
        nonlocal calls
        calls += 1
        # Append mode stats once to open and once to find the end.
        if calls == (2 if append else 1):
            descriptors.append(fd)
            started.set()
            try:
                await blocked.wait()
            except asyncio.CancelledError as error:
                cancellations.append(error)
                raise
        return await original_stat(fd)

    async def failed_close(op, submit):
        if released:
            await submit(op)
        raise failure

    async def open_file():
        if append:
            await stream.__aenter__()
        else:
            await afp.open()

    with mock.patch.object(
        context, "stat", side_effect=held_stat,
    ), patch_close(context, failed_close):
        task = asyncio.ensure_future(open_file())
        await started.wait()
        task.cancel("initial stat cancelled")
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
    assert task.cancelled()
    assert len(cancellations) == 1
    assert caught.value is cancellations[0]
    assert caught.value.__cause__ is failure
    assert afp.closed == released
    if released:
        assert_closed(descriptors[0])
    else:
        assert os.fstat(descriptors[0]).st_size == 0
        await afp.close()


async def test_collected_file_releases_descriptor(native, context, tmp_path):
    afp = AIOFile(tmp_path / "collected", "w+b", context=context)
    await afp.open()
    fd = afp.fileno()
    reference = weakref.ref(afp)
    del afp
    gc.collect()
    assert reference() is None
    assert_closed(fd)


async def test_collected_file_keeps_descriptor_for_pending_truncate(
    native, context, tmp_path,
):
    path = tmp_path / "pending-truncate"
    afp = AIOFile(path, "w+b", context=context)
    await afp.open()
    fd = afp.fileno()
    operation = afp.truncate(8)
    del afp
    gc.collect()
    assert os.fstat(fd).st_size == 0
    await operation
    assert path.stat().st_size == 8


async def test_released_close_error_does_not_retain_descriptor(
    native, context, tmp_path,
):
    path = tmp_path / "close-error"
    afp = AIOFile(path, "w+b", context=context)
    await afp.open()
    fd = afp.fileno()

    async def released_close(op, submit):
        await submit(op)
        raise native.CloseError(
            OSError(errno.EIO, "flush error"), fd_closed=True,
        )

    with patch_close(context, released_close):
        with pytest.raises(native.CloseError):
            await afp.close()
    assert afp.closed

    replacement = reopen_same_fd(path, fd)
    try:
        await afp.close()
        del afp
        gc.collect()
        assert os.fstat(replacement).st_size == 0
    finally:
        os.close(replacement)


async def test_memory_error_after_close_spares_reused_descriptor(
    native, context, tmp_path,
):
    path = tmp_path / "close-memory-error"
    afp = AIOFile(path, "w+b", context=context)
    await afp.open()
    fd = afp.fileno()
    original_submit = context._submit_op
    original_wait = context._wait_completion
    closing = False
    injected = False

    def observed_submit(op, future):
        nonlocal closing
        original_submit(op, future)
        closing = op.closes_file

    async def after_completion(future):
        nonlocal injected
        result = await original_wait(future)
        if closing and not injected:
            injected = True
            raise MemoryError("after native close completed")
        return result

    with mock.patch.object(
        context, "_submit_op", side_effect=observed_submit,
    ), mock.patch.object(
        context, "_wait_completion", side_effect=after_completion,
    ):
        with pytest.raises(native.CloseError) as caught:
            await afp.close()
    assert caught.value.errno == errno.ENOMEM
    assert isinstance(caught.value.__cause__, MemoryError)
    assert afp.closed

    replacement = reopen_same_fd(path, fd)
    try:
        await afp.close()
        del afp
        gc.collect()
        assert os.fstat(replacement).st_size == 0
    finally:
        os.close(replacement)


async def test_close_error_construction_failure_releases_ownership(
    native, context, tmp_path,
):
    path = tmp_path / "close-error-construction"
    path.write_bytes(b"owned")
    afp = AIOFile(path, "rb", context=context)
    await afp.open()
    fd = afp.fileno()
    finalizer = afp._native_finalizer
    assert finalizer is not None
    original_submit = context._submit_op
    original_wait = context._wait_completion
    closing = None
    failure = MemoryError("CloseError construction failed after CLOSE CQE")

    def observed_submit(op, future):
        nonlocal closing
        original_submit(op, future)
        if op.closes_file:
            closing = future

    async def failed_after_close(future):
        result = await original_wait(future)
        if future is closing:
            raise OSError(errno.EIO, "adapter failure after CLOSE")
        return result

    with mock.patch.object(
        context, "_submit_op", side_effect=observed_submit,
    ), mock.patch.object(
        context, "_wait_completion", side_effect=failed_after_close,
    ), mock.patch.object(native.module, "CloseError", side_effect=failure):
        with pytest.raises(MemoryError) as caught:
            await afp.close()
    assert caught.value is failure
    assert afp.closed
    assert not finalizer.alive

    replacement = reopen_same_fd(path, fd)
    try:
        await afp.close()
        del afp
        gc.collect()
        assert os.fstat(replacement).st_size == 5
    finally:
        os.close(replacement)


async def test_close_bookkeeping_failure_releases_ownership(
    native, context, tmp_path,
):
    path = tmp_path / "close-finally-failure"
    path.write_bytes(b"owned")
    afp = AIOFile(path, "rb", context=context)
    await afp.open()
    fd = afp.fileno()
    finalizer = afp._native_finalizer
    assert finalizer is not None
    original_release = context.semaphore.release
    failure = MemoryError("CLOSE bookkeeping failed")

    def failed_release():
        original_release()
        raise failure

    with mock.patch.object(
        context.semaphore, "release", side_effect=failed_release,
    ):
        with pytest.raises(MemoryError) as caught:
            await afp.close()
    assert caught.value is failure
    assert afp.closed
    assert not finalizer.alive

    replacement = reopen_same_fd(path, fd)
    try:
        await afp.close()
        del afp
        gc.collect()
        assert os.fstat(replacement).st_size == 5
    finally:
        os.close(replacement)


@pytest.mark.parametrize("cleanup", ["retry", "gc"])
async def test_close_operation_construction_failure_keeps_ownership(
    native, context, tmp_path, cleanup,
):
    path = tmp_path / "close-construction-failure"
    path.write_bytes(b"owned")
    afp = AIOFile(path, "rb", context=context)
    await afp.open()
    fd = afp.fileno()
    finalizer = afp._native_finalizer
    assert finalizer is not None
    failure = MemoryError("Operation.close allocation failed")
    factory = mock.Mock(spec=native.Operation)
    factory.close.side_effect = failure

    with mock.patch.object(context, "OPERATION_CLASS", factory):
        with pytest.raises(MemoryError) as caught:
            await afp.close()
    assert caught.value is failure
    assert not afp.closed
    assert finalizer.alive
    assert os.fstat(fd).st_size == 5

    if cleanup == "retry":
        await afp.close()
        assert afp.closed
        assert not finalizer.alive
    else:
        # The raised exception's traceback still references afp's frames.
        traceback.clear_frames(caught.tb)
        reference = weakref.ref(afp)
        del afp
        gc.collect()
        assert reference() is None
    assert_closed(fd)


async def test_open_retries_rejected_cleanup_of_failed_open(
    native, context, tmp_path,
):
    path = tmp_path / "retry-rejected-cleanup"
    path.write_bytes(b"retry")
    afp = AIOFile(path, "rb", context=context)
    descriptors = []

    async def failed_stat(fd):
        descriptors.append(fd)
        raise OSError(errno.EIO, "injected stat failure")

    async def rejected_close(op, submit):
        raise native.CloseError(
            OSError(errno.EAGAIN, "not submitted"), fd_closed=False,
        )

    with mock.patch.object(
        context, "stat", side_effect=failed_stat,
    ), patch_close(context, rejected_close):
        with pytest.raises(native.CloseError):
            await afp.open()
    assert not afp.closed
    assert os.fstat(descriptors[0]).st_size == 5

    async with afp:
        assert await afp.read_bytes() == b"retry"
    assert afp.closed
    assert_closed(descriptors[0])


async def test_rejected_close_keeps_descriptor(native, context, tmp_path):
    afp = AIOFile(tmp_path / "reject-close", "w+b", context=context)
    await afp.open()
    fd = afp.fileno()

    async def rejected_close(op, submit):
        raise native.CloseError(
            OSError(errno.EAGAIN, "not submitted"), fd_closed=False,
        )

    with patch_close(context, rejected_close):
        with pytest.raises(native.CloseError):
            await afp.close()
    assert not afp.closed
    assert os.fstat(fd).st_size == 0
    await afp.close()
    assert afp.closed


async def test_append_initialization_failure_closes_file(
    native, context, tmp_path,
):
    original_stat = context.stat
    descriptors = []

    async def append_stat(fd):
        descriptors.append(fd)
        if len(descriptors) == 2:
            raise OSError("append stat failed")
        return await original_stat(fd)

    stream = async_open(tmp_path / "append-failure", "a+b", context=context)
    with mock.patch.object(context, "stat", side_effect=append_stat):
        with pytest.raises(OSError, match="append stat failed"):
            await stream.__aenter__()
    assert stream.closed
    assert len(descriptors) == 2
    assert_closed(descriptors[0])


async def test_append_initialization_cancellation_closes_file(
    native, context, tmp_path,
):
    original_stat = context.stat
    started = asyncio.Event()
    release = asyncio.Event()
    descriptors = []

    async def append_stat(fd):
        descriptors.append(fd)
        if len(descriptors) == 2:
            started.set()
            await release.wait()
        return await original_stat(fd)

    stream = async_open(tmp_path / "append-cancel", "a+b", context=context)
    with mock.patch.object(context, "stat", side_effect=append_stat):
        task = asyncio.ensure_future(stream.__aenter__())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed
    assert len(descriptors) == 2
    assert_closed(descriptors[0])


async def test_failed_open_cleanup_allows_retry_after_second_cancel(
    native, context, tmp_path,
):
    path = tmp_path / "retry-after-cleanup"
    path.write_bytes(b"retry")
    afp = AIOFile(path, "rb", context=context)
    stat_started = asyncio.Event()
    close_started = asyncio.Event()
    release_stat = asyncio.Event()
    release_close = asyncio.Event()

    async def blocked_stat(fd):
        stat_started.set()
        await release_stat.wait()
        return os.fstat(fd)

    async def blocked_close(op, submit):
        close_started.set()
        await release_close.wait()
        return await submit(op)

    with mock.patch.object(
        context, "stat", side_effect=blocked_stat,
    ), patch_close(context, blocked_close):
        task = asyncio.ensure_future(afp.open())
        await stat_started.wait()
        task.cancel()
        await close_started.wait()
        task.cancel()
        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert afp.closed
    async with afp:
        assert await afp.read_bytes() == b"retry"


async def test_failed_append_clone_can_be_retried(native, context, tmp_path):
    path = tmp_path / "retry-clone"
    path.write_bytes(b"old")
    afp = await async_open(path, "a+b", context=context)
    cloner = clone(afp)
    with mock.patch.object(
        context, "stat", side_effect=OSError("clone stat"),
    ):
        with pytest.raises(OSError, match="clone stat"):
            await cloner
    assert not afp.closed

    recovered = await cloner
    assert recovered.tell() == 3
    await afp.close()
    assert not recovered.closed
    await recovered.close()
    assert afp.closed


async def test_cancelled_append_clone_can_be_retried(
    native, context, tmp_path,
):
    path = tmp_path / "cancel-clone"
    path.write_bytes(b"old")
    afp = await async_open(path, "a+b", context=context)
    cloner = clone(afp)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_stat(fd):
        started.set()
        await release.wait()
        return os.fstat(fd)

    with mock.patch.object(context, "stat", side_effect=blocked_stat):
        task = asyncio.ensure_future(cloner)
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    recovered = await cloner
    assert recovered.tell() == 3
    await afp.close()
    assert not recovered.closed
    await recovered.close()
    assert afp.closed


async def test_data_io_does_not_use_an_executor(
    native, context, no_executor, tmp_path,
):
    assert isinstance(context.context, native.Context)
    with open(tmp_path / "data", "w+b") as raw:
        afp = AIOFile.from_fp(raw, context=context)
        payload = b"native io_uring data I/O"
        assert await afp.write_bytes(payload) == len(payload)
        assert await afp.read_bytes(len(payload)) == payload
        await afp.fsync()


async def test_python_context_works_when_io_uring_is_denied(native, tmp_path):
    with mock.patch.object(
        native.AsyncioContext, "CONTEXT_CLASS",
        side_effect=PermissionError("io_uring creation denied"),
    ):
        async with PythonContext() as ctx:
            async with AIOFile(
                tmp_path / "compat.bin", "w+b", context=ctx,
            ) as afp:
                assert await afp.write_bytes(b"compatible") == 10
                assert await afp.read_bytes(10) == b"compatible"


@pytest.fixture
def uring_is_default(native):
    if caio.AsyncioContext is not native.AsyncioContext:
        pytest.skip("caio's default backend is not io_uring here")


async def test_default_context_propagates_io_uring_failure(
    native, uring_is_default,
):
    denied = PermissionError("io_uring creation denied")
    with mock.patch.object(
        native.AsyncioContext, "CONTEXT_CLASS", side_effect=denied,
    ):
        with pytest.raises(PermissionError) as caught:
            AIOFile("never-opened.bin", "w+b")
    assert caught.value is denied


async def test_default_io_uring_context_is_native(
    native, uring_is_default, no_executor, tmp_path,
):
    path = tmp_path / "default.bin"
    async with AIOFile(path, "w+b") as afp:
        assert isinstance(get_default_context().context, native.Context)
        assert await afp.write_bytes(b"native default") == 14
        assert await afp.read_bytes() == b"native default"
        await afp.truncate(6)
    assert path.read_bytes() == b"native"
