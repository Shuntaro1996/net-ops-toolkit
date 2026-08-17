"""
pages/wifi_heatmap.py — Wi-Fi RSSI ヒートマップページ

機能:
  - 測定ポイントの編集（st.data_editor）
  - 空間補間ヒートマップ（SciPy + Plotly Contour）
  - フロア見取り図画像のアップロードとRSSI重畳表示
  - 測定データのCSVエクスポート / インポート
  - RSSIスコアサマリー（平均・最小・不感地帯割合）
"""
import io
import base64

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy.interpolate import griddata


# ─────────────────────────────────────────────────────
# デフォルト測定データ
# ─────────────────────────────────────────────────────

DEFAULT_RSSI = pd.DataFrame({
    "X座標 (m)": [2,  4,  8, 12, 16, 18,  5, 10, 15,  3, 14],
    "Y座標 (m)": [3,  8,  2,  7,  3,  9, 12, 14, 11, 16, 17],
    "RSSI (dBm)":[-42,-55,-48,-72,-80,-85,-60,-68,-78,-65,-82],
    "測定点名":  ["P1","P2","P3","P4","P5","P6","P7","P8","P9","P10","P11"],
})

RSSI_THRESHOLDS = {
    "優良 (>= -60 dBm)":   (-60,  0,   "#22c55e"),
    "良好 (-70〜-60 dBm)": (-70, -60,  "#84cc16"),
    "普通 (-80〜-70 dBm)": (-80, -70,  "#f59e0b"),
    "弱い (-90〜-80 dBm)": (-90, -80,  "#ef4444"),
}


# ─────────────────────────────────────────────────────
# ページ本体
# ─────────────────────────────────────────────────────

def render():
    st.subheader("📶 Wi-Fi電波強度（RSSI）ヒートマップ解析")
    st.caption("フロア内の測定ポイントとRSSI値から、空間全体の電波分布を補間マッピングします。")

    # セッションステートで測定データを保持
    if "rssi_df" not in st.session_state:
        st.session_state.rssi_df = DEFAULT_RSSI.copy()
    if "floor_image" not in st.session_state:
        st.session_state.floor_image = None
    if "floor_size" not in st.session_state:
        st.session_state.floor_size = (20.0, 20.0)  # (width_m, depth_m)

    # ── フロアサイズ設定 ──────────────────────────────
    with st.expander("⚙️ フロア設定・画像アップロード", expanded=False):
        col_w, col_d = st.columns(2)
        floor_w = col_w.number_input("フロア幅 (m)", min_value=1.0, max_value=500.0,
                                     value=st.session_state.floor_size[0], step=1.0)
        floor_d = col_d.number_input("フロア奥行 (m)", min_value=1.0, max_value=500.0,
                                     value=st.session_state.floor_size[1], step=1.0)
        st.session_state.floor_size = (floor_w, floor_d)

        uploaded_img = st.file_uploader(
            "フロア見取り図をアップロード（PNG/JPG）",
            type=["png", "jpg", "jpeg"],
            help="画像の左下を原点(0,0)として扱います。"
        )
        if uploaded_img:
            st.session_state.floor_image = uploaded_img.read()
            st.success("✅ フロア画像を読み込みました。")
        if st.session_state.floor_image and st.button("🗑️ フロア画像を削除"):
            st.session_state.floor_image = None

    # ── CSV インポート ────────────────────────────────
    with st.expander("📂 CSVインポート / エクスポート", expanded=False):
        col_imp, col_exp = st.columns(2)

        with col_imp:
            st.markdown("**インポート**")
            csv_file = st.file_uploader("測定データCSVをアップロード",
                                         type=["csv"], key="rssi_csv_import")
            if csv_file:
                try:
                    imported = pd.read_csv(csv_file)
                    required_cols = {"X座標 (m)", "Y座標 (m)", "RSSI (dBm)"}
                    if required_cols.issubset(set(imported.columns)):
                        if "測定点名" not in imported.columns:
                            imported["測定点名"] = [f"P{i+1}" for i in range(len(imported))]
                        st.session_state.rssi_df = imported[list(DEFAULT_RSSI.columns)]
                        st.success(f"✅ {len(imported)} 件のデータを読み込みました。")
                    else:
                        st.error(f"❌ 必須列が不足しています: {required_cols - set(imported.columns)}")
                except Exception as e:
                    st.error(f"読み込みエラー: {e}")

        with col_exp:
            st.markdown("**エクスポート**")
            csv_str = st.session_state.rssi_df.to_csv(index=False)
            st.download_button(
                label="📥 CSVダウンロード",
                data=csv_str,
                file_name="rssi_measurements.csv",
                mime="text/csv",
                use_container_width=True,
            )
            # JSON エクスポート
            json_str = st.session_state.rssi_df.to_json(orient="records", force_ascii=False, indent=2)
            st.download_button(
                label="📥 JSONダウンロード",
                data=json_str,
                file_name="rssi_measurements.json",
                mime="application/json",
                use_container_width=True,
            )

    st.divider()

    # ── データ編集 & ヒートマップ ─────────────────────
    col_data, col_plot = st.columns([1, 2])

    with col_data:
        st.markdown("**📍 測定ポイント一覧**")
        df = st.data_editor(
            st.session_state.rssi_df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "RSSI (dBm)": st.column_config.NumberColumn(
                    "RSSI (dBm)", min_value=-100, max_value=0, step=1
                ),
            },
            key="rssi_editor",
        )
        st.session_state.rssi_df = df
        st.info("💡 数値を直接編集すると右のマップが即座に更新されます。")

        # ── RSSIサマリー ──
        st.markdown("**📊 電波品質サマリー**")
        rssi_vals = df["RSSI (dBm)"].dropna()
        if len(rssi_vals) > 0:
            avg_rssi = rssi_vals.mean()
            min_rssi = rssi_vals.min()
            weak_pct = (rssi_vals < -75).sum() / len(rssi_vals) * 100

            m1, m2, m3 = st.columns(3)
            m1.metric("平均", f"{avg_rssi:.1f} dBm")
            m2.metric("最弱点", f"{min_rssi:.0f} dBm")
            m3.metric("弱電界 (<-75)", f"{weak_pct:.0f}%")

            # 品質評価
            if avg_rssi >= -60:
                st.success("🟢 全体的に電波状況は良好です")
            elif avg_rssi >= -70:
                st.warning("🟡 一部エリアで電波が弱い可能性があります")
            else:
                st.error("🔴 複数エリアでAPの追加設置を検討してください")

    with col_plot:
        _render_heatmap(df, st.session_state.floor_size, st.session_state.floor_image)

    # ── RSSI凡例 ──────────────────────────────────────
    st.divider()
    st.markdown("**📘 RSSI強度レベル凡例**")
    cols = st.columns(len(RSSI_THRESHOLDS))
    for col, (label, (lo, hi, color)) in zip(cols, RSSI_THRESHOLDS.items()):
        col.markdown(
            f'<div style="background:{color}22;border:1px solid {color};border-radius:8px;'
            f'padding:8px;text-align:center;font-size:0.8rem">'
            f'<b style="color:{color}">{label}</b></div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────
# ヒートマップ描画ヘルパー
# ─────────────────────────────────────────────────────

def _render_heatmap(df: pd.DataFrame, floor_size: tuple, floor_image: bytes | None):
    fw, fd = floor_size
    valid = df.dropna(subset=["X座標 (m)", "Y座標 (m)", "RSSI (dBm)"])

    if len(valid) < 3:
        st.warning("⚠️ ヒートマップの描画には測定ポイントが3点以上必要です。")
        return

    grid_x, grid_y = np.mgrid[0:fw:100j, 0:fd:100j]
    grid_z = griddata(
        (valid["X座標 (m)"], valid["Y座標 (m)"]),
        valid["RSSI (dBm)"],
        (grid_x, grid_y),
        method="linear",
        fill_value=-90,
    )

    fig = go.Figure()

    # フロア画像を背景に重畳
    if floor_image:
        b64 = base64.b64encode(floor_image).decode()
        fig.add_layout_image(
            source=f"data:image/png;base64,{b64}",
            xref="x", yref="y",
            x=0, y=fd,
            sizex=fw, sizey=fd,
            sizing="stretch",
            opacity=0.35,
            layer="below",
        )

    # 等高線ヒートマップ
    fig.add_trace(go.Contour(
        z=grid_z.T,
        x=np.linspace(0, fw, 100),
        y=np.linspace(0, fd, 100),
        colorscale=[
            [0.0,  "#ef4444"],   # -90 dBm 弱
            [0.25, "#f59e0b"],
            [0.6,  "#84cc16"],
            [1.0,  "#22c55e"],   #   0 dBm 強
        ],
        zmin=-90, zmax=-40,
        colorbar=dict(title="RSSI (dBm)", titleside="right"),
        contours=dict(showlabels=True, labelfont=dict(size=10)),
        opacity=0.75,
    ))

    # 測定ポイントマーカー
    hover_texts = [
        f"<b>{row.get('測定点名', f'P{i+1}')}</b><br>"
        f"X={row['X座標 (m)']}m, Y={row['Y座標 (m)']}m<br>"
        f"RSSI: {row['RSSI (dBm)']} dBm"
        for i, (_, row) in enumerate(valid.iterrows())
    ]
    fig.add_trace(go.Scatter(
        x=valid["X座標 (m)"],
        y=valid["Y座標 (m)"],
        mode="markers+text",
        text=valid.get("測定点名", [f"P{i+1}" for i in range(len(valid))]),
        textposition="top center",
        hovertext=hover_texts,
        hoverinfo="text",
        marker=dict(size=12, color="white", symbol="x",
                    line=dict(color="black", width=2)),
        name="測定ポイント",
    ))

    fig.update_layout(
        title=f"フロア RSSI ヒートマップ（{fw}m × {fd}m）",
        xaxis=dict(title="幅 (m)", range=[0, fw], showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
        yaxis=dict(title="奥行 (m)", range=[0, fd], showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
        height=520,
        plot_bgcolor="rgba(15,23,42,0.8)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0"),
        margin=dict(t=40, b=20, l=20, r=20),
    )
    st.plotly_chart(fig, use_container_width=True)
