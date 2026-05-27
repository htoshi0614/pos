# pos.py 内の残ダーク :root ブロック（メインUI以外）を Premium Pink 化
import re
from pathlib import Path

NEW_ROOT = (
    ":root{"
    "--bg:#fafafa;--card:#ffffff;--card2:#ffffff;"
    "--line:#eaeaef;--border:#eaeaef;"
    "--text:#0a0a0f;--ink:#0a0a0f;"
    "--muted:#8a8a95;--body:#4a4a55;"
    "--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;"
    "--gold:#c9a96e;--gold-soft:#faf3e3;"
    "--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;"
    "--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;"
    "}"
)

OVERRIDE_CSS = """
/* === Premium Pink Theme Override (auto-injected) === */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
body{font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif !important;background:#fafafa !important;color:#0a0a0f !important;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{color:#0a0a0f}
a{color:#d64583}
.card,section,article{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.stat,.kpi,.tile,.metric{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f}
table{border-color:#eaeaef !important}
th{background:#fafafa !important;color:#4a4a55 !important;border-color:#eaeaef !important;font-weight:700 !important}
td{border-color:#f3f3f6 !important;color:#0a0a0f !important;background:#ffffff}
tr:nth-child(even) td{background:#fafafa}
.btn{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;font-weight:600 !important;transition:all .2s}
.btn:hover{border-color:#d64583 !important;color:#d64583 !important}
.btn.primary,.btn.solid{background:#d64583 !important;border-color:#d64583 !important;color:#ffffff !important}
.btn.danger{background:#ef4444 !important;border-color:#ef4444 !important;color:#ffffff !important}
input,select,textarea{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important}
input:focus,select:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important;outline:none}
.method.POST{background:#f0fdf4 !important;color:#15803d !important}
.method.DELETE{background:#fef2f2 !important;color:#b91c1c !important}
.method.PATCH{background:#fff7ed !important;color:#c2410c !important}
.method.PUT{background:#eff6ff !important;color:#1d4ed8 !important}
.cast-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.cast-card.in .status{background:#f0fdf4 !important;color:#15803d !important}
.cast-card.out .status{background:#fafafa !important;color:#8a8a95 !important}
.bar{background:#ffffff !important;border-color:#eaeaef}
.notice{background:#fff7ed !important;color:#7c2d12 !important;border-left:3px solid #f59e0b !important}
.modal-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.toast.ok{background:#f0fdf4 !important;color:#14532d !important;border-color:#86efac !important}
.toast.err{background:#fef2f2 !important;color:#7f1d1d !important;border-color:#fca5a5 !important}
[style*="background:#111827"],[style*="background:#0f172a"],[style*="background:#0a1220"],[style*="background:#0e1a26"]{background:#ffffff !important;color:#0a0a0f !important}
[style*="border:1px solid #263244"],[style*="border:1px solid #334155"]{border-color:#eaeaef !important}
"""

# 暗い :root のみマッチ（メイン UI の Premium Pink 版は --accent:#d64583 で書かれている）
DARK_ROOT = re.compile(r":root\{--bg:#0b1220[^}]*\}")

# OVERRIDE_CSS が同じ <style> 内に既に入っているか確認するためのマーカー
MARKER = "Premium Pink Theme Override"


def patch(file_path: Path):
    text = file_path.read_text(encoding="utf-8")
    matches = list(DARK_ROOT.finditer(text))
    if not matches:
        print(f"  no dark :root found in {file_path}")
        return False

    # 各 :root から最寄りの </style> までを抽出 → 既に MARKER がなければ注入
    new_text = text
    # 逆順に処理（位置がずれないように）
    for m in reversed(matches):
        # この :root の直後に存在する最初の </style> を見つける
        start = m.end()
        close_idx = new_text.find("</style>", start)
        if close_idx < 0:
            continue
        segment = new_text[start:close_idx]
        if MARKER in segment:
            # 既に注入済み（:root も置換済みの可能性大）
            continue
        # :root を新しいパレットに置換
        new_text = new_text[:m.start()] + NEW_ROOT + new_text[m.end():close_idx] + OVERRIDE_CSS + "\n" + new_text[close_idx:]
    if new_text != text:
        file_path.write_text(new_text, encoding="utf-8")
        return True
    return False


def main():
    targets = [
        Path(r"C:\Users\htosh\OneDrive\デスクトップ\posstart\pos.py"),
        Path(r"C:\Users\htosh\OneDrive\デスクトップ\POSv2\pos.py"),
    ]
    for p in targets:
        print(f"\n=== {p} ===")
        if patch(p):
            print("  [patched]")
        else:
            print("  [unchanged]")


if __name__ == "__main__":
    main()
