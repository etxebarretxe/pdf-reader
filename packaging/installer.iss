; Instalador de Lector PDF para Windows (Inno Setup 6).
; Empaqueta la carpeta standalone generada por packaging/build_windows.py.
;
;   1) python packaging/build_windows.py
;   2) Compilar este script con Inno Setup (ISCC.exe packaging\installer.iss)

#define AppName "Lector PDF"
#define AppVersion "1.0.0"
#define AppExe "LectorPDF.exe"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=LectorPDF
DefaultDirName={autopf}\LectorPDF
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\build\installer
OutputBaseFilename=LectorPDF-{#AppVersion}-setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Sin privilegios de administrador: instalacion por usuario.
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
Name: "associate"; Description: "Abrir los archivos .pdf con {#AppName}"; GroupDescription: "Asociaciones de archivo:"; Flags: unchecked

[Files]
; Todo el contenido de la carpeta standalone de Nuitka.
Source: "..\build\main.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Asociacion de la extension .pdf (solo si el usuario marca la tarea).
Root: HKA; Subkey: "Software\Classes\LectorPDF.Documento"; ValueType: string; ValueName: ""; ValueData: "Documento PDF"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\LectorPDF.Documento\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\LectorPDF.Documento\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "LectorPDF.Documento"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent
