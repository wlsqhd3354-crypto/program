"""실행 환경(스크립트 vs PyInstaller exe)에 무관하게 리소스 경로를 해석."""

import os
import sys


def app_dir() -> str:
    """exe(또는 .py)가 위치한 디렉토리. messages/, images/ 등을 여기 기준으로 찾음."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts: str) -> str:
    return os.path.join(app_dir(), *parts)
