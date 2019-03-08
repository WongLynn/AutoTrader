# encoding: utf-8
import smtplib
import multiprocessing
import multiprocessing
import threading
import queue
import time
import traceback
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime

from vnpy.trader.vtGlobal import globalSetting

from . import Singleton
from . import LoggerMixin

logger = LoggerMixin()

def get_empty(q):
    if isinstance(q, multiprocessing.queues.Queue):
        Empty = multiprocessing.queues.Empty
    elif isinstance(q, queue.Queue):
        Empty = queue.Empty
    return Empty

class StrategyInfo(object):
    def __init__(self):
        self.name = "Unknown"
        self.mailAdd = []
    
    @classmethod
    def from_strategy(cls, strategy):
        obj = cls()
        obj.name = strategy.name
        obj.mailAdd = strategy.mailAdd    
        return obj

class MailRequest(object):
    def __init__(self):
        self.id = None
        self.content = None
        self.strategy = None
        self.retry = 0

class MailSender(object):
    interval = 10

    def __init__(self):
        self.mail_account = globalSetting['mailAccount']
        self.mail_pass = globalSetting['mailPass']
        self.mail_server = globalSetting['mailServer']
        self.mail_port = globalSetting['mailPort']
        self.server = None

    def _get_server(self):
        if not (self.mail_account and self.mail_pass and self.mail_port and self.mail_server):
            raise ValueError("Please fill sender\'s mail info in vtSetting.json")
        server = smtplib.SMTP_SSL(self.mail_server, self.mail_port, timeout = 10)
        server.login(self.mail_account, self.mail_pass)
        return server

    def send(self, req):
        if not self.server:
            self.server = self._get_server()
        server = self.server
        strategy = req.strategy
        if strategy.mailAdd:
            if len(strategy.mailAdd)>1:
                to_receiver = strategy.mailAdd[0]
                cc_receiver = strategy.mailAdd[1:len(strategy.mailAdd)]
                cc_receiver = ",".join(cc_receiver)
                my_receiver = ",".join([to_receiver,cc_receiver])
            elif len(strategy.mailAdd)==1:
                to_receiver = my_receiver = strategy.mailAdd[0]
                cc_receiver = ""
        else:
            raise ValueError("Please fill email address in ctaSetting.json")
    
        content = req.content + "<br><br> from strategy: "+ strategy.name + "<br><br>Good Luck<br>" + datetime.now().strftime("%Y%m%d %H:%M:%S")
        msg = MIMEText(content, 'html', 'utf-8')
        msg['From'] = formataddr(['VNPY_CryptoCurrency', self.mail_account])
        msg['To'] = to_receiver#formataddr(["收件人昵称",to_receiver])
        if cc_receiver:
            msg['Cc'] = cc_receiver#formataddr(["CC收件人昵称",cc_receiver])
        msg['Subject'] = '策略信息播报'
        msg = msg.as_string()

        try:
            if cc_receiver:
                server.sendmail(self.mail_account, [to_receiver, cc_receiver], msg)
            else:
                server.sendmail(self.mail_account, [to_receiver], msg)
        except Exception as e:
            # reconnect to the server at next time.
            self.server = None
            server.quit()
            raise e

    def run(self, qin, qout):
        Empty = get_empty(qin)
        while True:
            req = None
            try:
                req = qin.get(timeout=1)
                self.send(req)
                qout.put((req, None))
                time.sleep(self.interval)
            except Empty:
                pass
            except Exception as e:
                error = traceback.format_exc()
                qout.put((req, error))
                time.sleep(self.interval)


def _run_sender(qin, qout, cls=MailSender):
    sender = cls()
    sender.run(qin, qout)

def _receive(qin, qout, max_retry=3):
    Empty = get_empty(qout)
    while True:
        try:
            req, error = qout.get(timeout=1)
            if not req:
                logger.error("未知邮件的错误:\n%s", error)
                continue
            if error:
                if req.retry < max_retry:
                    req.retry += 1
                    logger.error("%s号邮件第%s次发送失败,下次继续重试,错误堆栈为:\n%s", req.id, req.retry, error)
                    qin.put(req)
                else:
                    logger.error("%s号邮件第%s次发送失败,不再继续重试,错误堆栈为:\n%s", req.id, req.retry, error)
            else:
                logger.info("%s号邮件发送成功", req.id)
        except Empty:
            pass
        except:
            msg = traceback.format_exc()
            logger.error("邮件回报出错:\n%s", msg)

class EmailHelper(LoggerMixin, metaclass=Singleton):
    Thread = threading.Thread
    Queue = queue.Queue

    def __init__(self, nparallel=1):
        LoggerMixin.__init__(self)
        self._count = 0
        self._timestamp = int(time.time())
        self._q = None
        self._qin = None
        self._qout = None
        self._workers = None
        self._receiver = None
        self._thread = None
        self._nparallel = nparallel
        self._start()

    def _start(self):
        self._qin = self.Queue()
        self._qout = self.Queue()
        self._receiver = threading.Thread(target=_receive, args=(self._qin, self._qout))
        self._receiver.daemon = True
        self._receiver.start()
        self._workers = [self.Thread(target=_run_sender, args=(self._qin, self._qout)) for i in range(self._nparallel)]
        for w in self._workers:
            w.daemon = True
            w.start()

    def send(self, content, strategy):
        self._count += 1
        req_id = str(self._timestamp) + "-" + str(self._count)
        self.info("开始发送由策略%s发出的邮件,内容长度为%s,发送编号为%s", strategy.name, len(content), req_id)
        try:
            strategy = StrategyInfo.from_strategy(strategy)
            req = MailRequest()
            req.id = req_id
            req.content = content
            req.strategy = strategy
            self._qin.put(req)
        except:
            error = traceback.format_exc()
            self.error("%s号邮件发送失败,错误堆栈为:\n%s", req_id, error)
    

class MultiprocessEmailHelper(EmailHelper):
    Thread = multiprocessing.Process
    Queue = multiprocessing.Queue


def mail(content, strategy):
    helper = MultiprocessEmailHelper()
    if not content:
        helper.warn("Email content from strategy [%s] is empty, skip", strategy.name)
        return
    helper.send(content, strategy)
