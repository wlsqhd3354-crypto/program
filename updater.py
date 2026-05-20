from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from config import APP_VERSION, UPDATE_INFO_URL, USER_AGENT
from paths import app_dir


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    notes: str = ""
    sha256: str = ""
    force: bool = False


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(p) for p in parts) if parts else (0,)


def is_newer_version(remote: str, current: str = APP_VERSION) -> bool:
    return _version_tuple(remote) > _version_tuple(current)


def check_for_update(timeout: int = 6) -> UpdateInfo | None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    }
    resp = requests.get(UPDATE_INFO_URL, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    version = str(data.get("version", "")).strip()
    download_url = str(data.get("download_url", "")).strip()
    if not version or not download_url:
        raise ValueError("update info requires version and download_url")

    if not is_newer_version(version):
        return None

    return UpdateInfo(
        version=version,
        download_url=download_url,
        notes=str(data.get("notes", "") or ""),
        sha256=str(data.get("sha256", "") or "").lower(),
        force=bool(data.get("force", False)),
    )


def download_update(info: UpdateInfo, timeout: int = 60) -> Path:
    target = Path(app_dir()) / "SellClubBot.update.exe"
    headers = {"User-Agent": USER_AGENT}
    with requests.get(info.download_url, headers=headers, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with open(target, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

    if info.sha256:
        digest = hashlib.sha256(target.read_bytes()).hexdigest().lower()
        if digest != info.sha256:
            try:
                target.unlink()
            except OSError:
                pass
            raise ValueError("downloaded update hash mismatch")

    return target


def install_and_restart(downloaded_exe: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("updates can only be installed from the packaged exe")

    current_exe = Path(sys.executable)
    bat_path = Path(app_dir()) / "apply_update.bat"
    bat = f"""@echo off
setlocal
timeout /t 2 /nobreak >nul
copy /Y "{downloaded_exe}" "{current_exe}" >nul
del "{downloaded_exe}" >nul 2>nul
start "" "{current_exe}"
del "%~f0" >nul 2>nul
"""
    bat_path.write_text(bat, encoding="utf-8")

    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        cwd=app_dir(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    os._exit(0)
