; Instalador do Forja3D. Gere por scripts\gerar_instalador.py, que confere o OrcaSlicer e o pacote antes.
; Instala só para quem está usando o computador, sem pedir permissão de administrador.

#ifndef Versao
  #define Versao "0.0.0"
#endif

[Setup]
AppId={{8F3C2B1E-6D4A-4E7B-9A51-3C0D7E92F1A6}
AppName=Forja3D
AppVersion={#Versao}
DefaultDirName={autopf}\Forja3D
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ChangesAssociations=yes
OutputDir=..\dist\instalador
OutputBaseFilename=Forja3D-{#Versao}-instalador
SetupIconFile=..\build\forja3d.ico
UninstallDisplayIcon={app}\Forja3D.exe
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "associar"; Description: "Abrir arquivos .forja3d com o Forja3D"
Name: "atalho"; Description: "{cm:CreateDesktopIcon}"

[InstallDelete]
; Sem isso, uma atualização deixaria para trás módulos da versão anterior.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Forja3D\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\orca\OrcaSlicer_2.4.2\*"; DestDir: "{app}\ferramentas\OrcaSlicer_2.4.2"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "TERCEIROS.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Forja3D"; Filename: "{app}\Forja3D.exe"
Name: "{autodesktop}\Forja3D"; Filename: "{app}\Forja3D.exe"; Tasks: atalho

[Registry]
; HKA vira HKEY_CURRENT_USER neste modo: a associação vale só para quem instalou.
Root: HKA; Subkey: "Software\Classes\.forja3d"; ValueType: string; ValueName: ""; ValueData: "Forja3D.Projeto"; Flags: uninsdeletevalue; Tasks: associar
Root: HKA; Subkey: "Software\Classes\.forja3d\OpenWithProgids"; ValueType: string; ValueName: "Forja3D.Projeto"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associar
Root: HKA; Subkey: "Software\Classes\Forja3D.Projeto"; ValueType: string; ValueName: ""; ValueData: "Projeto do Forja3D"; Flags: uninsdeletekey; Tasks: associar
Root: HKA; Subkey: "Software\Classes\Forja3D.Projeto\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\Forja3D.exe,0"; Tasks: associar
Root: HKA; Subkey: "Software\Classes\Forja3D.Projeto\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Forja3D.exe"" ""%1"""; Tasks: associar

[Run]
Filename: "{app}\Forja3D.exe"; Description: "{cm:LaunchProgram,Forja3D}"; Flags: nowait postinstall skipifsilent
