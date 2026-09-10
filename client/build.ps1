param([string]$Python, [switch]$SkipInstall, [switch]$Clean, [string]$OutputName = "ScreenShotScannerClient.exe")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot ".venv-build\Scripts\python.exe"
if (-not $Python) {
    $Python = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { "python" }
}
if ([IO.Path]::GetFileName($OutputName) -ne $OutputName -or -not $OutputName.EndsWith(".exe")) {
    throw "输出名称必须是不含目录的 EXE 文件名"
}
$outputFile = Join-Path $PSScriptRoot $OutputName
$temporaryRoot = Join-Path $PSScriptRoot ".pyinstaller-tmp"
$temporaryBuild = Join-Path $temporaryRoot "build"
$temporaryDist = Join-Path $temporaryRoot "dist"
$temporarySpec = Join-Path $temporaryRoot "spec"
if (-not $SkipInstall) {
    & $Python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "客户端依赖安装失败" }
}
$buildArguments = @("-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", "ScreenShotScannerClient", "--workpath", $temporaryBuild, "--distpath", $temporaryDist, "--specpath", $temporarySpec)
$buildArguments += @("--add-data", ((Join-Path $PSScriptRoot "..\server\app\release.json") + ";."))
if ($Clean) { $buildArguments += "--clean" }
try {
    & $Python @buildArguments "app.py"
    if ($LASTEXITCODE -ne 0) { throw "客户端打包失败" }
    $builtFile = Join-Path $temporaryDist "ScreenShotScannerClient.exe"
    if (-not (Test-Path -LiteralPath $builtFile)) {
        throw "打包结束但未找到客户端程序"
    }
    Move-Item -LiteralPath $builtFile -Destination $outputFile -Force
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemporary = [IO.Path]::GetFullPath($temporaryRoot)
        $expectedTemporary = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".pyinstaller-tmp"))
        if ($resolvedTemporary -ne $expectedTemporary) { throw "打包临时目录不在预期位置，停止清理" }
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
Write-Host "打包完成：$outputFile"
