# 访问令牌配置
ACCESS_TOKEN = "mogu11223344"  # 访问密码

# 允许访问的IP地址列表（可选）
ALLOWED_IPS = []  # 空列表表示允许所有IP访问

# 令牌有效期（秒）
TOKEN_EXPIRY = 24 * 60 * 60  # 24小时

# 安全配置
SECURITY_CONFIG = {
    "MAX_LOGIN_ATTEMPTS": 5,  # 最大登录尝试次数
    "LOGIN_TIMEOUT": 300,     # 登录超时时间（秒）
    "SESSION_TIMEOUT": 3600,  # 会话超时时间（秒）
    "ENABLE_HTTPS": True,     # 是否启用HTTPS
    "SSL_CERT_PATH": "cert.pem",  # SSL证书路径
    "SSL_KEY_PATH": "key.pem",    # SSL密钥路径
}

# 服务器配置
SERVER_CONFIG = {
    "HOST": "0.0.0.0",        # 监听所有网络接口
    "PORT": 8050,             # 服务端口
    "DEBUG": True,            # 开启调试模式
    "WORKERS": 4,             # 工作进程数
} 