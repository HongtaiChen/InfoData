' InvestBuddy backend auto-start on Windows logon (hidden console)
' Copy this file into the Startup folder:
'   C:\Users\cht\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup
' It runs scripts\start_backend.bat hidden; backend logs go to backend\logs\uvicorn.log
Set sh = CreateObject("WScript.Shell")
sh.Run "D:\Project\InfoData\backend\scripts\start_backend.bat", 0, False
