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
$vsDevCmdPath = Join-Path $installation 'Common7/Tools/VsDevCmd.bat'
if (-not (Test-Path -LiteralPath $vsDevCmdPath)) { throw 'Actual VS environment script is missing' }
$environmentRows = & cmd.exe /d /s /c "`"`"$vsDevCmdPath`" -arch=x64 -host_arch=x64 >nul && set`""
if ($LASTEXITCODE -ne 0) { throw 'Actual VS environment initialization failed' }
foreach ($line in $environmentRows) {
    $splitAt = $line.IndexOf('=')
    if ($splitAt -gt 0) { [Environment]::SetEnvironmentVariable($line.Substring(0,$splitAt),$line.Substring($splitAt+1),'Process') }
}
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
        & cl.exe /nologo /TC /std:c11 /O2 /fp:precise /GL- /MD $archOption $sourcePath "/Fo:$objectPath" "/Fe:$exePath" /link /INCREMENTAL:NO
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $exePath
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Output "$isa Main$version independent controls raw0"
    }
    $librarySource = Join-Path $nativeRoot 'p10-g0-main07/main_grid_step.c'
    $libraryPath = Join-Path $isaOutput 'g0_step.dll'
    $libraryObject = Join-Path $isaOutput 'g0_step.obj'
    & cl.exe /nologo /TC /std:c11 /O2 /fp:precise /GL- /MD /LD $archOption $librarySource "/Fo:$libraryObject" "/Fe:$libraryPath" /link /INCREMENTAL:NO
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & python (Join-Path $nativeRoot 'verify_g0_native.py') --dll $libraryPath --types (Join-Path $nativeRoot 'p10-g0-main07/grid_step_types.py') --out (Join-Path $isaOutput 'abi.json')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$sourceRows = foreach ($relative in @('p10-g0-main06/main_grid.h','p10-g0-main06/main_grid.c','p10-g0-main06/controls.c','p10-g0-main07/main_grid_step.h','p10-g0-main07/main_grid_step.c','p10-g0-main07/controls.c','p10-g0-main07/grid_step_types.py','verify_g0_native.py')) {
    $sourcePath = Join-Path $nativeRoot $relative
    [pscustomobject]@{path="tests/native/$relative";sha256=(Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash}
}
$binaryRows = Get-ChildItem -LiteralPath $outputRoot -Recurse -File | Where-Object Extension -In @('.exe','.dll') | ForEach-Object {
    [pscustomobject]@{name=$_.FullName.Substring($outputRoot.Length+1);sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
}
$guardRows = foreach ($name in @('run_validation_guarded.py','_validation_windows.py','validation_guard_delegation.py','validation_guard_handles.py')) {
    [pscustomobject]@{path="tests/native/r215_guard/$name";sha256=(Get-FileHash -LiteralPath (Join-Path $nativeRoot "r215_guard/$name") -Algorithm SHA256).Hash}
}
[pscustomobject]@{status='G0_NATIVE_CONTROLS_ONLY';head=$env:SOURCE_HEAD_SHA;checkout=$env:GITHUB_SHA;source=$sourceRows;guard=$guardRows;binaries=$binaryRows} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outputRoot 'sources.json') -Encoding utf8
exit 0
