#!/usr/bin/env bash
# ============================================================
#  POSStart — Ubuntu VPS デプロイスクリプト
#  対象OS: Ubuntu 22.04 LTS / 24.04 LTS
#
#  使い方:
#    # 初回セットアップ
#    bash deploy.sh
#
#    # デモデータも一緒に投入
#    bash deploy.sh --demo
#
#    # アプリだけ最新に更新（git pull + restart）
#    bash deploy.sh --update
# ============================================================
set -euo pipefail

# ─── 設定 ───────────────────────────────────────────────────
REPO="https://github.com/htoshi0614/pos.git"
INSTALL_DIR="/opt/posstart"
SERVICE_NAME="posstart"
APP_USER="posstart"
PORT=8000
PYTHON="python3"
# ────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# ─── 引数処理 ─────────────────────────────────────────────
DEMO_MODE=false
UPDATE_MODE=false
for arg in "$@"; do
  case "$arg" in
    --demo)   DEMO_MODE=true ;;
    --update) UPDATE_MODE=true ;;
  esac
done

# ─── root チェック ───────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  err "このスクリプトはroot（またはsudo）で実行してください"
fi

# ────────────────────────────────────────────────────────────
# UPDATE モード: git pull + サービス再起動のみ
# ────────────────────────────────────────────────────────────
if $UPDATE_MODE; then
  log "アプリを最新版に更新中..."
  cd "$INSTALL_DIR"
  sudo -u "$APP_USER" git pull origin main
  sudo -u "$APP_USER" "$INSTALL_DIR/.venv/bin/pip" install -r requirements.txt -q
  systemctl restart "$SERVICE_NAME"
  log "更新完了！サービスを再起動しました"
  systemctl status "$SERVICE_NAME" --no-pager
  exit 0
fi

# ────────────────────────────────────────────────────────────
# FULL INSTALL
# ────────────────────────────────────────────────────────────
log "==== POSStart セットアップ開始 ===="

# ─── システムパッケージ ─────────────────────────────────────
log "システムパッケージをインストール中..."
apt-get update -qq
apt-get install -y -qq \
  python3 python3-pip python3-venv \
  git nginx ufw curl

# Python バージョン確認
PY_VER=$($PYTHON --version 2>&1 | grep -oP '[\d]+\.[\d]+')
log "Python $PY_VER を使用"

# ─── ユーザー作成 ────────────────────────────────────────────
if ! id "$APP_USER" &>/dev/null; then
  log "システムユーザー '$APP_USER' を作成..."
  useradd -r -m -s /bin/bash "$APP_USER"
fi

# ─── リポジトリのクローン ─────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
  log "既存のリポジトリを更新中..."
  cd "$INSTALL_DIR"
  sudo -u "$APP_USER" git pull origin main
else
  log "リポジトリをクローン中: $REPO"
  rm -rf "$INSTALL_DIR"
  git clone "$REPO" "$INSTALL_DIR"
  chown -R "$APP_USER":"$APP_USER" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ─── Python 仮想環境 ─────────────────────────────────────────
log "仮想環境を構築中..."
sudo -u "$APP_USER" $PYTHON -m venv "$INSTALL_DIR/.venv"
sudo -u "$APP_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$APP_USER" "$INSTALL_DIR/.venv/bin/pip" install -r requirements.txt -q
log "依存パッケージのインストール完了"

# ─── デモデータ投入 ─────────────────────────────────────────
if $DEMO_MODE; then
  log "デモデータを投入中..."
  cd "$INSTALL_DIR"
  sudo -u "$APP_USER" "$INSTALL_DIR/.venv/bin/python" demo_seed.py
fi

# ─── systemd サービス ────────────────────────────────────────
log "systemd サービスを設定中..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=POSStart - Girls Bar POS System
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/uvicorn pos:app --host 127.0.0.1 --port ${PORT} --workers 1
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# 環境変数（必要なら追加）
Environment="PYTHONUNBUFFERED=1"
# Environment="STRIPE_WEBHOOK_SECRET=whsec_xxxx"

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
log "systemd サービス起動完了"

# ─── Nginx リバースプロキシ ──────────────────────────────────
log "Nginx を設定中..."

# サーバーのIPアドレスを自動取得
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

cat > "/etc/nginx/sites-available/${SERVICE_NAME}" <<EOF
server {
    listen 80;
    server_name ${SERVER_IP} _;

    # WebSocket サポート（POSのリアルタイム更新に必要）
    location /ws {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 86400;
    }

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
        client_max_body_size 10m;
    }
}
EOF

# デフォルト設定を無効化
if [[ -f /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi

# 設定を有効化
ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"

# Nginx 設定テスト & 再起動
nginx -t && systemctl reload nginx
log "Nginx 設定完了"

# ─── ファイアウォール ────────────────────────────────────────
log "ファイアウォールを設定中..."
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS（将来のSSL用）
ufw --force enable
log "ufw 設定完了 (22, 80, 443)"

# ─── 完了メッセージ ──────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}🍸 POSStart デプロイ完了！${NC}"
echo ""
echo -e "  📡 アクセスURL    : ${YELLOW}http://${SERVER_IP}/${NC}"
echo -e "  🔑 ログインPW      : ${YELLOW}posstart2024${NC}"
echo -e "  📁 インストール先  : ${INSTALL_DIR}"
echo -e "  🔧 サービス状態    : $(systemctl is-active $SERVICE_NAME)"
echo ""
echo -e "  📋 よく使うコマンド:"
echo -e "    ログ確認  : journalctl -u ${SERVICE_NAME} -f"
echo -e "    再起動    : systemctl restart ${SERVICE_NAME}"
echo -e "    更新      : bash ${INSTALL_DIR}/deploy.sh --update"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
