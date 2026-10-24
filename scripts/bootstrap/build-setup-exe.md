# Сборка setup.exe (Inno Setup)

Для создания `Jarvis-Setup.exe` можно использовать [Inno Setup](https://jrsoftware.org/isinfo.php).

Пример скрипта `JarvisSetup.iss` (упрощённый):

```iss
[Setup]
AppName=JARVIS
AppVersion=0.1.0
DefaultDirName={localappdata}\Programs\Jarvis
DefaultGroupName=JARVIS
OutputDir=.\dist
OutputBaseFilename=Jarvis-Setup

[Files]
Source: "scripts\bootstrap\setup-jarvis.ps1"; DestDir: "{tmp}"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{tmp}\setup-jarvis.ps1"""; WorkingDir: "{tmp}"; Flags: runhidden nowait
```

Либо просто распространять `setup-jarvis.ps1`/`.sh` напрямую через `irm`/`curl` как в README.
