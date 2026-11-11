@echo off
title Sistema de Gestao - Corretora de Seguros
cd /d "%~dp0"

echo ===================================================
echo   Sistema de Gestao - Corretora de Seguros
echo ===================================================
echo.

REM 1. Verifica se o Python esta instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no sistema.
    echo Instale o Python ou adicione-o ao PATH do Windows.
    echo.
    pause
    exit /b 1
)

REM 2. Garante instalacao das dependencias (Flask e Servidor Waitress)
echo Verificando dependencias...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [AVISO] Falha ao verificar dependencias. Continuando...
)

REM 3. Garante que o banco de dados SQLite esteja criado
if not exist "corretora.db" (
    echo Criando banco de dados inicial da corretora...
    python init_db.py
)

REM 4. Descobre o IP local da maquina para acesso em rede
for /f "tokens=*" %%I in ('python -c "import socket; s=socket.socket(); s.connect((\"8.8.8.8\",80)); print(s.getsockname()[0]); s.close()" 2^>nul') do set LOCAL_IP=%%I
if "%LOCAL_IP%"=="" set LOCAL_IP=127.0.0.1

echo.
echo ===================================================
echo   Servidor de Producao (Waitress WSGI) Ativo!
echo.
echo   Acesso neste computador:
echo   http://127.0.0.1:5000
echo.
echo   Acesso na rede local (outros computadores / celulares):
echo   http://%LOCAL_IP%:5000
echo.
echo   Banco de dados: corretora.db
echo   Backup: copie o arquivo corretora.db e a pasta uploads
echo.
echo   Pressione Ctrl+C nesta janela para encerrar.
echo ===================================================
echo.

REM 5. Abre o navegador apos 2 segundos
start "" cmd /c "timeout /t 2 /nobreak >nul 2>&1 & start http://127.0.0.1:5000"

REM 6. Regra de firewall para permitir conexoes locais na porta 5000
netsh advfirewall firewall show rule name="Corretora Seguros" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="Corretora Seguros" dir=in action=allow protocol=TCP localport=5000 >nul 2>&1
)

REM 7. Executa o servidor de producao multithreaded (Waitress)
python -m waitress --host=0.0.0.0 --port=5000 --threads=8 app:app
pause
