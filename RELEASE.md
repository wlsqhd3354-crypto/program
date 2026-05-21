# Release Workflow

## 1. Version bump

Update `APP_VERSION` in `config.py`.

## 2. Build EXE

Run:

```bat
build.bat
```

The executable is created at:

```text
dist\SellClubBot.exe
```

## 3. Create GitHub Release

Create a new release tag such as:

```text
v1.3.3
```

Upload these files as release assets:

```text
SellClubBot.exe
version.json
```

## 4. version.json

Use this format:

```json
{
  "version": "1.3.3",
  "download_url": "https://github.com/wlsqhd3354-crypto/program/releases/download/v1.3.3/SellClubBot.exe",
  "notes": "공통 탭 입력칸 우선 발송, 반복발송 간격 초/분/시간 단위 선택",
  "sha256": "",
  "force": false
}
```

The app checks:

```text
https://github.com/wlsqhd3354-crypto/program/releases/latest/download/version.json
```

If `version` is newer than `APP_VERSION`, the user will see an update prompt.
