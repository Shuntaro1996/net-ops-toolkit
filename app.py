import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.interpolate import griddata

st.set_page_config(page_title="Network Ops Toolkit", layout="wide")

st.title("🌐 Network Infrastructure & Wi-Fi RSSI Dashboard")
st.caption("Wi-Fi電波状況の可視化およびネットワーク機器の死活監視デモツール")

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

# --- Tab 2: 機器死活監視 ---
with tab2:
    st.subheader("ネットワーク機器 稼働ステータス")
    
    device_data = pd.DataFrame([
        {"Host": "Core-Router-01", "IP": "192.168.1.1", "Type": "Router", "Status": "Online", "Latency (ms)": 1.2},
        {"Host": "PoE-Switch-A", "IP": "192.168.1.10", "Type": "Switch", "Status": "Online", "Latency (ms)": 2.4},
        {"Host": "PoE-Switch-B", "IP": "192.168.1.11", "Type": "Switch", "Status": "Online", "Latency (ms)": 2.8},
        {"Host": "AP-Floor-1F", "IP": "192.168.1.50", "Type": "Access Point", "Status": "Online", "Latency (ms)": 5.1},
        {"Host": "AP-Floor-2F", "IP": "192.168.1.51", "Type": "Access Point", "Status": "Offline", "Latency (ms)": None},
        {"Host": "NAS-Storage", "IP": "192.168.1.100", "Type": "Storage", "Status": "Online", "Latency (ms)": 3.6},
    ])

    col1, col2, col3 = st.columns(3)
    col1.metric("監視対象機器", f"{len(device_data)} 台")
    col2.metric("稼働中 (Online)", f"{(device_data['Status'] == 'Online').sum()} 台")
    col3.metric("異常検知 (Offline)", f"{(device_data['Status'] == 'Offline').sum()} 台", delta_color="inverse")

    st.dataframe(
        device_data.style.map(
            lambda v: "background-color: #ffcccc; color: #cc0000; font-weight: bold;" if v == "Offline" 
            else ("background-color: #ccffcc; color: #008800;" if v == "Online" else ""),
            subset=["Status"]
        ),
        use_container_width=True
    )
