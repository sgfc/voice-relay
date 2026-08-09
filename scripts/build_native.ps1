# native/ 配下のヘルパーを一括ビルドする。
# 使い方:
#   .\scripts\build_native.ps1 -Vst3SdkRoot C:\src\vst3sdk
# VST3 SDK の取得 (初回のみ):
#   git clone --recursive https://github.com/steinbergmedia/vst3sdk C:\src\vst3sdk
param(
    [Parameter(Mandatory = $true)][string]$Vst3SdkRoot,
    [string]$Config = "Release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# ヘルパーを追加したらこの配列に足す。
# 例: wasapi-loopback (プロセス単位ループバックキャプチャ) を将来ここに追加。
$helpers = @(
    @{ Name = "beatrice-host"; Source = "native/beatrice-host"; CMakeArgs = @("-DVST3_SDK_ROOT=$Vst3SdkRoot", "-DSMTG_USE_STATIC_CRT=ON") }
)

foreach ($h in $helpers) {
    $src = Join-Path $root $h.Source
    $build = Join-Path $root ("build/" + $h.Name)
    Write-Host "=== configure: $($h.Name) ===" -ForegroundColor Cyan
    cmake -S $src -B $build @($h.CMakeArgs)
    if ($LASTEXITCODE -ne 0) { throw "configure failed: $($h.Name)" }
    Write-Host "=== build: $($h.Name) ($Config) ===" -ForegroundColor Cyan
    cmake --build $build --config $Config
    if ($LASTEXITCODE -ne 0) { throw "build failed: $($h.Name)" }
}

Write-Host "done. binaries under build/<helper>/$Config/" -ForegroundColor Green
