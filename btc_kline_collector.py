import json
import pandas as pd
import requests
import threading
from datetime import datetime
from websocket import WebSocketApp
from dash import Dash, dcc, html
from dash.dependencies import Input, Output, State
import plotly.graph_objs as go

# ========== 全局配置 ==========
kline_history = []
chat_history = []
app = Dash(__name__)
deepseek_api_key = 'sk-449b142eed554ec5b16bd5a385b82907'  # 你的 DeepSeek API 密钥
deepseek_base_url = 'https://api.deepseek.com'
MODEL = "deepseek-chat"

# ========== Binance WebSocket 接收函数 ==========
def on_message(ws, message):
    global kline_history
    data = json.loads(message)
    kline = data["k"]

    if kline["x"]:
        time_str = datetime.fromtimestamp(kline["t"] / 1000).strftime('%Y-%m-%d %H:%M:%S')

        kline_data = {
            "时间": time_str,
            "开盘价": float(kline["o"]),
            "最高价": float(kline["h"]),
            "最低价": float(kline["l"]),
            "收盘价": float(kline["c"]),
            "成交量": float(kline["v"])
        }

        kline_history.append(kline_data)
        if len(kline_history) > 200:
            kline_history.pop(0)

# ========== DeepSeek 分析函数 ==========
def deepseek_api_call(prompt):
    url = f"{deepseek_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {deepseek_api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "你是一个加密货币市场分析专家。你需要简略的介绍下面该做什么。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 500
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        return result.get('choices', [{}])[0].get('message', {}).get('content', '无返回内容')
    except Exception as e:
        return f"请求失败：{e}"

# ========== Dash 页面布局 ==========
app.layout = html.Div([
    html.H1("BTC/USDT 实时K线图 (1分钟)", style={"textAlign": "center"}),
    dcc.Graph(id='kline-graph'),
    html.Button('获取交易建议', id='analyze-button', n_clicks=0),
    html.Div(id='deepseek-chat-box', style={"whiteSpace": "pre-line", "marginTop": "20px", "maxHeight": "300px", "overflowY": "scroll", "border": "1px solid #ccc", "padding": "10px"}),
    dcc.Interval(id='interval-component', interval=5000, n_intervals=0)
])

# ========== K线图更新回调 ==========
@app.callback(Output('kline-graph', 'figure'), [Input('interval-component', 'n_intervals')])
def update_kline_chart(n):
    if len(kline_history) < 2:
        return go.Figure()
    df = pd.DataFrame(kline_history)
    df["时间"] = pd.to_datetime(df["时间"])

    fig = go.Figure(data=[
        go.Candlestick(
            x=df["时间"],
            open=df["开盘价"],
            high=df["最高价"],
            low=df["最低价"],
            close=df["收盘价"],
            name="K线"
        )
    ])
    fig.update_layout(xaxis_rangeslider_visible=False)
    return fig

# ========== DeepSeek 分析回调 ==========
@app.callback(
    Output('deepseek-chat-box', 'children'),
    [Input('analyze-button', 'n_clicks')],
    [State('deepseek-chat-box', 'children')]
)
def analyze(n_clicks, previous_content):
    global chat_history
    if n_clicks == 0 or len(kline_history) < 10:
        return previous_content or "点击按钮以获取分析结果"

    df = pd.DataFrame(kline_history[-20:])
    kline_data = "\n".join([
        f"时间: {row['时间']}，开盘: {row['开盘价']}，高: {row['最高价']}，低: {row['最低价']}，收: {row['收盘价']}"
        for _, row in df.iterrows()
    ])
    prompt = f"""
最近20根BTC/USDT 1分钟K线如下：
{kline_data}
请基于以上数据简要分析当前市场情况，并给出合理交易建议。
"""
    result = deepseek_api_call(prompt)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    chat_entry = f"[{timestamp}]\n{result}\n"
    chat_history.append(chat_entry)
    return "\n\n".join(chat_history)

# ========== 启动 Binance WebSocket ==========
def start_ws():
    ws_url = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"
    ws = WebSocketApp(ws_url, on_message=on_message)
    ws.run_forever()

threading.Thread(target=start_ws, daemon=True).start()

if __name__ == '__main__':
    app.run_server(debug=True, port=8050)
