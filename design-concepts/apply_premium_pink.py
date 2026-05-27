# Premium Pink テーマを派生画面に一括適用するパッチスクリプト
# 各モジュールの <style> 内 :root{...} を置換し、override block を追加する
import re
import sys
from pathlib import Path

MODULES = [
    "backup_service.py",
    "bottle_keep.py",
    "cast_salary.py",
    "closing.py",
    "customer_crm.py",
    "data_import.py",
    "management.py",
    "point_mail.py",
    "pricing_engine.py",
    "stripe_service.py",
    "tab_management.py",
    "weather_service.py",
]

# 各モジュールが使用している :root 行をライト化する変数を網羅
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

# Premium Pink 共通オーバーライド（各<style>に注入）
OVERRIDE_CSS = """
/* === Premium Pink Theme Override (auto-injected) === */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
body{font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif !important;background:#fafafa !important;color:#0a0a0f !important;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{color:#0a0a0f}
h1{font-weight:800;letter-spacing:-.01em}
a{color:#d64583}
.card,.box,section,article{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.stat,.kpi,.tile,.metric{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f}
.stat .val,.kpi .val,.metric .val,.tile .val{color:#0a0a0f !important}
.stat .label,.kpi .label,.tile .label{color:#8a8a95 !important}
table{border-color:#eaeaef !important}
th{background:#fafafa !important;color:#4a4a55 !important;font-weight:700 !important;border-color:#eaeaef !important;letter-spacing:.02em}
td{border-color:#f3f3f6 !important;color:#0a0a0f !important;background:#ffffff}
tr:nth-child(even) td{background:#fafafa}
.badge.open{background:#fafafa !important;color:#8a8a95 !important;border:1px solid #eaeaef !important}
.badge.preliminary{background:#fff7ed !important;color:#c2410c !important;border-color:#fed7aa !important}
.badge.final{background:#f0fdf4 !important;color:#15803d !important;border-color:#86efac !important}
.btn{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;transition:all .2s;font-weight:600 !important}
.btn:hover{border-color:#d64583 !important;color:#d64583 !important;background:#ffffff !important;transform:translateY(-1px);box-shadow:0 2px 8px rgba(214,69,131,.08)}
.btn.primary,.btn.solid{background:#d64583 !important;border-color:#d64583 !important;color:#ffffff !important}
.btn.primary:hover,.btn.solid:hover{background:#b03468 !important;border-color:#b03468 !important;color:#ffffff !important;opacity:1 !important}
.btn.warn{background:#f59e0b !important;border-color:#f59e0b !important;color:#ffffff !important}
.btn.danger,.btn.err{background:#ef4444 !important;border-color:#ef4444 !important;color:#ffffff !important}
input,select,textarea{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;font-family:inherit}
input:focus,select:focus,textarea:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important;outline:none}
.tab{color:#8a8a95 !important;background:transparent !important}
.tab.active{background:#fdf0f7 !important;color:#d64583 !important;border-color:#eaeaef !important;border-bottom-color:#fdf0f7 !important;font-weight:700}
.tab-body{background:#ffffff !important;border-color:#eaeaef !important}
.toast.ok{background:#f0fdf4 !important;color:#14532d !important;border:1px solid #86efac}
.toast.err{background:#fef2f2 !important;color:#7f1d1d !important;border:1px solid #fca5a5}
.history-row{border-color:#f3f3f6 !important}
header{background:rgba(255,255,255,.92) !important;backdrop-filter:blur(20px);border-color:#eaeaef !important;color:#0a0a0f !important}
header h1{background:linear-gradient(135deg,#0a0a0f,#d64583);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
nav a,.nav a{color:#4a4a55}
nav a:hover,.nav a:hover{color:#d64583}
/* よくあるダーク背景の hex 値を強制ライト化 */
[style*="background:#0f172a"],[style*="background:#0b1220"],[style*="background:#1e293b"],[style*="background:#0a1423"],[style*="background:#111827"],[style*="background:#1a1d29"]{background:#ffffff !important;color:#0a0a0f !important;border-color:#eaeaef}
[style*="color:#e5e7eb"],[style*="color:#e2e8f0"]{color:#0a0a0f !important}
[style*="color:#94a3b8"],[style*="color:#9ca3af"]{color:#8a8a95 !important}
[style*="border:1px solid #1f2937"],[style*="border:1px solid #334155"],[style*="border:1px solid #263244"]{border-color:#eaeaef !important}
"""


def patch(file_path: Path) -> bool:
    text = file_path.read_text(encoding="utf-8")
    original = text

    # 既に適用済みならスキップ
    if "Premium Pink Theme Override" in text:
        return False

    # :root{...} を全置換
    pattern = re.compile(r":root\s*\{[^}]*\}")
    if not pattern.search(text):
        print(f"  [skip] :root not found in {file_path.name}")
        return False
    text = pattern.sub(NEW_ROOT, text, count=1)

    # 最初の </style> の直前に OVERRIDE_CSS を挿入
    if "</style>" not in text:
        print(f"  [skip] </style> not found in {file_path.name}")
        return False

    text = text.replace("</style>", OVERRIDE_CSS + "\n</style>", 1)

    if text != original:
        file_path.write_text(text, encoding="utf-8")
        return True
    return False


def main():
    roots = [
        Path(r"C:\Users\htosh\OneDrive\デスクトップ\posstart"),
        Path(r"C:\Users\htosh\OneDrive\デスクトップ\POSv2"),
    ]
    for root in roots:
        print(f"\n=== {root} ===")
        for mod in MODULES:
            p = root / mod
            if not p.exists():
                print(f"  [missing] {mod}")
                continue
            if patch(p):
                print(f"  [patched] {mod}")
            else:
                print(f"  [unchanged] {mod}")


if __name__ == "__main__":
    main()
