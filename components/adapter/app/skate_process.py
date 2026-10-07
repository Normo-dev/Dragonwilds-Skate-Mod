"""Windows lifetime management for this mod's own helper processes only.

API contracts: Microsoft Learn CreateMutexW, AssignProcessToJobObject,
JOBOBJECT_EXTENDED_LIMIT_INFORMATION and SetInformationJobObject.
"""
import ctypes as C
from ctypes import wintypes as W
import contextlib
import os
from pathlib import Path

K = C.WinDLL('kernel32', use_last_error=True)
K.CloseHandle.argtypes = [W.HANDLE]
K.CloseHandle.restype = W.BOOL
K.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
K.CreateMutexW.restype = W.HANDLE
K.CreateJobObjectW.argtypes = [C.c_void_p, W.LPCWSTR]
K.CreateJobObjectW.restype = W.HANDLE
K.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
K.SetInformationJobObject.restype = W.BOOL
K.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
K.AssignProcessToJobObject.restype = W.BOOL
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
K.WaitForSingleObject.restype = W.DWORD


class BasicLimits(C.Structure):
    _fields_ = [('ProcessTime', C.c_int64), ('JobTime', C.c_int64), ('Flags', W.DWORD),
                ('MinWorkingSet', C.c_size_t), ('MaxWorkingSet', C.c_size_t),
                ('ActiveProcesses', W.DWORD), ('Affinity', C.c_size_t),
                ('Priority', W.DWORD), ('SchedulingClass', W.DWORD)]


class IOCounts(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in
                ('ReadOperations', 'WriteOperations', 'OtherOperations', 'ReadBytes', 'WriteBytes', 'OtherBytes')]


class ExtendedLimits(C.Structure):
    _fields_ = [('Basic', BasicLimits), ('IO', IOCounts),
                ('ProcessMemory', C.c_size_t), ('JobMemory', C.c_size_t),
                ('PeakProcessMemory', C.c_size_t), ('PeakJobMemory', C.c_size_t)]


class Handle:
    def __init__(self, value):
        if not value:
            raise C.WinError(C.get_last_error())
        self.value = value

    def close(self):
        if self.value:
            K.CloseHandle(self.value)
            self.value = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Singleton(Handle):
    def __init__(self, name=r'Local\DragonwildsSkate.Relay.v1'):
        C.set_last_error(0)
        value = K.CreateMutexW(None, False, name)
        already = C.get_last_error() == 183
        super().__init__(value)
        self.acquired = not already


class ChildJob(Handle):
    def __init__(self):
        super().__init__(K.CreateJobObjectW(None, None))
        limits = ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not K.SetInformationJobObject(self.value, 9, C.byref(limits), C.sizeof(limits)):
            error = C.WinError(C.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        # Only a subprocess we just created can be assigned. The child waits for
        # our stdin handshake before importing code that creates descendants.
        if not K.AssignProcessToJobObject(self.value, int(process._handle)):
            raise C.WinError(C.get_last_error())


class ParentProcess(Handle):
    def __init__(self, pid):
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError('Invalid parent process ID')
        super().__init__(K.OpenProcess(0x100000, False, pid))  # SYNCHRONIZE, no write access

    def exited(self):
        result = K.WaitForSingleObject(self.value, 0)
        if result == 0:
            return True
        if result == 258:
            return False
        raise C.WinError(C.get_last_error())


class ProcessEntry(C.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                ('parent_pid', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                ('name', W.WCHAR * 260)]


def process_images(names):
    """Read matching executable identities; never acquire process write access."""
    K.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    K.CreateToolhelp32Snapshot.restype = W.HANDLE
    K.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(ProcessEntry)]
    K.Process32FirstW.restype = W.BOOL
    K.Process32NextW.argtypes = [W.HANDLE, C.POINTER(ProcessEntry)]
    K.Process32NextW.restype = W.BOOL
    K.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    K.QueryFullProcessImageNameW.restype = W.BOOL
    snapshot = K.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    with Handle(snapshot):
        entry = ProcessEntry()
        entry.size = C.sizeof(entry)
        present = K.Process32FirstW(snapshot, C.byref(entry))
        while present:
            name, pid = entry.name, int(entry.pid)
            if name.casefold() in names and pid != os.getpid():
                image = None
                handle = K.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if handle:
                    with Handle(handle):
                        buffer = C.create_unicode_buffer(32768)
                        length = W.DWORD(len(buffer))
                        if K.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(length)):
                            image = buffer.value
                yield pid, name, image
            present = K.Process32NextW(snapshot, C.byref(entry))
        if C.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise C.WinError(C.get_last_error())


def blocking_processes(paths, *, images=None):
    targets = [Path(paths['game_root']) / 'RSDragonwilds/Binaries/Win64/RSDragonwilds-Win64-Shipping.exe']
    targets += [Path(paths[key]) for key in ('worker', 'python_runtime', 'map_export','building_export') if key in paths]
    exact = {os.path.normcase(str(path.resolve())) for path in targets}
    names = {path.name.casefold() for path in targets}
    records = process_images(names) if images is None else images
    blocked = []
    for pid, name, image in records:
        if pid == os.getpid() or name.casefold() not in names:
            continue
        # A matching name whose identity cannot be read is not proof of closure.
        if image is None or os.path.normcase(str(Path(image).resolve())) in exact:
            blocked.append({'pid': pid, 'name': name, 'identity_readable': image is not None})
    return blocked


@contextlib.contextmanager
def offline_maintenance(paths):
    """Hold the launcher's mutex while checking and changing private map state."""
    with Singleton() as singleton:
        if not singleton.acquired:
            raise ValueError('Close the mod helper before archiving map data; its launcher is running')
        blocked = blocking_processes(paths)
        if blocked:
            description = ', '.join(f"{p['name']} (PID {p['pid']})" for p in blocked)
            raise ValueError('Close Dragonwilds and the mod helpers before archiving map data: ' + description)
        yield
