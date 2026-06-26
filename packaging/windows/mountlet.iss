#ifndef AppVersion
  #error AppVersion must be defined
#endif
#ifndef SourceDir
  #error SourceDir must be defined
#endif
#ifndef OutputDir
  #error OutputDir must be defined
#endif
#ifndef OutputBaseName
  #error OutputBaseName must be defined
#endif

[Setup]
AppId={{B36E40DC-6A3E-45EC-A668-25E36A9E527F}
AppName=Mountlet
AppVersion={#AppVersion}
AppPublisher=Eric Holt
AppPublisherURL=https://github.com/eric-holt/mountlet
AppSupportURL=https://github.com/eric-holt/mountlet/issues
DefaultDirName={localappdata}\Programs\Mountlet
DefaultGroupName=Mountlet
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Mountlet
UninstallDisplayIcon={app}\Mountlet.exe
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Mountlet"; Filename: "{app}\Mountlet.exe"
Name: "{group}\Uninstall Mountlet"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Mountlet"; Filename: "{app}\Mountlet.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Mountlet.exe"; Description: "Start Mountlet"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM Mountlet.exe /F >NUL 2>&1"; Flags: runhidden; RunOnceId: "StopMountlet"

[Code]
var
  MaintenancePage: TInputOptionWizardPage;
  ExistingUninstaller: String;
  ClosingAfterUninstall: Boolean;

function RcloneAvailable: Boolean;
begin
  Result :=
    FileExists(GetEnv('RCLONE_PATH')) or
    (FileSearch('rclone.exe', GetEnv('PATH')) <> '') or
    FileExists(ExpandConstant('{localappdata}\Microsoft\WinGet\Links\rclone.exe')) or
    FileExists(ExpandConstant('{userprofile}\scoop\shims\rclone.exe')) or
    FileExists(ExpandConstant('{commonappdata}\chocolatey\bin\rclone.exe')) or
    FileExists(ExpandConstant('{pf}\rclone\rclone.exe')) or
    FileExists('C:\rclone\rclone.exe');
end;

function WinFspAvailable: Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{pf}\WinFsp\bin\fsptool-x64.exe')) or
    FileExists(ExpandConstant('{pf32}\WinFsp\bin\fsptool-x86.exe'));
end;

function HasCommandLineParam(Value: String): Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
    if CompareText(ParamStr(Index), Value) = 0 then
    begin
      Result := True;
      exit;
    end;
end;

function InitializeSetup: Boolean;
var
  Missing: String;
begin
  if HasCommandLineParam('/PACKAGINGTEST') then
  begin
    Result := True;
    exit;
  end;
  if RegQueryStringValue(
    HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B36E40DC-6A3E-45EC-A668-25E36A9E527F}_is1',
    'UninstallString',
    ExistingUninstaller
  ) then
  begin
    Result := True;
    exit;
  end;
  Missing := '';
  if not RcloneAvailable then
    Missing := Missing + #13#10 + '- rclone: https://rclone.org/install/';
  if not WinFspAvailable then
    Missing := Missing + #13#10 + '- WinFsp: https://winfsp.dev/rel/';
  if Missing <> '' then
  begin
    MsgBox(
      'Mountlet needs the following software before installation:' + #13#10 +
      Missing + #13#10#13#10 +
      'Install it, then run the Mountlet installer again.',
      mbInformation,
      MB_OK
    );
    Result := False;
    exit;
  end;
  Result := True;
end;

procedure InitializeWizard;
begin
  if RegQueryStringValue(
    HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B36E40DC-6A3E-45EC-A668-25E36A9E527F}_is1',
    'UninstallString',
    ExistingUninstaller
  ) then
  begin
    MaintenancePage := CreateInputOptionPage(
      wpWelcome,
      'Mountlet is already installed',
      'Choose a maintenance action',
      'Update or repair the installed files, or remove Mountlet. User settings and rclone data are preserved.',
      True,
      False
    );
    MaintenancePage.Add('Update or repair Mountlet');
    MaintenancePage.Add('Uninstall Mountlet');
    MaintenancePage.SelectedValueIndex := 0;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if (MaintenancePage <> nil) and (CurPageID = MaintenancePage.ID) and
     (MaintenancePage.SelectedValueIndex = 1) then
  begin
    if Exec(
      RemoveQuotes(ExistingUninstaller),
      '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
      '',
      SW_SHOW,
      ewWaitUntilTerminated,
      ResultCode
    ) then
    begin
      ClosingAfterUninstall := True;
      MsgBox('Mountlet was uninstalled. Your settings and rclone configuration were preserved.', mbInformation, MB_OK);
      WizardForm.Close;
    end
    else
      MsgBox('Mountlet could not be uninstalled. Use Windows Installed apps instead.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if ClosingAfterUninstall then
    Confirm := False;
end;
