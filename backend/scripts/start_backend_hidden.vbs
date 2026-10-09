' ============================================================
'  InvestBuddy backend autostart at Windows logon (hidden window)
'
'  Usage: put this file (or a shortcut to it) into the Startup folder:
'      %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
'  It runs start_backend.bat from the same folder with a HIDDEN console;
'  backend logs are appended to backend\logs\uvicorn.log
'
'  PORTABILITY (rewritten 2026-10-09)
'    The path is derived from WScript.ScriptFullName, so the project can live
'    in any directory and this file needs no editing.
'    The old version hardcoded:
'      sh.Run "D:\Project\InfoData\backend\scripts\start_backend.bat", 0, False
'    which broke as soon as the project moved.
'
'  NOTE: if the project has been moved, replace any old copy of this file
'  that still sits in the Startup folder with this one.
'
'  NOTE: ASCII-only on purpose - non-ASCII comments in .vbs can be misread
'  by the script host and break parsing.
' ============================================================

Dim fso, scriptDir, batPath, sh

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = fso.BuildPath(scriptDir, "start_backend.bat")

If Not fso.FileExists(batPath) Then
    MsgBox "Cannot find backend launcher:" & vbCrLf & batPath, 16, "InvestBuddy"
    WScript.Quit 1
End If

' 2nd arg 0 = hidden window ; 3rd arg False = do not wait for it to finish
Set sh = CreateObject("WScript.Shell")
sh.Run """" & batPath & """", 0, False
