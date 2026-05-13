"""
launcher.py — POSStart Windows実行ファイルのエントリーポイント

PyInstallerで実行ファイル化する際の起点となるスクリプト。
1) 必要に応じて初期データベースをセットアップ
2) FastAPI サーバーを起動
3) 自動でブラウザを開く
"""
import os
import sys
import threading
import time
import webbrowser
import socket

# Windowsの日本語表示対応
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 実行ファイル化されているかどうかでベースディレクトリを切り替え
if getattr(sys, 'frozen', False):
    # PyInstallerでビルドされた状態
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS  # PyInstallerの一時展開先（読み取り専用）
else:
    # 開発時（直接Pythonで起動）
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = os.path.normpath(os.path.join(APP_DIR, '..'))
    BUNDLE_DIR = APP_DIR

# データ（pos.db、.env等）はAPP_DIRに保存（書き込み可能な場所）
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, BUNDLE_DIR)

PORT = 8000

def find_free_port(start=8000, end=8100):
    """指定範囲で空いているポートを探す"""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return start

def is_port_open(port):
    """既にPOSStartが起動しているかチェック"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            return s.connect_ex(('127.0.0.1', port)) == 0
        except Exception:
            return False

def open_browser_when_ready(port):
    """サーバー起動を待ってからブラウザを開く"""
    for _ in range(30):  # 最大30秒待機
        if is_port_open(port):
            time.sleep(0.5)  # 念のためもう少し待つ
            webbrowser.open(f'http://localhost:{port}')
            return
        time.sleep(1)

APP_VERSION = "1.0.1"
UPDATE_INFO = {"available": False, "latest": "", "url": ""}

def check_for_update():
    """GitHub Latest Releaseを確認し、新バージョンがあればコンソールに通知"""
    try:
        import urllib.request, json
        req = urllib.request.Request(
            'https://api.github.com/repos/htoshi0614/pos/releases/latest',
            headers={'Accept': 'application/vnd.github+json', 'User-Agent': f'POSStart/{APP_VERSION}'}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        latest = (data.get('tag_name') or '').lstrip('v')
        if latest and latest != APP_VERSION:
            UPDATE_INFO['available'] = True
            UPDATE_INFO['latest'] = latest
            UPDATE_INFO['url'] = data.get('html_url', '')
            # 環境変数に保存しておく（pos.pyから読める）
            os.environ['POSSTART_UPDATE_AVAILABLE'] = '1'
            os.environ['POSSTART_UPDATE_LATEST'] = latest
            os.environ['POSSTART_UPDATE_URL'] = UPDATE_INFO['url']
            print()
            print('=' * 56)
            print(f'  📢 新しいバージョン v{latest} がリリースされています！')
            print(f'  現在のバージョン: v{APP_VERSION}')
            print(f'  ダウンロード   : {UPDATE_INFO["url"]}')
            print(f'  ※ 新インストーラーを実行するだけで上書き更新されます')
            print(f'    （データは保持されます）')
            print('=' * 56)
            print()
    except Exception:
        # オフライン時・GitHub API障害時などは何もせずスキップ
        pass

def first_run_setup():
    """初回起動時のデータベースセットアップ"""
    db_path = os.path.join(APP_DIR, 'pos.db')
    if not os.path.exists(db_path):
        print('[初回起動] データベースを初期化しています...')
        try:
            # モジュールをロードするだけでテーブルが作られる
            from db_shared import Base, engine
            import pos  # noqa: F401 - モデル定義を読み込ませる
            import stripe_service  # noqa: F401
            import cast_salary  # noqa: F401
            import bottle_keep  # noqa: F401
            import customer_crm  # noqa: F401
            import closing  # noqa: F401
            import management  # noqa: F401
            import tab_management  # noqa: F401
            Base.metadata.create_all(engine)
            print('[初回起動] データベース初期化完了')
        except Exception as e:
            print(f'[警告] データベース初期化エラー: {e}')

def main():
    global PORT

    # 既に起動していたらブラウザだけ開いて終了
    if is_port_open(PORT):
        print(f'POSStart は既に起動しています。ブラウザを開きます...')
        webbrowser.open(f'http://localhost:{PORT}')
        return

    PORT = find_free_port(PORT, PORT + 100)

    print('=' * 56)
    print('  🍸  POSStart を起動しています...')
    print('=' * 56)
    print(f'  起動ディレクトリ : {APP_DIR}')
    print(f'  ポート          : {PORT}')
    print(f'  アクセスURL     : http://localhost:{PORT}')
    print('=' * 56)
    print('  ※ このウィンドウを閉じるとPOSStartが停止します')
    print('=' * 56)

    first_run_setup()

    # アップデート確認（別スレッドでバックグラウンド実行・サーバー起動を妨げない）
    threading.Thread(target=check_for_update, daemon=True).start()

    # ブラウザ自動起動（別スレッド）
    threading.Thread(target=open_browser_when_ready, args=(PORT,), daemon=True).start()

    # サーバー起動（メインスレッド）
    import uvicorn
    from pos import app
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nPOSStart を停止しました')
    except Exception as e:
        print(f'\n[エラー] 予期しないエラーが発生しました:')
        print(f'  {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        input('\nEnterキーを押すと終了します...')
