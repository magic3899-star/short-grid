#!/usr/bin/env python3
"""
바이낸스 선물 물타기 봇 - Flask 웹서버
Python으로 완전 구현 (볼린저밴드, 그리드 주문, 차트, 물타기)
"""

import sys
import io
# Windows cp949 인코딩 문제 해결
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import threading
import time
import hmac
import hashlib
import requests
import json
import os
import numpy as np
from datetime import datetime, timedelta
import websocket

# ==================== 설정 ====================
try:
    from config import (
        BINANCE_API_KEY, BINANCE_API_SECRET,
        AVG_INTERVAL, AVG_TP_INTERVAL, AVG_AMOUNT, CHECK_INTERVAL
    )
except ImportError:
    BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
    BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')
    AVG_INTERVAL = 2
    AVG_TP_INTERVAL = 2
    AVG_AMOUNT = 500
    CHECK_INTERVAL = 30

API_KEY = BINANCE_API_KEY
API_SECRET = BINANCE_API_SECRET
BASE_URL = 'https://fapi.binance.com'
STATE_FILE = 'averaging_state.json'
WATCHLIST_FILE = 'watchlist.json'
TRADES_FILE = 'trades.json'  # 매매 기록 파일

# Flask 앱
app = Flask(__name__, static_folder='static')
CORS(app)

# 전역 상태
averaging_bots = {}
server_time_offset = 0
logs = []
watchlist = []  # 코인 워치리스트
coin_data = {}  # 코인별 데이터 (status, bb 등)
exchange_info = None  # 거래소 정보 캐시
bb_cache = {}  # 볼린저밴드 캐시
trades = []  # 매매 기록
realtime_prices = {}  # 웹소켓 실시간 가격
ws_connected = False  # 웹소켓 연결 상태

# 그리드 설정
grid_settings = {
    'amount': 100,      # 주문당 금액
    'count': 3,         # 추가 주문 개수
    'interval': 5,      # 추가 주문 간격 %
    'entry_offset': 0,  # 1차 진입 오프셋 %
    'leverage': 10      # 레버리지
}

# ==================== 유틸리티 ====================
def log(message, level='info'):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prefix = {'info': 'ℹ️', 'success': '✅', 'warning': '⚠️', 'error': '❌'}.get(level, 'ℹ️')
    log_entry = {'time': timestamp, 'level': level, 'message': message}
    logs.append(log_entry)
    if len(logs) > 500:
        logs.pop(0)
    print(f'[{timestamp}] {prefix} {message}', flush=True)

def get_server_time():
    global server_time_offset
    try:
        res = requests.get(f'{BASE_URL}/fapi/v1/time', timeout=5)
        server_time = res.json()['serverTime']
        local_time = int(time.time() * 1000)
        server_time_offset = server_time - local_time
        return server_time
    except:
        return int(time.time() * 1000) + server_time_offset

def sign_request(params):
    query = '&'.join([f"{k}={v}" for k, v in params.items()])
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + '&signature=' + sig

def api_request(method, endpoint, params=None, signed=False):
    headers = {'X-MBX-APIKEY': API_KEY}
    url = BASE_URL + endpoint
    if params is None:
        params = {}

    if signed:
        params['timestamp'] = get_server_time()
        params['recvWindow'] = 60000
        url += '?' + sign_request(params)
        params = None

    try:
        if method == 'GET':
            res = requests.get(url, headers=headers, params=params, timeout=10)
        elif method == 'POST':
            res = requests.post(url, headers=headers, params=params, timeout=10)
        elif method == 'DELETE':
            res = requests.delete(url, headers=headers, params=params, timeout=10)
        return res.json()
    except Exception as e:
        log(f'API 오류: {e}', 'error')
        return None

def round_step(value, precision):
    return round(value, precision)

def round_tick(value, tick_size, precision):
    return round(round(value / tick_size) * tick_size, precision)

# ==================== 바이낸스 API ====================
def is_hedge_mode():
    result = api_request('GET', '/fapi/v1/positionSide/dual', {}, signed=True)
    return result.get('dualSidePosition', False) if result else False

def get_exchange_info():
    global exchange_info
    if exchange_info is None:
        try:
            res = requests.get(f'{BASE_URL}/fapi/v1/exchangeInfo', timeout=10)
            exchange_info = res.json()
        except:
            pass
    return exchange_info

def get_all_symbols():
    """USDT 무기한 선물 심볼 목록"""
    info = get_exchange_info()
    if info:
        return [s for s in info['symbols']
                if s['symbol'].endswith('USDT')
                and s['contractType'] == 'PERPETUAL'
                and s['status'] == 'TRADING']
    return []

def get_recent_listings(limit=50):
    """최근 신규상장 종목 조회 (onboardDate 기준)"""
    info = get_exchange_info()
    if not info:
        return []

    symbols_with_date = []
    now = datetime.now()

    for s in info['symbols']:
        # USDT 페어, 거래중, 무기한 선물만
        if (s.get('quoteAsset') == 'USDT' and
            s.get('status') == 'TRADING' and
            s.get('contractType') == 'PERPETUAL'):

            onboard = s.get('onboardDate', 0)
            if onboard:
                dt = datetime.fromtimestamp(onboard / 1000)
                days_ago = (now - dt).days
                symbols_with_date.append({
                    'symbol': s['symbol'],
                    'onboard_date': dt.strftime('%Y-%m-%d %H:%M'),
                    'onboard_timestamp': onboard,
                    'days_ago': days_ago
                })

    # 최근 상장순 정렬
    symbols_with_date.sort(key=lambda x: x['onboard_timestamp'], reverse=True)

    return symbols_with_date[:limit]

def get_symbol_info(symbol):
    info = get_exchange_info()
    if info:
        for s in info['symbols']:
            if s['symbol'] == symbol:
                return s
    return None

def get_all_positions():
    result = api_request('GET', '/fapi/v2/positionRisk', {}, signed=True)
    if result:
        return [p for p in result if float(p.get('positionAmt', 0)) != 0]
    return []

def get_position(symbol):
    result = api_request('GET', '/fapi/v2/positionRisk', {'symbol': symbol}, signed=True)
    if result:
        for p in result:
            if p['symbol'] == symbol and p.get('positionSide') == 'SHORT':
                return p
        for p in result:
            if p['symbol'] == symbol and float(p.get('positionAmt', 0)) < 0:
                return p
    return None

def get_open_orders(symbol=None):
    params = {'symbol': symbol} if symbol else {}
    result = api_request('GET', '/fapi/v1/openOrders', params, signed=True)
    return result if result else []

def get_price(symbol):
    try:
        res = requests.get(f'{BASE_URL}/fapi/v1/ticker/price', params={'symbol': symbol}, timeout=5)
        return float(res.json()['price'])
    except:
        return 0

def get_all_prices():
    """실시간 가격 반환 (웹소켓 우선, 없으면 REST API)"""
    if realtime_prices:
        return realtime_prices.copy()
    try:
        res = requests.get(f'{BASE_URL}/fapi/v1/ticker/price', timeout=5)
        return {p['symbol']: float(p['price']) for p in res.json()}
    except:
        return {}

def get_klines(symbol, interval='4h', limit=100):
    """캔들 데이터"""
    try:
        res = requests.get(f'{BASE_URL}/fapi/v1/klines',
                          params={'symbol': symbol, 'interval': interval, 'limit': limit},
                          timeout=10)
        return res.json()
    except:
        return []

def create_order(symbol, side, order_type, quantity, price=None, reduce_only=False, trade_type='manual', note=None):
    params = {'symbol': symbol, 'side': side, 'type': order_type, 'quantity': quantity}
    if is_hedge_mode():
        params['positionSide'] = 'SHORT'
    if price:
        params['price'] = price
        params['timeInForce'] = 'GTC'
    if reduce_only:
        params['reduceOnly'] = 'true'

    result = api_request('POST', '/fapi/v1/order', params, signed=True)
    if result and 'orderId' in result:
        log(f'{symbol} 주문: {side} {quantity} @ {price}', 'success')
        # 매매 기록 저장
        record_trade(
            trade_type=trade_type,
            symbol=symbol,
            side=side,
            quantity=float(quantity),
            price=float(price) if price else float(result.get('avgPrice', 0)),
            order_id=result.get('orderId'),
            note=note
        )
        return result
    log(f'{symbol} 주문 실패: {result}', 'error')
    return None

def cancel_order(symbol, order_id):
    result = api_request('DELETE', '/fapi/v1/order', {'symbol': symbol, 'orderId': order_id}, signed=True)
    if result and 'orderId' in result:
        log(f'{symbol} 취소: {order_id}', 'info')
    return result

def cancel_all_orders(symbol):
    for order in get_open_orders(symbol):
        cancel_order(symbol, order['orderId'])

def cancel_orders_by_ids(symbol, order_ids):
    """특정 주문 ID만 취소 (물타기 봇 전용)"""
    cancelled = []
    open_orders = get_open_orders(symbol)
    open_order_ids = {o['orderId'] for o in open_orders}

    for order_id in order_ids:
        if order_id in open_order_ids:
            result = cancel_order(symbol, order_id)
            if result:
                cancelled.append(order_id)
    return cancelled

def set_leverage(symbol, leverage):
    result = api_request('POST', '/fapi/v1/leverage', {'symbol': symbol, 'leverage': leverage}, signed=True)
    return result

# ==================== 볼린저밴드 ====================
def calculate_bollinger_bands(closes, period=20, multiplier=2):
    """볼린저밴드 계산"""
    if len(closes) < period:
        return None

    closes = np.array(closes, dtype=float)
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])

    upper = sma + (multiplier * std)
    lower = sma - (multiplier * std)

    return {
        'upper': upper,
        'middle': sma,
        'lower': lower,
        'current': closes[-1]
    }

def check_bb_position(symbol, interval='4h'):
    """볼린저밴드 위치 체크 (상단 근처면 True)"""
    cache_key = f'{symbol}_{interval}'
    now = time.time()

    # 캐시 확인 (5분)
    if cache_key in bb_cache:
        cached = bb_cache[cache_key]
        if now - cached['time'] < 300:
            return cached['result']

    klines = get_klines(symbol, interval, 50)
    if not klines:
        return None

    closes = [float(k[4]) for k in klines]
    bb = calculate_bollinger_bands(closes)

    if bb is None:
        return None

    # 상단 98% 이상이면 True
    range_size = bb['upper'] - bb['lower']
    position = (bb['current'] - bb['lower']) / range_size if range_size > 0 else 0.5

    result = {
        'upper': position >= 0.98,
        'position': position,
        'bb': bb
    }

    bb_cache[cache_key] = {'time': now, 'result': result}
    return result

def scan_bb_upper():
    """볼린저밴드 상단 코인 스캔"""
    result = []
    prices = get_all_prices()

    for symbol in watchlist:
        if symbol not in prices:
            continue

        # 15분봉 + 4시간봉 체크
        bb_15m = check_bb_position(symbol, '15m')
        bb_4h = check_bb_position(symbol, '4h')

        if bb_15m and bb_4h and bb_15m.get('upper') and bb_4h.get('upper'):
            result.append({
                'symbol': symbol,
                'price': prices[symbol],
                'bb_15m': bb_15m['position'],
                'bb_4h': bb_4h['position']
            })

    return result

# ==================== 상태 저장/로드 ====================
def save_state():
    data = {}
    for symbol, bot in averaging_bots.items():
        data[symbol] = bot.state
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log(f'상태 저장 실패: {e}', 'error')

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

# ==================== 매매 기록 저장/로드 ====================
def save_trades():
    """매매 기록 파일에 저장"""
    try:
        with open(TRADES_FILE, 'w') as f:
            json.dump(trades, f, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        log(f'매매 기록 저장 실패: {e}', 'error')

def load_trades():
    """매매 기록 파일에서 로드"""
    global trades
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, 'r') as f:
                trades = json.load(f)
                log(f'매매 기록 로드: {len(trades)}건', 'info')
        except:
            trades = []
    return trades

def record_trade(trade_type, symbol, side, quantity, price, order_id=None, pnl=None, note=None):
    """매매 기록 추가
    trade_type: 'grid_entry', 'grid_add', 'averaging_short', 'averaging_tp', 'manual', 'cancel'
    """
    trade = {
        'id': len(trades) + 1,
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'type': trade_type,
        'symbol': symbol,
        'side': side,
        'quantity': quantity,
        'price': price,
        'value': quantity * price if quantity and price else 0,
        'order_id': order_id,
        'pnl': pnl,
        'note': note
    }
    trades.append(trade)
    save_trades()
    log(f'매매 기록: {trade_type} {symbol} {side} {quantity}@{price}', 'info')
    return trade

def save_watchlist():
    try:
        with open(WATCHLIST_FILE, 'w') as f:
            json.dump({'watchlist': watchlist, 'coin_data': coin_data}, f, indent=2)
    except Exception as e:
        log(f'워치리스트 저장 실패: {e}', 'error')

def load_watchlist():
    global watchlist, coin_data
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, 'r') as f:
                data = json.load(f)
                watchlist = data.get('watchlist', [])
                coin_data = data.get('coin_data', {})
        except:
            pass

# ==================== 물타기 봇 ====================
class AveragingBot:
    def __init__(self, symbol, avg_interval=AVG_INTERVAL, avg_tp_interval=AVG_TP_INTERVAL, avg_amount=AVG_AMOUNT):
        self.symbol = symbol
        self.avg_interval = avg_interval
        self.avg_tp_interval = avg_tp_interval
        self.avg_amount = avg_amount
        self._lock = threading.Lock()  # 동시성 제어용 락

        info = get_symbol_info(symbol)
        if not info:
            raise Exception(f'{symbol} 심볼 정보 없음')

        self.price_precision = info['pricePrecision']
        self.qty_precision = info['quantityPrecision']
        self.tick_size = float([f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER'][0]['tickSize'])
        self.min_qty = float([f for f in info['filters'] if f['filterType'] == 'LOT_SIZE'][0]['minQty'])

        self.state = {
            'is_active': False,
            'start_qty': 0,
            'start_entry': 0,
            'last_qty': 0,
            'last_entry': 0,
            'short_order_price': 0,
            'realized_profit': 0,
            'tp_count': 0,
            'entries': [],
            'avg_interval': avg_interval,
            'avg_tp_interval': avg_tp_interval,
            'avg_amount': avg_amount,
            'order_ids': []  # 물타기 봇이 생성한 주문 ID 목록
        }

    def start(self, start_qty=None, start_entry=None):
        position = get_position(self.symbol)
        if not position or float(position['positionAmt']) >= 0:
            log(f'{self.symbol} 숏 포지션 없음', 'error')
            return False

        pos_qty = abs(float(position['positionAmt']))
        entry_price = float(position['entryPrice'])
        current_price = get_price(self.symbol)  # 현재 시장가

        # 기준가 = 현재 시장가 (사용자가 입력하면 그 값 사용)
        base_price = start_entry if start_entry else current_price

        self.state.update({
            'is_active': True,
            'start_qty': pos_qty,
            'start_entry': base_price,
            'last_qty': pos_qty,
            'last_entry': entry_price,
            'short_order_price': base_price * (1 + self.avg_interval / 100),
            'entries': [],
            'order_ids': []
        })

        log(f'{self.symbol} 물타기 시작 - 기준가: ${base_price:.4f}, 수량: {pos_qty:.0f}, 간격: {self.avg_interval}%', 'success')

        # 처음 시작 시 항상 숏 주문만 배치
        self.place_short_order()

        save_state()
        return True

    def stop(self):
        self.cancel_my_orders()  # 물타기 봇 주문만 취소
        self.state['is_active'] = False
        save_state()
        log(f'{self.symbol} 물타기 정지', 'info')

    def get_my_open_orders(self):
        """내가 생성한 오픈 주문만 반환"""
        orders = get_open_orders(self.symbol)
        my_order_ids = set(self.state.get('order_ids', []))
        return [o for o in orders if o.get('orderId') in my_order_ids and o.get('positionSide') == 'SHORT']

    def has_pending_order(self, side):
        """해당 방향(SELL/BUY)의 대기 주문이 있는지 확인"""
        orders = get_open_orders(self.symbol)
        for order in orders:
            if order.get('side') == side:
                return True
        return False

    def place_short_order(self):
        """숏 주문 배치"""
        # 기존 SELL 주문 있는지 확인 (positionSide 관계없이)
        orders = get_open_orders(self.symbol)
        for order in orders:
            if order.get('side') == 'SELL':
                log(f'{self.symbol} SELL 주문 이미 존재 - 스킵', 'info')
                return

        price = round_tick(self.state['start_entry'] * (1 + self.avg_interval / 100), self.tick_size, self.price_precision)
        qty = max(round_step((self.avg_amount * 2) / price, self.qty_precision), self.min_qty)
        result = create_order(self.symbol, 'SELL', 'LIMIT', qty, price,
                             trade_type='averaging_short', note=f'물타기 숏 (+{self.avg_interval}%)')
        if result and 'orderId' in result:
            self.state['short_order_price'] = price
            self.state['order_ids'].append(result['orderId'])
            save_state()
            log(f'{self.symbol} 숏 주문: {qty}개 @ ${price:.4f} (기준가+{self.avg_interval}%)', 'success')

    def place_tp_order(self, entry_price=None):
        """익절 주문 배치 - 물타기 진입가 기준으로 익절"""
        # 기존 BUY 주문 있는지 확인 (positionSide 관계없이)
        orders = get_open_orders(self.symbol)
        for order in orders:
            if order.get('side') == 'BUY':
                log(f'{self.symbol} BUY 주문 이미 존재 - 스킵', 'info')
                return

        position = get_position(self.symbol)
        if not position:
            return
        pos_qty = abs(float(position['positionAmt']))
        added_qty = pos_qty - self.state['start_qty']
        if added_qty <= self.min_qty:
            log(f'{self.symbol} 물타기 수량 부족: {added_qty}', 'warning')
            return

        # 익절가 계산: 물타기 진입가에서 -2% (포지션 평균가 아님!)
        if entry_price is None:
            # entries에서 물타기 진입가 가져오기
            if self.state.get('entries'):
                entry_price = self.state['entries'][-1]['price']
            else:
                # entries 없으면 short_order_price 사용 (물타기 숏 주문가)
                entry_price = self.state.get('short_order_price', float(position['entryPrice']))

        tp_price = round_tick(entry_price * (1 - self.avg_tp_interval / 100), self.tick_size, self.price_precision)
        tp_qty = round_step(added_qty, self.qty_precision)
        result = create_order(self.symbol, 'BUY', 'LIMIT', tp_qty, tp_price,
                    trade_type='averaging_tp', note=f'물타기 익절 (-{self.avg_tp_interval}%)')
        if result and 'orderId' in result:
            self.state['order_ids'].append(result['orderId'])
            log(f'{self.symbol} 익절 주문 생성: {tp_qty} @ ${tp_price:.4f} (진입가 ${entry_price:.4f})', 'success')
        save_state()

    def force_tp_order(self):
        position = get_position(self.symbol)
        if not position:
            return False
        pos_qty = abs(float(position['positionAmt']))
        added_qty = pos_qty - self.state.get('start_qty', 0)
        last_entry = (self.state['entries'][-1]['price'] if self.state.get('entries')
                     else self.state.get('short_order_price', float(position['entryPrice'])))
        tp_price = round_tick(last_entry * (1 - self.avg_tp_interval / 100), self.tick_size, self.price_precision)
        if added_qty > self.min_qty:
            tp_qty = round_step(added_qty, self.qty_precision)
        else:
            tp_qty = max(round_step((self.avg_amount * 2) / tp_price, self.qty_precision), self.min_qty)
        result = create_order(self.symbol, 'BUY', 'LIMIT', tp_qty, tp_price,
                             trade_type='averaging_tp', note='강제 익절')
        return bool(result)

    def cancel_my_orders(self):
        """물타기 봇이 생성한 주문만 취소"""
        order_ids = self.state.get('order_ids', [])
        if order_ids:
            cancel_orders_by_ids(self.symbol, order_ids)
        self.state['order_ids'] = []
        save_state()

    def fix_tp_order(self):
        # 물타기 봇이 생성한 주문만 취소 (수동 주문은 유지)
        self.cancel_my_orders()
        position = get_position(self.symbol)
        if not position:
            return

        pos_qty = abs(float(position['positionAmt']))
        added_qty = pos_qty - self.state.get('start_qty', 0)

        if added_qty > self.min_qty:
            # 물타기 진입 상태 → 익절 주문만 (place_tp_order가 entries에서 진입가 자동 참조)
            self.place_tp_order()
            log(f'{self.symbol} 익절 주문 재배치 완료 (물타기 {added_qty:.0f}개)', 'success')
        else:
            # 물타기 진입 전 → 숏 주문만
            self.place_short_order()
            log(f'{self.symbol} 숏 주문 재배치 완료', 'success')

    def check_and_update(self):
        """포지션 변화 감지 및 주문 관리 - 락으로 동시 실행 방지"""
        if not self.state.get('is_active'):
            return

        # 락 획득 시도 (non-blocking)
        if not self._lock.acquire(blocking=False):
            log(f'{self.symbol} check_and_update 이미 실행 중 - 스킵', 'info')
            return

        try:
            position = get_position(self.symbol)
            if not position or float(position['positionAmt']) >= 0:
                log(f'{self.symbol} 포지션 청산됨 - 자동 정지', 'info')
                self.stop()
                return

            current_qty = abs(float(position['positionAmt']))
            current_entry = float(position['entryPrice'])
            current_price = get_price(self.symbol)
            last_qty = self.state.get('last_qty', current_qty)
            last_entry = self.state.get('last_entry', current_entry)
            qty_changed = abs(current_qty - last_qty) > self.min_qty

            if qty_changed:
                if current_qty > last_qty:
                    # 물타기 진입 감지
                    added_qty = current_qty - last_qty
                    added_price = (current_qty * current_entry - last_qty * last_entry) / added_qty
                    self.state['entries'].append({
                        'price': added_price,
                        'qty': added_qty,
                        'time': datetime.now().isoformat()
                    })
                    log(f'{self.symbol} 물타기 진입: ${added_price:.4f} × {added_qty:.0f}', 'success')
                    record_trade(
                        trade_type='averaging_short_filled',
                        symbol=self.symbol,
                        side='SELL',
                        quantity=added_qty,
                        price=added_price,
                        note=f'물타기 {len(self.state["entries"])}차 체결'
                    )
                    # 기존 숏 주문 ID 제거 (체결됐으므로)
                    self.state['order_ids'] = [oid for oid in self.state.get('order_ids', [])
                                               if self._is_order_still_open(oid)]
                    self.place_tp_order(added_price)
                elif current_qty < last_qty:
                    # 익절 감지
                    filled_qty = last_qty - current_qty
                    tp_profit = filled_qty * last_entry * (self.avg_tp_interval / 100)
                    self.state['realized_profit'] = self.state.get('realized_profit', 0) + tp_profit
                    self.state['tp_count'] = self.state.get('tp_count', 0) + 1
                    self.state['entries'] = []
                    log(f'{self.symbol} 익절: +${tp_profit:.2f} (총 {self.state["tp_count"]}회)', 'success')
                    record_trade(
                        trade_type='averaging_tp_filled',
                        symbol=self.symbol,
                        side='BUY',
                        quantity=filled_qty,
                        price=last_entry * (1 - self.avg_tp_interval / 100),
                        pnl=tp_profit,
                        note=f'익절 체결 ({self.state["tp_count"]}회차)'
                    )
                    self.state['start_entry'] = current_price
                    self.state['start_qty'] = current_qty
                    # 기존 익절 주문 ID 제거 (체결됐으므로)
                    self.state['order_ids'] = [oid for oid in self.state.get('order_ids', [])
                                               if self._is_order_still_open(oid)]
                    self.place_short_order()
                self.state['last_qty'] = current_qty
                self.state['last_entry'] = current_entry
                save_state()
            else:
                # 물타기 진입 상태 확인
                added_qty = current_qty - self.state.get('start_qty', current_qty)
                if added_qty > self.min_qty:
                    # 물타기 진입됨 → 익절 대기 상태, 가격 따라가기 안함
                    pass
                else:
                    # 물타기 진입 전 상태
                    # 1. 봇 숏 주문이 없으면 새로 배치
                    my_order_ids = set(self.state.get('order_ids', []))
                    orders = get_open_orders(self.symbol)
                    has_my_sell = any(o.get('orderId') in my_order_ids and o.get('side') == 'SELL'
                                     for o in orders)

                    if not has_my_sell:
                        log(f'{self.symbol} 숏 주문 없음 → 새로 배치', 'info')
                        self.place_short_order()
                        save_state()
                    else:
                        # 2. 가격 따라가기 로직
                        short_price = self.state.get('short_order_price', self.state['start_entry'] * (1 + self.avg_interval / 100))
                        threshold = short_price * (1 - self.avg_interval / 100)
                        if current_price < threshold:
                            log(f'{self.symbol} 가격 하락 → 따라가기 (${current_price:.4f})', 'info')
                            self.cancel_my_orders()
                            self.state['start_entry'] = current_price
                            self.place_short_order()
                            save_state()
        finally:
            self._lock.release()

    def _is_order_still_open(self, order_id):
        """주문이 아직 열려있는지 확인"""
        orders = get_open_orders(self.symbol)
        return any(o.get('orderId') == order_id for o in orders)

# ==================== 웹소켓 ====================
def on_ws_message(ws, message):
    """웹소켓 메시지 수신 (전체 티커)"""
    global realtime_prices
    try:
        data = json.loads(message)
        if isinstance(data, list):
            for item in data:
                if 's' in item and 'c' in item:
                    realtime_prices[item['s']] = float(item['c'])
    except Exception as e:
        pass

def on_ws_error(ws, error):
    global ws_connected
    ws_connected = False
    log(f'웹소켓 오류: {error}', 'error')

def on_ws_close(ws, close_status_code, close_msg):
    global ws_connected
    ws_connected = False
    log('웹소켓 연결 종료', 'warning')

def on_ws_open(ws):
    global ws_connected
    ws_connected = True
    log('웹소켓 연결됨 (실시간 가격)', 'success')

def websocket_stream():
    """바이낸스 선물 전체 티커 웹소켓"""
    global ws_connected
    WS_URL = 'wss://fstream.binance.com/ws/!ticker@arr'

    while True:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_message=on_ws_message,
                on_error=on_ws_error,
                on_close=on_ws_close,
                on_open=on_ws_open
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log(f'웹소켓 재연결 시도: {e}', 'warning')
        ws_connected = False
        time.sleep(5)  # 재연결 대기

# ==================== 백그라운드 스레드 ====================
def background_checker():
    while True:
        try:
            for symbol, bot in list(averaging_bots.items()):
                if bot.state.get('is_active'):
                    bot.check_and_update()
        except Exception as e:
            log(f'체크 오류: {e}', 'error')
        time.sleep(CHECK_INTERVAL)

def auto_listing_scanner():
    """4시간마다 신규상장 자동 스캔 및 워치리스트 추가"""
    SCAN_INTERVAL = 4 * 60 * 60  # 4시간
    MAX_DAYS = 30  # 30일 이내 상장 종목만

    while True:
        try:
            # exchange_info 캐시 갱신
            global exchange_info
            exchange_info = None
            get_exchange_info()

            # 신규상장 조회
            listings = get_recent_listings(100)
            recent = [l for l in listings if l['days_ago'] <= MAX_DAYS]

            added = []
            for item in recent:
                symbol = item['symbol']
                if symbol not in watchlist:
                    info = get_symbol_info(symbol)
                    if info:
                        watchlist.append(symbol)
                        coin_data[symbol] = {'status': 'watching', 'bb_upper': False}
                        added.append(symbol)

            if added:
                save_watchlist()
                log(f'신규상장 자동추가: {len(added)}개 ({", ".join(added[:5])}{"..." if len(added) > 5 else ""})', 'success')
            else:
                log(f'신규상장 스캔 완료 - 새 종목 없음 (총 {len(watchlist)}개)', 'info')

        except Exception as e:
            log(f'신규상장 스캔 오류: {e}', 'error')

        time.sleep(SCAN_INTERVAL)

# ==================== Flask API ====================
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)

# --- 상태 ---
@app.route('/api/status')
def api_status():
    return jsonify({
        'status': 'ok',
        'api_connected': bool(API_KEY and API_SECRET),
        'ws_connected': ws_connected,
        'realtime_prices': len(realtime_prices),
        'averaging_count': len([b for b in averaging_bots.values() if b.state.get('is_active')]),
        'watchlist_count': len(watchlist),
        'server_time': datetime.now().isoformat()
    })

# --- 포지션 ---
@app.route('/api/positions')
def api_positions():
    return jsonify(get_all_positions())

@app.route('/api/position/<symbol>')
def api_position(symbol):
    pos = get_position(symbol)
    return jsonify(pos) if pos else (jsonify({'error': '포지션 없음'}), 404)

# --- 주문 ---
@app.route('/api/orders')
def api_orders():
    symbol = request.args.get('symbol')
    return jsonify(get_open_orders(symbol))

@app.route('/api/order/create', methods=['POST'])
def api_order_create():
    data = request.json or {}
    result = create_order(data.get('symbol'), data.get('side'), data.get('type', 'LIMIT'),
                         data.get('quantity'), data.get('price'), data.get('reduceOnly', False))
    return jsonify(result) if result else (jsonify({'error': '주문 실패'}), 400)

@app.route('/api/order/cancel', methods=['POST'])
def api_order_cancel():
    data = request.json or {}
    result = cancel_order(data.get('symbol'), data.get('orderId'))
    return jsonify(result) if result else (jsonify({'error': '취소 실패'}), 400)

@app.route('/api/order/cancel_all', methods=['POST'])
def api_order_cancel_all():
    data = request.json or {}
    symbol = data.get('symbol')
    if symbol:
        cancel_all_orders(symbol)
        return jsonify({'success': True})
    return jsonify({'error': '심볼 필요'}), 400

# --- 가격 ---
@app.route('/api/prices')
def api_prices():
    return jsonify(get_all_prices())

@app.route('/api/price/<symbol>')
def api_price(symbol):
    return jsonify({'symbol': symbol, 'price': get_price(symbol)})

# --- 차트 ---
@app.route('/api/klines/<symbol>')
def api_klines(symbol):
    interval = request.args.get('interval', '4h')
    limit = request.args.get('limit', 100, type=int)
    klines = get_klines(symbol, interval, limit)
    # OHLCV 형식으로 변환
    result = []
    for k in klines:
        result.append({
            'time': k[0] // 1000,
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5])
        })
    return jsonify(result)

# --- 볼린저밴드 ---
@app.route('/api/bb/<symbol>')
def api_bb(symbol):
    interval = request.args.get('interval', '4h')
    result = check_bb_position(symbol, interval)
    return jsonify(result) if result else (jsonify({'error': '데이터 없음'}), 404)

@app.route('/api/bb/scan')
def api_bb_scan():
    result = scan_bb_upper()
    return jsonify(result)

# --- 신규상장 ---
@app.route('/api/listings')
def api_listings():
    """최근 신규상장 종목 조회"""
    limit = request.args.get('limit', 50, type=int)
    result = get_recent_listings(limit)
    return jsonify(result)

@app.route('/api/listings/add_to_watchlist', methods=['POST'])
def api_listings_add_to_watchlist():
    """신규상장 종목을 워치리스트에 일괄 추가"""
    data = request.json or {}
    symbols = data.get('symbols', [])
    days = data.get('days')  # N일 이내 상장 종목만

    if not symbols and days is not None:
        # days가 지정되면 해당 기간 내 상장 종목 자동 선택
        listings = get_recent_listings(100)
        symbols = [l['symbol'] for l in listings if l['days_ago'] <= days]

    added = []
    skipped = []

    for symbol in symbols:
        symbol = symbol.upper()
        if not symbol.endswith('USDT'):
            symbol += 'USDT'

        if symbol in watchlist:
            skipped.append(symbol)
            continue

        info = get_symbol_info(symbol)
        if not info:
            skipped.append(symbol)
            continue

        watchlist.append(symbol)
        coin_data[symbol] = {'status': 'watching', 'bb_upper': False}
        added.append(symbol)

    if added:
        save_watchlist()
        log(f'신규상장 {len(added)}개 워치리스트 추가', 'success')

    return jsonify({
        'success': True,
        'added': added,
        'added_count': len(added),
        'skipped': skipped,
        'skipped_count': len(skipped)
    })

# --- 워치리스트 ---
@app.route('/api/watchlist')
def api_watchlist():
    prices = get_all_prices()

    # 포지션 보유 종목 확인
    position_symbols = set()
    try:
        positions = get_all_positions()
        for pos in positions:
            if pos and float(pos.get('positionAmt', 0)) != 0:
                position_symbols.add(pos['symbol'])
    except:
        pass

    result = []
    for symbol in watchlist:
        data = coin_data.get(symbol, {})

        # 포지션 보유 여부로 status 결정
        status = 'entered' if symbol in position_symbols else 'watching'

        # BB 상단 여부 체크 (15m + 4h 모두 상단)
        bb_upper = False
        try:
            bb_15m = check_bb_position(symbol, '15m')
            bb_4h = check_bb_position(symbol, '4h')
            if bb_15m and bb_4h and bb_15m.get('upper') and bb_4h.get('upper'):
                bb_upper = True
        except:
            pass

        result.append({
            'symbol': symbol,
            'price': prices.get(symbol, 0),
            'status': status,
            'bb_upper': bb_upper
        })
    return jsonify(result)

@app.route('/api/watchlist/add', methods=['POST'])
def api_watchlist_add():
    data = request.json or {}
    symbol = data.get('symbol', '').upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'

    if symbol in watchlist:
        return jsonify({'error': '이미 존재'}), 400

    # 심볼 유효성 검사
    info = get_symbol_info(symbol)
    if not info:
        return jsonify({'error': '유효하지 않은 심볼'}), 400

    watchlist.append(symbol)
    coin_data[symbol] = {'status': 'watching', 'bb_upper': False}
    save_watchlist()
    log(f'{symbol} 워치리스트 추가', 'success')
    return jsonify({'success': True, 'symbol': symbol})

@app.route('/api/watchlist/remove', methods=['POST'])
def api_watchlist_remove():
    data = request.json or {}
    symbol = data.get('symbol')
    if symbol in watchlist:
        watchlist.remove(symbol)
        if symbol in coin_data:
            del coin_data[symbol]
        save_watchlist()
        log(f'{symbol} 워치리스트 삭제', 'info')
        return jsonify({'success': True})
    return jsonify({'error': '없는 심볼'}), 400

@app.route('/api/watchlist/status', methods=['POST'])
def api_watchlist_status():
    data = request.json or {}
    symbol = data.get('symbol')
    status = data.get('status')
    if symbol in watchlist:
        if symbol not in coin_data:
            coin_data[symbol] = {}
        coin_data[symbol]['status'] = status
        save_watchlist()
        return jsonify({'success': True})
    return jsonify({'error': '없는 심볼'}), 400

# --- 그리드 주문 ---
@app.route('/api/grid/settings')
def api_grid_settings():
    return jsonify(grid_settings)

@app.route('/api/grid/settings', methods=['POST'])
def api_grid_settings_update():
    data = request.json or {}
    for key in ['amount', 'count', 'interval', 'entry_offset', 'leverage']:
        if key in data:
            grid_settings[key] = data[key]
    return jsonify(grid_settings)

@app.route('/api/grid/place', methods=['POST'])
def api_grid_place():
    """그리드 숏 주문 실행"""
    data = request.json or {}
    symbol = data.get('symbol')
    if not symbol:
        return jsonify({'error': '심볼 필요'}), 400

    info = get_symbol_info(symbol)
    if not info:
        return jsonify({'error': '심볼 정보 없음'}), 400

    price_precision = info['pricePrecision']
    qty_precision = info['quantityPrecision']
    tick_size = float([f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER'][0]['tickSize'])
    min_qty = float([f for f in info['filters'] if f['filterType'] == 'LOT_SIZE'][0]['minQty'])

    current_price = get_price(symbol)
    if current_price <= 0:
        return jsonify({'error': '가격 조회 실패'}), 400

    amount = data.get('amount', grid_settings['amount'])
    count = data.get('count', grid_settings['count'])
    interval = data.get('interval', grid_settings['interval'])
    entry_offset = data.get('entry_offset', grid_settings['entry_offset'])
    leverage = data.get('leverage', grid_settings['leverage'])

    # 레버리지 설정
    set_leverage(symbol, leverage)

    orders = []

    # 1차 진입
    entry_price = current_price * (1 + entry_offset / 100)
    entry_price = round_tick(entry_price, tick_size, price_precision)
    entry_qty = max(round_step(amount / entry_price, qty_precision), min_qty)

    if entry_offset == 0:
        # 시장가
        result = create_order(symbol, 'SELL', 'MARKET', entry_qty, trade_type='grid_entry', note='1차 진입 (시장가)')
    else:
        # 지정가
        result = create_order(symbol, 'SELL', 'LIMIT', entry_qty, entry_price, trade_type='grid_entry', note=f'1차 진입 (+{entry_offset}%)')

    if result:
        orders.append(result)
        # 상태 업데이트
        if symbol in coin_data:
            coin_data[symbol]['status'] = 'entered'
            save_watchlist()

    # 추가 주문 (1차 진입가 기준)
    for i in range(1, count + 1):
        add_price = entry_price * (1 + (interval * i) / 100)
        add_price = round_tick(add_price, tick_size, price_precision)
        add_qty = max(round_step(amount / add_price, qty_precision), min_qty)

        result = create_order(symbol, 'SELL', 'LIMIT', add_qty, add_price, trade_type='grid_add', note=f'추가 {i}차 (+{interval*i}%)')
        if result:
            orders.append(result)

    log(f'{symbol} 그리드 주문 완료: 1차 + {count}개', 'success')
    return jsonify({'success': True, 'orders': orders})

# --- 물타기 ---
@app.route('/api/averaging/list')
def api_averaging_list():
    result = {}
    for symbol, bot in averaging_bots.items():
        result[symbol] = {
            **bot.state,
            'current_price': get_price(symbol)
        }
    return jsonify(result)

@app.route('/api/averaging/start', methods=['POST'])
def api_averaging_start():
    data = request.json or {}
    symbol = data.get('symbol')
    if not symbol:
        return jsonify({'error': '심볼 필요'}), 400
    try:
        bot = AveragingBot(
            symbol,
            data.get('avg_interval', AVG_INTERVAL),
            data.get('avg_tp_interval', AVG_TP_INTERVAL),
            data.get('avg_amount', AVG_AMOUNT)
        )
        averaging_bots[symbol] = bot  # 먼저 등록해야 save_state()가 저장함
        if bot.start(data.get('start_qty'), data.get('start_entry')):
            return jsonify({'success': True, 'state': bot.state})
        else:
            del averaging_bots[symbol]  # 실패하면 제거
        return jsonify({'error': '시작 실패'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/averaging/stop', methods=['POST'])
def api_averaging_stop():
    data = request.json or {}
    symbol = data.get('symbol')
    if symbol in averaging_bots:
        averaging_bots[symbol].stop()
        del averaging_bots[symbol]
        return jsonify({'success': True})
    return jsonify({'error': '물타기 없음'}), 400

@app.route('/api/averaging/force_tp', methods=['POST'])
def api_averaging_force_tp():
    data = request.json or {}
    symbol = data.get('symbol')
    if symbol in averaging_bots:
        if averaging_bots[symbol].force_tp_order():
            return jsonify({'success': True})
    return jsonify({'error': '강제 익절 실패'}), 400

@app.route('/api/averaging/fix_tp', methods=['POST'])
def api_averaging_fix_tp():
    data = request.json or {}
    symbol = data.get('symbol')
    if symbol in averaging_bots:
        averaging_bots[symbol].fix_tp_order()
        return jsonify({'success': True})
    return jsonify({'error': '물타기 없음'}), 400

@app.route('/api/averaging/place_correct_tp', methods=['POST'])
def api_averaging_place_correct_tp():
    """물타기 진입량 기준으로 정확한 익절 주문 배치"""
    data = request.json or {}
    symbol = data.get('symbol')

    if symbol not in averaging_bots:
        return jsonify({'error': '물타기 없음'}), 400

    bot = averaging_bots[symbol]
    position = get_position(symbol)
    if not position:
        return jsonify({'error': '포지션 없음'}), 400

    pos_qty = abs(float(position['positionAmt']))
    start_qty = bot.state.get('start_qty', 0)
    entries = bot.state.get('entries', [])

    # 물타기로 추가된 수량 계산
    added_qty = pos_qty - start_qty

    if added_qty <= bot.min_qty:
        return jsonify({'error': f'물타기 수량 없음 (현재: {pos_qty}, 기준: {start_qty})'}), 400

    # 물타기 진입 평균가 계산
    if entries:
        total_value = sum(e['price'] * e['qty'] for e in entries)
        total_qty = sum(e['qty'] for e in entries)
        avg_entry = total_value / total_qty if total_qty > 0 else entries[-1]['price']
    else:
        avg_entry = bot.state.get('short_order_price', float(position['entryPrice']))

    # 기존 익절 주문 취소
    orders = get_open_orders(symbol)
    for order in orders:
        if order.get('side') == 'BUY' and order.get('positionSide') == 'SHORT':
            cancel_order(symbol, order['orderId'])
            log(f'{symbol} 기존 익절 주문 취소: {order["orderId"]}', 'info')

    # 새 익절 주문 배치
    tp_price = round_tick(avg_entry * (1 - bot.avg_tp_interval / 100), bot.tick_size, bot.price_precision)
    tp_qty = round_step(added_qty, bot.qty_precision)

    result = create_order(symbol, 'BUY', 'LIMIT', tp_qty, tp_price,
                trade_type='averaging_tp', note=f'물타기 익절 수정 ({len(entries)}차 기준)')

    if result and 'orderId' in result:
        bot.state['order_ids'].append(result['orderId'])
        save_state()
        return jsonify({
            'success': True,
            'tp_qty': tp_qty,
            'tp_price': tp_price,
            'avg_entry': avg_entry,
            'entries_count': len(entries),
            'order_id': result['orderId']
        })

    return jsonify({'error': '익절 주문 실패'}), 400

@app.route('/api/averaging/set_base', methods=['POST'])
def api_averaging_set_base():
    data = request.json or {}
    symbol = data.get('symbol')
    base_entry = data.get('base_entry')
    if symbol in averaging_bots and base_entry:
        averaging_bots[symbol].state['start_entry'] = float(base_entry)
        save_state()
        log(f'{symbol} 기준가 설정: ${base_entry}', 'success')
        return jsonify({'success': True})
    return jsonify({'error': '설정 실패'}), 400

@app.route('/api/averaging/state/<symbol>')
def api_averaging_state(symbol):
    if symbol in averaging_bots:
        return jsonify(averaging_bots[symbol].state)
    return jsonify({'error': '물타기 없음'}), 404

# --- 심볼 정보 ---
@app.route('/api/symbol/<symbol>')
def api_symbol_info(symbol):
    info = get_symbol_info(symbol)
    if info:
        return jsonify(info)
    return jsonify({'error': '심볼 없음'}), 404

# --- 로그 ---
@app.route('/api/logs')
def api_logs():
    limit = request.args.get('limit', 100, type=int)
    return jsonify(logs[-limit:])

# --- 매매 기록 ---
@app.route('/api/trades')
def api_trades():
    """매매 기록 조회"""
    symbol = request.args.get('symbol')
    trade_type = request.args.get('type')
    limit = request.args.get('limit', 100, type=int)

    result = trades
    if symbol:
        result = [t for t in result if t.get('symbol') == symbol]
    if trade_type:
        result = [t for t in result if t.get('type') == trade_type]

    return jsonify(result[-limit:])

@app.route('/api/trades/summary')
def api_trades_summary():
    """매매 기록 요약"""
    symbol = request.args.get('symbol')

    result = trades
    if symbol:
        result = [t for t in result if t.get('symbol') == symbol]

    total_pnl = sum(t.get('pnl', 0) or 0 for t in result)
    total_trades = len(result)
    grid_entries = len([t for t in result if t.get('type') == 'grid_entry'])
    avg_shorts = len([t for t in result if 'averaging_short' in t.get('type', '')])
    avg_tps = len([t for t in result if 'averaging_tp' in t.get('type', '')])

    return jsonify({
        'total_trades': total_trades,
        'total_pnl': total_pnl,
        'grid_entries': grid_entries,
        'averaging_shorts': avg_shorts,
        'averaging_tps': avg_tps,
        'symbols': list(set(t.get('symbol') for t in result if t.get('symbol')))
    })

@app.route('/api/trades/clear', methods=['POST'])
def api_trades_clear():
    """매매 기록 초기화"""
    global trades
    trades = []
    save_trades()
    log('매매 기록 초기화', 'info')
    return jsonify({'success': True})

# --- 설정 ---
@app.route('/api/config')
def api_config():
    return jsonify({
        'avg_interval': AVG_INTERVAL,
        'avg_tp_interval': AVG_TP_INTERVAL,
        'avg_amount': AVG_AMOUNT,
        'check_interval': CHECK_INTERVAL,
        'grid': grid_settings
    })

# ==================== 메인 ====================
if __name__ == '__main__':
    log('서버 시작 중...', 'info')

    # 서버 시간 동기화
    get_server_time()

    # 거래소 정보 로드
    get_exchange_info()

    # 워치리스트 로드
    load_watchlist()
    log(f'워치리스트 로드: {len(watchlist)}개', 'info')

    # 매매 기록 로드
    load_trades()

    # 물타기 상태 로드 (자동 복원 안함 - 사용자가 명시적으로 시작해야 함)
    saved = load_state()
    if saved:
        # 모든 물타기 상태를 비활성화로 초기화
        for symbol, state in saved.items():
            state['is_active'] = False
        save_state()
        log(f'물타기 상태 초기화 완료 (자동 복원 비활성화)', 'info')

    # 웹소켓 실시간 가격 스트림
    ws_thread = threading.Thread(target=websocket_stream, daemon=True)
    ws_thread.start()
    log('웹소켓 스트림 시작 (실시간 가격)', 'info')

    # 백그라운드 체커
    checker = threading.Thread(target=background_checker, daemon=True)
    checker.start()
    log(f'백그라운드 체커 시작 ({CHECK_INTERVAL}초 간격)', 'info')

    # 신규상장 자동 스캐너 (4시간마다)
    listing_scanner = threading.Thread(target=auto_listing_scanner, daemon=True)
    listing_scanner.start()
    log('신규상장 자동 스캐너 시작 (4시간 간격)', 'info')

    # Flask 서버
    log('웹서버: http://0.0.0.0:80', 'success')
    app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
