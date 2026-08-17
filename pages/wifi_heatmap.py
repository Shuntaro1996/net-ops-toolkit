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

        # クイック測定点追加フォーム
        with st.expander("➕ 測定ポイントをクイック追加", expanded=False):
            with st.form("quick_add_point"):
                qa_c1, qa_c2 = st.columns(2)
                fw, fd = st.session_state.floor_size
                qa_x = qa_c1.number_input("X座標 (m)", 0.0, float(fw), float(fw)/2, 0.5)
                qa_y = qa_c2.number_input("Y座標 (m)", 0.0, float(fd), float(fd)/2, 0.5)
                qa_c3, qa_c4 = st.columns(2)
                qa_rssi = qa_c3.number_input("RSSI (dBm)", -100, 0, -65, 1)
                qa_name = qa_c4.text_input("点名", f"P{len(df)+1}")
                if st.form_submit_button("＋ リストに追加", use_container_width=True):
                    new_row = pd.DataFrame([{
                        "X座標 (m)": qa_x, "Y座標 (m)": qa_y,
                        "RSSI (dBm)": qa_rssi, "測定点名": qa_name
                    }])
                    st.session_state.rssi_df = pd.concat([st.session_state.rssi_df, new_row], ignore_index=True)
                    st.rerun()

        # ── RSSIサマリー ──
        st.markdown("**📊 電波品質サマリー**")
        rssi_vals = df["RSSI (dBm)"].dropna()
        if len(rssi_vals) > 0:
            avg_rssi = rssi_vals.mean()
            min_rssi = rssi_vals.min()
            weak_pct = (rssi_vals < -75).sum() / len(rssi_vals) * 100

            m1, m2, m3 = st.columns(3)
            m1.metric("平均電波強度", f"{avg_rssi:.1f} dBm")
            m2.metric("最弱測定点", f"{min_rssi:.0f} dBm")
            m3.metric("弱電界率 (< -75dBm)", f"{weak_pct:.0f}%")

            # 品質評価
            if avg_rssi >= -60:
                st.success("🟢 **電波品質: 優良** — フロア全体で安定した通信が期待できます。")
            elif avg_rssi >= -70:
                st.warning("🟡 **電波品質: 良好** — 一部エリアで速度低下の可能性があります。")
            else:
                st.error("🔴 **電波品質: 改善推奨** — 複数エリアでAP増設または出力調整が必要です。")

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

    # 等高線ヒートマップ (UniFi / Datadog Pro Palette)
    fig.add_trace(go.Contour(
        z=grid_z.T,
        x=np.linspace(0, fw, 100),
        y=np.linspace(0, fd, 100),
        colorscale=[
            [0.0,  "#e11d48"],   # -90 dBm 弱 (Rose / Crimson)
            [0.25, "#f59e0b"],   # -77 dBm 注意 (Amber)
            [0.55, "#10b981"],   # -62 dBm 良好 (Emerald)
            [1.0,  "#06b6d4"],   # -40 dBm 強 (Cyan)
        ],
        zmin=-90, zmax=-40,
        colorbar=dict(
            title=dict(text="RSSI (dBm)", side="top", font=dict(size=11, color="#cbd5e1")),
            tickfont=dict(color="#94a3b8", size=10),
            thickness=14,
            len=0.9,
        ),
        contours=dict(
            showlabels=True,
            labelfont=dict(size=10, color="#ffffff"),
            coloring="heatmap",
        ),
        opacity=0.82,
    ))

    # 測定ポイントマーカー
    hover_texts = [
        f"<b>{row.get('測定点名', f'P{i+1}')}</b><br>"
        f"X: {row['X座標 (m)']} m, Y: {row['Y座標 (m)']} m<br>"
        f"<b>RSSI: {row['RSSI (dBm)']} dBm</b>"
        for i, (_, row) in enumerate(valid.iterrows())
    ]
    fig.add_trace(go.Scatter(
        x=valid["X座標 (m)"],
        y=valid["Y座標 (m)"],
        mode="markers+text",
        text=valid.get("測定点名", [f"P{i+1}" for i in range(len(valid))]),
        textposition="top center",
        textfont=dict(size=11, color="#f8fafc", family="JetBrains Mono"),
        hovertext=hover_texts,
        hoverinfo="text",
        marker=dict(
            size=11,
            color="#22d3ee",
            symbol="diamond",
            line=dict(color="#0f172a", width=2),
        ),
        name="測定ポイント",
    ))

    fig.update_layout(
        title=dict(
            text=f"フロア RSSI ヒートマップ（{fw}m × {fd}m）",
            font=dict(size=14, color="#f1f5f9"),
        ),
        xaxis=dict(
            title=dict(text="幅 (m)", font=dict(color="#94a3b8")),
            range=[0, fw],
            showgrid=True,
            gridcolor="rgba(148,163,184,0.12)",
            tickfont=dict(color="#94a3b8"),
        ),
        yaxis=dict(
            title=dict(text="奥行 (m)", font=dict(color="#94a3b8")),
            range=[0, fd],
            showgrid=True,
            gridcolor="rgba(148,163,184,0.12)",
            tickfont=dict(color="#94a3b8"),
        ),
        height=530,
        plot_bgcolor="rgba(15,23,42,0.9)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0"),
        margin=dict(t=45, b=25, l=25, r=25),
    )
    st.plotly_chart(fig, use_container_width=True)
