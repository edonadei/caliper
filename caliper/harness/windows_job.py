"""Contain a suspended Windows MCP launcher and all of its children in a job."""

from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes

import psutil

CREATE_SUSPENDED = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_INVALID_RESUME_COUNT = 0xFFFFFFFF

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel32.TerminateJobObject.restype = wintypes.BOOL
_kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenThread.restype = wintypes.HANDLE
_kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
_kernel32.ResumeThread.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.QueryInformationJobObject.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPDWORD,
)
_kernel32.QueryInformationJobObject.restype = wintypes.BOOL

_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1


class _BasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", wintypes.LARGE_INTEGER),
        ("TotalKernelTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def assign_and_resume(process: subprocess.Popen) -> int:
    """Assign before the launcher can start children, then resume its thread."""
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not _kernel32.AssignProcessToJobObject(job, process._handle):
            raise ctypes.WinError(ctypes.get_last_error())
        threads = psutil.Process(process.pid).threads()
        if not threads:
            raise OSError("suspended MCP launcher has no thread to resume")
        thread = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, threads[0].id)
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if _kernel32.ResumeThread(thread) == _INVALID_RESUME_COUNT:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _kernel32.CloseHandle(thread)
        return job
    except Exception:
        close(job)
        raise


def close(job: int, timeout: float = 2.0) -> None:
    """End every process in the job, including children of an exited launcher.

    ``TerminateJobObject`` only starts the termination, so wait (up to
    ``timeout``) until the job reports no active process, as the POSIX branch
    waits on its process group: a caller that returns must leave no survivor.
    """
    try:
        if not _kernel32.TerminateJobObject(job, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + timeout
        while _active_processes(job) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        _kernel32.CloseHandle(job)


def _active_processes(job: int) -> int:
    info = _BasicAccounting()
    if not _kernel32.QueryInformationJobObject(
        job,
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
        None,
    ):
        return 0  # Unqueryable: nothing more to wait on.
    return info.ActiveProcesses
