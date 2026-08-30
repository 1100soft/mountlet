param(
  [Parameter(Mandatory = $true)][string]$Package,
  [Parameter(Mandatory = $true)][string]$ExpectedVersion,
  [Parameter(Mandatory = $true)][string]$Marker
)

$ErrorActionPreference = "Stop"

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

$packagePath = (Resolve-Path $Package).Path
$tempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$work = Join-Path $tempRoot "mountlet-msix-sign"
New-Item -ItemType Directory -Path $work -Force | Out-Null
$pfx = Join-Path $work "msix.pfx"
$cer = Join-Path $work "msix.cer"
$signed = Join-Path $work ([IO.Path]::GetFileName($packagePath))
$unpacked = Join-Path $work "unpacked"

$makeAppx = Find-WindowsKitTool "makeappx.exe"
& $makeAppx unpack /p $packagePath /d $unpacked /o | Out-Null
if ($LASTEXITCODE -ne 0) { throw "MakeAppx unpack failed with exit code $LASTEXITCODE" }
[xml]$manifest = Get-Content (Join-Path $unpacked "AppxManifest.xml") -Raw
$identityName = $manifest.Package.Identity.Name
$publisher = $manifest.Package.Identity.Publisher
$applicationId = $manifest.Package.Applications.Application.Id
if (-not $identityName -or -not $publisher -or -not $applicationId) {
  throw "MSIX manifest identity is incomplete"
}

$certificate = New-SelfSignedCertificate `
  -Type Custom `
  -Subject $publisher `
  -FriendlyName "Mountlet CI MSIX" `
  -KeyUsage DigitalSignature `
  -CertStoreLocation "Cert:\CurrentUser\My" `
  -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
$password = ConvertTo-SecureString "mountlet-ci" -AsPlainText -Force
Export-PfxCertificate -Cert $certificate -FilePath $pfx -Password $password | Out-Null
Export-Certificate -Cert $certificate -FilePath $cer | Out-Null
Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\CurrentUser\TrustedPeople" | Out-Null
Write-Host "Trusting the temporary MSIX certificate"
& certutil.exe -user -silent -addstore -f Root $cer | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw "certutil failed to trust the temporary MSIX certificate with exit code $LASTEXITCODE"
}

Copy-Item $packagePath $signed -Force
$signTool = Find-WindowsKitTool "signtool.exe"
& $signTool sign /fd SHA256 /f $pfx /p mountlet-ci $signed
if ($LASTEXITCODE -ne 0) {
  throw "SignTool failed with exit code $LASTEXITCODE"
}

Get-AppxPackage -Name $identityName | Remove-AppxPackage -ErrorAction SilentlyContinue
Write-Host "Installing the CI-signed MSIX package"
Add-AppxPackage -Path $signed
$installed = Get-AppxPackage -Name $identityName | Select-Object -First 1
if (-not $installed) {
  throw "MSIX package $identityName was not installed"
}
$executable = Join-Path $installed.InstallLocation "mountlet.exe"
if (-not (Test-Path $executable)) {
  throw "Installed MSIX executable not found at $executable"
}

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class MountletMsixWindowProbe {
  private delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);
  [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
  [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder name, int count);
  [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
  public static uint[] VisibleConsoleProcessIds() {
    var ids = new List<uint>();
    EnumWindows((window, parameter) => {
      var name = new StringBuilder(256);
      if (IsWindowVisible(window) && GetClassName(window, name, name.Capacity) > 0 && name.ToString() == "ConsoleWindowClass") {
        uint processId;
        GetWindowThreadProcessId(window, out processId);
        ids.Add(processId);
      }
      return true;
    }, IntPtr.Zero);
    return ids.ToArray();
  }
}

[ComImport, Guid("2E941141-7F97-4756-BA1D-9DECDE894A3D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IApplicationActivationManager {
  int ActivateApplication([MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
    [MarshalAs(UnmanagedType.LPWStr)] string arguments, uint options, out uint processId);
  int ActivateForFile(string appUserModelId, IntPtr itemArray, string verb, out uint processId);
  int ActivateForProtocol(string appUserModelId, IntPtr itemArray, out uint processId);
}
[ComImport, Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")]
class ApplicationActivationManager { }
public static class MountletMsixActivator {
  public static uint Activate(string appUserModelId, string arguments) {
    var manager = (IApplicationActivationManager)new ApplicationActivationManager();
    uint processId;
    int result = manager.ActivateApplication(appUserModelId, arguments, 0, out processId);
    if (result < 0) Marshal.ThrowExceptionForHR(result);
    return processId;
  }
}
"@

Remove-Item $Marker -Force -ErrorAction SilentlyContinue
$existingConsoles = [MountletMsixWindowProbe]::VisibleConsoleProcessIds()
$unexpectedConsoles = [System.Collections.Generic.HashSet[uint32]]::new()
$appUserModelId = "$($installed.PackageFamilyName)!$applicationId"
Write-Host "Activating $appUserModelId through its package identity"
$activatedProcessId = [MountletMsixActivator]::Activate($appUserModelId, "--startup-smoke `"$Marker`"")
$process = Get-Process -Id $activatedProcessId
try {
  for ($attempt = 0; $attempt -lt 600 -and -not (Test-Path $Marker); $attempt++) {
    if ($process.HasExited) { break }
    foreach ($consoleProcess in [MountletMsixWindowProbe]::VisibleConsoleProcessIds()) {
      if ($consoleProcess -notin $existingConsoles) { $null = $unexpectedConsoles.Add($consoleProcess) }
    }
    Start-Sleep -Milliseconds 100
    $process.Refresh()
  }
  if (-not (Test-Path $Marker)) { throw "Installed MSIX app did not report startup readiness" }
  if ($unexpectedConsoles.Count -ne 0) { throw "Installed MSIX app spawned visible console windows owned by: $($unexpectedConsoles -join ', ')" }
  if (-not $process.WaitForExit(10000)) { throw "Installed MSIX app did not exit after its startup probe" }
  if ($process.ExitCode -ne 0) { throw "Installed MSIX app exited with $($process.ExitCode)" }
  $result = Get-Content $Marker -Raw | ConvertFrom-Json
  if ($result.version -ne $ExpectedVersion -or -not $result.buildId -or -not $result.frontendReady -or -not $result.mainWindowReady -or -not $result.mainWindowVisible -or -not $result.remoteStateReady -or -not $result.behaviorComplete) {
    throw "Installed MSIX app returned an invalid startup probe: $($result | ConvertTo-Json -Compress)"
  }
  Write-Host "MSIX package activation and startup probe passed"
} finally {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  Get-AppxPackage -Name $identityName | Remove-AppxPackage -ErrorAction SilentlyContinue
  Remove-Item "Cert:\CurrentUser\Root\$($certificate.Thumbprint)" -Force -ErrorAction SilentlyContinue
  Remove-Item "Cert:\CurrentUser\TrustedPeople\$($certificate.Thumbprint)" -Force -ErrorAction SilentlyContinue
  Remove-Item "Cert:\CurrentUser\My\$($certificate.Thumbprint)" -Force -ErrorAction SilentlyContinue
}
