; Kurgu Windows kurulum programı (Inno Setup 6)
; packaging/build.py tarafından şu tanımlarla çağrılır:
;   /DMyAppVersion=… /DSourceDir=…\dist\Kurgu /DOutDir=…\dist /DIconFile=…\icon.ico

#define MyAppName "Kurgu"
#define MyAppExeName "Kurgu.exe"

[Setup]
AppId={{6E2B5C1A-7F3D-4B8E-9C11-4B7A2D9E0F31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Kurgu contributors
AppPublisherURL=https://github.com/Batuhanuluer/Kurgu
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=Kurgu-{#MyAppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; .kurgu proje dosyaları çift tıklayınca Kurgu ile açılsın
Root: HKA; Subkey: "Software\Classes\.kurgu"; ValueType: string; ValueName: ""; ValueData: "KurguProject"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\KurguProject"; ValueType: string; ValueName: ""; ValueData: "Kurgu Project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\KurguProject\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\KurguProject\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
