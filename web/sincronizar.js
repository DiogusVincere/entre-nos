/* Entre Nós: sincronização entre aparelhos pelo Google Drive (DESIGN.md, seções 22 e 22.2).
 *
 * Quem fala com o Google é esta página; o núcleo (Python, no computador ou no Worker do celular)
 * nunca vê o token. O núcleo cifra e decifra os arquivos (rotas /api/sync/...) e faz a mescla; aqui só
 * passam bytes já embaralhados com a senha de sincronização.
 *
 * - Login (22.2): NENHUM código do Google roda nesta página. "Conectar" abre uma janela do Google
 *   (o fluxo de token para apps web: accounts.google.com/o/oauth2/v2/auth, escopo SÓ drive.appdata,
 *   include_granted_scopes=false, um `state` aleatório) que volta para o oauth.html deste mesmo
 *   endereço; ele entrega o acesso a esta página (postMessage ou BroadcastChannel, só a mesma origem)
 *   e se fecha. Um acesso com o `state` errado, ou com permissão além de drive.appdata, é recusado. O
 *   token fica só na memória desta página (nunca em armazenamento do navegador, nunca em log), dura
 *   cerca de 1 h e é esquecido quando o diário se bloqueia; "Desconectar" e "Parar" revogam.
 * - Drive REST v3 na appDataFolder: listar, baixar, criar e atualizar SÓ o arquivo deste aparelho
 *   (entrenos-<aparelho>.sync) e o entrenos-sync.json (criado uma vez, pelo primeiro aparelho); depois
 *   de atualizar, apaga as revisões antigas do próprio arquivo; apaga os arquivos antigos DESTE aparelho
 *   (de uma senha anterior) e, ao parar, o próprio arquivo, se a pessoa quiser.
 * - Quando: enquanto o acesso vale, ao abrir, uns 30 s depois de salvar (agrupado) e no "Sincronizar
 *   agora". Sem internet: fica na fila. Sem acesso (o app foi fechado ou se bloqueou): o selo ☁️ no
 *   alto da tela pede um toque.
 *
 * O motor (criarDrive, criarSincronizador, criarConta) não depende da tela: o teste do Node
 * (testes/navegador/teste_sincronizar.mjs) roda os mesmos objetos contra um Drive de mentira.
 */
'use strict';

(() => {
  const ESCOPO = 'https://www.googleapis.com/auth/drive.appdata';
  const URL_AUTORIZAR = 'https://accounts.google.com/o/oauth2/v2/auth';
  const URL_REVOGAR = 'https://oauth2.googleapis.com/revoke';
  const PAGINA_RETORNO = 'oauth.html';
  const CANAL_OAUTH = 'entrenos-oauth';
  const BASE_DRIVE = 'https://www.googleapis.com/drive/v3';
  const BASE_ENVIO = 'https://www.googleapis.com/upload/drive/v3';
  const ARQUIVO_CONFIG = 'entrenos-sync.json';
  const RE_ARQUIVO = /^entrenos-([0-9a-f]{16})\.sync$/;
  const AGRUPAR_MS = 30000;                 // depois de salvar, espera juntar mais mudanças
  const FILA_MS = 60000;                    // sem rede: tenta de novo (se o evento "online" não vier antes)
  const ESPERAS_GOOGLE_MS = [2, 5, 15].map((m) => m * 60000);
  const MARGEM_TOKEN_MS = 60000;            // token a menos de 1 min de vencer: pede outro
  const LIMITE_LOGIN_MS = 5 * 60000;        // a janela do Google aberta sem resposta: desiste
  // O arquivo de outro aparelho vai ao núcleo em base64; o núcleo diz o maior que aceita
  // (/sync/estado: limite_arquivo). Sem essa informação, o limite do computador.
  const LIMITE_ENVIO = 64 * 1024 * 1024;

  const MENSAGENS = Object.freeze({
    rede: 'Sem internet agora. A sincronização fica na fila e tenta de novo quando a conexão voltar.',
    token: 'O acesso ao Google venceu (ele dura cerca de uma hora). Toque em “Sincronizar agora” para entrar de novo.',
    sem_token: 'Toque em “Sincronizar agora” para entrar no Google e sincronizar.',
    permissao: 'O Google não deixou o Entre Nós guardar arquivos no seu Drive. Conecte de novo e, na janela do Google, deixe marcada a permissão de guardar os dados do app.',
    permissao_demais: 'O Google devolveu um acesso maior que a pasta escondida do app (talvez de uma permissão dada antes ao mesmo ID). Por segurança, o Entre Nós não usou esse acesso. Em myaccount.google.com → Segurança → Apps de terceiros, remova o acesso do app e conecte de novo.',
    api_desligada: 'A Google Drive API não está ativada no projeto do Google Cloud deste ID do app (veja o passo 2 do passo a passo).',
    limite: 'O Google pediu para esperar um pouco. Tento de novo em alguns minutos.',
    servidor: 'O Google Drive não respondeu direito agora. Tento de novo em alguns minutos.',
    espaco: 'Seu Google Drive está sem espaço. Libere espaço e toque em “Sincronizar agora”.',
    nao_encontrado: 'Um arquivo da sincronização sumiu do Google Drive no meio do caminho. Toque em “Sincronizar agora” para tentar de novo.',
    popup_bloqueado: 'O navegador bloqueou a janela do Google. Permita janelas (pop-ups) para este endereço e toque de novo.',
    popup_fechado: 'A janela do Google foi fechada (ou demorou demais) antes de terminar. Toque de novo quando quiser.',
    cancelado: 'Conexão com o Google cancelada.',
    sem_id: 'Falta o ID do app do Google: siga o passo a passo e cole o ID aqui.',
    senha_mudou: 'A senha de sincronização foi trocada em outro aparelho. Digite a senha de sincronização de novo.',
    sem_senha: 'Falta a senha de sincronização neste aparelho.',
    arquivo: 'Um arquivo de sincronização no Google Drive não abre com a sua senha (mexido ou de outra senha). Ele ficou de fora; o resto sincronizou.',
    versao: 'Um dos seus aparelhos usa uma versão mais nova do Entre Nós. Atualize este aparelho para sincronizar com ele.',
    bloqueado: 'O Entre Nós se bloqueou: a sincronização continua depois que você digitar o PIN.',
    grande_outro: 'O arquivo de outro aparelho é grande demais para este aparelho abrir; ele ficou de fora (nem foi baixado).',
    desconhecido: 'A sincronização não deu certo desta vez. Toque em “Sincronizar agora” para tentar de novo.',
  });

  class ErroSync extends Error {
    constructor(tipo, mensagem = null, status = 0) {
      super(mensagem || MENSAGENS[tipo] || MENSAGENS.desconhecido);
      this.tipo = tipo;
      this.status = status;
    }
  }

  // ---------------------------------------------------------------- bytes e texto

  function bytesParaB64(bytes) {
    let texto = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      texto += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(texto);
  }

  function b64ParaBytes(b64) {
    const texto = atob(String(b64 || ''));
    const bytes = new Uint8Array(texto.length);
    for (let i = 0; i < texto.length; i += 1) bytes[i] = texto.charCodeAt(i);
    return bytes;
  }

  function porCriacao(a, b) {
    const ta = String(a.createdTime || ''), tb = String(b.createdTime || '');
    if (ta !== tb) return ta < tb ? -1 : 1;
    return String(a.id) < String(b.id) ? -1 : 1;
  }

  function etiquetaDe(arquivo) {
    // O md5 do Drive muda quando o conteúdo muda; sem ele, a data de modificação.
    return String((arquivo && (arquivo.md5Checksum || arquivo.modifiedTime)) || '').slice(0, 200) || null;
  }

  function hexAleatorio(bytes = 16) {
    const valores = new Uint8Array(bytes);
    globalThis.crypto.getRandomValues(valores);
    return [...valores].map((v) => v.toString(16).padStart(2, '0')).join('');
  }

  // ---------------------------------------------------------------- uma frase de senha (22.2)
  //
  // Palavras simples, sem acento (iguais em qualquer teclado). Quatro delas + dois algarismos dão
  // umas 9 × 10^12 combinações: com o scrypt da senha, testar todas fora do app leva anos.
  const PALAVRAS = [...new Set(`
    abelha aguia alce andorinha anta arara baleia besouro bezerro bode borboleta burro cabra cachorro
    camelo canario caracol carneiro castor cavalo cegonha cervo cigarra cisne coala coelho coruja corvo
    cotovia doninha elefante esquilo falcao foca formiga gaivota galinha galo ganso gato gaviao girafa
    golfinho gorila grilo guaxinim hamster iguana jabuti jacare javali joaninha lagarto lebre leao
    leopardo lhama lobo lontra macaco marreco morcego mula onca orca ovelha panda papagaio pardal pato
    pavao peixe pelicano periquito pinguim polvo pombo potro preguica puma raposa rena rouxinol sabia
    sapo siri suricato tamandua tartaruga tatu texugo tigre toupeira tucano urso vaca veado zebra
    abacate abacaxi abobora acerola alface alho ameixa amendoim amora arroz azeitona banana batata
    baunilha beterraba biscoito bolacha bolo cacau cafe caju canela caqui carambola cebola cenoura
    cereja chocolate coco cocada cuscuz damasco ervilha feijao figo framboesa goiaba granola groselha
    jaca kiwi laranja limao lentilha maca mamao manga mangaba maracuja melancia melao milho mingau
    morango nabo nectarina nozes omelete pacoca pamonha panqueca pao pepino pera pequi pimenta pipoca
    pitanga pudim queijo quiabo rabanete salada sorvete sopa tapioca tomate torrada uva vagem
    areia arvore aurora bosque brisa cachoeira campo caverna cerrado chuva colina cometa deserto duna
    eclipse estrela floresta fonte galaxia garoa geada granizo horizonte ilha lago lagoa lua luar
    mangue mar mata montanha neblina neve nuvem oceano onda orvalho pantano pedra planeta planalto
    praia rio riacho rocha savana serra sol terra trovao vale vento vulcao cascata penhasco recife
    relva pradaria acacia alecrim azaleia bambu begonia bromelia cacto camelia capim cravo dalia
    girassol geranio hortela ipe jasmim lavanda lirio lotus magnolia malva manjericao margarida musgo
    orquidea palmeira papoula petunia pinheiro rosa salgueiro samambaia tulipa violeta trevo junco
    jacaranda cipreste eucalipto figueira abajur agulha almofada ancora anel apito armario balanco
    balde balao bandeira banco barco bengala berimbau bicicleta binoculo boneca bola bolsa bota botao
    bule bussola cadeado caderno cadeira caixa calendario cama camisa caneca caneta canoa carimbo
    carroca carrossel casaco castelo catavento chaleira chapeu chave chinelo chocalho cobertor colar
    colher copo corda cortina coroa escada escova espelho estojo farol flauta fogao foguete garfo
    garrafa gaveta guitarra harpa janela jarra lampiao lanterna lapis livro lousa luva mala mapa
    martelo mesa mochila moeda moinho novelo oculos panela papel pandeiro peteca piano pincel pipa piao
    prato quadro regador relogio rede sabonete sacola sanfona sapato sino sofa tambor tapete tesoura
    tijolo toalha trem trompete vaso vassoura vela veleiro violao xicara ziper zabumba cesta concha
    carrinho aldeia avenida barraca cabana capela casa celeiro chacara cidade circo cozinha estacao
    fazenda feira fogueira galpao horta jardim mercado museu padaria palacio parque ponte porto
    quintal rua sitio teatro torre varanda vila sotao pomar estrada trilha gruta quiosque azul verde
    amarelo roxo rosado dourado prateado branco cinza bege lilas marrom turquesa alegre calmo bravo
    doce leve macio quente frio gentil sereno curioso distante antigo novo pequeno grande rapido lento
    brilhante secreto sonhador feliz tranquilo valente sabido risonho cheiroso crocante fofo redondo
    quadrado samba forro frevo choro valsa tango bolero ciranda maracatu poesia verso rima cantiga
    melodia ritmo sinfonia opera novela conto fabula lenda enigma charada cartola pirata palhaco
    sereia fada duende gigante dragao unicornio viagem aventura domingo sabado janeiro outubro
    primavera outono inverno verao segredo abraco sorriso bilhete carta postal retrato album saudade
    cafune afago carinho pijama travesseiro lareira cobertura telhado chamine jangada farofa quindim
    canjica pastel coxinha empada brownie waffle muffin cookie mousse tiramisu risoto lasanha nhoque
    talharim pizza esfiha quibe tabule homus falafel burrito nacho taco paella fondue sushi tempura
  `.trim().split(/\s+/))];

  function sortear(n) {
    // Sem viés: descarta o que passaria do maior múltiplo de n.
    const limite = Math.floor(0x100000000 / n) * n;
    const valor = new Uint32Array(1);
    do globalThis.crypto.getRandomValues(valor); while (valor[0] >= limite);
    return valor[0] % n;
  }

  /* "coruja tapioca farol sereno 47": quatro palavras diferentes e dois algarismos. */
  function gerarFrase(quantas = 4) {
    const escolhidas = new Set();
    while (escolhidas.size < quantas) escolhidas.add(PALAVRAS[sortear(PALAVRAS.length)]);
    return `${[...escolhidas].join(' ')} ${10 + sortear(90)}`;
  }

  /* O mesmo que o núcleo confere (sincronizacao.problema_da_senha), só para avisar antes de enviar; o
   * núcleo decide. */
  function problemaDaSenha(senha) {
    const texto = String(senha || '').normalize('NFC').trim();
    if (!texto) return 'Digite a senha de sincronização.';
    if (texto.length < 14) return 'A senha de sincronização precisa ter pelo menos 14 caracteres. Uma frase de três ou quatro palavras é o mais fácil de lembrar (como a sugerida aqui).';
    const simples = texto.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    const palavras = new Set(simples.match(/[a-z]{2,}/g) || []);
    const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^\p{L}\p{N}\s]/u].filter((r) => r.test(texto)).length;
    if (palavras.size >= 3 || (texto.length >= 20 && classes >= 3 && new Set(texto).size >= 12)) return null;
    return 'Use uma frase com pelo menos 3 palavras diferentes (como a sugerida aqui). Uma palavra ou um nome só, mesmo com números ou símbolos, é fácil de adivinhar por quem copiar os arquivos do Drive.';
  }

  // ---------------------------------------------------------------- Drive REST v3 (appDataFolder)

  async function erroDoGoogle(resposta) {
    let razao = '';
    try {
      const corpo = await resposta.json();
      const erro = corpo && corpo.error;
      razao = [erro && erro.status, ...((erro && Array.isArray(erro.errors) ? erro.errors : []).map((e) => e && e.reason))]
        .filter(Boolean).join(' ');
    } catch {
      // corpo que não é JSON: decide só pelo status
    }
    const s = resposta.status;
    if (s === 401) return new ErroSync('token', null, s);
    if (s === 429 || /rateLimit|userRateLimit|RESOURCE_EXHAUSTED/i.test(razao)) return new ErroSync('limite', null, s);
    if (/storageQuota/i.test(razao)) return new ErroSync('espaco', null, s);
    if (/accessNotConfigured|SERVICE_DISABLED|apiNotActivated/i.test(razao)) return new ErroSync('api_desligada', null, s);
    if (s === 403) return new ErroSync('permissao', null, s);
    if (s === 404) return new ErroSync('nao_encontrado', null, s);
    return new ErroSync('servidor', null, s);
  }

  /* buscar: fetch; token(): o token de agora (ou null). base/baseEnvio: os endereços da API (o teste
   * aponta para um Drive de mentira). */
  function criarDrive({ buscar, token, base = BASE_DRIVE, baseEnvio = BASE_ENVIO }) {
    async function pedir(url, opcoes = {}, comoResposta = 'json') {
      const t = token();
      if (!t) throw new ErroSync('sem_token');
      let resposta;
      try {
        resposta = await buscar(url, {
          ...opcoes,
          headers: { ...(opcoes.headers || {}), Authorization: `Bearer ${t}` },
          credentials: 'omit',
          referrerPolicy: 'no-referrer',
        });
      } catch {
        throw new ErroSync('rede');
      }
      if (!resposta.ok) throw await erroDoGoogle(resposta);
      try {
        if (comoResposta === 'bytes') return new Uint8Array(await resposta.arrayBuffer());
        if (comoResposta === 'texto') return await resposta.text();
        if (resposta.status === 204) return null;
        return await resposta.json();
      } catch {
        throw new ErroSync('rede');          // a conexão caiu no meio da resposta
      }
    }

    function multipart(nome, bytes, tipo) {
      const limite = `entrenos${Date.now().toString(16)}${Math.floor(Math.random() * 1e9).toString(16)}`;
      const meta = JSON.stringify({ name: nome, parents: ['appDataFolder'], mimeType: tipo });
      const corpo = new Blob([
        `--${limite}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${meta}\r\n`,
        `--${limite}\r\nContent-Type: ${tipo}\r\n\r\n`, bytes, `\r\n--${limite}--\r\n`,
      ]);
      return { corpo, tipo: `multipart/related; boundary=${limite}` };
    }

    const CAMPOS = 'id,name,size,md5Checksum,modifiedTime,createdTime';
    const drive = {
      async listar() {
        const arquivos = [];
        let pagina = '';
        for (let volta = 0; volta < 50; volta += 1) {
          const q = new URLSearchParams({ spaces: 'appDataFolder', pageSize: '1000', orderBy: 'createdTime',
            fields: `nextPageToken,files(${CAMPOS})` });
          if (pagina) q.set('pageToken', pagina);
          const r = await pedir(`${base}/files?${q}`);
          if (r && Array.isArray(r.files)) arquivos.push(...r.files.filter((a) => a && typeof a.name === 'string' && a.id));
          if (!r || !r.nextPageToken) break;
          pagina = r.nextPageToken;
        }
        return arquivos;
      },
      baixar(id) {
        return pedir(`${base}/files/${encodeURIComponent(id)}?alt=media`, {}, 'bytes');
      },
      baixarTexto(id) {
        return pedir(`${base}/files/${encodeURIComponent(id)}?alt=media`, {}, 'texto');
      },
      criar(nome, bytes, tipo = 'application/octet-stream') {
        const { corpo, tipo: tipoCorpo } = multipart(nome, bytes, tipo);
        return pedir(`${baseEnvio}/files?uploadType=multipart&fields=${CAMPOS}`,
          { method: 'POST', headers: { 'Content-Type': tipoCorpo }, body: corpo });
      },
      // Só o arquivo deste aparelho é atualizado (o motor nunca chama isto com o id de outro).
      atualizar(id, bytes, tipo = 'application/octet-stream') {
        return pedir(`${baseEnvio}/files/${encodeURIComponent(id)}?uploadType=media&fields=${CAMPOS}`,
          { method: 'PATCH', headers: { 'Content-Type': tipo }, body: bytes });
      },
      // Só a configuração que o próprio aparelho acabou de criar numa corrida, o arquivo deste
      // aparelho (ao parar) e os arquivos antigos dele (de uma senha anterior).
      apagar(id) {
        return pedir(`${base}/files/${encodeURIComponent(id)}`, { method: 'DELETE' });
      },
      /* Depois de atualizar o próprio arquivo: as versões anteriores que o Drive guarda (revisões, por
       * uns 30 dias) saem, para o que foi apagado não continuar lá, mesmo embaralhado (22.2). A última
       * versão o Drive não deixa apagar (e é a de agora). */
      async limparRevisoes(id) {
        const r = await pedir(`${base}/files/${encodeURIComponent(id)}/revisions?fields=${encodeURIComponent('revisions(id)')}`);
        const revisoes = (r && Array.isArray(r.revisions) ? r.revisions : []).filter((v) => v && v.id);
        let apagadas = 0;
        for (const revisao of revisoes.slice(0, -1)) {
          await pedir(`${base}/files/${encodeURIComponent(id)}/revisions/${encodeURIComponent(revisao.id)}`, { method: 'DELETE' });
          apagadas += 1;
        }
        return apagadas;
      },
      async conta() {
        const r = await pedir(`${base}/about?fields=${encodeURIComponent('user(displayName,emailAddress)')}`);
        const u = (r && r.user) || {};
        return String(u.emailAddress || u.displayName || '').slice(0, 200) || null;
      },
    };
    return drive;
  }

  // ---------------------------------------------------------------- o login do Google (janela, sem biblioteca)

  // A volta do login: postMessage do oauth.html (mesma origem) e, se a janela perdeu a ligação com
  // esta página (o navegador isola janelas que passaram por outro site), o BroadcastChannel.
  function ouvirRetornoPadrao(aoReceber) {
    const desligar = [];
    const tratar = (dados) => {
      if (dados && typeof dados === 'object' && dados.tipo === 'entrenos-oauth') aoReceber(dados);
    };
    if (globalThis.addEventListener) {
      const ouvinte = (evento) => {
        if (evento && evento.origin === globalThis.location.origin) tratar(evento.data);
      };
      globalThis.addEventListener('message', ouvinte);
      desligar.push(() => globalThis.removeEventListener('message', ouvinte));
    }
    if (typeof BroadcastChannel === 'function') {
      const canal = new BroadcastChannel(CANAL_OAUTH);
      canal.onmessage = (evento) => tratar(evento && evento.data);
      desligar.push(() => canal.close());
    }
    return () => desligar.forEach((f) => f());
  }

  function enderecoDeRetorno() {
    return new URL(PAGINA_RETORNO, globalThis.location.href).href.split('#')[0].split('?')[0];
  }

  /* O token do Google, só na memória. definirId(id_app) guarda o ID do app; entrar() abre a janela do
   * Google (chame direto no toque, sem await antes: janela só abre com toque). */
  function criarConta({
    abrirJanela = (url) => globalThis.open(url, 'entrenos-google', 'popup,width=520,height=680'),
    ouvirRetorno = ouvirRetornoPadrao, retorno = enderecoDeRetorno, aleatorio = () => hexAleatorio(16),
    buscar = (...a) => globalThis.fetch(...a), agora = () => Date.now(),
    agendar = (f, ms) => setTimeout(f, ms), cancelar = (id) => clearTimeout(id),
  } = {}) {
    let token = null;
    let expira = 0;
    let idCliente = null;
    let esperando = null;          // {resolver, rejeitar, desligar, tempo, janela} do entrar() em andamento

    function terminar(erro, valor) {
      const e = esperando;
      esperando = null;
      if (!e) return;
      try {
        e.desligar();
      } catch {
        // já desligado
      }
      if (e.tempo !== null) cancelar(e.tempo);
      if (erro) e.rejeitar(erro);
      else e.resolver(valor);
    }

    function revogarToken(t) {
      if (!t) return;
      // Sem CORS (a resposta não interessa): o Google revoga do mesmo jeito.
      Promise.resolve().then(() => buscar(URL_REVOGAR, {
        method: 'POST', mode: 'no-cors', credentials: 'omit', referrerPolicy: 'no-referrer',
        body: new URLSearchParams({ token: t }),
      })).catch(() => {});
    }

    function receber(dados, state) {
      if (!esperando || !dados || dados.state !== state) return;       // não é a resposta deste pedido
      if (dados.error) {
        terminar(new ErroSync(dados.error === 'access_denied' ? 'permissao' : 'desconhecido'));
        return;
      }
      const escopos = String(dados.scope || '').split(/\s+/).filter(Boolean);
      const t = String(dados.access_token || '');
      if (!t || !escopos.includes(ESCOPO)) {
        revogarToken(t);
        terminar(new ErroSync('permissao'));
        return;
      }
      if (escopos.some((e) => e !== ESCOPO)) {
        // Mais do que a pasta do app (o Drive inteiro, por exemplo): não usa.
        revogarToken(t);
        terminar(new ErroSync('permissao_demais'));
        return;
      }
      token = t;
      expira = agora() + Math.max(60, Number(dados.expires_in) || 3600) * 1000;
      terminar(null, true);
    }

    return {
      pronta: () => Boolean(idCliente),
      tokenValido: () => Boolean(token) && agora() < expira - MARGEM_TOKEN_MS,
      token: () => (token && agora() < expira ? token : null),
      conectando: () => Boolean(esperando),
      definirId(id) {
        idCliente = id ? String(id) : null;
      },
      entrar({ dica } = {}) {
        if (!idCliente) return Promise.reject(new ErroSync('sem_id'));
        if (esperando) terminar(new ErroSync('cancelado'));
        const state = aleatorio();
        const q = new URLSearchParams({
          client_id: idCliente, redirect_uri: retorno(), response_type: 'token', scope: ESCOPO,
          include_granted_scopes: 'false', state,
        });
        if (dica) q.set('login_hint', String(dica));
        let janela = null;
        try {
          janela = abrirJanela(`${URL_AUTORIZAR}?${q}`);
        } catch {
          janela = null;
        }
        if (!janela) return Promise.reject(new ErroSync('popup_bloqueado'));
        return new Promise((resolver, rejeitar) => {
          esperando = { resolver, rejeitar, desligar: () => {}, tempo: null, janela };
          esperando.desligar = ouvirRetorno((dados) => receber(dados, state));
          esperando.tempo = agendar(() => terminar(new ErroSync('popup_fechado')), LIMITE_LOGIN_MS);
        });
      },
      // "Cancelar" enquanto a janela do Google está aberta (ou foi fechada sem terminar).
      cancelar() {
        const janela = esperando && esperando.janela;
        terminar(new ErroSync('cancelado'));
        try {
          if (janela && typeof janela.close === 'function') janela.close();
        } catch {
          // a pessoa fecha
        }
      },
      // Esquece o token (o diário se bloqueou): para sincronizar de novo, um toque.
      esquecer() {
        token = null;
        expira = 0;
      },
      // Desconectar e Parar: esquece e pede ao Google para revogar.
      revogar() {
        const t = token;
        token = null;
        expira = 0;
        revogarToken(t);
      },
    };
  }

  // ---------------------------------------------------------------- o motor

  /* api(metodo, rota, corpo, opcoes): a mesma do app (EntreNos.api), que fala com o núcleo.
   * drive: criarDrive(...). conta: criarConta(...) (ou algo com tokenValido/esquecer).
   * relogio: {agendar(f, ms), cancelar(id)}. online(): há rede? aoMudar(estado): para a tela. */
  function criarSincronizador({ api, drive, conta, relogio = null, online = null, aoMudar = () => {} }) {
    const agendar = (relogio && relogio.agendar) || ((f, ms) => setTimeout(f, ms));
    const cancelar = (relogio && relogio.cancelar) || ((id) => clearTimeout(id));
    const temRede = online || (() => typeof navigator === 'undefined' || navigator.onLine !== false);
    const estado = {
      situacao: 'parada',     // parada | precisa_entrar | sincronizando | ok | na_fila | erro | senha_mudou | sem_senha
      erro: null, tipoErro: null, ultimaVez: null, resumo: null, pendente: false, avisos: [],
    };
    // Arquivos de outros aparelhos que não entraram (grandes demais, que não abrem): a etiqueta e o
    // aviso, para não baixar de novo enquanto não mudarem.
    const recusados = new Map();
    let andamento = null;
    let temporizador = null;
    let tentativas = 0;
    let parado = false;

    function mudar(campos) {
      Object.assign(estado, campos);
      try {
        aoMudar({ ...estado });
      } catch {
        // a tela pode ter saído: o motor segue
      }
    }

    function limparTemporizador() {
      if (temporizador !== null) cancelar(temporizador);
      temporizador = null;
    }

    function programar(ms, motivo) {
      limparTemporizador();
      temporizador = agendar(() => {
        temporizador = null;
        return sincronizar({ motivo }).catch(() => null);
      }, ms);
    }

    async function lerConfigDoDrive(arquivos) {
      const configs = arquivos.filter((a) => a.name === ARQUIVO_CONFIG).sort(porCriacao);
      return configs.length ? { arquivo: configs[0], texto: await drive.baixarTexto(configs[0].id) } : null;
    }

    async function umaVez() {
      const est = await api('GET', '/sync/estado', undefined, { semBloqueio: true });
      if (!est || !est.configurada) {
        mudar({ situacao: 'sem_senha', erro: MENSAGENS.sem_senha, tipoErro: 'sem_senha' });
        return null;
      }
      const limite = Number(est.limite_arquivo) > 0 ? Number(est.limite_arquivo) : LIMITE_ENVIO;
      let arquivos = await drive.listar();
      let config = await lerConfigDoDrive(arquivos);
      if (!config && est.sync_json) {
        // O entrenos-sync.json sumiu do Drive (apagado à mão?): volta, igual (sal e verificador desta chave).
        const criado = await drive.criar(ARQUIVO_CONFIG, new TextEncoder().encode(est.sync_json), 'application/json');
        arquivos = [...arquivos, criado];
        config = { arquivo: criado, texto: est.sync_json };
      }
      if (!config) throw new ErroSync('desconhecido');
      const inicio = await api('POST', '/sync/iniciar', { sync_json: config.texto }, { semBloqueio: true });
      const proprio = inicio.arquivo;
      const conhecidas = inicio.etiquetas || {};
      // Arquivos antigos DESTE aparelho (de uma senha anterior): saem do Drive.
      const apagados = [];
      for (const nome of Array.isArray(est.apagar) ? est.apagar : []) {
        if (!RE_ARQUIVO.test(nome) || nome === proprio) continue;
        for (const velho of arquivos.filter((a) => a.name === nome)) {
          try {
            await drive.apagar(velho.id);
          } catch (e) {
            if (!(e instanceof ErroSync && e.tipo === 'nao_encontrado')) throw e;     // já tinha saído
          }
        }
        apagados.push(nome);
      }
      if (apagados.length) arquivos = arquivos.filter((a) => !apagados.includes(a.name));
      const resumo = { recebidos: 0, registros_novos: 0, apagados: 0, atualizados: 0, conflitos: 0, itens_novos: 0, avisos: [] };
      for (const arquivo of arquivos) {
        const m = RE_ARQUIVO.exec(arquivo.name);
        if (!m || arquivo.name === proprio) continue;
        const etiqueta = etiquetaDe(arquivo);
        if (etiqueta && conhecidas[m[1]] === etiqueta) continue;          // não mudou: nem baixa
        const recusado = recusados.get(m[1]);
        if (recusado && etiqueta && recusado.etiqueta === etiqueta) {
          resumo.avisos.push(recusado.aviso);                              // o mesmo de antes: nem baixa
          continue;
        }
        // Grande demais para o núcleo daqui: nem baixa (antes baixava inteiro, a cada sincronização).
        if (Number(arquivo.size) > limite) {
          resumo.avisos.push(MENSAGENS.grande_outro);
          if (etiqueta) recusados.set(m[1], { etiqueta, aviso: MENSAGENS.grande_outro });
          continue;
        }
        const bytes = await drive.baixar(arquivo.id);
        try {
          const r = await api('POST', '/sync/receber', { nome: arquivo.name, etiqueta, conteudo_b64: bytesParaB64(bytes) },
            { semBloqueio: true, limiteBytes: LIMITE_ENVIO });
          recusados.delete(m[1]);
          resumo.recebidos += 1;
          for (const campo of ['registros_novos', 'apagados', 'atualizados', 'conflitos', 'itens_novos']) {
            resumo[campo] += Number(r && r[campo]) || 0;
          }
          for (const aviso of (r && Array.isArray(r.avisos) ? r.avisos : [])) resumo.avisos.push(String(aviso));
        } catch (e) {
          // Um arquivo estranho (mexido, de outra senha, antigo, de versão mais nova) fica de fora; o resto segue.
          let aviso = null;
          if (e && e.status === 400 && e.dados && e.dados.arquivo_invalido) {
            aviso = typeof e.dados.erro === 'string' && e.dados.erro ? e.dados.erro : MENSAGENS.arquivo;
          } else if (e && e.status === 409 && e.dados && e.dados.versao_nova) aviso = MENSAGENS.versao;
          else if (e && e.status === 413) aviso = MENSAGENS.grande_outro;
          else throw e;
          resumo.avisos.push(aviso);
          if (etiqueta) recusados.set(m[1], { etiqueta, aviso });
        }
      }
      const meu = await api('POST', '/sync/preparar', {}, { semBloqueio: true });
      if (meu.aviso) resumo.avisos.push(String(meu.aviso));
      const bytes = b64ParaBytes(meu.conteudo_b64);
      const existente = arquivos.find((a) => a.name === meu.nome);
      const enviado = existente ? await drive.atualizar(existente.id, bytes) : await drive.criar(meu.nome, bytes);
      if (existente) {
        // Revisões antigas do próprio arquivo: sai o que foi apagado (melhor esforço; ver DESIGN 22.2).
        try {
          await drive.limparRevisoes(existente.id);
        } catch {
          // o Drive não deixou agora: tenta na próxima
        }
      }
      const fim = await api('POST', '/sync/enviado', { etiqueta: etiquetaDe(enviado), ...(apagados.length ? { apagados } : {}) },
        { semBloqueio: true });
      resumo.avisos = [...new Set(resumo.avisos)];
      return { ...resumo, ultima: fim && fim.ultima };
    }

    function tratarErro(e) {
      if (e && e.status === 423) {
        mudar({ situacao: 'parada', erro: MENSAGENS.bloqueado, tipoErro: 'bloqueado', pendente: true });
        return;
      }
      if (e && e.status === 409 && e.dados && e.dados.senha_mudou) {
        mudar({ situacao: 'senha_mudou', erro: MENSAGENS.senha_mudou, tipoErro: 'senha_mudou' });
        return;
      }
      if (e && e.status === 409 && e.dados && e.dados.sem_senha) {
        mudar({ situacao: 'sem_senha', erro: MENSAGENS.sem_senha, tipoErro: 'sem_senha' });
        return;
      }
      const tipo = e instanceof ErroSync ? e.tipo : null;
      if (tipo === 'token' || tipo === 'sem_token') {
        conta.esquecer();
        mudar({ situacao: 'precisa_entrar', erro: tipo === 'token' ? MENSAGENS.token : MENSAGENS.sem_token, tipoErro: tipo, pendente: true });
        return;
      }
      if (tipo === 'rede') {
        mudar({ situacao: 'na_fila', erro: MENSAGENS.rede, tipoErro: 'rede', pendente: true });
        programar(FILA_MS, 'fila');
        return;
      }
      if (tipo === 'limite' || tipo === 'servidor') {
        const espera = ESPERAS_GOOGLE_MS[Math.min(tentativas, ESPERAS_GOOGLE_MS.length - 1)];
        tentativas += 1;
        mudar({ situacao: 'na_fila', erro: e.message, tipoErro: tipo, pendente: true });
        programar(espera, 'nova-tentativa');
        return;
      }
      mudar({ situacao: 'erro', erro: (e && e.message) || MENSAGENS.desconhecido, tipoErro: tipo || 'desconhecido' });
    }

    async function executar(motivo) {
      if (!temRede()) {
        mudar({ situacao: 'na_fila', erro: MENSAGENS.rede, tipoErro: 'rede', pendente: true });
        programar(FILA_MS, 'fila');          // e também quando o evento "online" chegar (voltou)
        return null;
      }
      if (!conta.tokenValido()) {
        conta.esquecer();
        mudar({ situacao: 'precisa_entrar', erro: MENSAGENS.sem_token, tipoErro: 'sem_token', pendente: true });
        return null;
      }
      mudar({ situacao: 'sincronizando', erro: null, tipoErro: null, pendente: false, motivo });
      try {
        const resumo = await umaVez();
        if (!resumo) return null;
        tentativas = 0;
        mudar({ situacao: 'ok', ultimaVez: resumo.ultima || new Date().toISOString(), resumo, avisos: resumo.avisos });
        return resumo;
      } catch (e) {
        tratarErro(e);
        return null;
      }
    }

    function sincronizar({ motivo = 'toque' } = {}) {
      if (parado) return Promise.resolve(null);
      limparTemporizador();
      if (andamento) {
        estado.pendente = true;            // termina esta e faz mais uma (salvou no meio)
        return andamento;
      }
      andamento = executar(motivo).finally(() => {
        andamento = null;
        if (estado.pendente && estado.situacao === 'ok') {
          estado.pendente = false;
          programar(AGRUPAR_MS, 'salvou');
        }
      });
      return andamento;
    }

    return {
      estado: () => ({ ...estado }),
      sincronizar,
      // Depois de salvar: espera ~30 s juntando mudanças (cada nova adia). Sem acesso ao Google, só
      // marca que há algo para mandar (o selo ☁️ pede o toque).
      depoisDeSalvar() {
        if (parado) return;
        estado.pendente = true;
        if (andamento) return;
        if (!conta.tokenValido()) {
          mudar({ situacao: 'precisa_entrar', erro: MENSAGENS.sem_token, tipoErro: 'sem_token', pendente: true });
          return;
        }
        programar(AGRUPAR_MS, 'salvou');
      },
      // A rede voltou, ou a aba voltou a aparecer: se havia algo na fila, vai agora.
      voltou() {
        if (!parado && estado.pendente && !andamento && temRede() && conta.tokenValido()) sincronizar({ motivo: 'fila' });
      },
      // O diário se bloqueou: nada fica marcado (as novas tentativas param até abrir de novo).
      bloqueou() {
        limparTemporizador();
      },
      parar() {
        parado = true;
        limparTemporizador();
      },
      retomar() {
        parado = false;
      },

      /* Primeiro passo num aparelho: há entrenos-sync.json no Drive? ('digitar' a senha dos outros
       * aparelhos) ou não ('criar' a senha). Pede o token. */
      async precisaDeSenha() {
        const arquivos = await drive.listar();
        return arquivos.some((a) => a.name === ARQUIVO_CONFIG) ? 'digitar' : 'criar';
      },

      /* Liga a sincronização neste aparelho com a senha. Sem entrenos-sync.json no Drive, cria (e, se
       * outro aparelho criou ao mesmo tempo, fica o mais antigo). Erro do núcleo (401 senha errada,
       * 429 espere, 409 igual ao PIN) sobe como veio. `extras` vão no corpo (mesmo_que_o_pin). */
      async ligarComSenha(senha, extras = {}) {
        const arquivos = await drive.listar();
        const config = await lerConfigDoDrive(arquivos);
        if (config) {
          await api('POST', '/sync/senha', { senha, sync_json: config.texto }, { semBloqueio: true });
          return { criou: false };
        }
        const r = await api('POST', '/sync/senha', { senha, ...extras }, { semBloqueio: true });
        const criado = await drive.criar(ARQUIVO_CONFIG, new TextEncoder().encode(r.sync_json), 'application/json');
        const depois = (await drive.listar()).filter((a) => a.name === ARQUIVO_CONFIG).sort(porCriacao);
        if (depois.length > 1 && depois[0].id !== criado.id) {
          // Outro aparelho criou ao mesmo tempo: vale o mais antigo; o nosso sai.
          try {
            const texto = await drive.baixarTexto(depois[0].id);
            await api('POST', '/sync/senha', { senha, sync_json: texto, substituir: true }, { semBloqueio: true });
          } finally {
            await drive.apagar(criado.id).catch(() => {});
          }
          return { criou: false };
        }
        return { criou: true };
      },

      /* "Parar" com "apagar também o arquivo deste aparelho no Drive": só o próprio arquivo. */
      async apagarProprio(nome) {
        if (!RE_ARQUIVO.test(String(nome || ''))) return 0;
        const meus = (await drive.listar()).filter((a) => a.name === nome);
        for (const a of meus) await drive.apagar(a.id);
        recusados.clear();
        return meus.length;
      },
    };
  }

  const motor = { ErroSync, MENSAGENS, ESCOPO, URL_AUTORIZAR, URL_REVOGAR, PAGINA_RETORNO, CANAL_OAUTH, ARQUIVO_CONFIG,
    AGRUPAR_MS, PALAVRAS, criarDrive, criarConta, criarSincronizador, gerarFrase, problemaDaSenha, bytesParaB64,
    b64ParaBytes };
  globalThis.EntreNosSincronizar = Object.freeze(motor);

  // ================================================================ a tela: Ajustes → ☁️ Sincronização e o selo ☁️

  const EN = globalThis.window && globalThis.window.EntreNos;
  if (!EN || typeof EN.registrarSecaoAjustes !== 'function') return;     // no teste do Node não há tela
  const { h, api } = EN;
  const u = EN.util;

  const conta = criarConta();
  const drive = criarDrive({ buscar: (...a) => globalThis.fetch(...a), token: () => conta.token() });
  const ouvintes = new Set();
  const sinc = criarSincronizador({
    api, drive, conta,
    aoMudar: (est) => {
      for (const f of [...ouvintes]) {
        try {
          f(est);
        } catch {
          // um desenho que falha não atrapalha os outros
        }
      }
    },
  });
  let ultimoEstado = null;     // o GET /api/sync/estado mais recente (sem segredo)
  let avisouSemToken = false;  // "Salvo neste aparelho..." aparece uma vez por abertura
  let aberto = false;          // o diário está aberto (entre desbloquear e bloquear)

  async function lerEstado() {
    ultimoEstado = await api('GET', '/sync/estado', undefined, { semBloqueio: true });
    conta.definirId(ultimoEstado && ultimoEstado.id_app);
    desenharSelo();
    return ultimoEstado;
  }

  function automatica() {
    return Boolean(ultimoEstado && ultimoEstado.configurada && ultimoEstado.automatica !== false);
  }

  function horaCurta(iso) {
    const t = Date.parse(iso || '');
    if (!Number.isFinite(t)) return '';
    const d = new Date(t);
    const hoje = new Date();
    const hora = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    return d.toDateString() === hoje.toDateString() ? hora : `${u.dataBr(u.isoDe(d))} ${hora}`;
  }

  // ---------------------------------------------------------------- o selo ☁️ (fora de Ajustes)
  //
  // Sem acesso ao Google (o normal depois de fechar o app ou de ele se bloquear), com algo para mandar,
  // um erro ou uma pergunta: um selo fixo no alto da tela diz isso e resolve num toque (22.2). Antes,
  // só Ajustes → ☁️ mostrava, e "Sincronizar sozinho" fazia acreditar que tudo já estava no Drive.
  const selo = h('div', { id: 'selo-sync', class: 'selo-sync', hidden: true });

  function irParaSecao() {
    EN.navegar('#ajustes');
    setTimeout(() => {
      const alvo = document.getElementById('sync-status');
      if (alvo) {
        if (typeof alvo.scrollIntoView === 'function') alvo.scrollIntoView({ block: 'center' });
        alvo.focus();
      }
    }, 400);
  }

  function entrarESincronizar(botao) {
    // A janela do Google abre AQUI, no toque (sem await antes).
    const promessa = conta.entrar({ dica: ultimoEstado && ultimoEstado.conta });
    if (botao) {
      botao.disabled = true;
      botao.textContent = '☁️ Conectando ao Google…';
    }
    desenharSelo();
    promessa.then(async () => {
      await sinc.sincronizar({ motivo: 'toque' });
      await lerEstado().catch(() => {});
    }).catch((e) => {
      if (e && e.tipo !== 'cancelado') u.avisar(e.message || MENSAGENS.desconhecido, 'erro');
    }).finally(() => desenharSelo());
  }

  function desenharSelo() {
    const est = ultimoEstado || {};
    const s = sinc.estado();
    u.limpar(selo);
    let mostrar = aberto && est.configurada;
    if (mostrar) {
      const perguntas = Number(est.conflitos) || 0;
      if (s.situacao === 'sincronizando' || conta.conectando()) {
        u.anexar(selo, h('span', { class: 'selo-sync-texto', role: 'status' }, s.situacao === 'sincronizando' ? '☁️ Sincronizando…' : '☁️ Conectando ao Google…'));
      } else if (perguntas > 0) {
        u.anexar(selo, h('button', { type: 'button', class: 'selo-sync-botao', onclick: irParaSecao },
          `☁️ ${perguntas === 1 ? '1 pergunta' : `${perguntas} perguntas`} da sincronização: qual versão fica?`));
      } else if (!conta.tokenValido()) {
        const quando = est.ultima ? `Não sincronizado desde ${horaCurta(est.ultima)}` : 'Ainda não sincronizado';
        const frase = s.pendente ? 'Salvo só neste aparelho' : quando;
        u.anexar(selo, h('button', { type: 'button', class: 'selo-sync-botao', onclick: (ev) => entrarESincronizar(ev.currentTarget) },
          `☁️ ${frase} · Sincronizar`));
      } else if (s.situacao === 'erro' || s.situacao === 'senha_mudou' || s.situacao === 'sem_senha') {
        u.anexar(selo, h('button', { type: 'button', class: 'selo-sync-botao', onclick: irParaSecao }, '☁️ A sincronização precisa de você · Ver'));
      } else if (s.situacao === 'na_fila') {
        u.anexar(selo, h('span', { class: 'selo-sync-texto', role: 'status' }, '☁️ Na fila: sincroniza quando a conexão voltar'));
      } else {
        mostrar = false;
      }
    }
    selo.hidden = !mostrar;
    if (document.body && document.body.classList) document.body.classList.toggle('com-selo-sync', mostrar);
    if (mostrar && !selo.isConnected && document.body) document.body.append(selo);
  }
  ouvintes.add(() => desenharSelo());
  setInterval(() => {
    if (aberto) desenharSelo();          // o token vence e "há N min" muda com a tela parada
  }, 60000);

  // Depois de salvar algo: agrupa e sincroniza (só com a sincronização ligada e automática).
  window.addEventListener('entrenos:gravou', () => {
    if (!ultimoEstado || !ultimoEstado.configurada) return;
    if (automatica()) sinc.depoisDeSalvar();
    if (!conta.tokenValido() && !avisouSemToken) {
      avisouSemToken = true;
      u.avisar('Salvo neste aparelho. Para mandar aos outros, toque em ☁️ Sincronizar, no alto da tela.');
    }
    desenharSelo();
  });
  // Ao abrir: o estado (para o selo) e, se o acesso ao Google ainda vale, sincroniza.
  window.addEventListener('entrenos:desbloqueado', () => {
    aberto = true;
    avisouSemToken = false;
    sinc.retomar();
    lerEstado().then((est) => {
      if (est && est.configurada && est.automatica !== false && conta.tokenValido()) {
        setTimeout(() => sinc.sincronizar({ motivo: 'abriu' }), 1500);
      }
    }).catch(() => {});
  });
  // Bloqueou: o token do Google é esquecido junto com o diário (22.2) e nada fica marcado.
  window.addEventListener('entrenos:bloqueado', () => {
    aberto = false;
    sinc.bloqueou();
    conta.cancelar();
    conta.esquecer();
    ultimoEstado = null;
    desenharSelo();
  });
  window.addEventListener('online', () => sinc.voltou());
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') sinc.voltou();
  });

  // ---------------------------------------------------------------- textos e pedaços da tela

  const CAMPOS_ITEM = {
    tipo: 'tipo', resumo: 'resumo', trecho: 'trecho', certeza: 'certeza', categorias: 'categorias',
    importancia: 'importância', data_evento: 'data', data_expressao: 'como a data foi dita', precisao: 'precisão da data',
    recorrente: 'repete todo ano', pessoa: 'sobre quem', alvo: 'do que se trata', polaridade: 'gosta ou não gosta',
    lembrar_em: 'lembrar em', status: 'situação',
  };
  const CAMPOS_PESSOA = { nome: 'nome', relacao: 'relação', apelidos: 'apelidos', observacoes: 'observações', aniversario: 'aniversário' };
  const SITUACOES = { ativo: 'ativo', resolvido: 'resolvido', mudou: 'mudou (não vale mais)', descartado: 'descartado' };
  const CERTEZAS = { confirmado: '🟢 confirmado', observado: '🟡 observado', inferencia: '🔵 inferência', incerto: '🔴 incerto' };

  function valorParaTexto(campo, valor) {
    if (valor == null || valor === '' || (Array.isArray(valor) && !valor.length)) return '(vazio)';
    if (Array.isArray(valor)) return valor.join(', ');
    if (typeof valor === 'boolean') return valor ? 'sim' : 'não';
    if (campo === 'status') return SITUACOES[valor] || String(valor);
    if (campo === 'certeza') return CERTEZAS[valor] || String(valor);
    if (campo === 'polaridade') return Number(valor) > 0 ? 'gosta / ajuda' : 'não gosta / não ajuda';
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(valor))) return u.dataBr(String(valor));
    return String(valor);
  }

  function haQuanto(iso) {
    const t = Date.parse(iso || '');
    if (!Number.isFinite(t)) return '';
    const min = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (min < 1) return 'agora há pouco';
    if (min < 60) return `há ${min} min`;
    const horas = Math.round(min / 60);
    if (horas < 24) return `há ${horas} h`;
    const dias = Math.round(horas / 24);
    return dias === 1 ? 'ontem' : `há ${dias} dias`;
  }

  function noIPhone() {
    const ua = String(navigator.userAgent || '');
    return /iPhone|iPad|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }

  function instalado() {
    return navigator.standalone === true || (typeof matchMedia === 'function' && matchMedia('(display-mode: standalone)').matches);
  }

  // Um nome para o aparelho, já preenchido ao ligar (a pessoa troca se quiser).
  function nomeSugerido(est) {
    const atual = est && est.nome;
    if (atual && atual !== 'Celular ou navegador' && atual !== 'Computador') return atual;
    const ua = String(navigator.userAgent || '');
    if (/iPad/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) return 'iPad';
    if (/iPhone/.test(ua)) return 'iPhone';
    if (/Android/.test(ua)) return /Mobile/.test(ua) ? 'Celular Android' : 'Tablet Android';
    return est && est.plataforma === 'computador' ? 'Computador' : 'Navegador do computador';
  }

  function campoSenha(id, extras = {}) {
    // Como o PIN: o navegador não oferece "Salvar senha?" (com o gerenciador de senhas sincronizando
    // pela conta do Google, a senha iria parar junto dos arquivos que ela protege).
    return h('input', Object.assign({
      id, type: 'password', autocomplete: 'one-time-code', autocapitalize: 'off', spellcheck: 'false',
      'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', 'data-form-type': 'other',
    }, extras));
  }

  function botaoCopiar(texto, rotulo = 'Copiar') {
    return h('button', {
      type: 'button', class: 'botao botao-secundario botao-pequeno',
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(texto);
          u.falar('Copiado.');
          u.avisar('Copiado.');
        } catch {
          u.avisar('Não deu para copiar daqui: selecione e copie à mão.', 'erro');
        }
      },
    }, rotulo);
  }

  function enderecoLocal() {
    return ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname);
  }

  // As origens para o Google: o site (se esta página é o site, em https) e o computador sempre por
  // localhost (o Google não aceita 127.0.0.1).
  function origensParaOGoogle() {
    const lista = [];
    if (location.protocol === 'https:') lista.push(location.origin);
    lista.push(`http://localhost:${(enderecoLocal() && location.port) || '8787'}`);
    return [...new Set(lista)];
  }

  function passoAPasso(aberto = false) {
    const origens = origensParaOGoogle();
    const noSite = location.protocol === 'https:';
    const linha = (texto) => h('li', null, h('code', null, texto), ' ', botaoCopiar(texto));
    return h('details', { class: 'detalhes-ia', open: aberto },
      h('summary', null, 'Passo a passo: criar o ID do app no Google Cloud (uma vez só, uns 10 minutos)'),
      h('p', null, 'O ID do app diz ao Google que é o SEU Entre Nós pedindo acesso. Ele não é segredo, é grátis e não dá acesso a nada sozinho. Você cria uma vez e usa o mesmo em todos os aparelhos.'),
      h('ol', { class: 'lista-passos' },
        h('li', null, 'Entre em console.cloud.google.com com a sua conta do Google e crie um projeto (por exemplo, “Entre Nós”).'),
        h('li', null, 'Em “APIs e serviços” → “Biblioteca”, procure “Google Drive API” e toque em “Ativar”.'),
        h('li', null, 'Em “Google Auth Platform” (ou “Tela de permissão OAuth”), configure: tipo de usuário “Externo”, o nome do app (“Entre Nós”) e o seu e-mail. Em “Público” (ou “Usuários de teste”), adicione o seu próprio e-mail.'),
        h('li', null, 'Em “Clientes” (ou “Credenciais” → “Criar credenciais”), crie um “ID do cliente OAuth” do tipo “Aplicativo da Web”.'),
        h('li', null, 'Em “Origens JavaScript autorizadas”, adicione, cada um numa linha, sem barra no fim:',
          h('ul', null, origens.map(linha),
            noSite ? null : h('li', null, 'e o endereço do site do Entre Nós no celular (por exemplo, https://entre-nos.pages.dev)'))),
        h('li', null, 'Em “URIs de redirecionamento autorizados”, adicione (é para onde o Google volta depois do login):',
          h('ul', null, origens.map((o) => linha(`${o}/${PAGINA_RETORNO}`)),
            noSite ? null : h('li', null, `e o endereço do site seguido de /${PAGINA_RETORNO} (por exemplo, https://entre-nos.pages.dev/${PAGINA_RETORNO})`))),
        h('li', null, 'Salve. Copie o “ID do cliente” (termina em .apps.googleusercontent.com) e cole aqui, em cada aparelho.')),
      h('p', { class: 'sutil' }, 'Na primeira vez, o Google avisa que o app “não foi verificado”: é o seu próprio app. Toque em “Continuar”. A permissão pedida é só a de guardar os dados do app numa pasta escondida do seu Drive: ele não vê seus outros arquivos.'),
      h('p', { class: 'sutil' }, 'Com o app “em teste”, o Google pode pedir a autorização de novo a cada 7 dias (e repetir o aviso). Se incomodar, em “Público” toque em “Publicar app”: como o Entre Nós só pede a pasta escondida do app, em geral o Google não exige verificação para isso.'),
      h('p', { class: 'sutil' }, 'Para levar o ID ao celular: em “Trocar o ID do app”, toque em “Copiar o ID” e mande para você mesmo (e-mail ou mensagem); no celular, copie e cole aqui.'));
  }

  function avisoIPhone() {
    if (!noIPhone()) return null;
    return h('p', { class: 'nota-cuidado' }, instalado()
      ? 'No iPhone, com o app instalado na Tela de Início, a janela de login do Google às vezes não volta para o app. Se isso acontecer, use o caminho manual: em “Levar ou trazer dados”, baixe uma cópia aqui e restaure (ou importe) no outro aparelho.'
      : 'No iPhone, a sincronização funciona melhor no Safari. Se o login do Google não voltar para o app, use o caminho manual: baixe uma cópia em “Levar ou trazer dados” e restaure (ou importe) no outro aparelho.');
  }

  function avisoEndereco() {
    // O Google não aceita 127.0.0.1 como origem autorizada: no computador, abra por localhost.
    if (location.hostname !== '127.0.0.1' && location.hostname !== '[::1]') return null;
    const certo = `http://localhost:${location.port || '8787'}/`;
    return h('p', { class: 'nota-cuidado' },
      'O Google não aceita o endereço 127.0.0.1. Para sincronizar neste computador, abra o Entre Nós por ',
      h('a', { href: certo }, certo), ' (é o mesmo Entre Nós; ele vai pedir o PIN de novo).');
  }

  // ---------------------------------------------------------------- a seção

  EN.registrarSecaoAjustes('sincronizacao', '☁️ Sincronização', (corpo, ctx) => {
    const painel = { vez: 0, atualizar: () => {} };
    const status = h('div', { id: 'sync-status', class: 'sync-status', 'aria-live': 'polite', tabindex: '-1' });
    const valendo = (vez) => vez === painel.vez && corpo.isConnected && ctx.atual();

    function ouvir(est) {
      if (!corpo.isConnected) {
        ouvintes.delete(ouvir);
        return;
      }
      painel.atualizar();
      if (est.situacao === 'ok' && est.resumo && est.resumo.conflitos) painel.desenhar();
    }
    ouvintes.add(ouvir);
    // O selo "Conectado como…" e o "há N min" mudam com o tempo: a cada minuto, enquanto a seção aparece.
    const relogioDaTela = setInterval(() => {
      if (!corpo.isConnected) {
        clearInterval(relogioDaTela);
        return;
      }
      painel.atualizar();
    }, 60000);

    function desenharStatus(est = sinc.estado()) {
      u.limpar(status);
      const e = ultimoEstado || {};
      const partes = [];
      if (est.situacao === 'sincronizando') partes.push(h('span', { class: 'chip chip-info' }, 'Sincronizando…'));
      else if (conta.conectando()) partes.push(h('span', { class: 'chip chip-info' }, 'Conectando ao Google…'));
      else if (conta.tokenValido()) partes.push(h('span', { class: 'chip chip-ligada' }, e.conta ? `Conectado como ${e.conta}` : 'Conectado ao Google'));
      else partes.push(h('span', { class: 'chip' }, e.conta ? `Não conectado agora (${e.conta})` : 'Não conectado ao Google'));
      const ultima = est.ultimaVez || e.ultima;
      if (ultima) partes.push(h('span', { class: 'sutil' }, `Última sincronização ${haQuanto(ultima)}`));
      u.anexar(status, h('p', { class: 'situacao-ia' }, partes));
      if (est.erro && est.situacao !== 'sincronizando') {
        u.anexar(status, h('p', { class: est.situacao === 'na_fila' ? 'nota-cuidado' : 'erro-campo', role: 'status' }, est.erro));
      }
      if (est.situacao === 'ok' && est.resumo) {
        const r = est.resumo;
        const coisas = [];
        if (r.registros_novos) coisas.push(`${r.registros_novos} ${r.registros_novos === 1 ? 'registro novo' : 'registros novos'}`);
        if (r.atualizados) coisas.push(`${r.atualizados} ${r.atualizados === 1 ? 'mudança' : 'mudanças'}`);
        if (r.apagados) coisas.push(`${r.apagados} ${r.apagados === 1 ? 'apagado' : 'apagados'}`);
        u.anexar(status, h('p', { class: 'sutil' }, coisas.length ? `Chegou dos outros aparelhos: ${coisas.join(', ')}.` : 'Tudo em dia.'));
        for (const aviso of est.avisos || []) u.anexar(status, h('p', { class: 'nota-cuidado' }, aviso));
      }
    }

    /* Abre a janela do Google NO TOQUE (a chamada vem antes de qualquer await) e mostra "Conectando…"
     * com um "Cancelar" enquanto ela não volta. Devolve true se conectou. */
    async function comLogin(botao) {
      let promessa;
      try {
        promessa = conta.entrar({ dica: ultimoEstado && ultimoEstado.conta });
      } catch (e) {
        promessa = Promise.reject(e);
      }
      const original = botao ? botao.textContent : '';
      if (botao) {
        botao.disabled = true;
        botao.textContent = 'Conectando ao Google…';
      }
      const cancelar = h('button', { type: 'button', class: 'botao botao-texto botao-pequeno', onclick: () => conta.cancelar() }, 'Cancelar');
      if (botao && botao.parentNode) botao.parentNode.appendChild(cancelar);
      desenharStatus();
      desenharSelo();
      try {
        await promessa;
        return true;
      } catch (e) {
        if (e && e.tipo !== 'cancelado') desenharStatus({ ...sinc.estado(), erro: e.message, situacao: 'erro' });
        else desenharStatus();
        return false;
      } finally {
        cancelar.remove();
        if (botao) {
          botao.disabled = false;
          botao.textContent = original;
        }
        desenharSelo();
      }
    }

    async function sincronizarAgora(botao) {
      if (!conta.tokenValido() && !(await comLogin(botao))) return;
      if (botao) {
        botao.disabled = true;
        botao.textContent = 'Sincronizando…';
      }
      try {
        await sinc.sincronizar({ motivo: 'toque' });
        await lerEstado().catch(() => {});
      } finally {
        if (botao) botao.disabled = false;
      }
      painel.desenhar({ focar: true });
    }

    async function conectar(botao) {
      if (!(await comLogin(botao))) return;
      try {
        const quem = await drive.conta().catch(() => null);
        if (quem) await api('PATCH', '/sync/ajustes', { conta: quem }, { semBloqueio: true });
      } catch {
        // o nome da conta é só para mostrar
      }
      await painel.desenhar({ mensagem: 'Conectado ao Google.', focar: true });
      const est = ultimoEstado || {};
      if (est.configurada) sinc.sincronizar({ motivo: 'conectou' });
    }

    function formIdApp(est) {
      const erro = h('p', { class: 'erro-campo', id: 'sync-id-erro', role: 'alert' });
      const campo = h('input', {
        id: 'sync-id-app', type: 'text', autocomplete: 'off', spellcheck: 'false', autocapitalize: 'off',
        inputmode: 'url', value: est.id_app || '', placeholder: '1234…-abc….apps.googleusercontent.com',
        'aria-describedby': 'sync-id-erro',
      });
      const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, est.id_app ? 'Salvar o ID' : 'Ligar a sincronização');
      const form = h('form', { novalidate: true, class: 'form-ia' },
        u.campo('ID do app (do Google Cloud)', campo, est.id_app
          ? 'Não é segredo. Use o mesmo em todos os seus aparelhos.'
          : 'Ainda não tem? Abra o passo a passo logo abaixo: uns 10 minutos, uma vez só. Depois use o mesmo ID em todos os aparelhos.'),
        erro, h('div', { class: 'acoes' }, botao, est.id_app ? botaoCopiar(est.id_app, 'Copiar o ID') : null));
      form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        erro.textContent = '';
        const valor = campo.value.trim();
        if (!/\.apps\.googleusercontent\.com$/.test(valor)) {
          erro.textContent = 'O ID do app termina em .apps.googleusercontent.com. Copie de novo do Google Cloud.';
          campo.focus();
          return;
        }
        botao.disabled = true;
        try {
          ultimoEstado = await api('PATCH', '/sync/ajustes', { id_app: valor }, { semBloqueio: true });
          conta.definirId(ultimoEstado && ultimoEstado.id_app);
          await painel.desenhar({ mensagem: 'ID do app salvo. Agora é só conectar ao Google.', focar: true });
        } catch (e) {
          if (e.bloqueio) return;
          erro.textContent = e.message;
          botao.disabled = false;
        }
      });
      return form;
    }

    function formSenha(modo, est) {
      const criar = modo === 'criar';
      const erro = h('p', { class: 'erro-campo', id: 'sync-senha-erro', role: 'alert' });
      const senha = campoSenha('sync-senha', { 'aria-describedby': 'sync-senha-erro sync-senha-dica' });
      const repetir = criar ? campoSenha('sync-senha-repetir') : null;
      const mostrar = h('input', {
        id: 'sync-senha-mostrar', type: 'checkbox',
        onchange: (e) => {
          senha.type = e.target.checked ? 'text' : 'password';
          if (repetir) repetir.type = senha.type;
        },
      });
      const nome = h('input', { id: 'sync-nome-ligar', type: 'text', maxlength: '40', autocomplete: 'off', value: nomeSugerido(est) });
      const primeiro = criar ? h('input', { id: 'sync-primeiro', type: 'checkbox' }) : null;
      const TEXTO_BOTAO = criar ? 'Criar a senha e sincronizar' : 'Usar esta senha';
      const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, TEXTO_BOTAO);
      let sugestao = criar ? gerarFrase() : null;
      const textoSugestao = h('code', { class: 'sync-frase' }, sugestao || '');
      const usar = h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno' }, 'Usar esta frase');
      const outra = h('button', { type: 'button', class: 'botao botao-texto botao-pequeno' }, 'Outra');
      usar.addEventListener('click', () => {
        senha.value = sugestao;
        if (repetir) repetir.value = sugestao;
        mostrar.checked = true;
        senha.type = 'text';
        if (repetir) repetir.type = 'text';
        u.falar('Frase colocada nos dois campos. Anote num lugar seguro.');
      });
      outra.addEventListener('click', () => {
        sugestao = gerarFrase();
        textoSugestao.textContent = sugestao;
      });
      // Copia a sugestão que está na tela (muda com "Outra").
      const copiarSugestao = h('button', {
        type: 'button', class: 'botao botao-texto botao-pequeno',
        onclick: async () => {
          try {
            await navigator.clipboard.writeText(sugestao);
            u.falar('Copiado.');
            u.avisar('Copiado. Guarde num lugar seguro (e não no navegador).');
          } catch {
            u.avisar('Não deu para copiar daqui: anote a frase à mão.', 'erro');
          }
        },
      }, 'Copiar');
      const conta_ = est.conta || 'a conta do Google conectada';
      const form = h('form', { novalidate: true, class: 'form-ia' },
        h('h3', null, criar ? 'Crie a senha de sincronização' : 'Digite a senha de sincronização'),
        criar
          ? h('div', { class: 'nota-importante', role: 'note' },
            h('p', null, `Não encontrei a sincronização no Google Drive de ${conta_}.`),
            h('p', null, 'Se você já ligou em outro aparelho, confira se entrou aqui com a MESMA conta do Google (se não, toque em “Desconectar do Google” e conecte com a outra). Criando uma senha agora, esta conta passa a ter uma sincronização separada.'))
          : null,
        h('p', null, criar
          ? 'Ela embaralha os arquivos antes de irem para o Google: sem ela, ninguém (nem o Google) lê. Você vai digitá-la uma vez em cada aparelho. Use uma frase só sua, diferente do PIN.'
          : 'Use a mesma senha que você criou no primeiro aparelho.'),
        criar ? h('div', { class: 'sync-sugestao' }, h('p', null, 'Sugestão (quatro palavras ao acaso e um número):'),
          h('p', null, textoSugestao), h('div', { class: 'acoes' }, usar, outra, copiarSugestao)) : null,
        u.campo('Senha de sincronização', senha),
        h('p', { class: 'dica', id: 'sync-senha-dica' }, criar
          ? 'Pelo menos 14 caracteres e 3 palavras diferentes (palavra ou nome só, sequências e frases feitas não valem). Não existe recuperação: anote num lugar seguro.'
          : 'Maiúsculas, acentos e espaços contam.'),
        repetir ? u.campo('Repita a senha', repetir) : null,
        h('label', { class: 'opcao-linha', for: 'sync-senha-mostrar' }, mostrar, ' Mostrar o que estou digitando'),
        u.campo('Nome deste aparelho', nome, 'Aparece nos outros aparelhos e nas perguntas (por exemplo, “iPhone” ou “Notebook”).'),
        primeiro ? h('label', { class: 'opcao-linha', for: 'sync-primeiro' }, primeiro, ' Este é o primeiro aparelho em que ligo a sincronização') : null,
        h('p', { class: 'sutil' }, 'Não salve esta senha no navegador.'),
        erro, h('div', { class: 'acoes' }, botao),
        criar ? null : h('details', { class: 'detalhes-ajuda' }, h('summary', null, 'Esqueci a senha de sincronização'),
          h('p', null, 'A senha não fica guardada em lugar nenhum. Para começar de novo com outra:'),
          h('ol', null,
            h('li', null, 'Em cada aparelho, toque em “Parar de sincronizar neste aparelho” (os registros de cada um continuam).'),
            h('li', null, 'No computador, abra drive.google.com → ⚙️ Configurações → Gerenciar apps → Entre Nós → Opções → “Excluir dados de apps ocultos”.'),
            h('li', null, 'Ligue a sincronização de novo e crie uma senha nova; depois, nos outros aparelhos, digite a nova.'))));
      form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        if (u.esperando(botao)) return;
        erro.textContent = '';
        const valor = senha.value;
        const problema = criar ? problemaDaSenha(valor) : (valor ? null : 'Digite a senha de sincronização.');
        if (problema) {
          erro.textContent = problema;
          senha.focus();
          return;
        }
        if (criar && valor !== repetir.value) {
          erro.textContent = 'As duas senhas não são iguais. Digite de novo com calma.';
          repetir.focus();
          return;
        }
        if (primeiro && !primeiro.checked) {
          erro.textContent = 'Confirme que este é o primeiro aparelho (senão, entre com a mesma conta do Google do outro aparelho).';
          primeiro.focus();
          return;
        }
        botao.disabled = true;
        botao.textContent = 'Conferindo…';
        let espera = 0;
        let extras = {};
        for (let tentativa = 0; tentativa < 2; tentativa += 1) {
          try {
            await sinc.ligarComSenha(valor, extras);
            form.reset();
            if (nome.value.trim()) await api('PATCH', '/sync/ajustes', { nome: nome.value.trim() }, { semBloqueio: true }).catch(() => {});
            await lerEstado();
            await painel.desenhar({ mensagem: 'Sincronização ligada neste aparelho.', focar: true });
            sinc.sincronizar({ motivo: 'ligou' });
            return;
          } catch (e) {
            if (e.bloqueio) return;
            if (e.status === 409 && e.dados && e.dados.igual_ao_pin && !extras.mesmo_que_o_pin) {
              // A senha é o PIN deste diário: avisa com destaque antes (decisão do usuário: pode ser igual).
              const ok = await u.confirmar({
                titulo: 'Usar o PIN como senha de sincronização?', texto: e.message,
                ok: 'Usar mesmo assim', cancelar: 'Escolher outra', perigo: true,
              });
              if (ok) {
                extras = { mesmo_que_o_pin: true };
                continue;
              }
              erro.textContent = 'Escolha outra frase (a sugerida aqui serve).';
            } else {
              erro.textContent = e.message || MENSAGENS.desconhecido;
              espera = u.segundosDeEspera(e);
            }
            senha.value = '';
            if (repetir) repetir.value = '';
            break;
          }
        }
        botao.disabled = false;
        botao.textContent = TEXTO_BOTAO;
        if (espera && form.isConnected) u.contarEspera(botao, espera);
        senha.focus();
      });
      return form;
    }

    function listaAparelhos(est) {
      const itens = [h('li', null, h('strong', null, est.nome || 'Este aparelho'), ' (este)')];
      for (const a of est.aparelhos || []) {
        itens.push(h('li', null, a.nome || 'Outro aparelho', a.gerado_em ? h('span', { class: 'sutil' }, ` · atualizado ${haQuanto(a.gerado_em)}`) : null));
      }
      return h('div', null, h('h3', null, 'Aparelhos'), h('ul', { class: 'lista-simples sync-aparelhos' }, itens));
    }

    function formNome(est) {
      const campo = h('input', { id: 'sync-nome', type: 'text', maxlength: '40', autocomplete: 'off', value: est.nome || '' });
      const form = h('form', { novalidate: true, class: 'form-ia' },
        h('div', { class: 'linha-modelo' }, u.campo('Nome deste aparelho', campo),
          h('button', { type: 'submit', class: 'botao botao-secundario botao-pequeno' }, 'Salvar')));
      form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        try {
          ultimoEstado = await api('PATCH', '/sync/ajustes', { nome: campo.value }, { semBloqueio: true });
          u.avisar('Nome salvo. Os outros aparelhos veem na próxima sincronização.');
        } catch (e) {
          if (!e.bloqueio) u.avisarErro(e);
        }
      });
      return form;
    }

    function opcaoAutomatica(est) {
      const caixa = h('input', {
        id: 'sync-automatica', type: 'checkbox', checked: est.automatica !== false,
        onchange: async (ev) => {
          try {
            ultimoEstado = await api('PATCH', '/sync/ajustes', { automatica: ev.target.checked }, { semBloqueio: true });
            u.falar('Preferência salva.');
          } catch (e) {
            if (!e.bloqueio) u.avisarErro(e);
          }
        },
      });
      return h('div', null,
        h('label', { class: 'opcao-linha', for: 'sync-automatica' }, caixa,
          ' Sincronizar sozinho enquanto o acesso ao Google valer (cerca de 1 hora): ao abrir e uns 30 segundos depois de salvar'),
        h('p', { class: 'sutil' }, 'Depois de fechar o app ou de ele se bloquear, o acesso ao Google é esquecido: toque em ☁️ Sincronizar, no alto da tela, para mandar de novo.'));
    }

    async function blocoPerguntas() {
      let perguntas = [];
      try {
        perguntas = await api('GET', '/sync/conflitos', undefined, { semBloqueio: true });
      } catch {
        return null;
      }
      if (!Array.isArray(perguntas) || !perguntas.length) return null;
      const caixa = h('div', { class: 'sync-perguntas' },
        h('h3', null, perguntas.length === 1 ? '1 pergunta' : `${perguntas.length} perguntas`),
        h('p', null, 'Algo mudou em dois aparelhos quase ao mesmo tempo, e não dá para saber qual mudança veio depois. Qual versão fica? A escolhida vai para todos os aparelhos.'));
      for (const p of perguntas) {
        const nomes = p.tipo === 'item' ? CAMPOS_ITEM : CAMPOS_PESSOA;
        const idTitulo = `sync-pergunta-${p.id}`;
        const quando = p.contexto && p.contexto.data_registro ? ` (registro de ${u.dataBr(p.contexto.data_registro)})` : '';
        const titulo = p.tipo === 'item'
          ? (p.discreto
            ? `🔒 Uma informação reservada${quando}: abra o registro para ver`
            : `Informação${p.resumo ? `: ${u.encurtar(p.resumo, 80)}` : ''}${quando}`)
          : `Pessoa: ${p.resumo || 'sem nome'}`;
        const outroNome = p.nome_aparelho || 'outro aparelho';
        const resolver = async (fica, botao) => {
          botao.disabled = true;
          try {
            await api('POST', `/sync/conflitos/${p.id}`, { fica }, { semBloqueio: true });
            u.avisar('Pronto. Vale para todos os aparelhos na próxima sincronização.');
            if (automatica()) sinc.depoisDeSalvar();
            await lerEstado().catch(() => {});
            painel.desenhar({ focar: true });
          } catch (e) {
            if (!e.bloqueio) u.avisarErro(e);
            botao.disabled = false;
          }
        };
        const este = h('button', { type: 'button', class: 'botao botao-secundario', 'aria-describedby': idTitulo }, 'Fica a versão deste aparelho');
        const outro = h('button', { type: 'button', class: 'botao botao-secundario', 'aria-describedby': idTitulo }, `Fica a versão de: ${outroNome}`);
        este.addEventListener('click', () => resolver('este', este));
        outro.addEventListener('click', () => resolver('outro', outro));
        const valor = (c, lado) => (c.reservado ? '(reservado: abra o registro para ver)' : valorParaTexto(c.campo, lado));
        u.anexar(caixa, h('div', { class: 'cartao-ia sync-conflito' },
          h('p', { class: 'rotulo-salva', id: idTitulo }, titulo),
          p.discreto && p.contexto && p.contexto.registro_id ? h('p', null, u.linkRegistro(p.contexto.registro_id, 'Abrir o registro')) : null,
          h('dl', { class: 'sync-versoes' }, (p.campos || []).map((c) => [
            h('dt', null, u.capitalizar(nomes[c.campo] || c.campo)),
            h('dd', null, h('span', { class: 'sutil' }, 'Aqui: '), valor(c, c.aqui)),
            h('dd', null, h('span', { class: 'sutil' }, `Em ${outroNome}: `), valor(c, c.la))])),
          h('div', { class: 'acoes' }, este, outro)));
      }
      return caixa;
    }

    painel.desenhar = async (opcoes = {}) => {
      painel.vez += 1;
      const vez = painel.vez;
      u.limpar(corpo);
      u.anexar(corpo, u.carregando());
      let est;
      try {
        est = await lerEstado();
      } catch (e) {
        if (e.bloqueio || !valendo(vez)) return;
        u.limpar(corpo);
        u.anexar(corpo, h('p', { class: 'erro-campo' }, e.message), h('button', {
          type: 'button', class: 'botao botao-secundario', onclick: () => painel.desenhar(),
        }, 'Tentar de novo'));
        return;
      }
      if (!valendo(vez)) return;
      const intro = h('div', { class: 'intro-ia' },
        h('p', null, 'Opcional. Com ela ligada, cada aparelho (o computador, o celular) guarda no SEU Google Drive uma cópia embaralhada com a senha de sincronização, e junta o que os outros escreveram. O Google só vê arquivos embaralhados, numa pasta escondida do app.'),
        h('details', { class: 'detalhes-ia' },
          h('summary', null, 'Como funciona'),
          h('ul', null,
            h('li', null, 'Registros novos de cada aparelho aparecem nos outros, sem duplicar; o 🔒 e os dados do ciclo e da área Eu vão junto, sempre embaralhados.'),
            h('li', null, 'O que você apaga num aparelho some dos outros e não volta (também o que for apagado com a sincronização parada, se você ligar de novo com a mesma senha).'),
            h('li', null, 'Se a mesma informação mudar em dois aparelhos, vale a mudança mais recente; se mudou quase ao mesmo tempo, o Entre Nós pergunta qual fica (o selo ☁️ no alto da tela avisa).'),
            h('li', null, 'Sincroniza sozinho enquanto o acesso ao Google valer (cerca de 1 hora): ao abrir, uns 30 segundos depois de salvar e quando você toca em “Sincronizar agora”. Sem internet, fica na fila.'),
            h('li', null, 'O acesso ao Google é esquecido quando o Entre Nós se bloqueia ou é fechado; aí o selo ☁️ no alto da tela pede um toque para entrar de novo (é um limite do Google para apps sem servidor).'),
            h('li', null, 'Nenhum código do Google roda dentro do Entre Nós: o login é uma janela do Google que volta para cá. No Drive, os arquivos têm o tamanho arredondado, e as versões antigas do arquivo de cada aparelho são apagadas.'))));
      const servico = h('select', { id: 'sync-servico', value: 'google_drive' },
        h('option', { value: 'google_drive' }, 'Google Drive'),
        h('option', { value: 'dropbox', disabled: true }, 'Dropbox (em breve)'),
        h('option', { value: 'onedrive', disabled: true }, 'OneDrive (em breve)'));
      const botoes = h('div', { class: 'acoes sync-botoes' });
      const extra = h('div', { class: 'sync-extra' });
      u.limpar(corpo);
      u.anexar(corpo,
        opcoes.mensagem ? h('p', { class: 'nota-cuidado mensagem-ia', role: 'status' }, opcoes.mensagem) : null,
        intro, u.campo('Serviço', servico), avisoEndereco(), avisoIPhone(), status, botoes, extra);
      desenharStatus();

      function desenharBotoes() {
        u.limpar(botoes);
        if (!est.id_app) return;
        if (!conta.tokenValido()) {
          // O mesmo nome no texto do status e no botão (MENSAGENS.sem_token).
          const rotulo = est.configurada ? 'Sincronizar agora' : 'Conectar ao Google';
          const entrar_ = h('button', { type: 'button', class: 'botao botao-primario' }, rotulo);
          entrar_.addEventListener('click', () => (est.configurada ? sincronizarAgora(entrar_) : conectar(entrar_)));
          u.anexar(botoes, entrar_);
          return;
        }
        if (est.configurada) {
          const agora = h('button', { type: 'button', class: 'botao botao-primario' }, 'Sincronizar agora');
          agora.addEventListener('click', () => sincronizarAgora(agora));
          u.anexar(botoes, agora);
        }
        const sair = h('button', { type: 'button', class: 'botao botao-secundario' }, 'Desconectar do Google');
        sair.addEventListener('click', async () => {
          conta.revogar();
          try {
            ultimoEstado = await api('PATCH', '/sync/ajustes', { conta: null }, { semBloqueio: true });
          } catch {
            // só o nome da conta
          }
          painel.desenhar({ focar: true, mensagem: 'Desconectado do Google neste aparelho. A senha de sincronização continua guardada aqui: para voltar, é só conectar de novo.' });
        });
        u.anexar(botoes, sair);
      }
      desenharBotoes();
      painel.atualizar = () => {
        if (!valendo(vez)) return;
        desenharStatus();
        if (!conta.conectando()) desenharBotoes();
      };

      if (!est.id_app) {
        u.anexar(extra, formIdApp(est), passoAPasso(true));
      } else if (!est.configurada) {
        if (conta.tokenValido()) {
          u.anexar(extra, u.carregando('Procurando a sincronização no seu Google Drive…'));
          try {
            const modo = await sinc.precisaDeSenha();
            if (!valendo(vez)) return;
            u.limpar(extra);
            const form = formSenha(modo, est);
            // Acabou de parar: o formulário fica recolhido (ligar de novo é escolha da pessoa).
            u.anexar(extra, est.religavel && !opcoes.abrirSenha
              ? h('details', { class: 'detalhes-ajuda' }, h('summary', null, 'Quer ligar de novo? Digite a senha'), form)
              : form);
          } catch (e) {
            if (!valendo(vez)) return;
            u.limpar(extra);
            u.anexar(extra, h('p', { class: 'erro-campo', role: 'alert' }, e.message));
          }
        } else {
          u.anexar(extra, h('p', null, est.religavel
            ? 'A sincronização está parada neste aparelho. Para ligar de novo, conecte ao Google e digite a senha de sincronização.'
            : 'Conecte ao Google para criar (ou digitar) a senha de sincronização.'));
        }
        u.anexar(extra, h('details', { class: 'detalhes-ajuda' }, h('summary', null, 'Trocar o ID do app'), formIdApp(est)), passoAPasso());
      } else {
        const sincronizacao = sinc.estado();
        if (sincronizacao.situacao === 'senha_mudou') u.anexar(extra, formSenhaDeNovo());
        const perguntas = await blocoPerguntas();
        if (!valendo(vez)) return;
        u.anexar(extra, perguntas, opcaoAutomatica(est), listaAparelhos(est), formNome(est),
          h('details', { class: 'detalhes-ajuda' }, h('summary', null, 'Trocar o ID do app'), formIdApp(est)),
          passoAPasso(), blocoParar(est));
      }
      if (opcoes.focar) {
        // Depois de conectar, ligar, responder, desconectar ou parar: o foco vai para a situação (o
        // botão tocado sumiu com o redesenho), em vez de cair no começo da página.
        status.focus();
      }
    };

    function formSenhaDeNovo() {
      // A senha mudou em outro aparelho: começa de novo aqui com a senha nova (os dados ficam).
      const erro = h('p', { class: 'erro-campo', id: 'sync-nova-erro', role: 'alert' });
      const senha = campoSenha('sync-senha-nova', { 'aria-describedby': 'sync-nova-erro' });
      const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Usar a senha nova');
      const form = h('form', { novalidate: true, class: 'form-ia' }, u.campo('Senha de sincronização (a nova)', senha), erro,
        h('div', { class: 'acoes' }, botao));
      form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        if (u.esperando(botao)) return;
        erro.textContent = '';
        botao.disabled = true;
        let espera = 0;
        try {
          const arquivos = await drive.listar();
          const config = arquivos.filter((a) => a.name === ARQUIVO_CONFIG).sort(porCriacao)[0];
          if (!config) throw new Error(MENSAGENS.sem_senha);
          const texto = await drive.baixarTexto(config.id);
          await api('POST', '/sync/senha', { senha: senha.value, sync_json: texto, substituir: true }, { semBloqueio: true });
          form.reset();
          await painel.desenhar({ mensagem: 'Senha nova guardada neste aparelho.', focar: true });
          sinc.sincronizar({ motivo: 'senha' });
          return;
        } catch (e) {
          if (e.bloqueio) return;
          senha.value = '';
          erro.textContent = e.message;
          espera = u.segundosDeEspera(e);
        }
        botao.disabled = false;
        if (espera && form.isConnected) u.contarEspera(botao, espera);
      });
      return form;
    }

    function blocoParar(est) {
      const conectado = conta.tokenValido();
      const apagarNoDrive = h('input', { id: 'sync-apagar-proprio', type: 'checkbox', checked: conectado, disabled: !conectado });
      const botao = h('button', { type: 'button', class: 'botao botao-perigo' }, 'Parar de sincronizar neste aparelho');
      botao.addEventListener('click', async () => {
        const apagar = apagarNoDrive.checked && conta.tokenValido();
        const ok = await u.confirmar({
          titulo: 'Parar de sincronizar neste aparelho?',
          texto: `A senha de sincronização sai deste aparelho e ele para de mandar e receber. Seus registros daqui continuam, e os outros aparelhos continuam sincronizando entre si. ${apagar
            ? 'O arquivo deste aparelho no Google Drive vai ser apagado.'
            : 'O arquivo deste aparelho fica no Google Drive (embaralhado); se você ligar de novo com a mesma senha, ele é reescrito.'}`,
          ok: 'Parar', perigo: true,
        });
        if (!ok) return;
        botao.disabled = true;
        try {
          sinc.parar();
          let apagou = 0;
          if (apagar) {
            try {
              apagou = await sinc.apagarProprio(est.arquivo);
            } catch (e) {
              u.avisar(`Não consegui apagar o arquivo deste aparelho no Drive agora (${e.message}). A sincronização vai parar mesmo assim.`, 'erro');
            }
          }
          ultimoEstado = await api('POST', '/sync/desligar', {}, { semBloqueio: true });
          // Parou: o acesso ao Google sai daqui (a tela não insiste em pedir a senha de novo).
          conta.revogar();
          sinc.retomar();
          painel.desenhar({ focar: true, mensagem: apagou
            ? 'A sincronização foi desligada neste aparelho, e o arquivo dele saiu do Google Drive.'
            : 'A sincronização foi desligada neste aparelho.' });
        } catch (e) {
          sinc.retomar();
          botao.disabled = false;
          if (!e.bloqueio) u.avisarErro(e);
        }
      });
      return h('div', { class: 'sync-parar' },
        h('label', { class: 'opcao-linha', for: 'sync-apagar-proprio' }, apagarNoDrive,
          ' Apagar também o arquivo deste aparelho no Google Drive'),
        conectado ? null : h('p', { class: 'sutil' }, 'Para apagar o arquivo deste aparelho no Drive, conecte ao Google antes de parar.'),
        h('div', { class: 'acoes' }, botao));
    }

    painel.desenhar();
    return null;
  });
})();
