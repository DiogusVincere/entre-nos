# Entre Nós

Diário do relacionamento: você conta o que ela te disse, o Entre Nós organiza (gostos,
pessoas, datas, preocupações) e te lembra do que importa. Roda **só neste computador**,
abre no navegador e guarda tudo **criptografado**. Nada é salvo sem você revisar antes.

## Como abrir

- Dois cliques em **`Abrir Entre Nos.bat`**. O navegador abre em `http://127.0.0.1:8787`
  (se a porta estiver ocupada por outro programa, usa uma de 8788 a 8799). Se o Entre Nós
  já estiver aberto, o atalho só mostra a página de novo.
- Pelo terminal: `python iniciar.py` (ou `python -m entrenos`), com as opções
  `--sem-navegador`, `--porta N` e `--dados PASTA`.
- Instalado pelo instalador (veja "Instalar em outra máquina"): atalho **Entre Nós** no menu
  Iniciar. O `EntreNos.exe` aceita as mesmas opções.
- Na primeira vez você cria o PIN (pelo menos 6 caracteres; uma frase curta é melhor).
  PINs fáceis de adivinhar (123456, 111111, "senha123"...) não são aceitos, e um PIN só de
  números precisa ter pelo menos 10 dígitos. Não deixe o navegador salvar o PIN.
  **Não existe "esqueci o PIN"**: sem ele ninguém abre os dados, nem você.
- Para fechar: **Ajustes → Sair** (salva, bloqueia e encerra). No terminal, Ctrl+C.

Precisa do Python 3.14 com a biblioteca `cryptography` (o `rapidfuzz` não é mais usado).

**Se nada acontecer** ao abrir o `.bat`:
- Ele mesmo explica o que falta numa janela preta (e espera uma tecla): um arquivo do programa
  que sumiu (quase sempre o antivírus, veja "Se o antivírus bloquear"), o Python ou a biblioteca
  `cryptography`.
- Se a janela não disser nada de útil, abra um terminal nesta pasta e rode `python iniciar.py`:
  o erro aparece ali.
- Seu diário não é afetado em nenhum desses casos: ele fica em `%LOCALAPPDATA%\EntreNos`.
- No programa instalado, se ele parar de abrir ou a página vier em branco ou com "Faltam
  arquivos do Entre Nós", rode o instalador de novo por cima: os dados ficam.

## Onde ficam os dados

Em `%LOCALAPPDATA%\EntreNos` (nunca na pasta do código):

| Arquivo | O que é |
|---|---|
| `entrenos.cofre` | tudo o que você registrou, num arquivo só, criptografado com o PIN (AES-256-GCM, chave pelo scrypt) |
| `backups\` | cópias automáticas das versões anteriores (até 5, uma por dia), também criptografadas, e a cópia do diário que estava aqui antes de cada restauração (`entrenos-antes-da-restauracao-…`) |
| `backups\entrenos-anterior-…` | cópias de um diário anterior: o `entrenos.cofre` sumiu (antivírus, apagado) e outro diário começou aqui, ou a cópia é de outro PIN. Nunca são apagadas sozinhas; abrem com o PIN daquele diário. Apague quando não precisar mais |
| `backups\entrenos-danificado-…` | o arquivo do diário que estava danificado e foi trocado por um backup (nada é apagado) |
| `pin_espera.json` | só aparece depois de um PIN errado: quantas tentativas erradas seguidas e até quando esperar (nada do PIN). Sai quando você acerta o PIN |
| `erros.log` | só aparece se der erro; guarda o tipo do erro e o ponto do código, nunca o que você escreveu |

## Privacidade

- O servidor escuta só em `127.0.0.1`: outro computador da rede não enxerga nada.
  Sem CDN, sem fonte externa, sem telemetria. A internet só é usada se você configurar uma IA
  na internet (e só quando tocar num botão ✨) ou ligar a sincronização com o Google Drive.
- A página só conversa com o próprio Entre Nós (proteções contra outras páginas abertas no
  navegador) e cada sessão tem uma chave própria.
- Bloqueia sozinho depois de 10 minutos sem uso (dá para mudar em Ajustes). Ao bloquear, a
  chave sai da memória. Depois de um PIN errado, o próximo só é conferido depois de uma espera
  cada vez maior (1 s, 2 s, 4 s... até 30 s); o botão mostra quanto falta. Fechar e abrir o
  Entre Nós não zera essa espera.
  Restos do que foi digitado podem ficar na memória do computador até o programa fechar:
  quando terminar, use **Ajustes → Sair**.
- Apagar um registro apaga do banco, da busca, do arquivo e das cópias automáticas
  (`backups\`). Cópias que você guardou em outro lugar (um pendrive) continuam com ele.
- O banco é montado só na memória: nem as cópias temporárias do SQLite vão para o disco.
- **Exportar** gera um JSON **sem criptografia**: guarde com cuidado e apague quando não
  precisar mais.
- **Sincronização** (opcional, desligada até você ligar): os arquivos vão para o seu Google
  Drive embaralhados com a senha de sincronização. Nenhum código do Google roda dentro do Entre
  Nós: o login é uma janela do Google que volta para cá, e o acesso fica só na memória da tela
  (esquecido quando o Entre Nós se bloqueia). Veja "Sincronizar entre aparelhos".

## Cópia de segurança, restaurar e importar

Tudo em **Ajustes → Levar ou trazer dados**:

- **Fazer backup**: escolha uma pasta (um pendrive, por exemplo). Sai um arquivo
  `entrenos-backup-AAAA-MM-DD.cofre`, criptografado, que só abre com o PIN que valia no dia da
  cópia.
- **Restaurar um backup**: escolha o `.cofre`, digite o PIN daquele backup e o seu PIN de agora.
  O Entre Nós confere que o backup abre e está inteiro **antes** de trocar qualquer coisa; guarda
  o diário de agora em `backups\entrenos-antes-da-restauracao-AAAA-MM-DD-HHMMSS.cofre` (abre com
  o PIN de antes; inclui o que só estava na memória) e só então troca. Depois disso o PIN, os
  ajustes e a IA passam a ser os do backup. A tela mostra o caminho completo dessa cópia, com um
  botão para copiar. Num computador sem diário, a primeira tela tem **Já tenho um backup**, que
  faz o mesmo sem pedir PIN de agora. Se o arquivo do diário estiver danificado (cortado), o
  Entre Nós diz isso em vez de "PIN incorreto" e deixa restaurar um backup no lugar, guardando o
  arquivo danificado em `backups\entrenos-danificado-…`.
- **Exportar (JSON)**: tudo o que você registrou num arquivo legível, **sem criptografia**.
- **Importar JSON (juntar)**: junta ao diário de agora os registros de um JSON exportado (deste
  ou de outro computador). Primeiro mostra o que vai entrar, com os nomes das pessoas novas, das
  que já existiam e das que ganham o que faltava (aniversário, apelido...). Registro que já
  existe (mesma data e mesmo texto, sem contar maiúsculas, acentos, espaços e pontuação) não se
  repete: fica a versão deste computador, mas se ele estava 🔒 no arquivo passa a ficar 🔒 aqui
  também (nada perde o 🔒 ao importar). Pessoas com o mesmo nome, apelido ou relação são ligadas
  às que você já tem; mesmo nome com outra relação ("amiga" e "melhor amiga") entra como pessoa
  nova, e a prévia avisa. Ajustes, PIN, IA e avisos dispensados do arquivo não entram. Não existe
  importar substituindo: para trocar o diário inteiro, use o backup.

## Instalar em outra máquina

O jeito mais simples é o **instalador** (`EntreNos-Setup-<versão>.exe`), que leva o Entre Nós
para um computador **sem Python**. No computador da empresa o antivírus coloca o instalador em
quarentena (veja "Se o antivírus bloquear"), então ele é **gerado fora daqui**, por um destes
caminhos:

### 1. Gerar o instalador

**Caminho A: no próprio PC pessoal (pendrive).**
1. Aqui, leve para o pendrive o pacote `dist\EntreNos-codigo-<versão>.zip` (só código, sem
   nenhum dado seu) e o seu backup (**Ajustes → Levar ou trazer dados → Fazer backup**).
2. No PC pessoal, descompacte o `.zip` numa pasta (por exemplo `Documentos\EntreNos`).
3. Dois cliques em **`GERAR INSTALADOR NESTE PC.bat`**. Ele procura o Python 3.14 e o Inno
   Setup 6; o que faltar, **pergunta** e instala pelo winget só para o seu usuário (sem
   administrador). Depois instala as bibliotecas, roda os testes e gera
   `dist\EntreNos-Setup-<versão>.exe`, mostrando o **SHA-256** (a "impressão digital" do arquivo).
   Leva uns 5 minutos.

**Caminho B: pelo GitHub (nuvem).** Com o código no seu repositório privado: no site, **Actions →
Instalador → Run workflow**. Uns 10 minutos depois, o instalador aparece em **Artifacts**
("EntreNos-Setup") para baixar no PC pessoal. Nada é gerado neste computador.

**Caminho C: aqui**, só se a TI liberar a pasta no antivírus:

```
powershell -ExecutionPolicy Bypass -File .\construir_instalador.ps1
```

Em todos os caminhos o script confere se os arquivos do programa estão na pasta, roda os testes
(para se algum falhar), empacota e grava `dist\EntreNos-Setup-<versão>.exe`. Se o instalador
sumir logo depois de gerado, ele avisa: é o antivírus.

### 2. Levar o arquivo

Por **pendrive** ou pela **nuvem** (OneDrive, Google Drive). **E-mail não serve**: Gmail e
Outlook bloqueiam anexo `.exe`, inclusive dentro de `.zip`. Se o instalador foi gerado em outro
computador, confira que é o mesmo arquivo, num PowerShell na pasta dele:

```
Get-FileHash .\EntreNos-Setup-0.3.0.exe -Algorithm SHA256
```

O resultado precisa ser igual ao SHA-256 que o script mostrou ao gerar.

O `dist\EntreNos-Setup-0.2.0.exe` que a TI devolveu da quarentena em 01/10/2026 funciona, mas é
de antes da revisão de 01/10 e não tem as novidades de 07-08/10 (recursos novos, celular,
sincronização). Gere o 0.3.0.

### 3. Instalar no outro computador (Windows 10 ou 11, 64 bits)

1. Dois cliques em `EntreNos-Setup-<versão>.exe`. **Não pede administrador**: instala só para o
   seu usuário, em `%LOCALAPPDATA%\Programs\Entre Nos`.
2. **Aviso do Windows (SmartScreen).** O instalador não tem assinatura digital paga, então o
   Windows pode mostrar a tela azul "O Windows protegeu o computador", só com o botão "Não
   executar". Clique no texto **Mais informações**: aparece o botão **Executar assim mesmo**;
   clique nele. Faça isso só com o instalador que você mesmo gerou e cujo SHA-256 conferiu.
3. Siga o instalador: atalho no menu Iniciar e, se você marcar, na área de trabalho. No fim,
   deixe marcado "Abrir o Entre Nós agora".
4. O Entre Nós abre no navegador padrão, como aqui. Abrir o atalho **Entre Nós** com ele já
   aberto só mostra a página de novo.

### 4. Primeiro uso lá: trazer o seu diário

**Cada computador tem o seu diário.** Os dados ficam em `%LOCALAPPDATA%\EntreNos` de cada
máquina e nada é sincronizado (nada sai do computador). Para levar o diário:
1. aqui: **Ajustes → Levar ou trazer dados → Fazer backup** (um arquivo `.cofre`, criptografado);
2. leve o arquivo junto com o instalador (pendrive ou nuvem);
3. lá, na primeira tela, toque em **📦 Já tenho um backup** (não em "Criar meu diário"), escolha
   o `.cofre` e digite o PIN que valia quando o backup foi feito. Se já usa o Entre Nós lá:
   **Ajustes → Levar ou trazer dados → Restaurar um backup**.

Depois disso o PIN do Entre Nós lá passa a ser o desse backup. Para juntar registros dos dois
computadores sem trocar o diário inteiro, use **Exportar (JSON)** num e **Importar JSON** no
outro.

### Sem instalador: rodar pelo código

Descompacte o `EntreNos-codigo-<versão>.zip`, instale o Python 3.14 e
`python -m pip install cryptography`, e abra pelo `Abrir Entre Nos.bat`.

Num computador da empresa, qualquer um dos caminhos pode esbarrar no antivírus: veja a seção
abaixo antes.

### Atualizar e desinstalar

**Atualizar:** rode o instalador da versão nova por cima. O diário continua, porque ele não fica
na pasta do programa. Se o Entre Nós estiver aberto, o instalador pede para fechar antes
(**Ajustes → Sair**).

**Desinstalar sem perder dados:** Configurações do Windows → Aplicativos → Entre Nós →
Desinstalar. No fim ele pergunta se você quer apagar também o diário; a resposta já marcada é
**Não**, e com ela o diário fica em `%LOCALAPPDATA%\EntreNos`, pronto para uma próxima
instalação. Se o Entre Nós ainda estiver aberto (pelo atalho ou pelo `.bat`), ele não apaga o
diário mesmo com "Sim" e avisa. Desinstalando em modo silencioso (`unins000.exe /VERYSILENT`),
os dados nunca são apagados. Antes de apagar de vez, faça um backup.

## Se o antivírus bloquear

**O que aconteceu em 30/09/2026.** Durante os testes do instalador, por volta das 20h23, o
antivírus da empresa (Bitdefender, gerenciado pela TI) colocou em **quarentena** o `iniciar.py`
desta pasta e o `dist\EntreNos-Setup-0.2.0.exe`, e passou a bloquear a criação do `iniciar.py`.
Programas novos, sem assinatura digital, que abrem um servidor local e geram executáveis
(PyInstaller) costumam disparar esse tipo de proteção, mesmo sem nada de errado neles.

**Sinais:** o `Abrir Entre Nos.bat` não abre nada (agora ele avisa qual arquivo falta); some o
`iniciar.py`, o `EntreNos.exe` ou o instalador; aparece um aviso de ameaça; o atalho diz que o
item foi movido ou excluído; a página abre em branco ou com "Faltam arquivos do Entre Nós".

**Seus dados ficam a salvo.** O diário fica em `%LOCALAPPDATA%\EntreNos`, fora da pasta do
programa, e continua criptografado. Se o próprio `entrenos.cofre` for para a quarentena, as
cópias automáticas continuam em `backups\`, e o Entre Nós avisa na primeira tela (use **Já tenho
um backup** com a cópia mais recente, ou peça a restauração do arquivo).

**Como verificar a quarentena:**
- **Computador da empresa (Bitdefender):** clique no ícone do Bitdefender perto do relógio e
  veja as notificações ou eventos. A quarentena do Bitdefender da empresa é gerenciada pela TI:
  quem restaura e libera é ela.
- **Computador pessoal (Windows Defender):** Segurança do Windows → Proteção contra vírus e
  ameaças → Histórico de proteção. Restaure só se for mesmo o arquivo do Entre Nós (no caso do
  instalador, confira antes o SHA-256 com `Get-FileHash`).

**Não desligue o antivírus**, nem crie exceções por conta própria num computador da empresa, nem
renomeie ou copie arquivos para "passar" pelo bloqueio. Peça a liberação. Texto curto para
mandar à TI:

> Olá! O Bitdefender colocou em quarentena, em 30/09/2026 por volta das 20h23, dois arquivos de
> um programa pessoal que eu mesmo escrevi (o Entre Nós, um diário que roda só neste computador,
> em 127.0.0.1, sem acesso à internet): `C:\Users\Usuario\EntreNos\iniciar.py` e
> `C:\Users\Usuario\EntreNos\dist\EntreNos-Setup-0.2.0.exe`. Vocês podem restaurá-los da
> quarentena e liberar a pasta `C:\Users\Usuario\EntreNos` e o programa instalado em
> `%LOCALAPPDATA%\Programs\Entre Nos`? Se precisarem conferir o instalador, o SHA-256 dele é
> o que o script de construção mostra no fim (posso mandar). Obrigado!

Se a TI preferir não liberar, use o Entre Nós num computador pessoal (instalador ou pelo código)
e leve os dados por backup.

## IA (opcional)

Quem organiza os textos é sempre um conjunto de regras que roda aqui mesmo: não entende tudo,
mas nunca inventa e nunca manda nada para fora.

Uma IA pode ser configurada em **Ajustes → IA**, protegida por um segundo PIN (o PIN da IA):
- **Neste computador:** Ollama ou LM Studio (sem chave; nada sai daqui).
- **Na internet:** Groq, OpenRouter, Google Gemini, OpenAI, Mistral ou outro serviço compatível
  com o formato da OpenAI. Basta colar a chave: o Entre Nós reconhece o serviço pelo começo dela,
  preenche o endereço, e "Buscar modelos" lista os modelos para escolher. "Testar conexão"
  manda uma frase inventada, nunca seus registros. O Claude (Anthropic) ainda não está disponível.
- Dá para ter uma IA **principal** e uma **de reserva**: se a principal não responder, a reserva
  tenta; se nenhuma responder, ficam as regras.

A IA **só roda quando você toca num botão ✨**: "Analisar a fundo com IA" na revisão,
"Perguntar à IA" na busca e "Pensar numa sugestão" na página Hoje. Registros marcados
**🔒 Não enviar para a IA** nunca vão para ela. Mesmo com IA, nada é salvo sem revisão, as
datas são calculadas no Python e cada informação cita o trecho de onde veio. A chave fica
dentro do arquivo criptografado e a tela mostra só os 4 últimos caracteres.

O PIN da IA fecha a área de ajustes da IA para quem encontrar o Entre Nós aberto; ele não é uma
segunda senha do arquivo: quem souber o PIN principal consegue ler a chave. Chaves que começam
só com `sk-` são de vários serviços (OpenAI, DeepSeek, Moonshot...): nesse caso escolha o serviço
na lista.

## No celular

O mesmo Entre Nós abre no iPhone, no Android e em qualquer navegador como **app instalável**
(PWA), sem servidor: o próprio código Python roda dentro do navegador (Pyodide) e o diário fica
**no aparelho**, criptografado, no mesmo formato `.cofre` do computador. O site guarda só o código
do app; nada do que você escreve vai para ele.

**Montar o site** (na raiz do projeto):

```
python construir_web.py
```

Gera a pasta `site/` (não vai para o Git): confere o Pyodide guardado em `web/pyodide/314.0.7/`
(SHA-256 de cada arquivo e das rodas) e as licenças, empacota o código Python em
`web/entrenos-py.zip` (sem o `servidor.py`), grava o `sw.js` com a versão e o `_headers` com a
política de segurança (a do computador, mais a permissão de rodar WebAssembly para o Python), uma
vez só, em todos os endereços do site. O `404.html` faz um endereço que não existe responder
"página não encontrada", em vez do app. Não gera `.exe`.

**Publicar** (Cloudflare Pages, grátis, HTTPS): você cria a conta e liga o repositório privado do
GitHub (os logins são seus). Comando de build `python construir_web.py`, pasta de saída `site`.
Cada push publica a versão nova; quem estiver com o app aberto vê "Nova versão do Entre Nós
disponível" e escolhe quando recarregar.

**Usar:**
- Na primeira vez o app inteiro (uns 16 MB) vem para o aparelho: prefira o Wi-Fi; pode levar até
  um minuto. Depois abre rápido, mesmo sem internet. Dá para ir digitando o PIN enquanto ele
  termina de abrir.
- **iPhone:** abra o endereço no Safari. Antes de baixar o app, ele mostra os passos para instalar:
  toque em Compartilhar (no iOS 26, dentro do menu ⋯) → **Adicionar à Tela de Início** (deixe
  "Abrir como App Web" ligado, se aparecer) e crie o diário **no app instalado**. O app instalado
  guarda os dados separado do Safari: um diário criado no Safari não aparece no app.
- **Android e computador (Chrome, Edge):** Ajustes → Este aparelho → **Instalar o app**.
- Só uma janela por vez é dona do diário: numa segunda aba aparece "já está aberto em outra
  janela", e ela abre sozinha quando a primeira fechar.
- **Cópias:** Ajustes → Levar ou trazer dados → **Baixar uma cópia** (compartilhar para o
  Arquivos, o Drive, o e-mail, ou baixar: costuma ficar em Downloads). É o mesmo `.cofre` do
  computador: um backup do PC restaura no celular e vice-versa, com o PIN daquele backup. A cópia
  não leva a senha de sincronização (restaurada, ela é pedida de novo). Antes de restaurar no
  celular, baixe uma cópia de agora (o botão está ali mesmo): a cópia interna que o app guarda
  ainda não abre pela tela.
- **Proteção contra limpeza:** Ajustes → Este aparelho mostra o espaço usado e pede ao navegador
  para não apagar os dados do site. Limpar os dados do site apaga o diário deste aparelho: faça
  cópias de vez em quando.
- **Avisos** (Android e computador, com o app instalado no Chrome ou no Edge): Ajustes → Este
  aparelho → Avisos. Uma vez por dia, se houver lembretes, aparece "Você tem 2 lembretes hoje no
  Entre Nós", sem dizer quais. Para isso fica fora do diário cifrado só a lista de **dias** com
  lembretes e quantos; desligar os avisos (ou o aparelho tirar a permissão) apaga essa lista. O
  Entre Nós só pede a permissão de notificação depois de conferir que o aparelho consegue avisar.
  No iPhone, ainda não.
- A **IA** não aparece no celular por enquanto.

## Sincronizar entre aparelhos

Opcional e desligada até você ligar em **Ajustes → ☁️ Sincronização**. Com ela ligada, cada
aparelho (o computador, o celular, um tablet) guarda no **seu** Google Drive, numa pasta escondida
do app, um arquivo **embaralhado com a senha de sincronização**, e junta o que os outros
escreveram. O Google só vê arquivos sem sentido; a senha e a chave nunca saem dos aparelhos.

**O que acontece:**
- Registros novos de cada aparelho aparecem nos outros, sem duplicar. O 🔒, os dados do ciclo, da
  área Eu e dos outros recursos vão junto (sempre embaralhados).
- O que você apaga num aparelho some dos outros e **não volta** (fica só uma "impressão" do que
  foi apagado, sem conteúdo nenhum).
- A mesma informação mudou em dois aparelhos? Vale a mudança mais recente. Mudou quase ao mesmo
  tempo (menos de 2 minutos de diferença)? A tela pergunta qual versão fica. O 🔒 não pergunta: na
  dúvida, fica 🔒.
- Sincroniza sozinho **enquanto o acesso ao Google valer** (cerca de uma hora, limite do Google
  para apps sem servidor): ao abrir, uns 30 segundos depois de salvar e quando você toca em
  **Sincronizar agora**. Sem internet, fica na fila e vai quando a conexão voltar.
- Quando o Entre Nós se bloqueia ou é fechado, o acesso ao Google é esquecido. Aí um selo
  **☁️ Sincronizar** aparece no alto da tela ("Não sincronizado desde 14:20", "Salvo só neste
  aparelho"): um toque abre a janela do Google e sincroniza. O selo também avisa quando há uma
  pergunta da sincronização para responder.
- Cada aparelho tem o seu diário e o seu PIN; a **senha de sincronização** é outra, a mesma em
  todos os aparelhos. Ela precisa ser forte (pelo menos 14 caracteres e 3 palavras diferentes; a
  tela sugere uma frase de 4 palavras ao acaso), porque quem tiver os arquivos do Drive pode testar
  senhas fora do app. Pode ser igual ao PIN, mas a tela avisa antes (quem descobrir uma sabe a
  outra). Não existe recuperação dela.

**O que você precisa (uma vez só, uns 10 minutos): o "ID do app" do Google.** Ele diz ao Google que
é o seu Entre Nós pedindo acesso; não é segredo, é grátis e sozinho não dá acesso a nada.

1. Entre em <https://console.cloud.google.com> com a sua conta do Google e crie um projeto (por
   exemplo, "Entre Nós").
2. Em **APIs e serviços → Biblioteca**, procure **Google Drive API** e toque em **Ativar**.
3. Em **Google Auth Platform** (ou "Tela de permissão OAuth"): tipo de usuário **Externo**, nome do
   app "Entre Nós" e o seu e-mail. Em **Público** (ou "Usuários de teste"), adicione o **seu
   próprio e-mail**. O app fica "em teste": só os e-mails da lista conseguem entrar. Nesse modo o
   Google pode pedir a autorização de novo a cada 7 dias; se incomodar, toque em **Publicar app**
   (o Entre Nós só pede a pasta escondida do app, e em geral o Google não exige verificação para
   isso).
4. Em **Clientes** (ou **Credenciais → Criar credenciais**), crie um **ID do cliente OAuth** do tipo
   **Aplicativo da Web**.
5. Em **Origens JavaScript autorizadas**, adicione, cada um numa linha e sem barra no fim:
   - o endereço do site do celular (por exemplo, `https://entre-nos.pages.dev`);
   - `http://localhost:8787` (o computador; se o seu Entre Nós abrir em outra porta, essa porta).
   O Google não aceita `127.0.0.1`: para sincronizar no computador, abra o Entre Nós por
   `http://localhost:8787` (a tela de Ajustes mostra o link).
6. Em **URIs de redirecionamento autorizados** (para onde o Google volta depois do login), adicione
   os mesmos endereços seguidos de `/oauth.html`: `https://entre-nos.pages.dev/oauth.html` e
   `http://localhost:8787/oauth.html`. Salve.
7. Copie o **ID do cliente** (termina em `.apps.googleusercontent.com`) e cole em Ajustes → ☁️
   Sincronização, **em cada aparelho**.

**Ligar:** cole o ID do app → **Conectar ao Google** (na primeira vez o Google avisa que o app "não
foi verificado": é o seu próprio app, toque em Continuar; a permissão pedida é só guardar os dados
do app numa pasta escondida do seu Drive, ele não vê seus outros arquivos) → no primeiro aparelho,
**crie** a senha de sincronização; nos outros, **digite** a mesma. Use a **mesma conta do Google**
em todos: se a tela pedir para criar a senha num aparelho que não é o primeiro, você entrou com
outra conta. Para levar o ID ao celular, toque em "Copiar o ID" e mande para você mesmo.

**No iPhone**, com o app instalado na Tela de Início, a janela de login do Google às vezes não abre
ou não volta para o app. Se acontecer, use o caminho manual: **Baixar uma cópia** num aparelho e
**restaurar** (troca tudo) ou **Importar JSON** (junta) no outro.

**Desconectar do Google** esquece e revoga o acesso neste aparelho (a senha continua guardada).
**Parar de sincronizar neste aparelho** tira a senha e a chave daqui (e das cópias automáticas) e,
com o Google conectado, apaga o arquivo deste aparelho no Drive; os seus registros continuam. O que
você apagar com a sincronização parada não volta se ligar de novo com a mesma senha. Para apagar
tudo do Drive de vez (ou se esqueceu a senha): pare em todos os aparelhos e apague os dados do app
em drive.google.com → Configurações → Gerenciar apps → Entre Nós → Opções → Excluir dados de apps
ocultos.

**Restaurar um backup** com a sincronização ligada não "volta no tempo": o que está nos outros
aparelhos volta na próxima sincronização, e o que foi apagado neles continua apagado.

**O que fica no Drive:** `entrenos-sync.json` (sal, parâmetros e um verificador da senha, nunca a
senha nem a chave) e um `entrenos-<aparelho>.sync` por aparelho, cifrado (AES-256-GCM), com o
tamanho arredondado em degraus (o Google não vê quanto você escreve). Cada aparelho só escreve o
seu arquivo e apaga as versões antigas dele que o Drive guarda.

## Testes

```
cd C:\Users\Usuario\EntreNos
python -m unittest discover -s testes -t .
```

Os testes usam uma pasta temporária (variável `ENTRENOS_DADOS`) e nunca tocam nos seus dados.
O contrato entre os módulos está em `DESIGN.md`.

O app do celular é testado também com o **Pyodide de verdade**, no Node (o do VS Code serve), sem
navegador: monta o site numa pasta temporária, carrega o Python, faz o fluxo inteiro, abre no
Pyodide um `.cofre` criado pelo Python do PC (e o contrário) e mede os tempos.

```
$env:ELECTRON_RUN_AS_NODE='1'; & "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe" testes\navegador\teste_pyodide.mjs
```

A sincronização da página (`web/sincronizar.js`) é testada contra um **Google Drive de mentira**
(um servidor local que imita os endereços da Drive API: token vencido, sem rede, erros do Google,
revisões), com o login do Google de mentira (a janela e a volta pelo `oauth.html`), três aparelhos
rodando o núcleo de verdade no Pyodide, e a tela de Ajustes num DOM de mentira.
Nada vai para o Google:

```
$env:ELECTRON_RUN_AS_NODE='1'; & "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe" testes\navegador\teste_sincronizar.mjs
```
