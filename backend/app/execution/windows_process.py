"""Create suspended Windows processes, attach a kill-on-close Job, then resume."""

import ctypes as ct
import os
import subprocess
from ctypes import wintypes as wt


class StartupInfo(ct.Structure):
    _fields_ = [("cb", wt.DWORD), ("reserved", wt.LPWSTR), ("desktop", wt.LPWSTR),
                ("title", wt.LPWSTR), ("x", wt.DWORD), ("y", wt.DWORD),
                ("xsize", wt.DWORD), ("ysize", wt.DWORD), ("xchars", wt.DWORD),
                ("ychars", wt.DWORD), ("fill", wt.DWORD), ("flags", wt.DWORD),
                ("show", wt.WORD), ("reservedsize", wt.WORD), ("reservedbytes", ct.c_void_p),
                ("stdin", wt.HANDLE), ("stdout", wt.HANDLE), ("stderr", wt.HANDLE)]


class StartupInfoEx(ct.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", ct.c_void_p)]


class ProcessInfo(ct.Structure):
    _fields_ = [("process", wt.HANDLE), ("thread", wt.HANDLE), ("pid", wt.DWORD), ("tid", wt.DWORD)]


class BasicLimits(ct.Structure):
    _fields_ = [("process_time", ct.c_int64), ("job_time", ct.c_int64), ("flags", wt.DWORD),
                ("minimum", ct.c_size_t), ("maximum", ct.c_size_t), ("active", wt.DWORD),
                ("affinity", ct.c_size_t), ("priority", wt.DWORD), ("scheduling", wt.DWORD)]


class IOCounters(ct.Structure):
    _fields_ = [(name, ct.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ct.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IOCounters), ("process_memory", ct.c_size_t),
                ("job_memory", ct.c_size_t), ("peak_process", ct.c_size_t), ("peak_job", ct.c_size_t)]


class WindowsProcess:
    def __init__(self, argv, cwd, environment, stdout_fd, stderr_fd):
        import msvcrt

        self.kernel = ct.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ct.c_void_p, wt.LPCWSTR], wt.HANDLE),
            "SetInformationJobObject": ([wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD], wt.BOOL),
            "AssignProcessToJobObject": ([wt.HANDLE, wt.HANDLE], wt.BOOL),
            "TerminateJobObject": ([wt.HANDLE, wt.UINT], wt.BOOL),
            "CloseHandle": ([wt.HANDLE], wt.BOOL),
            "ResumeThread": ([wt.HANDLE], wt.DWORD),
            "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
            "GetExitCodeProcess": ([wt.HANDLE, ct.POINTER(wt.DWORD)], wt.BOOL),
            "TerminateProcess": ([wt.HANDLE, wt.UINT], wt.BOOL),
            "InitializeProcThreadAttributeList": ([ct.c_void_p, wt.DWORD, wt.DWORD, ct.POINTER(ct.c_size_t)], wt.BOOL),
            "UpdateProcThreadAttribute": ([ct.c_void_p, wt.DWORD, ct.c_size_t, ct.c_void_p, ct.c_size_t, ct.c_void_p, ct.c_void_p], wt.BOOL),
            "DeleteProcThreadAttributeList": ([ct.c_void_p], None),
            "CreateProcessW": ([wt.LPCWSTR, wt.LPWSTR, ct.c_void_p, ct.c_void_p, wt.BOOL, wt.DWORD,
                                ct.c_void_p, wt.LPCWSTR, ct.POINTER(StartupInfoEx), ct.POINTER(ProcessInfo)], wt.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.job = self.kernel.CreateJobObjectW(None, None)
        self.info = ProcessInfo()
        self._check(self.job)
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self._check(self.kernel.SetInformationJobObject(self.job, 9, ct.byref(limits), ct.sizeof(limits)))
            with open(os.devnull, "rb") as stdin:
                handles = [msvcrt.get_osfhandle(fd) for fd in (stdin.fileno(), stdout_fd, stderr_fd)]
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                size = ct.c_size_t()
                self.kernel.InitializeProcThreadAttributeList(None, 1, 0, ct.byref(size))
                attributes = ct.create_string_buffer(size.value)
                self._check(self.kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ct.byref(size)))
                try:
                    handle_list = (wt.HANDLE * len(handles))(*handles)
                    self._check(self.kernel.UpdateProcThreadAttribute(attributes, 0, 0x20002,
                                                                      handle_list, ct.sizeof(handle_list), None, None))
                    startup = StartupInfoEx()
                    startup.startup.cb = ct.sizeof(startup)
                    startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
                    startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
                    startup.attributes = ct.cast(attributes, ct.c_void_p)
                    command = ct.create_unicode_buffer(subprocess.list2cmdline(argv))
                    env = ct.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0")
                    self._check(self.kernel.CreateProcessW(argv[0], command, None, None, True,
                                                           0x08080004 | 0x400, env, cwd, ct.byref(startup), ct.byref(self.info)))
                finally:
                    self.kernel.DeleteProcThreadAttributeList(attributes)
                    for handle in handles:
                        os.set_handle_inheritable(handle, False)
            self._check(self.kernel.AssignProcessToJobObject(self.job, self.info.process))
            self._check(self.kernel.ResumeThread(self.info.thread) != 0xFFFFFFFF)
        except BaseException:
            if self.info.process:
                self.kernel.TerminateProcess(self.info.process, 1)
            self.close()
            raise
        finally:
            if self.info.thread:
                self.kernel.CloseHandle(self.info.thread)
                self.info.thread = None
        self.pid = self.info.pid

    @staticmethod
    def _check(result):
        if not result:
            raise ct.WinError(ct.get_last_error())

    def poll(self):
        if self.kernel.WaitForSingleObject(self.info.process, 0) == 0x102:
            return None
        code = wt.DWORD()
        self._check(self.kernel.GetExitCodeProcess(self.info.process, ct.byref(code)))
        return code.value

    def close(self):
        if self.job:
            self.kernel.TerminateJobObject(self.job, 1)
            self.kernel.CloseHandle(self.job)
            self.job = None
        if self.info.process:
            self.kernel.WaitForSingleObject(self.info.process, 5000)
            self.kernel.CloseHandle(self.info.process)
            self.info.process = None
