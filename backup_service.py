"""backup_service.py — バックアップ自動化
SQLiteのDBファイルを定期的にローカル or クラウドに自動バックアップ
"""

import shutil, os, threading, time
from datetime import datetime, date
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from db_shared import require_role

router = APIRouter(tags=["backup"])
ADMIN_ROLES = ["owner", "manager"]

BACKUP_DIR = Path("./backups")
DB_PATH = Path("./pos.db")
MAX_BACKUPS = 30  # 最大保持数

# バックグラウンドタスク用
_auto_backup_running = False
_auto_backup_interval = 3600  # 1時間ごと（秒）

def _ensure_backup_dir():
    BACKUP_DIR.mkdir(exist_ok=True)

def _create_backup(label: str = "") -> str:
    _ensure_backup_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"pos_backup_{ts}"
    if label:
        name += f"_{label}"
    name += ".db"
    dest = BACKUP_DIR / name
    shutil.copy2(str(DB_PATH), str(dest))
    _cleanup_old_backups()
    return name

def _cleanup_old_backups():
    files = sorted(BACKUP_DIR.glob("pos_backup_*.db"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[MAX_BACKUPS:]:
        f.unlink(missing_ok=True)

def _list_backups():
    _ensure_backup_dir()
    files = sorted(BACKUP_DIR.glob("pos_backup_*.db"), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        stat = f.stat()
        result.append({
            "name": f.name,
            "size_mb": round(stat.st_size / (1024*1024), 2),
            "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return result

def _auto_backup_loop():
    global _auto_backup_running
    # 起動直後に1回バックアップ
    time.sleep(10)
    try:
        name = _create_backup("startup")
        print(f"[backup] 起動時バックアップ完了: {name}")
    except Exception as e:
        print(f"[backup] 起動時バックアップ失敗: {e}")
    # 以降は定期実行
    while _auto_backup_running:
        time.sleep(_auto_backup_interval)
        try:
            name = _create_backup("auto")
            print(f"[backup] 自動バックアップ完了: {name}")
        except Exception as e:
            print(f"[backup] auto-backup error: {e}")

def start_auto_backup_on_startup(interval_minutes: int = 360):
    """サーバー起動時に自動バックアップを開始する（pos.py の startup イベントから呼ぶ）"""
    global _auto_backup_running, _auto_backup_interval
    if _auto_backup_running:
        return  # すでに起動中
    _auto_backup_interval = max(10, interval_minutes) * 60
    _auto_backup_running = True
    t = threading.Thread(target=_auto_backup_loop, daemon=True)
    t.start()

# ---------- API ----------
@router.post("/backup/create")
def create_backup(label: str = "", x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    name = _create_backup(label or "manual")
    return {"ok": True, "name": name}

@router.get("/backup/list")
def list_backups(x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    return _list_backups()

@router.get("/backup/download/{name}")
def download_backup(name: str, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    path = BACKUP_DIR / name
    if not path.exists() or not path.name.startswith("pos_backup_"):
        raise HTTPException(404, "Backup not found")
    return FileResponse(str(path), filename=name, media_type="application/octet-stream")

@router.delete("/backup/{name}")
def delete_backup(name: str, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    path = BACKUP_DIR / name
    if not path.exists() or not path.name.startswith("pos_backup_"):
        raise HTTPException(404, "Backup not found")
    path.unlink()
    return {"ok": True}

@router.post("/backup/restore/{name}")
def restore_backup(name: str, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner"])  # オーナーのみ
    path = BACKUP_DIR / name
    if not path.exists() or not path.name.startswith("pos_backup_"):
        raise HTTPException(404, "Backup not found")
    # 復元前に現在のDBをバックアップ
    _create_backup("pre_restore")
    shutil.copy2(str(path), str(DB_PATH))
    return {"ok": True, "message": "復元完了。サーバーを再起動してください。"}

@router.post("/backup/auto/start")
def start_auto_backup(interval_minutes: int = 60, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    global _auto_backup_running, _auto_backup_interval
    if _auto_backup_running:
        return {"ok": True, "message": "Already running"}
    _auto_backup_interval = max(10, interval_minutes) * 60
    _auto_backup_running = True
    t = threading.Thread(target=_auto_backup_loop, daemon=True)
    t.start()
    return {"ok": True, "interval_minutes": interval_minutes}

@router.post("/backup/auto/stop")
def stop_auto_backup(x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    global _auto_backup_running
    _auto_backup_running = False
    return {"ok": True}

@router.get("/backup/auto/status")
def auto_backup_status(x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    return {"running": _auto_backup_running, "interval_minutes": _auto_backup_interval // 60}

# ---------- UI ----------
@router.get("/ui/backup", response_class=HTMLResponse)
def ui_backup():
    return HTMLResponse("""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>バックアップ管理</title>
<style>
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#b0bec5;--accent:#0ea5e9}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text);padding:20px}
h1{font-size:22px;margin-bottom:16px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
.btn{cursor:pointer;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:#111827;color:var(--text);font-size:14px}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018}
.btn.danger{background:#7f1d1d;border-color:#ef4444;color:#fca5a5}
.btn.green{background:#14532d;border-color:#22c55e;color:#4ade80}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
th{background:#111827;font-size:12px;color:var(--muted)}
.auto-status{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px;display:flex;gap:16px;align-items:center}
.badge{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px}
.badge.on{background:#14532d;color:#4ade80}
.badge.off{background:#1e293b;color:var(--muted)}
a{color:var(--accent)}
input{font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text)}
</style></head><body>
<h1>💾 バックアップ管理</h1>
<div class="auto-status">
  <span>状態: <span id="autoStatus" class="badge off">確認中...</span></span>
  <span style="color:var(--muted);font-size:13px">保存件数: <strong id="countBadge">—</strong></span>
  <input id="interval" type="number" value="360" style="width:72px" placeholder="分">
  <span style="font-size:12px;color:var(--muted)">分間隔</span>
  <button class="btn green" onclick="startAuto()">自動ON</button>
  <button class="btn" onclick="stopAuto()">自動OFF</button>
  <a href="/ui" style="margin-left:auto;font-size:13px">← POS に戻る</a>
</div>
<div class="toolbar">
  <button class="btn solid" onclick="manualBackup()">今すぐバックアップ</button>
  <span style="font-size:12px;color:var(--muted)">※ backups/ フォルダに保存、最大30件保持</span>
</div>
<table>
<thead><tr><th>ファイル名</th><th>サイズ</th><th>作成日時</th><th>操作</th></tr></thead>
<tbody id="list"></tbody>
</table>

<script>
const H=()=>({'Content-Type':'application/json','X-Role':'owner','X-Token':sessionStorage.getItem('pos_token')||''});
async function apiFetch(path,opt={}){
  const r=await fetch(path,{headers:H(),...opt});
  if(r.status===401){sessionStorage.clear();location.href='/';return null;}
  return r;
}
async function load(){
  const r=await apiFetch('/backup/list'); if(!r)return;
  const data=await r.json();
  const count=data.length;
  document.getElementById('countBadge').textContent=count+'件';
  document.getElementById('list').innerHTML=count===0
    ?'<tr><td colspan="4" style="text-align:center;color:#64748b;padding:20px">バックアップがありません</td></tr>'
    :data.map(b=>`<tr>
    <td style="font-size:12px;font-family:monospace">${b.name}</td><td>${b.size_mb} MB</td><td>${b.created}</td>
    <td style="display:flex;gap:6px;flex-wrap:wrap">
      <a href="/backup/download/${b.name}" class="btn" style="font-size:12px;padding:4px 8px;text-decoration:none;background:#14532d;border-color:#22c55e;color:#4ade80">⬇ DL</a>
      <button class="btn" style="font-size:12px;padding:4px 8px" onclick="restore('${b.name}')">↩ 復元</button>
      <button class="btn danger" style="font-size:12px;padding:4px 8px" onclick="del('${b.name}')">削除</button>
    </td></tr>`).join('');
}
async function loadStatus(){
  const r=await apiFetch('/backup/auto/status'); if(!r)return;
  const d=await r.json();
  const el=document.getElementById('autoStatus');
  el.textContent=d.running?`自動ON (${d.interval_minutes}分間隔)`:'自動OFF';
  el.className='badge '+(d.running?'on':'off');
  if(d.running) document.getElementById('interval').value=d.interval_minutes;
}
async function manualBackup(){
  const btn=document.querySelector('.btn.solid');
  btn.disabled=true; btn.textContent='バックアップ中...';
  try{
    const r=await apiFetch('/backup/create',{method:'POST'}); if(!r)return;
    const d=await r.json();
    if(d.ok) alert('✅ バックアップ完了: '+d.name);
    load();
  }finally{btn.disabled=false;btn.textContent='今すぐバックアップ';}
}
async function startAuto(){
  const m=parseInt(document.getElementById('interval').value)||360;
  await apiFetch(`/backup/auto/start?interval_minutes=${m}`,{method:'POST'}); loadStatus();
}
async function stopAuto(){
  await apiFetch('/backup/auto/stop',{method:'POST'}); loadStatus();
}
async function restore(name){
  if(!confirm('【'+name+'】\nこのバックアップからDBを復元しますか？\n※現在のデータは事前にバックアップされます。'))return;
  const r=await apiFetch(`/backup/restore/${name}`,{method:'POST'}); if(!r)return;
  const d=await r.json();
  alert(d.message||'復元完了。サーバーを再起動してください。'); load();
}
async function del(name){
  if(!confirm(name+' を削除しますか？'))return;
  await apiFetch(`/backup/${name}`,{method:'DELETE'}); load();
}
load(); loadStatus();
</script></body></html>""")
