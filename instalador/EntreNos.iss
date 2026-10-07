; Instalador do Entre Nós (Inno Setup 6.3 ou mais novo; DESIGN.md, seção 18.3).
;
; Não compile direto: use construir_instalador.ps1 na raiz, que roda os testes e o PyInstaller
; antes e passa para o ISCC:
;   /DAppVersao=0.2.0          versão lida de entrenos\config.py (sem ela, este arquivo lê sozinho)
;   /DPastaPrograma=<pasta>    a pasta do PyInstaller com o EntreNos.exe
;   /O<pasta>                  onde gravar o EntreNos-Setup-<versão>.exe
;
; - Instala só para o usuário atual, sem pedir administrador, em %LOCALAPPDATA%\Programs\Entre Nos.
; - Os dados NÃO ficam aqui: ficam em %LOCALAPPDATA%\EntreNos, e reinstalar por cima os mantém.
; - O desinstalador nunca apaga os dados em modo silencioso; no modo normal pergunta, com "Não"
;   como resposta padrão, e não apaga com o Entre Nós aberto (CheckForMutexes, seção 18.6).
; - Arquivo em UTF-8 (sem BOM, aceito desde o Inno Setup 6.3).

#define AppNome "Entre Nós"
#define AppExe "EntreNos.exe"
; O mesmo nome de servidor.MUTEX_ABERTO: o Entre Nós cria este mutex enquanto está aberto (o
; EntreNos.exe e também o aberto pelo iniciar.py / Abrir Entre Nos.bat, que usa a mesma pasta de dados).
#define AppMutexNome "EntreNos-Aberto-A5F79238-49BF-4FE8-8496-7CD3D084F9B4"

#ifndef AppVersao
  ; Sem /DAppVersao: lê VERSAO = "x.y.z" de entrenos\config.py.
  #define ArquivoConfig AddBackslash(SourcePath) + "..\entrenos\config.py"
  #define Leitor FileOpen(ArquivoConfig)
  #if !Leitor
    #error Não consegui abrir entrenos\config.py para ler a versão.
  #endif
  #define AppVersao ""
  #define Linha ""
  #define Contador 0
  ; Dentro do #sub, #define criaria variáveis locais: por isso a atribuição é com #expr.
  #sub LerLinhaDaVersao
    #expr Linha = FileRead(Leitor)
    #if Pos('VERSAO = "', Linha) == 1
      #expr AppVersao = Copy(Linha, 11, Pos('"', Copy(Linha, 11, 64)) - 1)
    #endif
  #endsub
  #for {Contador = 0; AppVersao == "" && !FileEof(Leitor); Contador++} LerLinhaDaVersao
  #expr FileClose(Leitor)
  #if AppVersao == ""
    #error Não achei VERSAO = "x.y.z" em entrenos\config.py.
  #endif
#endif

#ifndef PastaPrograma
  #define PastaPrograma AddBackslash(SourcePath) + "..\build\pyinstaller\EntreNos"
#endif

[Setup]
; AppId fixo: é por ele que o Windows sabe que uma versão nova atualiza a instalada. Nunca mude.
AppId={{57800451-942D-4B6C-ADDC-86A52EA33434}
AppName={#AppNome}
AppVersion={#AppVersao}
AppVerName={#AppNome} {#AppVersao}
AppPublisher={#AppNome}
VersionInfoVersion={#AppVersao}
VersionInfoProductName={#AppNome}
VersionInfoDescription=Instalador do {#AppNome}
; Só para o usuário atual: sem administrador, em %LOCALAPPDATA%\Programs.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Entre Nos
DefaultGroupName={#AppNome}
DisableProgramGroupPage=yes
AllowNoIcons=yes
; O EntreNos.exe é de 64 bits (Python 3.14) e precisa do Windows 10 ou mais novo.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=EntreNos-Setup-{#AppVersao}
SetupIconFile=entrenos.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppNome}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Programa aberto: o instalador e o desinstalador pedem para fechar antes (o Entre Nós cria o
; mutex). Sem o token da sessão não dá para pedir ao Entre Nós que feche sozinho.
AppMutex={#AppMutexNome}
; Segunda proteção: arquivos em uso aparecem na tela "Preparando para instalar".
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
SetupAppRunningError=O %1 está aberto neste computador.%n%nFeche o %1 antes de continuar: no navegador, Ajustes → Sair (ou encerre "EntreNos.exe" no Gerenciador de Tarefas). Seus dados não são afetados.%n%nDepois clique em OK para continuar ou em Cancelar para sair.
UninstallAppRunningError=O %1 está aberto neste computador.%n%nFeche o %1 antes de continuar: no navegador, Ajustes → Sair (ou encerre "EntreNos.exe" no Gerenciador de Tarefas).%n%nDepois clique em OK para continuar ou em Cancelar para sair.

[CustomMessages]
brazilianportuguese.AbrirAgora=Abrir o Entre Nós agora
brazilianportuguese.ComentarioAtalho=Abre o Entre Nós no navegador

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Atualização limpa: nada da versão anterior sobra misturado com a nova (os dados ficam em outra pasta).
; Só quando a pasta escolhida já tinha o Entre Nós: na pasta de outro programa feito com o
; PyInstaller, o _internal é dele (seção 18.6).
Type: filesandordirs; Name: "{app}\_internal"; Check: PastaJaEraDoEntreNos

[Files]
Source: "{#PastaPrograma}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppNome}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "{cm:ComentarioAtalho}"; Check: not WizardNoIcons
Name: "{autodesktop}\{#AppNome}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "{cm:ComentarioAtalho}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:AbrirAgora}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
{ Dados do usuário: o diário (entrenos.cofre), as cópias automáticas (backups) e o erros.log. }
function PastaDosDados: String;
begin
  Result := ExpandConstant('{localappdata}\EntreNos');
end;

{ [InstallDelete]: a pasta de instalação já tinha o Entre Nós (é uma atualização). }
function PastaJaEraDoEntreNos: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\{#AppExe}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Pasta: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  { Em modo silencioso os dados NUNCA são apagados (nem se alguém responder por linha de comando). }
  if UninstallSilent then
    Exit;
  Pasta := PastaDosDados;
  if not DirExists(Pasta) then
    Exit;
  if SuppressibleMsgBox(
       'O programa Entre Nós foi removido.' + #13#10#13#10 +
       'Quer apagar também o seu diário?' + #13#10#13#10 +
       'Ele fica em ' + Pasta + ': o diário (entrenos.cofre) e as cópias de segurança ' +
       'automáticas (pasta backups), tudo criptografado.' + #13#10#13#10 +
       'Sim: apaga tudo para sempre. Não dá para desfazer.' + #13#10 +
       'Não: o diário continua aqui e volta a abrir se você instalar o Entre Nós de novo.' + #13#10#13#10 +
       'Se tiver dúvida, escolha Não.',
       mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) <> IDYES then
    Exit;
  { Um Entre Nós ainda aberto (pelo atalho ou pelo Abrir Entre Nos.bat, que também cria o mutex)
    tem o diário na memória: apagar a pasta agora não adiantaria. Com ele aberto, nada é apagado. }
  if CheckForMutexes('{#AppMutexNome}') then
  begin
    MsgBox('O Entre Nós ainda está aberto neste computador, então o diário NÃO foi apagado.' + #13#10#13#10 +
      'Feche o Entre Nós (no navegador, Ajustes → Sair) e, se ainda quiser apagar, apague a pasta ' +
      Pasta + ' pelo Explorador de Arquivos.', mbError, MB_OK);
    Exit;
  end;
  if not DelTree(Pasta, True, True, True) then
    MsgBox('Não consegui apagar tudo em ' + Pasta + '. Se quiser, apague essa pasta pelo Explorador de Arquivos.',
      mbError, MB_OK);
end;
