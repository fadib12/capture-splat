# Run elevated on the selected PC. No public-network or router rule is added.
$ErrorActionPreference = 'Stop'
$captureRepo = Split-Path -Parent $PSScriptRoot
& netsh advfirewall firewall add rule name='Capture Splat ReSplat PC' dir=in action=allow protocol=TCP localport=8770 profile=private remoteip=localsubnet | Out-File (Join-Path $captureRepo 'runs/pc-bridge-firewall.log')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
