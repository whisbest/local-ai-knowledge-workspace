# 邮箱注册与发信设置

## 现在如何本地测试

打开 `http://127.0.0.1:18770`，登录页选择“用邮箱注册”，填写邮箱并发送验证码。当前配置是本地测试模式，**不会向真实邮箱发送邮件**。

另开一个无痕窗口或不同浏览器，用管理员账号登录（同一个浏览器普通窗口会共享登录状态），进入“平台管理 → 本地验证邮件”，查看对应邮箱的验证码；回到注册页面填写验证码和两次密码。注册成功后使用邮箱和密码登录，新账号是普通用户，知识空间为空，资料与其他账号隔离。

验证码10分钟有效，60秒内不能重发，最多5次校验尝试，每邮箱每小时最多6次发送。重发使旧码失效，成功注册后验证码不能再次使用。邮箱按小写归一化，同一邮箱不能注册多个账号；密码至少12字符。原用户名账号、管理员权限与现有数据保留，密码忘记仍由管理员重置。

私有配置 `.runtime/multiuser.env` 的本地设置为：

```dotenv
MULTIUSER_REGISTRATION_ENABLED=true
MULTIUSER_MAIL_BACKEND=local
```

本地预览仅管理员可访问，限localhost开发环境；预览文件位于私有数据卷的 `registration-mail`，只保留10分钟内有效邮件，不进入个人导出。普通用户不能读取预览。公开部署不能使用本地邮件预览。

## 部署到服务器时配置SMTP

SMTP就是系统通过发件邮箱发送邮件的方式。注册者只需输入收到的验证码，**不需要向系统提供自己的邮箱密码**。系统维护者准备一个发件邮箱，例如QQ邮箱或163邮箱。

QQ邮箱通常可在网页版设置的“第三方客户端”或“POP3/IMAP/SMTP”服务中开启SMTP，按页面验证身份后生成授权码。授权码用于程序发信，与QQ登录密码不同。

在服务器私有 `.runtime/multiuser.env` 中保留原有数据库密码和主密钥，增加或修改：

```dotenv
MULTIUSER_REGISTRATION_ENABLED=true
MULTIUSER_MAIL_BACKEND=smtp
MULTIUSER_SMTP_HOST=smtp.qq.com
MULTIUSER_SMTP_PORT=465
MULTIUSER_SMTP_SECURITY=ssl
MULTIUSER_SMTP_USERNAME=你的QQ邮箱地址
MULTIUSER_SMTP_PASSWORD=邮箱生成的授权码
MULTIUSER_MAIL_FROM=你的QQ邮箱地址
```

服务器按部署说明重启：`bash stop-multiuser.sh --production`，然后 `bash start-multiuser.sh --production`。Windows本地测试真实发信则执行 `stop-multiuser.ps1` / `start-multiuser.ps1`。不要只用 `docker restart`，因为新的环境变量需重新创建容器才能生效。

其他发信邮箱按服务商提供的服务器地址、端口和TLS方式填写；支持SSL和STARTTLS，证书验证开启。授权码只放本机/服务器私有配置，不提交Git，不发到聊天，也不放前端代码里。SMTP配置缺失时注册会明确提示，发信失败不创建账号、不回显SMTP原始错误。

SMTP模式关闭管理员的本地验证码预览。上线时需真实测试验证码送达、垃圾邮件箱、有效期、重发、注册、邮箱登录及账号隔离；服务器出站端口可能需服务商放行。本次已验证本地流程和SMTP协议/TLS配置测试，**尚未用真实SMTP凭据发送邮件**。

## 升级与备份

新增数据库迁移 `mu0003`：现有用户增加可空且唯一的email字段，新建验证码表。既有用户名账号不强行绑定邮箱。全站新备份使用mu0003，恢复工具仍接受校验正确的mu0002备份，补齐空邮箱和验证码表，保留原账号及资料。升级前已在私有卷保存全站备份，主密钥独立保护。
