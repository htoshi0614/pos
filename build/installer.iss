; ============================================================
;  POSStart Windows Installer (Inno Setup Script)
;  ビルド: ISCC.exe installer.iss
; ============================================================

#define AppName        "POSStart"
#define AppVersion     "1.0.6"
#define AppPublisher   "POSStart"
#define AppURL         "https://github.com/htoshi0614/pos"
#define AppExeName     "POSStart.exe"

[Setup]
AppId={{8E1D4F5A-9D2B-4C7E-9E1F-7A6B5C4D3E2F}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=
PrivilegesRequired=lowest
OutputBaseFilename=POSStart_setup
OutputDir=output
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; SetupIconFile=icon.ico  ← icon.icoがあれば自動使用される。今は無し
UninstallDisplayIcon={app}\{#AppExeName}
LanguageDetectionMethod=locale
ShowLanguageDialog=auto

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english";  MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "Windows起動時に自動起動する"; GroupDescription: "起動オプション"; Flags: unchecked

[Files]
Source: "dist\POSStart\POSStart.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\POSStart\*";             DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\.env.example";              DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{group}\{#AppName}";             Filename: "{app}\{#AppExeName}"
Name: "{group}\{#AppName} を停止";       Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";       Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}";       Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; アンインストール時にユーザーデータを削除するか聞きたい場合は ConfirmedDelete などのフラグ追加可
; ここではpos.dbなどデータは残す（誤って消えないように）
; Type: filesandordirs; Name: "{app}\pos.db"

[Code]
function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  sUnInstPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1';
  sUnInstallString := '';
  if not RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if IsUpgrade() then
  begin
    MsgBox('既にPOSStartがインストールされています。' + #13#10 +
           'このセットアップは既存のPOSStartを上書きアップデートします。' + #13#10 +
           '（データベースは保持されます）', mbInformation, MB_OK);
  end;
end;
