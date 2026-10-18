"""Pin Windows parent directories and open the final file without following links."""

import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def locked_file(path: Path, *, write=False, create=False, delete=False):
    if os.name != "nt":
        flags = (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        # Open relative to no-follow directory descriptors, including ancestors.
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = following
            descriptor = os.open(path.name, flags, 0o600, dir_fd=directory)
            with os.fdopen(descriptor, "r+b" if write else "rb") as stream:
                yield stream
                if delete:
                    before, current = os.fstat(stream.fileno()), os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                    if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                        raise OSError("File identity changed")
                    os.unlink(path.name, dir_fd=directory)
        finally:
            os.close(directory)
        return

    import ctypes as ct
    import msvcrt
    from ctypes import wintypes as wt

    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ct.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
    kernel.CreateFileW.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD]
    kernel.SetFileInformationByHandle.argtypes = [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD]

    class AttributeTag(ct.Structure):
        _fields_ = [("attributes", wt.DWORD), ("tag", wt.DWORD)]

    def open_handle(value, access, disposition, flags):
        handle = kernel.CreateFileW(str(value), access, 1, None, disposition, flags | 0x00200000, None)
        if handle == ct.c_void_p(-1).value:
            raise ct.WinError(ct.get_last_error())
        attributes = AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ct.byref(attributes), ct.sizeof(attributes)) or attributes.attributes & 0x400:
            kernel.CloseHandle(handle)
            raise OSError("Reparse paths are not authorized")
        return handle

    parents = []
    descriptor = None
    try:
        current = Path(path.anchor)
        for part in path.parts[1:-1]:
            current /= part
            # Share read only: rename, deletion, junction retargeting and writes
            # to each directory are excluded during this short file operation.
            parents.append(open_handle(current, 0x80, 3, 0x02000000))
        access = 0x80000000 | (0x40000000 if write else 0) | (0x10000 if delete else 0)
        handle = open_handle(path, access, 1 if create else 3, 0)
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if write else os.O_RDONLY))
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        with os.fdopen(descriptor, "r+b" if write else "rb") as stream:
            descriptor = None
            yield stream
            if delete:
                disposition = wt.BOOL(True)
                if not kernel.SetFileInformationByHandle(handle, 4, ct.byref(disposition), ct.sizeof(disposition)):
                    raise ct.WinError(ct.get_last_error())
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for handle in reversed(parents):
            kernel.CloseHandle(handle)
