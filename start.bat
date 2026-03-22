@echo off
title Girls Bar POS
cd /d "%~dp0"

echo ================================
echo  Girls Bar POS - 起動中...
echo ================================
echo.

echo [1/3] アップデートを確認中...
git pull --ff-only 2>nul && (
    echo   最新バージョンに更新しました
) || (
    echo   (git なし / オフライン - スキップ)
)
echo.

echo [2/3] 依存パッケージを確認中...
pip install -r requirements.txt -q --disable-pip-version-check
echo   完了
echo.

echo [3/3] サーバーを起動中...
echo   ブラウザで開く: http://localhost:8000
echo.
start "" "http://localhost:8000"

uvicorn pos:app --host 0.0.0.0 --port 8000 --reload
pause
