"""Windows named mutex covering the fixed Chrome debug ports across installs."""
import ctypes
from ctypes import wintypes

MUTEX_NAME = "Local\\LietaAutomator.Chrome9222_9225"


class InstanceLock:
    def __enter__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
        error = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        if error == 183:
            kernel.CloseHandle(self.handle)
            self.handle = None
            raise RuntimeError("另一個 Lieta 程式或批次正在使用瀏覽器，請先結束該程序。")
        return self

    def __exit__(self, *args):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
