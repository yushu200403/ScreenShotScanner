param([string]$Python = "python", [switch]$SkipInstall, [switch]$Clean)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not $SkipInstall) {
    & $Python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "客户端依赖安装失败" }
}
$buildArguments = @("-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", "ScreenShotScannerClient")
if ($Clean) { $buildArguments += "--clean" }
$buildArguments += "app.py"
& $Python @buildArguments
if ($LASTEXITCODE -ne 0) { throw "客户端打包失败" }
if (-not (Test-Path -LiteralPath "$PSScriptRoot\dist\ScreenShotScannerClient.exe")) {
    throw "打包结束但未找到客户端程序"
}
Write-Host "打包完成：$PSScriptRoot\dist\ScreenShotScannerClient.exe"
