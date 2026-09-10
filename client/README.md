# Windows 客户端

## 开发运行

在 Windows PowerShell 中执行：

~~~powershell
cd client
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
~~~

客户端填写服务器地址、账号和密码后登录连接，点击“打开截图问答”。电脑名称、预设选择、共享申请均在网页设置；客户端显示连接状态并负责截图。公网服务器地址使用 HTTPS。

## 打包

~~~powershell
.\build.ps1
~~~

输出文件为 ScreenShotScannerClient.exe，依赖已打包，目标电脑不需要安装 Python。打包临时目录会在结束后自动清理。最小化按钮会将客户端收起到系统托盘；关闭按钮会断开连接并退出。

项目已配置 `.venv-build` 专用构建环境；该环境存在时，脚本会自动使用它，以保证 Tcl/Tk 图形组件可用。

已有项目虚拟环境时，可指定解释器并跳过重复安装依赖：

~~~powershell
.\build.ps1 -Python ..\.venv\Scripts\python.exe -SkipInstall
~~~

旧客户端正在运行而无法覆盖时，可指定 `-OutputName ScreenShotScannerClient-2.1.1.exe` 输出到其他文件；默认文件名不变。

添加 -Clean 可清理 PyInstaller 构建缓存后重建。脚本会检查安装和打包退出码，失败时直接报错。

服务器地址只填写协议、主机和可选端口，不附带路径、查询参数或登录信息。公网使用 HTTPS，本机开发可使用 http://127.0.0.1:8000。

登录状态由 Windows 加密保存，不保存密码。下次启动优先复用有效的登录状态，过期后要求重新输入密码。同账号的电脑会自动出现在网页中，不需要输入邀请码；邀请码仅用于其他账号申请使用这台电脑。

电脑初始名称使用系统主机名，可在网页操作台右上角的三道杠菜单中进入“电脑设置”修改，并同步到客户端。电脑名称只用于显示，服务器始终通过独立 ID 识别电脑。同账号默认最多保存 5 台电脑，离线电脑也计入数量。达到上限后，在网页删除旧电脑再登录；修改 `.env` 中的 `MAX_DEVICES_PER_USER` 可以调整上限。

版本由 `../server/app/release.json` 统一定义，打包脚本会将它一并打入程序。客户端与服务器版本不一致时会拒绝登录、连接和截图上传，必须同时更新。当前版本为 2.1.1，保留 2.0.0 数据；从 1.x 升级时服务端会清空重建旧版数据表。
