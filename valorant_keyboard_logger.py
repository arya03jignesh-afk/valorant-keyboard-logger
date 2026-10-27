#!/usr/bin/env python3
"""VALORANT keyboard event logger for Windows.

This application records low-level keyboard events while VALORANT is the active
foreground process and writes them to a CSV file with UTC timestamps and elapsed
milliseconds. It is designed to work independently of the Tkinter window focus
so that the user can switch to VALORANT and play while the logger runs in the
background.

It uses a Windows low-level keyboard hook (WH_KEYBOARD_LL) via ctypes. The hook
runs regardless of the Tkinter window's focus, which makes it appropriate for
this task. A process-name check is used to determine whether VALORANT is in the
foreground and whether keyboard events should be recorded.

Limitations:
- Riot Vanguard or anti-cheat products may block or interfere with this kind of
  hook.
- This tool does not attempt to bypass anti-cheat, inject code, or manipulate the
  game process.
- Real-world validation requires a Windows system with VALORANT installed and
  running, which cannot be fully tested from a non-Windows environment.
"""

from __future__ import annotations

import csv
import ctypes
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import tkinter as tk
from tkinter import filedialog, ttk


# ---------------------------------------------------------------------------
# Windows constants and helper types
# ---------------------------------------------------------------------------
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
LLKHF_INJECTED = 0x00000010
LLKHF_LOWER_IL_INJECTED = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class KBDLLHOOKSTRUCT(ctypes.Structure):
    """Low-level keyboard hook structure."""

    _fields_ = [
        ("vkCode", ctypes.c_uint),
        ("scanCode", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_wparam, ctypes.POINTER(KBDLLHOOKSTRUCT))


VIRTUAL_KEY_NAMES: Dict[int, str] = {
    0x08: "BACKSPACE",
    0x09: "TAB",
    0x0D: "ENTER",
    0x10: "SHIFT",
    0x11: "CTRL",
    0x12: "ALT",
    0x13: "PAUSE",
    0x14: "CAPSLOCK",
    0x20: "SPACE",
    0x21: "PAGEUP",
    0x22: "PAGEDOWN",
    0x23: "END",
    0x24: "HOME",
    0x25: "LEFT",
    0x26: "UP",
    0x27: "RIGHT",
    0x28: "DOWN",
    0x2C: "PRINTSCREEN",
    0x2D: "INSERT",
    0x2E: "DELETE",
    0x30: "0",
    0x31: "1",
    0x32: "2",
    0x33: "3",
    0x34: "4",
    0x35: "5",
    0x36: "6",
    0x37: "7",
    0x38: "8",
    0x39: "9",
    0x41: "A",
    0x42: "B",
    0x43: "C",
    0x44: "D",
    0x45: "E",
    0x46: "F",
    0x47: "G",
    0x48: "H",
    0x49: "I",
    0x4A: "J",
    0x4B: "K",
    0x4C: "L",
    0x4D: "M",
    0x4E: "N",
    0x4F: "O",
    0x50: "P",
    0x51: "Q",
    0x52: "R",
    0x53: "S",
    0x54: "T",
    0x55: "U",
    0x56: "V",
    0x57: "W",
    0x58: "X",
    0x59: "Y",
    0x5A: "Z",
    0x5B: "LEFT_WIN",
    0x5C: "RIGHT_WIN",
    0x60: "NUMPAD0",
    0x61: "NUMPAD1",
    0x62: "NUMPAD2",
    0x63: "NUMPAD3",
    0x64: "NUMPAD4",
    0x65: "NUMPAD5",
    0x66: "NUMPAD6",
    0x67: "NUMPAD7",
    0x68: "NUMPAD8",
    0x69: "NUMPAD9",
    0x6A: "MULTIPLY",
    0x6B: "ADD",
    0x6C: "SEPARATOR",
    0x6D: "SUBTRACT",
    0x6E: "DECIMAL",
    0x6F: "DIVIDE",
    0x70: "F1",
    0x71: "F2",
    0x72: "F3",
    0x73: "F4",
    0x74: "F5",
    0x75: "F6",
    0x76: "F7",
    0x77: "F8",
    0x78: "F9",
    0x79: "F10",
    0x7A: "F11",
    0x7B: "F12",
    0x90: "NUMLOCK",
    0x91: "SCROLLLOCK",
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\",
    0xDD: "]",
    0xDE: "'",
    0xDF: "MISC",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def timestamp_utc_from_epoch(seconds: float) -> str:
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def key_name_from_vk(vk_code: int) -> str:
    if vk_code in VIRTUAL_KEY_NAMES:
        return VIRTUAL_KEY_NAMES[vk_code]
    if 0x41 <= vk_code <= 0x5A:
        return chr(vk_code)
    if 0x30 <= vk_code <= 0x39:
        return str(vk_code - 0x30)
    return f"VK_{vk_code:02X}"


# ---------------------------------------------------------------------------
# Foreground process detection
# ---------------------------------------------------------------------------
def get_foreground_window_pid() -> Optional[int]:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_process_image_name(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buffer = (ctypes.c_wchar * 260)()
        size = ctypes.c_ulong(len(buffer))
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
        if ok:
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def is_valorant_foreground() -> bool:
    pid = get_foreground_window_pid()
    if pid is None:
        return False
    image_name = get_process_image_name(pid).lower()
    base_name = Path(image_name).name.lower()
    return "valorant" in base_name or "valorant" in image_name


# ---------------------------------------------------------------------------
# Keyboard hook management
# ---------------------------------------------------------------------------
class KeyboardHookManager:
    """Low-level Windows keyboard hook manager.

    The hook runs independently of the Tkinter window focus. It uses a worker
    thread that owns a message loop required by WH_KEYBOARD_LL hooks.
    """

    def __init__(self, callback):
        self.callback = callback
        self.stop_event = threading.Event()
        self.hook_handle = None
        self.hook_thread = None
        self.thread_id = None
        self._hook_proc = None

    def start(self) -> bool:
        if self.hook_handle is not None:
            return True

        self.stop_event.clear()
        self._hook_proc = HOOKPROC(self._hook_callback)
        self.hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, user32.GetModuleHandleW(None), 0)
        if not self.hook_handle:
            err = ctypes.WinError(ctypes.get_last_error())
            raise RuntimeError(f"Keyboard hook initialization failed: {err}")

        self.hook_thread = threading.Thread(target=self._message_loop, daemon=True)
        self.hook_thread.start()
        return True

    def _message_loop(self) -> None:
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = ctypes.wintypes.MSG()
        while not self.stop_event.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == 0x0012:  # WM_QUIT
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _hook_callback(self, nCode: int, wParam: int, lParam: Any) -> int:
        if self.stop_event.is_set():
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        if nCode >= 0:
            flags = lParam.contents.flags
            if flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            vk_code = int(lParam.contents.vkCode)
            event_type = "key_down" if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN) else "key_up"
            if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
                self.callback(vk_code, event_type)

        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def stop(self) -> None:
        self.stop_event.set()

        if self.thread_id is not None:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT

        if self.hook_handle is not None:
            user32.UnhookWindowsHookEx(self.hook_handle)
            self.hook_handle = None

        if self.hook_thread is not None:
            self.hook_thread.join(timeout=2.0)
            self.hook_thread = None


# ---------------------------------------------------------------------------
# Session recorder and CSV writer
# ---------------------------------------------------------------------------
class SessionRecorder:
    """Queue-backed session manager with CSV writing and synchronization markers."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = ""
        self.csv_path = None
        self.markers_path = None
        self.event_queue: "queue.Queue[dict]" = queue.Queue()
        self.writer_stop = threading.Event()
        self.writer_thread: Optional[threading.Thread] = None
        self.recording = False
        self.event_count = 0
        self.session_start_monotonic = 0.0
        self.tick_base_utc = utc_now_iso()
        self._lock = threading.Lock()

    def start(self) -> str:
        if self.recording:
            return self.session_id

        session_suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"_{uuid.uuid4().hex[:8]}"
        self.session_id = f"session_{session_suffix}"
        self.csv_path = self.output_dir / f"{self.session_id}.csv"
        self.markers_path = self.output_dir / f"{self.session_id}_markers.csv"
        self.event_count = 0
        self.session_start_monotonic = time.perf_counter()
        self.tick_base_utc = utc_now_iso()
        self.recording = True

        self.writer_stop.clear()
        self.writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.writer_thread.start()

        self._write_header_and_start_record()
        return self.session_id

    def _write_header_and_start_record(self) -> None:
        if self.csv_path is None:
            return

        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["session_id", "event_index", "timestamp_utc", "elapsed_ms", "event_type", "key_name", "virtual_key_code"])
            writer.writerow([
                self.session_id,
                0,
                self.tick_base_utc,
                0.0,
                "session_start",
                "",
                "",
            ])
            handle.flush()

    def push_event(self, vk_code: int, event_type: str) -> None:
        if not self.recording:
            return

        now_monotonic = time.perf_counter()
        elapsed_ms = (now_monotonic - self.session_start_monotonic) * 1000.0
        timestamp_utc = timestamp_utc_from_epoch(time.time())
        payload = {
            "session_id": self.session_id,
            "event_index": self.event_count + 1,
            "timestamp_utc": timestamp_utc,
            "elapsed_ms": round(elapsed_ms, 3),
            "event_type": event_type,
            "key_name": key_name_from_vk(vk_code),
            "virtual_key_code": vk_code,
        }
        self.event_queue.put(payload)
        with self._lock:
            self.event_count = max(self.event_count, payload["event_index"])

    def _writer_loop(self) -> None:
        while not self.writer_stop.is_set() or not self.event_queue.empty():
            try:
                payload = self.event_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if self.csv_path is None:
                continue

            try:
                with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow([
                        payload["session_id"],
                        payload["event_index"],
                        payload["timestamp_utc"],
                        payload["elapsed_ms"],
                        payload["event_type"],
                        payload["key_name"],
                        payload["virtual_key_code"],
                    ])
                    handle.flush()
            finally:
                self.event_queue.task_done()

    def insert_marker(self, label: str) -> None:
        if not self.recording or self.markers_path is None:
            return

        elapsed_ms = (time.perf_counter() - self.session_start_monotonic) * 1000.0
        row = {
            "session_id": self.session_id,
            "timestamp_utc": utc_now_iso(),
            "elapsed_ms": round(elapsed_ms, 3),
            "marker_label": label,
        }
        path = self.markers_path
        file_exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["session_id", "timestamp_utc", "elapsed_ms", "marker_label"])
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
            handle.flush()

    def stop(self) -> None:
        if not self.recording:
            return

        self.recording = False
        self.writer_stop.set()
        if self.csv_path is not None:
            final_row = [
                self.session_id,
                self.event_count + 1,
                utc_now_iso(),
                round((time.perf_counter() - self.session_start_monotonic) * 1000.0, 3),
                "session_end",
                "",
                "",
            ]
            with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(final_row)
                handle.flush()

        if self.writer_thread is not None:
            self.writer_thread.join(timeout=3.0)
            self.writer_thread = None

        self.event_queue.join()

    def reset(self) -> None:
        self.recording = False
        self.writer_stop.set()
        self.session_id = ""
        self.csv_path = None
        self.markers_path = None
        self.event_count = 0


# ---------------------------------------------------------------------------
# GUI application
# ---------------------------------------------------------------------------
class ValorantKeyboardLoggerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("VALORANT Keyboard Event Logger")
        self.root.geometry("820x560")
        self.root.minsize(760, 500)

        self.output_dir = str(Path.home() / "Documents" / "valorant_keyboard_logs")
        self.session = SessionRecorder(self.output_dir)
        self.hook = None
        self.status_var = tk.StringVar(value="Stopped")
        self.timer_var = tk.StringVar(value="00:00.000")
        self.counter_var = tk.StringVar(value="0")
        self.output_dir_var = tk.StringVar(value=self.output_dir)
        self.message_var = tk.StringVar(value="Ready")
        self.current_session_label_var = tk.StringVar(value="No active session")
        self.foreground_state = False

        self._build_ui()
        self._update_timer()
        self._poll_foreground()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)

        header = ttk.Label(frame, text="VALORANT Keyboard Event Logger", font=("Segoe UI", 16, "bold"))
        header.pack(anchor="w", pady=(0, 12))

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(0, 12))

        self.start_btn = ttk.Button(btn_row, text="Start Recording", command=self.start_recording)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.stop_btn = ttk.Button(btn_row, text="Stop Recording", command=self.stop_recording, state="disabled")
        self.stop_btn.pack(side=tk.LEFT)

        self.sync_btn = ttk.Button(btn_row, text="Insert Sync Marker", command=self.insert_sync_marker, state="disabled")
        self.sync_btn.pack(side=tk.LEFT, padx=(10, 0))

        status_frame = ttk.LabelFrame(frame, text="Recording status")
        status_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(status_frame, text="Status:").grid(row=0, column=0, sticky="w", padx=(12, 8), pady=8)
        ttk.Label(status_frame, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w")

        ttk.Label(status_frame, text="Timer:").grid(row=1, column=0, sticky="w", padx=(12, 8), pady=(0, 8))
        ttk.Label(status_frame, textvariable=self.timer_var, font=("Segoe UI", 10, "bold")).grid(row=1, column=1, sticky="w")

        ttk.Label(status_frame, text="Events:").grid(row=2, column=0, sticky="w", padx=(12, 8), pady=(0, 8))
        ttk.Label(status_frame, textvariable=self.counter_var, font=("Segoe UI", 10, "bold")).grid(row=2, column=1, sticky="w")

        ttk.Label(status_frame, text="Session:").grid(row=3, column=0, sticky="w", padx=(12, 8), pady=(0, 8))
        ttk.Label(status_frame, textvariable=self.current_session_label_var, font=("Segoe UI", 9)).grid(row=3, column=1, sticky="w")

        output_frame = ttk.LabelFrame(frame, text="Output")
        output_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(output_frame, text="Directory:").grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 4))
        ttk.Entry(output_frame, textvariable=self.output_dir_var, width=76).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(10, 4))
        ttk.Button(output_frame, text="Browse", command=self.choose_output_dir).grid(row=0, column=2, sticky="ew", padx=(0, 10), pady=(10, 4))

        ttk.Label(output_frame, text="File:").grid(row=1, column=0, sticky="w", padx=(12, 8), pady=(0, 12))
        ttk.Label(output_frame, textvariable=self.message_var, wraplength=620, justify="left").grid(row=1, column=1, columnspan=2, sticky="w", padx=(0, 10), pady=(0, 12))

        output_frame.columnconfigure(1, weight=1)

        notes = ttk.LabelFrame(frame, text="Notes")
        notes.pack(fill=tk.BOTH, expand=True)

        text = (
            "- Start Recording begins a new session and generates a CSV file.\n"
            "- The logger only records while VALORANT is the foreground process.\n"
            "- When the game loses focus, the current status changes to inactive and the hook stops recording until focus returns.\n"
            "- Current timestamps are UTC. Elapsed time is measured from the start of the active session, using a monotonic clock.\n"
            "- If Vanguard or another anti-cheat blocks the low-level hook, the app reports the limitation instead of pretending the capture works."
        )
        ttk.Label(notes, text=text, justify="left", wraplength=700).pack(anchor="w", padx=12, pady=12)

    def _update_timer(self) -> None:
        if self.session.recording:
            elapsed_ms = (time.perf_counter() - self.session.session_start_monotonic) * 1000.0
            minutes, rem = divmod(elapsed_ms, 60000.0)
            seconds, millis = divmod(rem, 1000.0)
            self.timer_var.set(f"{int(minutes):02d}:{int(seconds):02d}.{int(millis):03d}")
            self.counter_var.set(str(self.session.event_count))
        else:
            self.timer_var.set("00:00.000")
            self.counter_var.set("0")
        self.root.after(100, self._update_timer)

    def _poll_foreground(self) -> None:
        if self.session.recording:
            active = is_valorant_foreground()
            if active:
                self.foreground_state = True
                self.status_var.set("Active - VALORANT in foreground")
            else:
                self.foreground_state = False
                self.status_var.set("Inactive - VALORANT not in foreground")
        else:
            self.foreground_state = False
            self.status_var.set("Stopped")
        self.root.after(250, self._poll_foreground)

    def choose_output_dir(self) -> None:
        directory = filedialog.askdirectory(title="Choose output directory", initialdir=self.output_dir)
        if directory:
            self.output_dir = directory
            self.output_dir_var.set(directory)
            self.session.output_dir = Path(directory)
            self.session.output_dir.mkdir(parents=True, exist_ok=True)

    def start_recording(self) -> None:
        try:
            self.session.start()
            self.current_session_label_var.set(self.session.session_id)
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.sync_btn.configure(state="normal")
            self.message_var.set(f"Recording to: {self.session.csv_path}")
            self.status_var.set("Active - VALORANT in foreground")
            self.hook = KeyboardHookManager(self.handle_key_event)
            self.hook.start()
            self.message_var.set(f"Session started: {self.session.csv_path}")
        except Exception as exc:  # pragma: no cover - GUI error path
            self.message_var.set(f"Failed to start recording: {exc}")
            self.session.reset()
            self.status_var.set("Error")

    def stop_recording(self) -> None:
        if self.hook is not None:
            try:
                self.hook.stop()
            except Exception as exc:  # pragma: no cover - GUI error path
                self.message_var.set(f"Hook shutdown warning: {exc}")
            finally:
                self.hook = None

        try:
            self.session.stop()
        except Exception as exc:  # pragma: no cover - GUI error path
            self.message_var.set(f"Error finalizing session: {exc}")

        self.current_session_label_var.set(self.session.session_id)
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.sync_btn.configure(state="disabled")
        self.status_var.set("Stopped")
        self.message_var.set(f"Session finished: {self.session.csv_path}")
        self.session.reset()

    def handle_key_event(self, vk_code: int, event_type: str) -> None:
        if not self.session.recording:
            return
        if not is_valorant_foreground():
            return
        self.session.push_event(vk_code, event_type)

    def insert_sync_marker(self) -> None:
        if not self.session.recording:
            self.message_var.set("No active recording session to mark.")
            return

        label = f"sync_{int(time.time() * 1000)}"
        self.session.insert_marker(label)
        self.message_var.set(f"Inserted sync marker: {label} at {utc_now_iso()}")

    def on_close(self) -> None:
        try:
            if self.hook is not None:
                self.hook.stop()
        except Exception:
            pass

        try:
            if self.session.recording:
                self.session.stop()
        except Exception:
            pass
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    if os_name() != "Windows":
        print("This application is designed for Windows and uses a low-level Windows keyboard hook.")
        print("Please run it on a Windows 10/11 machine with Python 3.10+ installed.")
        raise SystemExit(1)

    root = tk.Tk()
    app = ValorantKeyboardLoggerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


def os_name() -> str:
    import platform
    return platform.system()


if __name__ == "__main__":
    main()
