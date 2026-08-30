; Tauri uses the finish-page "readme" checkbox for desktop shortcut creation.
; NSIS checks it by default unless this MUI setting is defined before pages.
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

; v0.6.8 and earlier used Inno Setup and a different install directory.  Remove
; that application during the native upgrade while leaving AppData (settings,
; trial and cache state) untouched.  Otherwise Windows presents two Mountlet
; installations and the retired Python executable can still start at login.
!macro NSIS_HOOK_PREINSTALL
  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\{B36E40DC-6A3E-45EC-A668-25E36A9E527F}_is1" "QuietUninstallString"
  ${If} $R0 == ""
    ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\{B36E40DC-6A3E-45EC-A668-25E36A9E527F}_is1" "UninstallString"
  ${EndIf}
  ${If} $R0 != ""
    DetailPrint "Removing the previous Python-based Mountlet installation..."
    ExecWait '$R0 /VERYSILENT /SUPPRESSMSGBOXES /NORESTART' $R1
    ${If} $R1 != 0
      Abort "The previous Mountlet installation could not be removed (exit code $R1). Close Mountlet and run setup again."
    ${EndIf}
  ${EndIf}
!macroend
