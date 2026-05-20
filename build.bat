@echo off
REM 셀클럽 자동발송기 - PyInstaller 단일 exe 빌드 스크립트
REM 사용법: build.bat

setlocal

echo [1/3] 의존성 설치
pip install -r requirements.txt
pip install pyinstaller

echo [2/3] 빌드
pyinstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name "SellClubBot" ^
  --collect-all customtkinter ^
  main.py

echo [3/3] 배포 폴더 구성
if not exist dist\messages mkdir dist\messages
if not exist dist\images mkdir dist\images
copy README.txt dist\ >nul 2>nul

echo.
echo 완료. dist\SellClubBot.exe 를 사용자에게 전달하세요.
echo (messages\, images\ 폴더는 exe 옆에 자동 생성됨)
endlocal
pause
