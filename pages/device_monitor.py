"""
pages/device_monitor.py — ネットワーク機器 死活監視ページ

機能:
  - 並列ICMPPing / TCPフォールバック / SNMP
  - リトライ判定（fail_threshold回失敗→Offline）
  - 自動ポーリング（30秒/60秒/カスタム間隔）
  - SQLite履歴ログ・稼働率（24h/7d）
  - Slack / Emailアラート通知（初回障害検知 + 復旧通知）
  - 機器グループ管理（フロア/拠点別フィルタ）
  - 機器別タイムアウト・リトライ設定
  - レイテンシ時系列トレンドグラフ
  - 機器リスト CSV/JSON インポート・エクスポート
"""
import time
import io
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.ping import PingChecker
from core.database import Database
from core.notifier import Notifier
from core.validator import validate_device_row


# ─────────────────────────────────────────────────────
# ページ本体
# ─────────────────────────────────────────────────────

def render(config: dict, db: Database, checker: PingChecker, notifier: Notifier):
    st.subheader("🖥️ ネットワーク機器 死活監視")

    # ── セッションステート初期化 ──────────────────────
    ss = st.session_state
    ss.setdefault("polling_active", config.get("polling", {}).get("enabled_on_startup", False))
    ss.setdefault("poll_interval", config.get("polling", {}).get("default_interval_seconds", 60))
    ss.setdefault("last_checked", None)
    ss.setdefault("check_results", pd.DataFrame())
    ss.setdefault("alert_log", [])

    # ─────────────────────────────────────────────────
    # サイドパネル：設定 & コントロール
    # ─────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### ⚙️ 監視設定")

        # 自動ポーリング
        st.markdown("**自動ポーリング**")
        ss.polling_active = st.toggle("自動更新を有効化", value=ss.polling_active)
        ss.poll_interval  = st.select_slider(
            "更新間隔（秒）",
            options=[15, 30, 60, 120, 300],
            value=ss.poll_interval,
        )
        if ss.polling_active:
            st.info(f"🔄 {ss.poll_interval}秒ごとに自動更新中")

        st.divider()

        # グループフィルタ
        st.markdown("**グループフィルタ**")
        all_groups = ["すべて"] + db.get_groups()
        selected_group = st.selectbox("表示グループ", all_groups)

        st.divider()

        # 通知テスト
        st.markdown("**アラート通知**")
        if st.button("🔔 通知テスト送信", use_container_width=True):
            results = notifier.send_test()
            for r in results:
                st.caption(r)

    # ─────────────────────────────────────────────────
    # 機器リスト管理
    # ─────────────────────────────────────────────────
    with st.expander("📋 監視対象機器リスト管理", expanded=False):
        _render_device_manager(db, config)

    # ─────────────────────────────────────────────────
    # 手動Ping実行 & 自動ポーリング
    # ─────────────────────────────────────────────────
    col_btn, col_ts = st.columns([1, 3])
    with col_btn:
        run_now = st.button("🔍 今すぐチェック", type="primary", use_container_width=True)

    if run_now or (ss.polling_active and _should_poll(ss.last_checked, ss.poll_interval)):
        _run_check(db, checker, notifier, config)

    with col_ts:
        if ss.last_checked:
            st.caption(f"🕐 最終チェック: {ss.last_checked}")
        else:
            st.caption("📋 「今すぐチェック」ボタンを押すか、自動更新を有効にしてください。")

    st.divider()

    # ─────────────────────────────────────────────────
    # 稼働サマリーメトリクス
    # ─────────────────────────────────────────────────
    devices_df = db.get_all_devices()
    if selected_group != "すべて":
        devices_df = devices_df[devices_df["grp"] == selected_group]

    data = ss.check_results
    if not data.empty and selected_group != "すべて":
        data = data[data["grp"] == selected_group]

    if not data.empty:
        online_cnt  = (data["status"] == "Online").sum()
        offline_cnt = (data["status"] == "Offline").sum()
        unknown_cnt = (data["status"] == "—").sum()
        avg_lat     = data["latency_ms"].dropna().mean()

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("監視機器", f"{len(data)} 台")
        c2.metric("✅ Online",   f"{online_cnt} 台")
        c3.metric("🔴 Offline",  f"{offline_cnt} 台", delta_color="inverse")
        c4.metric("⏳ 未チェック", f"{unknown_cnt} 台")
        c5.metric("平均レイテンシ", f"{avg_lat:.1f} ms" if pd.notna(avg_lat) else "—")
    else:
        st.info("まだチェック結果がありません。")
        return

    # ─────────────────────────────────────────────────
    # アラートログ表示
    # ─────────────────────────────────────────────────
    if ss.alert_log:
        with st.expander(f"🔔 アラートログ（{len(ss.alert_log)}件）", expanded=False):
            for entry in reversed(ss.alert_log[-20:]):
                icon = "🔴" if entry["type"] == "offline" else "🟢"
                st.markdown(f"{icon} `{entry['time']}` **{entry['host']}** — {entry['message']}")

    # ─────────────────────────────────────────────────
    # メインステータステーブル
    # ─────────────────────────────────────────────────
    _render_status_table(data)

    st.divider()

    # ─────────────────────────────────────────────────
    # 稼働率サマリー
    # ─────────────────────────────────────────────────
    _render_uptime_section(devices_df, db, selected_group)

    st.divider()

    # ─────────────────────────────────────────────────
    # レイテンシ時系列グラフ
    # ─────────────────────────────────────────────────
    _render_latency_trend(devices_df, db, selected_group)

    # ─────────────────────────────────────────────────
    # 自動ポーリング rerun
    # ─────────────────────────────────────────────────
    if ss.polling_active:
        time.sleep(ss.poll_interval)
        st.rerun()


# ─────────────────────────────────────────────────────
# Ping実行
# ─────────────────────────────────────────────────────

def _should_poll(last_checked: str | None, interval: int) -> bool:
    if last_checked is None:
        return False
    try:
        last = datetime.strptime(last_checked, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - last).total_seconds() >= interval
    except Exception:
        return False


def _run_check(db: Database, checker: PingChecker, notifier: Notifier, config: dict):
    ss = st.session_state
    devices = db.get_all_devices()
    if devices.empty:
        st.warning("監視対象機器が登録されていません。")
        return

    with st.spinner(f"⚡ {len(devices)} 台へ並列Pingを送信中..."):
        device_dicts = devices.to_dict("records")
        results = checker.ping_all(device_dicts)

    cooldown = config.get("notifications", {}).get("cooldown_seconds", 300)
    rows = []
    for r in results:
        # DB に記録
        if r.device_id:
            db.record_ping(r.device_id, r.status, r.latency_ms, r.error_type, r.method)

        # アラート判定
        if r.device_id and db.should_alert(r.device_id, r.status, cooldown):
            prev = db.get_alert_state(r.device_id)
            if r.status == "Offline":
                msgs = notifier.notify_offline(r.host, r.ip, r.error_type)
                ss.alert_log.append({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "host": r.host,
                    "type": "offline",
                    "message": f"Offline検知 ({_error_label(r.error_type)})",
                })
            elif r.status == "Online" and prev.get("last_status") == "Offline":
                msgs = notifier.notify_recovery(r.host, r.ip, r.latency_ms)
                ss.alert_log.append({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "host": r.host,
                    "type": "recovery",
                    "message": f"復旧 (レイテンシ: {r.latency_ms} ms)",
                })
            db.update_alert_state(r.device_id, r.status)

        # デバイス情報を付加
        dev_row = devices[devices["id"] == r.device_id].to_dict("records")
        grp = dev_row[0]["grp"] if dev_row else "—"
        rows.append({
            "host":       r.host,
            "ip":         r.ip,
            "grp":        grp,
            "status":     r.status,
            "latency_ms": r.latency_ms,
            "error_type": r.error_type,
            "method":     r.method,
        })

    ss.check_results  = pd.DataFrame(rows)
    ss.last_checked   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────────────
# ステータステーブル
# ─────────────────────────────────────────────────────

def _render_status_table(data: pd.DataFrame):
    display = data.rename(columns={
        "host": "Host", "ip": "IP", "grp": "グループ",
        "status": "Status", "latency_ms": "Latency (ms)",
        "error_type": "エラー種別", "method": "方式",
    })

    def _style_status(val):
        if val == "Offline":
            return "background-color:rgba(239,68,68,0.2);color:#ef4444;font-weight:700"
        if val == "Online":
            return "background-color:rgba(34,197,94,0.15);color:#22c55e"
        return "color:#64748b"

    def _style_latency(val):
        if pd.isna(val) or val is None:
            return ""
        if val < 10:
            return "color:#22c55e"
        if val < 50:
            return "color:#f59e0b"
        return "color:#ef4444"

    styled = display.style.map(_style_status, subset=["Status"])
    if "Latency (ms)" in display.columns:
        styled = styled.map(_style_latency, subset=["Latency (ms)"])

    st.dataframe(styled, use_container_width=True, hide_index=True)

    # CSV エクスポート
    csv = display.to_csv(index=False)
    st.download_button("📥 チェック結果をCSVダウンロード", csv,
                       "check_results.csv", "text/csv")


# ─────────────────────────────────────────────────────
# 稼働率
# ─────────────────────────────────────────────────────

def _render_uptime_section(devices_df: pd.DataFrame, db: Database, selected_group: str):
    st.markdown("#### 📈 稼働率（Uptime）")

    uptime_24h = db.get_uptime_all(hours=24)
    uptime_7d  = db.get_uptime_all(hours=168)

    rows = []
    for _, dev in devices_df.iterrows():
        did = dev["id"]
        u24 = uptime_24h.get(did)
        u7d = uptime_7d.get(did)
        rows.append({
            "Host":      dev["host"],
            "グループ":  dev["grp"],
            "24h稼働率": f"{u24:.1f}%" if u24 is not None else "履歴なし",
            "7d稼働率":  f"{u7d:.1f}%" if u7d is not None else "履歴なし",
        })

    if rows:
        uptime_df = pd.DataFrame(rows)

        def _color_uptime(val):
            if "%" not in str(val):
                return "color:#64748b"
            pct = float(str(val).replace("%", ""))
            if pct >= 99:
                return "color:#22c55e"
            if pct >= 95:
                return "color:#f59e0b"
            return "color:#ef4444"

        styled = uptime_df.style.map(_color_uptime, subset=["24h稼働率", "7d稼働率"])
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.info("稼働率を表示するには、まずPingチェックを実行してください。")


# ─────────────────────────────────────────────────────
# レイテンシトレンドグラフ
# ─────────────────────────────────────────────────────

def _render_latency_trend(devices_df: pd.DataFrame, db: Database, selected_group: str):
    st.markdown("#### 📉 レイテンシ時系列トレンド（直近24時間）")

    hours_sel = st.select_slider("表示時間範囲", options=[1, 3, 6, 12, 24], value=6,
                                 key="trend_hours")
    history = db.get_all_history(hours=hours_sel)

    if history.empty:
        st.info("トレンドデータがありません。Pingチェックを実行すると蓄積されます。")
        return

    if selected_group != "すべて":
        history = history[history["grp"] == selected_group]

    online_hist = history[history["status"] == "Online"].dropna(subset=["latency_ms"])
    if online_hist.empty:
        st.info("オンライン機器のレイテンシ履歴がありません。")
        return

    fig = go.Figure()
    colors = ["#22d3ee","#22c55e","#f59e0b","#a78bfa","#f472b6","#fb923c","#34d399","#60a5fa"]
    for i, (host, grp) in enumerate(online_hist[["host","grp"]].drop_duplicates().values):
        sub = online_hist[online_hist["host"] == host].sort_values("checked_at")
        fig.add_trace(go.Scatter(
            x=sub["checked_at"],
            y=sub["latency_ms"],
            mode="lines+markers",
            name=f"{host} ({grp})",
            line=dict(color=colors[i % len(colors)], width=2),
            marker=dict(size=5),
            hovertemplate=f"<b>{host}</b><br>%{{x}}<br>%{{y:.1f}} ms<extra></extra>",
        ))

    fig.update_layout(
        xaxis_title="時刻",
        yaxis_title="Latency (ms)",
        height=360,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="rgba(15,23,42,0.6)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0"),
        xaxis=dict(gridcolor="rgba(255,255,255,0.07)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.07)"),
        margin=dict(t=20, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

    # 履歴CSVエクスポート
    csv = db.export_history_csv(hours=hours_sel)
    st.download_button(f"📥 直近{hours_sel}時間の履歴をCSVダウンロード",
                       csv, "ping_history.csv", "text/csv")


# ─────────────────────────────────────────────────────
# 機器管理 UI
# ─────────────────────────────────────────────────────

def _render_device_manager(db: Database, config: dict):
    devices_df = db.get_all_devices()

    tab_list, tab_add, tab_import = st.tabs(["📋 一覧・編集", "➕ 機器追加", "📂 CSVインポート"])

    # ── 一覧・削除 ──────────────────────────────────
    with tab_list:
        if devices_df.empty:
            st.info("登録機器がありません。「機器追加」タブから追加してください。")
        else:
            display = devices_df.rename(columns={
                "id": "ID", "host": "Host", "ip": "IP", "type": "タイプ",
                "grp": "グループ", "timeout_sec": "タイムアウト(s)",
                "retry_count": "リトライ回数", "tcp_port": "TCPポート", "notes": "メモ",
            })
            st.dataframe(display, use_container_width=True, hide_index=True)

            del_id = st.number_input("削除する機器ID", min_value=1, step=1, key="del_id")
            if st.button("🗑️ 削除する", key="del_btn", type="secondary"):
                db.delete_device(int(del_id))
                st.success(f"ID {del_id} を削除しました。")
                st.rerun()

    # ── 機器追加 ──────────────────────────────────
    with tab_add:
        with st.form("add_device_form"):
            col1, col2 = st.columns(2)
            new_host    = col1.text_input("Host名 *", placeholder="Router-01")
            new_ip      = col2.text_input("IPアドレス *", placeholder="192.168.1.1")
            col3, col4  = st.columns(2)
            new_type    = col3.selectbox("タイプ", ["Router","Switch","Access Point","Server","Storage","Other"])
            new_group   = col4.text_input("グループ", value="Default", placeholder="Core / 1F など")
            col5, col6, col7 = st.columns(3)
            new_timeout = col5.number_input("タイムアウト(s)", 0.1, 30.0, 1.0, 0.5)
            new_retry   = col6.number_input("リトライ回数",   1,    10,   3,   1)
            new_port    = col7.number_input("TCPポート",      1, 65535, 80,  1)
            new_notes   = st.text_input("メモ（任意）")
            submitted   = st.form_submit_button("✅ 登録する", type="primary")

        if submitted:
            errors = validate_device_row(new_host, new_ip, new_timeout, new_retry)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                db.upsert_device(
                    new_host, new_ip, new_type, new_group,
                    new_timeout, new_retry, new_port, new_notes
                )
                st.success(f"✅ {new_host} ({new_ip}) を登録しました。")
                st.rerun()

    # ── CSV インポート ─────────────────────────────
    with tab_import:
        st.markdown("""
**CSVフォーマット（必須列: `host`, `ip` / 任意: `type`, `group`, `timeout`, `retry`, `notes`）**
```
host,ip,type,group,timeout,retry,notes
Core-Router-01,192.168.1.1,Router,Core,1,3,
AP-1F,192.168.1.50,Access Point,1F,2,3,
```
""")
        csv_file = st.file_uploader("機器リストCSVをアップロード",
                                     type=["csv"], key="device_csv_import")
        if csv_file:
            content = csv_file.read().decode("utf-8-sig", errors="replace")
            added, errors = db.import_devices_csv(content)
            st.success(f"✅ {added} 件の機器をインポートしました。")
            for e in errors:
                st.warning(e)
            if added:
                st.rerun()

        # エクスポート
        st.divider()
        st.markdown("**現在の機器リストをエクスポート**")
        export_df = devices_df.rename(columns={
            "host": "host", "ip": "ip", "type": "type",
            "grp": "group", "timeout_sec": "timeout",
            "retry_count": "retry", "notes": "notes",
        })[["host","ip","type","group","timeout","retry","notes"]]
        st.download_button("📥 機器リストCSVダウンロード",
                           export_df.to_csv(index=False),
                           "devices.csv", "text/csv",
                           use_container_width=True)


# ─────────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────────

def _error_label(error_type: str | None) -> str:
    return {
        "timeout":          "タイムアウト",
        "nxdomain":         "名前解決失敗",
        "permission_denied": "権限エラー",
        "unknown":          "不明",
    }.get(error_type or "unknown", error_type or "不明")
