#!/usr/bin/env python3
"""
설정 파일 - API 키와 물타기 설정
"""

# ==================== API 설정 ====================
# 바이낸스 API 키
BINANCE_API_KEY = 'OUzVh5PB6lQ3KCL9j5w3af0ezkh9yD9UpxvPxHMGqzmdt8FcgjixAqBv9m7EKp0q'
BINANCE_API_SECRET = 'Gz1W4s14MUSOlZLAFLJILp55ZEGRfPjScEta3Sv7a3oszvKVW3uxaclEfFGYb1Qu'

# ==================== 물타기 설정 ====================
AVG_INTERVAL = 2      # 추가숏 간격 % (기준가 +2%)
AVG_TP_INTERVAL = 2   # 익절 간격 % (물타기 진입가 -2%)
AVG_AMOUNT = 500       # 물타기 금액 ($)

# ==================== 체크 간격 ====================
CHECK_INTERVAL = 30   # 초
