import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _win_apis():
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR,
                                         ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR),
                                           ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _config_path():
    root = Path(os.getenv("APPDATA", Path.home())) / "ScreenShotScanner"
    root.mkdir(parents=True, exist_ok=True)
    return root / "client.dat"


def _protect(data):
    if os.name != "nt":
        raise RuntimeError("当前系统不支持保存客户端配置")
    crypt32, kernel32 = _win_apis()
    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_char)))
    target = DataBlob()
    description = ctypes.c_wchar_p("ScreenShotScanner")
    if not crypt32.CryptProtectData(ctypes.byref(source), description, None, None, None, 0, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def _unprotect(data):
    if os.name != "nt":
        raise RuntimeError("当前系统不支持保存客户端配置")
    crypt32, kernel32 = _win_apis()
    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_char)))
    target = DataBlob()
    description = wintypes.LPWSTR()
    if not crypt32.CryptUnprotectData(ctypes.byref(source), ctypes.byref(description), None, None, None, 0, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))
        kernel32.LocalFree(target.pbData)


class SecureStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else _config_path()

    def load(self):
        if not self.path.exists():
            return {}
        try:
            encrypted = base64.b64decode(self.path.read_bytes(), validate=True)
            value = json.loads(_unprotect(encrypted).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            raise RuntimeError("无法读取已保存的客户端配置") from exc

    def save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        encoded = base64.b64encode(_protect(payload))
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, self.path)

    def clear(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
