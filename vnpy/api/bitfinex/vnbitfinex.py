# encoding: UTF-8

from __future__ import print_function
import json
import requests
import traceback
import ssl
from threading import Thread
from queue import Queue, Empty
import time
import hmac
import base64
import hashlib
import websocket

from six.moves import input

WEBSOCKET_V2_URL = 'wss://api.bitfinex.com/ws/2'
RESTFUL_V1_URL = 'https://api.bitfinex.com/v1'
RESTFUL_V1_DOMAIN = 'https://api.bitfinex.com'


########################################################################
class BitfinexApi(object):
    """"""

    # ----------------------------------------------------------------------
    def __init__(self):
        """Constructor"""
        self.ws = None
        self.thread = None

        self.active = False  # 初始化策略的启动状态为

        self.restQueue = Queue()
        self.restThread = None

        self.apiKey = ""
        self.secretKey = ""

    # ----------------------------------------------------------------------
    def start(self):
        """"""
        self.ws = websocket.create_connection(WEBSOCKET_V2_URL,
                                              sslopt={'cert_reqs': ssl.CERT_NONE})

        self.active = True

        self.thread = Thread(target=self.run)  # wes 线程启动
        self.thread.start()

        self.restThread = Thread(target=self.runRest)  # rest 线程启动
        self.restThread.start()

        self.onConnect()

    # ----------------------------------------------------------------------
    def reconnect(self):
        """"""
        self.ws = websocket.create_connection(WEBSOCKET_V2_URL,
                                              sslopt={'cert_reqs': ssl.CERT_NONE})

        self.onConnect()

    # ----------------------------------------------------------------------
    def run(self):
        """"""
        while self.active:
            try:
                stream = self.ws.recv()
                data = json.loads(stream)
                self.onData(data)
            except:
                msg = traceback.format_exc()
                self.onError(msg)
                self.reconnect()

    # ----------------------------------------------------------------------
    def runRest(self):
        """"""
        while self.active:
            try:
                path, callback, post = self.restQueue.get(timeout=1)
                if post:
                    self.httpPost(path, callback)
                else:
                    self.httpGet(path, callback)
            except Empty:
                pass
            except Exception as e:
                print(traceback.format_exc())

    # ----------------------------------------------------------------------
    def close(self):
        """"""
        self.active = False

        if self.thread:
            self.thread.join()

        if self.restThread:
            self.thread.join()

    # ----------------------------------------------------------------------
    def onConnect(self):
        """"""
        print('connected')

    # ----------------------------------------------------------------------
    def onData(self, data):
        """"""
        print(data)

    # ----------------------------------------------------------------------
    def onError(self, msg):
        """"""
        print(msg)

    # ----------------------------------------------------------------------
    def sendReq(self, req):
        """"""
        self.ws.send(json.dumps(req))

        # ----------------------------------------------------------------------

    def sendRestReq(self, path, callback, post):
        """"""
        self.restQueue.put((path, callback, post))

    # ----------------------------------------------------------------------
    def httpGet(self, path, callback):
        """"""
        url = RESTFUL_V1_URL + path
        resp = requests.get(url)
        callback(resp.json())

    def __signature(self, payload):
        j = json.dumps(payload)
        data = base64.standard_b64encode(j.encode('utf8'))

        h = hmac.new(self.secretKey.encode('utf8'), data, hashlib.sha384)
        signature = h.hexdigest()
        return {
            "X-BFX-APIKEY": self.apiKey,
            "X-BFX-SIGNATURE": signature,
            "X-BFX-PAYLOAD": data
        }

    def _post(self, path, params):
        body = params
        rawBody = json.dumps(body)
        headers = self.__signature(body)
        url = RESTFUL_V1_DOMAIN + path
        resp = requests.post(url, headers=headers, data=rawBody, verify=True)

        return resp

    def httpPost(self, path, callback):
        """"""
        if path.startswith("/"):
            v1_path = "/v1" + path
        else:
            v1_path = '/v1/' + path

        payload = {
            'request': v1_path,
            'nonce': str(int(time.time() * 1000000))  # nonce
        }
        resp = self._post(v1_path, payload)
        callback(resp.json())



# 进行回测主要查看返回的数据结果
if __name__ == '__main__':

    ###############################################################################################################REST测试

    # # ------------------------------------------------------- restful public api         'RESTFUL_V1_URL = 'https://api.bitfinex.com/v1'
    api = BitfinexApi()                    # 包含self.secretKey  self.accessKey
    api.start()                            #启动线程
    # print("*******************************************  pubticker ****************************************************************")
    # time.sleep(5)
    # api.httpGet('/pubticker/btcusd', api.onData)      #def httpGet(self, path, callback):  注意这里的path = '/....'  回调函数就是打印
    # print("*******************************************  stats ****************************************************************")
    # time.sleep(5)
    # api.httpGet('/stats/btcusd', api.onData)
    # print("*******************************************  book ****************************************************************")
    # time.sleep(5)
    # api.httpGet('/book/btcusd', api.onData)
    # print("*******************************************  trades ****************************************************************")
    # time.sleep(5)
    # api.httpGet('/trades/btcusd', api.onData)
    # print("*******************************************  symbols ****************************************************************")
    # time.sleep(5)
    # api.httpGet('/symbols', api.onData)
    print("*******************************************  symbols_details ****************************************************************")
    time.sleep(5)
    api.httpGet('/symbols_details', api.onData)

    # ----------------------------------------------restful private  api   即使用post的方式进行处理  'RESTFUL_V1_URL = 'https://api.bitfinex.com/v1'

    api = BitfinexApi()  # 包含self.secretKey  self.accessKey
    api.start()  # 启动线程
    # print("*******************************************  account_infos ****************************************************************")
    # time.sleep(5)
    # api.httpPost('/account_infos', api.onData)

    print(
        "*******************************************  margin_infos ****************************************************************")
    time.sleep(5)
    rev = api.httpPost('/margin_infos', api.onData)

    """

    """

    # account_info = rev[0]
    # print("account_info",account_info)

    # print("*******************************************  account_fees ****************************************************************")
    # time.sleep(5)
    # api.httpPost('/account_fees', api.onData)
    #
    # print("*******************************************  key_info ****************************************************************")
    # time.sleep(5)
    # api.httpPost('/key_info', api.onData)
    #
    # print(
    #     "*******************************************  balances ****************************************************************")
    # time.sleep(5)
    # api.httpPost('/balances', api.onData)

    print(
        "*******************************************  active positions ****************************************************************")
    time.sleep(5)
    # api.httpPost('/positions', api.onData)

    api.sendRestReq('/positions', api.onData, post=True)
    """
[{'id': 140620317, 'symbol': 'eosusd', 'status': 'ACTIVE', 'base': '5.0615', 'amount': '6.0', 'timestamp': '1557243822.0', 'swap': '0.0', 'pl': '0.0817764'},
{'id': 140620418, 'symbol': 'xrpusd', 'status': 'ACTIVE', 'base': '0.3149', 'amount': '16.0', 'timestamp': '1557245090.0', 'swap': '0.0', 'pl': '-0.01199296'}
]
    """

# [{
#   "margin_balance":"14.80039951",
#   "tradable_balance":"-12.50620089",
#   "unrealized_pl":"-0.18392",
#   "unrealized_swap":"-0.00038653",
#   "net_value":"14.61609298",
#   "required_margin":"7.3569",
#   "leverage":"2.5",
#   "margin_requirement":"13.0",
#   "margin_limits":[{
#     "on_pair":"BTCUSD",
#     "initial_margin":"30.0",
#     "margin_requirement":"15.0",
#     "tradable_balance":"-0.329243259666666667"
#   }
#   [{'margin_balance': '336.6680839',
#     'tradable_balance': '806.71960255',
#     'unrealized_pl': '0.18271712',
#     'unrealized_swap': '0.0',
#     'net_value': '336.85080102',
#     'required_margin': '5.31111',
#     'leverage': '2.5',
#     'margin_requirement': '0.0',
#     'margin_limits':
#
#                     [{'on_pair': 'BTCUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'},
#
#                      {'on_pair': 'LTCUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'},
#                      {'on_pair': 'LTCBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'},
#                     {'on_pair': 'ETHUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'ETHBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ETCBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ETCUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'ZECUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'ZECBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'XMRUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'XMRBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'DSHUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'DSHBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'BTCEUR', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'BTCJPY', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'XRPUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'XRPBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'IOTUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'IOTBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'IOTETH', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'EOSUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'EOSBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'EOSETH', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'SANUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'SANBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'SANETH', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'OMGUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'OMGBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'OMGETH', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'NEOUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'NEOBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'NEOETH', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ETPUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'ETPBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ETPETH', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'EDOUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'EDOBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'EDOETH', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'BTGUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'BTGBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'IOTEUR', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ZRXUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'ZRXETH', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'BTCGBP', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ETHEUR', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ETHJPY', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ETHGBP', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'NEOEUR', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'NEOJPY', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'NEOGBP', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'EOSEUR', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'EOSJPY', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'EOSGBP', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'IOTJPY', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'IOTGBP', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'BSVUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'BSVBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'BABUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'BABBTC', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'USTUSD', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '1086.72417823'}, {'on_pair': 'BTCUST', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}, {'on_pair': 'ETHUST', 'initial_margin': '30.0', 'margin_requirement': '15.0', 'tradable_balance': '986.42817823'}], 'message': 'Margin requirement, leverage and tradable balance are now per pair. Values displayed in the root of the JSON message are incorrect (deprecated). '
#                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              'You will find the correct ones under margin_limits, for each pair. Please update your code as soon as possible.'}]]