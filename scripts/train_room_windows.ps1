param(
    [Parameter(Mandatory = $true)][string]$Capture,
    [string]$Out,
    [ValidateRange(1000, 30000)][int]$Steps = 15000,
    [ValidateRange(256, 1920)][int]$Resolution = 1280,
    [ValidateRange(100000, 1000000)][int]$MaxSplats = 700000
)
$ErrorActionPreference = 'Stop'
$WorkspacePath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonPath = Join-Path $WorkspacePath '.venv/Scripts/python.exe'
$TrainerPath = Join-Path $WorkspacePath 'external/brush-v0.3.0/bin/brush_app.exe'
$CapturePath = (Resolve-Path -LiteralPath $Capture).Path
if (-not (Test-Path -LiteralPath $TrainerPath)) {
    throw 'Install the pinned Brush v0.3.0 Windows release under external/brush-v0.3.0 first. See docs/local_brush_training.md.'
}
if (-not $Out) { $Out = Join-Path $WorkspacePath ('runs/room-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')) }
$OutputPath = [IO.Path]::GetFullPath($Out)
if (Test-Path -LiteralPath $OutputPath) { throw 'Choose a fresh output directory; existing runs are never overwritten.' }
New-Item -ItemType Directory -Path $OutputPath | Out-Null
$env:PYTHONPATH = Join-Path $WorkspacePath 'python'

function Invoke-CheckedPython([string[]]$CommandArguments) {
    & $PythonPath @CommandArguments
    if ($LASTEXITCODE -ne 0) { throw "Python stage failed with exit code $LASTEXITCODE" }
}

Invoke-CheckedPython @('-m', 'capture_splat.cli', 'capture-quality-report', '--capture', $CapturePath, '--out', (Join-Path $OutputPath 'capture-qa'))
$CaptureReport = Get-Content -Raw -LiteralPath (Join-Path $OutputPath 'capture-qa/capture_splat_capture_quality_report.json') | ConvertFrom-Json
if ($CaptureReport.decision -eq 'reject') { throw 'Capture quality rejected this input. Review capture-qa before training.' }
Invoke-CheckedPython @((Join-Path $PSScriptRoot 'prepare_brush_capture.py'), '--capture', $CapturePath, '--out', (Join-Path $OutputPath 'prepared'))
$DatasetPath = Join-Path $OutputPath 'prepared/nerfstudio_dataset'
$TrainingPath = Join-Path $OutputPath 'training'
New-Item -ItemType Directory -Path $TrainingPath | Out-Null
$TrainerArguments = @($DatasetPath, '--total-steps', "$Steps", '--max-splats', "$MaxSplats",
    '--max-resolution', "$Resolution", '--sh-degree', '3', '--eval-split-every', '8',
    '--eval-every', "$Steps", '--eval-save-to-disk', '--export-every', "$Steps", '--export-path', $TrainingPath)
@{ trainer = 'Brush v0.3.0'; arguments = $TrainerArguments; pose_source = 'arkit_prior_not_sfm_refined'; proposal_only = $true } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OutputPath 'training-command.json') -Encoding UTF8
& $TrainerPath @TrainerArguments *> (Join-Path $OutputPath 'training.log')
if ($LASTEXITCODE -ne 0) { throw 'Training failed. Inspect training.log; no successful result is claimed.' }
$RawModelPath = Join-Path $TrainingPath ('export_' + $Steps + '.ply')
if (-not (Test-Path -LiteralPath $RawModelPath)) { throw 'Trainer exited without the expected PLY.' }
$CleanModelPath = Join-Path $OutputPath 'room-clean.ply'
Invoke-CheckedPython @('-m', 'capture_splat.cli', 'prune-ply', '--input', $RawModelPath, '--out', $CleanModelPath, '--min-alpha', '12')
$ViewerPath = Join-Path $OutputPath 'viewer'
New-Item -ItemType Directory -Path $ViewerPath | Out-Null
Invoke-CheckedPython @((Join-Path $PSScriptRoot 'export_mobile_splat.py'), '--input', $CleanModelPath, '--out', (Join-Path $ViewerPath 'model.splat'))
$ViewerAssetsPath = Join-Path $WorkspacePath 'apps/ios/CaptureSplat/CaptureSplat/Resources/SplatViewer'
foreach ($AssetName in @('index.html', 'main.js', 'LICENSE', 'UPSTREAM.md')) {
    Copy-Item -LiteralPath (Join-Path $ViewerAssetsPath $AssetName) -Destination $ViewerPath
}
Write-Output "Trained and cleaned model: $CleanModelPath"
Write-Output ('Mobile result: ' + (Join-Path $ViewerPath 'model.splat'))
Write-Output "Open the mobile result using Capture Splat > More > Open trained splat."
Write-Output "For the desktop viewer, serve $ViewerPath with a local HTTP server."
Write-Output 'Training completion is not visual-quality, scale, or collision approval. Inspect the held-out renders.'
