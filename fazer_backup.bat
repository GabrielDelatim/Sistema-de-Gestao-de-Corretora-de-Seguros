@echo off
title Backup do Sistema da Corretora
cd /d "%~dp0"

echo ===================================================
echo   Rotina de Backup - Corretora de Seguros
echo ===================================================
echo.

REM Gera timestamp no formato YYYY-MM-DD_HHMM
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set LDT=%%I
set TIMESTAMP=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2%_%LDT:~8,2%%LDT:~10,2%

set DESTINO=backups\backup_%TIMESTAMP%
mkdir "%DESTINO%" >nul 2>&1

echo Criando copia de seguranca em: %DESTINO%

REM Copia banco de dados SQLite
if exist "corretora.db" (
    copy "corretora.db" "%DESTINO%\corretora.db" >nul
    echo [OK] Banco de dados salvo: corretora.db
) else (
    echo [AVISO] Arquivo corretora.db nao encontrado.
)

REM Copia arquivos anexos
if exist "uploads" (
    xcopy /E /I /Y "uploads" "%DESTINO%\uploads" >nul 2>&1
    echo [OK] Pasta de anexos salva: uploads/
)

echo.
echo ===================================================
echo   Backup concluido com sucesso!
echo   Pasta de destino: %~dp0%DESTINO%
echo ===================================================
echo.
ping 127.0.0.1 -n 3 >nul
