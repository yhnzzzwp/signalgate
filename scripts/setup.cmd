@echo off
rem Wrapper Windows untuk setup.py.
rem Perintah "python3" tidak ada di Windows: yang ada peluncur "py" dan "python". Kalau keduanya
rem tidak terpasang, Windows menangkap nama itu lewat App Execution Alias dan memunculkan pesan
rem "Python was not found" yang terdengar seperti error skrip ini, padahal bukan.
setlocal

set "SCRIPT=%~dp0setup.py"

py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 "%SCRIPT%" %*
    exit /b %errorlevel%
)

python --version >nul 2>&1
if not errorlevel 1 (
    python "%SCRIPT%" %*
    exit /b %errorlevel%
)

echo.
echo [ gagal] Python tidak ditemukan di PATH.
echo.
echo Pasang Python 3.11 atau lebih baru dari:
echo     https://www.python.org/downloads/windows/
echo.
echo Saat memasang, CENTANG "Add python.exe to PATH". Tutup lalu buka lagi Command Prompt
echo sesudahnya, karena PATH hanya dibaca saat jendela dibuka.
echo.
echo Kalau mengetik "python" malah membuka Microsoft Store, matikan aliasnya di:
echo     Settings ^> Apps ^> Advanced app settings ^> App execution aliases
echo dan matikan entri python.exe serta python3.exe.
echo.
exit /b 1
