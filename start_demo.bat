@echo off
chcp 65001 > nul
title POSStart デモ起動

echo.
echo  ============================================
echo   🍸 POSStart デモ環境 起動スクリプト
echo  ============================================
echo.

:: ─── 設定 ───────────────────────────────────────
:: ngrok の静的ドメインを設定してください
:: （未設定の場合は毎回URLが変わります）
set NGROK_DOMAIN=
:: 例: set NGROK_DOMAIN=your-name.ngrok-free.app
::
:: ngrokのパスを設定（PATHに入っていれば "ngrok" のままでOK）
set NGROK_CMD=ngrok
:: 例: set NGROK_CMD=C:\Users\htosh\Downloads\ngrok.exe
::
:: サーバーのポート
set PORT=8000
:: ─────────────────────────────────────────────────

:: サーバーディレクトリ（このbatファイルの場所）
set SERVER_DIR=%~dp0.claude\worktrees\dazzling-knuth

echo [1/3] デモデータを確認・投入中...
cd /d "%~dp0.claude\worktrees\dazzling-knuth"
python "%~dp0demo_seed.py"
echo.

echo [2/3] POSサーバーを起動中...
start "POSStart Server" /min cmd /c "cd /d "%SERVER_DIR%" && python -m uvicorn pos:app --host 0.0.0.0 --port %PORT% --reload"

:: サーバーの起動を少し待つ
timeout /t 3 /nobreak > nul

:: サーバーが起動したか確認
curl -s http://localhost:%PORT%/ > nul 2>&1
if errorlevel 1 (
    echo   サーバー起動中... もう少し待ちます
    timeout /t 3 /nobreak > nul
)

echo.
echo [3/3] ngrok トンネルを開通中...

if "%NGROK_DOMAIN%"=="" (
    echo   ⚠️  静的ドメイン未設定 → 毎回URLが変わります
    echo   ※ 永続URLにするには ngrok_setup.txt を参照してください
    echo.
    start "ngrok" %NGROK_CMD% http %PORT%
) else (
    echo   ドメイン: https://%NGROK_DOMAIN%
    start "ngrok" %NGROK_CMD% http --domain=%NGROK_DOMAIN% %PORT%
)

:: ngrok 起動を待つ
timeout /t 2 /nobreak > nul

echo.
echo  ============================================

if "%NGROK_DOMAIN%"=="" (
    echo   📡 アクセスURL: ngrokの黒い画面でURLを確認してください
    echo      例: https://xxxx-xx-xx-xx.ngrok-free.app
) else (
    echo   📡 アクセスURL: https://%NGROK_DOMAIN%
)

echo   🔑 ログインPW : posstart2024
echo   📊 ngrok管理  : http://localhost:4040
echo  ============================================
echo.
echo   このウィンドウを閉じるとサーバーは停止します
echo   （ngrokとサーバーのウィンドウは別で動いています）
echo.
pause
