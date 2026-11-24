"""Windows helpers (read-only except beeps). Nothing here moves the mouse or types: all input is the Pico 2.

Screen: cursor position (GetCursorPos, DPI-aware = physical pixels), screen / virtual screen size, monitor count,
system DPI scale, foreground window (title + class), visible dialogs (class #32770, e.g. 'Save As'), screenshots
(PIL.ImageGrab), PC beeps (winsound). RawInputWatcher: WM_INPUT from every mouse / keyboard; events from a device
whose name has VID_2E8A (Raspberry Pi = the Pico 2) are ours, everything else is the human -> callback.
FakeScreen: the same surface for the offline tests.
"""
from __future__ import annotations

import sys
import threading
import time

IS_WIN = sys.platform == 'win32'
PICO_VID = 'VID_2E8A'


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


class Screen:
    """Real Windows screen (ctypes). Constructed only on Windows."""

    def __init__(self):
        if not IS_WIN:
            raise RuntimeError('Windows only')
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.u32 = ctypes.windll.user32
        self.dpi_mode = 'unaware'
        try:                                                 # physical pixels everywhere (cursor, screenshots)
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            self.dpi_mode = 'per_monitor'
        except Exception:  # noqa: BLE001
            try:
                self.u32.SetProcessDPIAware()
                self.dpi_mode = 'system'
            except Exception:  # noqa: BLE001
                pass
        self.watcher = None

    def cursor(self):
        p = self.wt.POINT()
        if not self.u32.GetCursorPos(self.ct.byref(p)):
            raise OSError('GetCursorPos failed')
        return (int(p.x), int(p.y))

    def screen_size(self):
        return (int(self.u32.GetSystemMetrics(0)), int(self.u32.GetSystemMetrics(1)))

    def virtual_screen(self):
        g = self.u32.GetSystemMetrics
        return (int(g(76)), int(g(77)), int(g(78)), int(g(79)))           # x, y, w, h

    def monitors(self):
        return int(self.u32.GetSystemMetrics(80))

    def dpi_scale(self):
        try:
            return float(self.u32.GetDpiForSystem()) / 96.0
        except Exception:  # noqa: BLE001
            try:
                hdc = self.u32.GetDC(0)
                v = self.ct.windll.gdi32.GetDeviceCaps(hdc, 88)            # LOGPIXELSX
                self.u32.ReleaseDC(0, hdc)
                return float(v) / 96.0
            except Exception:  # noqa: BLE001
                return 1.0

    def _title(self, hwnd):
        n = self.u32.GetWindowTextLengthW(hwnd)
        buf = self.ct.create_unicode_buffer(n + 1)
        self.u32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _cls(self, hwnd):
        buf = self.ct.create_unicode_buffer(256)
        self.u32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def foreground(self):
        h = self.u32.GetForegroundWindow()
        if not h:
            return {'hwnd': 0, 'title': '', 'cls': ''}
        r = self.wt.RECT()
        self.u32.GetWindowRect(h, self.ct.byref(r))
        return {'hwnd': int(h), 'title': self._title(h), 'cls': self._cls(h),
                'rect': (int(r.left), int(r.top), int(r.right), int(r.bottom))}

    def chrome_foreground(self):
        f = self.foreground()
        return f.get('cls') == 'Chrome_WidgetWin_1', f

    def window_at(self, x, y):
        """Top-level window that is really on top at this screen pixel (WindowFromPoint -> root window)."""
        try:
            pt = self.wt.POINT(int(x), int(y))
            h = self.u32.WindowFromPoint(pt)
            if not h:
                return {'hwnd': 0, 'cls': '', 'title': ''}
            root = self.u32.GetAncestor(h, 2) or h          # GA_ROOT
            return {'hwnd': int(root), 'cls': self._cls(root), 'title': self._title(root)}
        except Exception as e:  # noqa: BLE001
            return {'hwnd': 0, 'cls': '', 'title': '', 'error': str(e)[:80]}

    def dialogs(self, titles=()):
        """Visible top-level dialogs (#32770) - e.g. Chrome's 'Save As' (다른 이름으로 저장)."""
        out = []
        cb_t = self.ct.WINFUNCTYPE(self.wt.BOOL, self.wt.HWND, self.wt.LPARAM)

        def cb(h, _l):
            try:
                if self.u32.IsWindowVisible(h) and self._cls(h) == '#32770':
                    t = self._title(h)
                    if not titles or any(x in t for x in titles):
                        out.append({'hwnd': int(h), 'title': t})
            except Exception:  # noqa: BLE001
                pass
            return True
        self.u32.EnumWindows(cb_t(cb), 0)
        return out

    def screenshot(self, bbox=None):
        try:
            from PIL import ImageGrab
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception:  # noqa: BLE001
            return None

    def beep(self, pattern):
        try:
            import winsound
            for f, ms in pattern:
                if f:
                    winsound.Beep(int(f), int(ms))
                else:
                    time.sleep(ms / 1000.0)
        except Exception:  # noqa: BLE001
            pass

    def start_watcher(self, on_human):
        try:
            self.watcher = RawInputWatcher(on_human)
            self.watcher.start()
        except Exception as e:  # noqa: BLE001
            self.watcher = None
            return f'raw input watcher not available: {type(e).__name__}: {e}'
        return 'raw input watcher on' if self.watcher.ok else ('raw input watcher failed: ' + self.watcher.error)

    def human_since(self, ts):
        w = self.watcher
        return bool(w and w.ok and w.last_human > ts)

    def last_human(self):
        w = self.watcher
        return w.last_human if (w and w.ok) else 0.0


class RawInputWatcher(threading.Thread):
    """Message-only window receiving WM_INPUT for all mice / keyboards (RIDEV_INPUTSINK, read-only).
    The device name of the Pico 2 contains VID_2E8A; any other device = the human (touchpad, own mouse, keys)."""

    def __init__(self, on_human):
        super().__init__(daemon=True, name='rawinput-watcher')
        self.on_human = on_human
        self.ok = False
        self.error = ''
        self.last_human = 0.0
        self.names = {}
        self.ready = threading.Event()
        self.counts = {'pico': 0, 'human': 0, 'injected': 0}

    def run(self):  # pragma: no cover - Windows only
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            self.error = f'{type(e).__name__}: {e}'
            self.ok = False
            self.ready.set()

    def _run(self):  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        u32.DefWindowProcW.restype = LRESULT
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        class WNDCLASS(ctypes.Structure):
            _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                        ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                        ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HBRUSH),
                        ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [('usUsagePage', wintypes.USHORT), ('usUsage', wintypes.USHORT),
                        ('dwFlags', wintypes.DWORD), ('hwndTarget', wintypes.HWND)]

        class RAWINPUTHEADER(ctypes.Structure):
            _fields_ = [('dwType', wintypes.DWORD), ('dwSize', wintypes.DWORD), ('hDevice', wintypes.HANDLE),
                        ('wParam', wintypes.WPARAM)]

        def name_of(hdev):
            key = int(hdev or 0)
            if key in self.names:
                return self.names[key]
            size = wintypes.UINT(0)
            u32.GetRawInputDeviceInfoW(hdev, 0x20000007, None, ctypes.byref(size))
            buf = ctypes.create_unicode_buffer(size.value + 1)
            u32.GetRawInputDeviceInfoW(hdev, 0x20000007, buf, ctypes.byref(size))
            self.names[key] = buf.value or ''
            return self.names[key]

        def proc(hwnd, msg, wp, lp):
            if msg == 0x00FF:                                           # WM_INPUT
                try:
                    hdr = RAWINPUTHEADER()
                    sz = wintypes.UINT(ctypes.sizeof(RAWINPUTHEADER))
                    u32.GetRawInputData(wintypes.HANDLE(lp), 0x10000005, ctypes.byref(hdr), ctypes.byref(sz),
                                        ctypes.sizeof(RAWINPUTHEADER))                       # RID_HEADER
                    if not hdr.hDevice:
                        self.counts['injected'] += 1                    # SendInput (no device): not a hand
                    elif PICO_VID in name_of(hdr.hDevice).upper():
                        self.counts['pico'] += 1
                    else:
                        self.counts['human'] += 1
                        self.last_human = time.time()
                        try:
                            self.on_human('mouse' if hdr.dwType == 0 else 'keyboard' if hdr.dwType == 1 else 'hid')
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass
            return u32.DefWindowProcW(hwnd, msg, wp, lp)

        self._proc = WNDPROC(proc)                                      # keep a reference
        hinst = k32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = 'BidguardRawInputWatcher'
        u32.RegisterClassW(ctypes.byref(wc))
        hwnd = u32.CreateWindowExW(0, wc.lpszClassName, 'bidguard', 0, 0, 0, 0, 0, wintypes.HWND(-3), None, hinst, None)
        if not hwnd:
            raise OSError('CreateWindowExW failed')
        devs = (RAWINPUTDEVICE * 2)()
        devs[0].usUsagePage, devs[0].usUsage, devs[0].dwFlags, devs[0].hwndTarget = 1, 2, 0x00000100, hwnd
        devs[1].usUsagePage, devs[1].usUsage, devs[1].dwFlags, devs[1].hwndTarget = 1, 6, 0x00000100, hwnd
        if not u32.RegisterRawInputDevices(devs, 2, ctypes.sizeof(RAWINPUTDEVICE)):
            raise OSError('RegisterRawInputDevices failed')
        self.ok = True
        self.ready.set()
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    def start(self):
        super().start()
        self.ready.wait(2.0)


class FakeScreen:
    """Offline stand-in: a cursor the fake Pico moves, screenshots as tiny 'images' (tuples), scripted human moves."""

    def __init__(self, size=(1920, 1080), dpi=1.0, chrome_foreground=True):
        self.pos = (size[0] // 2, size[1] // 2)
        self.size = size
        self.dpi = dpi
        self.fg = chrome_foreground
        self.beeps = []
        self.dlg = []
        self.shots = 0
        self.human_ts = 0.0
        self.watcher = None
        self.shot_fn = None                 # tests: callable(bbox) -> image-like
        self.clock = time.time

    def cursor(self):
        return tuple(int(round(v)) for v in self.pos)

    def screen_size(self):
        return self.size

    def virtual_screen(self):
        return (0, 0) + tuple(self.size)

    def monitors(self):
        return 1

    def dpi_scale(self):
        return self.dpi

    def foreground(self):
        return {'hwnd': 1, 'title': 'court - Chrome' if self.fg else 'Notepad',
                'cls': 'Chrome_WidgetWin_1' if self.fg else 'Notepad'}

    def chrome_foreground(self):
        f = self.foreground()
        return f['cls'] == 'Chrome_WidgetWin_1', f

    def window_at(self, x, y):
        cls = getattr(self, 'cover_cls', None) or ('Chrome_WidgetWin_1' if self.fg else 'Notepad')
        return {'hwnd': 1, 'cls': cls, 'title': 'court - Chrome'}

    def dialogs(self, titles=()):
        return [d for d in self.dlg if not titles or any(t in d['title'] for t in titles)]

    def screenshot(self, bbox=None):
        self.shots += 1
        if self.shot_fn:
            return self.shot_fn(bbox)
        return None

    def beep(self, pattern):
        self.beeps.append(list(pattern))

    def start_watcher(self, on_human):
        self._on_human = on_human
        return 'fake watcher'

    def human_move(self, x, y):
        """Test: the human grabs the mouse."""
        self.pos = (x, y)
        self.human_ts = self.clock()
        cb = getattr(self, '_on_human', None)
        if cb:
            cb('mouse')

    def human_since(self, ts):
        return self.human_ts > ts

    def last_human(self):
        return self.human_ts
