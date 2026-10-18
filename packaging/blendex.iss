#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=1.2.3 blendex.iss
#endif

[Setup]
AppId={{6F2C1B7E-4D1A-4C8B-9E55-2B7D0F3A9C41}
AppName=Blendex
AppVersion={#AppVersion}
AppVerName=Blendex {#AppVersion}
AppPublisher=g-s-jithesh
AppPublisherURL=https://github.com/g-s-jithesh/blendex
AppSupportURL=https://github.com/g-s-jithesh/blendex/issues
DefaultDirName={autopf}\Blendex
DefaultGroupName=Blendex
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=blendex.ico
UninstallDisplayIcon={app}\Blendex.exe
OutputDir=..\release
OutputBaseFilename=Blendex-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\release\dist\Blendex\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Blendex"; Filename: "{app}\Blendex.exe"
Name: "{autodesktop}\Blendex"; Filename: "{app}\Blendex.exe"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Blendex"

[Run]
Filename: "{app}\Blendex.exe"; Description: "{cm:LaunchProgram,Blendex}"; Flags: nowait postinstall skipifsilent
