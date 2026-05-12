from __future__ import annotations

import argparse
import ctypes
import os
import queue
import signal
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyautogui
import pyperclip
import pystray
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from PIL import Image, ImageDraw
from pynput import keyboard
from ctypes import wintypes


SAMPLE_RATE = 16_000
CHANNELS = 1
TOGGLE_KEY = keyboard.Key.f13
TOGGLE_KEY_NAME = "F13"
TOGGLE_MOUSE_BUTTON_NAME = "Mouse Button 5"
TOGGLE_LABEL = f"{TOGGLE_MOUSE_BUTTON_NAME} or {TOGGLE_KEY_NAME}"

WH_MOUSE_LL = 14
HC_ACTION = 0
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_XBUTTONDBLCLK = 0x020D
XBUTTON2 = 0x0002


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


LowLevelMouseProc = ctypes.WINFUNCTYPE(
    wintypes.LPARAM,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelMouseProc, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = wintypes.LPARAM
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


class SuppressingMouseHotkey:
    def __init__(self, on_toggle: callable) -> None:
        self.on_toggle = on_toggle
        self.thread: threading.Thread | None = None
        self.thread_id: int | None = None
        self.hook: int | None = None
        self.callback = LowLevelMouseProc(self._handle_event)
        self.ready = threading.Event()
        self.stop_event = threading.Event()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="mouse-hotkey-hook", daemon=True)
        self.thread.start()
        self.ready.wait(timeout=2.0)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread_id is not None:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread is not None:
            self.thread.join(timeout=1.0)

    def _run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()
        self.hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self.callback,
            kernel32.GetModuleHandleW(None),
            0,
        )
        if not self.hook:
            print("Could not install low-level mouse hook.", file=sys.stderr)
            self.ready.set()
            return

        self.ready.set()
        msg = wintypes.MSG()
        while not self.stop_event.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self.hook:
            user32.UnhookWindowsHookEx(self.hook)
            self.hook = None

    def _handle_event(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == HC_ACTION and w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK):
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            x_button = (info.mouseData >> 16) & 0xFFFF
            if x_button == XBUTTON2:
                if w_param == WM_XBUTTONDOWN:
                    self.on_toggle()
                return 1

        return user32.CallNextHookEx(self.hook, n_code, w_param, l_param)


@dataclass(frozen=True)
class Settings:
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = "ru"
    beam_size: int = 1
    vad_filter: bool = True
    restore_clipboard: bool = True
    restore_clipboard_delay: float = 0.5
    min_record_seconds: float = 0.35


class WhisperTap:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model: WhisperModel | None = None
        self.icon: pystray.Icon | None = None
        self.listener: keyboard.Listener | None = None
        self.mouse_hotkey: SuppressingMouseHotkey | None = None
        self.stop_event = threading.Event()
        self.recording = False
        self.processing = False
        self.pressed: set[keyboard.Key | keyboard.KeyCode] = set()
        self.last_toggle_at = 0.0
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self.audio_chunks: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.record_started_at = 0.0
        self.lock = threading.RLock()

    def run(self) -> None:
        self._set_status("Loading model...")
        self.model = WhisperModel(
            self.settings.model,
            device=self.settings.device,
            compute_type=self.settings.compute_type,
        )
        self._set_status(f"Ready: press {TOGGLE_LABEL}")

        self.listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self.listener.start()
        self.mouse_hotkey = SuppressingMouseHotkey(self._toggle_from_mouse)
        self.mouse_hotkey.start()

        self.icon = pystray.Icon(
                "WhisperTap",
                self._make_icon("ready"),
                "WhisperTap - ready",
                menu=pystray.Menu(
                pystray.MenuItem(f"Press {TOGGLE_LABEL} to start/stop dictation", None, enabled=False),
                pystray.MenuItem("Exit and unload model", self.quit),
            ),
        )
        self.icon.run()

    def quit(self, _icon: pystray.Icon | None = None, _item: object | None = None) -> None:
        self.stop_event.set()
        with self.lock:
            if self.recording:
                self._stop_recording_locked(process=False)
        if self.listener is not None:
            self.listener.stop()
        if self.mouse_hotkey is not None:
            self.mouse_hotkey.stop()
        if self.icon is not None:
            self.icon.stop()

    def _on_key_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        with self.lock:
            self.pressed.add(key)
            if self.stop_event.is_set() or self.processing or key != TOGGLE_KEY:
                return
            self._toggle_recording_locked()

    def _on_key_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        with self.lock:
            self.pressed.discard(key)

    def _toggle_from_mouse(self) -> None:
        with self.lock:
            if self.stop_event.is_set() or self.processing:
                return
            self._toggle_recording_locked()

    def _toggle_recording_locked(self) -> None:
        now = time.monotonic()
        if now - self.last_toggle_at < 0.2:
            return
        self.last_toggle_at = now
        if self.recording:
            self._stop_recording_locked(process=True)
        else:
            self._start_recording_locked()

    def _start_recording_locked(self) -> None:
        self.audio_chunks = []
        self.audio_queue = queue.Queue()
        self.record_started_at = time.monotonic()
        self.recording = True
        self._set_status("Recording...")

        def callback(indata: np.ndarray, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
            if status:
                print(f"Audio status: {status}", file=sys.stderr)
            self.audio_queue.put(indata.copy())

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=callback,
        )
        self.stream.start()

    def _stop_recording_locked(self, process: bool) -> None:
        self.recording = False
        stream = self.stream
        self.stream = None
        if stream is not None:
            stream.stop()
            stream.close()

        while not self.audio_queue.empty():
            self.audio_chunks.append(self.audio_queue.get_nowait())

        duration = time.monotonic() - self.record_started_at
        if not process or duration < self.settings.min_record_seconds or not self.audio_chunks:
            self._set_status(f"Ready: press {TOGGLE_LABEL}")
            return

        audio = np.concatenate(self.audio_chunks, axis=0)
        self.processing = True
        self._set_status("Transcribing...")
        threading.Thread(target=self._transcribe_and_paste, args=(audio,), daemon=True).start()

    def _transcribe_and_paste(self, audio: np.ndarray) -> None:
        tmp_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix="whispertap-", suffix=".wav")
            os.close(fd)
            tmp_path = Path(name)
            sf.write(tmp_path, audio, SAMPLE_RATE)

            assert self.model is not None
            segments, _info = self.model.transcribe(
                str(tmp_path),
                language=self.settings.language,
                beam_size=self.settings.beam_size,
                vad_filter=self.settings.vad_filter,
                condition_on_previous_text=False,
            )
            text = self._clean_text(" ".join(segment.text.strip() for segment in segments))
            if text:
                self._paste_text(text)
                self._set_status("Inserted text")
            else:
                self._set_status("No speech detected")
        except Exception as exc:
            self._set_status("Error - see console")
            print(f"WhisperTap error: {exc}", file=sys.stderr)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            with self.lock:
                self.processing = False
                if not self.stop_event.is_set():
                    time.sleep(0.4)
                    self._set_status(f"Ready: press {TOGGLE_LABEL}")

    def _paste_text(self, text: str) -> None:
        old_clipboard = None
        if self.settings.restore_clipboard:
            try:
                old_clipboard = pyperclip.paste()
            except pyperclip.PyperclipException:
                old_clipboard = None

        pyperclip.copy(text)
        time.sleep(0.2)
        self._send_ctrl_v()
        print("Paste hotkey sent", flush=True)

        if self.settings.restore_clipboard and old_clipboard is not None:
            time.sleep(self.settings.restore_clipboard_delay)
            pyperclip.copy(old_clipboard)

    @staticmethod
    def _send_ctrl_v() -> None:
        try:
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_V, 0, 0, 0)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pyautogui.hotkey("ctrl", "v")

    @staticmethod
    def _clean_text(text: str) -> str:
        text = " ".join(text.split())
        replacements = {
            " ,": ",",
            " .": ".",
            " !": "!",
            " ?": "?",
            " :": ":",
            " ;": ";",
            " )": ")",
            "( ": "(",
        }
        for before, after in replacements.items():
            text = text.replace(before, after)
        return text

    def _set_status(self, status: str) -> None:
        print(status, flush=True)
        if self.icon is not None:
            self.icon.title = f"WhisperTap - {status}"

    @staticmethod
    def _make_icon(state: str) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        fill = (40, 160, 90, 255) if state == "ready" else (220, 80, 60, 255)
        draw.rounded_rectangle((16, 8, 48, 42), radius=14, fill=fill)
        draw.rectangle((27, 38, 37, 52), fill=fill)
        draw.arc((18, 24, 46, 56), 0, 180, fill=fill, width=5)
        draw.line((22, 56, 42, 56), fill=fill, width=5)
        return image


def parse_args() -> Settings:
    parser = argparse.ArgumentParser(description="Local GPU dictation that pastes text into the active app.")
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--language", default="ru", help="Use 'auto' for language auto-detection.")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--no-vad", action="store_true")
    parser.add_argument("--keep-clipboard", action="store_true")
    parser.add_argument("--restore-clipboard-delay", type=float, default=0.5)
    args = parser.parse_args()

    language = None if args.language.lower() == "auto" else args.language
    return Settings(
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=language,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
        restore_clipboard=not args.keep_clipboard,
        restore_clipboard_delay=args.restore_clipboard_delay,
    )


def main() -> int:
    pyautogui.FAILSAFE = False
    app = WhisperTap(parse_args())

    def handle_signal(_signum: int, _frame: object) -> None:
        app.quit()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
