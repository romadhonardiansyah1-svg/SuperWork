Option Explicit
Dim shell, files, root, python, candidate, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = "pythonw.exe"
candidate = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe"
If files.FileExists(candidate) Then python = candidate
candidate = root & "\.venv\Scripts\pythonw.exe"
If files.FileExists(candidate) Then python = candidate
command = Chr(34) & python & Chr(34) & " " & Chr(34) & root & "\tools\launch_dashboard.py" & Chr(34)
shell.CurrentDirectory = root
On Error Resume Next
exitCode = shell.Run(command, 0, False)
If Err.Number <> 0 Then
    MsgBox "SuperWork tidak dapat membuka Python. Pastikan Python 3.11 tersedia di komputer ini.", vbCritical, "SuperWork"
    WScript.Quit 1
End If
On Error GoTo 0
