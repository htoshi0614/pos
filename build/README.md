# POSStart Windows インストーラー ビルド手順

`POSStart_setup.exe` を生成するための開発者向けマニュアル。

---

## 📋 前提条件

ビルドするPCに以下が必要：

1. **Python 3.10 以降**
   - https://www.python.org/downloads/
   - インストール時 "Add Python to PATH" にチェック

2. **Inno Setup 6**（インストーラー生成用）
   - https://jrsoftware.org/isdl.php
   - 無料、デフォルト設定でインストール

3. **Windows 10 / 11**
   - PyInstallerは実行する OS 用のEXEを生成するため、Windows上でビルドが必要

---

## 🚀 ビルド方法

### ワンクリックビルド

`build/build.bat` をダブルクリック

→ 自動で以下が実行されます：
1. PyInstaller / 依存パッケージインストール
2. 古いビルドのクリーンアップ
3. `POSStart.exe` 生成（PyInstaller）
4. `POSStart_setup.exe` 生成（Inno Setup）

成果物: `build/output/POSStart_setup.exe`

---

### コマンドラインからビルド

```bash
cd build
pip install pyinstaller
pip install -r ..\requirements.txt
pyinstaller posstart.spec --clean --noconfirm
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

---

## 📦 生成物の構成

```
build/
├── dist/POSStart/         ← PyInstallerの出力
│   ├── POSStart.exe       ← 直接起動可能なEXE
│   ├── _internal/         ← 同梱DLL・Pythonランタイム
│   └── ...
├── output/
│   └── POSStart_setup.exe ← 配布用インストーラー（150〜250MB）
└── ...
```

---

## ✅ お客様の利用フロー

1. お客様が `POSStart_setup.exe` をダウンロード
2. ダブルクリック
3. インストーラー画面で「次へ」「次へ」「インストール」
4. デスクトップに「POSStart」アイコンが作成される
5. アイコンをダブルクリック → 黒いコンソール画面が起動 → ブラウザが自動で開く
6. ログインPW: `posstart2024`

---

## 🔧 アイコンの差し替え

`icon.ico` をこのディレクトリに配置すると、自動でEXEとインストーラーに使われます。

icoファイルは以下で簡単に作れます：
- https://convertico.com/  （PNG → ICO）
- https://www.icoconverter.com/

サイズ: 256x256, 128x128, 64x64, 32x32, 16x16 の複合ICO推奨。

---

## 🐛 よくあるトラブル

| 症状 | 対処 |
|------|------|
| `pyinstaller` コマンドが見つからない | `pip install pyinstaller` |
| Inno Setup が見つからない | https://jrsoftware.org/isdl.php からインストール |
| ビルド中にウィルス対策に止められる | 一時的に除外設定 |
| 起動時に「ポートが使用中」エラー | 既に起動中の可能性、タスクマネージャから停止 |
| 起動が遅い | PyInstallerのonefileは初回展開に時間がかかる仕様 |

---

## 📤 配布方法

### 推奨: Google Drive / Dropbox 共有
ファイルが大きい（150〜250MB）ため、メール添付は不可。
共有リンクを発行してお客様にお送りします。

### GitHub Releases
GitHub の Releases 機能でアップロードもOK（公開リポジトリ前提）。

### 自社サイト
将来的には自社サイトに「ダウンロード」ボタンを設置するのがベスト。

---

## 🔄 バージョンアップ

1. `installer.iss` の `AppVersion` を更新
2. `version_info.txt` の `filevers` / `prodvers` / `FileVersion` / `ProductVersion` を更新
3. `build.bat` 実行
4. 新しい `POSStart_setup.exe` を配布

お客様は新しいインストーラーを実行するだけで上書きアップデートされます。
（pos.db データは保持されます）
