import os
import json
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from copy import copy
from urllib.parse import urlencode
import pandas as pd
import requests
from requests import ConnectionError

from vnpy.api.rest import RestClient, Request
from vnpy.api.websocket import WebsocketClient
from vnpy.trader.vtGateway import *
from .okutil import generateSignature, ERRORCODE
from vnpy.trader.constant import Direction, Status, Offset, PriceType, Product, Format

# 委托状态类型映射
statusMapReverse = {}
statusMapReverse['open'] = Status.NOTTRADED           # SpotOrder
statusMapReverse['part_filled'] = Status.PARTTRADED
statusMapReverse['filled'] = Status.ALLTRADED
statusMapReverse['cancelled'] = Status.CANCELLED
statusMapReverse['failure'] = Status.REJECTED

# 方向和开平映射
typeMap = {}
typeMap[(Direction.LONG, Offset.OPEN)] = 'buy'
typeMap[(Direction.SHORT, Offset.CLOSE)] = 'sell'
typeMapReverse = {v:k for k,v in typeMap.items()}

SUBGATEWAY_NAME = "SPOT"
#############################################################################################################
class OkexSpotRestApi(RestClient):
    """SPOT REST API实现"""

    #----------------------------------------------------------------------
    def __init__(self, gateway):
        """Constructor"""
        super(OkexSpotRestApi, self).__init__()

        self.gateway = gateway                  # type: okexGateway # gateway对象
        self.gatewayName = gateway.gatewayName  # gateway对象名称
        self.leverage = 0
        
        self.contractDict = {}    # store contract info
        self.orderDict = {}       # store order info
        self.okexIDMap = {}       # store okexID <-> OID
    
    #----------------------------------------------------------------------
    def connect(self, REST_HOST, leverage, sessionCount):
        """连接服务器"""
        self.leverage = leverage
        
        self.init(REST_HOST)
        self.start(sessionCount)
        self.gateway.writeLog(f'{SUBGATEWAY_NAME} REST API 连接成功')
        self.queryContract()
    #----------------------------------------------------------------------
    def sign(self, request):
        """okex的签名方案"""
        # 生成签名
        timestamp = (datetime.utcnow().isoformat()[:-3]+'Z')
        request.data = json.dumps(request.data)
        
        if request.params:
            path = request.path + '?' + urlencode(request.params)
        else:
            path = request.path
            
        msg = timestamp + request.method + path + request.data
        signature = generateSignature(msg, self.gateway.apiSecret)
        
        # 添加表头
        request.headers = {
            'OK-ACCESS-KEY': self.gateway.apiKey,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.gateway.passphrase,
            'Content-Type': 'application/json'
        }
        return request
    # ----------------------------------------------------------------------
    def sign2(self, request):
        """okex的签名方案, 针对全平和全撤接口"""
        # 生成签名
        timestamp = (datetime.utcnow().isoformat()[:-3] + 'Z') 

        if request.params:
            path = request.path + '?' + urlencode(request.params)
        else:
            path = request.path

        if request.data:
            request.data = json.dumps(request.data)
            msg = timestamp + request.method + path + request.data
        else:
            msg = timestamp + request.method + path
        signature = generateSignature(msg, self.gateway.apiSecret)

        # 添加表头
        request.headers = {
            'OK-ACCESS-KEY': self.gateway.apiKey,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.gateway.passphrase,
            'Content-Type': 'application/json'
        }
        return request

    #----------------------------------------------------------------------
    def sendOrder(self, orderReq, orderID):# type: (VtOrderReq)->str
        """限速规则：100次/2s"""
        type_ = typeMap[(orderReq.direction, orderReq.offset)]

        data = {
            'client_oid': orderID,
            'instrument_id': str.lower(orderReq.symbol),
            'side': type_,
            'size': float(orderReq.volume)    # in market order means quantity sold
        }
        if orderReq.priceType == PriceType.LIMITPRICE:
            data["type"] = "limit"
            data["price"] = orderReq.price
        elif orderReq.priceType == PriceType.MARKETPRICE:
            data["type"] = "market"
            data["notional"] = orderReq.price    # in market order means amount bought

        if self.leverage > 0:
            data["margin_trading"] = 2
        else:
            data["margin_trading"] = 1
        
        
        order = VtOrderData()
        order.gatewayName = self.gatewayName
        order.symbol = orderReq.symbol
        order.exchange = 'OKEX'
        order.vtSymbol = Format.VN_SEPARATOR.join([order.symbol, order.gatewayName])
        order.orderID = orderID
        order.vtOrderID = Format.VN_SEPARATOR.join([self.gatewayName, order.orderID])
        order.direction = orderReq.direction
        order.offset = orderReq.offset
        order.price = orderReq.price
        order.totalVolume = orderReq.volume
        
        self.addRequest('POST', '/api/spot/v3/orders', 
                        callback=self.onSendOrder, 
                        data=data, 
                        extra=order,
                        onFailed=self.onSendOrderFailed,
                        onError=self.onSendOrderError)

        self.orderDict[orderID] = order
        return order.vtOrderID
    
    #----------------------------------------------------------------------
    def cancelOrder(self, cancelOrderReq):
        """限速规则：100次/2s"""
        req = {
            'instrument_id': cancelOrderReq.symbol,
        }
        path = f'/api/spot/v3/cancel_orders/{cancelOrderReq.orderID}'
        self.addRequest('POST', path, 
                        callback=self.onCancelOrder,
                        data=req)

    #----------------------------------------------------------------------
    def queryContract(self):
        """限速规则：20次/2s"""
        self.addRequest('GET', '/api/spot/v3/instruments', 
                        callback=self.onQueryContract)
    
    #----------------------------------------------------------------------
    def queryMonoAccount(self, symbol):
        """限速规则：20次/2s"""
        sym = str.lower(symbol.split("-")[0])
        self.addRequest('GET', f'/api/spot/v3/accounts/{sym}', 
                        callback=self.onQueryMonoAccount)
    def queryAccount(self):
        """限速规则：20次/2s"""
        self.addRequest('GET', '/api/spot/v3/accounts', 
                        callback=self.onQueryAccount)
    #----------------------------------------------------------------------
    def queryMonoPosition(self, symbol):
        """占位"""
        pass
    def queryPosition(self):
        """占位"""
        pass
    #----------------------------------------------------------------------
    def queryOrder(self):
        """限速规则：20次/2s"""
        self.gateway.writeLog('\n\n----------start Quary SPOT Orders,positions,Accounts---------------')
        for symbol in self.gateway.gatewayMap[SUBGATEWAY_NAME]["symbols"]:  
            # 未成交
            req = {
                'instrument_id': symbol
            }
            path = f'/api/spot/v3/orders_pending'
            self.addRequest('GET', path, params=req,
                            callback=self.onQueryOrder)

    # ----------------------------------------------------------------------
    def batch_cancel_order(self, symbol=None, orderIDs=None):
        """撤销所有挂单,若交易所支持批量撤单,使用批量撤单接口

        Parameters
        ----------
        symbol : str, optional
            用逗号隔开的多个合约代码,表示只撤销这些合约的挂单(默认为None,表示撤销所有合约的所有挂单)
        orders : str, optional
            用逗号隔开的多个vtOrderID.
            若为None,先从交易所先查询所有未完成订单作为待撤销订单列表进行撤单;
            若不为None,则对给出的对应订单中和symbol参数相匹配的订单作为待撤销订单列表进行撤单。
        Return
        ------
        vtOrderIDs: list of str
        包含本次所有撤销的订单ID的列表
        """
        vtOrderIDs = []
        # 未完成(未成交和部分成交)
        req = {
            'instrument_id': symbol,
        }
        path = f'/api/spot/v3/orders_pending'
        request = Request('GET', path, params=req, callback=None, data=None, headers=None)
        request = self.sign2(request)
        request.extra = orderIDs
        url = self.makeFullUrl(request.path)
        response = requests.get(url, headers=request.headers, params=request.params)
        data = response.json()

        if data:
            rsp = self.onCancelAll(data, request)
            # failed rsp: {'code': 33027, 'message': 'Order has been revoked or revoked'}
            if "code" in rsp.keys():
                self.gateway.writeLog(f"交易所返回{symbol}撤单失败:{rsp['message']}")
                return []
            # rsp: {'eth-usdt': 
            # {'result': True, 'client_oid': '', 
            # 'order_id': ['2432470701654016', '2432470087389184', '2432469715472384']}}
            for sym, result in rsp.items():
                if result['result']:
                    vtOrderIDs += result['order_id']
                    self.gateway.writeLog(f"交易所返回{sym}撤单成功: ids: {result['order_id']}")
        return vtOrderIDs

    def onCancelAll(self, data, request):
        orderids = [str(order['order_id']) for order in data if
                   order['status'] == 'open' or order['status'] == 'part_filled']
        if request.extra:
            orderids = list(set(orderids).intersection(set(request.extra.split(","))))
        for i in range(len(orderids) // 10 + 1):
            orderid = orderids[i * 10:(i + 1) * 10]

            req = [{
                'instrument_id': str.lower(request.params['instrument_id']),
                'order_ids': orderid
            }]
            path = "/api/spot/v3/cancel_batch_orders"
            # self.addRequest('POST', path, data=req, callback=self.onCancelAll)
            request = Request('POST', path, params=None, callback=None, data=req, headers=None)

            request = self.sign(request)
            url = self.makeFullUrl(request.path)
            response = requests.post(url, headers=request.headers, data=request.data)
            return response.json()

    def closeAll(self, symbol, standard_token = None):
        """以市价单的方式全平某个合约的当前仓位,若交易所支持批量下单,使用批量下单接口

        Parameters
        ----------
        symbols : str
            所要平仓的合约代码,多个合约代码用逗号分隔开。
        direction : str, optional
            账户统一到某个币本位上

        Return
        ------
        vtOrderIDs: list of str
        包含平仓操作发送的所有订单ID的列表
        """
        if not standard_token:
            return []
        vtOrderIDs = []
        base_currency, quote_currency = symbol.split("-")
        if base_currency == standard_token:
            path = f'/api/spot/v3/accounts/{str.lower(quote_currency)}'
            side = 'buy'
        elif quote_currency == standard_token:
            path = f'/api/spot/v3/accounts/{str.lower(base_currency)}'
            side = 'sell'
        else:
            return []  # 币对双方都不是指定的本位
        request = Request('GET', path, params=None, callback=None, data=None, headers=None)

        request = self.sign2(request)
        request.extra = {"instrument_id": symbol, 
                         "side": side }
        url = self.makeFullUrl(request.path)
        response = requests.get(url, headers=request.headers, params=request.params)
        data = response.json()
        rsp = self.onCloseAll(data, request)
        # rsp: [{'client_oid': '', 'order_id': '2433076975049728', 'result': True}]
        # failed: [{'code': 30024, 'message': 'Parameter value filling error'}]
        for result in rsp:
            if "code" in result.keys():
                self.gateway.writeLog(f'换币失败:{result}')
            elif "result" in result.keys():
                if result['result']:
                    vtOrderIDs.append(result['order_id'])
                    self.gateway.writeLog(f'换币成功:{result}')
        
        return vtOrderIDs

    def onCloseAll(self, data, request):
        l = []
        
        def _response(request, l):
            request = self.sign(request)
            url = self.makeFullUrl(request.path)
            response = requests.post(url, headers=request.headers, data=request.data)
            l.append(response.json())
            return l

        req = {
            'client_oid': None,
            'instrument_id': request.extra['instrument_id'],
            'type': "market",
            'side': request.extra['side'], 
            'notional': data['available'],  # buy amount
            'size': data['available']       # sell quantity
        }

        if self.leverage > 0:
            req["margin_trading"] = 2
        else:
            req["margin_trading"] = 1
        path = '/api/spot/v3/orders'
        request = Request('POST', path, params=None, callback=None, data=req, headers=None)
        l = _response(request, l)
        return l

    #----------------------------------------------------------------------
    def onQueryContract(self, data, request):
        """ [{"base_currency":"DASH","base_increment":"0.000001","base_min_size":"0.001","instrument_id":"DASH-BTC",
        "min_size":"0.001","product_id":"DASH-BTC","quote_currency":"BTC","quote_increment":"0.00000001",
        "size_increment":"0.000001","tick_size":"0.00000001"}"""
        for d in data:
            contract = VtContractData()
            contract.gatewayName = self.gatewayName
            
            contract.symbol = d['instrument_id']
            contract.exchange = 'OKEX'
            contract.vtSymbol = Format.VN_SEPARATOR.join([contract.symbol, contract.gatewayName])
            
            contract.name = contract.symbol
            contract.productClass = Product.FUTURES
            contract.priceTick = float(d['tick_size'])
            contract.size = float(d['size_increment'])
            
            self.contractDict[contract.symbol] = contract

            self.gateway.onContract(contract)
        self.gateway.writeLog(u'OKEX 现货币对信息查询成功')

        self.queryOrder()
        self.queryAccount()
        
    #----------------------------------------------------------------------
    def processAccountData(self, data):
        account = VtAccountData()
        account.gatewayName = self.gatewayName
        
        account.accountID = "_".join([data['currency'], SUBGATEWAY_NAME])
        account.vtAccountID = Format.VN_SEPARATOR.join([account.gatewayName, account.accountID])
        
        account.balance = float(data['balance'])
        account.available = float(data['available'])
        
        self.gateway.onAccount(account)

    def onQueryMonoAccount(self, data, request):
        """{'frozen': '0', 'hold': '0', 'id': '8445285', 'currency': 'ETH', 
        'balance': '0', 'available': '0', 'holds': '0'}"""
        self.processAccountData(data)

    def onQueryAccount(self, data, request):
        """[{"frozen":"0","hold":"0","id":"8445285","currency":"BTC","balance":"0.00000000723",
        "available":"0.00000000723","holds":"0"},
        {"frozen":"0","hold":"0","id":"8445285","currency":"ETH","balance":"0.0100001336",
        "available":"0.0100001336","holds":"0"}]"""
        for account_info in data:
            self.processAccountData(account_info)
    
    #----------------------------------------------------------------------
    def processOrderData(self, data):
        okexID = data['order_id']
        if "client_oid" not in data.keys():
            oid = self.okexIDMap.get(okexID, "not_exist")
        else:
            if data["client_oid"] == "0":
                data["client_oid"] = ""
            oid = str(data['client_oid'])
        order = self.orderDict.get(oid, None)
        
        if not order:
            order = self.gateway.newOrderObject(data)
            order.totalVolume = float(data['size'])
            order.tradedVolume = 0.0
            order.direction, order.offset = typeMapReverse[str(data['side'])]
        
        # update order info
        incremental_filled_size = float(data['filled_size'])
        if incremental_filled_size:
            order.price_avg = float(data['filled_notional'])/incremental_filled_size
        else:
            order.price_avg = 0.0
        order.deliveryTime = datetime.now()
        order.thisTradedVolume = incremental_filled_size - order.tradedVolume
        order.status = statusMapReverse[str(data['status'])]
        order.tradedVolume = incremental_filled_size

        self.gateway.onOrder(copy(order))
        self.orderDict[oid] = order

        if order.thisTradedVolume:
            self.gateway.newTradeObject(order)
            
        if order.status in Status.FINISHED:
            finish_id = self.okexIDMap.get(okexID, None)
            if finish_id:
                del self.okexIDMap[okexID]
            finish_order = self.orderDict.get(oid, None)
            if finish_order:
                del self.orderDict[oid]

    def onQueryOrder(self, d, request):
        """{
            "order_id": "233456", "notional": "10000.0000", "price"："8014.23"， "size"："4", "instrument_id": "BTC-USDT",  
            "side": "buy", "type": "market", "timestamp": "2016-12-08T20:09:05.508Z",
            "filled_size": "0.1291771", "filled_notional": "10000.0000", "status": "filled"
        }"""
        for data in d:
            self.processOrderData(data)

    #----------------------------------------------------------------------
    def onSendOrderFailed(self, data, request):
        """
        下单失败回调：服务器明确告知下单失败
        """
        self.gateway.writeLog(f"{data} onsendorderfailed, {request.response.text}")
        order = request.extra
        order.status = Status.REJECTED
        order.rejectedInfo = str(eval(request.response.text)['code']) + ' ' + eval(request.response.text)['message']
        self.gateway.onOrder(order)
    
    #----------------------------------------------------------------------
    def onSendOrderError(self, exceptionType, exceptionValue, tb, request):
        """
        下单失败回调：连接错误
        """
        self.gateway.writeLog(f"{exceptionType} onsendordererror, {exceptionValue}")
        order = request.extra
        order.status = Status.UNKNOWN
        order.rejectedInfo = "onSendOrderError: OKEX not response or network issue"
        #str(eval(request.response.text)['code']) + ' ' + eval(request.response.text)['message']
        self.gateway.onOrder(order)
    
    #----------------------------------------------------------------------
    def onSendOrder(self, data, request):
        """1:{'client_oid': 'SPOT19030511351110001', 'order_id': '2426209593007104', 'result': True}
           2: http400 if rejected
        """
        self.okexIDMap[data['order_id']] = data['client_oid']
        self.gateway.writeLog(f"RECORD: successful order, oid:{data['client_oid']} <--> okex_id:{data['order_id']}")

    #----------------------------------------------------------------------
    def onCancelOrder(self, data, request):
        """ 1: {'result': True, 'order_id': '1882519016480768', 'instrument_id': 'EOS-USD-181130'} 
            2: failed cancel order: http400
        """
        if data['result']:
            rsp = eval(request.data)
            oid = request.path.split("/")[-1]
            self.gateway.writeLog(f"交易所返回{rsp['instrument_id']}撤单成功: oid-{oid}")
        else:
            self.gateway.writeLog(f"WARNING: cancelorder error, oid:{data['client_oid']}, msg:{data['error_code']},{data['error_message']}")

    #----------------------------------------------------------------------
    def onFailed(self, httpStatusCode, request):  # type:(int, Request)->None
        """
        请求失败处理函数（HttpStatusCode!=2xx）.
        默认行为是打印到stderr
        """
        print("onfailed", request)
        e = VtErrorData()
        e.gatewayName = self.gatewayName
        e.errorID = str(httpStatusCode)
        e.errorMsg = str(httpStatusCode) #request.response.text
        self.gateway.onError(e)
    
    #----------------------------------------------------------------------
    def onError(self, exceptionType, exceptionValue, tb, request):
        """
        Python内部错误处理：默认行为是仍给excepthook
        """
        print(request, "onerror")
        e = VtErrorData()
        e.gatewayName = self.gatewayName
        e.errorID = exceptionType
        e.errorMsg = exceptionValue
        self.gateway.onError(e)

        sys.stderr.write(self.exceptionDetail(exceptionType, exceptionValue, tb, request))

    def loadHistoryBar(self, REST_HOST, symbol, req):
        """[{"close":"3417.0365","high":"3444.0271","low":"3412.535","open":"3432.194",
        "time":"2018-12-11T09:00:00.000Z","volume":"1215.46194777"}]"""

        url = f'{REST_HOST}/api/spot/v3/instruments/{symbol}/candles'
        r = requests.get(url, headers={"contentType": "application/x-www-form-urlencoded"}, params = req, timeout=10)
        text = eval(r.text)
        return pd.DataFrame(text, columns=["time", "open", "high", "low", "close", "volume"])

class OkexSpotWebsocketApi(WebsocketClient):
    """SPOT WS API"""
    #----------------------------------------------------------------------
    def __init__(self, gateway):
        """Constructor"""
        super(OkexSpotWebsocketApi, self).__init__()
        
        self.gateway = gateway
        self.gatewayName = gateway.gatewayName
        self.restGateway = None

        self.callbackDict = {}
        self.tickDict = {}
    
    #----------------------------------------------------------------------
    def unpackData(self, data):
        """重载"""
        return json.loads(zlib.decompress(data, -zlib.MAX_WBITS))
        
    #----------------------------------------------------------------------
    def connect(self, WEBSOCKET_HOST):
        """"""
        self.restGateway = self.gateway.gatewayMap[SUBGATEWAY_NAME]["REST"]
        self.init(WEBSOCKET_HOST)
        self.start()
        
    #----------------------------------------------------------------------
    def onConnected(self):
        """连接回调"""
        self.login()
        self.gateway.writeLog(f'{SUBGATEWAY_NAME} Websocket API连接成功')
        
    #----------------------------------------------------------------------
    def onDisconnected(self):
        """连接回调"""
        self.gateway.writeLog(f'{SUBGATEWAY_NAME} Websocket API连接断开')
    
    #----------------------------------------------------------------------
    def onPacket(self, packet):
        """数据回调"""
        if 'event' in packet.keys():
            if packet['event'] == 'login':
                callback = self.callbackDict['login']
                callback(packet)
            elif packet['event'] == 'subscribe':
                self.gateway.writeLog(f"subscribe {packet['channel']} successfully")
            else:
                self.gateway.writeLog(f"{SUBGATEWAY_NAME} info {packet}")
        elif 'error_code' in packet.keys():
            print(f"{SUBGATEWAY_NAME} error:{packet['error_code']}")
        else:
            channel = packet['table']
            callback = self.callbackDict.get(channel, None)
            if callback:
                callback(packet['data'])
    
    #----------------------------------------------------------------------
    def onError(self, exceptionType, exceptionValue, tb):
        """Python错误回调"""
        e = VtErrorData()
        e.gatewayName = self.gatewayName
        e.errorID = exceptionType
        e.errorMsg = exceptionValue
        self.gateway.onError(e)
        
        sys.stderr.write(self.exceptionDetail(exceptionType, exceptionValue, tb))

    #----------------------------------------------------------------------
    def login(self):
        """"""
        timestamp = str(time.time())
        
        msg = timestamp + 'GET' + '/users/self/verify'
        signature = generateSignature(msg, self.gateway.apiSecret)
        
        req = {
                "op": "login",
                "args": [self.gateway.apiKey,
                        self.gateway.passphrase,
                        timestamp,
                        str(signature, encoding = "utf-8")]
                }
        self.sendPacket(req)
        
        self.callbackDict['login'] = self.onLogin
        
    #----------------------------------------------------------------------
    def subscribe(self, symbol):
        """"""
        # 订阅TICKER
        self.callbackDict['spot/ticker'] = self.onSpotTick
        req1 = {
            'op': 'subscribe',
            'args': f'spot/ticker:{symbol}'
        }
        self.sendPacket(req1)
        
        # 订阅DEPTH
        self.callbackDict['spot/depth5'] = self.onSpotDepth
        req2 = {
            'op': 'subscribe',
            'args': f'spot/depth5:{symbol}'
        }
        self.sendPacket(req2)

        # subscribe trade
        self.callbackDict['spot/trade'] = self.onSpotTrades
        req3 = {
            'op': 'subscribe',
            'args': f'spot/trade:{symbol}'
        }
        self.sendPacket(req3)
        
        # 创建Tick对象
        tick = VtTickData()
        tick.gatewayName = self.gatewayName
        tick.symbol = symbol
        tick.exchange = 'OKEX'
        tick.vtSymbol = Format.VN_SEPARATOR.join([tick.symbol, tick.gatewayName])

        self.tickDict[tick.symbol] = tick
    
    #----------------------------------------------------------------------
    def onLogin(self, d):
        """"""
        if not d['success']:
            return
        
        # 订阅交易相关推送
        self.callbackDict['spot/order'] = self.onTrade
        self.callbackDict['spot/account'] = self.onAccount

        for currency in self.gateway.gatewayMap[SUBGATEWAY_NAME]["symbols"]:
            self.subscribe(currency)
            self.sendPacket({'op': 'subscribe', 'args': f'spot/account:{currency.split("-")[0]}'})
            self.sendPacket({'op': 'subscribe', 'args': f'spot/order:{currency}'})
    #----------------------------------------------------------------------
    def onSpotTick(self, d):
        """{'table': 'spot/ticker', 'data': [{
            'instrument_id': 'ETH-USDT', 'last': '120.2243', 'best_bid': '120.1841', 'best_ask': '120.2242', 
            'open_24h': '121.1132', 'high_24h': '121.7449', 'low_24h': '117.5606', 'base_volume_24h': '386475.880496', 
            'quote_volume_24h': '46309890.36931134', 'timestamp': '2019-01-19T08:50:40.943Z'}]} """
        for idx, data in enumerate(d):
            tick = self.tickDict[data['instrument_id']]
            
            tick.lastPrice = float(data['last'])
            tick.highPrice = float(data['high_24h'])
            tick.lowPrice = float(data['low_24h'])
            tick.volume = float(data['quote_volume_24h'])
            tick.askPrice1 = float(data['best_ask'])
            tick.bidPrice1 = float(data['best_bid'])
            tick.datetime, tick.date, tick.time = self.gateway.convertDatetime(data['timestamp'])
            tick.localTime = datetime.now()
            tick.volumeChange = 0
            tick.lastVolume = 0
            
            if tick.askPrice5:    
                tick=copy(tick)
                self.gateway.onTick(tick)

    #----------------------------------------------------------------------
    def onSpotDepth(self, d):
        """{'table': 'spot/depth5', 'data': [{
            'asks': [['120.2248', '1.468488', 1], ['120.2397', '14.27', 1], ['120.2424', '0.3', 1], 
            ['120.2454', '3.805996', 1], ['120.25', '0.003', 1]], 
            'bids': [['120.1868', '0.12', 1], ['120.1732', '0.354665', 1], ['120.1679', '21.15', 1], 
            ['120.1678', '23.25', 1], ['120.1677', '18.760982', 1]], 
            'instrument_id': 'ETH-USDT', 'timestamp': '2019-01-19T08:50:41.422Z'}]} """
        for data in d:
            tick = self.tickDict[data['instrument_id']]
            
            for idx, buf in enumerate(data['bids']):
                price, volume = buf[:2]
                tick.__setattr__(f'bidPrice{(idx +1)}', float(price))
                tick.__setattr__(f'bidVolume{(idx +1)}', float(volume))
            
            for idx, buf in enumerate(data['asks']):
                price, volume = buf[:2]
                tick.__setattr__(f'askPrice{(10-idx)}', float(price))
                tick.__setattr__(f'askVolume{(10-idx)}', float(volume))
            
            tick.datetime, tick.date, tick.time = self.gateway.convertDatetime(data['timestamp'])
            tick.localTime = datetime.now()
            tick.volumeChange = 0
            tick.lastVolume = 0
            if tick.lastPrice:
                tick=copy(tick)
                self.gateway.onTick(tick)

    def onSpotTrades(self,d):
        """[{'instrument_id': 'ETH-USDT', 'price': '120.32', 'side': 'buy', 'size': '1.628', 
        'timestamp': '2019-01-19T08:56:48.669Z', 'trade_id': '782430504'}]"""
        for idx, data in enumerate(d):
            tick = self.tickDict[data['instrument_id']]
            tick.lastPrice = float(data['price'])
            tick.lastVolume = float(data['size'])
            tick.type = data['side']
            tick.volumeChange = 1
            tick.datetime, tick.date, tick.time = self.gateway.convertDatetime(data['timestamp'])
            tick.localTime = datetime.now()
            if tick.askPrice1:
                tick=copy(tick)
                self.gateway.onTick(tick)
    #---------------------------------------------------
    def onTrade(self, d):
        """{
            "table": "spot/order",
            "data": [{'client_oid': 'SPOT19030511282010001', 'filled_notional': '0', 'filled_size': '0', 
            'instrument_id': 'ETH-USDT', 'margin_trading': '1', 'notional': '', 'order_id': '2426182069529600', 
            'order_type': '0', 'price': '252.0066', 'side': 'sell', 'size': '0.01', 'status': 'open', 
            'timestamp': '2019-03-05T03:30:00.000Z', 'type': 'limit'}]
        }"""
        for idx, data in enumerate(d):
            self.restGateway.processOrderData(data)
        
    #----------------------------------------------------------------------
    def onAccount(self, d):
        """[{'balance': '0.0100001336', 'available': '0.0100001336', 'currency': 'ETH', 'id': '8445285', 'hold': '0'}]"""
        for idx, data in enumerate(d):
            self.restGateway.processAccountData(data)