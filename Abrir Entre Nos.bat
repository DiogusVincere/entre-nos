@echo off
rem Abre o Entre Nos (servidor local + navegador) sem deixar janela preta aberta.
rem Antes confere se os arquivos do programa estao nesta pasta (o antivirus pode te-los
rem colocado em quarentena) e se o Python consegue carregar o Entre Nos (com a biblioteca
rem cryptography). Faltando algo, explica o que fazer e espera uma tecla.
rem Nunca tenta contornar o antivirus: nada de rodar o programa por outro caminho.
setlocal
cd /d "%~dp0"
for %%F in ("iniciar.py" "entrenos\servidor.py" "entrenos\nucleo.py" "entrenos\cofre.py" "entrenos\banco.py" "web\index.html" "web\app.js" "web\estilo.css") do (
    if not exist "%~dp0%%~F" (
        set "FALTA=%%~F"
        goto falta_arquivo
    )
)
rem Primeiro o pythonw do PATH; se ele nao carregar o Entre Nos, o da instalacao padrao do
rem Python 3.14 deste usuario (gerenciador de instalacao ou instalador do python.org).
set "PYW=pythonw"
pythonw -c "import cryptography, entrenos.servidor" >nul 2>nul
if not errorlevel 1 goto abrir
set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
if not exist "%PYW%" set "PYW=%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
if not exist "%PYW%" goto sem_python
"%PYW%" -c "import cryptography, entrenos.servidor" >nul 2>nul
if errorlevel 1 goto nao_carrega

:abrir
start "" "%PYW%" "%~dp0iniciar.py" %*
exit /b 0

:falta_arquivo
title Entre Nos - falta um arquivo
echo.
echo  Nao consegui abrir o Entre Nos: falta o arquivo %FALTA%
echo  na pasta %~dp0
echo.
echo  O antivirus pode ter colocado esse arquivo em quarentena (em 30/09/2026 isso
echo  aconteceu com o iniciar.py). Seu diario NAO foi afetado: ele fica em outra pasta,
echo  %LOCALAPPDATA%\EntreNos, e continua criptografado.
echo.
echo  O que fazer:
echo   - No computador da empresa: peca para a TI restaurar o arquivo da quarentena do
echo     antivirus (Bitdefender) e liberar a pasta %~dp0
echo   - No computador pessoal: Seguranca do Windows, Protecao contra virus e ameacas,
echo     Historico de protecao, e restaure o arquivo (so se for mesmo o do Entre Nos).
echo   - Nao desligue o antivirus. O README.md explica tudo, na secao
echo     "Se o antivirus bloquear".
echo.
pause
exit /b 1

:nao_carrega
title Entre Nos - nao consegui carregar
echo.
echo  O Python abriu, mas nao conseguiu carregar o Entre Nos: falta a biblioteca
echo  cryptography, ou algum arquivo do programa foi bloqueado pelo antivirus.
echo  Seu diario NAO foi afetado: ele fica em %LOCALAPPDATA%\EntreNos.
echo.
echo  Para ver o motivo, abra um terminal nesta pasta e rode:
echo      python iniciar.py
echo  Se faltar a biblioteca, instale com:
echo      python -m pip install cryptography rapidfuzz
echo  Se for o antivirus, veja a secao "Se o antivirus bloquear" do README.md.
echo  Nao desligue o antivirus.
echo.
pause
exit /b 1

:sem_python
title Entre Nos - falta o Python
echo.
echo  Nao encontrei o Python 3.14 com a biblioteca cryptography neste computador.
echo.
echo  Em outro computador, o mais simples e o instalador EntreNos-Setup, que nao
echo  precisa de Python: veja "Instalar em outra maquina" no README.md.
echo  Para rodar pelo codigo: instale o Python 3.14 e depois rode
echo      python -m pip install cryptography rapidfuzz
echo.
pause
exit /b 1
