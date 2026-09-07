import ctypes
from ctypes import wintypes
from io import BytesIO
import os

import mss
from PIL import Image


TARGET_IMAGE_BYTES = 4_800_000


def enable_high_dpi():
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def _cursor_position():
    point = wintypes.POINT()
    if os.name == "nt" and ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return point.x, point.y
    raise RuntimeError("无法获取鼠标位置，不能确定截图显示器")


def _current_monitor(monitors):
    x, y = _cursor_position()
    for monitor in monitors:
        left, top = monitor["left"], monitor["top"]
        if left <= x < left + monitor["width"] and top <= y < top + monitor["height"]:
            return monitor
    return monitors[0]


def capture_current_monitor():
    enable_high_dpi()
    with mss.mss() as capture:
        monitors = capture.monitors[1:]
        if not monitors:
            raise RuntimeError("没有检测到可截图的显示器")
        raw = capture.grab(_current_monitor(monitors))
        image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    quality = 92
    working = image
    while True:
        output = BytesIO()
        working.save(output, format="JPEG", quality=quality, optimize=True)
        data = output.getvalue()
        if len(data) <= TARGET_IMAGE_BYTES:
            return data
        if quality > 58:
            quality -= 8
            continue
        new_size = (max(1, int(working.width * 0.86)), max(1, int(working.height * 0.86)))
        if new_size == working.size:
            break
        working = working.resize(new_size, Image.Resampling.LANCZOS)
        quality = 82
    raise RuntimeError("当前显示器截图压缩后仍超过 5 MB")
