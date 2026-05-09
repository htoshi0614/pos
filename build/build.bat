@echo off
chcp 65001 > nul
title POSStart - Build

setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   POSStart - Windows Installer Build
echo ============================================================
echo.

cd /d "%~dp0"

:: ─── Step 1: Python依存パッケージ確認 ────────────────────
echo [1/5] PyInstallerの確認...
where pyinstaller > nul 2>&1
if errorlevel 1 (
    echo   PyInstallerが見つかりません。インストールします...
    pip install pyinstaller
    if errorlevel 1 (
        echo   ERROR: PyInstallerのインストールに失敗しました
        pause
        exit /b 1
    )
)
echo   OK
echo.

:: ─── Step 2: 依存パッケージのインストール ──────────────────
echo [2/5] 依存パッケージのインストール...
pip install -r ..\requirements.txt -q
if errorlevel 1 (
    echo   ERROR: 依存パッケージのインストールに失敗しました
    pause
    exit /b 1
)
echo   OK
echo.

:: ─── Step 3: 旧ビルド削除 ────────────────────────────────
echo [3/5] 旧ビルドのクリーンアップ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist output rmdir /s /q output
echo   OK
echo.

:: ─── Step 4: PyInstallerでEXE生成 ─────────────────────────
echo [4/5] POSStart.exe をビルド中（数分かかります）...
pyinstaller posstart.spec --clean --noconfirm
if errorlevel 1 (
    echo   ERROR: PyInstallerビルドに失敗しました
    pause
    exit /b 1
)

if not exist "dist\POSStart\POSStart.exe" (
    echo   ERROR: POSStart.exe が生成されませんでした
    pause
    exit /b 1
)
echo   OK: dist\POSStart\POSStart.exe
echo.

:: ─── Step 5: Inno Setupでインストーラー生成 ────────────────
echo [5/5] インストーラー（POSStart_setup.exe）を生成中...

:: Inno Setupの場所を探す
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe"      set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"

if "!ISCC!"=="" (
    echo.
    echo   Inno Setup が見つかりません → ZIP配布版を作成します
    echo.

    if not exist output mkdir output
    cd dist
    powershell -Command "Compress-Archive -Path POSStart -DestinationPath ../output/POSStart_v1.0.0.zip -Force"
    cd ..

    if not exist "output\POSStart_v1.0.0.zip" (
        echo   ERROR: ZIPファイルの生成に失敗しました
        pause
        exit /b 1
    )

    echo.
    echo ============================================================
    echo   ビルド完了（ZIP配布版）
    echo ============================================================
    echo.
    echo   生成されたファイル:
    echo     %CD%\output\POSStart_v1.0.0.zip
    echo.
    echo   お客様への案内:
    echo     1. ZIPをダウンロード
    echo     2. 任意の場所に解凍
    echo     3. POSStartフォルダ内のPOSStart.exeをダブルクリック
    echo.
    echo   ※ 正式なインストーラー（POSStart_setup.exe）が必要な場合:
    echo      Inno Setup を https://jrsoftware.org/isdl.php からインストール後
    echo      再度 build.bat を実行してください
    echo ============================================================
    pause
    exit /b 0
)

"!ISCC!" installer.iss
if errorlevel 1 (
    echo   ERROR: Inno Setup のビルドに失敗しました
    pause
    exit /b 1
)

if not exist "output\POSStart_setup.exe" (
    echo   ERROR: POSStart_setup.exe が生成されませんでした
    pause
    exit /b 1
)

echo   OK
echo.
echo ============================================================
echo   ビルド完了！
echo ============================================================
echo.
echo   生成されたファイル:
echo     %CD%\output\POSStart_setup.exe
echo.
echo   このファイルをお客様にダウンロードしてもらえばOKです。
echo ============================================================
echo.
pause
