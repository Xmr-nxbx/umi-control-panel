"""单实例：内核命名对象互斥（进程死掉由系统回收，绝不残留锁文件）。

open-revo 的那次「重启后再也起不来」正是残留状态导致的，
所以这里不用锁文件，用 CreateMutexW。
"""
import ctypes
import ctypes.wintypes as wt

k32 = ctypes.windll.kernel32
k32.CreateMutexW.restype = wt.HANDLE
k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
k32.CloseHandle.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    def __init__(self, name='UmiControlPanel'):
        self.name = 'Global\\' + name
        self._handle = None

    def acquire(self):
        self._handle = k32.CreateMutexW(None, False, self.name)
        if not self._handle:
            self._handle = k32.CreateMutexW(None, False, 'Local\\' + self.name)
            if not self._handle:
                raise OSError('CreateMutex 失败 err=%d' % ctypes.get_last_error())
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    def release(self):
        if self._handle:
            k32.CloseHandle(self._handle)
            self._handle = None
