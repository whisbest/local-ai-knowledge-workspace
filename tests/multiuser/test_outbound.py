import socket
import ssl
import pytest
import httpx
from app.multiuser.outbound import Policy, PinnedBackend, SafeTransport, public_address
from app.core import AppError


def resolver(*addresses):
    return lambda host,port,**kwargs:[(socket.AF_INET6 if ':' in ip else socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)) for ip in addresses]


@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','192.168.1.2','169.254.169.254','0.0.0.0','::1','fe80::1','fc00::1','::ffff:127.0.0.1','224.0.0.1','100.64.0.1'])
def test_private_reserved_ipv4_ipv6_blocked(ip):
    assert not public_address(ip)
    with pytest.raises(AppError):Policy('https://api.example.com',resolver=resolver(ip)).validate()


def test_mixed_dns_rebinding_and_internal_platform_permission():
    with pytest.raises(AppError):Policy('https://api.example.com',resolver=resolver('8.8.8.8','127.0.0.1')).validate()
    calls=[]
    def rebinding(host,port,**kwargs):
        calls.append(host);return resolver('8.8.8.8' if len(calls)==1 else '127.0.0.1')(host,port)
    policy=Policy('https://api.example.com',resolver=rebinding);policy.validate()
    with pytest.raises(AppError):PinnedBackend(policy).connect_tcp('api.example.com',443,timeout=1)
    Policy('http://ollama:11434',platform=True,allowed_hosts=['ollama'],resolver=resolver('172.18.0.2')).validate()
    with pytest.raises(AppError):Policy('https://ollama',resolver=resolver('172.18.0.2')).validate()
    with pytest.raises(AppError):Policy('http://localhost:11434').validate()


def test_actual_connection_pins_numeric_ip_and_retains_tls_hostname(monkeypatch):
    connections=[];tls=[]
    class Socket:
        def settimeout(self,value):pass
        def setsockopt(self,*args):pass
        def connect(self,address):connections.append(address)
        def getpeername(self):return ('8.8.8.8',443)
        def close(self):pass
    sock=Socket();monkeypatch.setattr(socket,'socket',lambda *args:sock)
    stream=PinnedBackend(Policy('https://api.example.com',resolver=resolver('8.8.8.8'))).connect_tcp('api.example.com',443,timeout=1)
    class TLSContext:
        def wrap_socket(self,value,server_hostname=None):tls.append(server_hostname);return value
    stream.start_tls(TLSContext(),server_hostname='api.example.com',timeout=1)
    assert connections==[('8.8.8.8',443)] and tls==['api.example.com']
    transport=SafeTransport(Policy('https://api.example.com',resolver=resolver('8.8.8.8')))
    assert transport._pool._ssl_context.verify_mode==ssl.CERT_REQUIRED
    assert transport._pool._ssl_context.check_hostname
    transport.close()


def test_redirects_and_environment_proxy_never_followed(monkeypatch):
    from app.providers import Provider
    calls=[]
    def redirect(request):
        calls.append(str(request.url));return httpx.Response(302,headers={'location':'http://169.254.169.254/latest'},json={})
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    p=Provider({'mode':'api','purpose':'embedding','endpoint':'https://public.example.com/v1','model':'x'},'secret',httpx.MockTransport(redirect))
    with pytest.raises(AppError):p.embed(['probe'])
    assert calls==['https://public.example.com/v1/embeddings'];p.close()
