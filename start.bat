@echo off
chcp 65001 >nul 2>&1
title Girls Bar POS
cd /d "%~dp0"

echo ================================
echo  Girls Bar POS - 起動中...
echo ================================
echo.

REM ====== Python 検出 ======
where py >nul 2>&1
if errorlevel 1 (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [エラー] Python が見つかりません。インストールしてください。
        pause
        exit /b 1
    ) else (
        set "PY=python"
    )
) else (
    set "PY=py"
)

echo [1/3] アップデートを確認中...
git pull --ff-only 2>nul && (
    echo   最新バージョンに更新しました
) || (
    echo   (git なし / オフライン - スキップ)
)
echo.

echo [2/3] 依存パッケージを確認中...
%PY% -m pip install -r requirements.txt -q --disable-pip-version-check 2>nul
echo   完了
echo.

echo [3/3] サーバーを起動中...
echo.

REM ====== ヘルスチェック後にブラウザを開く ======
start "" cmd /c "timeout /t 3 /nobreak >nul && start "" http://127.0.0.1:8000/ui"

%PY% -m uvicorn pos:app --host 0.0.0.0 --port 8000
pause
