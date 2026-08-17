Set-Content -Path "README.md" -Value @"
# 🌐 Network Ops Toolkit & Wi-Fi RSSI Dashboard

ネットワーク運用・インフラ管理を効率化するためのPython/Streamlitベースの可視化ダッシュボードです。

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://net-ops-toolkit.streamlit.app)

🔗 **Live Demo:** [https://net-ops-toolkit.streamlit.app](https://net-ops-toolkit.streamlit.app)

---

## 📌 主な機能

### 1. Wi-Fi RSSI ヒートマップ解析
* 空間座標（X, Y）と電波強度（RSSI: dBm）のデータから、2次元グリッド補間を行い等高線カラーマップを描画。
* 表データの直接編集・追加に対応し、リアルタイムに電波状況を再計算・可視化。
* Wi-Fiアクセスポイントの設置位置検討や、電波デッドゾーンの特定を支援。

### 2. ネットワーク機器 死活監視ダッシュボード
* ルーター、スイッチ、アクセスポイントなどの稼働状況（Online / Offline）および応答時間（Latency）を一覧表示。
* 異常発生時のステータス強調表示と台数サマリーの自動集計。

---

## 🛠️ 技術スタック

* **Language**: Python 3.10+
* **Framework**: Streamlit
* **Data & Analytics**: Pandas, NumPy, SciPy (2D Interpolation)
* **Visualization**: Plotly Graph Objects
* **Deployment**: Streamlit Community Cloud

---

## 🚀 ローカル環境での実行方法

```bash
# リポジトリのクローン
git clone [https://github.com/shuntaro1996/net-ops-toolkit.git](https://github.com/shuntaro1996/net-ops-toolkit.git)
cd net-ops-toolkit

# 依存パッケージのインストール
pip install -r requirements.txt

# アプリの起動
streamlit run app.py
