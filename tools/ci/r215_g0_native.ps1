param([Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$outputRoot = [IO.Path]::GetFullPath($OutputPath)
if (-not $outputRoot.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Native control output must remain inside this actual checkout'
}
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$vswherePath = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$installation = & $vswherePath -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or -not $installation) { throw 'Actual installed MSVC was not located' }
# Reuse the product CE builder's _find_msvc/_find_sdk/_build_env protocol.
# Resolve actual compiler/header/library paths; no nested shell quoting.
$msvcRoot = Join-Path $installation 'VC/Tools/MSVC'
$msvc = Get-ChildItem -LiteralPath $msvcRoot -Directory | Where-Object Name -Match '^\d+(\.\d+){1,3}$' | Sort-Object { [version]$_.Name } | Select-Object -Last 1
$kitsRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10'
$sdk = Get-ChildItem -LiteralPath (Join-Path $kitsRoot 'Include') -Directory | Where-Object Name -Match '^\d+(\.\d+){1,3}$' | Sort-Object { [version]$_.Name } | Select-Object -Last 1
if ($null -eq $msvc -or $null -eq $sdk) { throw 'Actual MSVC/Windows SDK directories are unavailable' }
$compilerPath = Join-Path $msvc.FullName 'bin/Hostx64/x64/cl.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) { throw 'Actual x64 compiler is missing' }
$includePaths = @((Join-Path $msvc.FullName 'include'),(Join-Path $sdk.FullName 'ucrt'),(Join-Path $sdk.FullName 'shared'),(Join-Path $sdk.FullName 'um'))
$libraryPaths = @((Join-Path $msvc.FullName 'lib/x64'),(Join-Path $kitsRoot "Lib/$($sdk.Name)/ucrt/x64"),(Join-Path $kitsRoot "Lib/$($sdk.Name)/um/x64"))
foreach ($requiredPath in $includePaths + $libraryPaths) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Actual compiler dependency path missing: $requiredPath" }
}
$env:INCLUDE = $includePaths -join ';'
$env:LIB = $libraryPaths -join ';'
$env:PATH = (Split-Path -Parent $compilerPath) + ';' + $env:PATH
if (-not [System.Runtime.Intrinsics.X86.Avx2]::IsSupported) { throw 'Actual hosted CPU/OS lacks AVX2; both-ISA acceptance unavailable' }
$nativeRoot = Join-Path $repoRoot 'tests/native'
foreach ($isa in @('sse2','avx2')) {
    $isaOutput = Join-Path $outputRoot $isa
    New-Item -ItemType Directory -Path $isaOutput -Force | Out-Null
    $archOption = if ($isa -eq 'avx2') { '/arch:AVX2' } else { '/arch:SSE2' }
    foreach ($version in @('06','07')) {
        $sourcePath = Join-Path $nativeRoot "p10-g0-main$version/controls.c"
        $exePath = Join-Path $isaOutput "controls$version.exe"
        $objectPath = Join-Path $isaOutput "controls$version.obj"
        & $compilerPath /nologo /utf-8 /TC /std:c11 /O2 /fp:precise /GL- /MD $archOption $sourcePath "/Fo:$objectPath" "/Fe:$exePath" /link /INCREMENTAL:NO
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $exePath
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Output "$isa Main$version independent controls raw0"
    }
    $librarySource = Join-Path $nativeRoot 'p10-g0-main07/main_grid_step.c'
    $libraryPath = Join-Path $isaOutput 'g0_step.dll'
    $libraryObject = Join-Path $isaOutput 'g0_step.obj'
    & $compilerPath /nologo /utf-8 /TC /std:c11 /O2 /fp:precise /GL- /MD /LD $archOption $librarySource "/Fo:$libraryObject" "/Fe:$libraryPath" /link /INCREMENTAL:NO
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & python (Join-Path $nativeRoot 'verify_g0_native.py') --dll $libraryPath --types (Join-Path $nativeRoot 'p10-g0-main07/grid_step_types.py') --out (Join-Path $isaOutput 'abi.json')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $clockRoot = Join-Path $nativeRoot 'p10-tc1'
    $clockLibrary = Join-Path $isaOutput 'sntc_test.dll'
    & $compilerPath /nologo /utf-8 /TC /std:c11 /O2 /fp:precise /GL- /MD /LD $archOption (Join-Path $clockRoot 'clock_test.c') "/Fo:$(Join-Path $isaOutput 'sntc_test.obj')" "/Fe:$clockLibrary" /link /INCREMENTAL:NO
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $env:QASCADE_SNTC_RESEARCH_DLL = $clockLibrary
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
    & python -m pytest --noconftest -q -o 'addopts=' (Join-Path $clockRoot 'test_sntc_v1.py') (Join-Path $clockRoot 'test_sntc_transition02.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $referenceExe = Join-Path $isaOutput 'reference_pending.exe'
    & $compilerPath /nologo /utf-8 /TC /std:c11 /O2 /fp:precise /GL- /MD $archOption (Join-Path $clockRoot 'reference_pending_controls.c') "/Fo:$(Join-Path $isaOutput 'reference_pending.obj')" "/Fe:$referenceExe" /link /INCREMENTAL:NO
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $referenceExe
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output "$isa SNTC independent protocol/authority and reference pending controls raw0"
}
$sourceRows = foreach ($relative in @('p10-g0-main06/main_grid.h','p10-g0-main06/main_grid.c','p10-g0-main06/controls.c','p10-g0-main07/main_grid_step.h','p10-g0-main07/main_grid_step.c','p10-g0-main07/controls.c','p10-g0-main07/grid_step_types.py','verify_g0_native.py')) {
    $sourcePath = Join-Path $nativeRoot $relative
    [pscustomobject]@{path="tests/native/$relative";sha256=(Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash}
}
$sourceRows += foreach ($relative in @('p10-tc1/clock_test.c','p10-tc1/sntc_abi.py','p10-tc1/test_sntc_v1.py','p10-tc1/test_sntc_transition02.py','p10-tc1/reference_pending_controls.c','p10-tc1/csrc/ce_sntc_v1_types.inc','p10-tc1/csrc/ce_sntc_v1_api.inc','p10-tc1/csrc/ce_sntc_consumer_v1.inc','p10-tc1/csrc/ce_sntc_media_v1_types.inc','p10-tc1/csrc/ce_sntc_media_v1_api.inc','p10-tc1/csrc/ce_sntc_reference_policy_v1.inc')) {
    [pscustomobject]@{path="tests/native/$relative";sha256=(Get-FileHash -LiteralPath (Join-Path $nativeRoot $relative) -Algorithm SHA256).Hash}
}
$binaryRows = Get-ChildItem -LiteralPath $outputRoot -Recurse -File | Where-Object Extension -In @('.exe','.dll') | ForEach-Object {
    [pscustomobject]@{name=$_.FullName.Substring($outputRoot.Length+1);sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
}
$guardRows = foreach ($name in @('run_validation_guarded.py','_validation_windows.py','validation_guard_delegation.py','validation_guard_handles.py')) {
    [pscustomobject]@{path="tests/native/r215_guard/$name";sha256=(Get-FileHash -LiteralPath (Join-Path $nativeRoot "r215_guard/$name") -Algorithm SHA256).Hash}
}
[pscustomobject]@{status='G0_AND_SNTC_NATIVE_CONTROLS_ONLY';head=$env:SOURCE_HEAD_SHA;checkout=(& git rev-parse HEAD);event_sha=$env:GITHUB_SHA;source=$sourceRows;guard=$guardRows;binaries=$binaryRows} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outputRoot 'sources.json') -Encoding utf8
exit 0
