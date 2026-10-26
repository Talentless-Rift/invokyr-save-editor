@echo off
rem Double-click launcher for the Invokyr save editor.
cd /d "%~dp0"
where pythonw >nul 2>&1 && (start "" pythonw "%~dp0invokyr_gui.py" & exit /b 0)
where python  >nul 2>&1 && (start "" python  "%~dp0invokyr_gui.py" & exit /b 0)
echo Python not found on PATH. Install Python 3 (with tkinter) or run:
echo     python invokyr_gui.py
pause
