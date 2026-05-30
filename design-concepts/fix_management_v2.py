# 分析画面 v2: 視認性修正 + 重要データ強調（KPI/テーブル/タブの再設計）
from pathlib import Path

MARKER = "分析画面 v2"

V2 = """
/* === 分析画面 v2: 視認性 + 重要データ強調 === */
.container{max-width:1120px}
/* KPIカード: 白ベース + 上辺アクセントバー + 大型数値 */
.kpi-grid{gap:14px;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));margin-bottom:18px}
.kpi{background:#ffffff !important;border:1px solid #eaeaef !important;border-radius:14px;padding:18px 20px;position:relative;overflow:hidden;box-shadow:0 1px 3px rgba(10,10,15,.04),0 2px 8px rgba(10,10,15,.05);transition:transform .2s,box-shadow .2s}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#d64583,#c9a96e)}
.kpi:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(214,69,131,.1)}
.kpi .label{font-size:11px;color:#8a8a95 !important;font-weight:700;letter-spacing:.04em;margin-bottom:6px}
.kpi .value{font-family:'Inter',sans-serif;font-size:28px !important;font-weight:800;color:#0a0a0f;letter-spacing:-.01em;line-height:1.15}
.kpi .sub{font-size:11px;color:#8a8a95 !important;margin-top:2px}
/* 各タブ先頭KPI(=最重要指標)を強調 */
.kpi-grid .kpi:first-child{background:linear-gradient(135deg,#fff 0%,#fdf0f7 100%) !important;border-color:#d64583 !important;box-shadow:0 4px 16px rgba(214,69,131,.1)}
.kpi-grid .kpi:first-child .value{font-size:34px !important;background:linear-gradient(135deg,#0a0a0f,#d64583);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
/* テーブル行ホバー: 黒→薄ピンク */
tr:hover td{background:#fdf0f7 !important}
/* バー / ゴール トラックをライト化 */
.bar-wrap{background:#f3f3f6 !important}
.bar-label{color:#0a0a0f !important;font-weight:600}
.bar-value{color:#0a0a0f !important}
.goal-bar{background:#f3f3f6 !important}
.goal-text{color:#0a0a0f !important}
/* ランキング上位行の淡い色付け */
tr.gold-row td{background:linear-gradient(90deg,#fffaf0,transparent) !important}
tr.silver-row td{background:linear-gradient(90deg,#f6f7f9,transparent) !important}
tr.bronze-row td{background:linear-gradient(90deg,#fdf6f0,transparent) !important}
.rank-1{background:#c9a96e !important;color:#fff !important}
.rank-2{background:#c0c0c0 !important;color:#0a0a0f !important}
.rank-3{background:#cd7f32 !important;color:#fff !important}
.rank-other{background:#f3f3f6 !important;color:#8a8a95 !important}
/* セクションタブ: アクティブ明確化 */
.section-tabs{border-bottom:2px solid #eaeaef;gap:4px}
.section-tab{background:#ffffff !important;border:1px solid #eaeaef !important;border-bottom:none !important;color:#8a8a95 !important;font-weight:600;padding:11px 20px;transition:all .2s}
.section-tab:hover{color:#d64583 !important;background:#fdf0f7 !important}
.section-tab.active{background:#d64583 !important;color:#fff !important;border-color:#d64583 !important;box-shadow:0 -2px 10px rgba(214,69,131,.18)}
/* カード/見出しの階層強化 */
.card{box-shadow:0 1px 3px rgba(10,10,15,.04),0 2px 8px rgba(10,10,15,.05);border-radius:16px}
.card h2{font-size:15px;font-weight:800;color:#0a0a0f;letter-spacing:-.01em}
th{color:#8a8a95 !important;font-weight:700 !important;text-transform:none}
.pie-dot{box-shadow:0 1px 3px rgba(0,0,0,.1)}
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
    for root in [r"C:\Users\htosh\OneDrive\デスクトップ\posstart",
                 r"C:\Users\htosh\OneDrive\デスクトップ\POSv2"]:
        p = Path(root) / "management.py"
        if p.exists():
            print(("[patched] " if patch(p) else "[skip] ") + root.split("\\")[-1])


if __name__ == "__main__":
    main()
