; Framecheck Windows installer.
;
; Produces a single FramecheckSetup.exe: download it, run it, and Framecheck is
; in the Start menu. No unzipping, no hunting for the executable.
;
; Build with:  python tools/build_exe.py --installer
; (or directly: ISCC.exe build\framecheck.iss)
;
; Installs per-user into %LOCALAPPDATA%\Programs\Framecheck so no administrator
; prompt is needed. A media buyer on a managed laptop can install this without
; calling IT, which matters more here than landing in Program Files.

#define AppName "Framecheck"
#define AppVersion "0.1.0"
#define AppPublisher "Framecheck"
#define AppURL "https://github.com/orbarnard/Framecheck"
#define AppExeName "Framecheck.exe"

[Setup]
AppId={{8F3A6C21-7D4E-4B92-9C1F-0E5A2B7D6C48}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Video, to spec.
VersionInfoCopyright=GPL-3.0-or-later

; Per-user install: no UAC prompt, works on locked-down machines.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

; The licence is shown during install because the app is GPL-3.0-or-later and
; bundles GPL FFmpeg and libmpv.
LicenseFile=..\LICENSE
InfoAfterFile=..\build\installer_after.txt

OutputDir=..\dist
OutputBaseFilename=FramecheckSetup-{#AppVersion}
SetupIconFile=..\assets\framecheck.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}

; The payload is ~423 MB of mostly-incompressible binaries (FFmpeg, libmpv,
; Qt). LZMA2 at max gets it to about a third; ultra64 costs a lot of build time
; and memory for very little extra.
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The whole PyInstaller onedir output. recursesubdirs keeps vendor/, specs/,
; assets/, licenses/ and the loose Qt DLLs exactly where the app expects them.
Source: "..\dist\Framecheck\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings and logs the app writes at runtime; leaving them behind after an
; uninstall is untidy.
Type: filesandordirs; Name: "{localappdata}\Framecheck\logs"
