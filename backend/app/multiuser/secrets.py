import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
import secrets
from ..core import AppError


class Vault:
    def __init__(self, key):
        try:
            raw=base64.urlsafe_b64decode(key.encode())
            if len(raw)!=32:raise ValueError()
            self.aead=AESGCM(raw)
        except Exception:
            raise AppError('MASTER_KEY','主密钥缺失或格式错误，请按部署文档配置固定主密钥。',503)

    def encrypt(self, value, reference, version=1):
        nonce=secrets.token_bytes(12)
        encrypted=self.aead.encrypt(nonce,value.encode(),f'{reference}:{version}'.encode())
        return base64.urlsafe_b64encode(nonce+encrypted).decode()

    def decrypt(self, value, reference, version=1):
        try:
            raw=base64.urlsafe_b64decode(value)
            return self.aead.decrypt(raw[:12],raw[12:],f'{reference}:{version}'.encode()).decode()
        except (InvalidTag,ValueError,UnicodeDecodeError):
            raise AppError('MASTER_KEY','当前主密钥无法解密已保存配置，请恢复正确主密钥。',503)
