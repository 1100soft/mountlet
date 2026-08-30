param(
  [string]$ReleaseDir = "",
  [Parameter(Mandatory = $true)][string]$Output,
  [string]$Version = "",
  [ValidateSet("x64", "arm64", "x86")][string]$Architecture = "x64",
  [string]$IdentityName = "Mountlet.CI",
  [string]$Publisher = "CN=Mountlet CI",
  [string]$PublisherDisplayName = "Mountlet"
)

$ErrorActionPreference = "Stop"

function ConvertTo-MsixVersion([string]$value) {
  $parts = @((($value.Trim() -replace '^v', '') -split '\.') | Where-Object { $_ -ne "" })
  if ($parts.Count -lt 3) {
    throw "MSIX version requires at least major.minor.patch, got: $value"
  }
  while ($parts.Count -lt 4) {
    $parts += "0"
  }
  if ($parts.Count -ne 4) { throw "MSIX version must have four components: $value" }
  $numbers = foreach ($part in $parts) {
    if ($part -notmatch '^\d+$') { throw "MSIX version component is not numeric: $part" }
    $number = [int]$part
    if ($number -lt 0 -or $number -gt 65535) { throw "MSIX version component is outside 0-65535: $part" }
    $number
  }
  if ($numbers[3] -ne 0) { throw "Microsoft Store reserves the fourth MSIX version component; it must be zero" }
  # Mountlet uses normal SemVer and remains pre-1.0, while Store package
  # versions forbid a zero major component. Offset only the package major so
  # ordering remains monotonic: app 0.7.0 -> package 1.7.0.0.
  $numbers[0] += 1
  if ($numbers[0] -gt 65535) { throw "Mapped MSIX major version exceeds 65535" }
  return ($numbers -join ".")
}

function Find-WindowsKitTool([string]$name) {
  foreach ($root in @(
      "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
      "${env:ProgramFiles}\Windows Kits\10\bin"
    )) {
    if (-not (Test-Path $root)) { continue }
    $found = Get-ChildItem $root -Recurse -Filter $name -ErrorAction SilentlyContinue |
      Where-Object { $_.Directory.Name -eq "x64" } |
      Sort-Object FullName -Descending |
      Select-Object -First 1
    if ($found) { return $found.FullName }
  }
  throw "Could not find $name in the Windows SDK"
}

$windowsDir = $PSScriptRoot
$appRoot = (Resolve-Path (Join-Path $windowsDir "../..")).Path
if (-not $ReleaseDir) {
  $ReleaseDir = Join-Path $appRoot "src-tauri/target/release"
}
$ReleaseDir = (Resolve-Path $ReleaseDir).Path

if (-not $Version) {
  $Version = (Get-Content (Join-Path $appRoot "package.json") -Raw | ConvertFrom-Json).version
}
$msixVersion = ConvertTo-MsixVersion $Version
if ($IdentityName -notmatch '^[A-Za-z0-9.-]{3,50}$') {
  throw "Invalid MSIX identity name: $IdentityName"
}
if (-not $Publisher.Trim() -or -not $PublisherDisplayName.Trim()) {
  throw "MSIX publisher values cannot be empty"
}

$executable = Join-Path $ReleaseDir "mountlet.exe"
if (-not (Test-Path $executable)) {
  throw "Packaged executable not found at $executable"
}

$layout = Join-Path ([System.IO.Path]::GetTempPath()) ("mountlet-msix-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $layout | Out-Null
try {
  Copy-Item $executable (Join-Path $layout "mountlet.exe")
  Get-ChildItem $ReleaseDir -File -Filter "*.dll" | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $layout $_.Name)
  }
  foreach ($name in @("resources", "vendor")) {
    $source = Join-Path $ReleaseDir $name
    if (Test-Path $source) {
      Copy-Item $source (Join-Path $layout $name) -Recurse
    }
  }
  $layoutVendor = Join-Path $layout "vendor"
  if (-not (Test-Path $layoutVendor)) {
    $releaseResourcesVendor = Join-Path $ReleaseDir "resources/vendor"
    $srcVendor = Join-Path $appRoot "src-tauri/vendor"
    if (Test-Path $releaseResourcesVendor) {
      Copy-Item $releaseResourcesVendor $layoutVendor -Recurse
    } elseif (Test-Path $srcVendor) {
      Copy-Item $srcVendor $layoutVendor -Recurse
    }
  }

  Copy-Item (Join-Path $windowsDir "Assets") (Join-Path $layout "Assets") -Recurse

  $manifest = Get-Content (Join-Path $windowsDir "Package.appxmanifest") -Raw
  $manifest = $manifest.Replace("__VERSION__", $msixVersion)
    .Replace("__ARCH__", $Architecture)
    .Replace("__IDENTITY_NAME__", [Security.SecurityElement]::Escape($IdentityName))
    .Replace("__PUBLISHER__", [Security.SecurityElement]::Escape($Publisher))
    .Replace("__PUBLISHER_DISPLAY_NAME__", [Security.SecurityElement]::Escape($PublisherDisplayName))
  $utf8Bom = New-Object System.Text.UTF8Encoding $true
  [System.IO.File]::WriteAllText((Join-Path $layout "AppxManifest.xml"), $manifest, $utf8Bom)

  $makeAppx = Find-WindowsKitTool "makeappx.exe"
  $destination = $Output
  if (-not [System.IO.Path]::IsPathRooted($destination)) {
    $destination = Join-Path (Get-Location) $destination
  }
  $directory = Split-Path $destination -Parent
  if ($directory) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
  }
  if (Test-Path $destination) {
    Remove-Item $destination -Force
  }
  & $makeAppx pack /d $layout /p $destination /o
  if ($LASTEXITCODE -ne 0) {
    throw "MakeAppx failed with exit code $LASTEXITCODE"
  }
  Write-Host "Wrote $destination"
} finally {
  Remove-Item $layout -Recurse -Force -ErrorAction SilentlyContinue
}
