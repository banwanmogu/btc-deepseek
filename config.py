# API配置
DEEPSEEK_API_KEY = 'sk-449b142eed554ec5b16bd5a385b82907'
DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
DEEPSEEK_API_URL = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
MODEL = "deepseek-chat"

# WebSocket配置
WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"
MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒

# 数据配置
MAX_KLINE_HISTORY = 200
UPDATE_INTERVAL = 5000  # 毫秒 ss