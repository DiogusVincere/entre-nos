<#
.SYNOPSIS
    Constrói o instalador do Entre Nós: dist\EntreNos-Setup-<versão>.exe.

.DESCRIPTION
    0. Confere se os arquivos do programa estão na pasta. Faltando algum (o antivírus pode tê-lo
       colocado em quarentena, como aconteceu com o iniciar.py em 30/09/2026), para e explica.
    1. Roda os testes (python -m unittest discover -s testes -t .). Se algum falhar, para.
    2. Gera o ícone (instalador\entrenos.ico) se ele não existir (precisa do Pillow).
    3. PyInstaller com instalador\entrenos.spec: build\pyinstaller\EntreNos\EntreNos.exe
       (trabalho em build\trabalho).
    4. Inno Setup (ISCC) com instalador\EntreNos.iss: dist\EntreNos-Setup-<versão>.exe.
    5. Espera alguns segundos e confere se o instalador continua lá (o antivírus já o removeu
       logo depois de gerado). Só então diz "Pronto", com o SHA-256.
    A versão vem de VERSAO em entrenos\config.py.

    Nunca tenta contornar o antivírus: se ele bloquear, o script avisa e para.

    Não instala nada e não mexe nos seus dados (%LOCALAPPDATA%\EntreNos).
    Precisa de: Python 3.14 com cryptography, rapidfuzz e PyInstaller; Inno Setup 6.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\construir_instalador.ps1

.EXAMPLE
    .\construir_instalador.ps1 -SemTestes -Python C:\caminho\python.exe
#>
param(
    [string]$Python = '',
    [string]$Iscc = '',
    [switch]$SemTestes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
Set-Location -LiteralPath $raiz

# O que fazer quando um arquivo some (seção "Se o antivírus bloquear" do README).
function Texto-Antivirus([string]$Arquivo) {
    return ("Se você não apagou $Arquivo, veja a quarentena do antivírus. Não tente contornar: " +
        "não desligue o antivírus, peça à TI para restaurar o arquivo e liberar a pasta $raiz " +
        "(README.md, seção 'Se o antivírus bloquear').")
}

# Arquivos sem os quais não há programa nem instalador. O ícone é gerado se faltar.
$obrigatorios = @(
    'iniciar.py',
    'entrenos\__init__.py', 'entrenos\config.py', 'entrenos\servidor.py', 'entrenos\nucleo.py',
    'entrenos\cofre.py', 'entrenos\banco.py', 'entrenos\motor.py',
    'web\index.html', 'web\app.js', 'web\estilo.css',
    'instalador\entrenos.spec', 'instalador\EntreNos.iss', 'instalador\gerar_icone.py'
)
$faltam = @($obrigatorios | Where-Object { -not (Test-Path -LiteralPath (Join-Path $raiz $_)) })
if ($faltam.Count -gt 0) {
    throw ("Falta(m) na pasta $raiz : " + ($faltam -join ', ') + '. ' + (Texto-Antivirus ($faltam -join ', ')))
}

function Achar-Python {
    if ($Python) { return $Python }
    $padrao = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
    if (Test-Path -LiteralPath $padrao) { return $padrao }
    # O "python" da Microsoft Store (WindowsApps) é só um atalho para a loja: não serve.
    $achado = Get-Command python.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1
    if ($achado) { return $achado.Source }
    throw 'Não achei o Python. Passe o caminho com -Python C:\...\python.exe'
}

function Achar-Iscc {
    if ($Iscc) { return $Iscc }
    $candidatos = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    foreach ($c in $candidatos) { if ($c -and (Test-Path -LiteralPath $c)) { return $c } }
    throw 'Não achei o Inno Setup 6 (ISCC.exe). Instale-o ou passe o caminho com -Iscc.'
}

# Programa externo: a saída vai para a tela e o código de saída diferente de 0 interrompe.
# (No PowerShell 5.1, texto no stderr de programa externo não pode virar erro que para o script.)
function Rodar([string]$Titulo, [string]$Programa, [string[]]$Argumentos) {
    Write-Host ''
    Write-Host "== $Titulo" -ForegroundColor Cyan
    $antes = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Programa @Argumentos | Out-Host
        $codigo = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $antes
    }
    if ($codigo -ne 0) { throw "$Titulo falhou (código de saída $codigo)." }
}

$py = Achar-Python
$iscc = Achar-Iscc

$configPy = Get-Content -LiteralPath (Join-Path $raiz 'entrenos\config.py') -Raw -Encoding UTF8
$achou = [regex]::Match($configPy, '(?m)^VERSAO\s*=\s*"(\d+\.\d+\.\d+)"')
if (-not $achou.Success) { throw 'Não achei VERSAO = "x.y.z" em entrenos\config.py.' }
$versao = $achou.Groups[1].Value
Write-Host "Entre Nós $versao" -ForegroundColor Green
Write-Host "Python: $py"
Write-Host "Inno Setup: $iscc"

if ($SemTestes) {
    Write-Host ''
    Write-Host '== Testes pulados (-SemTestes)' -ForegroundColor Yellow
} else {
    Rodar 'Testes' $py @('-m', 'unittest', 'discover', '-s', 'testes', '-t', '.')
}

$icone = Join-Path $raiz 'instalador\entrenos.ico'
if (-not (Test-Path -LiteralPath $icone)) {
    Rodar 'Ícone' $py @((Join-Path $raiz 'instalador\gerar_icone.py'))
}

$pastaPyInstaller = Join-Path $raiz 'build\pyinstaller'
Rodar 'PyInstaller' $py @('-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
    '--distpath', $pastaPyInstaller, '--workpath', (Join-Path $raiz 'build\trabalho'),
    (Join-Path $raiz 'instalador\entrenos.spec'))
$pastaPrograma = Join-Path $pastaPyInstaller 'EntreNos'
$exe = Join-Path $pastaPrograma 'EntreNos.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw ("O PyInstaller terminou sem erro, mas $exe não está lá: o antivírus pode tê-lo removido. " +
        (Texto-Antivirus $exe))
}
if (-not (Test-Path -LiteralPath (Join-Path $pastaPrograma '_internal\web\index.html'))) {
    throw ('O pacote saiu sem a pasta web (_internal\web\index.html). ' +
        (Texto-Antivirus (Join-Path $pastaPrograma '_internal\web\index.html')))
}

$saida = Join-Path $raiz 'dist'
New-Item -ItemType Directory -Force -Path $saida | Out-Null
Rodar 'Inno Setup' $iscc @('/Qp', "/DAppVersao=$versao", "/DPastaPrograma=$pastaPrograma", "/O$saida",
    (Join-Path $raiz 'instalador\EntreNos.iss'))

$instalador = Join-Path $saida "EntreNos-Setup-$versao.exe"
if (-not (Test-Path -LiteralPath $instalador)) {
    throw ("O Inno Setup terminou sem erro, mas $instalador não está lá: o antivírus pode tê-lo removido. " +
        (Texto-Antivirus $instalador))
}
$arquivo = Get-Item -LiteralPath $instalador
$programa = (Get-ChildItem -LiteralPath $pastaPrograma -Recurse -File | Measure-Object -Property Length -Sum).Sum
$hash = (Get-FileHash -LiteralPath $instalador -Algorithm SHA256).Hash

# Em 30/09/2026 o antivírus removeu o instalador segundos depois de gerado, quando o script já
# tinha dito "Pronto". Espera um pouco e confere de novo antes de dizer que deu certo.
Write-Host ''
Write-Host '== Conferindo se o antivírus deixou o instalador no lugar (5 s)' -ForegroundColor Cyan
Start-Sleep -Seconds 5
$continua = Test-Path -LiteralPath $instalador
if ($continua) {
    $continua = (Get-FileHash -LiteralPath $instalador -Algorithm SHA256).Hash -eq $hash
}
if (-not $continua) {
    Write-Host ''
    Write-Warning ("O instalador foi gerado, mas sumiu (ou mudou) segundos depois: o antivírus provavelmente o " +
        "removeu. " + (Texto-Antivirus $instalador))
    Write-Host ('SHA-256 do instalador gerado (para a TI conferir): {0}' -f $hash)
    exit 1
}

Write-Host ''
Write-Host 'Pronto.' -ForegroundColor Green
Write-Host ('Instalador: {0}' -f $arquivo.FullName)
Write-Host ('Tamanho:    {0:N1} MB ({1:N0} bytes); instalado ocupa {2:N1} MB' -f ($arquivo.Length / 1MB), $arquivo.Length, ($programa / 1MB))
Write-Host ('SHA-256:    {0}' -f $hash)
Write-Host ''
Write-Host 'Próximos passos:' -ForegroundColor Cyan
Write-Host '  1. Leve o instalador por pendrive ou pela nuvem (e-mail bloqueia .exe).'
Write-Host '  2. No outro computador, confira o SHA-256 antes de abrir:'
Write-Host ('       Get-FileHash .\{0} -Algorithm SHA256' -f $arquivo.Name)
Write-Host '     O resultado precisa ser igual ao SHA-256 acima.'
Write-Host '  3. Se o Windows mostrar "O Windows protegeu o computador": Mais informações > Executar assim mesmo.'
Write-Host '  4. Na primeira tela do Entre Nós lá, use "Já tenho um backup" com o .cofre feito aqui.'
