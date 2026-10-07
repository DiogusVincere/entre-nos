@echo off
rem Use no seu computador PESSOAL: instala o que faltar (perguntando antes) e gera o
rem instalador do Entre Nos em dist\. Veja "Instalar em outra maquina" no README.md.
setlocal
cd /d "%~dp0"
title Entre Nos - gerar o instalador
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gerar_instalador_no_pc_pessoal.ps1" %*
echo.
pause
