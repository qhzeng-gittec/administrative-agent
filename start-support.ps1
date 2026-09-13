param([int]$BackendPort = 8010, [int]$WebPort = 3100)
$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$backendDir = Join-Path $projectDir 'backend'
$webDir = Join-Path $projectDir 'web'
$logDir = Join-Path $backendDir 'data/support/launcher'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$pythonExecutable = (Get-Command python -ErrorAction Stop).Source
$nodeExecutable = (Get-Command node -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath (Join-Path $webDir 'node_modules/next/dist/bin/next'))) {
    throw '前端依赖未安装，请先在 web 目录执行 npm ci。'
}
foreach ($port in @($BackendPort, $WebPort)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "端口 $port 已被占用。现有程序不会被终止；请使用其他端口或访问已经启动的服务。"
    }
}
$env:STORAGE_MODE = 'memory'
$env:EMBEDDING_PROVIDER = 'hash'
$env:EMBEDDING_DIM = '128'
$env:RERANKER_ENABLED = 'false'
$env:PI_AGENT_ENABLED = 'false'
$env:BACKEND_URL = "http://127.0.0.1:$BackendPort"
$env:NEXT_TELEMETRY_DISABLED = '1'
$backendProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $BackendPort) -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'backend.out.log') -RedirectStandardError (Join-Path $logDir 'backend.err.log')
$webProcess = Start-Process -FilePath $nodeExecutable -ArgumentList @('node_modules/next/dist/bin/next', 'dev', '--hostname', '127.0.0.1', '--port', $WebPort) -WorkingDirectory $webDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'web.out.log') -RedirectStandardError (Join-Path $logDir 'web.err.log')
@{ backend_pid = $backendProcess.Id; web_pid = $webProcess.Id; backend_port = $BackendPort; web_port = $WebPort } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDir 'processes.json') -Encoding utf8
Write-Output "企业 Agent 工作台：http://127.0.0.1:$WebPort"
Write-Output "日志目录：$logDir"
Write-Output '企业任务、消息、后台作业、策略与评测记录持久保存在 backend/data/support；旧研发实验队列在本地模式下使用内存。'
