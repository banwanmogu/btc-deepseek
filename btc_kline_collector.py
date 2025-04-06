import json
import pandas as pd
import requests
import threading
import time
import logging
from datetime import datetime
from websocket import WebSocketApp
from dash import Dash, dcc, html, callback_context
from dash.dependencies import Input, Output, State
import plotly.graph_objs as go
from plotly.subplots import make_subplots
from config import *
from technical_indicators import TechnicalIndicators
from functools import wraps
import secrets
from auth_config import (
    ACCESS_TOKEN, 
    ALLOWED_IPS, 
    TOKEN_EXPIRY,
    SECURITY_CONFIG,
    SERVER_CONFIG
)
import ssl
import os

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)

# ========== 全局变量 ==========
kline_history = []
chat_history = []
ws = None
app = Dash(__name__)
has_data = False  # 添加数据状态标志
current_interval = "1m"  # 默认1分钟K线
current_price = 0  # 添加当前价格变量
login_attempts = {}  # 记录登录尝试次数

# ========== 访问控制装饰器 ==========
def require_auth(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        ctx = callback_context
        if not ctx.triggered:
            return "请先登录"
        
        # 获取请求头中的认证信息
        headers = ctx.triggered[0].get('headers', {})
        auth_token = headers.get('Authorization', '')
        
        if auth_token != f"Bearer {ACCESS_TOKEN}":
            return "未授权访问"
        
        return f(*args, **kwargs)
    return wrapped

# ========== 登录回调 ==========
@app.callback(
    Output('login-status', 'children'),
    [Input('login-button', 'n_clicks')],
    [State('password-input', 'value')]
)
def login(n_clicks, password):
    if not n_clicks:
        return "请输入访问密码"
    
    # 获取客户端IP（仅用于日志）
    ctx = callback_context
    headers = ctx.triggered[0].get('headers', {})
    client_ip = headers.get('X-Forwarded-For', 'unknown')
    
    # 检查登录尝试次数
    if client_ip in login_attempts:
        if login_attempts[client_ip] >= SECURITY_CONFIG["MAX_LOGIN_ATTEMPTS"]:
            return "登录尝试次数过多，请稍后再试"
    
    if password == ACCESS_TOKEN:
        # 登录成功，重置尝试次数
        login_attempts[client_ip] = 0
        logging.info(f"成功登录: {client_ip}")
        return "登录成功"
    
    # 登录失败，增加尝试次数
    login_attempts[client_ip] = login_attempts.get(client_ip, 0) + 1
    logging.warning(f"登录失败: {client_ip}")
    return "密码错误"

# ========== 获取历史数据 ==========
def fetch_historical_data():
    global kline_history, has_data
    try:
        # 计算时间戳
        end_time = int(time.time() * 1000)
        # 获取20分钟前的时间戳（确保有足够数据计算RSI）
        start_time = end_time - (20 * 60 * 1000)  # 20分钟前
        
        # 构建请求URL
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": "BTCUSDT",
            "interval": "1m",  # 使用1分钟K线
            "limit": 20,  # 获取20根K线
            "startTime": start_time,
            "endTime": end_time
        }
        
        # 发送请求
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        # 清空历史数据
        kline_history = []
        
        # 处理数据
        for kline in data:
            time_str = datetime.fromtimestamp(kline[0] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            kline_data = {
                "时间": time_str,
                "开盘价": float(kline[1]),
                "最高价": float(kline[2]),
                "最低价": float(kline[3]),
                "收盘价": float(kline[4]),
                "成交量": float(kline[5])
            }
            kline_history.append(kline_data)
        
        # 更新数据状态
        has_data = len(kline_history) >= 14  # 修改为至少需要14根K线
        logging.info(f"成功获取 {len(kline_history)} 根历史K线数据")
        return True
        
    except Exception as e:
        logging.error(f"获取历史数据失败: {e}")
        return False

# ========== WebSocket 相关函数 ==========
def on_error(ws, error):
    logging.error(f"WebSocket错误: {error}")

def on_close(ws, close_status_code, close_msg):
    logging.warning("WebSocket连接关闭")
    reconnect_websocket()

def on_open(ws):
    logging.info("WebSocket连接已建立")

def reconnect_websocket():
    global ws
    retries = 0
    while retries < MAX_RETRIES:
        try:
            logging.info(f"尝试重新连接WebSocket (尝试 {retries + 1}/{MAX_RETRIES})")
            ws = WebSocketApp(WS_URL, on_message=on_message, on_error=on_error, 
                            on_close=on_close, on_open=on_open)
            ws.run_forever()
            break
        except Exception as e:
            logging.error(f"重连失败: {e}")
            retries += 1
            time.sleep(RETRY_DELAY)
    if retries == MAX_RETRIES:
        logging.error("达到最大重试次数，停止重连")

def on_message(ws, message):
    global kline_history, has_data
    try:
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

            # 清理超过20分钟的数据（确保有足够数据计算RSI）
            current_time = datetime.now()
            kline_history = [
                data for data in kline_history 
                if (current_time - datetime.strptime(data["时间"], '%Y-%m-%d %H:%M:%S')).total_seconds() <= 1200  # 20分钟 = 1200秒
            ]
            
            # 添加新数据
            kline_history.append(kline_data)
            
            # 更新数据状态
            has_data = len(kline_history) >= 14  # 修改为至少需要14根K线
            
            # 保存数据到文件
            save_data()
    except Exception as e:
        logging.error(f"处理消息时出错: {e}")

# ========== 数据持久化 ==========
def save_data():
    try:
        with open('kline_history.json', 'w') as f:
            json.dump(kline_history, f)
    except Exception as e:
        logging.error(f"保存数据失败: {e}")

def load_data():
    global kline_history
    try:
        with open('kline_history.json', 'r') as f:
            kline_history = json.load(f)
    except FileNotFoundError:
        logging.info("未找到历史数据文件")
    except Exception as e:
        logging.error(f"加载数据失败: {e}")

# ========== DeepSeek 分析函数 ==========
def deepseek_api_call(prompt):
    """调用 DeepSeek API 获取分析结果"""
    try:
        logging.info("开始调用 DeepSeek API...")
        logging.info(f"请求内容：\n{prompt}")
        
        # 构建请求数据
        data = {
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个专业的加密货币交易分析师，擅长技术分析和市场预测。请基于提供的K线数据和技术指标，给出专业的交易建议。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
            "max_tokens": 1000
        }
        
        # 发送请求，增加超时时间
        logging.info("正在发送 API 请求...")
        response = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json=data,
            timeout=60  # 增加超时时间到60秒
        )
        
        # 检查响应状态
        if response.status_code == 200:
            logging.info("API 请求成功，正在解析响应...")
            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                analysis = result["choices"][0]["message"]["content"]
                logging.info("成功获取分析结果")
                return analysis
            else:
                logging.error("API 响应格式错误")
                return "分析结果解析失败，请重试"
        else:
            logging.error(f"API 请求失败，状态码：{response.status_code}")
            return f"API 请求失败，状态码：{response.status_code}"
            
    except requests.exceptions.Timeout:
        logging.error("API 请求超时")
        return "请求超时，请检查网络连接后重试"
    except requests.exceptions.ConnectionError:
        logging.error("API 连接错误")
        return "连接错误，请检查网络连接后重试"
    except requests.exceptions.RequestException as e:
        logging.error(f"API 请求异常：{str(e)}")
        return f"请求异常：{str(e)}，请稍后重试"
    except Exception as e:
        logging.error(f"未知错误：{str(e)}")
        return f"发生错误：{str(e)}，请稍后重试"

# ========== 页面布局 ==========
app.layout = html.Div([
    # 登录界面
    html.Div([
        html.Div([
            html.H2("BTC/USDT 实时分析系统", 
                    style={
                        "textAlign": "center", 
                        "marginBottom": "30px",
                        "color": "#2c3e50",
                        "fontSize": "24px",
                        "fontWeight": "600"
                    }),
            html.Div([
                html.Label("访问密码：", 
                          style={
                              "display": "block",
                              "marginBottom": "8px",
                              "color": "#374151",
                              "fontSize": "14px",
                              "fontWeight": "500"
                          }),
                dcc.Input(id='password-input', 
                         type='password',
                         style={
                             "width": "100%",
                             "padding": "10px 12px",
                             "border": "1px solid #e5e7eb",
                             "borderRadius": "6px",
                             "fontSize": "14px",
                             "marginBottom": "15px",
                             "boxSizing": "border-box",
                             "transition": "border-color 0.2s ease"
                         }),
                html.Button('登录', 
                           id='login-button', 
                           n_clicks=0,
                           style={
                               "width": "100%",
                               "padding": "10px",
                               "backgroundColor": "#3b82f6",
                               "color": "white",
                               "border": "none",
                               "borderRadius": "6px",
                               "fontSize": "14px",
                               "fontWeight": "500",
                               "cursor": "pointer",
                               "transition": "all 0.2s ease"
                           })
            ], style={
                "maxWidth": "300px",
                "margin": "0 auto",
                "padding": "0 20px"
            }),
            html.Div(id='login-status', 
                    style={
                        "marginTop": "15px",
                        "textAlign": "center",
                        "color": "#6b7280",
                        "fontSize": "14px"
                    })
        ], style={
            "padding": "40px",
            "backgroundColor": "white",
            "borderRadius": "12px",
            "boxShadow": "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
            "maxWidth": "400px",
            "width": "90%",
            "margin": "0 auto"
        })
    ], id='login-container', 
    style={
        "display": "flex",
        "justifyContent": "center",
        "alignItems": "center",
        "minHeight": "100vh",
        "backgroundColor": "#f3f4f6",
        "padding": "20px"
    }),
    
    # 主界面
    html.Div([
        # 添加定时更新组件
        dcc.Interval(
            id='interval-component',
            interval=1000,  # 每秒更新一次
            n_intervals=0
        ),
        
        # 顶部导航栏
        html.Div([
            # 左侧Logo和标题
            html.Div([
                html.I(className="fas fa-chart-line", 
                      style={"fontSize": "24px", "marginRight": "10px", "color": "#2c3e50"}),
                html.H1("BTC/USDT 实时分析", 
                       style={"margin": "0", "display": "inline-block", "fontSize": "20px", "color": "#2c3e50"})
            ], style={"display": "flex", "AlignItems": "center"})
        ], style={
            "display": "flex",
            "justifyContent": "space-between",
            "alignItems": "center",
            "padding": "15px 30px",
            "backgroundColor": "#ffffff",
            "borderBottom": "1px solid #e5e7eb",
            "position": "fixed",
            "top": 0,
            "left": 0,
            "right": 0,
            "zIndex": 1000
        }),
        
        # 主要内容区域
        html.Div([
            # 左侧边栏
            html.Div([
                # 设置选项按钮
                html.Div([
                    html.Button([
                        html.I(className="fas fa-cog", style={"marginRight": "12px", "fontSize": "16px"}),
                        "设置选项",
                        html.I(className="fas fa-chevron-down", 
                              style={
                                  "marginLeft": "auto",
                                  "transition": "transform 0.3s ease",
                                  "fontSize": "14px"
                              },
                              id="settings-arrow")
                    ], id='settings-button', n_clicks=0,
                    style={
                        "width": "100%",
                        "textAlign": "left",
                        "padding": "12px 16px",
                        "backgroundColor": "#ffffff",
                        "border": "none",
                        "borderBottom": "1px solid #e5e7eb",
                        "cursor": "pointer",
                        "fontSize": "14px",
                        "fontWeight": "500",
                        "color": "#374151",
                        "display": "flex",
                        "alignItems": "center",
                        "transition": "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
                        "position": "relative",
                        "zIndex": "1",
                        ":hover": {
                            "backgroundColor": "#f8fafc",
                            "boxShadow": "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
                            "transform": "translateY(-2px)",
                            "borderRadius": "8px",
                            "margin": "0 10px"
                        }
                    }),
                    # 设置选项内容
                    html.Div([
                        # K线周期选择
                        html.Div([
                            html.Label("K线周期", style={"color": "#6b7280", "fontSize": "13px"}),
                            dcc.Dropdown(
                                id='kline-interval',
                                options=[
                                    {'label': '1分钟', 'value': '1m'},
                                    {'label': '5分钟', 'value': '5m'},
                                    {'label': '15分钟', 'value': '15m'},
                                    {'label': '30分钟', 'value': '30m'},
                                    {'label': '1小时', 'value': '1h'}
                                ],
                                value='1m',
                                style={"marginTop": "5px", "marginBottom": "12px"}
                            ),
                            
                            # 技术指标选择
                            html.Label("技术指标", style={"color": "#6b7280", "fontSize": "13px"}),
                            dcc.Checklist(
                                id='technical-indicators',
                                options=[
                                    {'label': ' MA均线', 'value': 'ma'},
                                    {'label': ' RSI指标', 'value': 'rsi'},
                                    {'label': ' MACD指标', 'value': 'macd'},
                                    {'label': ' 布林带', 'value': 'bollinger'}
                                ],
                                value=['ma', 'rsi', 'macd', 'bollinger'],
                                style={"marginTop": "5px", "color": "#374151", "fontSize": "13px"}
                            )
                        ], style={"padding": "12px"})
                    ], id='settings-content', 
                    style={
                        "maxHeight": "0",
                        "overflow": "hidden",
                        "transition": "all 0.3s ease-in-out",
                        "opacity": "0"
                    })
                ]),
                
                # 持仓信息按钮
                html.Div([
                    html.Button([
                        html.I(className="fas fa-wallet", style={"marginRight": "12px", "fontSize": "16px"}),
                        "持仓信息",
                        html.I(className="fas fa-chevron-down", 
                              style={
                                  "marginLeft": "auto",
                                  "transition": "transform 0.3s ease",
                                  "fontSize": "14px"
                              },
                              id="position-arrow")
                    ], id='position-button', n_clicks=0,
                    style={
                        "width": "100%",
                        "textAlign": "left",
                        "padding": "12px 16px",
                        "backgroundColor": "#ffffff",
                        "border": "none",
                        "borderBottom": "1px solid #e5e7eb",
                        "cursor": "pointer",
                        "fontSize": "14px",
                        "fontWeight": "500",
                        "color": "#374151",
                        "display": "flex",
                        "alignItems": "center",
                        "transition": "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
                        "position": "relative",
                        "zIndex": "1",
                        ":hover": {
                            "backgroundColor": "#f8fafc",
                            "boxShadow": "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
                            "transform": "translateY(-2px)",
                            "borderRadius": "8px",
                            "margin": "0 10px"
                        }
                    }),
                    # 持仓信息内容
                    html.Div([
                        html.Div([
                            html.Div([
                                html.Label("开仓价格：", style={"fontSize": "13px"}),
                                dcc.Input(id='entry-price', type='number', value=0,
                                         style={"width": "100%", 
                                                "marginTop": "5px",
                                                "padding": "6px",
                                                "border": "1px solid #e5e7eb",
                                                "borderRadius": "4px",
                                                "fontSize": "13px"})
                            ], style={"marginBottom": "8px"}),
                            html.Div([
                                html.Label("当前价格：", style={"fontSize": "13px"}),
                                html.Div(id='current-price',
                                        style={"fontWeight": "bold", 
                                               "color": "#3b82f6",
                                               "marginTop": "5px",
                                               "fontSize": "13px"})
                            ], style={"marginBottom": "8px"}),
                            html.Div([
                                html.Label("仓位方向：", style={"fontSize": "13px"}),
                                dcc.Dropdown(
                                    id='position-direction',
                                    options=[
                                        {'label': '多仓', 'value': 'long'},
                                        {'label': '空仓', 'value': 'short'}
                                    ],
                                    value='long',
                                    style={"marginTop": "5px", "fontSize": "13px"}
                                )
                            ], style={"marginBottom": "8px"}),
                            html.Div([
                                html.Label("杠杆倍数：", style={"fontSize": "13px"}),
                                dcc.Input(id='leverage', type='number', value=10,
                                         style={"width": "100%", 
                                                "marginTop": "5px",
                                                "padding": "6px",
                                                "border": "1px solid #e5e7eb",
                                                "borderRadius": "4px",
                                                "fontSize": "13px"})
                            ], style={"marginBottom": "8px"}),
                            html.Div([
                                html.Label("持仓数量(USDT)：", style={"fontSize": "13px"}),
                                dcc.Input(id='position-size', type='number', value=50,
                                         style={"width": "100%", 
                                                "marginTop": "5px",
                                                "padding": "6px",
                                                "border": "1px solid #e5e7eb",
                                                "borderRadius": "4px",
                                                "fontSize": "13px"})
                            ])
                        ], style={"padding": "12px"})
                    ], id='position-content',
                    style={
                        "maxHeight": "0",
                        "overflow": "hidden",
                        "transition": "all 0.3s ease-in-out",
                        "opacity": "0"
                    })
                ])
            ], style={
                "width": "240px",
                "backgroundColor": "#ffffff",
                "borderRight": "1px solid #e5e7eb",
                "height": "calc(100vh - 60px)",
                "position": "fixed",
                "top": "60px",
                "left": 0,
                "overflowY": "auto"
            }),
            
            # 右侧主要内容
            html.Div([
                # 图表区域
                html.Div([
                    # K线图和建议区域（左右布局）
                    html.Div([
                        # 左侧K线图
                        html.Div([
                            dcc.Graph(id='kline-graph', 
                                     style={"height": "400px"})
                        ], style={
                            "backgroundColor": "#ffffff",
                            "borderRadius": "8px",
                            "boxShadow": "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
                            "padding": "12px",
                            "flex": "2",
                            "marginRight": "15px"
                        }),
                        
                        # 右侧交易建议区域
                        html.Div([
                            # 按钮组
                            html.Div([
                                html.Button('获取交易建议', 
                                          id='analyze-button', 
                                          n_clicks=0,
                                          style={
                                              "backgroundColor": "#f3f4f6",
                                              "color": "#374151",
                                              "padding": "8px 16px",
                                              "border": "1px solid #e5e7eb",
                                              "borderRadius": "6px",
                                              "cursor": "pointer",
                                              "fontSize": "13px",
                                              "fontWeight": "500",
                                              "width": "100%",
                                              "marginBottom": "8px",
                                              "transition": "all 0.2s ease",
                                              ":hover": {
                                                  "backgroundColor": "#e5e7eb",
                                                  "borderColor": "#d1d5db"
                                              }
                                          }),
                                html.Button('获取买入建议', 
                                          id='buy-analyze-button', 
                                          n_clicks=0,
                                          style={
                                              "backgroundColor": "#f3f4f6",
                                              "color": "#374151",
                                              "padding": "8px 16px",
                                              "border": "1px solid #e5e7eb",
                                              "borderRadius": "6px",
                                              "cursor": "pointer",
                                              "fontSize": "13px",
                                              "fontWeight": "500",
                                              "width": "100%",
                                              "marginBottom": "12px",
                                              "transition": "all 0.2s ease",
                                              ":hover": {
                                                  "backgroundColor": "#e5e7eb",
                                                  "borderColor": "#d1d5db"
                                              }
                                          })
                            ], style={
                                "marginBottom": "12px"
                            }),
                            
                            # 分析结果显示
                            dcc.Loading(
                                id="loading-analysis",
                                type="circle",
                                children=html.Div(
                                    id='deepseek-chat-box',
                                    style={
                                        "backgroundColor": "#ffffff",
                                        "borderRadius": "8px",
                                        "boxShadow": "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
                                        "padding": "12px",
                                        "height": "300px",
                                        "overflowY": "auto",
                                        "whiteSpace": "pre-line",
                                        "fontSize": "13px",
                                        "lineHeight": "1.5"
                                    }
                                )
                            )
                        ], style={
                            "backgroundColor": "#ffffff",
                            "borderRadius": "8px",
                            "boxShadow": "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
                            "padding": "12px",
                            "flex": "1",
                            "minWidth": "300px"
                        })
                    ], style={
                        "display": "flex",
                        "marginBottom": "15px",
                        "gap": "15px"
                    }),
                    
                    # 技术指标区域（左右布局）
                    html.Div([
                        # 左侧技术指标图
                        html.Div([
                            dcc.Graph(id='indicator-graph',
                                     style={"height": "300px"})
                        ], style={
                            "backgroundColor": "#ffffff",
                            "borderRadius": "8px",
                            "boxShadow": "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
                            "padding": "12px",
                            "flex": "2",
                            "marginRight": "15px"
                        }),
                        
                        # 右侧指标说明
                        html.Div([
                            html.H3("技术指标说明", 
                                    style={
                                        "color": "#2c3e50",
                                        "fontSize": "16px",
                                        "marginBottom": "15px",
                                        "fontWeight": "600"
                                    }),
                            
                            # RSI指标说明
                            html.Div([
                                html.H4("RSI指标", 
                                        style={
                                            "color": "#374151",
                                            "fontSize": "14px",
                                            "marginBottom": "8px",
                                            "fontWeight": "500"
                                        }),
                                html.P([
                                    "RSI（相对强弱指标）是一个衡量市场超买超卖的技术指标。",
                                    html.Br(),
                                    "• 取值范围：0-100",
                                    html.Br(),
                                    "• RSI > 70：超买，可能要回调（下跌）",
                                    html.Br(),
                                    "• RSI < 30：超卖，可能会反弹（上涨）",
                                    html.Br(),
                                    "• 🧠 当 RSI 超过 70，不要追高；低于 30，可以关注是否反弹机会。"
                                ], style={
                                    "color": "#6b7280",
                                    "fontSize": "13px",
                                    "lineHeight": "1.5",
                                    "marginBottom": "15px"
                                })
                            ]),
                            
                            # MACD指标说明
                            html.Div([
                                html.H4("MACD指标", 
                                        style={
                                            "color": "#374151",
                                            "fontSize": "14px",
                                            "marginBottom": "8px",
                                            "fontWeight": "500"
                                        }),
                                html.P([
                                    "MACD（移动平均线收敛散度）是一个趋势跟踪指标。",
                                    html.Br(),
                                    "• MACD线：快速EMA - 慢速EMA",
                                    html.Br(),
                                    "• 信号线：MACD的9日EMA",
                                    html.Br(),
                                    "• 柱状图：MACD - 信号线",
                                    html.Br(),
                                    "• MACD线 上穿 Signal线，🧠考虑买入",
                                    html.Br(),
                                    "• MACD线 下穿 Signal线，🧠考虑卖出或观望"
                                ], style={
                                    "color": "#6b7280",
                                    "fontSize": "13px",
                                    "lineHeight": "1.5"
                                })
                            ])
                        ], style={
                            "backgroundColor": "#ffffff",
                            "borderRadius": "8px",
                            "boxShadow": "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
                            "padding": "15px",
                            "flex": "1",
                            "minWidth": "300px",
                            "overflowY": "auto"
                        })
                    ], style={
                        "display": "flex",
                        "marginBottom": "15px",
                        "gap": "15px"
                    })
                ])
            ], style={
                "marginLeft": "260px",
                "marginTop": "80px",
                "padding": "15px",
                "maxWidth": "1200px"
            })
        ], style={"backgroundColor": "#f3f4f6"})
    ], id='main-container', style={"display": "none"})
])

# ========== 登录状态回调 ==========
@app.callback(
    [Output('login-container', 'style'),
     Output('main-container', 'style')],
    [Input('login-status', 'children')]
)
def update_visibility(login_status):
    if login_status == "登录成功":
        return {"display": "none"}, {"display": "block"}
    return {"display": "flex"}, {"display": "none"}

# ========== 图表更新回调 ==========
@app.callback(
    [Output('kline-graph', 'figure', allow_duplicate=True),
     Output('indicator-graph', 'figure', allow_duplicate=True)],
    [Input('interval-component', 'n_intervals'),
     Input('technical-indicators', 'value')],
    prevent_initial_call=True
)
def update_charts(n_intervals, selected_indicators):
    # 设置主题颜色
    bg_color = '#ffffff'
    text_color = '#1f2937'
    grid_color = '#e5e7eb'
    plot_bg_color = '#ffffff'
    
    # 创建K线图
    kline_fig = go.Figure()
    indicator_fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=('RSI指标', 'MACD指标'),
        column_widths=[0.5, 0.5]
    )

    if len(kline_history) == 0:
        # 如果没有数据，显示等待消息
        kline_fig.add_annotation(
            text="等待数据收集...",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(color=text_color)
        )
        indicator_fig.add_annotation(
            text="等待数据收集...",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(color=text_color)
        )
    else:
        try:
            df = pd.DataFrame(kline_history)
            df["时间"] = pd.to_datetime(df["时间"])

            # 计算技术指标
            df = TechnicalIndicators.calculate_all_indicators(df)
            
            # 添加K线图
            kline_fig.add_trace(
                go.Candlestick(
                    x=df["时间"],
                    open=df["开盘价"],
                    high=df["最高价"],
                    low=df["最低价"],
                    close=df["收盘价"],
                    name="BTC/USDT",
                    increasing_line_color='#26a69a',  # 上涨为绿色
                    decreasing_line_color='#ef5350',  # 下跌为红色
                    increasing_fillcolor='#26a69a',
                    decreasing_fillcolor='#ef5350'
                )
            )
            
            # 根据选择的技术指标添加相应的线
            if 'ma' in selected_indicators:
                kline_fig.add_trace(go.Scatter(
                    x=df["时间"], 
                    y=df["MA5"], 
                    name="MA5", 
                    line=dict(color='#2196f3', width=1)
                ))
                kline_fig.add_trace(go.Scatter(
                    x=df["时间"], 
                    y=df["MA10"], 
                    name="MA10", 
                    line=dict(color='#ff9800', width=1)
                ))
                kline_fig.add_trace(go.Scatter(
                    x=df["时间"], 
                    y=df["MA20"], 
                    name="MA20", 
                    line=dict(color='#4caf50', width=1)
                ))
                kline_fig.add_trace(go.Scatter(
                    x=df["时间"], 
                    y=df["MA30"], 
                    name="MA30", 
                    line=dict(color='#f44336', width=1)
                ))
            
            if 'bollinger' in selected_indicators:
                kline_fig.add_trace(go.Scatter(
                    x=df["时间"], 
                    y=df["BB_Upper"], 
                    name="布林上轨", 
                    line=dict(color='#9e9e9e', dash='dash', width=1)
                ))
                kline_fig.add_trace(go.Scatter(
                    x=df["时间"], 
                    y=df["BB_Lower"], 
                    name="布林下轨", 
                    line=dict(color='#9e9e9e', dash='dash', width=1)
                ))
                
                # 添加布林带填充
                kline_fig.add_trace(go.Scatter(
                    x=df["时间"].tolist() + df["时间"].tolist()[::-1],
                    y=df["BB_Upper"].tolist() + df["BB_Lower"].tolist()[::-1],
                    fill='toself',
                    fillcolor='rgba(158, 158, 158, 0.1)',
                    line=dict(color='rgba(255,255,255,0)'),
                    name='布林带'
                ))
            
            # 添加RSI图（左侧）
            if 'rsi' in selected_indicators:
                indicator_fig.add_trace(go.Scatter(x=df["时间"], y=df["RSI"], name="RSI", 
                                                 line=dict(color='purple')), row=1, col=1)
                indicator_fig.add_trace(go.Scatter(x=df["时间"], y=[70] * len(df), name="超买线", 
                                                 line=dict(color='red', dash='dash')), row=1, col=1)
                indicator_fig.add_trace(go.Scatter(x=df["时间"], y=[30] * len(df), name="超卖线", 
                                                 line=dict(color='green', dash='dash')), row=1, col=1)
            
            # 添加MACD图（右侧）
            if 'macd' in selected_indicators:
                indicator_fig.add_trace(go.Scatter(x=df["时间"], y=df["MACD"], name="MACD", 
                                                 line=dict(color='blue')), row=1, col=2)
                indicator_fig.add_trace(go.Scatter(x=df["时间"], y=df["Signal"], name="Signal", 
                                                 line=dict(color='orange')), row=1, col=2)
                indicator_fig.add_trace(go.Bar(x=df["时间"], y=df["MACD_Hist"], name="MACD Histogram", 
                                             marker_color='gray'), row=1, col=2)
            
        except Exception as e:
            logging.error(f"更新图表失败: {e}")
            kline_fig.add_annotation(
                text=f"图表更新失败: {str(e)}",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(color=text_color)
            )
    
    # 更新K线图布局
    kline_fig.update_layout(
        paper_bgcolor=bg_color,
        plot_bgcolor=plot_bg_color,
        font=dict(color=text_color),
        margin=dict(l=10, r=10, t=20, b=10),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            bgcolor='rgba(255, 255, 255, 0.8)',
            bordercolor='rgba(0, 0, 0, 0.1)',
            borderwidth=1,
            font=dict(size=10)
        ),
        xaxis=dict(
            showgrid=True,
            gridcolor=grid_color,
            showline=True,
            linecolor=grid_color,
            rangeslider=dict(visible=False),  # 禁用范围滑块
            type='date',
            tickformat='%H:%M',  # 只显示时间
            title=dict(text='时间', font=dict(size=10)),
            tickfont=dict(size=10)
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor=grid_color,
            showline=True,
            linecolor=grid_color,
            title=dict(text='价格 (USDT)', font=dict(size=10)),
            tickformat='.2f',  # 保留两位小数
            tickfont=dict(size=10)
        ),
        height=350,  # 减小图表高度
        title=dict(
            text='BTC/USDT 实时K线图',
            x=0.5,
            y=0.95,
            xanchor='center',
            yanchor='top',
            font=dict(size=14, color=text_color)
        )
    )
    
    # 更新技术指标图布局
    indicator_fig.update_layout(
        paper_bgcolor=bg_color,
        plot_bgcolor=plot_bg_color,
        font=dict(color=text_color),
        margin=dict(l=10, r=10, t=20, b=10),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10)
        ),
        height=250  # 减小图表高度
    )
    
    # 更新技术指标图的坐标轴
    indicator_fig.update_xaxes(
        showgrid=True, 
        gridcolor=grid_color, 
        showline=True, 
        linecolor=grid_color,
        tickfont=dict(size=10)
    )
    indicator_fig.update_yaxes(
        showgrid=True, 
        gridcolor=grid_color, 
        showline=True, 
        linecolor=grid_color,
        tickfont=dict(size=10)
    )
    
    # 更新RSI的Y轴范围
    if 'rsi' in selected_indicators:
        indicator_fig.update_yaxes(range=[0, 100], row=1, col=1)
    
    return kline_fig, indicator_fig

# ========== 更新当前价格回调 ==========
@app.callback(
    [Output('current-price', 'children'),
     Output('entry-price', 'value')],
    [Input('interval-component', 'n_intervals')]
)
def update_current_price(n):
    global current_price
    if len(kline_history) > 0:
        current_price = kline_history[-1]["收盘价"]
        return f"{current_price:.2f}", current_price
    return "等待数据...", 0

# ========== DeepSeek 分析回调 ==========
@app.callback(
    [Output('deepseek-chat-box', 'children'),
     Output('loading-analysis', 'type')],
    [Input('analyze-button', 'n_clicks'),
     Input('buy-analyze-button', 'n_clicks')],
    [State('deepseek-chat-box', 'children'),
     State('entry-price', 'value'),
     State('position-direction', 'value'),
     State('leverage', 'value'),
     State('position-size', 'value')]
)
def analyze(n_clicks, buy_clicks, previous_content, entry_price, position_direction, leverage, position_size):
    global chat_history, kline_history
    
    # 获取触发回调的按钮
    ctx = callback_context
    if not ctx.triggered:
        return previous_content or '点击按钮获取分析结果', 'circle'
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    # 检查按钮点击状态
    if button_id == 'analyze-button' and (n_clicks is None or n_clicks == 0):
        return previous_content or '点击"获取交易建议"按钮以获取分析结果', 'circle'
    elif button_id == 'buy-analyze-button' and (buy_clicks is None or buy_clicks == 0):
        return previous_content or '点击"获取买入建议"按钮以获取分析结果', 'circle'
    
    # 检查数据是否足够
    if len(kline_history) < 14:
        return f"数据量不足，请等待更多数据收集后再试（当前：{len(kline_history)}根K线，需要至少14根）", 'circle'
    
    try:
        # 获取最近20根K线数据
        df = pd.DataFrame(kline_history[-20:])
        
        # 计算技术指标
        df = TechnicalIndicators.calculate_all_indicators(df)
        
        # 获取最新的技术指标值
        latest = df.iloc[-1]
        
        # 构建更详细的分析提示
        kline_data = "\n".join([
            f"时间: {row['时间']}，开盘: {row['开盘价']:.2f}，高: {row['最高价']:.2f}，低: {row['最低价']:.2f}，收: {row['收盘价']:.2f}"
            for _, row in df.iterrows()
        ])
        
        # 根据按钮类型构建不同的提示信息
        if button_id == 'buy-analyze-button':
            # 构建买入建议的提示
            position_info = f"""
计划买入状态：
- 计划买入价格: {entry_price:.2f}
- 当前市场价格: {current_price:.2f}
- 计划杠杆倍数: {leverage}x
- 计划买入金额: {position_size:.2f} USDT
"""
            analysis_type = "买入"
            analysis_points = """
1. 当前趋势判断
2. 支撑位和阻力位
3. 超买超卖情况
4. 买入时机分析
5. 具体的买入建议（包括买入价格、止损位、止盈位等）
"""
        else:
            # 构建持仓分析的提示
            position_info = f"""
当前持仓状态：
- 开仓价格: {entry_price:.2f}
- 当前价格: {current_price:.2f}
- 仓位方向: {'多仓' if position_direction == 'long' else '空仓'}
- 杠杆倍数: {leverage}x
- 持仓数量: {position_size:.2f} USDT
"""
            analysis_type = "持仓"
            analysis_points = """
1. 当前趋势判断
2. 支撑位和阻力位
3. 超买超卖情况
4. 持仓盈亏分析
5. 具体的交易建议（包括是否继续持仓、止损位、止盈位等）
"""
        
        # 添加技术指标信息
        indicators_info = f"""
当前技术指标状态：
- RSI: {latest['RSI']:.2f}
- MACD: {latest['MACD']:.2f}
- Signal: {latest['Signal']:.2f}
- MA5: {latest['MA5']:.2f}
- MA10: {latest['MA10']:.2f}
- MA20: {latest['MA20']:.2f}
- MA30: {latest['MA30']:.2f}
"""
        
        prompt = f"""
最近20根BTC/USDT K线数据：
{kline_data}

{position_info}

{indicators_info}

请基于以上数据和技术指标状态，分析当前市场情况并给出具体的{analysis_type}建议。
分析时请考虑：
{analysis_points}
"""
        
        # 调用API获取分析结果
        logging.info("开始调用 DeepSeek API...")
        result = deepseek_api_call(prompt)
        logging.info(f"DeepSeek API 返回结果：\n{result}")
        
        # 记录时间戳和结果
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        chat_entry = f"[{timestamp}] {analysis_type}分析结果：\n{result}\n"
        
        # 只保留最新的分析结果
        chat_history = [chat_entry]
        
        return chat_entry, 'circle'
        
    except Exception as e:
        logging.error(f"分析失败: {str(e)}")
        return f"分析过程中出现错误：{str(e)}\n请稍后重试", 'circle'

# ========== 菜单折叠回调 ==========
@app.callback(
    [Output('settings-content', 'style'),
     Output('position-content', 'style'),
     Output('settings-arrow', 'style'),
     Output('position-arrow', 'style')],
    [Input('settings-button', 'n_clicks'),
     Input('position-button', 'n_clicks')]
)
def toggle_menu(settings_clicks, position_clicks):
    ctx = callback_context
    if not ctx.triggered:
        return (
            {"maxHeight": "0", "overflow": "hidden", "opacity": "0", "transition": "all 0.3s ease-in-out"},
            {"maxHeight": "0", "overflow": "hidden", "opacity": "0", "transition": "all 0.3s ease-in-out"},
            {"marginLeft": "auto", "transition": "transform 0.3s ease", "transform": "rotate(0deg)"},
            {"marginLeft": "auto", "transition": "transform 0.3s ease", "transform": "rotate(0deg)"}
        )
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if button_id == 'settings-button':
        settings_style = {
            "maxHeight": "500px" if settings_clicks % 2 == 1 else "0",
            "overflow": "hidden",
            "opacity": "1" if settings_clicks % 2 == 1 else "0",
            "transition": "all 0.3s ease-in-out"
        }
        settings_arrow = {
            "marginLeft": "auto",
            "transition": "transform 0.3s ease",
            "transform": "rotate(180deg)" if settings_clicks % 2 == 1 else "rotate(0deg)"
        }
        return (
            settings_style,
            {"maxHeight": "0", "overflow": "hidden", "opacity": "0", "transition": "all 0.3s ease-in-out"},
            settings_arrow,
            {"marginLeft": "auto", "transition": "transform 0.3s ease", "transform": "rotate(0deg)"}
        )
    else:
        position_style = {
            "maxHeight": "500px" if position_clicks % 2 == 1 else "0",
            "overflow": "hidden",
            "opacity": "1" if position_clicks % 2 == 1 else "0",
            "transition": "all 0.3s ease-in-out"
        }
        position_arrow = {
            "marginLeft": "auto",
            "transition": "transform 0.3s ease",
            "transform": "rotate(180deg)" if position_clicks % 2 == 1 else "rotate(0deg)"
        }
        return (
            {"maxHeight": "0", "overflow": "hidden", "opacity": "0", "transition": "all 0.3s ease-in-out"},
            position_style,
            {"marginLeft": "auto", "transition": "transform 0.3s ease", "transform": "rotate(0deg)"},
            position_arrow
        )

# ========== 启动应用 ==========
def start_ws():
    global ws
    ws = WebSocketApp(WS_URL, on_message=on_message, on_error=on_error, 
                      on_close=on_close, on_open=on_open)
    ws.run_forever()

def cleanup_data():
    """清理残留数据"""
    try:
        # 清理历史数据文件
        if os.path.exists('kline_history.json'):
            os.remove('kline_history.json')
            logging.info("已清理历史数据文件")
        
        # 清理日志文件
        if os.path.exists('app.log'):
            os.remove('app.log')
            logging.info("已清理日志文件")
        
        # 清理缓存文件
        if os.path.exists('__pycache__'):
            import shutil
            shutil.rmtree('__pycache__')
            logging.info("已清理缓存文件")
        
        # 清理CSV文件
        if os.path.exists('btc_1min_kline.csv'):
            os.remove('btc_1min_kline.csv')
            logging.info("已清理CSV文件")
        
        # 重置全局变量
        global kline_history, chat_history, has_data, current_price, login_attempts
        kline_history = []
        chat_history = []
        has_data = False
        current_price = 0
        login_attempts = {}
        logging.info("已重置全局变量")
        
    except Exception as e:
        logging.error(f"清理数据时出错: {e}")

if __name__ == '__main__':
    # 清理残留数据
    cleanup_data()
    
    # 获取历史数据
    if fetch_historical_data():
        logging.info("历史数据获取成功，开始实时数据收集")
    else:
        logging.warning("历史数据获取失败，将只收集实时数据")
    
    # 加载历史数据
    load_data()
    
    # 启动WebSocket线程
    ws_thread = threading.Thread(target=start_ws, daemon=True)
    ws_thread.start()
    
    # 启动Dash应用
    app.run_server(
        debug=True,
        host='0.0.0.0',
        port=8050
    )
