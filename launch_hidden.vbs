Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = base
shell.Environment("PROCESS")("PYTHONUTF8") = "1"
shell.Environment("PROCESS")("PYTHONUNBUFFERED") = "1"
shell.Environment("PROCESS")("JARVIS_ENABLE_DASHBOARD") = "1"

' Keep the console output so a failed phrase or a crash can be diagnosed later.
logDir = base & "\logs"
If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)
logFile = logDir & "\console.log"
If fso.FileExists(logFile) Then
    On Error Resume Next
    fso.CopyFile logFile, logDir & "\console.prev.log", True
    On Error GoTo 0
End If

' Start each run from an empty log (the previous run is kept in console.prev.log),
' otherwise ">>" appends and console.log grows without bound.
If fso.FileExists(logFile) Then
    On Error Resume Next
    fso.DeleteFile logFile, True
    On Error GoTo 0
End If

cmd = "cmd /c " & Chr(34) & Chr(34) & base & "\.venv\Scripts\python.exe" & Chr(34) & " " & Chr(34) & base & "\main.py" & Chr(34) & " >> " & Chr(34) & logFile & Chr(34) & " 2>&1" & Chr(34)
shell.Run cmd, 0, False
