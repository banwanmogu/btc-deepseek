import pandas as pd
import numpy as np

class TechnicalIndicators:
    @staticmethod
    def calculate_ma(df, periods=[5, 10, 20, 30]):
        """计算移动平均线"""
        for period in periods:
            df[f'MA{period}'] = df['收盘价'].rolling(window=period).mean()
        return df

    @staticmethod
    def calculate_rsi(df, period=14):
        """计算相对强弱指标(RSI)"""
        # 计算价格变化
        delta = df['收盘价'].diff()
        
        # 分离上涨和下跌
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        # 使用EMA计算平均上涨和下跌
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        
        # 计算相对强度
        rs = avg_gain / avg_loss.replace(0, float('inf'))  # 处理除数为0的情况
        
        # 计算RSI
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # 处理异常值
        df['RSI'] = df['RSI'].clip(0, 100)
        
        return df

    @staticmethod
    def calculate_macd(df, fast=12, slow=26, signal=9):
        """计算MACD指标"""
        exp1 = df['收盘价'].ewm(span=fast, adjust=False).mean()
        exp2 = df['收盘价'].ewm(span=slow, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['Signal']
        return df

    @staticmethod
    def calculate_bollinger_bands(df, period=20, std_dev=2):
        """计算布林带"""
        df['BB_Middle'] = df['收盘价'].rolling(window=period).mean()
        bb_std = df['收盘价'].rolling(window=period).std()
        df['BB_Upper'] = df['BB_Middle'] + (bb_std * std_dev)
        df['BB_Lower'] = df['BB_Middle'] - (bb_std * std_dev)
        return df

    @staticmethod
    def calculate_all_indicators(df):
        """计算所有技术指标"""
        df = TechnicalIndicators.calculate_ma(df)
        df = TechnicalIndicators.calculate_rsi(df)
        df = TechnicalIndicators.calculate_macd(df)
        df = TechnicalIndicators.calculate_bollinger_bands(df)
        return df 