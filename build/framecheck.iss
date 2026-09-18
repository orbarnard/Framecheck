; Framecheck Windows installer.
;
; Produces a single FramecheckSetup.exe: download it, run it, and Framecheck is
; in the Start menu. No unzipping, no hunting for the executable.
;
; Build with:  python tools/build_exe.py --installer
; It supplies AppVersion (from framecheck/__init__.py, the one place the
; version lives) and KnownSpecHashes; ISCC run by hand refuses without them.
;
; Upgrading: run a newer setup over an older install. The fixed AppId makes it
; replace the install in place -- same folder, same shortcuts, settings kept.
;
; Installs per-user into %LOCALAPPDATA%\Programs\Framecheck so no administrator
; prompt is needed. A media buyer on a managed laptop can install this without
; calling IT, which matters more here than landing in Program Files.

#define AppName "Framecheck"
#ifndef AppVersion
  #error Build with: python tools/build_exe.py --installer
#endif
#ifndef KnownSpecHashes
  #error Build with: python tools/build_exe.py --installer
#endif
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

[InstallDelete]
; Shipped specs are replaced wholesale, so a spec dropped or renamed in a newer
; release cannot linger and keep loading. Anything the user made in here has
; already been rescued to their own specs folder by PrepareToInstall below.
Type: files; Name: "{app}\specs\*.json"

[UninstallDelete]
; Settings and logs the app writes at runtime; leaving them behind after an
; uninstall is untidy.
Type: filesandordirs; Name: "{localappdata}\Framecheck\logs"

; Do not delete %LOCALAPPDATA%\Framecheck\specs on uninstall: it is the user's.

[Code]
// Before an upgrade touches the install folder, copy every spec in it that no
// release ever shipped -- one the user edited or added there, as 0.1.0's notes
// told them to -- into the user specs folder, which upgrades never touch and
// where a spec overrides the built-in with the same id. A failed copy stops
// the install rather than letting [InstallDelete] destroy the user's work.
function RescueUserSpecs(): String;
var
  FindRec: TFindRec;
  SpecsDir, UserDir, Src, Dst, Hash: String;
begin
  Result := '';
  SpecsDir := ExpandConstant('{app}\specs\');
  UserDir := ExpandConstant('{localappdata}\Framecheck\specs\');
  if not FindFirst(SpecsDir + '*.json', FindRec) then
    Exit;
  try
    repeat
      Src := SpecsDir + FindRec.Name;
      Hash := GetSHA1OfFile(Src);
      if Pos(';' + Hash + ';', '{#KnownSpecHashes}') = 0 then
      begin
        Dst := UserDir + FindRec.Name;
        if FileExists(Dst) and (GetSHA1OfFile(Dst) = Hash) then
          continue;  // already rescued
        if FileExists(Dst) then
          Dst := UserDir + ChangeFileExt(FindRec.Name, '') + ' (from old install).json';
        if not (ForceDirectories(UserDir) and CopyFile(Src, Dst, False)) then
        begin
          Result := 'Could not keep your custom spec ' + Src + ' (copying it to ' +
            UserDir + ' failed). Copy it somewhere safe, then run setup again.';
          Exit;
        end;
        Log('Kept custom spec ' + Src + ' as ' + Dst);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := RescueUserSpecs();
end;
