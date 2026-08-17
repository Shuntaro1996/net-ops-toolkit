"""
app.py — net-ops-toolkit メインエントリポイント

機能:
  - パスワード認証（config.yaml: auth.password）
  - カスタムCSS読み込み（assets/style.css）
  - 設定ファイル読み込み（config.yaml）
  - DB・Checker・Notifier の初期化
  - タブルーティング（Wi-Fi Heatmap / Device Monitor）
"""
import os
import yaml
import streamlit as st

from core.database import Database
from core.ping import PingChecker
from core.notifier import Notifier
import pages.wifi_heatmap as wifi_page
import pages.device_monitor as monitor_page

# ─────────────────────────────────────────────────────
# ページ基本設定
# ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="Network Ops Toolkit",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─────────────────────────────────────────────────────
# カスタムCSS 読み込み
# ─────────────────────────────────────────────────────
def _load_css():
    css_path = os.path.join(os.path.dirname(__file__), "assets", "style.css")
    if os.path.exists(css_path):
        with open(css_path, encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

_load_css()


# ─────────────────────────────────────────────────────
# 設定ファイル読み込み
# ─────────────────────────────────────────────────────
@st.cache_resource
def _load_config() -> dict:
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

config = _load_config()


# ─────────────────────────────────────────────────────
# コアリソースの初期化（シングルトン）
# ─────────────────────────────────────────────────────
@st.cache_resource
def _init_resources(config: dict):
    db_path = config.get("database", {}).get("path", "netops.db")
    db      = Database(db_path)
    # デフォルト機器を初回登録
    defaults = config.get("default_devices", [])
    if defaults:
        db.seed_defaults(defaults)
    checker  = PingChecker(config)
    notifier = Notifier(config)
    return db, checker, notifier

db, checker, notifier = _init_resources(config)


# ─────────────────────────────────────────────────────
# パスワード認証
# ─────────────────────────────────────────────────────
def _check_auth() -> bool:
    auth_cfg  = config.get("auth", {})
    if not auth_cfg.get("enabled", False):
        return True

    if st.session_state.get("authenticated"):
        return True

    # ログイン画面
    st.markdown(
        '<div class="login-container">'
        '<div class="login-title">🌐 Network Ops Toolkit</div>',
        unsafe_allow_html=True,
    )
    password = st.text_input("パスワード", type="password", key="login_pw",
                              placeholder="パスワードを入力してください")
    if st.button("ログイン", type="primary", use_container_width=True):
        if password == auth_cfg.get("password", ""):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ パスワードが違います")
    st.markdown("</div>", unsafe_allow_html=True)
    return False


# ─────────────────────────────────────────────────────
# メインレイアウト
# ─────────────────────────────────────────────────────
if not _check_auth():
    st.stop()

# ヘッダー
col_title, col_logout = st.columns([6, 1])
with col_title:
    st.title("🌐 Network Infrastructure & Wi-Fi RSSI Dashboard")
    st.caption("Wi-Fi電波状況の可視化 ／ ネットワーク機器の死活監視・アラート通知ツール")
with col_logout:
    if config.get("auth", {}).get("enabled", False):
        if st.button("🔓 ログアウト", use_container_width=True):
            st.session_state.authenticated = False
            st.rerun()

# タブ
tab1, tab2 = st.tabs(["📶 Wi-Fi RSSI Heatmap", "🖥️ Device Status Monitor"])

with tab1:
    wifi_page.render()

with tab2:
    monitor_page.render(config, db, checker, notifier)
