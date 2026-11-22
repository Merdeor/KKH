#!/usr/bin/env python3
"""phone-scan 폴더를 바탕화면에 복사하고 더블클릭 실행 파일을 만든다 (Windows 기준)."""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COPY_FILES = ["scan.py", "blocklist.txt", "README.md"]

BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (py -3 scan.py) || (python scan.py)
echo.
pause
"""


def desktop_dir(env=None, home=None):
    """OneDrive로 리디렉션된 바탕화면까지 고려해 바탕화면 경로를 찾는다."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    candidates = []
    for key in ("OneDriveConsumer", "OneDrive"):
        if env.get(key):
            candidates.append(Path(env[key]) / "Desktop")
            candidates.append(Path(env[key]) / "바탕 화면")
    candidates += [home / "Desktop", home / "바탕 화면"]
    for c in candidates:
        if c.is_dir():
            return c
    raise FileNotFoundError("바탕화면 폴더를 찾지 못했습니다: " + ", ".join(map(str, candidates)))


def install(dest_root, src=HERE):
    dest = Path(dest_root) / "폰검사"
    dest.mkdir(parents=True, exist_ok=True)
    for name in COPY_FILES:
        f = Path(src) / name
        if f.exists():
            # 사용자가 편집한 blocklist는 덮어쓰지 않는다
            if name == "blocklist.txt" and (dest / name).exists():
                continue
            shutil.copy2(f, dest / name)
    (dest / "폰검사.bat").write_text(BAT.replace("\n", "\r\n"), encoding="utf-8", newline="")
    # 바탕화면에서 바로 보이도록 폴더 밖에도 실행 파일을 둔다
    shortcut = Path(dest_root) / "폰검사 실행.bat"
    shortcut.write_text(
        '@echo off\r\ncd /d "%~dp0폰검사"\r\ncall 폰검사.bat\r\n', encoding="utf-8", newline="")
    return dest


def main():
    dest = install(desktop_dir())
    print("설치 완료: %s\n바탕화면의 '폰검사 실행.bat'을 더블클릭하세요." % dest)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
