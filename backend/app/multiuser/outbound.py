"""Validate DNS and pin the actual TCP connection while retaining HTTPS host/SNI."""
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit
import httpx
import httpcore
from httpcore._backends.sync import SyncBackend, SyncStream
from ..core import AppError


def normalize(value):
    try:
        url=urlsplit(value.strip())
        if url.scheme not in ('https','http') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError()
        if url.port is not None and not 1<=url.port<=65535:raise ValueError()
        if '\\' in value or any(ord(c)<32 for c in value):raise ValueError()
        return urlunsplit((url.scheme,url.netloc.lower(),url.path.rstrip('/'),'',''))
    except (ValueError,AttributeError):raise AppError('ENDPOINT','请输入不含凭据或查询参数的 HTTP/HTTPS 服务地址。',422)


def public_address(address):
    try:
        ip=ipaddress.ip_address(address.split('%')[0])
        if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped:ip=ip.ipv4_mapped
        return ip.is_global and not ip.is_multicast
    except ValueError:return False


class Policy:
    def __init__(self,endpoint,platform=False,allowed_hosts=(),resolver=None):
        self.endpoint=normalize(endpoint);url=urlsplit(self.endpoint)
        self.host=url.hostname;self.port=url.port or (443 if url.scheme=='https' else 80)
        self.platform=platform;self.allowed=set(allowed_hosts)
        self.resolver=resolver or socket.getaddrinfo
        if not platform and (url.scheme!='https' or self.port!=443):
            raise AppError('ENDPOINT','个人 API 必须使用 HTTPS 443 端口。',422)

    def addresses(self,host,port):
        if host!=self.host or port!=self.port:raise AppError('SSRF','连接目标与已授权服务不同。',403)
        try:rows=self.resolver(host,port,type=socket.SOCK_STREAM)
        except OSError:raise AppError('DNS','模型服务域名无法解析。',502)
        addresses=list(dict.fromkeys((row[0],row[4][0]) for row in rows))
        if not addresses:raise AppError('DNS','模型服务域名没有可连接地址。',502)
        internal=self.platform and self.host in self.allowed
        if not internal and any(not public_address(ip) for _,ip in addresses):
            raise AppError('SSRF','模型地址指向受限网络，已阻止连接。',403)
        return addresses

    def validate(self):
        self.addresses(self.host,self.port)


class PinnedBackend(SyncBackend):
    def __init__(self,policy):self.policy=policy

    def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        addresses=self.policy.addresses(host,port)
        for family,ip in addresses:
            sock=socket.socket(family,socket.SOCK_STREAM)
            try:
                sock.settimeout(timeout)
                if local_address:sock.bind((local_address,0))
                for option in socket_options or ():sock.setsockopt(*option)
                sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                sock.connect((ip,port))  # numeric validated address; no second DNS resolution
                peer=sock.getpeername()[0]
                if ipaddress.ip_address(peer)!=ipaddress.ip_address(ip):raise OSError('Peer mismatch')
                return SyncStream(sock)
            except OSError:
                sock.close()
        raise httpcore.ConnectError('Authorized model connection failed')


class SafeTransport(httpx.HTTPTransport):
    def __init__(self,policy):
        super().__init__(trust_env=False,retries=0)
        self._pool.close()
        self._pool=httpcore.ConnectionPool(ssl_context=ssl.create_default_context(),
              network_backend=PinnedBackend(policy),retries=0,max_connections=2,max_keepalive_connections=0)
