param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"

function Invoke-CheckedCommand {
    param([string]$Command, [string[]]$Arguments)

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
    }
}

foreach ($command in "node", "pnpm", "docker") {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "需要 $command"
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$staticDirectory = Join-Path $projectRoot "static"
$imageName = "sunxiao0721/panwatch"
$fullImage = "${imageName}:$Version"

Push-Location $projectRoot
try {
    Write-Host "🚀 PanWatch 构建脚本"
    Write-Host "版本: $Version"

    Write-Host "📦 构建前端..."
    Push-Location "frontend"
    try {
        Invoke-CheckedCommand "pnpm" @("install", "--frozen-lockfile")
        Invoke-CheckedCommand "pnpm" @("build")
    }
    finally {
        Pop-Location
    }

    Write-Host "📁 复制静态文件..."
    Remove-Item -LiteralPath $staticDirectory -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $staticDirectory | Out-Null
    Copy-Item -Path (Join-Path $projectRoot "frontend\dist\*") -Destination $staticDirectory -Recurse -Force

    Write-Host "🐳 构建 Docker 镜像 (linux/amd64)..."
    Invoke-CheckedCommand "docker" @("build", "--platform", "linux/amd64", "--build-arg", "VERSION=$Version", "-t", $fullImage, ".")

    if ($Version -ne "latest") {
        Invoke-CheckedCommand "docker" @("tag", $fullImage, "${imageName}:latest")
        Write-Host "✅ 镜像已构建: $fullImage 和 ${imageName}:latest"
    }
    else {
        Write-Host "✅ 镜像已构建: $fullImage"
    }
}
finally {
    Remove-Item -LiteralPath $staticDirectory -Recurse -Force -ErrorAction SilentlyContinue
    Pop-Location
}
