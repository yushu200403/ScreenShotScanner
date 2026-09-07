# ScreenShotScanner 屏幕问答器

ScreenShotScanner 是一个服务端集中控制的屏幕问答系统。Windows 客户端连接服务端并显示 9 位连接码；已登录的网页用户输入连接码后，客户端必须弹出许可确认。许可通过后，网页可以要求客户端截取当前鼠标所在显示器，并由服务端把截图和提示词提交给多模态模型。

## 初版功能

- 管理员和普通用户两种角色，公开注册后必须由管理员审批
- 注册备注、密码修改、后台重置、禁用、解锁、软删除和审计日志
- 每个账号可创建多个客户端凭证，凭证只在创建时显示一次
- Windows 客户端自动保存连接信息并在下次启动时重连
- 9 位纯数字连接码长期复用；网页解绑或客户端重置后旧码失效
- 任意已登录用户可以输入连接码，但每次首次授权必须由客户端确认
- 多个网页可查看同一设备；单设备同一时间只执行一个请求
- 网页可以强制取消现有请求并立即发起新请求
- 当前鼠标显示器高 DPI 截图，JPEG 自动压缩到 5 MB 以下
- 截图和问答历史按权限查看，默认图片保留 7 天、结果保留 90 天
- 模型配置由服务端统一管理
- WSS 实时设备状态、推理增量、回答增量和取消状态

## 模型协议

只解析以下标准协议，不提供任意响应模板或自定义响应解析：

1. DeepSeek Chat Completions
2. OpenAI Chat Completions
3. OpenAI Responses
4. Gemini generateContent / streamGenerateContent

DeepSeek Chat Completions 的 reasoning_content、OpenAI Responses 的 reasoning summary 事件和 Gemini 标记为 thought 的 part 会作为“推理过程”单独保存和显示。

推理强度支持 none、low、medium、high 和 xhigh。系统将其映射为 Chat Completions 的 reasoning_effort、OpenAI Responses 的 reasoning.effort，以及 Gemini 的 thinkingBudget。none 在 Chat Completions / Responses 中表示不发送推理强度参数、使用模型默认行为，在 Gemini 中表示 thinkingBudget=0；它不能保证所有模型都关闭推理。供应商或模型不支持某个强度时，接口可能拒绝该参数，应在对应模型配置中选择兼容值。

各协议只发送对应的高级参数。模型返回错误、输出达到上限或流意外中断时，请求会标记为失败，保留已经收到的内容供排查。只有推理内容而没有最终回答也会明确报错。协议适配不代表任意模型支持图片输入，必须选择供应商实际支持视觉输入的模型。

Gemini 端点可以使用模型占位符，例如：

~~~text
https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse
~~~

OpenAI 和 DeepSeek 配置填写完整的标准 API 端点，例如：

~~~text
https://api.openai.com/v1/responses
https://api.openai.com/v1/chat/completions
https://api.deepseek.com/chat/completions
~~~

## 项目结构

~~~text
server/                 Flask 服务端、WebSocket、模型适配器和 Web UI
server/tests/           服务端 API 与标准响应解析测试
client/                 Windows Tkinter 客户端和 PyInstaller 脚本
openresty/              HTTPS/WSS 反向代理配置示例
docker-compose.yml      PostgreSQL、Redis 和 Web 服务
.env.example            部署变量模板
~~~

## 服务端部署

1. 在 Linux 服务器安装 Docker Engine 和 Docker Compose Plugin。
2. 将 .env.example 复制为 .env。
3. 为 POSTGRES_PASSWORD、SECRET_KEY、APP_ENCRYPTION_KEY 和 INITIAL_ADMIN_PASSWORD 设置独立的高强度随机值。
4. 构建并启动服务：

~~~bash
docker compose up -d --build
~~~

5. 检查状态：

~~~bash
docker compose ps
curl http://127.0.0.1:8000/healthz
~~~

Web 服务只监听宿主机 127.0.0.1:8000，必须由 OpenResty 代理到公网。修改 openresty/screen-scanner.conf.example 中的域名和证书路径后加载配置。公网客户端地址必须使用 HTTPS，设备和浏览器实时连接使用 WSS。

首次启动会根据以下环境变量创建管理员：

~~~text
INITIAL_ADMIN_USERNAME
INITIAL_ADMIN_PASSWORD
INITIAL_ADMIN_DISPLAY_NAME
~~~

管理员已存在时，后续修改环境变量不会覆盖数据库密码。

## 使用流程

1. 管理员登录网页，创建至少一个标准模型配置和全局提示词。
2. 用户注册账号并填写备注，管理员在账号管理中批准。
3. 用户在“客户端凭证”页面创建凭证，记录只显示一次的完整值。
4. Windows 客户端填写 HTTPS 服务器地址和凭证，点击连接。
5. 客户端鉴权成功后显示 9 位连接码。
6. 网页输入连接码，客户端弹出连接许可提示。
7. 客户端同意后，网页选择提示词、模型、推理强度和补充问题。
8. 点击“截屏并提问”，网页实时显示状态、推理过程和最终回答。

每次成功发起请求后，提示词、模型名称和推理强度会保存为该设备连接码的默认设置。

解绑或重置连接码会撤销原授权、使待确认的连接许可失效，并取消设备当前请求。网页断开设备、撤销凭证或停用账号时，由服务端关闭连接。客户端掉线会终止尚未上传的截图请求，已上传并进入模型处理的请求可以继续完成。

截图和上传默认最多等待 90 秒，可用 CAPTURE_TIMEOUT_SECONDS 调整。服务重启后，未完成请求会标为失败，用户可重新发起；清理历史数据的命令不会修改在线设备状态。

## Windows 客户端

客户端开发和打包说明见 client/README.md。运行 client/build.ps1 后生成单文件 Windows EXE，目标电脑无需安装 Python。

客户端行为：

- 登录成功后保存服务器地址和凭证，下一次启动自动重连
- 除网页连接许可外，不显示通知弹窗，不切换其他窗口
- 点击最小化按钮进入系统托盘
- 点击关闭按钮断开服务器并退出
- 截图包括当前显示器上的客户端窗口
- 网页解绑后连接码显示为失效，需要手动重置

## 数据保留

管理员可通过环境变量设置保留天数。建议每天执行一次：

~~~bash
docker compose exec web flask --app wsgi cleanup-retention
~~~

可将该命令加入宿主机 cron。清理会删除过期截图，并清空超过结果保留期的提问、推理和回答正文，同时保留请求元数据和审计记录。

## 开发和测试

~~~bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
SESSION_COOKIE_SECURE=false pytest -q
~~~

Windows PowerShell 激活虚拟环境时使用 .venv\Scripts\Activate.ps1。

在项目根目录运行完整的服务端和客户端测试：

~~~powershell
.\.venv\Scripts\python.exe -m pytest server\tests client\tests -q
~~~

客户端测试需要安装 client/requirements.txt。测试用生成的图片及本地 HTTP 模型端点验证协议，不截取真实桌面，不调用付费模型。Windows 上还会验证 DPAPI 加密读写。

本地调试时，先启动 Redis，并在当前终端配置 SECRET_KEY、APP_ENCRYPTION_KEY、INITIAL_ADMIN_PASSWORD、REDIS_URL。使用独立的开发数据库和图片目录，例如：

~~~powershell
$env:DATABASE_URL = "sqlite:///screen_scanner.db"
$env:STORAGE_DIR = "$PWD\server\instance\screenshots"
$env:SESSION_COOKIE_SECURE = "false"
.\.venv\Scripts\python.exe server\run.py --port 8000
~~~

浏览器和客户端均连接 http://127.0.0.1:8000。run.py 负责启动状态恢复、超时检查，以及本地 WebSocket 关闭处理；生产环境使用 Docker 中的单进程 Gunicorn，gunicorn.conf.py 在工作进程启动时初始化运行状态。不要使用多进程或自动重载模式运行此版本。

## 本地验收范围

2026-09-07 已完成代码收尾与本地验证：账号审批、凭证管理、设备许可、截图上传、四种协议解析、流式问答、取消与重连、数据清理、桌面及手机网页操作，以及 Windows 客户端构建。具体修改记录见 CHANGELOG.md。

生产部署尚需提供真实模型配置，并在目标服务器验证 PostgreSQL、Redis、Docker Compose、HTTPS/WSS 和实际显示器截图。仓库中的模拟模型仅位于自动化测试内；运行产品时，缺少模型配置或外部服务不可用会明确报错。

## 安全注意事项

- 生产环境必须替换 .env 中全部示例密钥，并妥善备份 APP_ENCRYPTION_KEY；丢失后已保存的模型 API Key 无法解密。
- OpenResty 必须终止 TLS，并正确转发 Upgrade、Connection 和 X-Forwarded-Proto。
- 登录连续失败默认达到 5 次后锁定 15 分钟；Redis 同时限制登录和连接码尝试频率。Redis 不可用时相关操作返回 503，不会绕过限流。
- 修改或重置密码会使旧会话失效；本人修改密码后当前会话更新凭证。浏览器实时连接校验来源，并在账号状态或权限变化时断开。
- 客户端凭证和模型 API Key 不应写入日志、工单或聊天记录。
- 当前初版允许配置任意 HTTP/HTTPS 模型端点。向不受信任用户开放自建模型配置前，应增加 SSRF 私网地址阻断策略。
- 初版 WebSocket 连接注册表位于单个 Web 进程内，因此 Gunicorn 固定为一个进程和多线程。横向扩容前需要把连接路由迁移到 Redis Pub/Sub 或专用 WebSocket 网关。
