!macro customUnInstall
  ${ifNot} ${isUpdated}
    SetShellVarContext current
    ClearErrors
    RMDir /r "$APPDATA\SmallOffice"
    ${if} ${Errors}
      Abort "Unable to clear SmallOffice data. Close SmallOffice and retry uninstalling."
    ${endif}
    ${if} $installMode == "all"
      SetShellVarContext all
    ${endif}
  ${endif}
!macroend
