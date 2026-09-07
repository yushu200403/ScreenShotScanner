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

客户端填写服务器地址和客户端凭证后即可连接。公网服务器地址使用 HTTPS。

## 打包

~~~powershell
.\build.ps1
~~~

输出文件为 dist\ScreenShotScannerClient.exe，依赖已打包，目标电脑不需要安装 Python。最小化按钮会将客户端收起到系统托盘；关闭按钮会断开连接并退出。

已有项目虚拟环境时，可指定解释器并跳过重复安装依赖：

~~~powershell
.\build.ps1 -Python ..\.venv\Scripts\python.exe -SkipInstall
~~~

添加 -Clean 可清理 PyInstaller 构建缓存后重建。脚本会检查安装和打包退出码，失败时直接报错。

服务器地址只填写协议、主机和可选端口，不附带路径、查询参数或凭证。公网使用 HTTPS，本机开发可使用 http://127.0.0.1:8000。

连接信息以 Windows DPAPI 加密保存。重置连接码的待确认状态会一并保存，连接中断后可以继续确认。凭证保存失败时会在状态栏显示原因；每次网页授权仍需用户在客户端确认。
