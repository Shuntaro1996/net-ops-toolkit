@echo off
setlocal
cd /d "%~dp0"

title Network Ops Toolkit Launcher

echo ===================================================
echo   Network Ops Toolkit - Launcher
echo ===================================================
echo.

:: Python のインストール確認
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。
    echo Python 3.10 以上がインストールされ、PATH に追加されているか確認してください。
    echo.
    pause
    exit /b 1
)

:: 依存ライブラリの確認・自動インストール
echo [INFO] 依存ライブラリを確認中...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [WARNING] パッケージのインストール中に警告またはエラーが発生しました。
    echo そのまま起動を試みます...
)

echo.
echo [INFO] Network Ops Toolkit を起動しています...
echo [INFO] ブラウザが自動的に開きます (http://localhost:8501)
echo.
echo ※ このウィンドウを閉じるとアプリが終了します。
echo ===================================================
echo.

:: Streamlit アプリの起動
python -m streamlit run app.py --server.headless false

pause
