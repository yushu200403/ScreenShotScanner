# ScreenShotScanner 屏幕问答器

ScreenShotScanner 让 Windows 电脑负责截图，在网页上提问和查看回答。客户端只需要填写服务器地址、使用账号密码登录；连接后在网页设置电脑名称并选择预设。同账号登录的电脑会自动显示在网页中。

## 2.1.2 使用方式

- 客户端与网页使用相同的账号密码，注册后由管理员审批。
- 客户端保存登录状态，下次启动自动恢复；不保存密码，也不需要复制任何登录信息。
- 在网页“截图问答”选择电脑，直接在操作栏选择预设（自动保存）。手机点击电脑名称，通过弹窗切换电脑。右侧三道杠可填写补充问题，或进入“电脑设置”修改名称。客户端同步显示名称。
- 名称可以相同，所有操作始终按独立设备 ID 区分；网页会为同名电脑显示编号。
- 管理员提供公共预设，用户也可以创建自己的预设。每个预设完整保存提示词、请求格式、模型名称、模型端点、API 密钥和高级参数。
- 点击“截图并提问”开始请求，同一个按钮随即切换为“停止回答”；完成或停止后恢复。补充问题在三道杠菜单中填写，草稿按电脑分别保留，成功提问后清空。
- 每个账号默认最多保存 5 台电脑；达到上限后，需要在网页删除不再使用的电脑才能新增。
- 删除电脑会立即撤销登录状态、取消任务和共享访问，并释放名额；历史回答仍保留。暂时断开或退出客户端不释放名额。
- 如需其他账号操作，在“问答设置”的“共享管理”中生成邀请码；对方申请后，由电脑主人在网页允许或拒绝。生成新邀请码会撤销旧共享权限。
- 保留当前鼠标所在显示器截图、流式回答、停止回答、托盘运行和自动重连。

## 版本与数据库

版本统一维护在 `server/app/release.json`，当前为 **2.1.2**。服务端和客户端均从这份文件读取版本，构建客户端时会把同一份文件打入程序。

客户端请求、截图上传和实时连接均进行版本一致性检查。版本缺失或不一致时直接拒绝操作，并提示安装匹配版本；不要混用新旧客户端和服务端。`GET /api/version` 和 `/healthz` 可查询服务端版本。

**从 1.x 升级属于破坏性更新，不迁移旧数据。** 2.1.2 继续使用数据库版本 2，保留 2.0.0 数据。 服务端启动时检测数据库版本；旧版或未标记版本的屏幕问答器数据表会被清空重建，包括账号、设备、设置和问答历史。管理员按环境配置重新创建。当前版本的数据会保留；检测到更新版本的数据库则拒绝启动，避免降级破坏数据。不会删除同库中不属于本项目的数据表。

旧的凭证、单独提示词和单独模型管理接口返回升级提示，请改用预设页面。

## 模型协议

只解析以下标准协议，不提供任意响应模板或自定义响应解析：

1. DeepSeek Chat Completions
2. OpenAI Chat Completions
3. OpenAI Responses
4. Gemini generateContent / streamGenerateContent

DeepSeek Chat Completions 的 reasoning_content、OpenAI Responses 的 reasoning summary 事件和 Gemini 标记为 thought 的 part 会作为“推理过程”单独保存和显示。

推理强度支持 none、low、medium、high 和 xhigh。系统将其映射为 Chat Completions 的 reasoning_effort、OpenAI Responses 的 reasoning.effort，以及 Gemini 的 thinkingBudget。none 在 Chat Completions / Responses 中表示不发送推理强度参数、使用模型默认行为，在 Gemini 中表示 thinkingBudget=0；它不能保证所有模型都关闭推理。供应商或模型不支持某个强度时，接口可能拒绝该参数，应在预设的模型配置中选择兼容值。

预设中的“请求格式”决定协议适配器；Chat Completions / Responses 的“模型端点”填写截至 `/v1` 的基础 URL，服务端追加请求路径；Gemini 仍使用完整端点；“模型名称”填写供应商提供的模型 ID。高级参数保留标准名称：采样温度 `temperature`、请求超时（秒）、推理强度和输出 Token 上限。输出上限在 Chat Completions / DeepSeek 中对应 `max_tokens`，在 Responses / Gemini 预设中对应 `max_output_tokens`，Gemini 请求由适配器转换为 `maxOutputTokens`。

各协议只发送对应的高级参数。模型返回错误、输出达到上限或流意外中断时，请求会标记为失败，保留已经收到的内容供排查。只有推理内容而没有最终回答也会明确报错。协议适配不代表任意模型支持图片输入，必须选择供应商实际支持视觉输入的模型。

Gemini 端点可以使用模型占位符，例如：

~~~text
https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse
~~~

OpenAI 和 DeepSeek 配置填写基础端点，例如：

~~~text
https://api.openai.com/v1
https://api.deepseek.com/v1
~~~

Chat Completions / DeepSeek 自动追加 `/chat/completions`，Responses 自动追加 `/responses`。尾斜杠会移除；仅填写域名时自动补 `/v1`；粘贴旧完整端点时会归一化为基础地址，避免重复拼接。兼容服务的路径前缀会保留，例如 `https://example.com/proxy/v1`。

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

1. 管理员登录网页，进入“预设”，创建完整预设，并勾选“公共预设”。
2. 用户注册账号，等待管理员批准。
3. 打开 Windows 客户端，填写服务器地址、账号和密码，点击“登录并连接”。
4. 点击“打开截图问答”，在网页登录同一个账号。
5. 在“截图问答”选择电脑，通过下拉框选择预设；需要时进入“预设”创建自己的预设。
6. 填写可选的补充问题，点击“截图并提问”，等待回答。

预设保存后可编辑或停用。停用不会中断已经开始的回答，但会阻止后续提问，直到在网页选择其他可用预设。编辑预设不会改变正在执行或已保存问答所使用的配置。

截图和上传默认最多等待 90 秒，可用 `CAPTURE_TIMEOUT_SECONDS` 调整。服务重启后，未完成请求会标为失败，可重新提问。客户端掉线会终止尚未上传的截图请求，已上传并进入回答阶段的请求可以继续完成。

## 电脑数量与登录状态

在 `.env` 中设置，修改后重启服务端：

```dotenv
MAX_DEVICES_PER_USER=5
CLIENT_SESSION_DAYS=30
```

- 设备数量按账号分别计算，包含离线电脑；同一设备重复连接不增加数量。
- 账号达到上限时，新设备无法登录，并提示到网页删除旧电脑。
- 登录状态默认有效 30 天，在 Windows 中加密保存。有效期内优先复用，不重复创建登录记录或设备。
- 修改或重置密码、停用账号、删除电脑都会使相应登录状态失效。客户端退出登录会清除本机登录状态，联网时同时撤销服务器上的登录；离线退出会明确提示服务器尚未确认。
- 更换服务器或账号时使用独立设备标识，不按电脑名称匹配。
- 邀请码仅用于跨账号共享；取消邀请不影响电脑主人操作。

## Windows 客户端

开发和打包说明见 [client/README.md](client/README.md)。运行 `client/build.ps1` 后生成 `client/ScreenShotScannerClient.exe`，目标电脑无需安装 Python。

最小化后收起到系统托盘，关闭窗口会断开连接并退出。截图与共享申请不弹出客户端窗口、不切换其他窗口；共享申请在网页处理。截图包含当前显示器上的客户端窗口。

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

自动化测试覆盖账号审批、账号登录复用、版本拒绝、设备限额与删除、同名设备隔离、公共与个人预设权限、旧库重建、截图上传、四种协议解析、流式回答、取消和重连。网络测试使用本地模型端点和生成的测试图片。

实际部署仍需在目标服务器验证 PostgreSQL、Redis、Docker Compose、HTTPS/WSS、真实模型及实际显示器截图。本地模拟服务只用于测试；产品缺少预设或外部服务不可用时会明确报错。

## 安全注意事项

- 生产环境必须替换 .env 中全部示例密钥，并妥善备份 APP_ENCRYPTION_KEY；丢失后已保存的模型 API Key 无法解密。
- OpenResty 必须终止 TLS，并正确转发 Upgrade、Connection 和 X-Forwarded-Proto。
- 登录连续失败默认达到 5 次后锁定 15 分钟；Redis 同时限制登录和邀请码尝试频率。Redis 不可用时相关操作返回 503，不会绕过限流。
- 修改或重置密码会使旧会话失效；本人修改密码后当前网页登录保持有效。浏览器实时连接校验来源，并在账号状态或权限变化时断开。
- 客户端登录凭证和模型 API 密钥不应写入日志、工单或聊天记录。
- 当前版本允许配置任意 HTTP/HTTPS 模型端点。向不受信任用户开放自建模型配置前，应增加 SSRF 私网地址阻断策略。
- 当前 WebSocket 连接注册表位于单个 Web 进程内，因此 Gunicorn 固定为一个进程和多线程。横向扩容前需要把连接路由迁移到 Redis Pub/Sub 或专用 WebSocket 网关。

## 网页资源

网页使用顶部导航与自定义 SVG 标识。导航和操作图标使用 [Remix Icon](https://github.com/Remix-Design/RemixIcon) 4.6.0 字体，通过 [BootCDN](https://www.bootcdn.cn/remixicon/) 加载；项目保留本地 WOFF2 副本作为备用。字体许可见 `server/web/fonts/LICENSE-RemixIcon`。

回答和推理内容支持 Markdown（标题、列表、表格、引用、代码块等），推理内容默认折叠。渲染使用本地分发的 [Marked](https://marked.js.org/) 18.0.12 与 [DOMPurify](https://github.com/cure53/DOMPurify) 3.4.15，许可保存在 `server/web/vendor/`。
