"""单实例：内核命名对象互斥（进程死掉由系统回收，绝不残留锁文件）。

open-revo 的那次「重启后再也起不来」正是残留状态导致的，
所以这里不用锁文件，用 CreateMutexW。

必须用 WinDLL(..., use_last_error=True)：ctypes.windll 不把 last error 抄进
ctypes 自己的线程局部存储，get_last_error() 拿到的是上一次的残值，
于是 ERROR_ALREADY_EXISTS 永远判不出来——2026-09-30 实测两个实例同时跑、
一起绑在 8747 上，还互相踢掉对方的 GCUBridge 连接。
"""
import ctypes
import ctypes.wintypes as wt

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
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
        ctypes.set_last_error(0)
        self._handle = k32.CreateMutexW(None, False, self.name)
        if not self._handle:
            err = ctypes.get_last_error()
            ctypes.set_last_error(0)
            self._handle = k32.CreateMutexW(None, False, 'Local\\' + self.name)
            if not self._handle:
                raise OSError('CreateMutex 失败 err=%d/%d'
                              % (err, ctypes.get_last_error()))
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    @property
    def held(self):
        return bool(self._handle)

    def release(self):
        if self._handle:
            k32.CloseHandle(self._handle)
            self._handle = None
