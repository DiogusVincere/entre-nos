<#
.SYNOPSIS
    No seu computador PESSOAL: instala o que faltar (Python 3.14, bibliotecas e Inno Setup 6)
    e gera o instalador do Entre Nós em dist\EntreNos-Setup-<versão>.exe.

.DESCRIPTION
    Feito para o caso em que o instalador não pode ser gerado no computador da empresa (o
    antivírus de lá o coloca em quarentena). Rode este script na pasta do código, copiada para o
    PC pessoal (o pacote EntreNos-codigo-<versão>.zip, descompactado).

    1. Procura o Python 3.14 e o Inno Setup 6. O que faltar, instala pelo winget (só para o seu
       usuário, sem pedir administrador), depois de perguntar.
    2. Instala as bibliotecas: cryptography, rapidfuzz e pyinstaller.
    3. Chama o construir_instalador.ps1, que confere os arquivos, roda os testes, empacota e
       mostra o SHA-256 do instalador.

    Não mexe nos seus dados (%LOCALAPPDATA%\EntreNos) e não desliga nem contorna antivírus.

.EXAMPLE
    Dois cliques em "GERAR INSTALADOR NESTE PC.bat" (na mesma pasta).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\gerar_instalador_no_pc_pessoal.ps1 -SoConferir
    Só mostra o que encontrou e o que faltaria instalar; não instala nem gera nada.
#>
param(
    [switch]$SoConferir,
    [switch]$SemTestes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
Set-Location -LiteralPath $raiz

function Escrever([string]$Texto, [string]$Cor = 'Gray') { Write-Host $Texto -ForegroundColor $Cor }

function Perguntar([string]$Pergunta) {
    $resposta = Read-Host "$Pergunta [S/n]"
    return ($resposta -eq '' -or $resposta -match '^(s|sim|y|yes)$')
}

function Achar-Python {
    $candidatos = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'),
        (Join-Path $env:ProgramFiles 'Python314\python.exe')
    )
    foreach ($c in $candidatos) { if (Test-Path -LiteralPath $c) { return $c } }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $exe = (& py -3.14 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($exe -and (Test-Path -LiteralPath $exe.Trim())) { return $exe.Trim() }
        } catch { }
    }
    return $null
}

function Achar-Iscc {
    $candidatos = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    foreach ($c in $candidatos) { if ($c -and (Test-Path -LiteralPath $c)) { return $c } }
    return $null
}

function Instalar-Winget([string]$Id, [string]$Nome) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw ("Não encontrei o winget para instalar o $Nome. Instale-o à mão (python.org para o " +
            "Python 3.14; jrsoftware.org para o Inno Setup 6) e rode este script de novo.")
    }
    Escrever "Instalando $Nome pelo winget (só para o seu usuário)..." 'Cyan'
    & winget install --id $Id -e --source winget --scope user
    if ($LASTEXITCODE -ne 0) {
        throw "O winget não conseguiu instalar o $Nome (código $LASTEXITCODE). Instale à mão e rode de novo."
    }
}

Escrever ''
Escrever '  Entre Nós - gerar o instalador neste computador' 'White'
Escrever "  Pasta: $raiz"
Escrever ''

foreach ($arquivo in @('construir_instalador.ps1', 'iniciar.py', 'entrenos\servidor.py', 'web\app.js', 'instalador\EntreNos.iss')) {
    if (-not (Test-Path -LiteralPath (Join-Path $raiz $arquivo))) {
        throw ("Falta $arquivo nesta pasta. Descompacte o pacote do Entre Nós inteiro (o .zip) e rode " +
            "este script de dentro dele. Se o arquivo sumiu depois, veja a quarentena do antivírus.")
    }
}

$python = Achar-Python
$iscc = Achar-Iscc
Escrever ("  Python 3.14: " + $(if ($python) { $python } else { 'NÃO ENCONTRADO' }))
Escrever ("  Inno Setup 6: " + $(if ($iscc) { $iscc } else { 'NÃO ENCONTRADO' }))
Escrever ''

if ($SoConferir) {
    if (-not $python) { Escrever '  Faltaria instalar: Python 3.14 (winget Python.Python.3.14).' 'Yellow' }
    if (-not $iscc) { Escrever '  Faltaria instalar: Inno Setup 6 (winget JRSoftware.InnoSetup).' 'Yellow' }
    Escrever '  -SoConferir: nada foi instalado nem gerado.'
    return
}

if (-not $python) {
    if (-not (Perguntar 'O Python 3.14 não está instalado. Posso instalar agora?')) { throw 'Cancelado: sem o Python 3.14 não dá para gerar o instalador.' }
    Instalar-Winget 'Python.Python.3.14' 'Python 3.14'
    $python = Achar-Python
    if (-not $python) { throw 'O Python foi instalado, mas não o encontrei. Feche esta janela, abra de novo e rode o script outra vez.' }
}
if (-not $iscc) {
    if (-not (Perguntar 'O Inno Setup 6 (que monta o instalador) não está instalado. Posso instalar agora?')) { throw 'Cancelado: sem o Inno Setup não dá para gerar o instalador.' }
    Instalar-Winget 'JRSoftware.InnoSetup' 'Inno Setup 6'
    $iscc = Achar-Iscc
    if (-not $iscc) { throw 'O Inno Setup foi instalado, mas não o encontrei. Feche esta janela, abra de novo e rode o script outra vez.' }
}

Escrever 'Instalando/atualizando as bibliotecas: cryptography, rapidfuzz, pyinstaller...' 'Cyan'
& $python -m pip install --disable-pip-version-check --upgrade cryptography rapidfuzz pyinstaller
if ($LASTEXITCODE -ne 0) { throw "O pip não conseguiu instalar as bibliotecas (código $LASTEXITCODE)." }

$argumentos = @{ Python = $python; Iscc = $iscc }
if ($SemTestes) { $argumentos.SemTestes = $true }
& (Join-Path $raiz 'construir_instalador.ps1') @argumentos

$gerado = Get-ChildItem -LiteralPath (Join-Path $raiz 'dist') -Filter 'EntreNos-Setup-*.exe' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($gerado) {
    Escrever ''
    Escrever "  Pronto: $($gerado.FullName)" 'Green'
    Escrever '  Agora: dois cliques nele para instalar. Na primeira tela do Entre Nós, toque em'
    Escrever '  "Já tenho um backup", escolha o seu arquivo .cofre e digite o PIN dele.'
}
