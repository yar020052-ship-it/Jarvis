# Builds the tiny JARVIS Android PCM player DEX (no app install needed).
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$sdk = "$env:LOCALAPPDATA\Android\Sdk"
$androidJar = (Get-ChildItem "$sdk\platforms\*\android.jar" | Select-Object -First 1).FullName
$d8 = Get-ChildItem "$sdk\build-tools\*\d8.bat" | Select-Object -First 1 -ExpandProperty FullName
if (-not $androidJar) { throw "android.jar not found" }
if (-not $d8) { throw "d8.bat not found" }

Set-Location $dir
if (Test-Path classes) { Remove-Item -Recurse -Force classes }
if (Test-Path out) { Remove-Item -Recurse -Force out }
New-Item -ItemType Directory -Force classes, out | Out-Null

javac -source 11 -target 11 -classpath "$androidJar" -d classes PcmServer.java
if ($LASTEXITCODE -ne 0) { throw "javac failed" }
$classes = Get-ChildItem -Recurse -Filter *.class classes | ForEach-Object { $_.FullName }
& $d8 --min-api 31 --lib "$androidJar" --output out @classes
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }
if (-not (Test-Path out\classes.dex)) { throw "classes.dex not produced" }
Write-Output "OK: $(Resolve-Path out\classes.dex)"