; Carpet Eater - Inno Setup script
; Per-user install (no admin), no AppLocker / no UAC prompts.
;
; Build (locally): iscc installer\carpeteater.iss
; Build (CI):      iscc /DAppVersion=0.1.0 installer\carpeteater.iss
;
; Expects dist\CarpetEater.exe and build_icon.ico to exist beside this repo
; (produced by build.bat or the release workflow).

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#define AppName       "Carpet Eater"
#define AppExeName    "CarpetEater.exe"
#define AppPublisher  "Maximilian Wikstrom"
#define AppURL        "https://github.com/MaximilianWik/Carpet-Eater"

[Setup]
; Stable AppId — never change this; it's how Windows tracks upgrades.
AppId={{8E1E5B3F-7AC0-4D8B-9F6F-8E0F47C3E1A9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}

; Per-user install — no admin, sidesteps AppLocker on locked-down machines.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

DefaultDirName={localappdata}\Programs\CarpetEater
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes

OutputDir=Output
OutputBaseFilename=CarpetEater-Setup
SetupIconFile=..\build_icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The single bundled EXE is ~80 MB; allow it.
DiskSpanning=no

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Quietly close a running instance before upgrading.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\CarpetEater.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";          Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}";    Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Launch on finish (unchecked by default).
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent unchecked
