from __future__ import annotations

import io
import subprocess
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from app.telegram_wallet_reader.config import telegram_wallet_reader_settings


@dataclass(slots=True)
class TelegramWindowInfo:
    title: str
    handle: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def area(self) -> int:
        return max(0, self.right - self.left) * max(0, self.bottom - self.top)


def _artifacts_dir() -> Path:
    path = Path(telegram_wallet_reader_settings.artifacts_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _downloads_dir() -> Path:
    path = Path(telegram_wallet_reader_settings.downloads_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_telegram_windows(title_contains: str | None = None) -> list[TelegramWindowInfo]:
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise RuntimeError("pywinauto is not installed. Run: pip install -e .") from exc

    needle = (title_contains or telegram_wallet_reader_settings.telegram_window_title_contains).lower()
    windows: list[TelegramWindowInfo] = []
    for window in Desktop(backend="uia").windows():
        title = window.window_text() or ""
        if needle not in title.lower():
            continue
        rect = window.rectangle()
        windows.append(
            TelegramWindowInfo(
                title=title,
                handle=int(window.handle),
                left=int(rect.left),
                top=int(rect.top),
                right=int(rect.right),
                bottom=int(rect.bottom),
            )
        )
    return windows


def focus_telegram_window(title_contains: str | None = None):
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise RuntimeError("pywinauto is not installed. Run: pip install -e .") from exc

    windows = find_telegram_windows(title_contains)
    if not windows:
        raise RuntimeError("Telegram window was not found")
    best_window = max(windows, key=lambda item: item.area)
    handle = best_window.handle
    window = Desktop(backend="uia").window(handle=handle)
    window.set_focus()
    try:
        window.restore()
    except Exception:
        pass
    return window


def launch_telegram(executable_path: str | None = None) -> int:
    target = executable_path or telegram_wallet_reader_settings.telegram_exe_path
    if not target:
        raise RuntimeError("TELEGRAM_WALLET_READER_TELEGRAM_EXE_PATH is not configured")
    process = subprocess.Popen([target])
    return int(process.pid)


def capture_telegram_window(output_path: str | Path, title_contains: str | None = None) -> Path:
    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise RuntimeError("Pillow is not installed. Run: pip install -e .") from exc

    window = focus_telegram_window(title_contains)
    rect = window.rectangle()
    image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output


def dump_telegram_controls(output_path: str | Path, title_contains: str | None = None) -> Path:
    window = focus_telegram_window(title_contains)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        window.print_control_identifiers(depth=4)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(buffer.getvalue(), encoding="utf-8")
    return output


def latest_wallet_csv(pattern: str | None = None, downloads_dir: str | Path | None = None) -> Path | None:
    target_dir = Path(downloads_dir) if downloads_dir else _downloads_dir()
    file_pattern = pattern or telegram_wallet_reader_settings.csv_pattern
    candidates = sorted(target_dir.glob(file_pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def wait_for_new_wallet_csv(
    *,
    known_latest: Path | None = None,
    pattern: str | None = None,
    downloads_dir: str | Path | None = None,
    timeout_seconds: int | None = None,
    poll_interval_seconds: int | None = None,
) -> Path:
    target_dir = Path(downloads_dir) if downloads_dir else _downloads_dir()
    file_pattern = pattern or telegram_wallet_reader_settings.csv_pattern
    timeout = timeout_seconds or telegram_wallet_reader_settings.wait_timeout_seconds
    interval = poll_interval_seconds or telegram_wallet_reader_settings.poll_interval_seconds
    baseline = str(known_latest.resolve()) if known_latest and known_latest.exists() else None

    deadline = time.time() + timeout
    while time.time() < deadline:
        latest = latest_wallet_csv(file_pattern, target_dir)
        if latest is not None:
            candidate = str(latest.resolve())
            if baseline is None or candidate != baseline:
                return latest
        time.sleep(interval)
    raise TimeoutError("Timed out waiting for a new Wallet CSV export")


def default_window_screenshot_path() -> Path:
    return _artifacts_dir() / "telegram-window.png"


def default_controls_dump_path() -> Path:
    return _artifacts_dir() / "telegram-controls.txt"
