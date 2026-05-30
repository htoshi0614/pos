# 配色修正 v2: point_mail / data_import / closing の残ダーク要素を Premium Pink ライト化
# 既存の Override がカバーしていないページ固有クラスを、より後段で !important 上書きする
from pathlib import Path

MARKER = "Premium Pink v2 (page-specific)"

V2 = """
/* === Premium Pink v2 (page-specific) === */
.preset-btn{background:#ffffff !important;border:2px solid #eaeaef !important;color:#0a0a0f !important}
.preset-btn:hover,.preset-btn.active{border-color:#d64583 !important;background:#fdf0f7 !important;color:#b03468 !important}
.preset-btn .icon{color:#d64583 !important}
.btn.green,.btn.success{background:#f0fdf4 !important;border-color:#86efac !important;color:#15803d !important}
.btn.danger,.btn.err{background:#fef2f2 !important;border-color:#fca5a5 !important;color:#b91c1c !important}
.btn.solid,.btn.primary{color:#ffffff !important}
.step-num{color:#ffffff !important;background:#d64583 !important}
.badge.sent,.badge.success,.badge.ok{background:#f0fdf4 !important;color:#15803d !important;border:1px solid #86efac !important}
.badge.failed,.badge.error,.badge.ng{background:#fef2f2 !important;color:#b91c1c !important;border:1px solid #fca5a5 !important}
.status-bar{border-radius:10px}
.status-bar.ok{background:#f0fdf4 !important;border:1px solid #86efac !important;color:#15803d !important}
.status-bar.ng,.status-bar.err,.status-bar.warning{background:#fef2f2 !important;border:1px solid #fca5a5 !important;color:#b91c1c !important}
.recipient-item{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.recipient-item .name{color:#8a8a95 !important}
.note,.help,.hint{color:#4a4a55 !important}
input,select,textarea{background:#ffffff !important;color:#0a0a0f !important;border:1px solid #eaeaef !important}
input:focus,select:focus,textarea:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important}
.tab,.tab-btn{color:#8a8a95 !important}
.tab.active,.tab-btn.active{color:#d64583 !important;background:#fdf0f7 !important;border-color:#d64583 !important}
.tab-body{background:#ffffff !important;border-color:#eaeaef !important}
/* 残ダーク背景の inline / クラスを一掃 */
[style*="#0a1423"],[style*="#0a1624"],[style*="#0a1220"],[style*="#0c1a2e"],[style*="#0c2a3d"],[style*="#1a2438"],[style*="#1c1c2e"],[style*="#0c1d2e"]{background:#ffffff !important;color:#0a0a0f !important;border-color:#eaeaef !important}
[style*="#0ea5e9"]{color:#d64583 !important}
"""


def patch(p: Path) -> bool:
    t = p.read_text(encoding="utf-8")
    if MARKER in t:
        return False
    idx = t.find("</style>")
    if idx < 0:
        return False
    t = t[:idx] + V2 + "\n" + t[idx:]
    p.write_text(t, encoding="utf-8")
    return True


def main():
    files = ["point_mail.py", "data_import.py", "closing.py"]
    roots = [
        Path(r"C:\Users\htosh\OneDrive\デスクトップ\posstart"),
        Path(r"C:\Users\htosh\OneDrive\デスクトップ\POSv2"),
    ]
    for root in roots:
        for f in files:
            p = root / f
            if p.exists():
                print(("[patched] " if patch(p) else "[skip] ") + str(p.name) + " @ " + root.name)


if __name__ == "__main__":
    main()
