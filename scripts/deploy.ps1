# H3_CONTROLLER selects the native packaged executable. No password arguments.
$ErrorActionPreference = 'Stop'
$controller = if ($env:H3_CONTROLLER) { $env:H3_CONTROLLER } else { 'h3-controller.exe' }
& $controller deploy @args
exit $LASTEXITCODE
