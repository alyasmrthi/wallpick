"""Applying a wallpaper to the running desktop.

Detection order: an explicit `setter_command` from the config wins, then awww
(the renamed swww), then swww, hyprpaper, feh, GNOME, KDE.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import config


class SetterError(RuntimeError):
    """Raised when no backend is available, or the backend refused."""


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def _run(argv: list[str], timeout: int = 20) -> None:
    try:
        subprocess.run(argv, check=True, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SetterError(f"{argv[0]} not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise SetterError(f"{argv[0]} timed out") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"exit code {exc.returncode}"
        raise SetterError(f"{argv[0]}: {message}") from exc


def _daemon_alive(probe: list[str]) -> bool:
    try:
        result = subprocess.run(probe, capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ensure_daemon(daemon: str, probe: list[str]) -> None:
    if _daemon_alive(probe):
        return
    if not _has(daemon):
        raise SetterError(f"{daemon} is not installed")
    try:
        subprocess.Popen(
            [daemon],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise SetterError(f"Could not start {daemon}: {exc}") from exc

    for _ in range(30):  # up to ~3 seconds
        time.sleep(0.1)
        if _daemon_alive(probe):
            return
    raise SetterError(f"{daemon} did not come up")


# --- individual backends -------------------------------------------------


def _apply_awww(path: Path) -> None:
    _ensure_daemon("awww-daemon", ["awww", "query"])
    _run([
        "awww", "img", str(path),
        "--transition-type", "fade",
        "--transition-duration", "1",
        "--transition-fps", "30",
    ])


def _apply_swww(path: Path) -> None:
    _ensure_daemon("swww-daemon", ["swww", "query"])
    _run([
        "swww", "img", str(path),
        "--transition-type", "fade",
        "--transition-duration", "1",
        "--transition-fps", "30",
    ])


def _apply_hyprpaper(path: Path) -> None:
    _run(["hyprctl", "hyprpaper", "preload", str(path)])
    _run(["hyprctl", "hyprpaper", "wallpaper", f",{path}"])


def _apply_feh(path: Path) -> None:
    _run(["feh", "--no-fehbg", "--bg-fill", str(path)])


def _apply_gnome(path: Path) -> None:
    uri = Path(path).resolve().as_uri()
    _run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri])
    _run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri])


def _apply_kde(path: Path) -> None:
    _run(["plasma-apply-wallpaperimage", str(path)])


BACKENDS: list[tuple[str, str, object]] = [
    ("awww", "awww", _apply_awww),
    ("swww", "swww", _apply_swww),
    ("hyprpaper", "hyprctl", _apply_hyprpaper),
    ("feh", "feh", _apply_feh),
    ("gnome", "gsettings", _apply_gnome),
    ("kde", "plasma-apply-wallpaperimage", _apply_kde),
]


def available_backends() -> list[str]:
    return [name for name, binary, _ in BACKENDS if _has(binary)]


def apply(path: Path, custom_command: str = "") -> str:
    """Set `path` as the wallpaper. Returns the backend name that handled it."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise SetterError(f"No such file: {path}")

    if custom_command.strip():
        argv = [arg.replace("{path}", str(path)) for arg in shlex.split(custom_command)]
        if not argv:
            raise SetterError("setter_command is empty after parsing")
        _run(argv, timeout=30)
        _record(path, "custom")
        return "custom"

    errors: list[str] = []
    for name, binary, handler in BACKENDS:
        if not _has(binary):
            continue
        try:
            handler(path)  # type: ignore[operator]
        except SetterError as exc:
            errors.append(f"{name}: {exc}")
            continue
        _record(path, name)
        return name

    if errors:
        raise SetterError("; ".join(errors))
    raise SetterError(
        "No wallpaper backend found. Install awww/swww, or set "
        '"setter_command" in the config.'
    )


def _record(path: Path, backend: str) -> None:
    """Leave a breadcrumb other scripts (theme engine, etc.) can read."""
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        config.STATE_FILE.write_text(
            json.dumps(
                {"path": str(path), "backend": backend, "applied_at": int(time.time())},
                indent=2,
            )
            + "\n"
        )
        link = config.CURRENT_LINK
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(path)
    except OSError:
        pass


def current() -> dict:
    try:
        return json.loads(config.STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}
