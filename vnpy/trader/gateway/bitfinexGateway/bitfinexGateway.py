# encoding: UTF-8
'''
vnpy.api.bitfinex的gateway接入 对其中进行修改
v3  改进版的bitfinex 加载历史记录
v4 处理并且添加inintposition 用于实盘之中停止之后获取策略的仓位，即策略的仓位的维护作用
'''
from __future__ import print_function
import os
import json
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from copy import copy
from math import pow
import requests
import pandas as pd

from vnpy.api.bitfinex import BitfinexApi
from vnpy.trader.vtGateway import *
from vnpy.trader.vtConstant import *
from vnpy.trader.vtFunction import getJsonPath, getTempPath
from vnpy.trader.app.ctaStrategy.ctaBase import EVENT_CTA_LOG
from vnpy.trader.vtObject import *
from vnpy.trader.app.ctaStrategy.ctaBase import *


# 委托状态类型映射这里委托状态额映射
"""
Order Status: ACTIVE,
              PARTIALLY FILLED @ PRICE(AMOUNT),
              EXECUTED @ PRICE(AMOUNT) e.g. "EXECUTED @ 107.6(-0.2)",
              CANCELED,

              INSUFFICIENT MARGIN was: PARTIALLY FILLED @ PRICE(AMOUNT),

              CANCELED was: PARTIALLY FILLED @ PRICE(AMOUNT)

"""
statusMapReverse = {}
statusMapReverse['ACTIVE'] = STATUS_NOTTRADED                      # pending 订单活跃状态中
statusMapReverse['PARTIALLYFILLED'] = STATUS_PARTTRADED            # 'partial filled' 部分交易
statusMapReverse['EXECUTED'] = STATUS_ALLTRADED                    # 'filled'   交易完成
statusMapReverse['CANCELED'] = STATUS_CANCELLED                    # 'cancelled'  已经全部取消

##价格类型映射
priceTypeMap = {}

priceTypeMap[PRICETYPE_LIMITPRICE] = 'LIMIT'
priceTypeMap[PRICETYPE_MARKETPRICE] = 'MARKET'
priceTypeMap[PRICETYPE_FOK] = 'FILL-OR-KILL'


# import pdb;pdb.set_trace()

############################################################################################
class BitfinexGateay(VtGateway):
    """Bitfinex接口"""

    # ----------------------------------------------------------------------
    def __init__(self, eventEngine, gatewayName=''):
        """Constructor"""

        super(BitfinexGateay, self).__init__(eventEngine, gatewayName)
        self.api = GatewayApi(self)

        self.qryEnabled = False                                                    # 是否要启动循环查询

        self.fileName = self.gatewayName + '_connect.json'
        self.filePath = getJsonPath(self.fileName, __file__)

        self.connected = False
        self.count = 0

    # ----------------------------------------------------------------------
    def connect(self):
        """连接"""
        # 如果 accessKey accessSec pairs 在初始化的时候已经设置了，则不用配置文件里的了
        try:
            f = open(self.filePath)
        except IOError:
            log = VtLogData()
            log.gatewayName = self.gatewayName
            log.logContent = u'读取连接配置出错，请检查'
            self.onLog(log)
            return
        # 解析json文件
        setting = json.load(f)
        f.close()
        try:
            apiKey = str(setting['apiKey'])
            secretKey = str(setting['secretKey'])
            symbols = setting['symbols']
        except KeyError:
            log = VtLogData()
            log.gatewayName = self.gatewayName
            log.logContent = u'连接配置缺少字段，请检查'
            self.onLog(log)
            return

        if self.connected:
            return

        # 创建行情和交易接口对象
        self.api.connect(apiKey, secretKey, symbols)
        self.connected = True

        #----------added---------------------------------
        setQryEnabled = setting.get('setQryEnabled', None)
        self.setQryEnabled(setQryEnabled)

        setQryFreq = setting.get('setQryFreq', 60)
        self.initQuery(setQryFreq)


    # ----------------------------------------------------------------------
    def subscribe(self, subscribeReq):
        """订阅行情"""
        pass

    # ----------------------------------------------------------------------
    def sendOrder(self, orderReq):
        """发单"""
        return self.api.sendOrder(orderReq)

    # ----------------------------------------------------------------------
    def cancelOrder(self, cancelOrderReq):
        """撤单"""
        self.api.cancelOrder(cancelOrderReq)

    # ----------------------------------------------------------------------
    def close(self):
        """关闭"""
        self.api.close()

    # -------------------------------------------------------------------
    def qryPosition(self):
        """查询持仓"""
        self.api.onPosition()
    # ----------------------------------------------------------------------
    def qryAccount(self):
        """"""
        self.api.onWallet()

    #----------------------------------------------------------------------
    def initQuery(self, freq = 60):
        """初始化连续查询"""
        if self.qryEnabled:
            # 需要循环的查询函数列表
            self.qryFunctionList = [self.queryInfo]

            self.qryCount = 0                           # 查询触发倒计时
            self.qryTrigger = freq                      # 查询触发点
            self.qryNextFunction = 0                    # 上次运行的查询函数索引

            self.startQuery()

    # ----------------------------------------------------------------------
    def query(self, event):
        """注册到事件处理引擎上的查询函数"""
        self.qryCount += 1

        if self.qryCount > self.qryTrigger:
            # 清空倒计时
            self.qryCount = 0

            # 执行查询函数
            function = self.qryFunctionList[self.qryNextFunction]
            function()

            # 计算下次查询函数的索引，如果超过了列表长度，则重新设为0
            self.qryNextFunction += 1
            if self.qryNextFunction == len(self.qryFunctionList):
                self.qryNextFunction = 0

    # ----------------------------------------------------------------------
    def startQuery(self):
        """启动连续查询"""
        self.eventEngine.register(EVENT_TIMER, self.query)

    # ----------------------------------------------------------------------
    def setQryEnabled(self, qryEnabled):
        """设置是否要启动循环查询"""
        self.qryEnabled = qryEnabled

    #----------------------------------------------------------------------
    def queryInfo(self):
        """"""
        self.api.queryAccount()
        self.api.queryPosition()
        # self.api.queryOrder()

    # ----------------------------------------------------------------------
    # 启动初始化程序接口，在策略在持仓期间停止重启时候进行持仓信息的查询，这里使用restful 接口进行查
    def initPosition(self,vtSymbol):
        self.api.queryPosition()


    def qryAllOrders(self,vtSymbol,order_id,status=None):
        pass


    def loadHistoryBar(self, vtSymbol, type_, size=None, since=None):
        symbol = vtSymbol.split(':')[0]

        # 注意映射的周期后边的周期进行映射  针对bitfinex 有以下周期
        """
        '1m', '5m', '15m', '30m', '1h', '3h', '6h', '12h', '1D', '7D', '14D', '1M'
        """
        typeMap = {}
        typeMap['1min'] = '1m'
        typeMap['5min'] = '5m'
        typeMap['15min'] = '15m'
        typeMap['30min'] = '30m'
        typeMap['60min'] = '1h'
        typeMap['360min'] = '6h'

        url = f'https://api.bitfinex.com/v2/candles/trade:{typeMap[type_]}:t{symbol}/hist'

        params = {}
        if size:
            params['limit'] = size
        if since:
            params['start'] = since

        r = requests.get(url, headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }, params=params, timeout=10)

        df = pd.DataFrame(r.json(), columns=["MTS", "open", "close", "high", "low", "volume"])
        # print("交易所返回",df)
        """
             MTS      open     close      high     low        volume
        0    1556763900000  4.931400  4.931100  4.931400  4.9311    110.262000
        1    1556763600000  4.931700  4.933000  4.933000  4.9296    858.350710
        """
        df["datetime"] = df["MTS"].map(lambda x: datetime.fromtimestamp(x / 1000))
        df['volume'] = df['volume'].map(lambda x: float(x))
        df['open'] = df['open'].map(lambda x: float(x))
        df['high'] = df['high'].map(lambda x: float(x))
        df['low'] = df['low'].map(lambda x: float(x))
        df['close'] = df['close'].map(lambda x: float(x))
        pm = df.sort_values(by="datetime", ascending=True)                # 对时间以及数据进行转换
        return pm


########################################################################
class GatewayApi(BitfinexApi):
    """API实现"""

    # ----------------------------------------------------------------------
    def __init__(self, gateway):
        """Constructor"""
        super(GatewayApi, self).__init__()

        self.gateway = gateway                  # gateway对象
        self.gatewayName = gateway.gatewayName  # gateway对象名称
        self.symbols = []


        # 根据其中的api 接口这里传入的是utc 标准时间格式数组
        self.orderId = 1
        self.date = int(datetime.now().timestamp()) * self.orderId

        self.currencys = []
        self.tickDict = {}
        self.bidDict = {}
        self.askDict = {}
        self.orderLocalDict = {}                                   # 维护的本地订单编号字典
        self.channelDict = {}                                      # ChannelID : (Channel, Symbol)

        self.accountDict = {}                                      # 在定义account 账号时候使用

        # 因为在bitfinex上没有order 的开平的属性，但是可以记录pos 的多、空、none 属性辅助判定
        # 首先获得positon 的direction，然后根据order 交易的正负值去进行定义
        self.direction = DIRECTION_NET                            # 默认方向为空方向，在初始化时候ps 时候，定义为none

    # ----------------------------------------------------------------------
    def connect(self, apiKey, secretKey, symbols):
        """连接服务器"""
        self.apiKey = apiKey
        self.secretKey = secretKey
        self.symbols = symbols

        self.start()
        self.writeLog(u'交易API启动成功')

    # ----------------------------------------------------------------------
    def onConnect(self):
        """"""
        for symbol in self.symbols:
            self.subscribe(symbol, 'ticker')
            self.subscribe(symbol, 'book')
        self.writeLog(u'行情推送订阅成功')

        # 只获取数据，不交易
        self.authenticate()
        self.writeLog(u'认证成功，交易推送订阅成功')

        self.sendRestReq('/symbols_details', self.onSymbolDetails,post=False)

    # ----------------------------------------------------------------------
    def subscribe(self, symbol, channel):
        """"""
        if not symbol.startswith("t"):
            symbol = "t" + symbol

        req = {
            'event': 'subscribe',
            'channel': channel,
            'symbol': symbol
        }
        self.sendReq(req)

    # ----------------------------------------------------------------------
    def authenticate(self):
        """"""
        nonce = int(time.time() * 1000000)
        authPayload = 'AUTH' + str(nonce)
        signature = hmac.new(
            self.secretKey.encode(),
            msg=authPayload.encode(),
            digestmod=hashlib.sha384
        ).hexdigest()

        req = {
            'apiKey': self.apiKey,
            'event': 'auth',
            'authPayload': authPayload,
            'authNonce': nonce,
            'authSig': signature
        }

        self.sendReq(req)

    # ----------------------------------------------------------------------
    def writeLog(self, content):
        """发出日志"""
        """发出日志"""
        log = VtLogData()
        log.gatewayName = self.gatewayName
        log.logContent = content
        self.gateway.onLog(log)

    # ----------------------------------------------------------------------
    def generateDateTime(self, s):
        """生成时间"""
        dt = datetime.fromtimestamp(s / 1000.0)
        date = dt.strftime('%Y-%m-%d')
        time = dt.strftime("%H:%M:%S.%f")
        return date, time

    def sendOrder(self, orderReq):
        """
        #策略---策略模板---策略引擎--主引擎---gateway 所以要知道其中对应的关系
        buy ---- sell
        long  open /   short close
        short ---cover
        short open /  long  close
        :param orderReq:
        :return:
        """
        # print('gateway senderorder orderReq._dict_', orderReq.__dict__)
        # 这里我们默认从系统里面传过来的volume都是正数

        amount = 0                                         # 在引入amount 之前定义amount变量，防止后边变量引入报错
        self.orderId += 1
        orderId = self.date + self.orderId
        vtOrderID = ':'.join([self.gatewayName, str(orderId)])

        # 注意对amount 的定义，因为监听传过来有四种组合
        if orderReq.direction == DIRECTION_LONG and orderReq.offset == OFFSET_OPEN:          # 买开  buy
            amount = orderReq.volume
        elif orderReq.direction == DIRECTION_SHORT and orderReq.offset == OFFSET_CLOSE:      # 卖平  sell
            amount = -orderReq.volume
        elif orderReq.direction == DIRECTION_SHORT and orderReq.offset == OFFSET_OPEN:       # 卖开   short
            amount = -orderReq.volume
        elif orderReq.direction == DIRECTION_LONG and orderReq.offset == OFFSET_CLOSE:       # 买平   cover
            amount = orderReq.volume

        oSymbol = orderReq.symbol
        if not oSymbol.startswith("t"):
            oSymbol = "t" + oSymbol

        o = {
            'cid': orderId,                       # Should be unique in the day (UTC) (not enforced)  int45
            'type': priceTypeMap[orderReq.priceType],
            'symbol': oSymbol,
            'amount': str(amount),
            'price': str(orderReq.price)
        }

        req = [0, 'on', None, o]
        self.sendReq(req)
        return vtOrderID

    # ----------------------------------------------------------------------
    def cancelOrder(self, cancelOrderReq):
        """"""
        orderId = int(cancelOrderReq.orderID)
        date = cancelOrderReq.sessionID

        req = [
            0,
            'oc',
            None,
            {
                'cid': orderId,
                'cid_date': date,
            }
        ]

        self.sendReq(req)

    # ----------------------------------------------------------------------
    def calc(self):
        """"""
        l = []
        for currency in self.currencys:
            l.append(['wallet_exchange_' + currency])

        req = [0, 'calc', None, l]
        self.sendReq(req)

    # ----------------------------------------------------------------------
    def onData(self, data):
        """"""

        if isinstance(data, dict):
            self.onResponse(data)
        else:
            self.onUpdate(data)

    # ----------------------------------------------------------------------
    def onResponse(self, data):
        """"""
        if 'event' not in data:
            return

        # 如果有错误的返回信息，要打印出来
        print("[onResponse]data:" + json.dumps(data))

        if data['event'] == 'subscribed':
            symbol = str(data['symbol'].replace('t', ''))
            self.channelDict[data['chanId']] = (data['channel'], symbol)

    # ----------------------------------------------------------------------
    def onUpdate(self, data):
        """"""
        if data[1] == u'hb':
            return

        channelID = data[0]

        if not channelID:
            self.onTradeUpdate(data)
        else:
            self.onDataUpdate(data)

    # ----------------------------------------------------------------------
    def onDataUpdate(self, data):
        """"""
        channelID = data[0]
        channel, symbol = self.channelDict[channelID]
        symbol = str(symbol.replace('t', ''))

        # 获取Tick对象
        if symbol in self.tickDict:
            tick = self.tickDict[symbol]
        else:
            tick = VtTickData()
            tick.gatewayName = self.gatewayName
            tick.symbol = symbol
            tick.exchange = EXCHANGE_BITFINEX
            tick.vtSymbol = ':'.join([tick.symbol, tick.exchange])

            self.tickDict[symbol] = tick

        l = data[1]

        # 常规行情更新
        if channel == 'ticker':
            tick.volume = float(l[-3])
            tick.highPrice = float(l[-2])
            tick.lowPrice = float(l[-1])
            tick.lastPrice = float(l[-4])
            tick.openPrice = float(tick.lastPrice - l[4])
        # 深度报价更新
        elif channel == 'book':
            bid = self.bidDict.setdefault(symbol, {})
            ask = self.askDict.setdefault(symbol, {})

            if len(l) > 3:
                for price, count, amount in l:
                    price = float(price)
                    count = int(count)
                    amount = float(amount)

                    if amount > 0:
                        bid[price] = amount
                    else:
                        ask[price] = -amount
            else:
                price, count, amount = l
                price = float(price)
                count = int(count)
                amount = float(amount)

                if not count:
                    if price in bid:
                        del bid[price]
                    elif price in ask:
                        del ask[price]
                else:
                    if amount > 0:
                        bid[price] = amount
                    else:
                        ask[price] = -amount

            # Bitfinex的深度数据更新是逐档推送变动情况，而非5档一起推
            # 因此会出现没有Bid或者Ask的情况，这里使用try...catch过滤
            # 只有买卖深度满足5档时才做推送
            try:
                # BID
                bidPriceList = bid.keys()
                bidPriceList = sorted(bidPriceList)

                tick.bidPrice1 = bidPriceList[0]
                tick.bidPrice2 = bidPriceList[1]
                tick.bidPrice3 = bidPriceList[2]
                tick.bidPrice4 = bidPriceList[3]
                tick.bidPrice5 = bidPriceList[4]

                tick.bidVolume1 = bid[tick.bidPrice1]
                tick.bidVolume2 = bid[tick.bidPrice2]
                tick.bidVolume3 = bid[tick.bidPrice3]
                tick.bidVolume4 = bid[tick.bidPrice4]
                tick.bidVolume5 = bid[tick.bidPrice5]

                # ASK
                askPriceList = ask.keys()
                askPriceList = sorted(askPriceList)

                tick.askPrice1 = askPriceList[0]
                tick.askPrice2 = askPriceList[1]
                tick.askPrice3 = askPriceList[2]
                tick.askPrice4 = askPriceList[3]
                tick.askPrice5 = askPriceList[4]

                tick.askVolume1 = ask[tick.askPrice1]
                tick.askVolume2 = ask[tick.askPrice2]
                tick.askVolume3 = ask[tick.askPrice3]
                tick.askVolume4 = ask[tick.askPrice4]
                tick.askVolume5 = ask[tick.askPrice5]
            except IndexError:
                return

        dt = datetime.now()
        tick.date = dt.strftime('%Y%m%d')
        tick.time = dt.strftime('%H:%M:%S.%f')
        tick.datetime = dt

        # 推送
        self.gateway.onTick(copy(tick))

    # ----------------------------------------------------------------------
    def onTradeUpdate(self, data):
        """"""
        name = data[1]
        info = data[2]

        # -----------------
        if name == 'os':                                             # orders活动委托，发单委托
            for l in info:
                self.onOrder(l)
            self.writeLog(u' os  快照 活动委托orders获取成功')
        elif name in ['on', 'ou', 'oc']:                             # orders活动委托，发单更新
            self.onOrder(info)
            self.writeLog(u'活动委托orders更新成功')

        elif name == 'te':                                          # trade
            self.onTrade(info)
        # elif name == 'tu':                                        # tradeupdates
        #     # 接下来更新到的是'tu'  排序4
        #     self.onTrade(info)

        elif name == 'ps':                                          # position,这里是推送是关键是在ui 上显示pos 的基础
            for l in info:
                self.onPosition(l)
                self.writeLog(u'初始化持仓信息获取成功 快照')
        elif name in ['pn', 'pu', 'pc']:                            # position update这种形式是高级查询 包含利润，杠杆等信息
            """
            这里对仓位进行细粒度的控制，因为仓位的放行决定了 order 的方向
            [0, 'ps', [['tEOSUSD', 'ACTIVE', -26.369349,            2.8374,              -4.511e-05, 0, None, None, None, None]
            [0, 'pn', ['tEOSUSD', 'ACTIVE', -6, 4.9154, 0, 0, None, None, None, None, None, None, None, None, None, 0, None, None, None, None]]
            [0, 'pc', ['tEOSUSD', 'CLOSED', 0, 4.9, 0, 0, None, None, None, None, None, None, None, None, None, 0, None, None, None, None]]
            [0, 'pu', ['tEOSUSD',    'ACTIVE', -26.369349,  2.8374,    -5.205e-05,        0,                6.03048553,         8.05994925,  3.32558392,  -2.4796]]

            """
            self.onPosition(info)
            self.writeLog(u'持仓信息【更新】成功')                     # 注意这里获取的每一个资金账户之中的每一个币种，情况单独列举出来
        elif name == 'ws':                                          # wallets 账户信息包含 exchange  margin
            for l in info:
                self.onWallet(l)
            self.writeLog(u'账户资金获取成功 快照')
        elif name == 'wu':                                          # wallets 账户信息仅包含usd 信息   [0, 'wu', ['margin', 'USD', 213.06576039, 0, None]]
            self.onWallet(info)
            self.writeLog(u'账户资金 usd 【更新】获取成功')


    def onWallet(self, data):
        # 因为这里是使用margin 进行交易   第一次获得账户的快照信息
        if str(data[0]) == 'margin':
            """
             ['margin', 'USD', 213.11012559, 0, None]
             ['margin', 'ETC', 0.00079896, 0, None]
             ['margin', 'ETH', 0.00457842, 0, None]

            """
            account = VtAccountData()
            account.vtAccountID = self.gatewayName

            account.accountID = str(data[1])                                       # 交易的币种
            account.vtAccountID = ':'.join([account.gatewayName, account.accountID])
            account.balance = float(data[2])                                       # 现有的数量
            if data[-1]:
                account.available = float(data[-1])

            self.gateway.onAccount(account)


    def onPosition(self, data):
        """
        # 这里均为真实的由交易传过来的数据 程序启动的时候先进行 ps 但是如果没有仓位的情况下，是ps没有返回信息，此时我们初始化定义  self.direction
         ontradeupdate [0, 'ps', []]   出水啊为空
        [0, 'ps', [['tEOSUSD', 'ACTIVE', 6, 4.9, 0, 0, None, None, None, None, None, None, None, None, None, 0, None, None, None, None]]]
        获取持有仓位的快照信息，可以根据目前快照信息设定 仓位的方向 self.direction =""
        :param data:
        :return:
        """
        pos = VtPositionData()

        Symbol = data[0].split('t')[-1]
        pos.symbol = Symbol
        pos.gatewayName = self.gatewayName
        pos.exchange = EXCHANGE_BITFINEX
        pos.vtSymbol = ':'.join([pos.symbol, pos.exchange])                       # 合约在vt系统中的唯一代码，合约代码:交易所代码

        # 进行细力度的控制，仅有在仓位是 active 状态下才进行计算
        pos.position = abs(data[2])                  # 这里取持仓量是绝对值
        if data[2] > 0:
            pos.direction = DIRECTION_LONG          # 定义到头仓位
        elif data[2] < 0:
            pos.direction = DIRECTION_SHORT         # 定义空头仓位
        else:
            pos.direction = DIRECTION_NET           # 定义没有none 仓位 需要注意的是在进行平仓的动作之后，我们默认的为 net ，怎么去维护我们的pos ,在ctaenging中

        # 这里定义了一个全局变量方便后边引用对交易信息，已经订单信息进行判断
        self.direction = pos.direction

        pos.vtPositionName = ':'.join([pos.vtSymbol, pos.direction])
        pos.frozen = 0                               # 期货没有冻结概念，会直接反向开仓
        pos.price = data[3]                          # 持仓均价
        if data[6]:                                  # 持仓盈亏
            pos.positionProfit = data[6]
        self.gateway.onPosition(pos)

    #----------------------------------------------------------------------
    def queryPosition(self):
        """"""
        # self.sendRestReq('/positions', self.onQueryPosition , post=True)
        data = self.sendRestReq('/positions', self.onQueryPosition , post=True)
        print("策略启动 进行仓位初始化", data)


    #----------------------------------------------------------------------
    def queryAccount(self):
        """"""
        self.sendRestReq('/margin_infos', self.onQueryAccount , post=True)


    def onOrder(self, data):
        """
        ontradeupdate [0, 'os', []]    初始化启动为空
        [0, 'oc', [24705433913, None, 1556815903199, 'tEOSUSD', 1556815903199, 1556815903273, 0, -6, 'MARKET',   订单发出只有进行更新
            [23689701610, None, 1554194135, 'tEOSUSD', 1554194460237, 1554194460254, 0, 7, 'LIMIT', None, None, None, 0,
           'EXECUTED @ 4.6351(7.0)', None, None, 4.7, 4.6351, 0, 0, None, None, None, 0, 0, None, None, None, 'API>BFX',
            None, None, None]
            # 这里的order 只有正负之分，没有代表是 多仓买入（买开）    还是空仓买入（卖平），这里还是需要根据之前的pos 的状态进行判定

        :param data:
        :return:

        """
        order = VtOrderData()
        order.gatewayName = self.gatewayName

        order.symbol = str(data[3].replace('t', ''))                               # 交易对 EOSUSD
        order.exchange = EXCHANGE_BITFINEX                                         # 交易对 BITFINEX
        order.vtSymbol = ':'.join([order.symbol, order.exchange])                  # vnpy 系统编号 EOSUSD:BITFINEX

        order.orderID = str(data[2])                                               # 交易对 1553115420502   交易所返回的client订单编号
        order.vtOrderID = ':'.join([order.gatewayName, order.orderID])             # vnpy 系统编号 BITFINEX:1553115420502
        order.priceType = str(data[8])                                             # 价格类型

        # 当之前没有仓位的状态下
        """
        buy ---- sell
        long  open /   short close
        short ---cover
        short open /  long  close

        """
        # 之前没有仓位，那么 买开 或者  卖开
        if self.direction == DIRECTION_NET:
            if data[7] > 0:
                print('买开')
                order.direction = DIRECTION_LONG
                order.offset = OFFSET_OPEN
            else:
                print('卖开')
                order.direction = DIRECTION_SHORT
                order.offset = OFFSET_OPEN
        # 之前有多头持仓 那么加仓 那么减仓（卖出）
        elif self.direction == DIRECTION_LONG:  # 如果持有多仓仓位(注意这里是之前的仓位)
            if data[7] > 0:
                print('买开')
                order.direction = DIRECTION_LONG
                order.offset = OFFSET_OPEN
            else:
                print('卖出减仓')
                order.direction = DIRECTION_SHORT
                order.offset = OFFSET_CLOSE
        # 之前持有空头仓位 那么加仓  那么平仓（买入）
        elif self.direction == DIRECTION_SHORT:
            if data[7] > 0:
                print('平空减仓')
                order.direction = DIRECTION_LONG
                order.offset = OFFSET_CLOSE
            else:
                print('做空加仓')
                order.direction = DIRECTION_SHORT
                order.offset = OFFSET_OPEN

        order.price = float(data[16])                                     # 价格

        # 根据后边对其中的持仓的处理，首先推送到是position 为了避免冲突所以，这里全部置为0
        order.totalVolume = 0
        order.tradedVolume = 0
        order.thisTradedVolume = 0
        order.signalTradedVolume = abs(data[7]) - abs(data[6])                # 这里定义一个新的变量作为策略之中的判定使用

        # 在非完全成交状态下的判断,目前映射状态有很多
        if str(data[13]) == 'INSUFFICIENT BALANCE (U1)':
            # order.status = "资金量不足"
            order.status = STATUS_UNKNOWN  # 状态为 未知
            print("资金量不足")
        else:
            orderStatus = str(data[13].split('@')[0])
            orderStatus = orderStatus.replace(' ', '')
            order.status = statusMapReverse[orderStatus]                        # 对应的映射为STATUS_ALLTRADED    完全成交

        order.sessionID, order.orderTime = self.generateDateTime(data[4])       # 订单创建时间
        if order.status == STATUS_CANCELLED:
            buf, order.cancelTime = self.generateDateTime(data[5])

        # ===============================本地的订单编号为，key 为ID即order 编号，此标号为trade   values 为订单cid 即我们传入的cid
        self.orderLocalDict[data[0]] = order.orderID
        self.gateway.onOrder(order)
        self.calc()


    # ----------------------------------------------------------------------
    def onTrade(self, data):
        # trade 信息是基于order 的数据流进行判定的
        """
        没有order 情况下是不更新trade 的，当有order 的情况下才更新
        ontradeupdate [0, 'te', [353626766, 'tEOSUSD', 1556815903268, 24705433913, -6, 4.9154, 'MARKET', 4.9133, -1, None, None, 1556815903199]]
        # 根据成交状态可以继续您细粒度的控制 根据成交的状态进行


        :param data:
        :return:
        """
        trade = VtTradeData()
        trade.gatewayName = self.gatewayName

        trade.symbol = data[1].replace('t', '')
        trade.exchange = EXCHANGE_BITFINEX
        trade.vtSymbol = ':'.join([trade.symbol, trade.exchange])


        bitfinex_id = self.orderLocalDict.get(data[3], None)
        if not bitfinex_id:
            self.orderLocalDict[data[3]] = data[11]
        trade.orderID = self.orderLocalDict[data[3]]

        trade.vtOrderID = ':'.join([trade.gatewayName, str(trade.orderID)])
        # 注意返回值之中的第一个是trade 的编号id,这里需要是str
        trade.tradeID = str(data[0])
        trade.vtTradeID = ':'.join([trade.gatewayName, trade.tradeID])

        # 因为trade 返回只有成交的数量，没有成交的方向，所以可以根据仓位来进行判定，思路与order 是一致的；
        # 这里的trade 还是很有必要的，因为在部分的策略之中，是根据trade 的方向进行开仓与加仓的仓位的 价格的变化的，比如海龟交易策略
        if data[4] > 0 and self.direction == DIRECTION_LONG:
            print('成交（加仓）')
            trade.direction = DIRECTION_LONG
            trade.offset = OFFSET_OPEN
        elif data[4] > 0 and self.direction == DIRECTION_SHORT:
            print('买平')
            trade.direction = DIRECTION_LONG
            trade.offset = OFFSET_CLOSE
        elif data[4] > 0 and self.direction == DIRECTION_NET:
            print('买开')
            trade.direction = DIRECTION_LONG
            trade.offset = OFFSET_OPEN

        elif data[4] < 0 and self.direction == DIRECTION_LONG:
            print('卖平')
            trade.direction = DIRECTION_SHORT
            trade.offset = OFFSET_CLOSE
        elif data[4] < 0 and self.direction == DIRECTION_SHORT:
            print('做空加仓')
            trade.direction = DIRECTION_SHORT
            trade.offset = OFFSET_OPEN
        elif data[4] < 0 and self.direction == DIRECTION_NET:
            print('卖开')
            trade.direction = DIRECTION_SHORT
            trade.offset = OFFSET_OPEN

        trade.price = data[5]                                        # 成交的价格
        buf, trade.tradeTime = self.generateDateTime(data[2])        # 成交的时间


        # 根据目前的测试 暂时修改为  v3
        trade.volume = 0                         # 成交的数量v1
        trade.signalvolume = abs(data[4])        # 这里重新定义一个新的标量作为策略之中的判定使用

        self.gateway.onTrade(trade)


    # ----------------------------------------------------------------------
    def onSymbolDetails(self, data):
        """"""
        """
        [{'pair': 'btcusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.0008', 'expiration': 'NA', 'margin': True}, {'pair': 'ltcusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.06', 'expiration': 'NA', 'margin': True}, {'pair': 'ltcbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.06', 'expiration': 'NA', 'margin': True}, {'pair': 'ethusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': True}, {'pair': 'ethbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': True}, {'pair': 'etcbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '0.8', 'expiration': 'NA', 'margin': True}, {'pair': 'etcusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '0.8', 'expiration': 'NA', 'margin': True}, {'pair': 'rrtusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '112.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rrtbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '112.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zecusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '0.08', 'expiration': 'NA', 'margin': True}, {'pair': 'zecbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '0.08', 'expiration': 'NA', 'margin': True}, {'pair': 'xmrusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.06', 'expiration': 'NA', 'margin': True}, {'pair': 'xmrbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.06', 'expiration': 'NA', 'margin': True}, {'pair': 'dshusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.04', 'expiration': 'NA', 'margin': True}, {'pair': 'dshbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.04', 'expiration': 'NA', 'margin': True}, {'pair': 'btceur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.0008', 'expiration': 'NA', 'margin': True}, {'pair': 'btcjpy', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.0008', 'expiration': 'NA', 'margin': True}, {'pair': 'xrpusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'xrpbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'iotusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'iotbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'ioteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'eosusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '1.0', 'expiration': 'NA', 'margin': True}, {'pair': 'eosbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '1.0', 'expiration': 'NA', 'margin': True}, {'pair': 'eoseth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '1.0', 'expiration': 'NA', 'margin': True}, {'pair': 'sanusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': True}, {'pair': 'sanbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': True}, {'pair': 'saneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': True}, {'pair': 'omgusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': True}, {'pair': 'omgbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': True}, {'pair': 'omgeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': True}, {'pair': 'neousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '0.4', 'expiration': 'NA', 'margin': True}, {'pair': 'neobtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '0.4', 'expiration': 'NA', 'margin': True}, {'pair': 'neoeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '0.4', 'expiration': 'NA', 'margin': True}, {'pair': 'etpusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': True}, {'pair': 'etpbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': True}, {'pair': 'etpeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': True}, {'pair': 'qtmusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'qtmbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'qtmeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'avtusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '24.0', 'expiration': 'NA', 'margin': False}, {'pair': 'avtbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '24.0', 'expiration': 'NA', 'margin': False},
         {'pair': 'avteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '24.0', 'expiration': 'NA', 'margin': False}, {'pair': 'edousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': True}, {'pair': 'edobtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': True}, {'pair': 'edoeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': True}, {'pair': 'btgusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.2', 'expiration': 'NA', 'margin': True}, {'pair': 'btgbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.2', 'expiration': 'NA', 'margin': True}, {'pair': 'datusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '250000.0', 'minimum_order_size': '242.0', 'expiration': 'NA', 'margin': False}, {'pair': 'datbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '250000.0', 'minimum_order_size': '242.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dateth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '250000.0', 'minimum_order_size': '242.0', 'expiration': 'NA', 'margin': False}, {'pair': 'qshusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '40.0', 'expiration': 'NA', 'margin': False}, {'pair': 'qshbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '40.0', 'expiration': 'NA', 'margin': False}, {'pair': 'qsheth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '40.0', 'expiration': 'NA', 'margin': False}, {'pair': 'yywusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '228.0', 'expiration': 'NA', 'margin': False}, {'pair': 'yywbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '228.0', 'expiration': 'NA', 'margin': False}, {'pair': 'yyweth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '228.0', 'expiration': 'NA', 'margin': False}, {'pair': 'gntusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '62.0', 'expiration': 'NA', 'margin': False}, {'pair': 'gntbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '62.0', 'expiration': 'NA', 'margin': False}, {'pair': 'gnteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '62.0', 'expiration': 'NA', 'margin': False}, {'pair': 'sntusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'sntbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'snteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ioteur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'batusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '12.0', 'expiration': 'NA', 'margin': False}, {'pair': 'batbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '12.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bateth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '12.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mnausd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '94.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mnabtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '94.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mnaeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '94.0', 'expiration': 'NA', 'margin': False}, {'pair': 'funusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '878.0', 'expiration': 'NA', 'margin': False}, {'pair': 'funbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '878.0', 'expiration': 'NA', 'margin': False}, {'pair': 'funeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '878.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zrxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'zrxbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zrxeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'tnbusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '844.0', 'expiration': 'NA', 'margin': False}, {'pair': 'tnbbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '844.0', 'expiration': 'NA', 'margin': False}, {'pair': 'tnbeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '844.0', 'expiration': 'NA', 'margin': False}, {'pair': 'spkusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '384.0', 'expiration': 'NA', 'margin': False}, {'pair': 'spkbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '384.0', 'expiration': 'NA', 'margin': False}, {'pair': 'spketh', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '384.0', 'expiration': 'NA', 'margin': False}, {'pair': 'trxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'trxbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'trxeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rcnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '156.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rcnbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '156.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rcneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '156.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rlcusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '25000.0', 'minimum_order_size': '10.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rlcbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '25000.0', 'minimum_order_size': '10.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rlceth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '25000.0', 'minimum_order_size': '10.0', 'expiration': 'NA', 'margin': False}, {'pair': 'aidusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '90.0', 'expiration': 'NA', 'margin': False}, {'pair': 'aidbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '90.0', 'expiration': 'NA', 'margin': False}, {'pair': 'aideth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '90.0', 'expiration': 'NA', 'margin': False}, {'pair': 'sngusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '264.0', 'expiration': 'NA', 'margin': False}, {'pair': 'sngbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '264.0', 'expiration': 'NA', 'margin': False}, {'pair': 'sngeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '264.0', 'expiration': 'NA', 'margin': False}, {'pair': 'repusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000.0', 'minimum_order_size': '0.2', 'expiration': 'NA', 'margin': False}, {'pair': 'repbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000.0', 'minimum_order_size': '0.2', 'expiration': 'NA', 'margin': False}, {'pair': 'repeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000.0', 'minimum_order_size': '0.2', 'expiration': 'NA', 'margin': False}, {'pair': 'elfusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '75000.0', 'minimum_order_size': '28.0', 'expiration': 'NA', 'margin': False}, {'pair': 'elfbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '75000.0', 'minimum_order_size': '28.0', 'expiration': 'NA', 'margin': False}, {'pair': 'elfeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '75000.0', 'minimum_order_size': '28.0', 'expiration': 'NA', 'margin': False}, {'pair': 'btcgbp', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.0008', 'expiration': 'NA', 'margin': True}, {'pair': 'etheur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': True}, {'pair': 'ethjpy', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': True}, {'pair': 'ethgbp', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': True}, {'pair': 'neoeur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '0.4', 'expiration': 'NA', 'margin': True}, {'pair': 'neojpy', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '0.4', 'expiration': 'NA', 'margin': True}, {'pair': 'neogbp', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '0.4', 'expiration': 'NA', 'margin': True}, {'pair': 'eoseur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '1.0', 'expiration': 'NA', 'margin': True}, {'pair': 'eosjpy', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '1.0', 'expiration': 'NA', 'margin': True}, {'pair': 'eosgbp', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '1.0', 'expiration': 'NA', 'margin': True}, {'pair': 'iotjpy', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'iotgbp', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': True}, {'pair': 'iosusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '400.0', 'expiration': 'NA', 'margin': False}, {'pair': 'iosbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '400.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ioseth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '400.0', 'expiration': 'NA', 'margin': False}, {'pair': 'aiousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '24.0', 'expiration': 'NA', 'margin': False}, {'pair': 'aiobtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '24.0', 'expiration': 'NA', 'margin': False}, {'pair': 'aioeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '24.0', 'expiration': 'NA', 'margin': False}, {'pair': 'requsd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '198.0', 'expiration': 'NA', 'margin': False}, {'pair': 'reqbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '198.0', 'expiration': 'NA', 'margin': False}, {'pair': 'reqeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '198.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rdnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '14.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rdnbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '14.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rdneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '14.0', 'expiration': 'NA', 'margin': False}, {'pair': 'lrcusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '70.0', 'expiration': 'NA', 'margin': False}, {'pair': 'lrcbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '70.0', 'expiration': 'NA', 'margin': False}, {'pair': 'lrceth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '70.0', 'expiration': 'NA', 'margin': False}, {'pair': 'waxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '70.0', 'expiration': 'NA', 'margin': False}, {'pair': 'waxbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '70.0', 'expiration': 'NA', 'margin': False}, {'pair': 'waxeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '70.0', 'expiration': 'NA', 'margin': False}, {'pair': 'daiusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'daibtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'daieth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'agiusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '122.0', 'expiration': 'NA', 'margin': False}, {'pair': 'agibtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '122.0', 'expiration': 'NA', 'margin': False}, {'pair': 'agieth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '122.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bftusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '170.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bftbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '170.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bfteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '170.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mtnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '502.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mtnbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '502.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mtneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '502.0', 'expiration': 'NA', 'margin': False}, {'pair': 'odeusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '20.0', 'expiration': 'NA', 'margin': False}, {'pair': 'odebtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '20.0', 'expiration': 'NA', 'margin': False}, {'pair': 'odeeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '20.0', 'expiration': 'NA', 'margin': False}, {'pair': 'antusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': False}, {'pair': 'antbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': False}, {'pair': 'anteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dthusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '546.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dthbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '546.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dtheth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '546.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mitusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '90.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mitbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '90.0', 'expiration': 'NA', 'margin': False}, {'pair': 'miteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '90.0', 'expiration': 'NA', 'margin': False}, {'pair': 'stjusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '18.0', 'expiration': 'NA', 'margin': False}, {'pair': 'stjbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '18.0', 'expiration': 'NA', 'margin': False}, {'pair': 'stjeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '18.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xlmusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '48.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xlmeur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '48.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xlmjpy', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '48.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xlmgbp', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '48.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xlmbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '48.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xlmeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '48.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xvgusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1500000.0', 'minimum_order_size': '610.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xvgeur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '610.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xvgjpy', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1500000.0', 'minimum_order_size': '610.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xvggbp', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1500000.0', 'minimum_order_size': '610.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xvgbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1500000.0', 'minimum_order_size': '610.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xvgeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1500000.0', 'minimum_order_size': '610.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bciusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '40.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bcibtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '40.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mkrusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50.0', 'minimum_order_size': '0.008', 'expiration': 'NA', 'margin': False}, {'pair': 'mkrbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50.0', 'minimum_order_size': '0.008', 'expiration': 'NA', 'margin': False}, {'pair': 'mkreth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50.0', 'minimum_order_size': '0.008', 'expiration': 'NA', 'margin': False}, {'pair': 'kncusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '18.0', 'expiration': 'NA', 'margin': False}, {'pair': 'kncbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '18.0', 'expiration': 'NA', 'margin': False}, {'pair': 'knceth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '18.0', 'expiration': 'NA', 'margin': False}, {'pair': 'poausd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '150000.0', 'minimum_order_size': '110.0', 'expiration': 'NA', 'margin': False}, {'pair': 'poabtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '150000.0', 'minimum_order_size': '110.0', 'expiration': 'NA', 'margin': False}, {'pair': 'poaeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '150000.0', 'minimum_order_size': '110.0', 'expiration': 'NA', 'margin': False}, {'pair': 'lymusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '400000.0', 'minimum_order_size': '444.0', 'expiration': 'NA', 'margin': False}, {'pair': 'lymbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '400000.0', 'minimum_order_size': '444.0', 'expiration': 'NA', 'margin': False}, {'pair': 'lymeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '400000.0', 'minimum_order_size': '444.0', 'expiration': 'NA', 'margin': False}, {'pair': 'utkusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '300000.0', 'minimum_order_size': '96.0', 'expiration': 'NA', 'margin': False}, {'pair': 'utkbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '300000.0', 'minimum_order_size': '96.0', 'expiration': 'NA', 'margin': False}, {'pair': 'utketh', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '300000.0', 'minimum_order_size': '96.0', 'expiration': 'NA', 'margin': False}, {'pair': 'veeusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '800000.0', 'minimum_order_size': '866.0', 'expiration': 'NA', 'margin': False}, {'pair': 'veebtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '800000.0', 'minimum_order_size': '866.0', 'expiration': 'NA', 'margin': False}, {'pair': 'veeeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '800000.0', 'minimum_order_size': '866.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dadusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '92.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dadbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '92.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dadeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '92.0', 'expiration': 'NA', 'margin': False}, {'pair': 'orsusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '222.0', 'expiration': 'NA', 'margin': False}, {'pair': 'orsbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '222.0', 'expiration': 'NA', 'margin': False}, {'pair': 'orseth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '222.0', 'expiration': 'NA', 'margin': False}, {'pair': 'aucusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '150000.0', 'minimum_order_size': '398.0', 'expiration': 'NA', 'margin': False}, {'pair': 'aucbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '150000.0', 'minimum_order_size': '398.0', 'expiration': 'NA', 'margin': False}, {'pair': 'auceth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '150000.0', 'minimum_order_size': '398.0', 'expiration': 'NA', 'margin': False}, {'pair': 'poyusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '52.0', 'expiration': 'NA', 'margin': False}, {'pair': 'poybtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '52.0', 'expiration': 'NA', 'margin': False}, {'pair': 'poyeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '52.0', 'expiration': 'NA', 'margin': False}, {'pair': 'fsnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': False}, {'pair': 'fsnbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': False}, {'pair': 'fsneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '8.0', 'expiration': 'NA', 'margin': False}, {'pair': 'cbtusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '180.0', 'expiration': 'NA', 'margin': False}, {'pair': 'cbtbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '180.0', 'expiration': 'NA', 'margin': False}, {'pair': 'cbteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '180.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zcnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '46.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zcnbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '46.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zcneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '46.0', 'expiration': 'NA', 'margin': False}, {'pair': 'senusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '3050.0', 'expiration': 'NA', 'margin': False}, {'pair': 'senbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '3050.0', 'expiration': 'NA', 'margin': False}, {'pair': 'seneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '3050.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ncausd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '2316.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ncabtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '2316.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ncaeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '2316.0', 'expiration': 'NA', 'margin': False}, {'pair': 'cndusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '234.0', 'expiration': 'NA', 'margin': False}, {'pair': 'cndbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '234.0', 'expiration': 'NA', 'margin': False}, {'pair': 'cndeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '234.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ctxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '26.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ctxbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '26.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ctxeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '26.0', 'expiration': 'NA', 'margin': False}, {'pair': 'paiusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '118.0', 'expiration': 'NA', 'margin': False}, {'pair': 'paibtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '118.0', 'expiration': 'NA', 'margin': False}, {'pair': 'seeusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000000.0', 'minimum_order_size': '6636.0', 'expiration': 'NA', 'margin': False}, {'pair': 'seebtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000000.0', 'minimum_order_size': '6636.0', 'expiration': 'NA', 'margin': False}, {'pair': 'seeeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000000.0', 'minimum_order_size': '6636.0', 'expiration': 'NA', 'margin': False}, {'pair': 'essusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '3310.0', 'expiration': 'NA', 'margin': False}, {'pair': 'essbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '3310.0', 'expiration': 'NA', 'margin': False}, {'pair': 'esseth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '3310.0', 'expiration': 'NA', 'margin': False}, {'pair': 'atmusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '6172.0', 'expiration': 'NA', 'margin': False}, {'pair': 'atmbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '6172.0', 'expiration': 'NA', 'margin': False}, {'pair': 'atmeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '6172.0', 'expiration': 'NA', 'margin': False}, {'pair': 'hotusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '888.0', 'expiration': 'NA', 'margin': False}, {'pair': 'hotbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '888.0', 'expiration': 'NA', 'margin': False}, {'pair': 'hoteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '888.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dtausd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000000.0', 'minimum_order_size': '3504.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dtabtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000000.0', 'minimum_order_size': '3504.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dtaeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000000.0', 'minimum_order_size': '3504.0', 'expiration': 'NA', 'margin': False}, {'pair': 'iqxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '1168.0', 'expiration': 'NA', 'margin': False}, {'pair': 'iqxbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '1168.0', 'expiration': 'NA', 'margin': False}, {'pair': 'iqxeos', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '1168.0', 'expiration': 'NA', 'margin': False}, {'pair': 'wprusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '800000.0', 'minimum_order_size': '350.0', 'expiration': 'NA', 'margin': False}, {'pair': 'wprbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '800000.0', 'minimum_order_size': '350.0', 'expiration': 'NA', 'margin': False}, {'pair': 'wpreth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '800000.0', 'minimum_order_size': '350.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zilusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '254.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zilbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '254.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zileth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '254.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bntusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bntbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bnteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': False}, {'pair': 'absusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '668.0', 'expiration': 'NA', 'margin': False}, {'pair': 'abseth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '668.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xrausd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '282.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xraeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '282.0', 'expiration': 'NA', 'margin': False}, {'pair': 'manusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '32.0', 'expiration': 'NA', 'margin': False}, {'pair': 'maneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '32.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bbnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '5128.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bbneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '5128.0', 'expiration': 'NA', 'margin': False}, {'pair': 'niousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '644.0', 'expiration': 'NA', 'margin': False}, {'pair': 'nioeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '644.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dgxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '750.0', 'minimum_order_size': '0.2', 'expiration': 'NA', 'margin': False}, {'pair': 'dgxeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '750.0', 'minimum_order_size': '0.2', 'expiration': 'NA', 'margin': False}, {'pair': 'vetusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '734.0', 'expiration': 'NA', 'margin': False}, {'pair': 'vetbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '734.0', 'expiration': 'NA', 'margin': False}, {'pair': 'veteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '734.0', 'expiration': 'NA', 'margin': False}, {'pair': 'utnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000000.0', 'minimum_order_size': '1684.0', 'expiration': 'NA', 'margin': False}, {'pair': 'utneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000000.0', 'minimum_order_size': '1684.0', 'expiration': 'NA', 'margin': False}, {'pair': 'tknusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': False}, {'pair': 'tkneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': False}, {'pair': 'gotusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': False}, {'pair': 'goteur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': False}, {'pair': 'goteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '6.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xtzusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'xtzbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'cnnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000000.0', 'minimum_order_size': '27076.0', 'expiration': 'NA', 'margin': False}, {'pair': 'cnneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000000.0', 'minimum_order_size': '27076.0', 'expiration': 'NA', 'margin': False}, {'pair': 'boxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1500000.0', 'minimum_order_size': '788.0', 'expiration': 'NA', 'margin': False}, {'pair': 'boxeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1500000.0', 'minimum_order_size': '788.0', 'expiration': 'NA', 'margin': False}, {'pair': 'trxeur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'trxgbp', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'trxjpy', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '208.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mgousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '64.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mgoeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '64.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rteusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '3000000.0', 'minimum_order_size': '1754.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rteeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '3000000.0', 'minimum_order_size': '1754.0', 'expiration': 'NA', 'margin': False}, {'pair': 'yggusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '8000000.0', 'minimum_order_size': '10024.0', 'expiration': 'NA', 'margin': False}, {'pair': 'yggeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '8000000.0', 'minimum_order_size': '10024.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mlnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '0.6', 'expiration': 'NA', 'margin': False}, {'pair': 'mlneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '0.6', 'expiration': 'NA', 'margin': False}, {'pair': 'wtcusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'wtceth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'csxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '150000.0', 'minimum_order_size': '66.0', 'expiration': 'NA', 'margin': False}, {'pair': 'csxeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '150000.0', 'minimum_order_size': '66.0', 'expiration': 'NA', 'margin': False}, {'pair': 'omnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'omnbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '20000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'intusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '218.0', 'expiration': 'NA', 'margin': False}, {'pair': 'inteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '218.0', 'expiration': 'NA', 'margin': False}, {'pair': 'drnusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '42.0', 'expiration': 'NA', 'margin': False}, {'pair': 'drneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '42.0', 'expiration': 'NA', 'margin': False}, {'pair': 'pnkusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '566.0', 'expiration': 'NA', 'margin': False}, {'pair': 'pnketh', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '566.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dgbusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '374.0', 'expiration': 'NA', 'margin': False}, {'pair': 'dgbbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '374.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bsvusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.08', 'expiration': 'NA', 'margin': True}, {'pair': 'bsvbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '0.08', 'expiration': 'NA', 'margin': True}, {'pair': 'babusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': True}, {'pair': 'babbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': True}, {'pair': 'wlousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '160.0', 'expiration': 'NA', 'margin': False}, {'pair': 'wloxlm', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '5000.0', 'minimum_order_size': '160.0', 'expiration': 'NA', 'margin': False}, {'pair': 'vldusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000000.0', 'minimum_order_size': '1250.0', 'expiration': 'NA', 'margin': False}, {'pair': 'vldeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000000.0', 'minimum_order_size': '1250.0', 'expiration': 'NA', 'margin': False}, {'pair': 'enjusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '30.0', 'expiration': 'NA', 'margin': False}, {'pair': 'enjeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500000.0', 'minimum_order_size': '30.0', 'expiration': 'NA', 'margin': False}, {'pair': 'onlusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '250000.0', 'minimum_order_size': '100.0', 'expiration': 'NA', 'margin': False}, {'pair': 'onleth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '250000.0', 'minimum_order_size': '100.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rbtusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500.0', 'minimum_order_size': '0.0008', 'expiration': 'NA', 'margin': False}, {'pair': 'rbtbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '500.0', 'minimum_order_size': '0.0008', 'expiration': 'NA', 'margin': False}, {'pair': 'ustusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': True}, {'pair': 'euteur', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'eutusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'gsdusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'udcusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'tsdusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'paxusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000000.0', 'minimum_order_size': '4.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rifusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '56.0', 'expiration': 'NA', 'margin': False}, {'pair': 'rifbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '56.0', 'expiration': 'NA', 'margin': False}, {'pair': 'pasusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '806.0', 'expiration': 'NA', 'margin': False}, {'pair': 'paseth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '806.0', 'expiration': 'NA', 'margin': False}, {'pair': 'vsyusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '250000.0', 'minimum_order_size': '34.0', 'expiration': 'NA', 'margin': False}, {'pair': 'vsybtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '250000.0', 'minimum_order_size': '34.0', 'expiration': 'NA', 'margin': False}, {'pair': 'zrxdai', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '16.0', 'expiration': 'NA', 'margin': False}, {'pair': 'mkrdai', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '0.008', 'expiration': 'NA', 'margin': False}, {'pair': 'omgdai', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bttusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '15000000.0', 'minimum_order_size': '7340.0', 'expiration': 'NA', 'margin': False}, {'pair': 'bttbtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '15000000.0', 'minimum_order_size': '7340.0', 'expiration': 'NA', 'margin': False}, {'pair': 'btcust', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.0008', 'expiration': 'NA', 'margin': True}, {'pair': 'ethust', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': True}, {'pair': 'clousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '1612.0', 'expiration': 'NA', 'margin': False}, {'pair': 'clobtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '1612.0', 'expiration': 'NA', 'margin': False}, {'pair': 'impusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '220.0', 'expiration': 'NA', 'margin': False}, {'pair': 'impeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '50000.0', 'minimum_order_size': '220.0', 'expiration': 'NA', 'margin': False}, {'pair': 'ltcust', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.06', 'expiration': 'NA', 'margin': False}, {'pair': 'eosust', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '1.0', 'expiration': 'NA', 'margin': False}, {'pair': 'babust', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '2000.0', 'minimum_order_size': '0.02', 'expiration': 'NA', 'margin': False}, {'pair': 'scrusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '52.0', 'expiration': 'NA', 'margin': False}, {'pair': 'screth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '200000.0', 'minimum_order_size': '52.0', 'expiration': 'NA', 'margin': False}, {'pair': 'gnousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000.0', 'minimum_order_size': '0.4', 'expiration': 'NA', 'margin': False}, {'pair': 'gnoeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '1000.0', 'minimum_order_size': '0.4', 'expiration': 'NA', 'margin': False}, {'pair': 'genusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '26.0', 'expiration': 'NA', 'margin': False}, {'pair': 'geneth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '26.0', 'expiration': 'NA', 'margin': False}, {'pair': 'atousd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '0.8', 'expiration': 'NA', 'margin': False}, {'pair': 'atobtc', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '0.8', 'expiration': 'NA', 'margin': False}, {'pair': 'atoeth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '100000.0', 'minimum_order_size': '0.8', 'expiration': 'NA', 'margin': False}, {'pair': 'wbtusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10.0', 'minimum_order_size': '0.0006', 'expiration': 'NA', 'margin': False}, {'pair': 'xchusd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'eususd', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'wbteth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '10.0', 'minimum_order_size': '0.0006', 'expiration': 'NA', 'margin': False}, {'pair': 'xcheth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}, {'pair': 'euseth', 'price_precision': 5, 'initial_margin': '30.0', 'minimum_margin': '15.0', 'maximum_order_size': '30000.0', 'minimum_order_size': '2.0', 'expiration': 'NA', 'margin': False}]

        """
        for d in data:
            contract = VtContractData()
            contract.gatewayName = self.gatewayName
            contract.symbol = d['pair'].upper()                                    # btcusd ---->BTCUSD
            contract.exchange = EXCHANGE_BITFINEX
            contract.vtSymbol = ':'.join([contract.symbol, contract.exchange])      # 合约在vt系统中的唯一代码，通常是 合约代码:交易所代码
            contract.name = contract.vtSymbol                                       # 合约中文名
            contract.productClass = PRODUCT_SPOT                                    # 现货交易
            contract.priceTick = pow(10, d["price_precision"])                      # 10 的5次方
            contract.price_precision = d["price_precision"]
            contract.size = 1                                                       # 合约大小 数字货币现货合约大小为1

            self.gateway.onContract(contract)

        self.writeLog(u'合约信息查询成功')

    # ----------------------------------------------------------------------
    def onQueryPosition(self, data):
        """查询持仓"""
        """
        [{'id': 140620317, 'symbol': 'eosusd', 'status': 'ACTIVE', 'base': '5.0615', 'amount': '6.0', 'timestamp': '1557243822.0', 'swap': '0.0', 'pl': '0.0817764'},
        {'id': 140620418, 'symbol': 'xrpusd', 'status': 'ACTIVE', 'base': '0.3149', 'amount': '16.0', 'timestamp': '1557245090.0', 'swap': '0.0', 'pl': '-0.01199296'}
        ]
          "id":943715,
          "symbol":"btcusd",
          "status":"ACTIVE",
          "base":"246.94",
          "amount":"1.0",
          "timestamp":"1444141857.0",
          "swap":"0.0",
          "pl":"-2.22042"

        """
        # print("----------启动 onQueryPosition ----------------------")
        for d in data:
            longPosition = VtPositionData()
            longPosition.gatewayName = self.gatewayName
            # print(" 启动 onQueryPosition  longPosition.gatewayName  ",longPosition.gatewayName )
            longPosition.symbol = d['symbol'].upper()                                      # btcusd ---->BTCUSD
            longPosition.exchange = EXCHANGE_BITFINEX
            longPosition.vtSymbol = ':'.join([longPosition.symbol, longPosition.exchange])         # 合约在vt系统中的唯一代码，通常是 合约代码:交易所代码
            longPosition.position = int(d['amount'])
            if longPosition.position > 0:
                longPosition.direction = DIRECTION_LONG
                longPosition.vtPositionName = ':'.join([longPosition.vtSymbol, longPosition.direction])
                longPosition.price = float(d['base'])
                longPosition.positionProfit = float(d['pl'])

            # print(" 启动 onQueryPosition  longPosition  ", longPosition)

            shortPosition = copy(longPosition)
            shortPosition.direction = DIRECTION_SHORT
            longPosition.vtPositionName = ':'.join([longPosition.vtSymbol, longPosition.direction])
            longPosition.price = float(d['base'])
            longPosition.positionProfit = float(d['pl'])

            self.gateway.onPosition(longPosition)
            self.gateway.onPosition(shortPosition)
        self.writeLog(u'仓位初始化查询成功')

    # ----------------------------------------------------------------------
    def onQueryAccount(self, data):
        # """{'info': {'eos': {'equity': '9.49516783', 'margin': '0.52631594', 'margin_mode': 'crossed',
        # 'margin_ratio': '18.0408', 'realized_pnl': '-0.6195932', 'total_avail_balance': '10.11476103',
        # 'unrealized_pnl': '0'}}} """
        # """{"info":{"eos":{"contracts":[{"available_qty":"3.9479","fixed_balance":"0",
        #     "instrument_id":"EOS-USD-181228","margin_for_unfilled":"0","margin_frozen":"0",
        #     "realized_pnl":"0.06547904","unrealized_pnl":"0"}],"equity":"3.94797857",
        #     "margin_mode":"fixed","total_avail_balance":"3.88249953"}}}"""
        # for currency, d in data['info'].items():
        #     if 'contracts' in d.keys():  # fixed-margin
        #         self.writeLog('WARNING: temperately not support fixed-margin account')
        #         return
        #     else:
        #         account = VtAccountData()
        #         account.gatewayName = self.gatewayName
        #
        #         account.accountID = currency
        #         account.vtAccountID = VN_SEPARATOR.join([account.gatewayName, account.accountID])
        #
        #         account.balance = float(d['equity'])
        #         account.available = float(d['total_avail_balance'])
        #         account.margin = float(d['margin'])
        #         account.positionProfit = float(d['unrealized_pnl'])
        #         account.closeProfit = float(d['realized_pnl'])
        #
        #     self.gateway.onAccount(account)
        pass














