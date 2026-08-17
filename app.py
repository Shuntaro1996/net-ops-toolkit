import subprocess
import platform
import time
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.interpolate import griddata

st.set_page_config(page_title="Network Ops Toolkit", layout="wide")

st.title("🌐 Network Infrastructure & Wi-Fi RSSI Dashboard")
st.caption("Wi-Fi電波状況の可視化およびネットワーク機器の死活監視ツール")

tab1, tab2 = st.tabs(["📶 Wi-Fi RSSI Heatmap", "🖥️ Device Status Monitor"])

# --- Tab 1: Wi-Fi RSSI ヒートマップ ---
with tab1:
    st.subheader("Wi-Fi電波強度（RSSI）ヒートマップ解析")
    st.write("フロア内の測定地点とRSSI値（dBm）から、空間全体の電波状況を補間マッピングします。")

    default_data = pd.DataFrame({
        "X座標 (m)": [2, 4, 8, 12, 16, 18, 5, 10, 15, 3, 14],
        "Y座標 (m)": [3, 8, 2, 7, 3, 9, 12, 14, 11, 16, 17],
        "RSSI (dBm)": [-42, -55, -48, -72, -80, -85, -60, -68, -78, -65, -82]
    })

    col_data, col_plot = st.columns([1, 2])
    
    with col_data:
        st.markdown("**測定ポイント一覧**")
        df = st.data_editor(default_data, num_rows="dynamic")
        st.info("💡 表の数値を直接編集・追加すると、右側のマップが即座に更新されます。")

    with col_plot:
        grid_x, grid_y = np.mgrid[0:20:100j, 0:20:100j]
        grid_z = griddata(
            (df["X座標 (m)"], df["Y座標 (m)"]),
            df["RSSI (dBm)"],
            (grid_x, grid_y),
            method="linear",
            fill_value=-90
        )

        fig = go.Figure()
        fig.add_trace(go.Contour(
            z=grid_z.T,
            x=np.linspace(0, 20, 100),
            y=np.linspace(0, 20, 100),
            colorscale="Viridis",
            colorbar=dict(title="RSSI (dBm)"),
            contours=dict(showlabels=True)
        ))
        fig.add_trace(go.Scatter(
            x=df["X座標 (m)"],
            y=df["Y座標 (m)"],
            mode="markers+text",
            text=[f"{v}dBm" for v in df["RSSI (dBm)"]],
            textposition="top center",
            marker=dict(size=10, color="red", symbol="x"),
            name="Measurement Point"
        ))

        fig.update_layout(
            title="Floor RSSI Heatmap (20m x 20m)",
            xaxis_title="Width (m)",
            yaxis_title="Depth (m)",
            height=500
        )
        st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# Ping ユーティリティ関数
# =============================================================================

def ping_host(ip: str, count: int = 3, timeout: int = 1) -> dict:
    """
    指定したIPアドレスへICMP Pingを送信し、稼働状態と平均レイテンシを返す。

    Args:
        ip      : 対象IPアドレス
        count   : Pingの送信回数
        timeout : タイムアウト（秒）

    Returns:
        {"status": "Online"|"Offline", "latency_ms": float|None}
    """
    os_name = platform.system().lower()

    if os_name == "windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout * 1000), ip]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout), ip]

    try:
        start = time.time()
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout * count + 2
        )
        elapsed_ms = (time.time() - start) / count * 1000

        if result.returncode == 0:
            # Windows: "平均 = Xms" / "Average = Xms", Linux: "rtt min/avg/max ..."
            output = result.stdout.decode(errors="ignore")
            latency = _parse_latency(output, os_name, elapsed_ms)
            return {"status": "Online", "latency_ms": round(latency, 2)}
        else:
            return {"status": "Offline", "latency_ms": None}
    except Exception:
        return {"status": "Offline", "latency_ms": None}


def _parse_latency(output: str, os_name: str, fallback_ms: float) -> float:
    """Ping出力から平均レイテンシ（ms）を解析する。"""
    import re
    if os_name == "windows":
        # 日本語環境: "平均 = 3ms" / 英語環境: "Average = 3ms"
        m = re.search(r"(?:Average|平均)\s*=\s*(\d+)ms", output, re.IGNORECASE)
        if m:
            return float(m.group(1))
    else:
        # Linux/Mac: "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.100 ms"
        m = re.search(r"[\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms", output)
        if m:
            return float(m.group(1))
    return fallback_ms


def run_ping_check(device_df: pd.DataFrame) -> pd.DataFrame:
    """全機器へPingを実行し、Status と Latency を更新したDataFrameを返す。"""
    results = []
    for _, row in device_df.iterrows():
        res = ping_host(row["IP"])
        results.append({
            **row.to_dict(),
            "Status": res["status"],
            "Latency (ms)": res["latency_ms"],
        })
    return pd.DataFrame(results)


# =============================================================================
# Tab 2: 機器死活監視
# =============================================================================

# デフォルト機器リスト（セッションステートで保持）
_DEFAULT_DEVICES = [
    {"Host": "Core-Router-01", "IP": "192.168.1.1",   "Type": "Router",       "Status": "—", "Latency (ms)": None},
    {"Host": "PoE-Switch-A",   "IP": "192.168.1.10",  "Type": "Switch",       "Status": "—", "Latency (ms)": None},
    {"Host": "PoE-Switch-B",   "IP": "192.168.1.11",  "Type": "Switch",       "Status": "—", "Latency (ms)": None},
    {"Host": "AP-Floor-1F",    "IP": "192.168.1.50",  "Type": "Access Point", "Status": "—", "Latency (ms)": None},
    {"Host": "AP-Floor-2F",    "IP": "192.168.1.51",  "Type": "Access Point", "Status": "—", "Latency (ms)": None},
    {"Host": "NAS-Storage",    "IP": "192.168.1.100", "Type": "Storage",      "Status": "—", "Latency (ms)": None},
]

if "device_data" not in st.session_state:
    st.session_state.device_data = pd.DataFrame(_DEFAULT_DEVICES)

if "last_checked" not in st.session_state:
    st.session_state.last_checked = None

with tab2:
    st.subheader("ネットワーク機器 稼働ステータス")

    # --- 機器リスト編集 & Ping実行ボタン ---
    with st.expander("⚙️ 監視対象機器リストを編集する", expanded=False):
        edited_df = st.data_editor(
            st.session_state.device_data[["Host", "IP", "Type"]],
            num_rows="dynamic",
            use_container_width=True,
            key="device_editor"
        )
        # 編集内容を保存（Status/Latencyはそのまま引き継ぐ）
        if not edited_df[["Host", "IP", "Type"]].equals(
            st.session_state.device_data[["Host", "IP", "Type"]]
        ):
            merged = edited_df.copy()
            merged["Status"] = "—"
            merged["Latency (ms)"] = None
            st.session_state.device_data = merged
            st.session_state.last_checked = None

    # --- Ping実行 ---
    col_btn, col_status = st.columns([1, 3])
    with col_btn:
        run_ping = st.button("🔍 Ping チェックを実行", type="primary", use_container_width=True)

    if run_ping:
        with st.spinner("各機器へPingを送信中..."):
            updated = run_ping_check(st.session_state.device_data)
            st.session_state.device_data = updated
            st.session_state.last_checked = time.strftime("%Y-%m-%d %H:%M:%S")

    with col_status:
        if st.session_state.last_checked:
            st.caption(f"🕐 最終チェック日時: {st.session_state.last_checked}")
        else:
            st.caption("📋 「Ping チェックを実行」ボタンを押すと実機へICMP Pingを送信します。")

    st.divider()

    # --- サマリーメトリクス ---
    data = st.session_state.device_data
    online_count  = (data["Status"] == "Online").sum()
    offline_count = (data["Status"] == "Offline").sum()
    unknown_count = (data["Status"] == "—").sum()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("監視対象機器", f"{len(data)} 台")
    col2.metric("✅ 稼働中 (Online)",  f"{online_count} 台")
    col3.metric("🔴 異常検知 (Offline)", f"{offline_count} 台", delta_color="inverse")
    col4.metric("⏳ 未チェック", f"{unknown_count} 台")

    # --- ステータステーブル ---
    def _style_status(val):
        if val == "Offline":
            return "background-color: #ffcccc; color: #cc0000; font-weight: bold;"
        elif val == "Online":
            return "background-color: #ccffcc; color: #008800;"
        return "color: #888888;"

    st.dataframe(
        data.style.map(_style_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )

    # --- Ping結果グラフ（Online機器のレイテンシ棒グラフ） ---
    online_df = data[data["Status"] == "Online"].dropna(subset=["Latency (ms)"])
    if not online_df.empty:
        st.markdown("#### 📊 稼働中機器のレイテンシ比較")
        fig_bar = go.Figure(go.Bar(
            x=online_df["Host"],
            y=online_df["Latency (ms)"],
            marker_color=[
                "#22c55e" if v < 10 else "#f59e0b" if v < 50 else "#ef4444"
                for v in online_df["Latency (ms)"]
            ],
            text=[f"{v} ms" for v in online_df["Latency (ms)"]],
            textposition="outside",
        ))
        fig_bar.update_layout(
            xaxis_title="Host",
            yaxis_title="Latency (ms)",
            height=300,
            margin=dict(t=20, b=20),
            plot_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor="rgba(0,0,0,0.1)"),
        )
        st.plotly_chart(fig_bar, use_container_width=True)
