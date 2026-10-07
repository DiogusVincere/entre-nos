/* Entre Nós: interface do navegador (SPA em JavaScript puro, sem framework).
 *
 * Conversa só com a API local descrita no DESIGN.md (seções 15 e 17). Todo texto que vem do
 * usuário ou do servidor entra na página como nó de texto (função h), nunca como HTML.
 * Nada de script ou estilo inline: a política de segurança (CSP) do servidor proíbe.
 *
 * IA sob demanda (seção 17): as regras são sempre o motor. A IA só roda pelos botões ✨
 * (revisão, busca e Hoje), que só aparecem com uma IA configurada. A área Ajustes → IA pede o
 * PIN da IA; o que vem de /api/ia/config (serviço, URL, modelo, final da chave) fica na página
 * só enquanto essa área está aberta. A chave digitada nunca é guardada pela página.
 *
 * Levar ou trazer dados (seção 18): restaurar um backup (.cofre) no primeiro uso ou em Ajustes, e
 * importar um JSON exportado (só junta). O arquivo é lido no navegador (FileReader) e vai no corpo
 * do pedido (base64 ou texto); a página não guarda o conteúdo além do necessário.
 */
'use strict';

(() => {
  // ================================================================ constantes

  const CATEGORIAS = [
    'Família', 'Amigos', 'Trabalho', 'Estudos', 'Saúde', 'Viagens',
    'Datas importantes', 'Preferências', 'Comidas', 'Bebidas', 'Filmes',
    'Séries', 'Música', 'Livros', 'Hobbies', 'Lugares', 'Presentes', 'Sonhos',
    'Objetivos', 'Medos', 'Preocupações', 'Coisas que deixam feliz',
    'Coisas que incomodam', 'Experiências importantes', 'Infância',
    'Relacionamento', 'Conversas importantes', 'Situações pendentes',
    'Eventos futuros', 'Pessoas importantes', 'Aniversários',
    'Memórias compartilhadas',
  ];

  const CERTEZAS = {
    confirmado: { emoji: '🟢', nome: 'Confirmado', explica: 'ela disse, com as palavras dela' },
    observado: { emoji: '🟡', nome: 'Observado', explica: 'você viu ou percebeu' },
    inferencia: { emoji: '🔵', nome: 'Inferência', explica: 'hipótese, ainda sem confirmação' },
    incerto: { emoji: '🔴', nome: 'Incerto', explica: 'antigo ou contraditório; vale confirmar' },
  };
  const CICLO_CERTEZA = ['confirmado', 'observado', 'inferencia'];

  const TIPOS = {
    fato: ['ℹ️', 'Informação'],
    preferencia: ['💚', 'Preferência'],
    restricao: ['⚠️', 'Restrição'],
    evento: ['📌', 'Acontecimento'],
    data_importante: ['🎂', 'Data importante'],
    estado: ['🌦️', 'Como ela está'],
    padrao: ['🔁', 'Costuma acontecer'],
    apoio: ['🤝', 'O que ajuda'],
    pendencia: ['⏳', 'Assunto em aberto'],
    desejo: ['✨', 'Desejo'],
    pessoa_info: ['👥', 'Sobre alguém'],
    nota: ['📝', 'Anotação'],
  };
  const TIPOS_COM_DATA = new Set(['evento', 'data_importante', 'pendencia']);

  const NIVEIS = [
    { id: 'importante', emoji: '🔴', nome: 'Importante' },
    { id: 'lembrete', emoji: '🟡', nome: 'Lembretes' },
    { id: 'sugestao', emoji: '💙', nome: 'Sugestão' },
  ];

  const STATUS_ITEM = {
    ativo: 'Ativo',
    resolvido: 'Resolvido',
    mudou: 'Mudou (não vale mais)',
    descartado: 'Descartado',
  };
  const IMPORTANCIAS = { 1: 'Baixa', 2: 'Normal', 3: 'Alta' };

  const TIPOS_RELACAO = {
    contexto: ['🔗', 'Isso se relaciona com algo que você já registrou'],
    contradicao: ['⚖️', 'Possível mudança ou contradição'],
    mesma_pessoa: ['👥', 'Essa pessoa já apareceu antes'],
  };
  const STATUS_RELACAO = { pendente: 'Ainda sem decisão', confirmada: 'Confirmada', descartada: 'Descartada' };

  const GRUPOS_PREFERENCIA = [
    ['gosta', '💚', 'Gosta', null],
    ['nao_gosta', '🚫', 'Não gosta', null],
    ['restricoes', '⚠️', 'Restrições', 'alergias e o que ela não pode'],
    ['comentou_uma_vez', '💬', 'Comentou uma vez', 'ainda não dá para dizer que ela gosta'],
    ['incertos', '🔴', 'Vale confirmar', 'antigo ou contraditório'],
  ];
  const GRUPOS_COMO_ESTA = [
    ['situacao_atual', '🌦️', 'Como ela anda (últimos 14 dias)'],
    ['o_que_ajuda', '🤝', 'O que costuma ajudar'],
    ['o_que_nao_gosta', '🙅', 'O que ela não quer nesses momentos'],
    ['ultimos_acontecimentos', '📌', 'Últimos acontecimentos'],
  ];
  const ABAS_ELA = [
    { id: 'pessoas', emoji: '👥', nome: 'Pessoas' },
    { id: 'preferencias', emoji: '💚', nome: 'Preferências' },
    { id: 'como', emoji: '🌦️', nome: 'Como ela está' },
  ];

  const ICONES_DATA = {
    aniversario: '🎂', aniversario_dela: '🎂', data_importante: '🎂', evento: '📌',
    namoro: '💑', mesversario: '💑', pendencia: '⏳', feriado: '🎉',
  };

  const RELACOES_SUGERIDAS = [
    'mãe', 'pai', 'irmã', 'irmão', 'avó', 'avô', 'tia', 'tio', 'prima', 'primo', 'sobrinha',
    'sobrinho', 'madrinha', 'padrinho', 'cunhada', 'cunhado', 'sogra', 'filha', 'filho',
    'madrasta', 'padrasto', 'melhor amiga', 'melhor amigo', 'amiga', 'amigo', 'chefe', 'gerente',
    'colega de trabalho', 'professora', 'professor', 'orientadora', 'orientador', 'terapeuta',
    'psicóloga', 'médica', 'médico', 'ex', 'pet',
  ];
  const HUMORES = ['feliz', 'animada', 'tranquila', 'empolgada', 'cansada', 'preocupada',
    'ansiosa', 'nervosa', 'triste', 'chateada', 'estressada'];

  const EXEMPLOS_BUSCA = [
    'O que eu sei sobre a família dela?',
    'Qual é a comida que ela mais gosta?',
    'O que ela gosta de fazer quando está estressada?',
    'Quais são as pessoas mais importantes para ela?',
    'Ela já falou alguma coisa sobre presentes?',
    'O que ela falou sobre a viagem?',
    'Quando ela comentou sobre o trabalho?',
  ];

  const ANTECEDENCIAS_COMUNS = [60, 30, 15, 7, 3, 2, 1];
  const MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
    'setembro', 'outubro', 'novembro', 'dezembro'];
  const MESES_CURTOS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  const DIAS_SEMANA = ['domingo', 'segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira',
    'sexta-feira', 'sábado'];
  const DIAS_CURTOS = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];

  const PIN_MINIMO = 6;
  const POR_PAGINA = 30;
  const VAZIO = 'Ainda não há nada aqui. Que tal registrar algo que ela te contou hoje?';
  const CHAVE_TOKEN = 'entrenos.token';
  const CHAVE_TEMA = 'entrenos.tema';
  const MENSAGENS_HTTP = {
    400: 'Algo no pedido não estava certo.',
    401: 'É preciso desbloquear de novo.',
    403: 'Pedido recusado por segurança.',
    404: 'Não encontrei isso. Talvez já tenha sido apagado.',
    409: 'Isso entra em conflito com o que já existe.',
    413: 'O texto ficou grande demais.',
    423: 'O Entre Nós está bloqueado.',
    429: 'Espere um pouco antes de tentar o PIN de novo.',
    500: 'O Entre Nós teve um problema. Tente de novo.',
    502: 'A IA não respondeu direito. Tente de novo em instantes.',
    503: 'Essa parte do Entre Nós não está disponível agora.',
    504: 'A IA demorou demais para responder.',
  };

  // ---------------------------------------------------------------- IA (seção 17)

  const MINUTOS_AREA_IA = 10;          // a área Ajustes → IA fecha sozinha (no servidor também)
  const TEMPO_IA_MINIMO = 5;
  const TEMPO_IA_MAXIMO = 600;
  const TEMPO_IA_PADRAO = 120;
  const NOMES_CAMPOS_IA = {
    tipo: 'tipo', resumo: 'resumo', certeza: 'certeza', categorias: 'categorias', data_evento: 'data',
    precisao: 'precisão da data', pessoa: 'sobre quem', alvo: 'do que se trata', polaridade: 'gosta ou não gosta',
    importancia: 'importância', recorrente: 'repete todo ano',
  };
  const NOMES_FORMATO_IA = {
    json_schema: 'JSON com esquema', json_object: 'JSON simples', texto: 'texto com JSON dentro',
    esquema: 'JSON com esquema', json: 'JSON simples',
  };
  const QUEM_RESPONDEU = { principal: 'a IA principal', reserva: 'a IA de reserva' };

  // ---------------------------------------------------------------- backup, restaurar, importar (seção 18)

  const LIMITE_ENVIO_ARQUIVO = 64 * 1024 * 1024;   // /api/restaurar e /api/importar aceitam até 64 MB
  const MAIOR_COFRE = 47 * 1024 * 1024;            // em base64 o arquivo cresce 4/3 e ainda vão os PINs
  const MAIOR_JSON = LIMITE_ENVIO_ARQUIVO;         // o tamanho exato do pedido é conferido em api()
  const MENSAGEM_COFRE_GRANDE = 'Esse arquivo é grande demais para ser um backup do Entre Nós (o limite é de 47 MB). Confira se escolheu o arquivo .cofre certo.';
  const MENSAGEM_JSON_GRANDE = 'Esse arquivo é grande demais para importar de uma vez (o limite é de 64 MB).';
  const MENSAGEM_NAO_E_COFRE = 'Esse arquivo não é um backup do Entre Nós que dê para abrir. Confira se escolheu o arquivo .cofre certo.';
  const MENSAGEM_NAO_E_EXPORTACAO = 'Esse arquivo não é uma exportação do Entre Nós que dê para juntar. Use o arquivo gerado em “Exportar (JSON)”.';
  const MENSAGEM_PIN_DO_BACKUP = 'Esse PIN não abre este backup.';      // a mesma de servidor.PIN_DO_BACKUP

  // ================================================================ estado

  const estado = {
    modo: 'inicio',          // inicio | portao | app
    servidor: null,          // resposta de GET /api/estado
    config: null,            // resposta de GET /api/config
    vez: 0,                  // cada desenho de tela ganha um número; respostas atrasadas são ignoradas
    atalho: null,            // o que o Ctrl+Enter faz na tela atual
    rascunho: rascunhoVazio(),
    revisao: null,           // proposta em revisão (tela Registrar)
    salvando: false,
    busca: null,             // última pergunta e resposta da busca
    minutosBloqueio: null,   // guardado ao bloquear (os ajustes saem da memória)
    ultimaAtividade: Date.now(),
    ultimaRequisicao: Date.now(),
    iaConfig: null,          // resposta de GET /api/ia/config: só enquanto a área da IA está aberta na tela
    iaAreaAte: 0,            // quando a área da IA se fecha sozinha (Date.now() + 10 min)
    // Registrar aberto por um recurso (EntreNos.abrirRegistrar, seção 20.10): quem avisar quando ESTE
    // rascunho for salvo (lista de funções), organizar assim que o formulário aparecer, cursor no fim.
    aoSalvar: null,
    organizarAoAbrir: false,
    cursorNoFim: false,
  };
  let tokenMemoria = null;
  let contadorIds = 0;
  let painelIA = null;       // a área Ajustes → IA desenhada agora (para fechar sozinha)
  // Conteúdo de arquivo que precisa esperar um passo (o texto do JSON entre a prévia e o "Juntar"):
  // cada tela que guarda algo assim registra aqui como esquecer, e trocar de tela ou bloquear esquece.
  const esquecedoresArquivo = new Set();

  // ---------------------------------------------------------------- recursos (seção 20.0)
  // Cada recurso tem web/telas/<nome>.js, carregado depois deste arquivo, que se encaixa pelas
  // funções de window.EntreNos (no fim deste arquivo). Este arquivo não muda quando um recurso entra.
  const ROTAS_DO_NUCLEO = new Set(['hoje', 'registrar', 'ela', 'datas', 'buscar', 'registros', 'ajustes']);
  const ABAS_DO_NUCLEO = new Set(['pessoas', 'preferencias', 'como']);
  const RE_ID_RECURSO = /^[a-z][a-z0-9_-]{0,39}$/;
  const cartoesHoje = new Map();      // tipo do cartão (ou NOME do recurso) -> render(cartao, ctx) -> Node
  const abasEla = [];                 // {id, emoji, nome, render(painel, ctx)}
  const secoesAjustes = [];           // {id, emoji, nome, render(corpo, ctx)}
  const revisaoExtras = new Map();    // tipo do extra (ou NOME do recurso) -> render(extra, ctx) -> Node
  const revisaoOpcionais = new Set(); // extras só informativos: salvar sem decidir não pede confirmação
  const menusRecursos = [];           // {rota, rotulo, icone, grupo}
  const acoesRegistrar = [];          // {id, rotulo, acao(rascunho, ctx)}: botões ao lado do Organizar
  const modosRegistrar = [];          // {id, rotulo, aoEscolher(rascunho), dica()}: no alto do Registrar

  function rascunhoVazio() {
    return { texto: '', data: hojeIso(), contexto: '', humor: '', privado: false };
  }

  // ================================================================ utilidades

  const $ = (seletor) => document.querySelector(seletor);

  /* Cria um elemento. Textos entram sempre como nós de texto (seguro contra HTML).
   * props: class (texto ou lista), on<evento> (função), aria-* e data-*, value/checked
   * (aplicados depois dos filhos, para <select>), demais viram atributos. */
  function h(tag, props, ...filhos) {
    const el = document.createElement(tag);
    const depois = [];
    for (const [k, v] of Object.entries(props || {})) {
      if (k.startsWith('aria-') || k.startsWith('data-')) {
        if (v != null) el.setAttribute(k, String(v));
        continue;
      }
      if (v == null || v === false) continue;
      if (k === 'class') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'value' || k === 'checked') depois.push([k, v]);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    anexar(el, filhos);
    for (const [k, v] of depois) el[k] = v;
    return el;
  }

  function anexar(el, filhos) {
    for (const f of filhos.flat(Infinity)) {
      if (f == null || f === false || f === '') continue;
      el.appendChild(f instanceof Node ? f : document.createTextNode(String(f)));
    }
  }

  // Como el.append(...), mas ignorando null/false (o append nativo escreveria "null").
  function inserir(el, ...filhos) {
    anexar(el, filhos);
  }

  function limpar(el) {
    if (el) el.replaceChildren();
  }

  function novoId(prefixo) {
    contadorIds += 1;
    return `${prefixo}-${contadorIds}`;
  }

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function clonar(v) {
    return v == null ? v : JSON.parse(JSON.stringify(v));
  }

  function semInternos(obj) {
    const copia = {};
    for (const [k, v] of Object.entries(obj)) {
      if (!k.startsWith('_') && typeof v !== 'function') copia[k] = v;
    }
    return copia;
  }

  function normalizar(s) {
    return String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/\s+/g, ' ').trim();
  }

  // "a mãe dela" -> "mae"; "Mariana" -> "mariana" (só para comparar, nunca para mostrar).
  function chavePessoa(s) {
    return normalizar(s).replace(/\s+(dela|dele)$/, '').replace(/^(a|o|as|os)\s+/, '').trim();
  }

  function encurtar(s, n) {
    s = String(s || '').replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
  }

  function esquecerArquivos() {
    for (const esquecer of [...esquecedoresArquivo]) {
      try {
        esquecer();
      } catch {
        // esquecer nunca impede a troca de tela
      }
    }
    esquecedoresArquivo.clear();
  }

  function rotuloPessoa(p) {
    return (p && (p.nome || p.relacao)) || '';
  }

  function rotuloPessoaCompleto(p) {
    if (!p) return '';
    if (p.nome && p.relacao) return `${p.nome} (${p.relacao})`;
    return p.nome || p.relacao || 'Sem nome';
  }

  // ---------------------------------------------------------------- datas

  // Declarações de função (e não const): o estado inicial já usa hojeIso().
  function pad(n) {
    return String(n).padStart(2, '0');
  }

  function isoDe(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function hojeIso() {
    return isoDe(new Date());
  }

  // "AAAA-MM-DD" (ou com hora) -> Date local. Nunca new Date(iso), que lê como UTC.
  function paraData(iso) {
    if (!iso || typeof iso !== 'string') return null;
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(iso);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  }

  function dataBr(iso) {
    const d = paraData(iso);
    return d ? `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}` : '';
  }

  function diaMes(iso) {
    const d = paraData(iso);
    return d ? `${pad(d.getDate())}/${pad(d.getMonth() + 1)}` : '';
  }

  function horaBr(iso) {
    const d = paraData(iso);
    return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
  }

  function dataLonga(iso) {
    const d = paraData(iso);
    return d ? `${DIAS_SEMANA[d.getDay()]}, ${d.getDate()} de ${MESES[d.getMonth()]} de ${d.getFullYear()}` : '';
  }

  function diaComSemana(iso) {
    const d = paraData(iso);
    if (!d) return '';
    const ano = d.getFullYear() !== new Date().getFullYear() ? `/${d.getFullYear()}` : '';
    return `${DIAS_CURTOS[d.getDay()]}, ${pad(d.getDate())}/${pad(d.getMonth() + 1)}${ano}`;
  }

  function mesAno(iso) {
    const d = paraData(iso);
    return d ? `${MESES[d.getMonth()]} de ${d.getFullYear()}` : '';
  }

  function capitalizar(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }

  function somarDias(iso, n) {
    const d = paraData(iso);
    if (!d) return null;
    d.setDate(d.getDate() + n);
    return isoDe(d);
  }

  function diasAte(iso) {
    const d = paraData(iso);
    if (!d) return null;
    const h0 = new Date();
    return Math.round((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())
      - Date.UTC(h0.getFullYear(), h0.getMonth(), h0.getDate())) / 864e5);
  }

  function textoDias(n) {
    if (n == null || Number.isNaN(n)) return '';
    if (n === 0) return 'hoje';
    if (n === 1) return 'amanhã';
    if (n === -1) return 'ontem';
    return n > 0 ? `em ${n} dias` : `há ${-n} dias`;
  }

  // A data de um item respeitando a precisão ("novembro de 2026" em vez de 01/11/2026).
  function textoDataItem(it) {
    if (!it.data_evento) return '';
    const d = paraData(it.data_evento);
    if (!d) return '';
    if (it.precisao === 'mes') return `${MESES[d.getMonth()]} de ${d.getFullYear()}`;
    if (it.precisao === 'ano') return String(d.getFullYear());
    if (it.precisao === 'semana') return `semana de ${dataBr(it.data_evento)}`;
    return dataBr(it.data_evento);
  }

  function saudacao() {
    const hora = new Date().getHours();
    if (hora >= 5 && hora < 12) return 'Bom dia';
    if (hora >= 12 && hora < 18) return 'Boa tarde';
    return 'Boa noite';
  }

  // ---------------------------------------------------------------- token e tema

  function lerToken() {
    try {
      return sessionStorage.getItem(CHAVE_TOKEN) || tokenMemoria;
    } catch {
      return tokenMemoria;
    }
  }

  function guardarToken(token) {
    tokenMemoria = token;
    try {
      sessionStorage.setItem(CHAVE_TOKEN, token);
    } catch {
      // Sem sessionStorage: o token fica só em memória (some ao recarregar a página).
    }
  }

  function esquecerToken() {
    tokenMemoria = null;
    try {
      sessionStorage.removeItem(CHAVE_TOKEN);
    } catch {
      // nada a fazer
    }
  }

  function lerTema() {
    try {
      return localStorage.getItem(CHAVE_TEMA) || 'auto';
    } catch {
      return 'auto';
    }
  }

  function guardarTema(tema) {
    try {
      localStorage.setItem(CHAVE_TEMA, tema);
    } catch {
      // preferência de aparência apenas; tudo bem se não der para lembrar
    }
  }

  function aplicarTema(tema) {
    const raiz = document.documentElement;
    if (tema === 'claro' || tema === 'escuro') raiz.setAttribute('data-tema', tema);
    else raiz.removeAttribute('data-tema');
  }

  // ================================================================ API

  class ErroApi extends Error {
    constructor(mensagem, status = 0, bloqueio = false, doServidor = false, dados = null) {
      super(mensagem);
      this.status = status;
      this.bloqueio = bloqueio;       // a tela já foi trocada pela de bloqueio
      this.doServidor = doServidor;   // a mensagem veio do servidor
      this.dados = dados;             // o resto do JSON de erro (ex.: {"pin_ia": true})
    }
  }

  // 403 {"pin_ia": true}: a área Ajustes → IA está fechada (pede o PIN da IA).
  function pedePinIA(e) {
    return Boolean(e && e.status === 403 && e.dados && e.dados.pin_ia === true);
  }

  // PIN errado (seção 19): depois de cada erro o Entre Nós não confere outro PIN por 1 s, 2 s,
  // 4 s... até 30 s. A resposta vem na hora: o 401 já traz `espere_segundos`, e quem tenta antes
  // da hora recebe 429 com quanto falta. Quanto esperar (0 = não precisa).
  function segundosDeEspera(e) {
    const s = e && e.dados ? Number(e.dados.espere_segundos) : NaN;
    return Number.isFinite(s) && s > 0 ? Math.ceil(s) : 0;
  }

  const contagensPin = new WeakMap();      // botão -> {id do intervalo, texto original}

  function esperandoPin(botao) {
    return contagensPin.has(botao);
  }

  // O botão vira "Espere N s", contando, e volta sozinho no fim: tocar de novo reenvia. A
  // mensagem do erro fica parada no campo de aviso (um alerta por segundo cansaria quem usa
  // leitor de tela).
  function contarEsperaPin(botao, segundos) {
    const anterior = contagensPin.get(botao);
    const texto = anterior ? anterior.texto : botao.textContent;
    if (anterior) clearInterval(anterior.id);
    let restante = Math.max(1, Math.ceil(segundos));
    botao.disabled = true;
    botao.textContent = `Espere ${restante} s`;
    const id = setInterval(() => {
      restante -= 1;
      if (restante > 0 && botao.isConnected) {
        botao.textContent = `Espere ${restante} s`;
        return;
      }
      clearInterval(id);
      contagensPin.delete(botao);
      botao.disabled = false;
      botao.textContent = texto;
    }, 1000);
    contagensPin.set(botao, { id, texto });
  }

  const METODOS_COM_CORPO = new Set(['POST', 'PATCH', 'PUT', 'DELETE']);

  async function api(metodo, rota, corpo, opcoes = {}) {
    const cabecalhos = { Accept: 'application/json' };
    const token = lerToken();
    if (token) cabecalhos['X-EntreNos-Token'] = token;
    const pedido = { method: metodo, headers: cabecalhos, cache: 'no-store', credentials: 'same-origin' };
    if (METODOS_COM_CORPO.has(metodo)) {
      cabecalhos['Content-Type'] = 'application/json';
      const texto = JSON.stringify(corpo === undefined ? {} : corpo);
      if (opcoes.limiteBytes) {
        // Pedido que leva um arquivo (restaurar, importar): vai como Blob, e o tamanho em bytes
        // (UTF-8, já com as aspas escapadas) é conferido aqui, antes de mandar dezenas de MB à toa.
        const blob = new Blob([texto], { type: 'application/json' });
        if (blob.size > opcoes.limiteBytes) throw new ErroApi(MENSAGENS_HTTP[413], 413);
        pedido.body = blob;
      } else {
        pedido.body = texto;
      }
    }
    estado.ultimaRequisicao = Date.now();
    let resposta;
    try {
      resposta = await fetch('/api' + rota, pedido);
    } catch {
      throw new ErroApi('Não consegui falar com o Entre Nós. Ele ainda está aberto neste computador?', 0);
    }
    const bruto = await resposta.text().catch(() => '');
    let dados = null;
    if (bruto) {
      try {
        dados = JSON.parse(bruto);
      } catch {
        dados = null;
      }
    }
    if (resposta.ok) return dados;
    const doServidor = Boolean(dados && typeof dados.erro === 'string' && dados.erro);
    const mensagem = doServidor ? dados.erro
      : (MENSAGENS_HTTP[resposta.status] || 'Algo não deu certo. Tente de novo.');
    const extras = dados && typeof dados === 'object' ? dados : null;
    if ((resposta.status === 401 || resposta.status === 423) && !opcoes.semBloqueio) {
      irParaBloqueio(resposta.status === 423
        ? 'O Entre Nós se bloqueou sozinho para proteger seus registros.' : null);
      throw new ErroApi(mensagem, resposta.status, true, doServidor, extras);
    }
    throw new ErroApi(mensagem, resposta.status, false, doServidor, extras);
  }

  // ================================================================ avisos e diálogo

  function avisar(texto, tipo = 'ok') {
    const caixa = $('#avisos');
    if (!caixa) return;
    const el = h('div', { class: `aviso aviso-${tipo}`, role: tipo === 'erro' ? 'alert' : 'status' },
      h('span', { class: 'aviso-texto' }, texto),
      h('button', { type: 'button', class: 'aviso-fechar', 'aria-label': 'Fechar aviso', onclick: () => el.remove() }, '×'));
    inserir(caixa, el);
    setTimeout(() => el.remove(), tipo === 'erro' ? 9000 : 5000);
  }

  function avisarErro(e) {
    avisar((e && e.message) || 'Algo não deu certo. Tente de novo.', 'erro');
  }

  // Anúncio discreto para leitores de tela.
  function falar(texto) {
    const regiao = $('#falar');
    if (!regiao) return;
    regiao.textContent = '';
    setTimeout(() => { regiao.textContent = texto; }, 50);
  }

  function confirmar({ titulo, texto, ok = 'Confirmar', cancelar = 'Cancelar', perigo = false }) {
    const dialogo = $('#dialogo');
    if (!dialogo || typeof dialogo.showModal !== 'function') {
      return Promise.resolve(window.confirm(`${titulo}\n\n${texto}`));
    }
    $('#dialogo-titulo').textContent = titulo;
    $('#dialogo-texto').textContent = texto;
    const botaoOk = $('#dialogo-ok');
    botaoOk.textContent = ok;
    botaoOk.className = 'botao ' + (perigo ? 'botao-perigo' : 'botao-primario');
    $('#dialogo-cancelar').textContent = cancelar;
    dialogo.returnValue = '';
    const form = dialogo.querySelector('form');
    // Decide pelo submit (com o botão que enviou) e pelo cancel (Esc), que disparam na hora;
    // o evento close fica de reserva (em alguns navegadores ele espera a tela redesenhar).
    return new Promise((resolve) => {
      let decidido = false;
      const terminar = (valor) => {
        if (decidido) return;
        decidido = true;
        form.removeEventListener('submit', aoEnviar);
        dialogo.removeEventListener('cancel', aoCancelar);
        dialogo.removeEventListener('close', aoFechar);
        resolve(valor);
      };
      const aoEnviar = (e) => terminar(Boolean(e.submitter && e.submitter.value === 'ok'));
      const aoCancelar = () => terminar(false);
      const aoFechar = () => terminar(dialogo.returnValue === 'ok');
      form.addEventListener('submit', aoEnviar);
      dialogo.addEventListener('cancel', aoCancelar);
      dialogo.addEventListener('close', aoFechar);
      dialogo.showModal();
      $('#dialogo-cancelar').focus();
    });
  }

  // ================================================================ componentes

  function cabecalho(titulo, subtitulo, emoji) {
    return h('header', { class: 'cabeca' },
      h('h1', { tabindex: '-1' }, emoji ? h('span', { class: 'h1-icone', 'aria-hidden': 'true' }, emoji + ' ') : null, titulo),
      subtitulo ? h('p', { class: 'sutil' }, subtitulo) : null);
  }

  function secao(titulo, ...conteudo) {
    const id = novoId('secao');
    // Emoji no começo do título vai num span escondido do leitor de tela ("📅 Próximos...").
    const partes = /^([^\p{L}\p{N}]+?)\s+(\S.*)$/u.exec(String(titulo));
    const texto = partes ? [h('span', { 'aria-hidden': 'true' }, partes[1] + ' '), partes[2]] : titulo;
    return h('section', { class: 'secao', 'aria-labelledby': id }, h('h2', { id }, texto), conteudo);
  }

  function campo(rotulo, controle, dica) {
    const filhos = [h('label', { for: controle.id }, rotulo)];
    if (dica) {
      const idDica = controle.id + '-dica';
      const atual = controle.getAttribute('aria-describedby');
      controle.setAttribute('aria-describedby', atual ? `${atual} ${idDica}` : idDica);
      filhos.push(h('p', { class: 'dica', id: idDica }, dica));
    }
    filhos.push(controle);
    return h('div', { class: 'campo' }, filhos);
  }

  // "🔒 Não enviar este registro para a IA" (seção 17.1.5). aoMudar(marcado, caixa).
  // `travado` (texto): a caixinha fica marcada e sem mudar, e o texto diz por quê (seção 20.0:
  // um recurso, como o ciclo, deixa o registro fora da IA).
  function caixaPrivado(id, marcado, aoMudar, travado = null) {
    const idDica = `${id}-dica`;
    const caixa = h('input', {
      id, type: 'checkbox', checked: Boolean(marcado) || Boolean(travado), disabled: Boolean(travado),
      'aria-describedby': idDica,
      onchange: (e) => aoMudar(e.target.checked, e.target),
    });
    return h('div', { class: 'opcao-privado' },
      h('label', { class: 'opcao-linha', for: id }, caixa,
        h('span', null, h('span', { 'aria-hidden': 'true' }, '🔒 '), 'Não enviar este registro para a IA')),
      h('p', { class: 'dica', id: idDica }, travado || 'Marcado, ele nunca sai deste computador: fica fora do que a IA analisa, busca ou usa para sugerir. As regras continuam usando o registro normalmente.'));
  }

  // Campo de PIN que o navegador não trata como senha de site: "one-time-code" faz o
  // Chrome e o Edge não oferecerem "Salvar senha?" (se salvassem, qualquer pessoa no
  // computador abriria o Entre Nós, e com sincronização o PIN iria para a nuvem).
  function campoPin(id, extras = {}) {
    return h('input', Object.assign({
      id, type: 'password', autocomplete: 'one-time-code', autocapitalize: 'off', spellcheck: 'false',
      'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', 'data-form-type': 'other',
    }, extras));
  }

  const AVISO_NAO_SALVAR_PIN = 'Não salve o PIN no navegador. Se ele oferecer, escolha “Nunca”.';

  // As mesmas regras do servidor (cofre.problema_do_pin), para avisar antes de enviar.
  const PINS_COMUNS = new Set(('senha senha1 senha12 senha123 senha1234 password password1 qwerty qwerty1 '
    + 'qwerty123 qwertyuiop asdfgh asdfghjkl zxcvbn abc123 abcd1234 a1b2c3 1q2w3e 1q2w3e4r iloveyou teamo '
    + 'teamo1 teamo123 amor123 amorzinho meuamor amormeu mudar123 123mudar admin admin123 entrenos entre-nos '
    + 'princesa princesinha brasil flamengo palmeiras corinthians saopaulo santos gremio vasco naosei segredo '
    + 'segredo1 trustno1 changeme welcome').split(' '));
  const SEQUENCIAS_PIN = ['0123456789', 'abcdefghijklmnopqrstuvwxyz', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm'];
  const DIGITOS_MINIMOS = 10;

  function problemaDoPin(pin) {
    if (typeof pin !== 'string' || pin.length < PIN_MINIMO) return `O PIN precisa ter pelo menos ${PIN_MINIMO} caracteres.`;
    const compacto = pin.normalize('NFC').toLowerCase().replace(/\s+/g, '');
    if (!compacto) return 'O PIN não pode ser só de espaços.';
    if (new Set(compacto).size <= 2) return 'O PIN não pode ser só um ou dois caracteres repetidos (como 111111 ou 121212).';
    const invertido = (s) => [...s].reverse().join('');
    if (SEQUENCIAS_PIN.some((s) => s.includes(compacto) || invertido(s).includes(compacto))) {
      return 'O PIN não pode ser uma sequência (como 123456 ou abcdef).';
    }
    if (PINS_COMUNS.has(compacto)) return 'Esse PIN é muito comum. Escolha outro, de preferência uma frase curta.';
    if (/^\d+$/.test(compacto) && compacto.length < DIGITOS_MINIMOS) {
      return `Um PIN só de números precisa ter pelo menos ${DIGITOS_MINIMOS} dígitos. Uma frase curta é mais segura e fácil de lembrar, como “girassol na janela”.`;
    }
    return null;
  }

  function carregando(texto = 'Carregando…') {
    return h('p', { class: 'carregando', role: 'status' }, texto);
  }

  function estadoVazio(texto, comBotao = false) {
    return h('div', { class: 'vazio' },
      h('div', { class: 'vazio-icone', 'aria-hidden': 'true' }, '🌱'),
      h('p', null, texto),
      comBotao ? h('a', { href: '#registrar', class: 'botao botao-primario' }, '✍️ Registrar algo') : null);
  }

  function vazioPequeno(texto) {
    return h('p', { class: 'sutil vazio-pequeno' }, texto);
  }

  function caixaErro(e) {
    return h('div', { class: 'cartao caixa-erro', role: 'alert' },
      h('h2', null, 'Algo não deu certo'),
      h('p', null, (e && e.message) || 'Tente de novo em instantes.'),
      h('button', { type: 'button', class: 'botao botao-secundario', onclick: () => rotear() }, 'Tentar de novo'));
  }

  // ---------------------------------------------------------------- pedaços desenhados pelos recursos (seção 20.0)

  // O desenho que um recurso registrou para este cartão ou extra (pelo `tipo`, senão pelo NOME do
  // recurso). Sem desenho, ou se ele falhar, null: quem chamou usa o cartão genérico.
  function conteudoDeRecurso(mapa, obj, recurso, ctx) {
    const render = mapa.get(obj.tipo) || mapa.get(recurso);
    if (typeof render !== 'function') return null;
    try {
      const n = render(obj, ctx);
      return n instanceof Node || Array.isArray(n) ? n : null;
    } catch {
      return null;      // um recurso com defeito não apaga o resto da tela
    }
  }

  // Uma área preenchida por um recurso: `render(caixa, ctx)` desenha dentro da caixa (pode ser
  // assíncrono); se devolver um nó, ele entra na caixa. Erro vira uma mensagem só ali dentro.
  function montarDeRecurso(render, ctx, classe) {
    const caixa = h('div', { class: classe });
    const falhou = (e) => {
      if (e && e.bloqueio) return;
      limpar(caixa);
      inserir(caixa, h('p', { class: 'erro-campo', role: 'alert' },
        (e && e.message) || 'Esta parte não abriu agora. Tente de novo mais tarde.'));
    };
    const juntar = (n) => {
      if (n instanceof Node || Array.isArray(n)) inserir(caixa, n);
    };
    try {
      const r = render(caixa, ctx);
      if (r && typeof r.then === 'function') r.then(juntar, falhou);
      else juntar(r);
    } catch (e) {
      falhou(e);
    }
    return caixa;
  }

  // "🌙 Ciclo" -> ['🌙', 'Ciclo']; sem emoji no começo -> ['', texto].
  function separarEmoji(titulo) {
    const partes = /^([^\p{L}\p{N}]+?)\s+(\S.*)$/u.exec(String(titulo || ''));
    return partes ? [partes[1], partes[2]] : ['', String(titulo || '')];
  }

  function selo(certeza) {
    const chave = CERTEZAS[certeza] ? certeza : 'observado';
    const c = CERTEZAS[chave];
    return h('span', { class: `selo selo-${chave}`, title: c.explica },
      h('span', { class: 'so-leitor' }, 'Certeza: '),
      h('span', { 'aria-hidden': 'true' }, c.emoji), ' ', c.nome);
  }

  function legendaSelos() {
    return h('div', { class: 'legenda', role: 'note', 'aria-label': 'Legenda dos selos de certeza' },
      h('span', { class: 'legenda-titulo' }, 'Selos de certeza:'),
      Object.keys(CERTEZAS).map((c) => h('span', { class: 'legenda-item' }, selo(c), ' ', CERTEZAS[c].explica)));
  }

  function rotuloTipo(item) {
    let [emoji, nome] = TIPOS[item.tipo] || TIPOS.nota;
    if (item.tipo === 'preferencia' && Number(item.polaridade) < 0) [emoji, nome] = ['🚫', 'Não gosta'];
    else if (item.tipo === 'preferencia' && Number(item.polaridade) > 0) nome = 'Gosta';
    if (item.tipo === 'apoio' && Number(item.polaridade) < 0) [emoji, nome] = ['🙅', 'O que não ajuda'];
    return h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, emoji + ' '), nome);
  }

  function citacao(trecho) {
    return h('blockquote', { class: 'citacao' }, `“${trecho}”`);
  }

  function hrefRegistro(id) {
    return `#registros/${encodeURIComponent(String(id))}`;
  }

  function linkRegistro(id, texto = 'Abrir registro') {
    return h('a', { href: hrefRegistro(id), class: 'link-registro' }, texto);
  }

  function chipsCategorias(categorias) {
    const cats = lista(categorias);
    if (!cats.length) return null;
    return h('ul', { class: 'chips', 'aria-label': 'Categorias' }, cats.map((c) => h('li', { class: 'chip' }, c)));
  }

  // Uma entrada do perfil (item, texto ou objeto parecido): texto, selo, citação e origem.
  function linhaEntrada(e) {
    if (e == null) return null;
    if (typeof e !== 'object') return h('li', { class: 'entrada' }, String(e));
    const principal = e.resumo || e.texto || e.alvo || e.trecho || '';
    const quando = e.quando || (e.data_registro ? `em ${dataBr(e.data_registro)}` : '');
    // A data com a precisão que foi dita: "novembro de 2026", nunca um dia que ninguém disse.
    const data = (e.data_evento || e.data) ? textoDataItem({ data_evento: e.data_evento || e.data, precisao: e.precisao }) : '';
    const rodape = [data ? `📅 ${data}` : null, quando || null].filter(Boolean).join(' · ');
    const lados = lista(e.registros);
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, e.certeza ? selo(e.certeza) : null,
        h('span', { class: 'entrada-texto' }, principal)),
      e.motivo ? h('p', { class: 'dica' }, e.motivo) : null,
      lados.length
        // Contradição: os dois lados, cada um com o selo, o trecho e o registro de onde veio.
        ? h('ul', { class: 'lista-entradas lista-lados' }, lados.map((r) => h('li', { class: 'entrada' },
          h('div', { class: 'entrada-topo' }, r.certeza ? selo(r.certeza) : null,
            h('span', { class: 'entrada-texto' }, r.resumo || '')),
          r.trecho ? citacao(r.trecho) : null,
          h('p', { class: 'entrada-rodape sutil' }, r.quando || (r.data_registro ? `em ${dataBr(r.data_registro)}` : ''),
            r.registro_id != null ? [' · ', linkRegistro(r.registro_id, 'ver registro')] : null))))
        : (e.trecho && e.trecho !== principal ? citacao(e.trecho) : null),
      !lados.length && (rodape || e.registro_id != null)
        ? h('p', { class: 'entrada-rodape sutil' }, rodape,
          e.registro_id != null ? [rodape ? ' · ' : '', linkRegistro(e.registro_id, 'ver registro')] : null)
        : null);
  }

  function blocoEntradas(titulo, entradas) {
    const itens = lista(entradas);
    if (!itens.length) return null;
    return h('div', { class: 'bloco-entradas' }, h('h4', null, titulo),
      h('ul', { class: 'lista-entradas' }, itens.map(linhaEntrada)));
  }

  function editorCategorias(categorias, rotulo = 'Categorias') {
    const caixa = h('div', { class: 'editor-categorias', role: 'group', 'aria-label': rotulo });
    const desenhar = (focar) => {
      limpar(caixa);
      const chips = h('ul', { class: 'chips' }, categorias.map((c, i) => h('li', { class: 'chip' }, c,
        h('button', {
          type: 'button', class: 'chip-x', 'aria-label': `Tirar a categoria ${c}`,
          onclick: () => { categorias.splice(i, 1); desenhar(true); falar(`Categoria ${c} retirada.`); },
        }, '×'))));
      const idSel = novoId('cat');
      const escolha = h('select', {
        id: idSel, class: 'select-pequeno', 'aria-label': 'Acrescentar categoria',
        onchange: (ev) => {
          const v = ev.target.value;
          if (v && !categorias.includes(v)) {
            categorias.push(v);
            desenhar(true);
            falar(`Categoria ${v} acrescentada.`);
          }
        },
      }, h('option', { value: '' }, '+ categoria'),
      CATEGORIAS.filter((c) => !categorias.includes(c)).map((c) => h('option', { value: c }, c)));
      inserir(caixa, categorias.length ? chips : h('p', { class: 'sutil' }, 'Sem categoria'), escolha);
      if (focar) escolha.focus();
    };
    desenhar(false);
    return caixa;
  }

  function iconeData(p) {
    if (p.tipo && ICONES_DATA[p.tipo]) return ICONES_DATA[p.tipo];
    const t = normalizar(p.texto);
    if (t.includes('aniversario')) return '🎂';
    if (t.includes('juntos') || t.includes('namoro')) return '💑';
    return '📌';
  }

  function linhaProxima(p) {
    const dias = typeof p.dias === 'number' ? p.dias : diasAte(p.data);
    const d = paraData(p.data);
    const precisao = p.precisao || 'dia';
    // Mês ou semana: nada de dia e dia da semana que ninguém disse.
    let bloco = [pad(d ? d.getDate() : 0), d ? MESES_CURTOS[d.getMonth()] : ''];
    let quando = [diaComSemana(p.data), textoDias(dias)];
    if (d && precisao === 'mes') {
      bloco = [MESES_CURTOS[d.getMonth()], String(d.getFullYear())];
      quando = [mesAno(p.data), dias < 0 ? 'em andamento' : null];
    } else if (d && precisao === 'semana') {
      quando = [`semana de ${diaMes(p.data)}`, dias <= 0 ? (dias > -7 ? 'nesta semana' : null) : textoDias(dias)];
    } else if (d && precisao === 'ano') {
      bloco = ['', String(d.getFullYear())];
      quando = [String(d.getFullYear()), dias < 0 ? 'em andamento' : null];
    }
    const conteudo = [
      h('span', { class: 'data-bloco', 'aria-hidden': 'true' },
        h('span', { class: 'data-dia' }, d ? bloco[0] : '?'),
        h('span', { class: 'data-mes' }, d ? bloco[1] : '')),
      h('span', { class: 'proximo-corpo' },
        h('span', { class: 'proximo-texto' }, h('span', { 'aria-hidden': 'true' }, iconeData(p) + ' '), p.texto || ''),
        h('span', { class: 'sutil' }, quando.filter(Boolean).join(' · '))),
    ];
    const perto = precisao === 'dia' && dias != null && dias >= 0 && dias <= 1;
    const classe = ['proximo', perto ? 'proximo-perto' : null];
    return h('li', { class: classe }, p.registro_id != null
      ? h('a', { href: hrefRegistro(p.registro_id), class: 'proximo-link' }, conteudo)
      : h('div', { class: 'proximo-link' }, conteudo));
  }

  function linhaRegistroCurta(r) {
    const id = r.id != null ? r.id : r.registro_id;
    const corpo = [
      h('span', { class: 'linha-data' }, diaComSemana(r.data_registro || r.data)),
      h('span', { class: 'linha-texto' }, encurtar(r.resumo || r.texto || '', 150)),
    ];
    return h('li', null, id != null
      ? h('a', { href: hrefRegistro(id), class: 'linha-link' }, corpo)
      : h('div', { class: 'linha-link' }, corpo));
  }

  // Aceita uma lista ou um objeto com listas dentro (formatos possíveis de /api/datas).
  function extrairLista(dados) {
    if (Array.isArray(dados)) return dados;
    if (!dados || typeof dados !== 'object') return [];
    return Object.values(dados).filter(Array.isArray).flat();
  }

  // ---------------------------------------------------------------- IA: o que a tela pode saber

  // {configurada, sai_do_computador}, de GET /api/estado ou /api/ia/estado. Sem nome, URL nem modelo.
  function infoIA() {
    const ia = estado.servidor && estado.servidor.ia;
    return ia && typeof ia === 'object' ? ia : null;
  }

  // Os botões ✨ só aparecem com uma IA configurada que dá para usar (uma IA salva numa versão
  // antiga sem a chave, por exemplo, fica configurada mas não utilizável: aviso em Ajustes → IA).
  function iaConfigurada() {
    const ia = infoIA();
    return Boolean(ia && ia.configurada === true && ia.utilizavel !== false);
  }

  function iaSaiDoComputador() {
    const ia = infoIA();
    return Boolean(ia && ia.sai_do_computador);
  }

  function definirInfoIA(dados) {
    if (!dados || typeof dados !== 'object') return;
    const ia = {
      configurada: dados.configurada === true,
      utilizavel: dados.utilizavel !== false,
      sai_do_computador: Boolean(dados.sai_do_computador),
      espera_maxima: typeof dados.espera_maxima === 'number' ? dados.espera_maxima : null,
    };
    estado.servidor = Object.assign({}, estado.servidor || {}, { ia });
  }

  // "até 2 min", "até 45 s": o pior caso é o tempo limite de cada IA (principal e reserva).
  function textoEsperaIA(segundos) {
    const s = Number(segundos);
    if (!Number.isFinite(s) || s <= 0) return 'A IA pode levar alguns minutos.';
    if (s < 90) return `A IA pode levar até ${Math.round(s)} s.`;
    return `A IA pode levar até ${Math.ceil(s / 60)} min.`;
  }

  // Depois de mudar a configuração da IA (ou de um 409 "nenhuma IA"), pergunta de novo.
  async function atualizarInfoIA() {
    try {
      definirInfoIA(await api('GET', '/ia/estado', undefined, { semBloqueio: true }));
    } catch {
      // sem resposta: fica o que já se sabia
    }
  }

  // "vai para uma IA na internet" ou "IA neste computador", ao lado de cada botão ✨.
  function destinoIA() {
    const fora = iaSaiDoComputador();
    return h('span', { class: ['destino-ia', fora ? 'destino-fora' : 'destino-local'] },
      h('span', { 'aria-hidden': 'true' }, fora ? '🌐 ' : '🏠 '),
      fora ? 'vai para uma IA na internet' : 'IA neste computador');
  }

  function seloDestino(local) {
    return h('span', { class: ['selo-destino', local ? 'selo-local' : 'selo-internet'] },
      h('span', { 'aria-hidden': 'true' }, local ? '🏠 ' : '🌐 '), local ? 'neste computador' : 'na internet');
  }

  // "Quem respondeu" e os avisos da cadeia principal → reserva → regras.
  function rodapeIA(usada, avisos) {
    const quem = QUEM_RESPONDEU[usada] || null;
    const lista_ = lista(avisos).filter((a) => typeof a === 'string' && a && !(quem && /^Quem respondeu/.test(a)));
    if (!quem && !lista_.length) return null;
    return h('div', { class: 'rodape-ia' },
      quem ? h('p', { class: 'sutil' }, h('span', { 'aria-hidden': 'true' }, '✨ '), `Quem respondeu: ${quem}.`) : null,
      lista_.length ? h('ul', { class: 'avisos-ia' }, lista_.map((a) => h('li', null, a))) : null);
  }

  // Quando a IA pode demorar: "a IA pode levar até 4 min" (tempo limite × principal e reserva).
  function esperandoIA(texto) {
    const ia = infoIA();
    return h('p', { class: 'esperando-ia', role: 'status' }, texto,
      h('span', { class: 'sutil' }, ' ' + textoEsperaIA(ia && ia.espera_maxima)));
  }

  // Um botão ✨ que chamou a IA e ouviu 409 (nenhuma IA configurada): some das telas.
  function semIAAgora(e) {
    if (e && e.status === 409) {
      atualizarInfoIA();
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- arquivos: backup e JSON (seção 18)

  function plural(n, um, varios) {
    return n === 1 ? um : varios;
  }

  // Um número da API que pode vir como contagem ou como lista ("pessoas_novas": 2 ou [...]).
  function contagem(v) {
    if (typeof v === 'number' && Number.isFinite(v)) return Math.max(0, Math.round(v));
    if (Array.isArray(v)) return v.length;
    return 0;
  }

  function dataDoResumo(v) {
    if (typeof v === 'string') return paraData(v) ? v.slice(0, 10) : null;
    if (v && typeof v === 'object' && typeof v.data_registro === 'string') return dataDoResumo(v.data_registro);
    return null;
  }

  function tamanhoLegivel(bytes) {
    if (bytes < 1024) return `${bytes} bytes`;
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`;
  }

  // "entrenos-backup-2026-09-30.cofre, 245 KB, salvo em 30/09/2026 às 14:03": para a pessoa
  // conferir, antes de substituir o diário, que escolheu o backup certo.
  function descreverArquivo(arquivo) {
    const partes = [`“${arquivo.name}”`, tamanhoLegivel(arquivo.size)];
    if (arquivo.lastModified) {
      const d = new Date(arquivo.lastModified);
      if (!Number.isNaN(d.getTime())) partes.push(`salvo em ${dataBr(isoDe(d))} às ${pad(d.getHours())}:${pad(d.getMinutes())}`);
    }
    return partes.join(', ');
  }

  // Antes de ler: há arquivo, não está vazio, cabe no pedido e não é o tipo trocado.
  // tipo: 'cofre' (restaurar) | 'json' (importar). primeiroUso: ainda não existe a tela Ajustes.
  function problemaDoArquivo(arquivo, tipo, primeiroUso = false) {
    if (!arquivo) return tipo === 'cofre' ? 'Escolha o arquivo do backup (.cofre).' : 'Escolha o arquivo JSON exportado pelo Entre Nós.';
    const nome = normalizar(arquivo.name);
    if (tipo === 'cofre' && nome.endsWith('.json')) {
      return primeiroUso
        ? 'Esse é um arquivo exportado (JSON), não um backup. Para começar é preciso o backup (.cofre); o JSON dá para juntar depois, em Ajustes → Levar ou trazer dados.'
        : 'Esse é um arquivo exportado (JSON), não um backup. Para juntar os registros dele, use “Importar JSON (juntar)”, logo abaixo.';
    }
    if (tipo === 'json' && nome.endsWith('.cofre')) {
      return 'Esse é um backup (.cofre), não um JSON exportado. Para trazer um backup, use “Restaurar um backup”, logo acima.';
    }
    if (!arquivo.size) return 'Esse arquivo está vazio.';
    if (tipo === 'cofre' && arquivo.size > MAIOR_COFRE) return MENSAGEM_COFRE_GRANDE;
    if (tipo === 'json' && arquivo.size > MAIOR_JSON) return MENSAGEM_JSON_GRANDE;
    return null;
  }

  // Lê um arquivo escolhido pela pessoa: como 'base64' (o .cofre, para restaurar) ou 'texto' (o
  // JSON, para importar). aoAndar(fração) acompanha a leitura. Nada fica guardado aqui: quem chama
  // segura o resultado só enquanto precisa e depois solta (limpa a referência).
  function lerArquivo(arquivo, como, aoAndar) {
    return new Promise((resolve, reject) => {
      const leitor = new FileReader();
      const falhou = () => reject(new ErroApi('Não consegui ler o arquivo. Confira se ele ainda está no lugar (um pendrive que saiu, por exemplo) e escolha de novo.'));
      leitor.addEventListener('progress', (ev) => {
        if (aoAndar && ev.lengthComputable && ev.total > 0) aoAndar(ev.loaded / ev.total);
      });
      leitor.addEventListener('error', falhou);
      leitor.addEventListener('abort', falhou);
      leitor.addEventListener('load', () => {
        const resultado = leitor.result;
        if (typeof resultado !== 'string') {
          falhou();
          return;
        }
        if (como !== 'base64') {
          resolve(resultado);
          return;
        }
        // "data:application/octet-stream;base64,AAAA..." → só o que vem depois da vírgula.
        const virgula = resultado.indexOf(',');
        if (virgula < 0) falhou();
        else resolve(resultado.slice(virgula + 1));
      });
      try {
        if (como === 'base64') leitor.readAsDataURL(arquivo);
        else leitor.readAsText(arquivo, 'utf-8');
      } catch {
        falhou();
      }
    });
  }

  // Andamento de um arquivo: uma frase (muda só quando muda a fase, para o leitor de tela não
  // repetir porcentagens) e uma barra (a porcentagem ao ler; sem valor enquanto o Entre Nós confere).
  function indicadorProgresso() {
    const texto = h('p', { class: 'estado-arquivo', role: 'status' });
    const barra = h('progress', { class: 'barra-arquivo', max: '100', 'aria-label': 'Andamento', hidden: true });
    return {
      el: h('div', { class: 'progresso-arquivo' }, texto, barra),
      // fracao: número de 0 a 1, 'espera' (sem porcentagem) ou null (sem barra).
      fase(mensagem, fracao) {
        if (mensagem != null && texto.textContent !== mensagem) texto.textContent = mensagem;
        if (fracao == null) {
          barra.hidden = true;
          return;
        }
        barra.hidden = false;
        if (fracao === 'espera') barra.removeAttribute('value');
        else barra.value = Math.max(0, Math.min(100, Math.round(fracao * 100)));
      },
      limpar() {
        texto.textContent = '';
        barra.hidden = true;
        barra.removeAttribute('value');
      },
    };
  }

  // Lê o .cofre em base64 e manda para POST /api/restaurar ({arquivo_b64, pin[, pin_atual]}).
  // Devolve {token, resumo}. O conteúdo do arquivo só existe durante o pedido.
  async function restaurarBackup(arquivo, pins, progresso) {
    progresso.fase('Lendo o arquivo…', 0);
    const corpo = Object.assign({ arquivo_b64: null }, pins);
    try {
      corpo.arquivo_b64 = await lerArquivo(arquivo, 'base64', (f) => progresso.fase(null, f));
      progresso.fase('Conferindo o backup com o PIN… Isso pode levar alguns segundos.', 'espera');
      return await api('POST', '/restaurar', corpo, { semBloqueio: true, limiteBytes: LIMITE_ENVIO_ARQUIVO });
    } finally {
      for (const chave of Object.keys(corpo)) corpo[chave] = null;
      progresso.limpar();
    }
  }

  // ================================================================ navegação

  const ROTAS = {
    hoje: telaHoje,
    registrar: telaRegistrar,
    ela: telaEla,
    datas: telaDatas,
    buscar: telaBuscar,
    registros: telaRegistros,
    ajustes: telaAjustes,
  };

  function lerRota() {
    let bruto = location.hash.replace(/^#\/?/, '');
    try {
      bruto = decodeURIComponent(bruto);
    } catch {
      bruto = '';
    }
    const [nome, ...partes] = bruto.split('/');
    // Só as rotas de verdade (as daqui e as dos recursos), nunca nomes herdados como "toString".
    return Object.prototype.hasOwnProperty.call(ROTAS, nome) ? { nome, partes } : { nome: 'hoje', partes: [] };
  }

  function irPara(hash) {
    if (location.hash === hash) rotear();
    else location.hash = hash;
  }

  function marcarNav(rota) {
    document.querySelectorAll('[data-rota]').forEach((a) => {
      if (a.dataset.rota === rota) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    const mais = $('#botao-mais');
    if (mais) {
      mais.classList.toggle('ativo', ['datas', 'registros', 'ajustes', ...menusRecursos.map((m) => m.rota)].includes(rota));
    }
  }

  // Os itens de menu dos recursos (seção 20.0): no menu lateral (computador), antes de Ajustes; no
  // celular, dentro de "Mais" (a barra de baixo fica com Hoje, Ela, Registrar, Buscar e Mais).
  // Agrupados por `grupo`, na ordem em que cada grupo apareceu.
  function desenharMenusRecursos() {
    document.querySelectorAll('[data-menu-recurso]').forEach((el) => el.remove());
    const lateral = $('.nav-lateral .nav-lista');
    const antesLateral = lateral ? (lateral.querySelector('a[data-rota="ajustes"]') || {}).parentElement || null : null;
    const mais = $('#menu-mais');
    const antesMais = mais ? mais.querySelector('a[data-rota="ajustes"]') : null;
    const grupos = [...new Set(menusRecursos.map((m) => m.grupo))];
    for (const grupo of grupos) {
      for (const m of menusRecursos.filter((x) => x.grupo === grupo)) {
        if (lateral) {
          lateral.insertBefore(h('li', { 'data-menu-recurso': m.rota, 'data-grupo': grupo },
            h('a', { href: `#${m.rota}`, 'data-rota': m.rota },
              h('span', { class: 'nav-icone', 'aria-hidden': 'true' }, m.icone), h('span', null, m.rotulo))), antesLateral);
        }
        if (mais) {
          mais.insertBefore(h('a', { href: `#${m.rota}`, 'data-rota': m.rota, 'data-menu-recurso': m.rota, 'data-grupo': grupo },
            h('span', { 'aria-hidden': 'true' }, m.icone), ` ${m.rotulo}`), antesMais);
        }
      }
    }
    if (estado.modo === 'app') marcarNav(lerRota().nome);
  }

  async function rotear() {
    if (estado.modo !== 'app') return;
    fecharMenuMais();
    estado.atalho = null;
    // O que veio da área da IA sai da página a cada troca de tela (Ajustes → IA busca de novo).
    estado.iaConfig = null;
    painelIA = null;
    esquecerArquivos();          // um JSON lido para a prévia da importação não fica para trás
    const rota = lerRota();
    marcarNav(rota.nome);
    estado.vez += 1;
    const vez = estado.vez;
    const atual = () => vez === estado.vez && estado.modo === 'app';
    const main = $('#conteudo');
    limpar(main);
    main.setAttribute('aria-busy', 'true');
    window.scrollTo(0, 0);
    try {
      await ROTAS[rota.nome](main, rota.partes, atual);
    } catch (e) {
      if (atual() && !(e && e.bloqueio)) {
        limpar(main);
        inserir(main, caixaErro(e));
      }
    } finally {
      if (atual()) main.removeAttribute('aria-busy');
    }
    if (atual()) focarTitulo(main);
  }

  function focarTitulo(raiz) {
    const titulo = raiz.querySelector('h1');
    if (!titulo) return;
    if (!titulo.hasAttribute('tabindex')) titulo.setAttribute('tabindex', '-1');
    titulo.focus({ preventScroll: true });
  }

  function alternarMenuMais(abrir) {
    const botao = $('#botao-mais');
    const menu = $('#menu-mais');
    if (!botao || !menu) return;
    const vai = abrir == null ? menu.hidden : abrir;
    menu.hidden = !vai;
    botao.setAttribute('aria-expanded', String(vai));
    if (vai) {
      const primeiro = menu.querySelector('a, button');
      if (primeiro) primeiro.focus();
    }
  }

  function fecharMenuMais() {
    const menu = $('#menu-mais');
    if (menu && !menu.hidden) alternarMenuMais(false);
  }

  // ================================================================ portão (primeiro uso, bloqueio)

  function mostrarPortao(conteudo) {
    const saindoDoApp = estado.modo === 'app';
    estado.modo = 'portao';
    estado.atalho = null;
    // As telas dos recursos esquecem o que guardaram (seção 20.10).
    if (saindoDoApp) {
      try {
        window.dispatchEvent(new CustomEvent('entrenos:bloqueado'));
      } catch {
        // navegador sem CustomEvent: nada a avisar
      }
    }
    // Só o rascunho (o que a pessoa está escrevendo agora) fica. A revisão cita trechos de
    // registros antigos e os ajustes trazem dados dela: com a tela bloqueada, nada disso
    // deve continuar na memória da página.
    estado.busca = null;
    estado.revisao = null;
    if (estado.config) estado.minutosBloqueio = estado.config.minutos_bloqueio;
    estado.config = null;
    // Tudo da IA sai: a configuração da área do PIN da IA e até o "tem IA?".
    estado.iaConfig = null;
    estado.iaAreaAte = 0;
    painelIA = null;
    if (estado.servidor) estado.servidor = Object.assign({}, estado.servidor, { ia: null });
    esquecerArquivos();
    estado.vez += 1;
    fecharMenuMais();
    const dialogo = $('#dialogo');
    if (dialogo && dialogo.open) dialogo.close('cancelar');   // "Apagar Mariana?" não fica por cima do PIN
    limpar($('#avisos'));
    limpar($('#conteudo'));             // nada do conteúdo fica na página bloqueada
    $('#app').hidden = true;
    const portao = $('#portao');
    limpar(portao);
    inserir(portao, conteudo);
    portao.hidden = false;
    document.body.classList.add('em-portao');
    window.scrollTo(0, 0);
    const alvo = portao.querySelector('input') || portao.querySelector('h1');
    if (alvo) {
      if (alvo.tagName === 'H1') alvo.setAttribute('tabindex', '-1');
      alvo.focus();
    }
  }

  function mostrarApp() {
    estado.modo = 'app';
    const portao = $('#portao');
    limpar(portao);
    portao.hidden = true;
    $('#app').hidden = false;
    document.body.classList.remove('em-portao');
    estado.ultimaAtividade = Date.now();
    rotear();
  }

  function marca() {
    return h('div', { class: 'marca-grande', 'aria-hidden': 'true' }, '💙');
  }

  function irParaBloqueio(mensagem) {
    if (estado.modo === 'portao') return;
    esquecerToken();
    telaBloqueio(mensagem);
  }

  async function carregarEstado() {
    let est;
    try {
      est = await api('GET', '/estado', undefined, { semBloqueio: true });
    } catch {
      telaSemConexao();
      return;
    }
    estado.servidor = est || {};
    if (!estado.servidor.configurado) telaPrimeiroUso();
    else if (estado.servidor.danificado) telaDanificado();
    else if (!estado.servidor.desbloqueado || !lerToken()) telaBloqueio();
    else await entrar();
  }

  // Depois do PIN: ajustes e o estado (que diz se há IA configurada, para os botões ✨).
  async function entrar() {
    let est = null;
    try {
      [estado.config, est] = await Promise.all([
        api('GET', '/config'),
        api('GET', '/estado', undefined, { semBloqueio: true }).catch(() => null),
      ]);
    } catch (e) {
      if (!e.bloqueio) telaSemConexao();
      return;
    }
    if (est && typeof est === 'object') estado.servidor = est;
    mostrarApp();
  }

  // Seção 18.6: "Encontrei N cópias de um diário que já esteve neste computador": sem o
  // entrenos.cofre (quarentena do antivírus, apagado) ou com ele danificado, a pessoa precisa
  // saber que há cópias e onde, antes de começar outro diário.
  function avisoDeCopias(copias) {
    const n = copias && typeof copias === 'object' ? contagem(copias.quantas) : 0;
    if (!n) return null;
    const quando = dataDoResumo(copias.mais_recente);
    const pasta = typeof copias.pasta === 'string' && copias.pasta ? copias.pasta : '%LOCALAPPDATA%\\EntreNos\\backups';
    return h('div', { class: 'aviso-copias', role: 'note' },
      h('p', null, h('span', { 'aria-hidden': 'true' }, '📦 '),
        h('strong', null, `Encontrei ${n} ${plural(n, 'cópia', 'cópias')} de um diário que já esteve neste computador`),
        quando ? ` (a mais recente é de ${dataBr(quando)}).` : '.'),
      h('p', null, 'Se o seu diário sumiu (o antivírus pode ter colocado o arquivo em quarentena, ou ele foi apagado), escolha “Já tenho um backup” e use a cópia mais recente, que fica nesta pasta:'),
      linhaCaminho(pasta),
      h('p', { class: 'sutil' }, 'Cada cópia abre com o PIN que o diário tinha. Nada aqui apaga essas cópias.'));
  }

  // Um caminho de arquivo ou pasta, com um botão para copiar (a pasta de dados fica escondida no
  // Windows: colar o caminho no seletor de arquivos é o jeito mais fácil de chegar lá).
  function linhaCaminho(caminho) {
    const texto = h('code', { class: 'caminho' }, caminho);
    const botao = h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno' }, 'Copiar caminho');
    botao.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(caminho);
        avisar('Caminho copiado. No seletor de arquivos, cole na barra de endereço.');
      } catch {
        const selecao = window.getSelection();
        const faixa = document.createRange();
        faixa.selectNodeContents(texto);
        selecao.removeAllRanges();
        selecao.addRange(faixa);
        avisar('Selecionei o caminho: use Ctrl+C para copiar.');
      }
    });
    return h('div', { class: 'linha-caminho' }, texto, botao);
  }

  function telaPrimeiroUso() {
    const copias = estado.servidor && estado.servidor.copias_anteriores;
    const erro = h('p', { class: 'erro-campo', id: 'pu-erro', role: 'alert' });
    const pin = campoPin('pu-pin', { required: true, minlength: String(PIN_MINIMO), 'aria-describedby': 'pu-erro' });
    const pin2 = campoPin('pu-pin2', { required: true });
    const mostrar = h('input', {
      id: 'pu-mostrar', type: 'checkbox',
      onchange: (e) => { pin.type = pin2.type = e.target.checked ? 'text' : 'password'; },
    });
    const nome = h('input', { id: 'pu-nome', type: 'text', autocomplete: 'off', maxlength: '80' });
    const aniversario = h('input', { id: 'pu-aniversario', type: 'date' });
    const inicio = h('input', { id: 'pu-inicio', type: 'date', max: hojeIso() });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario botao-grande botao-largo' }, 'Começar 💙');

    const form = h('form', { class: 'form-portao', novalidate: true },
      h('fieldset', null, h('legend', null, 'Crie o seu PIN'),
        campo('PIN', pin, `Pelo menos ${PIN_MINIMO} caracteres. Uma frase curta é mais segura e fácil de lembrar, como “pizza de sexta com ela”. Sequências (123456), repetições e PINs comuns não são aceitos; só números, a partir de ${DIGITOS_MINIMOS} dígitos.`),
        campo('Repita o PIN', pin2),
        h('label', { class: 'opcao-linha', for: 'pu-mostrar' }, mostrar, ' Mostrar o que estou digitando'),
        h('p', { class: 'nota-importante' }, 'Não existe “esqueci o PIN”: sem ele, ninguém abre os dados, nem você. Guarde num lugar seguro.'),
        h('p', { class: 'sutil' }, AVISO_NAO_SALVAR_PIN)),
      h('fieldset', null, h('legend', null, 'Sobre ela (opcional)'),
        campo('Nome dela', nome),
        h('div', { class: 'grade-2' }, campo('Aniversário dela', aniversario), campo('Início do namoro', inicio))),
      erro, botao);

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      erro.textContent = '';
      const problema = problemaDoPin(pin.value);
      if (problema) {
        erro.textContent = problema;
        pin.focus();
        return;
      }
      if (pin.value !== pin2.value) {
        erro.textContent = 'Os dois PINs não são iguais. Digite de novo com calma.';
        pin2.focus();
        return;
      }
      // Este computador já teve um diário (as cópias estão em backups): começar outro às cegas
      // é como a pessoa perde o que tinha. Pergunta antes (seção 18.6).
      const qtdCopias = copias ? contagem(copias.quantas) : 0;
      if (qtdCopias > 0) {
        const seguir = await confirmar({
          titulo: 'Criar um diário novo?',
          texto: `Este computador já teve um diário do Entre Nós: há ${qtdCopias} ${plural(qtdCopias, 'cópia', 'cópias')} dele em ${copias.pasta || 'backups'}.\n\nSe o seu diário sumiu, volte e escolha “Já tenho um backup” com a cópia mais recente.\n\nCriar um diário novo não apaga essas cópias.`,
          ok: 'Criar um diário novo',
          cancelar: 'Voltar',
        });
        if (!seguir) return;
      }
      const corpo = { pin: pin.value };
      if (nome.value.trim()) corpo.nome_dela = nome.value.trim();
      if (aniversario.value) corpo.aniversario_dela = aniversario.value;
      if (inicio.value) corpo.inicio_namoro = inicio.value;
      botao.disabled = true;
      botao.textContent = 'Preparando o cofre…';
      try {
        const r = await api('POST', '/configurar', corpo, { semBloqueio: true });
        if (!r || !r.token) throw new ErroApi('Não recebi a chave de acesso. Tente de novo.');
        pin.value = '';
        pin2.value = '';
        guardarToken(r.token);
        await entrar();
        avisar('Tudo pronto. Bem-vindo ao Entre Nós 💙');
      } catch (erroApi) {
        if (erroApi.status === 409) {
          telaBloqueio('Este computador já tem um Entre Nós configurado. Use o seu PIN.');
          return;
        }
        erro.textContent = erroApi.message;
      } finally {
        botao.disabled = false;
        botao.textContent = 'Começar 💙';
      }
    });

    // Dois caminhos (seção 18.1): um diário novo, ou o diário de um backup (.cofre) de outro
    // computador ou de antes. Os dois painéis ficam na página; os botões mostram um de cada vez.
    const painelNovo = h('div', { id: 'pu-painel-novo', class: 'painel-inicio' }, form);
    const painelBackup = h('div', { id: 'pu-painel-backup', class: 'painel-inicio', hidden: true }, formRestaurarInicio());
    const botaoNovo = h('button', {
      type: 'button', class: 'botao botao-secundario botao-escolha', 'aria-pressed': 'true', 'aria-controls': 'pu-painel-novo',
    }, h('span', { 'aria-hidden': 'true' }, '🌱 '), 'Criar meu diário');
    const botaoBackup = h('button', {
      type: 'button', class: 'botao botao-secundario botao-escolha', 'aria-pressed': 'false', 'aria-controls': 'pu-painel-backup',
    }, h('span', { 'aria-hidden': 'true' }, '📦 '), 'Já tenho um backup');
    const escolher = (backup) => {
      botaoNovo.setAttribute('aria-pressed', String(!backup));
      botaoBackup.setAttribute('aria-pressed', String(backup));
      painelNovo.hidden = backup;
      painelBackup.hidden = !backup;
      falar(backup ? 'Restaurar um backup.' : 'Criar um diário novo.');
    };
    botaoNovo.addEventListener('click', () => escolher(false));
    botaoBackup.addEventListener('click', () => escolher(true));
    const temCopias = Boolean(copias && contagem(copias.quantas) > 0);
    if (temCopias) {
      // Há cópias de um diário que esteve aqui: o caminho mais provável é restaurar.
      botaoNovo.setAttribute('aria-pressed', 'false');
      botaoBackup.setAttribute('aria-pressed', 'true');
      painelNovo.hidden = true;
      painelBackup.hidden = false;
    }

    mostrarPortao(h('div', { class: 'portao-cartao cartao' },
      marca(),
      h('h1', null, 'Bem-vindo ao Entre Nós'),
      h('p', null, 'Um cantinho para guardar o que importa para ela: o que ela conta, do que gosta, as pessoas e as datas da vida dela. Para você lembrar e cuidar melhor.'),
      h('ul', { class: 'garantias' },
        h('li', null, h('span', { 'aria-hidden': 'true' }, '🔒 '), 'Tudo fica neste computador, criptografado.'),
        h('li', null, h('span', { 'aria-hidden': 'true' }, '🙈 '), 'Sem IA ligada, nada sai daqui.'),
        h('li', null, h('span', { 'aria-hidden': 'true' }, '✋ '), 'Nada é guardado sem você revisar antes.')),
      avisoDeCopias(copias),
      h('div', { class: 'escolha-inicio', role: 'group', 'aria-label': 'Como você quer começar?' }, botaoNovo, botaoBackup),
      painelNovo,
      painelBackup));
  }

  // O arquivo do diário existe mas está danificado (cortado no meio): nenhum PIN abre. Dá para
  // restaurar um backup no lugar; o arquivo danificado fica guardado em backups (seção 18.6).
  function telaDanificado(mensagem) {
    const copias = estado.servidor && estado.servidor.copias_anteriores;
    mostrarPortao(h('div', { class: 'portao-cartao cartao' },
      h('div', { class: 'marca-grande', 'aria-hidden': 'true' }, '🩹'),
      h('h1', null, 'O arquivo do diário está danificado'),
      h('p', null, mensagem || 'O arquivo do diário (entrenos.cofre) parece cortado no meio, e nenhum PIN consegue abri-lo. Seu PIN não foi testado.'),
      h('p', null, 'Você pode restaurar um backup no lugar dele. O arquivo danificado não é apagado: fica guardado na pasta “backups” dos dados do Entre Nós, com o nome “entrenos-danificado-…”.'),
      avisoDeCopias(copias),
      formRestaurarInicio({ danificado: true }),
      h('button', { type: 'button', class: 'botao botao-texto', onclick: () => carregarEstado() }, 'Conferir de novo (se o arquivo já foi recuperado)')));
  }

  // Primeiro uso → "Já tenho um backup": arquivo .cofre + o PIN daquele backup (POST /api/restaurar
  // sem sessão). Dando certo, mostra o resumo e entra; o PIN passa a ser o do backup.
  // `danificado`: o mesmo no lugar de um diário danificado (manda `substituir_danificado: true`).
  function formRestaurarInicio({ danificado = false } = {}) {
    const erro = h('p', { class: 'erro-campo', id: 'pu-bk-erro', role: 'alert' });
    const arquivo = h('input', { id: 'pu-bk-arquivo', type: 'file', accept: '.cofre', 'aria-describedby': 'pu-bk-erro' });
    const pin = campoPin('pu-bk-pin', { 'aria-describedby': 'pu-bk-erro' });
    const mostrar = h('input', {
      id: 'pu-bk-mostrar', type: 'checkbox',
      onchange: (e) => { pin.type = e.target.checked ? 'text' : 'password'; },
    });
    const progresso = indicadorProgresso();
    const TEXTO_BOTAO = 'Restaurar e entrar';
    const botao = h('button', { type: 'submit', class: 'botao botao-primario botao-grande botao-largo' }, TEXTO_BOTAO);
    const form = h('form', { class: 'form-portao', novalidate: true },
      h('fieldset', null, h('legend', null, danificado ? 'Restaurar um backup no lugar do arquivo danificado' : 'Restaurar um backup'),
        h('p', null, 'Escolha o arquivo .cofre (uma cópia de segurança do Entre Nós, feita neste ou em outro computador) e digite o PIN que valia quando ela foi feita.'),
        campo('Arquivo do backup (.cofre)', arquivo, 'Pode estar num pendrive, ou na pasta “backups” dos dados do Entre Nós do outro computador.'),
        campo('PIN desse backup', pin),
        h('label', { class: 'opcao-linha', for: 'pu-bk-mostrar' }, mostrar, ' Mostrar o que estou digitando'),
        h('p', { class: 'nota-cuidado' }, 'Depois de restaurar, o PIN do Entre Nós neste computador passa a ser o desse backup.'),
        h('p', { class: 'sutil' }, AVISO_NAO_SALVAR_PIN)),
      progresso.el, erro, botao);

    arquivo.addEventListener('change', () => {
      erro.textContent = problemaDoArquivo(arquivo.files && arquivo.files[0], 'cofre', true) || '';
    });

    let ocupado = false;
    const travar = (sim) => {
      ocupado = sim;
      arquivo.disabled = pin.disabled = mostrar.disabled = botao.disabled = sim;
      botao.textContent = sim ? 'Restaurando…' : TEXTO_BOTAO;
    };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (ocupado || esperandoPin(botao)) return;
      erro.textContent = '';
      const escolhido = arquivo.files && arquivo.files[0];
      const problema = problemaDoArquivo(escolhido, 'cofre', true);
      if (problema) {
        erro.textContent = problema;
        arquivo.focus();
        return;
      }
      if (!pin.value) {
        erro.textContent = 'Digite o PIN desse backup.';
        pin.focus();
        return;
      }
      const pins = { pin: pin.value };
      if (danificado) pins.substituir_danificado = true;
      travar(true);
      let foco = null;
      let espera = 0;
      try {
        esquecerToken();        // primeiro uso: sem sessão (um token velho desta aba não vale aqui)
        const r = await restaurarBackup(escolhido, pins, progresso);
        if (!r || !r.token) throw new ErroApi('O backup foi restaurado, mas não recebi a chave de acesso. Recarregue a página e digite o PIN do backup.');
        arquivo.value = '';
        guardarToken(r.token);
        telaRestaurado(r.resumo, false, r.copia, Boolean(r.danificado));
        return;
      } catch (erroApi) {
        // Sem sessão, o servidor só aceita restaurar enquanto não existe diário (ou ele está
        // danificado). Se outra aba criou (ou restaurou) um no meio, a resposta é 409, ou 401/423
        // (a rota passou a pedir o token): a mensagem do PIN do backup errado, que também é 401,
        // e o /api/estado desempatam.
        const pinDoBackup = erroApi.status === 401 && erroApi.doServidor && erroApi.message === MENSAGEM_PIN_DO_BACKUP;
        if (!pinDoBackup && erroApi.status === 409 && erroApi.dados && erroApi.dados.danificado) {
          await carregarEstado();
          return;
        }
        if (!pinDoBackup && (erroApi.status === 409
          || ((erroApi.status === 401 || erroApi.status === 423) && (await jaTemDiario())))) {
          telaBloqueio('Este computador já tem um Entre Nós configurado. Use o seu PIN.');
          return;
        }
        espera = segundosDeEspera(erroApi);
        if (erroApi.status === 401) {
          erro.textContent = 'Esse PIN não abre este backup. Use o PIN que valia quando ele foi feito.';
          foco = pin;
        } else if (erroApi.status === 413) {
          erro.textContent = MENSAGEM_COFRE_GRANDE;
          foco = arquivo;
        } else if (erroApi.status === 400) {
          erro.textContent = erroApi.doServidor ? erroApi.message : MENSAGEM_NAO_E_COFRE;
          foco = arquivo;
        } else {
          erro.textContent = erroApi.message;          // 429: "Espere N s antes de tentar o PIN de novo."
          if (erroApi.status === 429) foco = pin;
        }
      } finally {
        pins.pin = null;
        pin.value = '';
        if (form.isConnected) travar(false);
      }
      if (espera && form.isConnected) contarEsperaPin(botao, espera);
      if (foco) foco.focus();
    });
    return form;
  }

  // Já existe um diário neste computador? (GET /api/estado; sem resposta, fica o "não".)
  async function jaTemDiario() {
    try {
      const est = await api('GET', '/estado', undefined, { semBloqueio: true });
      return Boolean(est && est.configurado);
    } catch {
      return false;
    }
  }

  // Depois de restaurar (primeiro uso ou Ajustes): o que voltou, e o botão para entrar. A página
  // já passou pelo portão, então tudo o que era do diário anterior saiu da memória dela.
  // copia: o caminho da cópia do diário anterior que o servidor guardou (ao restaurar em Ajustes),
  // ou do arquivo danificado guardado (`danificado`, seção 18.6).
  function telaRestaurado(resumo, deAjustes, copia = null, danificado = false) {
    const r = resumo && typeof resumo === 'object' ? resumo : {};
    const registros = contagem(r.registros);
    const pessoas = contagem(r.pessoas);
    const primeiro = dataDoResumo(r.primeiro_registro);
    const ultimo = dataDoResumo(r.ultimo_registro);
    let periodo = null;
    if (registros > 0 && primeiro && ultimo && primeiro !== ultimo) periodo = `de ${dataBr(primeiro)} a ${dataBr(ultimo)}`;
    else if (registros > 0 && (primeiro || ultimo)) periodo = `em ${dataBr(primeiro || ultimo)}`;
    const botao = h('button', { type: 'button', class: 'botao botao-primario botao-grande botao-largo' }, 'Abrir meu diário 💙');
    const cartao = h('div', { class: 'portao-cartao cartao' },
      h('div', { class: 'marca-grande', 'aria-hidden': 'true' }, '📦'),
      h('h1', null, 'Backup restaurado'),
      h('p', null, registros > 0
        ? 'O diário desse backup agora está neste computador. Voltaram:'
        : 'O diário desse backup agora está neste computador, mas ele ainda não tinha nenhum registro.'),
      registros > 0 || pessoas > 0
        ? h('ul', { class: 'garantias resumo-restauracao' },
          h('li', null, h('span', { 'aria-hidden': 'true' }, '📖 '), `${registros} ${plural(registros, 'registro', 'registros')}`),
          periodo ? h('li', null, h('span', { 'aria-hidden': 'true' }, '📅 '), periodo) : null,
          h('li', null, h('span', { 'aria-hidden': 'true' }, '👥 '), pessoas > 0
            ? `${pessoas} ${plural(pessoas, 'pessoa', 'pessoas')} da vida dela`
            : 'nenhuma pessoa cadastrada'))
        : null,
      h('p', { class: 'nota-cuidado' }, 'A partir de agora, o PIN do Entre Nós neste computador é o desse backup. A IA e o PIN da IA também são os que estavam nele.'),
      deAjustes
        ? h('div', { class: 'copia-anterior' },
          h('p', { class: 'sutil' }, 'O diário que estava aqui antes ficou guardado neste arquivo. Ele abre com o PIN que valia antes da restauração:'),
          linhaCaminho(typeof copia === 'string' && copia ? copia : 'backups\entrenos-antes-da-restauracao-…'),
          h('p', { class: 'sutil' }, 'Para desfazer: Ajustes → Levar ou trazer dados → Restaurar um backup, cole este caminho no seletor de arquivos e use o PIN de antes. A pasta dos dados fica escondida no Windows; colar o caminho é o jeito mais fácil de chegar nela.'))
        : null,
      danificado && typeof copia === 'string' && copia
        ? h('div', { class: 'copia-anterior' },
          h('p', { class: 'sutil' }, 'O arquivo danificado não foi apagado; ficou guardado aqui:'),
          linhaCaminho(copia))
        : null,
      botao);
    botao.addEventListener('click', async () => {
      botao.disabled = true;
      botao.textContent = 'Abrindo…';
      try {
        history.replaceState(null, '', '#hoje');     // o diário é outro: começa pela página Hoje
      } catch {
        // sem history: abre na tela que estiver no endereço
      }
      await entrar();
      if (estado.modo !== 'app' && cartao.isConnected) {
        // A sessão acabou enquanto esta tela esperava (bloqueio por tempo): o PIN do backup abre.
        esquecerToken();
        telaBloqueio('Digite o PIN do backup para abrir o diário.');
      }
    });
    mostrarPortao(cartao);
    falar('Backup restaurado.');
  }

  function telaBloqueio(mensagem) {
    const erro = h('p', { class: 'erro-campo', id: 'bl-erro', role: 'alert' });
    const pin = campoPin('bl-pin', { required: true, 'aria-describedby': 'bl-erro' });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario botao-grande botao-largo' }, 'Abrir');
    // O arquivo do diário sumiu (quarentena do antivírus, apagado): o servidor diz onde estão as
    // cópias, e este botão leva para as opções de começar ou restaurar (seção 18.6).
    const opcoes = h('button', { type: 'button', class: 'botao botao-secundario botao-largo', hidden: true,
      onclick: () => carregarEstado() }, 'Ver as opções: restaurar um backup ou criar um diário');
    const form = h('form', { class: 'form-portao', novalidate: true }, campo('PIN', pin), erro, opcoes, botao);

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (esperandoPin(botao)) return;
      erro.textContent = '';
      if (!pin.value) {
        erro.textContent = 'Digite o seu PIN.';
        pin.focus();
        return;
      }
      botao.disabled = true;
      botao.textContent = 'Conferindo…';
      let espera = 0;
      try {
        const r = await api('POST', '/desbloquear', { pin: pin.value }, { semBloqueio: true });
        pin.value = '';
        if (!r || !r.token) throw new ErroApi('Não recebi a chave de acesso. Tente de novo.');
        guardarToken(r.token);
        await entrar();
      } catch (erroApi) {
        pin.value = '';
        const dados = erroApi.dados || {};
        if (erroApi.status === 409 && dados.danificado) {
          await carregarEstado();                // nenhum PIN abre (nem foi testado): vai para a restauração
          return;
        }
        // PIN errado (401) ou tentativa antes da hora (429): o botão conta a espera.
        espera = segundosDeEspera(erroApi);
        if (erroApi.status === 401 && !erroApi.doServidor) erro.textContent = 'PIN incorreto. Respire e tente de novo.';
        else erro.textContent = erroApi.message;
        opcoes.hidden = !(erroApi.status === 409 && dados.sem_diario);
        pin.focus();
      } finally {
        botao.disabled = false;
        botao.textContent = 'Abrir';
      }
      if (espera && form.isConnected) contarEsperaPin(botao, espera);
    });

    const minutos = (estado.config && estado.config.minutos_bloqueio) || estado.minutosBloqueio;
    mostrarPortao(h('div', { class: 'portao-cartao cartao' },
      h('div', { class: 'marca-grande', 'aria-hidden': 'true' }, '🔒'),
      h('h1', null, 'Entre Nós'),
      h('p', null, mensagem || 'Digite seu PIN para abrir.'),
      form,
      h('p', { class: 'sutil' }, minutos
        ? `Por segurança, ele se bloqueia sozinho depois de ${minutos} minutos sem uso.`
        : 'Por segurança, ele se bloqueia sozinho depois de um tempo sem uso.'),
      h('p', { class: 'sutil' }, AVISO_NAO_SALVAR_PIN),
      h('details', { class: 'detalhes-ajuda' },
        h('summary', null, 'Esqueci o PIN'),
        h('p', null, 'O PIN não fica guardado em lugar nenhum, e é ele que abre a criptografia. Sem ele, não há como abrir os registros, nem as cópias de segurança. Tente com calma as frases que você costuma usar.'))));
  }

  function telaSemConexao() {
    mostrarPortao(h('div', { class: 'portao-cartao cartao' },
      h('div', { class: 'marca-grande', 'aria-hidden': 'true' }, '🔌'),
      h('h1', null, 'Não encontrei o Entre Nós'),
      h('p', null, 'Parece que o programa não está aberto neste computador. Abra de novo pelo atalho “Entre Nós” do menu Iniciar (no computador onde ele foi instalado) ou pelo “Abrir Entre Nos.bat”, e depois toque em “Tentar de novo”.'),
      h('button', { type: 'button', class: 'botao botao-primario botao-largo', onclick: () => carregarEstado() }, 'Tentar de novo')));
  }

  function telaSaiu() {
    estado.servidor = null;
    mostrarPortao(h('div', { class: 'portao-cartao cartao' },
      marca(),
      h('h1', null, 'Até logo'),
      h('p', null, 'O Entre Nós foi fechado e tudo ficou salvo.'),
      h('p', { class: 'sutil' }, 'Pode fechar esta aba. Para abrir de novo, use o atalho “Entre Nós” do menu Iniciar (no computador onde ele foi instalado) ou o “Abrir Entre Nos.bat”.')));
  }

  async function bloquearAgora(automatico = false) {
    if (estado.modo !== 'app') return;
    const pedido = api('POST', '/bloquear', {}, { semBloqueio: true }).catch(() => {});
    esquecerToken();
    telaBloqueio(automatico
      ? 'Bloqueei depois de um tempo sem uso, para proteger seus registros.'
      : 'Bloqueado. Até já 💙');
    await pedido;
  }

  // Bloqueio por inatividade na própria página, e "sinal de vida" enquanto a pessoa usa
  // a tela sem fazer pedidos (escrevendo um texto longo, por exemplo).
  function limiteSemUso() {
    const minutos = Number(estado.config && estado.config.minutos_bloqueio) || 10;
    return minutos * 60000;
  }

  function passouDoTempoSemUso() {
    return estado.modo === 'app' && Date.now() - estado.ultimaAtividade >= limiteSemUso();
  }

  function verificarInatividade() {
    if (estado.modo !== 'app') return;
    const limite = limiteSemUso();
    const agora = Date.now();
    if (passouDoTempoSemUso()) {
      bloquearAgora(true);
      return;
    }
    // A área da IA fecha sozinha depois de 10 minutos (o servidor também fecha): esquece o que veio dela.
    if (estado.iaAreaAte && agora >= estado.iaAreaAte) {
      estado.iaAreaAte = 0;
      estado.iaConfig = null;
      if (painelIA) painelIA.desenhar({ mensagem: 'A área da IA se fechou sozinha depois de 10 minutos. Para mexer nela de novo, digite o PIN da IA.' });
    }
    if (agora - estado.ultimaAtividade < 60000
      && agora - estado.ultimaRequisicao >= Math.min(limite / 2, 120000)) {
      api('GET', '/config')
        .then((c) => { if (c && typeof c === 'object') estado.config = c; })
        .catch(() => {});
    }
  }

  // ================================================================ tela: Hoje

  async function telaHoje(main, _partes, atual) {
    inserir(main, carregando());
    const dados = (await api('GET', '/hoje')) || {};
    if (!atual()) return;
    limpar(main);
    inserir(main, 
      h('header', { class: 'cabeca' },
        h('p', { class: 'saudacao' }, `${saudacao()} 💙`),
        h('h1', { tabindex: '-1' }, 'Hoje'),
        h('p', { class: 'sutil' }, dataLonga(hojeIso()))),
      h('a', { href: '#registrar', class: 'botao botao-primario botao-grande botao-registrar' }, '✍️ Registrar algo'));

    const alertas = lista(dados.alertas);
    const proximos = lista(dados.proximos);
    const lembrar = lista(dados.lembrar);
    const ultimos = lista(dados.ultimos);
    if (!alertas.length && !proximos.length && !lembrar.length && !ultimos.length) {
      inserir(main, estadoVazio(VAZIO));
      return;
    }
    if (alertas.length) inserir(main, secaoAlertas(alertas));
    inserir(main, cartaoSugestaoIA());
    inserir(main, secao('📅 Próximos acontecimentos', proximos.length
      ? h('ul', { class: 'lista-proximos' }, proximos.map(linhaProxima))
      : vazioPequeno('Nada marcado para os próximos 30 dias.')));
    inserir(main, secao('💭 Coisas para lembrar', lembrar.length
      ? h('ul', { class: 'lista-entradas' }, lembrar.map(linhaLembrar))
      : vazioPequeno('Nenhum assunto em aberto agora.')));
    inserir(main, secao('📖 Últimos registros', ultimos.length
      ? h('ul', { class: 'lista-linhas' }, ultimos.map(linhaRegistroCurta))
      : estadoVazio(VAZIO)));
  }

  // Pendência ou estado recente: selo, trecho de onde veio e, na pendência, "Já resolvido".
  function linhaLembrar(l) {
    const li = h('li', { class: 'entrada lembrar' });
    const resolver = async (ev) => {
      const botao = ev.currentTarget;
      botao.disabled = true;
      try {
        await api('PATCH', `/itens/${l.item_id}`, { status: 'resolvido' });
        li.remove();
        avisar('Pronto, marquei como resolvido.');
      } catch (e) {
        if (!e.bloqueio) {
          botao.disabled = false;
          avisarErro(e);
        }
      }
    };
    inserir(li,
      h('div', { class: 'entrada-topo' }, l.certeza ? selo(l.certeza) : null,
        h('span', { class: 'entrada-texto' }, l.texto || '')),
      l.trecho ? citacao(l.trecho) : null,
      h('p', { class: 'entrada-rodape sutil' },
        l.registro_id != null ? linkRegistro(l.registro_id, 'ver registro') : null,
        l.tipo === 'pendencia' && l.item_id != null
          ? [l.registro_id != null ? ' · ' : '', h('button', { type: 'button', class: 'botao botao-texto', onclick: resolver }, '✔️ Já resolvido')]
          : null));
    return li;
  }

  function secaoAlertas(alertas) {
    const caixa = h('div', { class: 'alertas' });
    for (const nivel of NIVEIS) {
      const doNivel = alertas.filter((a) => a.nivel === nivel.id);
      if (!doNivel.length) continue;
      const id = novoId('nivel');
      const grupo = h('section', { class: `grupo-alertas grupo-${nivel.id}`, 'aria-labelledby': id },
        h('h2', { id }, h('span', { 'aria-hidden': 'true' }, nivel.emoji + ' '), nivel.nome));
      const ul = h('ul', { class: 'lista-cartoes' });
      doNivel.forEach((a) => inserir(ul, cartaoAlerta(a, grupo, ul)));
      inserir(grupo, ul);
      inserir(caixa, grupo);
    }
    return caixa;
  }

  // "💡 Sugestão" com ✨: a IA pensa em até 3 gestos pequenos, citando os registros.
  function cartaoSugestaoIA() {
    if (!iaConfigurada()) return null;
    const id = novoId('sug');
    const saida = h('div', { class: 'saida-ia' });
    const botao = h('button', { type: 'button', class: 'botao botao-ia' }, '✨ Pensar numa sugestão');
    botao.addEventListener('click', async () => {
      botao.disabled = true;
      botao.textContent = '✨ Pensando…';
      limpar(saida);
      inserir(saida, esperandoIA('Pensando numa ideia a partir do que você registrou…'));
      try {
        const r = (await api('POST', '/ia/sugestao', {})) || {};
        if (!saida.isConnected) return;
        desenharSugestoesIA(saida, r);
        falar(lista(r.sugestoes).length ? 'A sugestão chegou.'
          : (iaNaoRespondeuSugestao(r) ? 'A IA não respondeu desta vez.' : 'Não tenho informação suficiente para sugerir algo específico.'));
      } catch (e) {
        if (e.bloqueio || !saida.isConnected) return;
        limpar(saida);
        inserir(saida, h('p', { class: 'erro-campo' }, e.message));
        if (semIAAgora(e)) botao.hidden = true;
      } finally {
        botao.disabled = false;
        botao.textContent = '✨ Pensar numa sugestão';
      }
    });
    return h('section', { class: 'cartao cartao-ia-sugestao', 'aria-labelledby': id },
      h('h2', { id }, h('span', { 'aria-hidden': 'true' }, '💡 '), 'Sugestão'),
      h('p', { class: 'sutil' }, 'Uma ideia pequena, pensada a partir do que você registrou. Registros marcados 🔒 ficam de fora.'),
      h('div', { class: 'barra-ia' }, botao, destinoIA()),
      saida);
  }

  // Sem sugestão: ou a IA respondeu que não há o que sugerir (ia_usada preenchido), ou não havia
  // registro para mandar (sem avisos), ou nenhuma IA respondeu (ia_usada null com avisos).
  function iaNaoRespondeuSugestao(r) {
    return !r.ia_usada && lista(r.avisos).some((a) => typeof a === 'string' && a);
  }

  function desenharSugestoesIA(caixa, r) {
    limpar(caixa);
    const sugestoes = lista(r.sugestoes).filter((s) => s && typeof s.texto === 'string' && s.texto.trim());
    inserir(caixa,
      sugestoes.length
        ? h('ul', { class: 'lista-cartoes lista-sugestoes-ia' }, sugestoes.map(linhaSugestaoIA))
        : h('p', null, iaNaoRespondeuSugestao(r) ? 'A IA não respondeu desta vez.'
          : 'Não tenho informação suficiente para sugerir algo específico.'),
      rodapeIA(r.ia_usada, r.avisos));
  }

  function linhaSugestaoIA(s) {
    const ids = lista(s.registro_ids).filter((rid) => rid != null && rid !== '');
    return h('li', { class: 'sugestao-ia' },
      h('p', { class: 'sugestao-ia-texto' }, s.texto),
      s.porque || ids.length
        ? h('details', { class: 'porque' }, h('summary', null, 'Por quê?'),
          s.porque ? h('p', null, s.porque) : null,
          ids.length ? h('p', { class: 'links-registros' }, ids.map((rid, i) => [i ? ' · ' : '',
            linkRegistro(rid, ids.length > 1 ? `ver registro (${i + 1} de ${ids.length})` : 'ver o registro')])) : null)
        : null,
      h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: quem conhece ela é você.'));
  }

  function cartaoAlerta(a, grupo, ul) {
    const ids = lista(a.registro_ids);
    const li = h('li', { class: ['cartao', 'alerta', `alerta-${a.nivel}`] });
    const sair = () => {
      li.remove();
      if (!ul.children.length) grupo.remove();
    };
    const dispensar = async (ev) => {
      const botao = ev.currentTarget;   // depois do await, currentTarget já é null
      botao.disabled = true;
      try {
        await api('POST', '/alertas/dispensar', { chave: a.chave });
        sair();
        avisar('Pronto, esse aviso não volta mais.');
      } catch (e) {
        if (!e.bloqueio) {
          botao.disabled = false;
          avisarErro(e);
        }
      }
    };
    // Cartão de recurso (seção 20.0) com desenho próprio: o recurso desenha o miolo; o "dispensar"
    // é o mesmo de sempre (a chave fica guardada e o cartão não volta).
    if (a.recurso) {
      const proprio = conteudoDeRecurso(cartoesHoje, a, a.recurso, {
        dispensar: async () => { await api('POST', '/alertas/dispensar', { chave: a.chave }); sair(); },
        remover: sair,
        recarregar: () => rotear(),
        botaoDispensar: () => h('button', { type: 'button', class: 'botao botao-texto', onclick: dispensar }, 'Dispensar'),
      });
      if (proprio) {
        if (RE_ID_RECURSO.test(String(a.recurso))) li.classList.add(`cartao-recurso-${a.recurso}`);
        inserir(li, proprio);
        return li;
      }
    }
    inserir(li,
      h('p', { class: 'alerta-texto' }, a.texto || ''),
      a.data ? h('p', { class: 'sutil' }, `${diaComSemana(a.data)} · ${textoDias(diasAte(a.data))}`) : null,
      h('div', { class: 'alerta-acoes' },
        a.porque || ids.length
          ? h('details', { class: 'porque' }, h('summary', null, 'Por quê?'),
            a.porque ? h('p', null, a.porque) : null,
            ids.length ? h('p', { class: 'links-registros' }, ids.map((rid, i) => [i ? ' · ' : '', linkRegistro(rid, ids.length > 1 ? `ver registro (${i + 1} de ${ids.length})` : 'ver o registro')])) : null)
          : h('span'),
        h('button', { type: 'button', class: 'botao botao-texto', onclick: dispensar }, 'Dispensar')));
    return li;
  }

  // ================================================================ tela: Registrar

  async function telaRegistrar(main, _partes, atual) {
    if (estado.revisao) desenharRevisao(main);
    else desenharFormulario(main, atual);
  }

  function desenharFormulario(main, atual) {
    const r = estado.rascunho;
    if (!r.texto) r.data = hojeIso();     // a aba pode ter ficado aberta de um dia para o outro
    const erro = h('p', { class: 'erro-campo', id: 'reg-erro', role: 'alert' });
    const texto = h('textarea', {
      id: 'reg-texto', rows: '9', required: true, 'aria-describedby': 'reg-erro',
      'aria-keyshortcuts': 'Control+Enter',
      placeholder: 'Ex.: Ela disse que adora tulipas. A apresentação dela é dia 20 e ela está nervosa com isso.',
      value: r.texto,
      oninput: (e) => { r.texto = e.target.value; erro.textContent = ''; },
    });
    const data = h('input', {
      id: 'reg-data', type: 'date', required: true, value: r.data || hojeIso(),
      onchange: (e) => { r.data = e.target.value; },
    });
    const contexto = h('input', {
      id: 'reg-contexto', type: 'text', value: r.contexto, maxlength: '200',
      placeholder: 'no jantar, por mensagem, no carro…',
      oninput: (e) => { r.contexto = e.target.value; },
    });
    const humor = h('input', {
      id: 'reg-humor', type: 'text', list: 'lista-humores', value: r.humor, maxlength: '80',
      placeholder: 'animada, cansada, preocupada…',
      oninput: (e) => { r.humor = e.target.value; },
    });
    // Sem ✨: quem organiza aqui são as regras (o ✨ é só dos botões que chamam a IA, seção 17.7).
    const botao = h('button', { type: 'submit', class: 'botao botao-primario botao-grande', 'aria-keyshortcuts': 'Control+Enter' }, '🗂️ Organizar');

    const organizar = async () => {
      if (botao.disabled) return;
      if (!r.texto.trim()) {
        erro.textContent = 'Escreva alguma coisa antes de organizar.';
        texto.focus();
        return;
      }
      if (!r.data) r.data = hojeIso();
      botao.disabled = true;
      botao.textContent = 'Organizando…';
      form.setAttribute('aria-busy', 'true');
      try {
        const [proposta, conhecidas] = await Promise.all([
          api('POST', '/analisar', { texto: r.texto, data_registro: r.data }),
          api('GET', '/pessoas').catch((e) => { if (e.bloqueio) throw e; return []; }),
        ]);
        estado.revisao = novaRevisao(proposta || {}, conhecidas);
        if (!atual()) return;
        limpar(main);
        desenharRevisao(main);
        focarTitulo(main);
      } catch (e) {
        if (!e.bloqueio) {
          erro.textContent = e.message;
          texto.focus();
        }
      } finally {
        botao.disabled = false;
        botao.textContent = '🗂️ Organizar';
        form.removeAttribute('aria-busy');
      }
    };

    const form = h('form', { class: 'cartao form-registro', novalidate: true, onsubmit: (e) => { e.preventDefault(); organizar(); } },
      modosDoRegistrar(),
      campo('O que ela te contou?', texto),
      erro,
      h('div', { class: 'campo campo-data' }, h('label', { for: 'reg-data' }, 'Quando foi?'), data),
      caixaPrivado('reg-privado', r.privado, (marcado) => { r.privado = marcado; }),
      h('details', { class: 'mais-detalhes', open: Boolean(r.contexto || r.humor) },
        h('summary', null, 'Mais detalhes (opcional)'),
        campo('Onde ou como vocês conversaram', contexto),
        campo('Como ela estava', humor)),
      h('div', { class: 'acoes' }, botao,
        acoesRegistrar.map((a) => botaoAcaoRegistrar(a, main, atual)),
        h('span', { class: 'sutil atalho' }, 'ou Ctrl+Enter')));

    estado.atalho = organizar;
    // Aberto por um recurso (abrirRegistrar): organiza na hora ou deixa o cursor no fim do texto,
    // depois de a tela focar o título (o leitor de tela anuncia onde está).
    const organizarJa = estado.organizarAoAbrir && Boolean(r.texto.trim());
    const cursorNoFim = estado.cursorNoFim;
    estado.organizarAoAbrir = false;
    estado.cursorNoFim = false;
    if (organizarJa || cursorNoFim) {
      setTimeout(() => {
        if (!atual() || !texto.isConnected) return;
        if (organizarJa) {
          organizar();
          return;
        }
        texto.focus();
        try {
          texto.setSelectionRange(texto.value.length, texto.value.length);
        } catch {
          // nem todo navegador deixa posicionar o cursor; tudo bem
        }
      }, 0);
    }
    inserir(main,
      cabecalho('Registrar', 'Conte do seu jeito, como contaria a um amigo. Nada é salvo antes de você revisar.', '✍️'),
      form,
      h('section', { class: 'cartao dicas', 'aria-labelledby': 'dicas-titulo' },
        h('h2', { id: 'dicas-titulo' }, 'Dicas'),
        h('ul', null,
          h('li', null, '“Ela disse que…” vira ', selo('confirmado'), '.'),
          h('li', null, 'O que você viu ou percebeu vira ', selo('observado'), '.'),
          h('li', null, '“Acho que…” vira ', selo('inferencia'), ', e só você pode confirmar depois.'),
          h('li', null, 'Datas como “dia 20”, “sexta que vem” ou “em novembro” viram lembretes.'))));
  }

  // ---------------------------------------------------------------- Registrar: o que os recursos acrescentam (20.10)

  // Uma cópia do que está sendo escrito (os recursos nunca mexem no rascunho direto).
  function copiaDoRascunho() {
    const r = estado.rascunho;
    return {
      texto: r.texto || '', data: r.data || hojeIso(), contexto: r.contexto || '', humor: r.humor || '',
      privado: Boolean(r.privado), emRevisao: Boolean(estado.revisao),
    };
  }

  // "✍️ Texto livre" e os outros jeitos de registrar (registrarModoRegistrar), no alto do formulário.
  function modosDoRegistrar() {
    if (!modosRegistrar.length) return null;
    const dicas = [];
    const botoes = [h('button', { type: 'button', class: 'botao botao-secundario botao-escolha', 'aria-pressed': 'true' }, '✍️ Texto livre')];
    for (const m of modosRegistrar) {
      botoes.push(h('button', {
        type: 'button', class: 'botao botao-secundario botao-escolha', 'aria-pressed': 'false',
        onclick: () => {
          try {
            m.aoEscolher(copiaDoRascunho());
          } catch (e) {
            avisarErro(e);
          }
        },
      }, m.rotulo));
      if (m.dica) {
        try {
          const d = m.dica();
          if (typeof d === 'string' && d.trim()) dicas.push(d.trim());
        } catch {
          // a dica é opcional: sem ela, o botão continua
        }
      }
    }
    return [h('div', { class: 'escolha-inicio', role: 'group', 'aria-label': 'Jeito de registrar', id: 'reg-modos' }, botoes),
      dicas.map((d) => h('p', { class: 'dica' }, d))];
  }

  // Um botão de recurso ao lado do Organizar (registrarAcaoRegistrar): recebe uma cópia do rascunho e
  // `limpar()` (esvazia o formulário depois de usar o texto, como o "Guardar para organizar depois").
  function botaoAcaoRegistrar(a, main, atual) {
    const botao = h('button', { type: 'button', class: 'botao botao-secundario', id: `reg-acao-${a.id}` }, a.rotulo);
    botao.addEventListener('click', async () => {
      if (botao.disabled) return;
      botao.disabled = true;
      let limpou = false;
      const ctx = {
        limpar: () => {
          limpou = true;
          estado.rascunho = rascunhoVazio();
          estado.aoSalvar = null;
        },
        focar: () => {
          const t = $('#reg-texto');
          if (t) t.focus();
        },
      };
      try {
        await a.acao(copiaDoRascunho(), ctx);
      } catch (e) {
        if (!(e && e.bloqueio)) avisarErro(e);
      } finally {
        if (botao.isConnected) botao.disabled = false;
      }
      if (limpou && atual() && estado.modo === 'app' && lerRota().nome === 'registrar' && !estado.revisao) {
        limpar(main);
        desenharFormulario(main, atual);
        const t = $('#reg-texto');
        if (t) t.focus();
      }
    });
    return botao;
  }

  // EntreNos.abrirRegistrar({texto, data, contexto, humor, privado, juntar, organizar, aoSalvar}): abre
  // Registrar com o texto (juntar: embaixo do que já estava escrito, sem repetir) e, com `organizar`,
  // já vai para a revisão. `aoSalvar(id)` é chamado quando ESTE rascunho for salvo. Com uma revisão
  // aberta nada muda: a tela mostra a revisão e a resposta é {ok: false, motivo: 'revisao'}.
  function abrirRegistrar(opcoes) {
    const o = opcoes && typeof opcoes === 'object' ? opcoes : {};
    if (estado.modo !== 'app') return { ok: false, motivo: 'bloqueado' };
    if (estado.revisao) {
      irPara('#registrar');
      return { ok: false, motivo: 'revisao' };
    }
    const r = estado.rascunho;
    if (typeof o.texto === 'string') {
      if (o.juntar && r.texto.trim()) {
        if (!normalizar(r.texto).includes(normalizar(o.texto))) r.texto = `${r.texto.replace(/\s+$/, '')}\n\n${o.texto}`;
      } else {
        r.texto = o.texto;
        estado.aoSalvar = null;            // outro texto: quem esperava o anterior não espera este
      }
    }
    if (typeof o.data === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(o.data)) r.data = o.data;
    if (typeof o.contexto === 'string') r.contexto = o.contexto;
    if (typeof o.humor === 'string') r.humor = o.humor;
    if (typeof o.privado === 'boolean') r.privado = o.privado;
    if (typeof o.aoSalvar === 'function') estado.aoSalvar = [...(estado.aoSalvar || []), o.aoSalvar];
    estado.organizarAoAbrir = Boolean(o.organizar) && Boolean(r.texto.trim());
    estado.cursorNoFim = !estado.organizarAoAbrir;
    irPara('#registrar');
    return { ok: true };
  }

  // Depois de salvar: quem abriu o Registrar fica sabendo (e qualquer tela, pelo evento).
  function avisarRegistroSalvo(id, aoSalvar, privado) {
    if (id == null) return;
    for (const fn of lista(aoSalvar)) {
      try {
        const talvez = fn(id);
        if (talvez && typeof talvez.catch === 'function') talvez.catch(() => {});
      } catch {
        // o registro já está salvo; o recurso cuida do resto
      }
    }
    try {
      window.dispatchEvent(new CustomEvent('entrenos:registro-salvo', { detail: { id, privado: Boolean(privado) } }));
    } catch {
      // navegador sem CustomEvent: só o aoSalvar vale
    }
  }

  function novaRevisao(proposta, conhecidas) {
    const r = estado.rascunho;
    const rev = {
      texto: typeof proposta.texto === 'string' && proposta.texto ? proposta.texto : r.texto,
      data_registro: r.data || proposta.referencia || hojeIso(),
      contexto: r.contexto,
      humor: r.humor,
      privado: Boolean(r.privado),
      pessoas: lista(proposta.pessoas).map((p, i) => Object.assign(clonar(p), {
        _idx: i,
        _ignorar: false,
        _chaves: [chavePessoa(p.trecho), chavePessoa(p.nome), chavePessoa(p.relacao)].filter(Boolean),
      })),
      conhecidas: lista(conhecidas),
      // Guardada para "Voltar ao que as regras entenderam" depois de uma análise da IA.
      propostaRegras: clonar(proposta),
      ia: null,                // {usada, avisos, removidos} quando os cartões vieram da IA
      iaAvisos: null,          // avisos de uma tentativa em que nenhuma IA respondeu
      iaEsperando: false,
      // O que os recursos acharam (seção 20.0). Ficam ao trocar regras <-> IA (a IA nunca os vê).
      extras: extrasDaProposta(proposta),
      itens: [], relacoes: [], ambiguas: [], avisos: [], motor: 'regras',
    };
    trocarProposta(rev, proposta, null);
    return rev;
  }

  // {NOME: [extra]} da proposta -> lista plana, cada extra com o recurso e a decisão do usuário:
  // _estado 'pendente' (ainda não decidiu: não é guardado), 'confirmado' ou 'removido'.
  // `_privado` vem do servidor: guardar este extra deixa o registro 🔒 (fora da IA).
  function extrasDaProposta(proposta) {
    const brutos = proposta && proposta.extras && typeof proposta.extras === 'object' && !Array.isArray(proposta.extras)
      ? proposta.extras : {};
    const saida = [];
    for (const [recurso, itens] of Object.entries(brutos)) {
      for (const e of lista(itens)) {
        if (!e || typeof e !== 'object' || typeof e.tipo !== 'string' || typeof e.trecho !== 'string') continue;
        saida.push(Object.assign(clonar(e), { _recurso: recurso, _estado: 'pendente', _idx: saida.length }));
      }
    }
    return saida;
  }

  // Extra só informativo (registrarRevisaoExtra(..., {opcional: true})): pelo tipo, depois pelo NOME.
  function extraOpcional(e) {
    return revisaoOpcionais.has(e.tipo) || (!revisaoExtras.has(e.tipo) && revisaoOpcionais.has(e._recurso));
  }

  // Algum extra confirmado deixa o registro fora da IA? (o servidor confere de novo ao gravar)
  function privadoForcado(rev) {
    return lista(rev.extras).some((e) => e._privado && e._estado === 'confirmado');
  }

  // O texto tem algo que nunca vai para a IA (mesmo sem guardar o extra): o ✨ não aparece.
  function textoForaDaIA(rev) {
    return lista(rev.extras).some((e) => e._privado);
  }

  // Troca os cartões da revisão pelos de outra proposta (regras ou IA). As pessoas (com os
  // ajustes feitos) ficam; as datas ambíguas já escolhidas são escolhidas de novo.
  // `marcas`: Map índice -> {situacao, campos} vindo de /api/ia/analisar ("mudou", "novo").
  function trocarProposta(rev, proposta, marcas) {
    const perguntar = !(estado.config && estado.config.pergunta_ambigua === false);
    const escolhas = new Map(lista(rev.ambiguas).map((d) => [normalizar(d.expressao), d.escolhida]));
    rev.motor = proposta.motor || 'regras';
    rev.avisos = lista(proposta.avisos).filter((a) => typeof a === 'string');
    rev.itens = lista(proposta.itens).map((it, i) => {
      const item = Object.assign(clonar(it), { categorias: [...lista(it.categorias)], _idx: i, _removido: false });
      const marca = marcas && marcas.get(i);
      if (marca && (marca.situacao === 'mudou' || marca.situacao === 'novo')) {
        item._ia = { situacao: marca.situacao, campos: lista(marca.campos).filter((c) => typeof c === 'string') };
      }
      return item;
    });
    rev.relacoes = lista(proposta.relacoes).map((rel, i) => Object.assign(clonar(rel), { _idx: i, _decisao: null }));
    rev.ambiguas = perguntar ? lista(proposta.datas)
      .filter((d) => d && d.ambigua && lista(d.alternativas).length > 1)
      .map((d) => Object.assign(clonar(d), { escolhida: d.data || d.alternativas[0] })) : [];
    for (const d of rev.ambiguas) {
      const antes = escolhas.get(normalizar(d.expressao));
      if (antes && antes !== d.escolhida && lista(d.alternativas).includes(antes)) aplicarDataAmbigua(d, antes, rev);
    }
    rev._assinatura = assinaturaItens(rev.itens);
  }

  // Para saber se a pessoa já mexeu nos cartões (antes de trocá-los pelos da IA).
  function assinaturaItens(itens) {
    return JSON.stringify(lista(itens).map((it) => (it._removido ? null : semInternos(it))));
  }

  function desenharRevisao(main) {
    const rev = estado.revisao;
    const botaoSalvar = h('button', {
      type: 'button', id: 'rev-salvar', class: 'botao botao-primario botao-grande',
      'aria-keyshortcuts': 'Control+Enter', onclick: () => salvarRevisao(),
    }, '💾 Salvar');

    const areaIA = h('div', { class: 'cartao area-ia-revisao' });
    desenharAreaIARevisao(areaIA, rev);
    inserir(main,
      cabecalho('Confira antes de guardar', 'Nada foi salvo ainda. Ajuste o que precisar e toque em “Salvar”.', '🔍'),
      h('details', { class: 'cartao texto-original' },
        h('summary', null, `O que você escreveu (${dataBr(rev.data_registro)})`),
        h('p', { class: 'texto-livre' }, rev.texto)),
      rev.avisos.length ? h('div', { class: 'cartao aviso-suave', role: 'note' },
        h('h2', { class: 'titulo-pequeno' }, 'Para você conferir'),
        h('ul', null, rev.avisos.map((a) => h('li', null, a)))) : null,
      areaIA);

    const itensVisiveis = rev.itens.length;
    inserir(main, secao(`${rev.ia ? 'O que a IA entendeu' : 'O que eu entendi'}${itensVisiveis ? ` (${itensVisiveis})` : ''}`,
      itensVisiveis
        ? [h('p', { class: 'sutil' }, 'Toque no selo para trocar a certeza. Remova o que não fizer sentido.'),
          h('ul', { class: 'lista-cartoes' }, rev.itens.map((it) => cartaoRevisaoItem(it, rev)))]
        : h('p', { class: 'sutil' }, 'Não encontrei nada específico para organizar. O texto será guardado assim mesmo, e aparece na busca.')));

    if (rev.extras.length) inserir(main, secaoExtras(rev));

    const removidos = rev.ia ? lista(rev.ia.removidos) : [];
    if (removidos.length) {
      inserir(main, secao('🧹 O que a IA tirou',
        h('p', { class: 'sutil' }, 'Estes itens as regras tinham encontrado e a IA não manteve. Se algum fizer falta, use “Voltar ao que as regras entenderam”.'),
        h('ul', { class: 'lista-entradas lista-removidos-ia' }, removidos.map((m) => h('li', { class: 'entrada' },
          h('div', { class: 'entrada-topo' }, rotuloTipo({ tipo: m.tipo }),
            h('span', { class: 'entrada-texto' }, m.resumo || 'Item sem resumo')))))));
    }

    if (rev.ambiguas.length) {
      inserir(main, secao('📅 Qual data é?', h('ul', { class: 'lista-cartoes' }, rev.ambiguas.map((d) => cartaoDataAmbigua(d, rev)))));
    }
    if (rev.pessoas.length) {
      inserir(main, secao('👥 Pessoas citadas',
        h('p', { class: 'sutil' }, 'Confira o nome e a relação com ela. Pessoas novas entram em “Conhecendo Ela”.'),
        h('ul', { class: 'lista-cartoes' }, rev.pessoas.map((p) => cartaoRevisaoPessoa(p, rev)))));
    }
    if (rev.relacoes.length) {
      inserir(main, secao('🔗 Isso se relaciona com…',
        h('ul', { class: 'lista-cartoes' }, rev.relacoes.map((r) => cartaoRevisaoRelacao(r, rev)))));
    }

    inserir(main, legendaSelos(),
      h('div', { class: 'barra-salvar' },
        botaoSalvar,
        h('button', { type: 'button', class: 'botao botao-secundario', onclick: voltarAoTexto },
          '← Voltar', h('span', { class: 'rotulo-longo' }, ' ao texto')),
        h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto', onclick: descartarRevisao }, 'Descartar'),
        h('span', { class: 'sutil atalho' }, 'Ctrl+Enter salva')));
    estado.atalho = salvarRevisao;
  }

  // ---------------------------------------------------------------- revisão: o que os recursos acharam (seção 20.0)

  function secaoExtras(rev) {
    return secao(`🧩 Mais coisas que eu notei (${rev.extras.length})`,
      h('p', { class: 'sutil' }, 'Confirme o que quer guardar. O que não for confirmado não é salvo.'),
      h('ul', { class: 'lista-cartoes' }, rev.extras.map((e) => cartaoRevisaoExtra(e, rev))));
  }

  // "presente_desejado" -> "Presente desejado" (quando o recurso não desenha o próprio cartão).
  function rotuloExtra(e) {
    return capitalizar(String(e.tipo || e._recurso || 'informação').replace(/_/g, ' '));
  }

  function corpoExtraGenerico(e) {
    return [
      h('div', { class: 'item-topo' },
        h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, '🧩 '), rotuloExtra(e))),
      e.resumo ? h('p', { class: 'extra-resumo' }, e.resumo) : null,
      e.trecho ? citacao(e.trecho) : null,
    ];
  }

  // Um extra na revisão: o miolo é do recurso (registrarRevisaoExtra) ou o genérico (resumo e
  // trecho); confirmar / não guardar / desfazer são sempre daqui.
  function cartaoRevisaoExtra(e, rev) {
    const li = h('li', { class: 'cartao item-revisao extra-revisao' });
    const mudar = (novo, fala) => {
      e._estado = novo;
      desenhar();
      focarPrimeiro(li);
      falar(fala);
      if (e._privado) redesenharAreaIA(rev);       // a caixinha 🔒 trava ou destrava
    };
    function desenhar() {
      limpar(li);
      li.classList.toggle('removido', e._estado === 'removido');
      li.classList.toggle('confirmado', e._estado === 'confirmado');
      if (e._estado === 'removido') {
        inserir(li,
          h('p', { class: 'sutil' }, `“${encurtar(e.resumo || e.trecho, 90)}” não será guardado.`),
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => mudar('pendente', 'De volta: ainda não confirmado.') }, '↩️ Desfazer'));
        return;
      }
      const proprio = conteudoDeRecurso(revisaoExtras, e, e._recurso, { recurso: e._recurso, revisao: rev, redesenhar: desenhar });
      const confirmado = e._estado === 'confirmado';
      inserir(li,
        proprio || corpoExtraGenerico(e),
        e._privado ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '🔒 '),
          'Guardar isto deixa o registro fora da IA.') : null,
        extraOpcional(e) && e._estado === 'pendente'
          ? h('p', { class: 'sutil' }, 'É só um lembrete: dá para salvar sem decidir nada aqui.') : null,
        h('div', { class: 'acoes' },
          confirmado
            ? [h('span', { class: 'chip chip-confirmado' }, h('span', { 'aria-hidden': 'true' }, '✔️ '), 'Vai ser guardado'),
              h('button', { type: 'button', class: 'botao botao-texto', onclick: () => mudar('pendente', 'Desfeito: ainda não confirmado.') }, '↩️ Desfazer')]
            : [h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno', onclick: () => mudar('confirmado', 'Confirmado: vai ser guardado.') }, '✔️ Confirmar'),
              h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto', onclick: () => mudar('removido', 'Não vai ser guardado. Use Desfazer para trazer de volta.') }, '🗑️ Não guardar')]));
    }
    desenhar();
    return li;
  }

  // ---------------------------------------------------------------- revisão: ✨ Analisar a fundo com IA

  // Caixinha "não enviar para a IA", botão ✨ (com IA configurada e registro não privado),
  // quem respondeu e "Voltar ao que as regras entenderam".
  function desenharAreaIARevisao(caixa, rev) {
    limpar(caixa);
    const travado = privadoForcado(rev)
      ? 'Este registro vai ficar 🔒: ele tem informações que nunca vão para a IA. Para mudar isso, deixe de guardar essas informações.'
      : null;
    inserir(caixa, caixaPrivado('rev-privado', rev.privado, (marcado) => {
      rev.privado = marcado;
      estado.rascunho.privado = marcado;       // "Voltar ao texto" mantém a escolha
      desenharAreaIARevisao(caixa, rev);
      const c = caixa.querySelector('#rev-privado');
      if (c) c.focus();
      falar(marcado ? 'Marcado: este registro não vai para a IA.' : 'Desmarcado: este registro pode ir para a IA quando você pedir.');
    }, travado));
    if (rev.privado && rev.ia) {
      inserir(caixa, h('p', { class: 'nota-importante' }, 'Este texto já foi lido pela IA nesta revisão. Depois de salvo, ele não vai mais para a IA.'));
    }
    if (iaConfigurada() && !rev.privado && textoForaDaIA(rev)) {
      inserir(caixa, h('p', { class: 'dica' }, 'Este texto tem informações que nunca vão para a IA: quem organiza aqui são só as regras.'));
    }
    if (iaConfigurada() && !rev.privado && !textoForaDaIA(rev)) {
      const rotulo = rev.iaEsperando ? '✨ A IA está lendo…' : (rev.ia ? '✨ Analisar de novo com IA' : '✨ Analisar a fundo com IA');
      inserir(caixa,
        h('div', { class: 'barra-ia' },
          h('button', { type: 'button', class: 'botao botao-ia', disabled: rev.iaEsperando, onclick: () => analisarComIA(rev) }, rotulo),
          destinoIA()),
        rev.iaEsperando
          ? esperandoIA('A IA está lendo o seu texto. Os cartões trocam quando ela responder.')
          : h('p', { class: 'dica' }, 'Uma segunda leitura, mais atenta ao sentido. As datas continuam calculadas aqui, cada item cita o seu texto e nada é salvo sem você conferir.'));
    }
    if (!rev.ia && rev.iaAvisos && rev.iaAvisos.length) {
      inserir(caixa, h('div', { class: 'nota-ia', role: 'status' },
        h('p', null, h('strong', null, 'Os cartões continuam os das regras.')),
        h('ul', { class: 'avisos-ia' }, rev.iaAvisos.map((a) => h('li', null, a)))));
    }
    if (rev.ia) {
      inserir(caixa, h('div', { class: 'nota-ia', id: 'rev-ia-nota', tabindex: '-1' },
        h('p', null, h('strong', null, '✨ Estes cartões vieram da IA. '),
          'Os marcados com “mudou” ou “novo pela IA” são diferentes do que as regras entenderam. Confira antes de salvar.'),
        rodapeIA(rev.ia.usada, rev.ia.avisos),
        h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno', onclick: () => voltarAsRegras(rev) },
          '↩ Voltar ao que as regras entenderam')));
    }
  }

  function redesenharAreaIA(rev) {
    if (estado.revisao !== rev || estado.modo !== 'app' || lerRota().nome !== 'registrar') return;
    const caixa = $('#conteudo .area-ia-revisao');
    if (caixa) desenharAreaIARevisao(caixa, rev);
  }

  // Redesenha a revisão inteira (os cartões trocaram) e põe o foco onde faz sentido.
  function redesenharRevisaoNaTela(rev, foco) {
    if (estado.revisao !== rev || estado.modo !== 'app' || lerRota().nome !== 'registrar') return;
    const main = $('#conteudo');
    limpar(main);
    desenharRevisao(main);
    const alvo = (foco === 'nota' && $('#rev-ia-nota')) || main.querySelector('.area-ia-revisao .botao-ia')
      || main.querySelector('h1');
    if (alvo) alvo.focus();
  }

  async function analisarComIA(rev) {
    if (rev.iaEsperando || rev.privado || textoForaDaIA(rev) || estado.revisao !== rev) return;
    if (assinaturaItens(rev.itens) !== rev._assinatura) {
      const ok = await confirmar({
        titulo: 'Trocar os cartões pelos da IA?',
        texto: 'Você já ajustou alguns cartões. Quando a IA responder, os cartões trocam pelos dela e esses ajustes se perdem.\nAs pessoas e as datas que você escolheu continuam.',
        ok: 'Pedir à IA',
      });
      if (!ok || estado.revisao !== rev || rev.iaEsperando) return;
    }
    rev.iaEsperando = true;
    rev.iaAvisos = null;
    redesenharAreaIA(rev);
    let resposta = null;
    let erro = null;
    try {
      resposta = await api('POST', '/ia/analisar', { texto: rev.texto, data_registro: rev.data_registro, privado: false });
    } catch (e) {
      erro = e;
    }
    rev.iaEsperando = false;
    // Salvo, descartado ou bloqueado enquanto a IA pensava: a resposta não serve mais.
    if (estado.revisao !== rev || estado.modo !== 'app') return;
    if (erro) {
      if (erro.bloqueio) return;
      if (erro.status === 409) await atualizarInfoIA();
      avisarErro(erro);
      redesenharAreaIA(rev);
      return;
    }
    const trocou = aplicarRespostaIA(rev, resposta || {});
    if (trocou) {
      redesenharRevisaoNaTela(rev, 'nota');
      falar(`A IA respondeu. Os cartões foram trocados pelos dela${rev.ia && rev.ia.removidos.length ? ', e ela tirou alguns itens' : ''}.`);
    } else {
      redesenharAreaIA(rev);
      falar('Nenhuma IA respondeu. Os cartões continuam os das regras.');
    }
  }

  // {proposta, mudancas, ia_usada, avisos} de POST /api/ia/analisar. true se os cartões trocaram.
  function aplicarRespostaIA(rev, r) {
    const avisos = lista(r.avisos).filter((a) => typeof a === 'string');
    const proposta = r.proposta && typeof r.proposta === 'object' ? r.proposta : null;
    if (!r.ia_usada || !proposta) {
      rev.iaAvisos = avisos.length ? avisos : ['A IA não respondeu desta vez.'];
      return false;
    }
    const mudancas = lista(r.mudancas).filter((m) => m && typeof m === 'object');
    const marcas = new Map(mudancas.filter((m) => Number.isInteger(m.indice)).map((m) => [m.indice, m]));
    let removidos = mudancas.filter((m) => m.situacao === 'removido');
    if (!removidos.length) removidos = lista(r.removidos).filter((m) => m && typeof m === 'object');
    trocarProposta(rev, proposta, marcas);
    rev.ia = {
      usada: r.ia_usada,
      avisos,
      removidos: removidos.map((m) => ({ resumo: typeof m.resumo === 'string' ? m.resumo : '', tipo: m.tipo })),
    };
    rev.iaAvisos = null;
    return true;
  }

  function voltarAsRegras(rev) {
    if (estado.revisao !== rev || !rev.propostaRegras) return;
    trocarProposta(rev, rev.propostaRegras, null);
    rev.ia = null;
    rev.iaAvisos = null;
    redesenharRevisaoNaTela(rev, 'area');
    avisar('Voltei ao que as regras entenderam.');
  }

  // Marca do cartão que veio da IA: "mudou" (com o quê) ou "novo pela IA".
  function marcaIA(it) {
    if (!it._ia) return null;
    if (it._ia.situacao === 'novo') {
      return h('span', { class: 'chip chip-ia chip-ia-novo' }, h('span', { 'aria-hidden': 'true' }, '✨ '), 'novo pela IA');
    }
    return h('span', { class: 'chip chip-ia chip-ia-mudou' }, h('span', { 'aria-hidden': 'true' }, '✨ '), 'mudou');
  }

  function dicaMudancaIA(it) {
    if (!it._ia || it._ia.situacao !== 'mudou' || !it._ia.campos.length) return null;
    return h('p', { class: 'dica dica-ia' }, `A IA mudou: ${it._ia.campos.map((c) => NOMES_CAMPOS_IA[c] || c).join(', ')}.`);
  }

  function cartaoRevisaoItem(it, rev) {
    const li = h('li', { class: 'cartao item-revisao' });
    const desenhar = () => {
      limpar(li);
      li.classList.toggle('removido', it._removido);
      li.classList.toggle('item-ia-mudou', Boolean(it._ia && it._ia.situacao === 'mudou'));
      li.classList.toggle('item-ia-novo', Boolean(it._ia && it._ia.situacao === 'novo'));
      if (it._removido) {
        inserir(li,
          h('p', { class: 'sutil' }, `“${encurtar(it.resumo || it.trecho, 90)}” não será salvo.`),
          h('button', {
            type: 'button', class: 'botao botao-texto',
            onclick: () => { it._removido = false; desenhar(); focarPrimeiro(li); falar('Item de volta.'); },
          }, '↩️ Desfazer'));
        return;
      }
      const idResumo = novoId('resumo');
      inserir(li,
        h('div', { class: 'item-topo' }, rotuloTipo(it), marcaIA(it), botaoCerteza(it), caixaImportante(it)),
        dicaMudancaIA(it),
        h('div', { class: 'campo' },
          h('label', { for: idResumo, class: 'rotulo-pequeno' }, 'Resumo'),
          h('input', {
            id: idResumo, class: 'campo-resumo', type: 'text', value: it.resumo || '', maxlength: '300',
            oninput: (e) => { it.resumo = e.target.value; },
          })),
        it.trecho ? citacao(it.trecho) : null,
        campoPessoaItem(it, rev),
        camposDataItem(it),
        h('div', { class: 'rotulo-pequeno' }, 'Categorias'),
        editorCategorias(it.categorias),
        h('div', { class: 'acoes' },
          h('button', {
            type: 'button', class: 'botao botao-texto botao-perigo-texto',
            onclick: () => { it._removido = true; desenhar(); focarPrimeiro(li); falar('Item removido. Use Desfazer para trazer de volta.'); },
          }, '🗑️ Remover')));
    };
    it._redesenhar = desenhar;
    desenhar();
    return li;
  }

  function focarPrimeiro(raiz) {
    const alvo = raiz.querySelector('button, input, select, a[href]');
    if (alvo) alvo.focus();
  }

  function botaoCerteza(it) {
    const botao = h('button', { type: 'button', class: 'selo selo-botao' });
    const atualizar = () => {
      const chave = CERTEZAS[it.certeza] ? it.certeza : 'observado';
      const c = CERTEZAS[chave];
      botao.className = `selo selo-botao selo-${chave}`;
      limpar(botao);
      inserir(botao, h('span', { 'aria-hidden': 'true' }, c.emoji), ` ${c.nome}`);
      botao.title = `${c.nome}: ${c.explica}. Toque para trocar.`;
      botao.setAttribute('aria-label', `Certeza: ${c.nome}, ${c.explica}. Toque para trocar.`);
    };
    botao.addEventListener('click', () => {
      const i = CICLO_CERTEZA.indexOf(it.certeza);
      it.certeza = CICLO_CERTEZA[(i + 1) % CICLO_CERTEZA.length];
      atualizar();
      falar(`Certeza: ${CERTEZAS[it.certeza].nome}.`);
    });
    atualizar();
    return botao;
  }

  function caixaImportante(it) {
    const id = novoId('imp');
    return h('label', { class: 'marcar-importante', for: id },
      h('input', {
        type: 'checkbox', id, checked: Number(it.importancia) >= 3,
        onchange: (e) => { it.importancia = e.target.checked ? 3 : 2; },
      }), ' ⭐ Importante');
  }

  // Troca a data do item; se o resumo citava a data antiga ("… — 02/10/2026"), passa a citar a nova.
  function trocarDataItem(it, nova) {
    const antiga = it.data_evento ? dataBr(it.data_evento) : '';
    if (antiga && nova && typeof it.resumo === 'string' && it.resumo.includes(antiga)) {
      it.resumo = it.resumo.split(antiga).join(dataBr(nova));
    }
    it.data_evento = nova || null;
  }

  function camposDataItem(it) {
    if (!it.data_evento && !TIPOS_COM_DATA.has(it.tipo)) return null;
    const idData = novoId('data');
    const partes = [h('div', { class: 'campo-linha' },
      h('label', { for: idData }, it.tipo === 'data_importante' ? 'Data' : 'Quando'),
      h('input', {
        id: idData, type: 'date', value: it.data_evento || '',
        onchange: (e) => {
          // Sem redesenhar o cartão: o campo de data ainda pode estar sendo digitado.
          const cartao = e.target.closest('li');
          trocarDataItem(it, e.target.value || null);
          if (it.data_evento) it.precisao = 'dia';     // data escolhida à mão é um dia exato
          if (cartao) {
            const resumo = cartao.querySelector('.campo-resumo');
            if (resumo) resumo.value = it.resumo || '';
            cartao.querySelectorAll('.dica-precisao').forEach((d) => d.remove());
          }
        },
      }),
      it.data_expressao ? h('span', { class: 'sutil' }, `(“${it.data_expressao}”)`) : null)];
    if (it.data_evento && it.precisao === 'mes') partes.push(h('p', { class: 'dica dica-precisao' }, `Só o mês (${textoDataItem(it)}); o dia 1º é apenas um marcador.`));
    if (it.data_evento && it.precisao === 'semana') partes.push(h('p', { class: 'dica dica-precisao' }, 'Só a semana; a data mostrada é a segunda-feira.'));
    if (it.data_evento && it.precisao === 'ano') partes.push(h('p', { class: 'dica dica-precisao' }, 'Só o ano.'));
    if (it.recorrente) partes.push(h('p', { class: 'dica' }, '🔁 Repete todo ano.'));
    if (it.tipo === 'pendencia') {
      const idLembrar = novoId('lembrar');
      partes.push(h('div', { class: 'campo-linha' },
        h('label', { for: idLembrar }, 'Lembrar de perguntar em'),
        h('input', {
          id: idLembrar, type: 'date', value: it.lembrar_em || '',
          onchange: (e) => { it.lembrar_em = e.target.value || null; },
        })));
    }
    return h('div', { class: 'item-datas' }, partes);
  }

  function opcoesPessoaItem(rev) {
    const nomeDela = estado.config && estado.config.nome_dela;
    const opcoes = [{ v: '', rotulo: nomeDela ? `Ela (${nomeDela})` : 'Ela', pessoa: null, pessoa_id: null, chaves: [] }];
    const usados = new Set();
    for (const p of rev.pessoas) {
      if (p._ignorar || !rotuloPessoa(p)) continue;
      opcoes.push({
        v: `d${p._idx}`, rotulo: rotuloPessoaCompleto(p), pessoa: rotuloPessoa(p),
        pessoa_id: p.pessoa_id != null ? p.pessoa_id : null,
        chaves: [...p._chaves, chavePessoa(p.nome), chavePessoa(p.relacao)].filter(Boolean),
      });
      if (p.pessoa_id != null) usados.add(p.pessoa_id);
    }
    for (const c of rev.conhecidas) {
      if (usados.has(c.id) || !rotuloPessoa(c)) continue;
      opcoes.push({
        v: `c${c.id}`, rotulo: rotuloPessoaCompleto(c), pessoa: rotuloPessoa(c), pessoa_id: c.id,
        chaves: [chavePessoa(c.nome), chavePessoa(c.relacao), ...lista(c.apelidos).map(chavePessoa)].filter(Boolean),
      });
    }
    return opcoes;
  }

  function campoPessoaItem(it, rev) {
    const opcoes = opcoesPessoaItem(rev);
    let atual = '';
    if (it.pessoa || it.pessoa_id != null) {
      const k = chavePessoa(it.pessoa);
      const achada = opcoes.find((o) => o.v
        && ((it.pessoa_id != null && o.pessoa_id === it.pessoa_id) || (k && o.chaves.includes(k))));
      if (achada) {
        atual = achada.v;
      } else {
        opcoes.push({ v: 'x', rotulo: it.pessoa || 'outra pessoa', pessoa: it.pessoa, pessoa_id: it.pessoa_id != null ? it.pessoa_id : null, chaves: [] });
        atual = 'x';
      }
    }
    if (opcoes.length < 2) return null;
    const id = novoId('quem');
    return h('div', { class: 'campo-linha' },
      h('label', { for: id }, 'Sobre quem'),
      h('select', {
        id, value: atual,
        onchange: (e) => {
          const o = opcoes.find((x) => x.v === e.target.value);
          it.pessoa = o ? o.pessoa : null;
          it.pessoa_id = o ? o.pessoa_id : null;
        },
      }, opcoes.map((o) => h('option', { value: o.v }, o.rotulo))));
  }

  function cartaoDataAmbigua(d, rev) {
    const botoes = lista(d.alternativas).map((alt) => h('button', {
      type: 'button', class: 'botao botao-secundario botao-escolha',
      'aria-pressed': String(alt === d.escolhida),
      onclick: () => {
        aplicarDataAmbigua(d, alt, rev);
        botoes.forEach((b, i) => b.setAttribute('aria-pressed', String(d.alternativas[i] === alt)));
        falar(`Data escolhida: ${dataLonga(alt)}.`);
      },
    }, `${diaComSemana(alt)}`));
    return h('li', { class: 'cartao' },
      h('p', null, `Quando você escreveu “${d.expressao}”, quis dizer:`),
      h('div', { class: 'opcoes', role: 'group', 'aria-label': `Datas possíveis para “${d.expressao}”` }, botoes));
  }

  function aplicarDataAmbigua(d, alt, rev) {
    const chave = normalizar(d.expressao);
    for (const it of rev.itens) {
      const pelaExpressao = it.data_expressao && normalizar(it.data_expressao) === chave;
      const pelaData = !it.data_expressao && it.data_evento === d.escolhida
        && it.trecho && normalizar(it.trecho).includes(chave);
      if (!pelaExpressao && !pelaData) continue;
      if (it.tipo === 'pendencia' && it.data_evento && it.lembrar_em === somarDias(it.data_evento, 1)) {
        it.lembrar_em = somarDias(alt, 1);
      }
      trocarDataItem(it, alt);
      if (it._redesenhar) it._redesenhar();
    }
    d.escolhida = alt;
  }

  function cartaoRevisaoPessoa(p, rev) {
    const li = h('li', { class: 'cartao pessoa-revisao' });
    const antes = { nome: p.nome, relacao: p.relacao };
    const aoMudarNome = () => {
      renomearNosItens(rev, p, antes);
      antes.nome = p.nome;
      antes.relacao = p.relacao;
      redesenharItens(rev);
    };
    const desenhar = () => {
      limpar(li);
      li.classList.toggle('removido', p._ignorar);
      const titulo = h('p', { class: 'pessoa-trecho' }, h('span', { 'aria-hidden': 'true' }, '👤 '),
        `“${p.trecho || rotuloPessoa(p) || 'pessoa'}”`);
      if (p._ignorar) {
        inserir(li, titulo, h('p', { class: 'sutil' }, 'Não será tratada como pessoa.'),
          h('button', {
            type: 'button', class: 'botao botao-texto',
            onclick: () => { p._ignorar = false; desenhar(); focarPrimeiro(li); redesenharItens(rev); },
          }, '↩️ Desfazer'));
        return;
      }
      const idNome = novoId('pnome');
      const idRel = novoId('prel');
      const idQuem = novoId('pquem');
      const conhecida = p.pessoa_id != null ? rev.conhecidas.find((c) => c.id === p.pessoa_id) : null;
      const opcoesQuem = [h('option', { value: '' }, 'Não, é uma pessoa nova')];
      if (p.pessoa_id != null && !conhecida) opcoesQuem.push(h('option', { value: String(p.pessoa_id) }, rotuloPessoaCompleto(p)));
      rev.conhecidas.forEach((c) => opcoesQuem.push(h('option', { value: String(c.id) }, rotuloPessoaCompleto(c))));
      inserir(li, 
        h('div', { class: 'item-topo' }, titulo,
          h('span', { class: 'chip' }, p.pessoa_id != null ? 'Já conhecida' : 'Nova pessoa')),
        h('div', { class: 'grade-2' },
          campo('Nome', h('input', {
            id: idNome, type: 'text', value: p.nome || '', autocomplete: 'off', maxlength: '80',
            oninput: (e) => { p.nome = e.target.value.trim() || null; },
            onchange: aoMudarNome,
          })),
          campo('Relação com ela', h('input', {
            id: idRel, type: 'text', list: 'lista-relacoes', value: p.relacao || '', autocomplete: 'off', maxlength: '80',
            oninput: (e) => { p.relacao = e.target.value.trim() || null; },
            onchange: aoMudarNome,
          }))),
        opcoesQuem.length > 1 ? campo('É alguém que você já cadastrou?', h('select', {
          id: idQuem, value: p.pessoa_id != null ? String(p.pessoa_id) : '',
          onchange: (e) => {
            const v = e.target.value;
            const antigo = p.pessoa_id;
            p.pessoa_id = v ? Number(v) : null;
            p.nova = !v;
            // "Não, é uma pessoa nova" é escolha sua: o servidor não junta com um homônimo cadastrado.
            p.nova_escolhida = !v;
            for (const it of rev.itens) {
              if (antigo != null && it.pessoa_id === antigo) it.pessoa_id = p.pessoa_id;
            }
            desenhar();
            const sel = li.querySelector('select');
            if (sel) sel.focus();
            redesenharItens(rev);
          },
        }, opcoesQuem)) : null,
        h('div', { class: 'acoes' },
          h('button', {
            type: 'button', class: 'botao botao-texto botao-perigo-texto',
            onclick: () => { p._ignorar = true; desenhar(); focarPrimeiro(li); redesenharItens(rev); },
          }, 'Não é uma pessoa')));
    };
    desenhar();
    return li;
  }

  function renomearNosItens(rev, p, antes) {
    const antigas = new Set([chavePessoa(antes.nome), chavePessoa(antes.relacao)].filter(Boolean));
    const novo = rotuloPessoa(p);
    if (!novo || !antigas.size) return;
    for (const k of [chavePessoa(p.nome), chavePessoa(p.relacao)]) {
      if (k && !p._chaves.includes(k)) p._chaves.push(k);
    }
    for (const it of rev.itens) {
      if (it.pessoa && antigas.has(chavePessoa(it.pessoa))) it.pessoa = novo;
    }
  }

  function redesenharItens(rev) {
    for (const it of rev.itens) if (it._redesenhar) it._redesenhar();
  }

  function cartaoRevisaoRelacao(r, rev) {
    const [emoji, titulo] = TIPOS_RELACAO[r.tipo] || TIPOS_RELACAO.contexto;
    const item = r.item_novo != null ? rev.itens.find((i) => i._idx === r.item_novo) : null;
    const grupo = novoId('rel');
    const opcoes = r.tipo === 'contradicao'
      ? [['atualizar', 'Atualizar a preferência'], ['confirmada', 'Manter as duas'], ['descartada', 'Não tem a ver'], ['', 'Decidir depois']]
      : [['confirmada', 'Faz sentido'], ['descartada', 'Não tem a ver'], ['', 'Decidir depois']];
    return h('li', { class: ['cartao', 'relacao', r.tipo === 'contradicao' ? 'relacao-contradicao' : null] },
      h('p', { class: 'relacao-tipo' }, h('span', { 'aria-hidden': 'true' }, emoji + ' '), titulo),
      h('p', null, r.motivo || ''),
      r.sugestao ? h('p', { class: 'sugestao' }, h('span', { 'aria-hidden': 'true' }, '💡 '), r.sugestao) : null,
      item ? h('p', { class: 'sutil' }, 'Ligado a: ', item.resumo || item.trecho || '') : null,
      r.registro_antigo_id != null ? h('p', null, linkRegistro(r.registro_antigo_id, 'Ver o registro antigo'),
        h('span', { class: 'sutil' }, ' (sua revisão continua guardada em Registrar)')) : null,
      h('fieldset', { class: 'opcoes-relacao' },
        h('legend', null, 'O que fazer com isso?'),
        h('div', { class: 'opcoes' }, opcoes.map(([valor, rotulo]) => {
          const id = novoId('op');
          return h('label', { class: 'opcao', for: id },
            h('input', {
              type: 'radio', id, name: grupo, value: valor, checked: (r._decisao || '') === valor,
              onchange: () => { r._decisao = valor || null; },
            }), ' ', rotulo);
        })),
        r.tipo === 'contradicao' ? h('p', { class: 'dica' }, '“Atualizar” marca a informação antiga como “mudou”. Ela não some.') : null));
  }

  function montarCorpoRegistro(rev) {
    const mantidos = rev.itens.filter((i) => !i._removido);
    const mapa = new Map(mantidos.map((it, n) => [it._idx, n]));
    const itens = mantidos.map((it) => {
      const limpo = semInternos(it);
      limpo.resumo = String(limpo.resumo || '').trim() || limpo.trecho || '';
      return limpo;
    });
    const relacoes = rev.relacoes
      .filter((r) => r.item_novo == null || mapa.has(r.item_novo))
      .map((r) => Object.assign(semInternos(r), {
        item_novo: r.item_novo == null ? null : mapa.get(r.item_novo),
        status: r._decisao === 'confirmada' || r._decisao === 'descartada' ? r._decisao : 'pendente',
      }));
    // Só os extras confirmados, por recurso, sem as marcas internas (o servidor confere tudo de novo).
    const extras = Object.create(null);
    for (const e of lista(rev.extras)) {
      if (e._estado !== 'confirmado') continue;
      (extras[e._recurso] = extras[e._recurso] || []).push(semInternos(e));
    }
    return {
      corpo: {
        texto: rev.texto,
        data_registro: rev.data_registro,
        contexto: String(rev.contexto || '').trim() || null,
        humor: String(rev.humor || '').trim() || null,
        privado: Boolean(rev.privado) || privadoForcado(rev),
        itens,
        pessoas: rev.pessoas.filter((p) => !p._ignorar).map(semInternos),
        relacoes,
        extras,
      },
      atualizar: rev.relacoes.filter((r) => r._decisao === 'atualizar' && r.item_antigo_id != null
        && (r.item_novo == null || mapa.has(r.item_novo))),
    };
  }

  async function salvarRevisao() {
    const rev = estado.revisao;
    if (!rev || estado.salvando) return;
    // Extras ainda sem decisão não são guardados: melhor perguntar do que perder em silêncio
    // (menos os só informativos, como "um aprendizado seu", registrados com {opcional: true}).
    const pendentes = lista(rev.extras).filter((e) => e._estado === 'pendente' && !extraOpcional(e)).length;
    if (pendentes) {
      estado.salvando = true;            // um Ctrl+Enter a mais não abre outro diálogo
      const ok = await confirmar({
        titulo: 'Salvar sem essas informações?',
        texto: `${pendentes} ${plural(pendentes, 'informação', 'informações')} em “Mais coisas que eu notei” ainda não ${plural(pendentes, 'foi confirmada', 'foram confirmadas')} e não ${plural(pendentes, 'vai', 'vão')} ser ${plural(pendentes, 'guardada', 'guardadas')}.`,
        ok: 'Salvar assim',
        cancelar: 'Voltar e conferir',
      });
      estado.salvando = false;
      if (!ok || estado.revisao !== rev) return;
    }
    const botao = $('#rev-salvar');
    estado.salvando = true;
    if (botao) {
      botao.disabled = true;
      botao.textContent = 'Salvando…';
    }
    const { corpo, atualizar } = montarCorpoRegistro(rev);
    let id = null;
    let resposta = null;
    try {
      resposta = await api('POST', '/registros', corpo);
      id = resposta && resposta.id != null ? resposta.id : null;
    } catch (e) {
      estado.salvando = false;
      if (botao && botao.isConnected) {
        botao.disabled = false;
        botao.textContent = '💾 Salvar';
      }
      if (!e.bloqueio) avisarErro(e);
      return;
    }
    // Gravado: a revisão acaba aqui, mesmo que o passo seguinte falhe.
    const aoSalvar = estado.aoSalvar;
    estado.revisao = null;
    estado.rascunho = rascunhoVazio();
    estado.aoSalvar = null;
    estado.salvando = false;
    if (atualizar.length && id != null) await atualizarPreferenciasAntigas(id, atualizar);
    // `privado`: um extra deixou o registro 🔒; `avisos`: a parte de um recurso que não foi guardada.
    const forcado = Boolean(resposta && resposta.privado);
    avisarRegistroSalvo(id, aoSalvar, forcado || corpo.privado);
    avisar(forcado ? 'Guardado com carinho 💙 Ele ficou 🔒 fora da IA.' : 'Guardado com carinho 💙');
    lista(resposta && resposta.avisos).filter((a) => typeof a === 'string' && a)
      .slice(forcado ? 1 : 0).forEach((a) => avisar(a, 'erro'));
    irPara(id != null ? hrefRegistro(id) : '#registros');
  }

  // Contradição com "Atualizar a preferência": depois de gravar, resolve a relação pela API.
  async function atualizarPreferenciasAntigas(registroId, escolhidas) {
    try {
      const reg = await api('GET', `/registros/${registroId}`);
      const salvas = lista(reg && reg.relacoes);
      const usadas = new Set();
      for (const r of escolhidas) {
        const s = salvas.find((x) => !usadas.has(x.id) && x.tipo === r.tipo
          && x.item_antigo_id === r.item_antigo_id && x.status === 'pendente');
        if (s) {
          usadas.add(s.id);
          await api('POST', `/relacoes/${s.id}`, { acao: 'atualizar_preferencia' });
        } else {
          await api('PATCH', `/itens/${r.item_antigo_id}`, { status: 'mudou' });
        }
      }
    } catch (e) {
      if (!e.bloqueio) {
        avisar('O registro foi salvo, mas não consegui marcar a preferência antiga como “mudou”. Dá para fazer isso abrindo o registro.', 'erro');
      }
    }
  }

  async function voltarAoTexto() {
    const ok = await confirmar({
      titulo: 'Voltar ao texto?',
      texto: 'Você volta a editar o que escreveu. Os ajustes feitos nesta revisão serão perdidos.',
      ok: 'Voltar ao texto',
    });
    if (!ok) return;
    estado.revisao = null;
    rotear();
  }

  async function descartarRevisao() {
    const ok = await confirmar({
      titulo: 'Descartar este registro?',
      texto: 'O texto que você escreveu sai desta tela e nada é salvo.',
      ok: 'Descartar',
      perigo: true,
    });
    if (!ok) return;
    estado.revisao = null;
    estado.rascunho = rascunhoVazio();
    estado.aoSalvar = null;
    avisar('Descartado. Nada foi salvo.');
    rotear();
  }

  // ================================================================ tela: Conhecendo Ela

  async function telaEla(main, partes, atual) {
    // As abas do núcleo e, depois, as dos recursos (registrarAbaEla, seção 20.0).
    const abas = [...ABAS_ELA, ...abasEla];
    const aba = abas.some((a) => a.id === partes[0]) ? partes[0] : 'pessoas';
    inserir(main, carregando());
    const [perfil, cadastro] = await Promise.all([
      api('GET', '/perfil'),
      api('GET', '/pessoas').catch((e) => { if (e.bloqueio) throw e; return []; }),
    ]);
    if (!atual()) return;
    limpar(main);
    const p = perfil || {};
    const porId = new Map(lista(cadastro).map((c) => [c.id, c]));
    const pessoas = lista(p.pessoas).map((x) => Object.assign({}, porId.get(x.id) || {}, x));

    const painel = h('div', { class: 'painel-aba', role: 'tabpanel', id: 'painel-ela', tabindex: '0' });
    const botoes = abas.map((a) => h('button', {
      type: 'button', role: 'tab', id: `aba-${a.id}`, 'aria-controls': 'painel-ela',
      'aria-selected': String(a.id === aba), tabindex: a.id === aba ? '0' : '-1',
      onclick: () => escolher(a.id, true),
    }, a.emoji ? h('span', { class: 'aba-icone', 'aria-hidden': 'true' }, a.emoji + ' ') : null, a.nome));
    const paineis = {
      pessoas: () => painelPessoas(pessoas),
      preferencias: () => painelPreferencias(p.preferencias || {}),
      como: () => painelComoEsta(p.como_ela_esta || {}),
    };
    // Aba de recurso: render(painel, {perfil, pessoas, atual}) desenha dentro (pode buscar na API).
    for (const a of abasEla) {
      paineis[a.id] = () => montarDeRecurso(a.render, { perfil: p, pessoas, atual }, 'painel-recurso');
    }
    function escolher(id, focar) {
      botoes.forEach((b) => {
        const sel = b.id === `aba-${id}`;
        b.setAttribute('aria-selected', String(sel));
        b.tabIndex = sel ? 0 : -1;
        if (sel && focar) b.focus();
      });
      painel.setAttribute('aria-labelledby', `aba-${id}`);
      limpar(painel);
      inserir(painel, paineis[id]());
      try {
        history.replaceState(null, '', `#ela/${id}`);
      } catch {
        // sem history: a aba só não fica lembrada
      }
    }
    const barra = h('div', {
      class: 'abas', role: 'tablist', 'aria-label': 'Seções de Conhecendo Ela',
      onkeydown: (e) => {
        const i = botoes.indexOf(document.activeElement);
        if (i < 0) return;
        let novo = null;
        if (e.key === 'ArrowRight') novo = (i + 1) % botoes.length;
        else if (e.key === 'ArrowLeft') novo = (i - 1 + botoes.length) % botoes.length;
        else if (e.key === 'Home') novo = 0;
        else if (e.key === 'End') novo = botoes.length - 1;
        if (novo == null) return;
        e.preventDefault();
        escolher(abas[novo].id, true);
      },
    }, botoes);
    if (abas.length > ABAS_ELA.length) barra.classList.add('abas-muitas');    // quebram linha no celular

    inserir(main, cabecalhoEla(p.ela || {}), barra, painel, legendaSelos());
    escolher(aba, false);
  }

  function cabecalhoEla(ela) {
    const nome = ela.nome || (estado.config && estado.config.nome_dela);
    const chips = [];
    if (ela.aniversario) {
      chips.push(h('li', { class: 'chip-info' }, h('span', { 'aria-hidden': 'true' }, '🎂 '), `Aniversário: ${diaMes(ela.aniversario)}`));
    }
    if (ela.inicio_namoro) {
      const tempo = typeof ela.tempo_juntos === 'string' || typeof ela.tempo_juntos === 'number' ? ` · ${ela.tempo_juntos}` : '';
      chips.push(h('li', { class: 'chip-info' }, h('span', { 'aria-hidden': 'true' }, '💑 '), `Juntos desde ${dataBr(ela.inicio_namoro)}${tempo}`));
    }
    return h('header', { class: 'cabeca' },
      h('h1', { tabindex: '-1' }, h('span', { class: 'h1-icone', 'aria-hidden': 'true' }, '🌷 '), nome ? `Conhecendo ${nome}` : 'Conhecendo Ela'),
      chips.length
        ? h('ul', { class: 'chips-info' }, chips)
        : h('p', { class: 'sutil' }, 'Conte o nome e as datas dela em ', h('a', { href: '#ajustes' }, 'Ajustes'), '.'));
  }

  function painelPessoas(pessoas) {
    const caixa = h('div');
    const botaoNova = h('button', { type: 'button', class: 'botao botao-secundario' }, '➕ Adicionar pessoa');
    const fechar = () => { cartaoNova.hidden = true; botaoNova.hidden = false; botaoNova.focus(); };
    const cartaoNova = h('div', { class: 'cartao' }, h('h3', null, 'Nova pessoa'),
      formPessoa({}, async (dados) => {
        await api('POST', '/pessoas', dados);
        avisar('Pessoa adicionada.');
        rotear();
      }, fechar));
    cartaoNova.hidden = true;
    botaoNova.addEventListener('click', () => {
      cartaoNova.hidden = false;
      botaoNova.hidden = true;
      const primeiro = cartaoNova.querySelector('input');
      if (primeiro) primeiro.focus();
    });
    inserir(caixa, h('div', { class: 'barra-acoes' }, botaoNova), cartaoNova);
    if (!pessoas.length) {
      inserir(caixa, estadoVazio('Ninguém por aqui ainda. Quando você registrar algo sobre a família ou os amigos dela, eles aparecem aqui.', true));
    } else {
      inserir(caixa, h('ul', { class: 'lista-cartoes' }, pessoas.map(cartaoPessoa)));
    }
    return caixa;
  }

  function formPessoa(p, aoSalvar, aoCancelar) {
    const nome = h('input', { id: novoId('fnome'), type: 'text', value: p.nome || '', autocomplete: 'off', maxlength: '80' });
    const relacao = h('input', { id: novoId('frel'), type: 'text', list: 'lista-relacoes', value: p.relacao || '', autocomplete: 'off', maxlength: '80' });
    const apelidos = h('input', { id: novoId('fapel'), type: 'text', value: lista(p.apelidos).join(', '), autocomplete: 'off' });
    const aniversario = h('input', { id: novoId('faniv'), type: 'date', value: p.aniversario || '' });
    const observacoes = h('textarea', { id: novoId('fobs'), rows: '3', value: p.observacoes || '' });
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const salvar = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Salvar');
    const form = h('form', { class: 'form-pessoa', novalidate: true },
      h('div', { class: 'grade-2' }, campo('Nome', nome), campo('Relação com ela', relacao)),
      campo('Apelidos', apelidos, 'Separe por vírgula. Ex.: Mari, Nana'),
      campo('Aniversário', aniversario),
      campo('Observações', observacoes),
      erro,
      h('div', { class: 'acoes' }, salvar,
        h('button', { type: 'button', class: 'botao botao-secundario', onclick: aoCancelar }, 'Cancelar')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      erro.textContent = '';
      const dados = {
        nome: nome.value.trim() || null,
        relacao: relacao.value.trim() || null,
        apelidos: apelidos.value.split(',').map((s) => s.trim()).filter(Boolean),
        observacoes: observacoes.value.trim(),
        aniversario: aniversario.value || null,
      };
      if (!dados.nome && !dados.relacao) {
        erro.textContent = 'Informe pelo menos o nome ou a relação com ela.';
        nome.focus();
        return;
      }
      salvar.disabled = true;
      try {
        await aoSalvar(dados);
      } catch (erroApi) {
        if (!erroApi.bloqueio) erro.textContent = erroApi.message;
      } finally {
        salvar.disabled = false;
      }
    });
    return form;
  }

  function cartaoPessoa(p) {
    const rotulo = rotuloPessoa(p) || 'Sem nome';
    const li = h('li', { class: 'cartao pessoa', id: `pessoa-${p.id}` });
    const ver = () => {
      limpar(li);
      const mencoes = typeof p.mencoes === 'number'
        ? (p.mencoes === 1 ? 'Apareceu em 1 registro' : `Apareceu em ${p.mencoes} registros`) : null;
      inserir(li, 
        h('div', { class: 'pessoa-topo' },
          h('div', null, h('h3', null, rotulo), p.nome && p.relacao ? h('p', { class: 'sutil' }, p.relacao) : null),
          // A importância só ordena a lista; rótulo em gente ("apareceu pouco") soa frio.
          p.importancia === 'muito importante'
            ? h('span', { class: 'chip chip-importancia' }, 'aparece bastante nos seus registros') : null),
        lista(p.apelidos).length ? h('p', { class: 'sutil' }, `Também chamada de: ${p.apelidos.join(', ')}`) : null,
        mencoes ? h('p', { class: 'sutil' }, mencoes) : null,
        p.aniversario ? h('p', null, h('span', { 'aria-hidden': 'true' }, '🎂 '), `Aniversário: ${diaMes(p.aniversario)}`) : null,
        p.observacoes ? h('p', { class: 'texto-livre' }, p.observacoes) : null,
        blocoEntradas('O que você sabe', p.infos),
        blocoEntradas('Datas', p.datas),
        lista(p.ultimas_mencoes).length ? h('div', { class: 'bloco-entradas' },
          h('h4', null, 'Últimas vezes que apareceu'),
          h('ul', { class: 'lista-entradas' }, p.ultimas_mencoes.map((m) => h('li', { class: 'entrada' },
            m.trecho ? citacao(m.trecho) : null,
            h('p', { class: 'entrada-rodape sutil' }, dataBr(m.data || m.data_registro),
              m.registro_id != null ? [' · ', linkRegistro(m.registro_id, 'ver registro')] : null))))) : null,
        h('div', { class: 'acoes' },
          h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno', onclick: editar }, '✏️ Editar'),
          h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto', onclick: apagar }, '🗑️ Apagar')));
    };
    function editar() {
      limpar(li);
      const form = formPessoa(p, async (dados) => {
        await api('PATCH', `/pessoas/${p.id}`, dados);
        avisar('Pessoa atualizada.');
        rotear();
      }, () => { ver(); focarPrimeiro(li.querySelector('.acoes')); });
      inserir(li, h('h3', null, `Editar ${rotulo}`), form);
      const primeiro = form.querySelector('input');
      if (primeiro) primeiro.focus();
    }
    async function apagar() {
      const ok = await confirmar({
        titulo: `Apagar ${rotulo}?`,
        texto: 'Os registros continuam guardados; só o cadastro desta pessoa sai. Não dá para desfazer.',
        ok: 'Apagar',
        perigo: true,
      });
      if (!ok) return;
      try {
        await api('DELETE', `/pessoas/${p.id}`);
        avisar('Pessoa apagada.');
        rotear();
      } catch (e) {
        if (!e.bloqueio) avisarErro(e);
      }
    }
    ver();
    return li;
  }

  function ordemCategoria(a, b) {
    const ia = CATEGORIAS.indexOf(a);
    const ib = CATEGORIAS.indexOf(b);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || a.localeCompare(b, 'pt-BR');
  }

  function painelPreferencias(prefs) {
    const temAlgo = (grupos) => grupos && GRUPOS_PREFERENCIA.some(([k]) => lista(grupos[k]).length);
    const cats = Object.keys(prefs).filter((c) => temAlgo(prefs[c])).sort(ordemCategoria);
    if (!cats.length) {
      return estadoVazio('Ainda não sei do que ela gosta. Quando você registrar “ela adora tulipas” ou “ela não come camarão”, aparece aqui.', true);
    }
    const idFiltro = novoId('filtro');
    const conteudo = h('div', { class: 'lista-cartoes' });
    const filtro = h('select', {
      id: idFiltro,
      onchange: () => desenhar(),
    }, h('option', { value: '' }, 'Todas as categorias'), cats.map((c) => h('option', { value: c }, c)));
    const desenhar = () => {
      limpar(conteudo);
      for (const c of cats) {
        if (filtro.value && filtro.value !== c) continue;
        const grupos = prefs[c];
        const id = novoId('cat');
        inserir(conteudo, h('section', { class: 'cartao categoria-pref', 'aria-labelledby': id },
          h('h3', { id }, c),
          GRUPOS_PREFERENCIA.map(([k, emoji, nome, explica]) => (lista(grupos[k]).length
            ? h('div', { class: `bloco-entradas grupo-${k}` },
              h('h4', null, h('span', { 'aria-hidden': 'true' }, emoji + ' '), nome,
                explica ? h('span', { class: 'sutil explica' }, ` (${explica})`) : null),
              h('ul', { class: 'lista-entradas' }, grupos[k].map(linhaEntrada)))
            : null))));
      }
    };
    desenhar();
    return h('div', null, h('div', { class: 'campo campo-filtro' }, h('label', { for: idFiltro }, 'Mostrar'), filtro), conteudo);
  }

  function painelComoEsta(como) {
    const vazio = GRUPOS_COMO_ESTA.every(([k]) => !lista(como[k]).length);
    const nota = h('p', { class: 'nota-cuidado' }, 'Aqui não tem diagnóstico: só o que você registrou, com as palavras de vocês.');
    if (vazio) {
      return h('div', null, nota, estadoVazio('Nada recente por aqui. Quando ela contar como está se sentindo, aparece aqui.', true));
    }
    return h('div', null, nota, GRUPOS_COMO_ESTA.map(([k, emoji, titulo]) => {
      const id = novoId('como');
      const entradas = lista(como[k]);
      return h('section', { class: 'cartao', 'aria-labelledby': id },
        h('h3', { id }, h('span', { 'aria-hidden': 'true' }, emoji + ' '), titulo),
        entradas.length
          ? h('ul', { class: 'lista-entradas' }, entradas.map(linhaEntrada))
          : vazioPequeno('Nada registrado.'));
    }));
  }

  // ================================================================ tela: Datas

  async function telaDatas(main, _partes, atual) {
    inserir(main, carregando());
    const dados = await api('GET', '/datas?dias=365');
    if (!atual()) return;
    limpar(main);
    inserir(main, cabecalho('Datas', 'O que vem pela frente nos próximos 12 meses.', '📅'));
    const eventos = extrairLista(dados)
      .filter((e) => e && typeof e.data === 'string')
      .sort((a, b) => (a.data < b.data ? -1 : a.data > b.data ? 1 : 0));
    if (!eventos.length) {
      inserir(main, estadoVazio('Nenhuma data por aqui ainda. Quando ela contar de um aniversário, uma prova ou uma viagem, a data aparece aqui.', true));
    } else {
      let mesAtual = null;
      let ul = null;
      for (const e of eventos) {
        const mes = e.data.slice(0, 7);
        if (mes !== mesAtual) {
          mesAtual = mes;
          ul = h('ul', { class: 'lista-proximos' });
          inserir(main, secao(capitalizar(mesAno(e.data)), ul));
        }
        inserir(ul, linhaProxima(e));
      }
    }
    inserir(main, h('section', { class: 'cartao', 'aria-labelledby': 'antecedencias-titulo' },
      h('h2', { id: 'antecedencias-titulo' }, '🔔 Avisos com antecedência'),
      h('p', { class: 'sutil' }, 'Com quantos dias de antecedência a página Hoje te lembra de cada data.'),
      editorAntecedencias()));
  }

  function editorAntecedencias() {
    // No dia e na véspera o aviso sempre aparece (como Importante): essas duas não são opção.
    const atuais = new Set(lista(estado.config && estado.config.antecedencias).map(Number).filter((d) => d >= 2));
    const valores = [...new Set([...ANTECEDENCIAS_COMUNS.filter((d) => d >= 2), ...atuais])].sort((a, b) => b - a);
    const caixaOpcoes = h('div', { class: 'opcoes' });
    const rotuloDias = (d) => `${d} dias antes`;
    const acrescentarOpcao = (d, marcado) => {
      const id = novoId('ant');
      inserir(caixaOpcoes, h('label', { class: 'opcao', for: id },
        h('input', { type: 'checkbox', id, value: String(d), checked: marcado }), ' ', rotuloDias(d)));
    };
    valores.forEach((d) => acrescentarOpcao(d, atuais.has(d)));
    const idOutro = novoId('outro');
    const outro = h('input', { id: idOutro, type: 'number', min: '2', max: '366', step: '1', inputmode: 'numeric', class: 'numero-curto' });
    const botaoOutro = h('button', {
      type: 'button', class: 'botao botao-secundario botao-pequeno',
      onclick: () => {
        const d = Number(outro.value);
        if (!Number.isInteger(d) || d < 2 || d > 366) {
          avisar('Use um número de 2 a 366 dias (no dia e na véspera o aviso já aparece sempre).', 'erro');
          outro.focus();
          return;
        }
        const existente = caixaOpcoes.querySelector(`input[value="${d}"]`);
        if (existente) existente.checked = true;
        else acrescentarOpcao(d, true);
        outro.value = '';
        falar(`${rotuloDias(d)} acrescentado. Lembre de salvar.`);
      },
    }, 'Acrescentar');
    const form = h('form', { class: 'form-antecedencias', novalidate: true },
      h('fieldset', null, h('legend', null, 'Me avise'), caixaOpcoes),
      h('p', { class: 'dica' }, 'No dia e na véspera o aviso aparece sempre, como Importante.'),
      h('div', { class: 'campo-linha' }, h('label', { for: idOutro }, 'Outro prazo (dias)'), outro, botaoOutro),
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario' }, 'Salvar antecedências')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const marcados = [...caixaOpcoes.querySelectorAll('input:checked')].map((i) => Number(i.value));
      if (!marcados.length) {
        const ok = await confirmar({
          titulo: 'Tirar todos os avisos antecipados?',
          texto: 'Sem nenhum prazo marcado, a página Hoje só vai avisar no dia e na véspera de cada data.',
          ok: 'Salvar assim',
        });
        if (!ok) return;
      }
      await salvarConfig({ antecedencias: marcados }, 'Antecedências salvas.');
    });
    return form;
  }

  async function salvarConfig(mudancas, mensagem) {
    try {
      await api('PATCH', '/config', mudancas);
    } catch (e) {
      if (!e.bloqueio) avisarErro(e);
      return false;
    }
    try {
      estado.config = await api('GET', '/config');
    } catch (e) {
      if (e.bloqueio) return false;
      estado.config = Object.assign({}, estado.config, mudancas);
    }
    avisar(mensagem || 'Salvo.');
    return true;
  }

  // ================================================================ tela: Buscar

  async function telaBuscar(main, _partes, atual) {
    const entrada = h('input', {
      id: 'busca-q', type: 'search', autocomplete: 'off', maxlength: '300',
      placeholder: 'Ex.: qual a comida que ela mais gosta?',
      value: estado.busca ? estado.busca.pergunta : '',
    });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Buscar');
    const resultado = h('div', { class: 'resultado-busca', 'aria-live': 'polite' });
    const buscar = async (pergunta) => {
      const q = String(pergunta || '').trim();
      if (!q) {
        entrada.focus();
        return;
      }
      botao.disabled = true;
      resultado.setAttribute('aria-busy', 'true');
      limpar(resultado);
      inserir(resultado, carregando('Procurando…'));
      try {
        const r = await api('GET', `/busca?q=${encodeURIComponent(q)}`);
        estado.busca = { pergunta: q, resposta: r || {} };
        if (!atual()) return;
        desenharResposta(resultado, estado.busca.resposta);
      } catch (e) {
        if (e.bloqueio || !atual()) return;
        limpar(resultado);
        inserir(resultado, h('p', { class: 'erro-campo' }, e.message));
      } finally {
        botao.disabled = false;
        resultado.removeAttribute('aria-busy');
      }
    };
    const form = h('form', {
      role: 'search', class: 'form-busca', novalidate: true,
      onsubmit: (e) => { e.preventDefault(); buscar(entrada.value); },
    }, h('label', { for: 'busca-q', class: 'so-leitor' }, 'Sua pergunta'),
    h('div', { class: 'linha-busca' }, entrada, botao));
    const exemplos = h('div', { class: 'exemplos' },
      h('p', { class: 'sutil', id: 'exemplos-titulo' }, 'Experimente perguntar:'),
      h('ul', { class: 'chips', 'aria-labelledby': 'exemplos-titulo' }, EXEMPLOS_BUSCA.map((q) => h('li', null,
        h('button', {
          type: 'button', class: 'chip chip-botao',
          onclick: () => { entrada.value = q; buscar(q); },
        }, q)))));
    inserir(main, cabecalho('Buscar', 'Pergunte do seu jeito. Eu só respondo com o que você registrou.', '🔎'),
      form, exemplos, resultado);
    if (estado.busca && estado.busca.resposta) desenharResposta(resultado, estado.busca.resposta);
  }

  function desenharResposta(caixa, r) {
    limpar(caixa);
    const encontrados = lista(r.encontrados);
    const busca = estado.busca;
    inserir(caixa, h('div', { class: 'cartao resposta' },
      h('p', { class: 'resposta-texto' }, r.texto || 'Não encontrei essa informação.'),
      encontrados.length
        ? h('ul', { class: 'lista-encontrados' }, encontrados.map(linhaEncontrado))
        : h('p', { class: 'sutil' }, 'Tente com outras palavras, ou registre algo sobre isso quando ela comentar.')),
    busca && busca.resposta === r ? blocoPerguntarIA(busca) : null,
    encontrados.length ? legendaSelos() : null);
  }

  // "✨ Perguntar à IA": a busca das regras acha os trechos e a IA escreve a resposta com eles.
  function blocoPerguntarIA(busca) {
    if (!iaConfigurada() || !busca.pergunta) return null;
    if (busca.resposta && busca.resposta.para_ia === false && lista(busca.resposta.encontrados).length) {
      // Tudo o que a busca achou está em registros 🔒: não há o que mandar, então nem há ✨.
      return h('p', { class: 'dica bloco-ia-busca' }, h('span', { 'aria-hidden': 'true' }, '🔒 '),
        'Os registros achados estão marcados para não ir para a IA, então não há o que perguntar a ela.');
    }
    const saida = h('div', { class: 'saida-ia' });
    const botao = h('button', { type: 'button', class: 'botao botao-ia' }, '✨ Perguntar à IA');
    const bloco = h('div', { class: 'bloco-ia-busca' },
      h('div', { class: 'barra-ia' }, botao, destinoIA()),
      h('p', { class: 'dica' }, 'A IA recebe só a pergunta e os trechos encontrados acima (sem os registros marcados 🔒).'),
      saida);
    botao.addEventListener('click', async () => {
      botao.disabled = true;
      botao.textContent = '✨ Perguntando…';
      limpar(saida);
      inserir(saida, esperandoIA('Perguntando à IA…'));
      try {
        const r = (await api('POST', '/ia/busca', { q: busca.pergunta })) || {};
        if (estado.busca !== busca) return;          // outra pergunta foi feita enquanto isso
        busca.ia = r;
        if (!saida.isConnected) return;
        desenharRespostaIA(saida, r);
        falar(r.ia_usada ? 'A IA respondeu.'
          : (lista(r.encontrados).length ? 'A IA não respondeu desta vez.' : 'Nada que pudesse ir para a IA; nada saiu daqui.'));
      } catch (e) {
        if (e.bloqueio || !saida.isConnected) return;
        limpar(saida);
        inserir(saida, h('p', { class: 'erro-campo' }, e.message));
        if (semIAAgora(e)) botao.hidden = true;
      } finally {
        botao.disabled = false;
        botao.textContent = '✨ Perguntar à IA';
      }
    });
    if (busca.ia) desenharRespostaIA(saida, busca.ia);
    return bloco;
  }

  function desenharRespostaIA(caixa, r) {
    limpar(caixa);
    if (!r.ia_usada) {
      const avisos = lista(r.avisos).filter((a) => typeof a === 'string');
      // Sem trechos na resposta, o servidor nem chamou a IA (nada achado, ou só registros 🔒).
      // Com trechos, a IA foi chamada e não respondeu: ficou a resposta das regras.
      const naoSaiu = !lista(r.encontrados).length;
      inserir(caixa, h('div', { class: 'cartao resposta-ia', role: 'status' },
        h('p', null, naoSaiu ? 'Não encontrei registros sobre isso que pudessem ir para a IA, então nada saiu daqui.' : 'A IA não respondeu desta vez.'),
        rodapeIA(null, avisos)));
      return;
    }
    const encontrados = lista(r.encontrados);
    const id = novoId('resp-ia');
    inserir(caixa, h('section', { class: 'cartao resposta-ia', 'aria-labelledby': id },
      h('h2', { id, class: 'titulo-pequeno' }, h('span', { 'aria-hidden': 'true' }, '✨ '), 'Resposta da IA'),
      h('p', { class: 'resposta-texto texto-livre' }, r.texto || 'Não encontrei essa informação.'),
      h('p', { class: 'dica' }, 'Escrita pela IA a partir dos seus registros. Confira nos registros citados.'),
      encontrados.length ? h('ul', { class: 'lista-encontrados' }, encontrados.map(linhaEncontrado)) : null,
      rodapeIA(r.ia_usada, r.avisos)));
  }

  function linhaEncontrado(e) {
    const rodape = [e.data_registro ? `Registrado em ${dataBr(e.data_registro)}` : null, e.quando || null]
      .filter(Boolean).join(' · ');
    const corpo = [
      h('span', { class: 'encontrado-topo' }, e.certeza ? selo(e.certeza) : null,
        e.categoria ? h('span', { class: 'chip' }, e.categoria) : null),
      h('span', { class: 'encontrado-resumo' }, e.resumo || ''),
      e.trecho && e.trecho !== e.resumo ? h('span', { class: 'citacao citacao-linha' }, `“${e.trecho}”`) : null,
      rodape ? h('span', { class: 'sutil' }, rodape) : null,
    ];
    return h('li', null, e.registro_id != null
      ? h('a', { href: hrefRegistro(e.registro_id), class: 'encontrado-link' }, corpo)
      : h('div', { class: 'encontrado-link' }, corpo));
  }

  // ================================================================ tela: Registros

  async function telaRegistros(main, partes, atual) {
    if (partes[0] && /^\d+$/.test(partes[0])) {
      await detalheRegistro(main, Number(partes[0]), atual);
      return;
    }
    inserir(main, carregando());
    const primeiros = lista(await api('GET', `/registros?limite=${POR_PAGINA}`));
    if (!atual()) return;
    limpar(main);
    inserir(main, cabecalho('Registros', 'Tudo o que você guardou, do mais novo para o mais antigo.', '📖'));
    if (!primeiros.length) {
      inserir(main, estadoVazio(VAZIO, true));
      return;
    }
    const linha = h('div', { class: 'linha-tempo' });
    const mais = h('button', { type: 'button', class: 'botao botao-secundario' }, 'Carregar mais');
    let ultimoMes = null;
    let ultimoId = null;
    let ul = null;
    const acrescentar = (lote) => {
      let primeiroNovo = null;
      for (const r of lote) {
        const mes = String(r.data_registro || '').slice(0, 7);
        if (mes !== ultimoMes || !ul) {
          ultimoMes = mes;
          const id = novoId('mes');
          ul = h('ul', { class: 'lista-registros', 'aria-labelledby': id });
          inserir(linha, h('h2', { class: 'mes-titulo', id }, capitalizar(mesAno(r.data_registro)) || 'Sem data'), ul);
        }
        const li = itemLinhaTempo(r);
        inserir(ul, li);
        if (!primeiroNovo) primeiroNovo = li;
        ultimoId = r.id;
      }
      return primeiroNovo;
    };
    acrescentar(primeiros);
    mais.hidden = primeiros.length < POR_PAGINA;
    mais.addEventListener('click', async () => {
      mais.disabled = true;
      try {
        const lote = lista(await api('GET', `/registros?limite=${POR_PAGINA}&antes_de=${encodeURIComponent(ultimoId)}`));
        if (!atual()) return;
        const primeiroNovo = acrescentar(lote);
        mais.hidden = lote.length < POR_PAGINA;
        if (primeiroNovo) focarPrimeiro(primeiroNovo);
      } catch (e) {
        if (!e.bloqueio) avisarErro(e);
      } finally {
        mais.disabled = false;
      }
    });
    inserir(main, linha, h('div', { class: 'centro' }, mais));
  }

  function itemLinhaTempo(r) {
    const pessoas = lista(r.pessoas).map(rotuloPessoa).filter(Boolean);
    const meta = [
      typeof r.qtd_itens === 'number' ? (r.qtd_itens === 1 ? '1 item' : `${r.qtd_itens} itens`) : null,
      lista(r.categorias).slice(0, 3).join(', ') || null,
      pessoas.length ? `👥 ${pessoas.join(', ')}` : null,
      r.privado ? '🔒 não vai para a IA' : null,
    ].filter(Boolean).join(' · ');
    return h('li', { class: 'marco' },
      h('a', { href: hrefRegistro(r.id), class: 'marco-link' },
        h('span', { class: 'marco-data' }, diaComSemana(r.data_registro)),
        h('span', { class: 'marco-texto' }, encurtar(r.texto || '', 180)),
        meta ? h('span', { class: 'sutil' }, meta) : null));
  }

  async function detalheRegistro(main, id, atual) {
    inserir(main, carregando());
    let reg;
    try {
      reg = await api('GET', `/registros/${id}`);
    } catch (e) {
      if (e.status !== 404) throw e;
      if (!atual()) return;
      limpar(main);
      inserir(main, h('a', { href: '#registros', class: 'voltar' }, '← Registros'),
        cabecalho('Registro não encontrado'),
        estadoVazio('Esse registro não existe mais. Talvez tenha sido apagado.'));
      return;
    }
    if (!atual()) return;
    limpar(main);
    reg = reg || {};
    const itens = lista(reg.itens);
    const pessoas = lista(reg.pessoas);
    const relacoes = lista(reg.relacoes);
    const recarregar = () => rotear();

    const apagarRegistro = async () => {
      const ok = await confirmar({
        titulo: 'Apagar este registro?',
        texto: 'O texto, os itens e as ligações dele somem de vez, inclusive da busca e das cópias automáticas.\nCópias que você guardou em outro lugar (como um pendrive) continuam com ele.\nNão dá para desfazer.',
        ok: 'Apagar de vez',
        perigo: true,
      });
      if (!ok) return;
      try {
        await api('DELETE', `/registros/${id}`);
        avisar('Registro apagado.');
        irPara('#registros');
      } catch (e) {
        if (!e.bloqueio) avisarErro(e);
      }
    };

    // "🔒 Não enviar este registro para a IA": PATCH /api/registros/{id} {privado}.
    const marcarPrivado = async (marcado, caixa) => {
      caixa.disabled = true;
      try {
        await api('PATCH', `/registros/${id}`, { privado: marcado });
        reg.privado = marcado;
        avisar(marcado ? 'Pronto: este registro não vai mais para a IA.' : 'Pronto: este registro pode ir para a IA quando você tocar num ✨.');
      } catch (e) {
        if (e.bloqueio) return;
        caixa.checked = !marcado;
        avisarErro(e);
      } finally {
        caixa.disabled = false;
      }
    };

    inserir(main,
      h('a', { href: '#registros', class: 'voltar' }, '← Registros'),
      cabecalho(`Registro de ${dataBr(reg.data_registro)}`,
        reg.criado_em ? `Escrito em ${dataBr(reg.criado_em)} às ${horaBr(reg.criado_em)}` : null),
      h('section', { class: 'cartao', 'aria-label': 'Texto do registro' },
        h('p', { class: 'texto-livre texto-registro' }, reg.texto || ''),
        reg.contexto || reg.humor ? h('ul', { class: 'chips-info' },
          reg.contexto ? h('li', { class: 'chip-info' }, h('span', { 'aria-hidden': 'true' }, '📍 '), reg.contexto) : null,
          reg.humor ? h('li', { class: 'chip-info' }, h('span', { 'aria-hidden': 'true' }, '🙂 '), `Ela estava: ${reg.humor}`) : null) : null),
      h('div', { class: 'cartao cartao-privado' }, caixaPrivado(novoId('det-privado'), Boolean(reg.privado), marcarPrivado,
        reg.fora_da_ia === true ? 'Este registro fica 🔒 fora da IA: ele tem informações que nunca vão para a IA.' : null)),
      secao('O que foi organizado', itens.length
        ? h('ul', { class: 'lista-cartoes' }, itens.map((it) => cartaoItemSalvo(it, recarregar)))
        : h('p', { class: 'sutil' }, 'Nenhum item separado. O texto está guardado e aparece na busca.')),
      pessoas.length ? secao('Pessoas citadas', h('ul', { class: 'chips' }, pessoas.map((p) => h('li', null,
        h('a', { class: 'chip chip-link', href: '#ela/pessoas' }, rotuloPessoaCompleto(p)))))) : null,
      relacoes.length ? secao('Relações com outros registros',
        h('ul', { class: 'lista-cartoes' }, relacoes.map((r) => cartaoRelacaoSalva(r, itens, recarregar)))) : null,
      legendaSelos(),
      h('section', { class: 'zona-perigo', 'aria-labelledby': 'apagar-titulo' },
        h('h2', { id: 'apagar-titulo' }, 'Apagar'),
        h('p', { class: 'sutil' }, 'Apagar remove o texto, os itens e as ligações deste registro, também das cópias automáticas. Cópias guardadas por você em outro lugar não mudam. Não dá para desfazer.'),
        h('button', { type: 'button', class: 'botao botao-perigo', onclick: apagarRegistro }, '🗑️ Apagar este registro')));
  }

  function detalhesItem(it) {
    const partes = [];
    if (it.pessoa) partes.push(h('span', null, h('span', { 'aria-hidden': 'true' }, '👤 '), `Sobre: ${it.pessoa}`));
    if (it.data_evento) {
      partes.push(h('span', null, h('span', { 'aria-hidden': 'true' }, '📅 '), textoDataItem(it),
        it.data_expressao ? ` (“${it.data_expressao}”)` : '', it.recorrente ? ' · todo ano' : ''));
    }
    if (it.lembrar_em && it.tipo === 'pendencia') {
      partes.push(h('span', null, h('span', { 'aria-hidden': 'true' }, '⏳ '), `Lembrar em ${dataBr(it.lembrar_em)}`));
    }
    return partes.length ? h('p', { class: 'item-detalhes sutil' }, partes) : null;
  }

  function cartaoItemSalvo(it, recarregar) {
    const li = h('li', { class: ['cartao', 'item-salvo', it.status && it.status !== 'ativo' ? 'item-inativo' : null] });
    const ver = () => {
      limpar(li);
      inserir(li, 
        h('div', { class: 'item-topo' }, rotuloTipo(it), selo(it.certeza),
          Number(it.importancia) >= 3 ? h('span', { class: 'chip chip-importante' }, '⭐ Importante') : null,
          it.status && it.status !== 'ativo' ? h('span', { class: 'chip' }, STATUS_ITEM[it.status] || it.status) : null),
        h('p', { class: 'item-resumo' }, it.resumo || ''),
        it.trecho && it.trecho !== it.resumo ? citacao(it.trecho) : null,
        detalhesItem(it),
        chipsCategorias(it.categorias),
        h('div', { class: 'acoes' },
          h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno', onclick: editar }, '✏️ Editar'),
          h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto', onclick: apagar }, '🗑️ Apagar item')));
    };
    function editar() {
      limpar(li);
      const form = formItem(it, async (mudancas) => {
        if (Object.keys(mudancas).length) {
          await api('PATCH', `/itens/${it.id}`, mudancas);
          avisar('Item atualizado.');
        }
        recarregar();
      }, () => { ver(); focarPrimeiro(li.querySelector('.acoes')); });
      inserir(li, form);
      focarPrimeiro(form);
    }
    async function apagar() {
      const ok = await confirmar({
        titulo: 'Apagar este item?',
        texto: 'Só este item sai (também das cópias automáticas); o texto do registro continua guardado.',
        ok: 'Apagar item',
        perigo: true,
      });
      if (!ok) return;
      try {
        await api('DELETE', `/itens/${it.id}`);
        avisar('Item apagado.');
        recarregar();
      } catch (e) {
        if (!e.bloqueio) avisarErro(e);
      }
    }
    ver();
    return li;
  }

  function formItem(it, aoSalvar, aoCancelar) {
    const categorias = [...lista(it.categorias)];
    const resumo = h('input', { id: novoId('iresumo'), type: 'text', value: it.resumo || '', maxlength: '300' });
    const certeza = h('select', { id: novoId('icerteza'), value: CERTEZAS[it.certeza] ? it.certeza : 'observado' },
      Object.entries(CERTEZAS).map(([k, c]) => h('option', { value: k }, `${c.emoji} ${c.nome}: ${c.explica}`)));
    const importancia = h('select', { id: novoId('iimp'), value: String(Number(it.importancia) || 2) },
      Object.entries(IMPORTANCIAS).map(([k, nome]) => h('option', { value: k }, nome)));
    const status = h('select', { id: novoId('istatus'), value: STATUS_ITEM[it.status] ? it.status : 'ativo' },
      Object.entries(STATUS_ITEM).map(([k, nome]) => h('option', { value: k }, nome)));
    const temData = Boolean(it.data_evento) || TIPOS_COM_DATA.has(it.tipo);
    const data = temData ? h('input', { id: novoId('idata'), type: 'date', value: it.data_evento || '' }) : null;
    const lembrar = it.tipo === 'pendencia' ? h('input', { id: novoId('ilembrar'), type: 'date', value: it.lembrar_em || '' }) : null;
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const salvar = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Salvar');

    const form = h('form', { class: 'form-item', novalidate: true },
      h('div', { class: 'item-topo' }, rotuloTipo(it)),
      campo('Resumo', resumo),
      it.trecho ? citacao(it.trecho) : null,
      h('div', { class: 'grade-2' }, campo('Certeza', certeza), campo('Importância', importancia)),
      campo('Situação', status, '“Mudou” e “Descartado” deixam de valer no perfil e nos alertas.'),
      data ? campo(it.tipo === 'data_importante' ? 'Data' : 'Quando', data) : null,
      lembrar ? campo('Lembrar de perguntar em', lembrar) : null,
      h('div', { class: 'rotulo-pequeno' }, 'Categorias'),
      editorCategorias(categorias),
      erro,
      h('div', { class: 'acoes' }, salvar,
        h('button', { type: 'button', class: 'botao botao-secundario', onclick: aoCancelar }, 'Cancelar')));

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      erro.textContent = '';
      const m = {};
      const novoResumo = resumo.value.trim();
      if (novoResumo !== (it.resumo || '')) {
        if (!novoResumo) {
          erro.textContent = 'O resumo não pode ficar vazio.';
          resumo.focus();
          return;
        }
        m.resumo = novoResumo;
      }
      if (certeza.value !== it.certeza) m.certeza = certeza.value;
      if (Number(importancia.value) !== (Number(it.importancia) || 2)) m.importancia = Number(importancia.value);
      if (status.value !== (it.status || 'ativo')) m.status = status.value;
      if (data && (data.value || null) !== (it.data_evento || null)) {
        m.data_evento = data.value || null;
        if (data.value && it.precisao !== 'dia') m.precisao = 'dia';   // data escolhida à mão é um dia exato
        const antiga = it.data_evento ? dataBr(it.data_evento) : '';
        if (!('resumo' in m) && antiga && data.value && (it.resumo || '').includes(antiga)) {
          m.resumo = it.resumo.split(antiga).join(dataBr(data.value));
        }
      }
      if (lembrar && (lembrar.value || null) !== (it.lembrar_em || null)) m.lembrar_em = lembrar.value || null;
      if (JSON.stringify(categorias) !== JSON.stringify(lista(it.categorias))) m.categorias = categorias;
      salvar.disabled = true;
      try {
        await aoSalvar(m);
      } catch (erroApi) {
        if (!erroApi.bloqueio) erro.textContent = erroApi.message;
      } finally {
        salvar.disabled = false;
      }
    });
    return form;
  }

  function cartaoRelacaoSalva(r, itens, recarregar) {
    const [emoji, titulo] = TIPOS_RELACAO[r.tipo] || TIPOS_RELACAO.contexto;
    const itemNovo = itens.find((i) => i.id === r.item_novo);
    const agir = async (acao, mensagem) => {
      try {
        await api('POST', `/relacoes/${r.id}`, { acao });
        avisar(mensagem);
        recarregar();
      } catch (e) {
        if (!e.bloqueio) avisarErro(e);
      }
    };
    const pendente = (r.status || 'pendente') === 'pendente';
    return h('li', { class: ['cartao', 'relacao', r.tipo === 'contradicao' ? 'relacao-contradicao' : null] },
      h('p', { class: 'relacao-tipo' }, h('span', { 'aria-hidden': 'true' }, emoji + ' '), titulo),
      h('p', null, r.motivo || ''),
      r.sugestao ? h('p', { class: 'sugestao' }, h('span', { 'aria-hidden': 'true' }, '💡 '), r.sugestao) : null,
      itemNovo ? h('p', { class: 'sutil' }, 'Ligado a: ', itemNovo.resumo || '') : null,
      r.registro_antigo_id != null ? h('p', null, linkRegistro(r.registro_antigo_id, 'Ver o registro antigo')) : null,
      pendente
        ? h('div', { class: 'acoes' },
          r.tipo === 'contradicao' ? h('button', {
            type: 'button', class: 'botao botao-primario botao-pequeno',
            onclick: () => agir('atualizar_preferencia', 'Preferência atualizada. A antiga ficou marcada como “mudou”.'),
          }, 'Atualizar a preferência') : null,
          h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno', onclick: () => agir('confirmar', 'Relação confirmada.') },
            r.tipo === 'contradicao' ? 'Manter as duas' : 'Faz sentido'),
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => agir('descartar', 'Relação descartada.') }, 'Não tem a ver'))
        : h('p', null, h('span', { class: 'chip' }, STATUS_RELACAO[r.status] || r.status)));
  }

  // ================================================================ tela: Ajustes

  async function telaAjustes(main, _partes, atual) {
    inserir(main, carregando());
    const config = await api('GET', '/config');
    if (!atual()) return;
    if (config && typeof config === 'object') estado.config = config;
    const c = estado.config || {};
    limpar(main);
    inserir(main, 
      cabecalho('Ajustes', null, '⚙️'),
      secaoDadosDela(c),
      h('section', { class: 'cartao', 'aria-labelledby': 'aj-lembretes' },
        h('h2', { id: 'aj-lembretes' }, '🔔 Lembretes'),
        h('p', { class: 'sutil' }, 'Com quantos dias de antecedência a página Hoje te lembra das datas.'),
        editorAntecedencias()),
      secaoRegistro(c),
      secaoSeguranca(c),
      secaoAparencia(),
      secaoDados(),
      secoesAjustes.map((s) => secaoDeRecurso(s, atual)),
      secaoHistorico(),
      secaoPrivacidade(),
      secaoIA(),
      secaoSair(),
      h('p', { class: 'sutil centro versao' }, `Entre Nós${estado.servidor && estado.servidor.versao ? ` · versão ${estado.servidor.versao}` : ''}`));
  }

  // Seção de Ajustes de um recurso (registrarSecaoAjustes, seção 20.0): o cartão e o título são
  // daqui; render(corpo, {config, salvarConfig, atual}) desenha dentro.
  function secaoDeRecurso(s, atual) {
    const id = novoId('aj-recurso');
    return h('section', { class: 'cartao', 'aria-labelledby': id, 'data-recurso': s.id },
      h('h2', { id }, s.emoji ? h('span', { 'aria-hidden': 'true' }, s.emoji + ' ') : null, s.nome),
      montarDeRecurso(s.render, { config: estado.config || {}, salvarConfig, atual }, 'corpo-recurso'));
  }

  function secaoDadosDela(c) {
    const nome = h('input', { id: 'aj-nome', type: 'text', value: c.nome_dela || '', autocomplete: 'off', maxlength: '80' });
    const aniversario = h('input', { id: 'aj-aniversario', type: 'date', value: c.aniversario_dela || '' });
    const inicio = h('input', { id: 'aj-inicio', type: 'date', value: c.inicio_namoro || '' });
    const form = h('form', { novalidate: true },
      campo('Nome dela', nome),
      h('div', { class: 'grade-2' }, campo('Aniversário dela', aniversario), campo('Início do namoro', inicio)),
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario' }, 'Salvar')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      await salvarConfig({
        nome_dela: nome.value.trim() || null,
        aniversario_dela: aniversario.value || null,
        inicio_namoro: inicio.value || null,
      }, 'Dados dela salvos.');
    });
    return h('section', { class: 'cartao', 'aria-labelledby': 'aj-ela' },
      h('h2', { id: 'aj-ela' }, '🌷 Sobre ela'), form);
  }

  function secaoRegistro(c) {
    const pergunta = h('input', {
      id: 'aj-ambigua', type: 'checkbox', checked: c.pergunta_ambigua !== false,
      onchange: (e) => salvarConfig({ pergunta_ambigua: e.target.checked }, 'Preferência salva.'),
    });
    return h('section', { class: 'cartao', 'aria-labelledby': 'aj-registro' },
      h('h2', { id: 'aj-registro' }, '✍️ Registro'),
      h('label', { class: 'opcao-linha', for: 'aj-ambigua' }, pergunta,
        ' Perguntar quando uma data puder ter mais de uma leitura (como “sexta que vem”)'));
  }

  function secaoSeguranca(c) {
    const minutos = h('input', {
      id: 'aj-minutos', type: 'number', min: '1', max: '1440', step: '1', inputmode: 'numeric',
      class: 'numero-curto', value: String(c.minutos_bloqueio || 10),
    });
    const formMinutos = h('form', { novalidate: true },
      h('div', { class: 'campo-linha' },
        h('label', { for: 'aj-minutos' }, 'Bloquear sozinho depois de'), minutos, h('span', null, 'minutos sem uso'),
        h('button', { type: 'submit', class: 'botao botao-secundario botao-pequeno' }, 'Salvar')));
    formMinutos.addEventListener('submit', async (e) => {
      e.preventDefault();
      const v = Number(minutos.value);
      if (!Number.isInteger(v) || v < 1 || v > 1440) {
        avisar('Use um número inteiro de 1 a 1440 minutos.', 'erro');
        minutos.focus();
        return;
      }
      await salvarConfig({ minutos_bloqueio: v }, 'Tempo de bloqueio salvo.');
    });

    const atualPin = campoPin('aj-pin-atual');
    const novoPin = campoPin('aj-pin-novo', { minlength: String(PIN_MINIMO) });
    const repetir = campoPin('aj-pin-repetir');
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const botaoPin = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Trocar PIN');
    const formPin = h('form', { novalidate: true },
      h('h3', null, 'Trocar o PIN'),
      campo('PIN atual', atualPin),
      h('div', { class: 'grade-2' },
        campo('PIN novo', novoPin, `Pelo menos ${PIN_MINIMO} caracteres; uma frase curta é ótima. Só números, a partir de ${DIGITOS_MINIMOS} dígitos.`),
        campo('Repita o PIN novo', repetir)),
      h('p', { class: 'sutil' }, AVISO_NAO_SALVAR_PIN),
      erro,
      h('div', { class: 'acoes' }, botaoPin));
    formPin.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (esperandoPin(botaoPin)) return;
      erro.textContent = '';
      if (!atualPin.value) {
        erro.textContent = 'Digite o PIN atual.';
        atualPin.focus();
        return;
      }
      const problema = problemaDoPin(novoPin.value);
      if (problema) {
        erro.textContent = problema.replace('O PIN ', 'O PIN novo ');
        novoPin.focus();
        return;
      }
      if (novoPin.value !== repetir.value) {
        erro.textContent = 'Os dois PINs novos não são iguais.';
        repetir.focus();
        return;
      }
      botaoPin.disabled = true;
      let espera = 0;
      try {
        await api('POST', '/pin', { atual: atualPin.value, novo: novoPin.value }, { semBloqueio: true });
        formPin.reset();
        avisar('PIN trocado. Use o novo da próxima vez.');
      } catch (erroApi) {
        if (erroApi.status === 401 || erroApi.status === 423) {
          // Pode ser o PIN atual errado ou a sessão que expirou: o estado do servidor decide.
          const est = await api('GET', '/estado', undefined, { semBloqueio: true }).catch(() => null);
          if (!est || !est.desbloqueado) {
            irParaBloqueio();
            return;
          }
          erro.textContent = erroApi.doServidor ? erroApi.message : 'O PIN atual não confere.';
        } else {
          erro.textContent = erroApi.message;
        }
        espera = segundosDeEspera(erroApi);
        atualPin.value = '';
        atualPin.focus();
      } finally {
        botaoPin.disabled = false;
      }
      if (espera && formPin.isConnected) contarEsperaPin(botaoPin, espera);
    });

    return h('section', { class: 'cartao', 'aria-labelledby': 'aj-seguranca' },
      h('h2', { id: 'aj-seguranca' }, '🔒 Segurança'),
      formMinutos,
      h('div', { class: 'acoes' },
        h('button', { type: 'button', class: 'botao botao-secundario', onclick: () => bloquearAgora() }, '🔒 Bloquear agora')),
      h('hr'),
      formPin);
  }

  function secaoAparencia() {
    const tema = h('select', {
      id: 'aj-tema', value: lerTema(),
      onchange: (e) => { aplicarTema(e.target.value); guardarTema(e.target.value); falar('Aparência trocada.'); },
    }, h('option', { value: 'auto' }, 'Automática (igual ao sistema)'),
    h('option', { value: 'claro' }, 'Clara'),
    h('option', { value: 'escuro' }, 'Escura'));
    return h('section', { class: 'cartao', 'aria-labelledby': 'aj-aparencia' },
      h('h2', { id: 'aj-aparencia' }, '🎨 Aparência'), campo('Tema', tema));
  }

  // ================================================================ Ajustes → 📦 Levar ou trazer dados (seção 18)
  //
  // Cada computador tem o seu diário. O backup (.cofre, criptografado) leva tudo, e restaurar troca o
  // diário inteiro pelo dele (o de agora fica guardado em backups/). O JSON exportado sai sem
  // criptografia, e importar só junta: nunca substitui nem apaga.

  function secaoDados() {
    return h('section', { class: 'cartao', 'aria-labelledby': 'aj-dados' },
      h('h2', { id: 'aj-dados' }, h('span', { 'aria-hidden': 'true' }, '📦 '), 'Levar ou trazer dados'),
      h('p', null, 'Cada computador tem o seu próprio diário. Para levar seus registros para outro computador, faça um backup aqui e restaure lá.'),
      blocoBackup(),
      blocoRestaurar(),
      blocoExportar(),
      blocoImportar());
  }

  function tituloBloco(emoji, texto) {
    return h('h3', null, h('span', { 'aria-hidden': 'true' }, emoji + ' '), texto);
  }

  function blocoBackup() {
    const pasta = h('input', {
      id: 'aj-pasta', type: 'text', autocomplete: 'off', spellcheck: 'false',
      placeholder: 'Ex.: E:\\ (um pendrive) ou D:\\Backups',
    });
    const resultado = h('p', { class: 'resultado', 'aria-live': 'polite' });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Fazer backup');
    const form = h('form', { novalidate: true },
      campo('Pasta de destino', pasta, 'O backup continua criptografado: só abre com o seu PIN de agora.'),
      h('div', { class: 'acoes' }, botao), resultado);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      resultado.textContent = '';
      if (!pasta.value.trim()) {
        avisar('Informe a pasta onde guardar o backup.', 'erro');
        pasta.focus();
        return;
      }
      botao.disabled = true;
      try {
        const r = await api('POST', '/backup', { pasta: pasta.value.trim() });
        resultado.textContent = `Pronto! Backup salvo em: ${(r && r.arquivo) || pasta.value.trim()}`;
      } catch (erroApi) {
        if (!erroApi.bloqueio) resultado.textContent = erroApi.message;
      } finally {
        botao.disabled = false;
      }
    });
    return h('div', { class: 'bloco-dados' },
      tituloBloco('💾', 'Fazer backup'),
      h('p', { class: 'sutil' }, 'Uma cópia do diário inteiro (arquivo .cofre), para guardar fora deste computador, como num pendrive, ou para restaurar em outro computador. Além disso, o Entre Nós já guarda cópias automáticas na pasta “backups”.'),
      form);
  }

  // Restaurar com o app aberto: arquivo + PIN do backup + PIN de agora, confirmação em diálogo e
  // POST /api/restaurar. O servidor guarda o diário atual em backups/ antes de trocar e abre uma
  // sessão nova: a página guarda o token novo e recarrega tudo (passando pelo resumo).
  function blocoRestaurar() {
    const erro = h('p', { class: 'erro-campo', id: 'aj-rest-erro', role: 'alert' });
    const arquivo = h('input', { id: 'aj-rest-arquivo', type: 'file', accept: '.cofre', 'aria-describedby': 'aj-rest-erro' });
    const pinBackup = campoPin('aj-rest-pin', { 'aria-describedby': 'aj-rest-erro' });
    const pinAtual = campoPin('aj-rest-pin-atual', { 'aria-describedby': 'aj-rest-erro' });
    const progresso = indicadorProgresso();
    const TEXTO_BOTAO = 'Restaurar este backup…';
    const botao = h('button', { type: 'submit', class: 'botao botao-secundario botao-perigo-texto' }, TEXTO_BOTAO);
    const form = h('form', { novalidate: true, class: 'form-dados' },
      campo('Arquivo do backup (.cofre)', arquivo),
      h('div', { class: 'grade-2' },
        campo('PIN desse backup', pinBackup, 'O PIN que valia quando o backup foi feito.'),
        campo('Seu PIN de agora', pinAtual, 'Para confirmar que é você.')),
      h('p', { class: 'sutil' }, AVISO_NAO_SALVAR_PIN),
      progresso.el,
      erro,
      h('div', { class: 'acoes' }, botao));

    arquivo.addEventListener('change', () => {
      erro.textContent = problemaDoArquivo(arquivo.files && arquivo.files[0], 'cofre') || '';
    });

    let ocupado = false;
    const travar = (sim) => {
      ocupado = sim;
      arquivo.disabled = pinBackup.disabled = pinAtual.disabled = botao.disabled = sim;
      botao.textContent = sim ? 'Restaurando…' : TEXTO_BOTAO;
    };
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (ocupado || esperandoPin(botao)) return;
      erro.textContent = '';
      const escolhido = arquivo.files && arquivo.files[0];
      const problema = problemaDoArquivo(escolhido, 'cofre');
      if (problema) {
        erro.textContent = problema;
        arquivo.focus();
        return;
      }
      if (!pinBackup.value) {
        erro.textContent = 'Digite o PIN desse backup.';
        pinBackup.focus();
        return;
      }
      if (!pinAtual.value) {
        erro.textContent = 'Digite o seu PIN de agora.';
        pinAtual.focus();
        return;
      }
      const ok = await confirmar({
        titulo: 'Substituir o diário de agora?',
        texto: `O diário deste computador vai ser trocado pelo do backup ${descreverArquivo(escolhido)}. O que foi registrado aqui e não está nele deixa de aparecer.\n\nAntes de trocar, uma cópia do diário de agora fica guardada na pasta “backups”. Depois, o PIN passa a ser o desse backup.`,
        ok: 'Substituir pelo backup',
        perigo: true,
      });
      if (!ok || !form.isConnected || ocupado) return;
      const pins = { pin: pinBackup.value, pin_atual: pinAtual.value };
      travar(true);
      let foco = null;
      let espera = 0;
      try {
        const r = await restaurarBackup(escolhido, pins, progresso);
        arquivo.value = '';
        if (!r || !r.token) {
          // Trocou, mas a chave de acesso nova não veio: a antiga já não vale.
          esquecerToken();
          telaBloqueio('O backup foi restaurado. Digite o PIN do backup para abrir.');
          return;
        }
        guardarToken(r.token);
        telaRestaurado(r.resumo, true, r.copia);
        return;
      } catch (erroApi) {
        if (erroApi.status === 401 || erroApi.status === 423) {
          // PIN errado (do backup ou o de agora) ou a sessão que acabou: o servidor decide.
          if (!(await sessaoContinua(erroApi)) || !form.isConnected) return;
          const mensagem = erroApi.doServidor ? erroApi.message : 'Um dos PINs não confere: o desse backup ou o seu de agora.';
          erro.textContent = mensagem;
          foco = /atual|agora|principal/i.test(mensagem) && !/backup/i.test(mensagem) ? pinAtual : pinBackup;
        } else if (erroApi.status === 413) {
          erro.textContent = MENSAGEM_COFRE_GRANDE;
          foco = arquivo;
        } else if (erroApi.status === 400) {
          erro.textContent = erroApi.doServidor ? erroApi.message : MENSAGEM_NAO_E_COFRE;
          foco = arquivo;
        } else if (!erroApi.bloqueio) {
          erro.textContent = erroApi.message;          // 429: "Espere N s antes de tentar o PIN de novo."
        }
        espera = segundosDeEspera(erroApi);
      } finally {
        pins.pin = null;
        pins.pin_atual = null;
        pinBackup.value = '';
        pinAtual.value = '';
        if (form.isConnected) travar(false);
      }
      if (espera && form.isConnected) contarEsperaPin(botao, espera);
      if (foco && form.isConnected) foco.focus();
    });

    return h('div', { class: 'bloco-dados' },
      tituloBloco('🔄', 'Restaurar um backup'),
      h('p', { class: 'nota-importante' }, h('span', { 'aria-hidden': 'true' }, '⚠️ '),
        'Restaurar substitui o diário de agora pelo do backup: o que foi registrado aqui e não está no backup deixa de aparecer.'),
      h('ul', { class: 'lista-explica' },
        h('li', null, 'Antes de trocar, uma cópia do diário de agora fica guardada na pasta “backups” (arquivo “entrenos-antes-da-restauracao-…”).'),
        h('li', null, 'Depois, o PIN passa a ser o do backup. A IA e o PIN da IA também voltam como estavam nele.'),
        h('li', null, 'Quer só acrescentar os registros de outro computador, sem perder os daqui? Use “Importar JSON (juntar)”, logo abaixo.')),
      form);
  }

  function blocoExportar() {
    const botao = h('button', { type: 'button', class: 'botao botao-secundario' }, '📤 Exportar meus dados (JSON)');
    botao.addEventListener('click', async () => {
      const ok = await confirmar({
        titulo: 'Exportar sem criptografia?',
        texto: 'O arquivo exportado NÃO tem criptografia: qualquer pessoa que abrir lê tudo o que você registrou.\nGuarde num lugar seguro e apague quando não precisar mais.',
        ok: 'Entendi, exportar',
      });
      if (!ok) return;
      botao.disabled = true;
      try {
        const dados = await api('GET', '/exportar');
        baixarJson(dados, `entre-nos-exportacao-${hojeIso()}.json`);
        avisar('Exportação pronta. Guarde o arquivo com cuidado.');
      } catch (e) {
        if (!e.bloqueio) avisarErro(e);
      } finally {
        botao.disabled = false;
      }
    });
    return h('div', { class: 'bloco-dados' },
      tituloBloco('📤', 'Exportar (JSON)'),
      h('p', null, 'Gera um arquivo com tudo o que você registrou, legível em qualquer editor de texto. Também serve para juntar estes registros ao diário de outro computador (em “Importar JSON”, lá).'),
      h('p', { class: 'nota-importante' }, h('span', { 'aria-hidden': 'true' }, '⚠️ '), 'O arquivo exportado sai sem criptografia.'),
      botao);
  }

  // Importar JSON (juntar): escolher o arquivo, ver a prévia (POST /api/importar/previa, nada é
  // gravado) e só então juntar (POST /api/importar). O texto do arquivo fica na página apenas entre
  // a prévia e o "Juntar": cancelar, escolher outro arquivo, trocar de tela ou bloquear esquece.
  function blocoImportar() {
    const erro = h('p', { class: 'erro-campo', id: 'aj-imp-erro', role: 'alert' });
    const arquivo = h('input', { id: 'aj-imp-arquivo', type: 'file', accept: '.json,application/json', 'aria-describedby': 'aj-imp-erro' });
    const progresso = indicadorProgresso();
    const TEXTO_BOTAO = 'Ver o que vai entrar';
    const botao = h('button', { type: 'submit', class: 'botao botao-secundario' }, TEXTO_BOTAO);
    const previa = h('div', { class: 'previa-caixa' });
    const form = h('form', { novalidate: true, class: 'form-dados' },
      campo('Arquivo exportado (.json)', arquivo),
      progresso.el,
      erro,
      h('div', { class: 'acoes' }, botao));

    let texto = null;
    const esquecer = () => { texto = null; };
    const soltar = () => {
      texto = null;
      esquecedoresArquivo.delete(esquecer);
    };
    let ocupado = false;
    const travar = (sim, rotulo) => {
      ocupado = sim;
      arquivo.disabled = botao.disabled = sim;
      botao.textContent = sim && rotulo ? rotulo : TEXTO_BOTAO;
      previa.querySelectorAll('button').forEach((b) => { b.disabled = sim; });
    };

    arquivo.addEventListener('change', () => {
      soltar();
      limpar(previa);
      erro.textContent = problemaDoArquivo(arquivo.files && arquivo.files[0], 'json') || '';
    });

    const cancelar = () => {
      if (ocupado) return;
      soltar();
      limpar(previa);
      arquivo.value = '';
      falar('Nada foi juntado.');
      arquivo.focus();
    };

    const juntar = async () => {
      if (ocupado) return;
      erro.textContent = '';
      if (texto == null) {
        limpar(previa);
        erro.textContent = 'Escolha o arquivo de novo para ver o que vai entrar.';
        arquivo.focus();
        return;
      }
      travar(true, null);
      progresso.fase('Juntando ao seu diário…', 'espera');
      let foco = null;
      try {
        const corpo = { json: texto, confirmar: true };
        soltar();                 // daqui em diante o texto só existe dentro do pedido
        const r = (await api('POST', '/importar', corpo, { limiteBytes: LIMITE_ENVIO_ARQUIVO })) || {};
        corpo.json = null;
        estado.busca = null;      // a última busca não vê os registros novos
        if (!form.isConnected) return;
        arquivo.value = '';
        foco = desenharImportado(previa, r);
      } catch (e) {
        if (e.bloqueio || !form.isConnected) return;
        limpar(previa);
        // 400 e 413: o servidor recusou antes de gravar (tudo numa transação). Sem resposta
        // (status 0), não dá para garantir: a pessoa confere pela prévia de novo.
        if (e.status === 413) erro.textContent = `${MENSAGEM_JSON_GRANDE} Nada foi juntado.`;
        else if (e.status === 400) erro.textContent = `${e.doServidor ? e.message : MENSAGEM_NAO_E_EXPORTACAO} Nada foi juntado.`;
        else if (e.status === 0) erro.textContent = `${e.message} Para saber se algo entrou, veja a prévia de novo: o que já entrou aparece como “já está aqui”.`;
        else erro.textContent = e.message;
        foco = arquivo;
      } finally {
        progresso.limpar();
        if (form.isConnected) travar(false);
      }
      if (foco) foco.focus();
    };

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (ocupado) return;
      erro.textContent = '';
      soltar();
      limpar(previa);
      const escolhido = arquivo.files && arquivo.files[0];
      const problema = problemaDoArquivo(escolhido, 'json');
      if (problema) {
        erro.textContent = problema;
        arquivo.focus();
        return;
      }
      travar(true, 'Aguarde…');
      let lido = null;
      let foco = null;
      try {
        progresso.fase('Lendo o arquivo…', 0);
        lido = await lerArquivo(escolhido, 'texto', (f) => progresso.fase(null, f));
        progresso.fase('Conferindo o que já está no seu diário…', 'espera');
        const p = (await api('POST', '/importar/previa', { json: lido }, { limiteBytes: LIMITE_ENVIO_ARQUIVO })) || {};
        if (!form.isConnected || estado.modo !== 'app') return;
        texto = lido;
        esquecedoresArquivo.add(esquecer);
        foco = desenharPreviaImportacao(previa, p, { juntar, cancelar });
      } catch (erroApi) {
        if (erroApi.bloqueio || !form.isConnected) return;
        if (erroApi.status === 413) erro.textContent = MENSAGEM_JSON_GRANDE;
        else if (erroApi.status === 400) erro.textContent = erroApi.doServidor ? erroApi.message : MENSAGEM_NAO_E_EXPORTACAO;
        else erro.textContent = erroApi.message;
        foco = arquivo;
      } finally {
        lido = null;
        progresso.limpar();
        if (form.isConnected) travar(false);
      }
      if (foco) foco.focus();
    });

    return h('div', { class: 'bloco-dados' },
      tituloBloco('📥', 'Importar JSON (juntar)'),
      h('p', null, 'Junta ao diário de agora os registros de um arquivo gerado em “Exportar (JSON)”, neste ou em outro computador. Nada daqui é apagado ou substituído, e registros que já estão aqui não se repetem.'),
      h('p', { class: 'sutil' }, 'Primeiro você vê o que vai entrar; só depois confirma. Ajustes, PIN, IA e alertas dispensados do arquivo não são importados.'),
      form,
      previa);
  }

  // "Mariana (melhor amiga), Sandra (mãe) e mais 3": nomes que a prévia trouxer (lista de textos ou
  // de pessoas). Contagem pura (um número) não tem nomes para mostrar.
  function nomesDaPrevia(v, maximo = 8) {
    if (!Array.isArray(v)) return null;
    const nomes = v.map((p) => (typeof p === 'string' ? p.trim() : rotuloPessoaCompleto(p && typeof p === 'object' ? p : null)))
      .filter(Boolean);
    if (!nomes.length) return null;
    const mostrados = nomes.slice(0, maximo);
    const resto = nomes.length - mostrados.length;
    return resto > 0 ? `${mostrados.join(', ')} e mais ${resto}` : mostrados.join(', ');
  }

  function linhaNumero(emoji, n, um, varios, explica, nomes) {
    return h('li', { class: 'numero-linha' },
      h('span', { class: 'numero-valor' }, String(n)),
      h('span', { class: 'numero-rotulo' },
        h('span', { 'aria-hidden': 'true' }, emoji + ' '), plural(n, um, varios),
        explica ? h('span', { class: 'numero-explica' }, explica) : null,
        nomes ? h('span', { class: 'numero-nomes' }, nomes) : null));
  }

  // Campos que a importação preencheu numa pessoa que já existia (detalhes_pessoas.completadas).
  const CAMPOS_PESSOA = { nome: 'nome', relacao: 'relação', aniversario: 'aniversário', observacoes: 'observação' };

  function textoCompletada(c) {
    if (!c || typeof c !== 'object') return '';
    const partes = lista(c.campos).filter((campo) => campo !== 'apelidos' && CAMPOS_PESSOA[campo])
      .map((campo) => CAMPOS_PESSOA[campo]);
    const apelidos = lista(c.apelidos).filter((a) => typeof a === 'string' && a.trim());
    if (apelidos.length) partes.push(`${plural(apelidos.length, 'apelido', 'apelidos')} ${apelidos.join(', ')}`);
    return `${c.rotulo || 'Sem nome'}${partes.length ? `: ${partes.join(', ')}` : ''}`;
  }

  // A prévia da importação: números, nomes e avisos, e os botões. Devolve o título (para o foco).
  // Além de registros e pessoas novas, conta o que muda no que já existe (seção 18.6): pessoas que
  // ganham o que faltava e registros que passam a ficar 🔒. Isso também é "algo para juntar".
  function desenharPreviaImportacao(caixa, p, { juntar, cancelar }) {
    const novos = contagem(p.registros_novos);
    const repetidos = contagem(p.registros_repetidos);
    const noArquivo = contagem(p.registros_repetidos_no_arquivo);
    const marcados = contagem(p.registros_marcados_privados);
    const pessoasNovas = contagem(p.pessoas_novas);
    const pessoasExistentes = contagem(p.pessoas_existentes);
    const completadas = contagem(p.pessoas_completadas);
    const itens = contagem(p.itens);
    const det = p.detalhes_pessoas && typeof p.detalhes_pessoas === 'object' ? p.detalhes_pessoas : {};
    const novas = lista(det.novas).filter((x) => x && typeof x === 'object');
    const existentes = lista(det.existentes).filter((x) => x && typeof x === 'object');
    const nomesNovas = nomesDaPrevia(novas.map((x) => x.rotulo).filter((x) => typeof x === 'string'));
    const nomesExistentes = nomesDaPrevia(existentes.map((x) => (x.arquivo && x.daqui && x.arquivo !== x.daqui
      ? `${x.arquivo} → ${x.daqui}` : x.daqui || x.arquivo)).filter((x) => typeof x === 'string'));
    const nomesCompletadas = nomesDaPrevia(lista(det.completadas).map(textoCompletada).filter(Boolean), 5);
    // Pessoa que vai entrar como nova com o mesmo nome de alguém daqui: talvez seja a mesma.
    const homonimos = novas.filter((x) => typeof x.parecida_com === 'string' && x.parecida_com)
      .map((x) => `${x.rotulo} vai entrar como pessoa nova, e você já tem ${x.parecida_com}. Se for a mesma pessoa, cancele, deixe a relação igual à do arquivo (em Conhecendo Ela → Pessoas) e veja a prévia de novo.`);
    const avisos = [...homonimos, ...lista(p.avisos).filter((a) => typeof a === 'string' && a.trim())];
    // O que cada recurso junta (seção 20.0): {novos, texto} vindo do `importar` dele.
    const { textos: textosRecursos, novos: novosRecursos } = resumoDosRecursos(p);
    const temAlgo = novos > 0 || pessoasNovas > 0 || completadas > 0 || marcados > 0 || novosRecursos;
    const titulo = h('h4', { tabindex: '-1' }, temAlgo ? 'O que vai entrar no seu diário' : 'Nada novo para juntar');
    limpar(caixa);
    inserir(caixa, h('div', { class: 'previa-importacao' },
      titulo,
      h('ul', { class: 'numeros' },
        linhaNumero('📖', novos, 'registro novo', 'registros novos', null, null),
        itens > 0 ? linhaNumero('🧩', itens, 'informação organizada', 'informações organizadas', 'gostos, datas, lembretes…', null) : null,
        repetidos > 0 ? linhaNumero('🔁', repetidos, 'registro que já está aqui', 'registros que já estão aqui',
          'ficam de fora, sem repetir: vale a versão deste computador, inclusive os itens editados aqui', null) : null,
        noArquivo > 0 ? linhaNumero('📄', noArquivo, 'registro repetido dentro do arquivo', 'registros repetidos dentro do arquivo',
          'entram uma vez só', null) : null,
        marcados > 0 ? linhaNumero('🔒', marcados, 'registro daqui passa a ficar fora da IA', 'registros daqui passam a ficar fora da IA',
          'como no outro computador; nada perde o 🔒 ao importar', null) : null,
        pessoasNovas > 0 ? linhaNumero('🌱', pessoasNovas, 'pessoa nova', 'pessoas novas', null, nomesNovas) : null,
        pessoasExistentes > 0
          ? linhaNumero('👥', pessoasExistentes, 'pessoa que você já tinha cadastrado', 'pessoas que você já tinha cadastrado',
            plural(pessoasExistentes, 'os registros ficam ligados a ela', 'os registros ficam ligados a elas'), nomesExistentes)
          : null,
        completadas > 0
          ? linhaNumero('🧷', completadas, 'pessoa ganha informações que faltavam aqui', 'pessoas ganham informações que faltavam aqui',
            'nada do que já está aqui é trocado', nomesCompletadas)
          : null,
        textosRecursos.map((t) => h('li', null, h('span', { 'aria-hidden': 'true' }, '🧩 '), t))),
      avisos.length
        ? h('div', { class: 'avisos-previa' }, h('p', { class: 'rotulo-pequeno' }, 'Avisos'),
          h('ul', null, avisos.map((a) => h('li', null, a))))
        : null,
      temAlgo
        ? h('p', { class: 'sutil' }, 'Nada do seu diário é apagado ou substituído: só entra o que é novo e o que faltava.')
        : h('p', null, 'Tudo o que está nesse arquivo já está no seu diário.'),
      h('div', { class: 'acoes' },
        temAlgo ? h('button', { type: 'button', class: 'botao botao-primario', onclick: juntar }, 'Juntar ao meu diário') : null,
        h('button', { type: 'button', class: 'botao botao-secundario', onclick: cancelar }, temAlgo ? 'Cancelar' : 'Fechar'))));
    return titulo;
  }

  // {textos, novos} do que os recursos juntam (ou juntariam, na prévia): seção 20.0.
  function resumoDosRecursos(r) {
    const deRecursos = Object.values(r && r.recursos && typeof r.recursos === 'object' ? r.recursos : {})
      .filter((x) => x && typeof x === 'object');
    return {
      textos: deRecursos.map((x) => x.texto).filter((t) => typeof t === 'string' && t.trim()),
      novos: deRecursos.some((x) => Number(x.novos) > 0),
    };
  }

  // Depois de juntar: quantos entraram, avisos, e o lembrete de que o JSON está sem criptografia.
  function desenharImportado(caixa, r) {
    const v = r.importados;
    const registros = v && typeof v === 'object' && !Array.isArray(v) ? contagem(v.registros)
      : (v != null ? contagem(v) : contagem(r.registros_novos));
    const pessoas = contagem(r.pessoas_novas);
    const completadas = contagem(r.pessoas_completadas);
    const marcados = contagem(r.registros_marcados_privados);
    const avisos = lista(r.avisos).filter((a) => typeof a === 'string' && a.trim());
    const { textos: textosRecursos, novos: novosRecursos } = resumoDosRecursos(r);
    const mudou = registros > 0 || pessoas > 0 || completadas > 0 || marcados > 0 || novosRecursos;
    const titulo = h('h4', { tabindex: '-1' }, mudou ? 'Pronto!' : 'Nada novo entrou');
    let frase = 'Tudo o que estava nesse arquivo já estava no seu diário.';
    if (mudou) {
      const partes = [];
      if (registros > 0) partes.push(`${registros} ${plural(registros, 'registro', 'registros')}`);
      if (pessoas > 0) partes.push(`${pessoas} ${plural(pessoas, 'pessoa nova', 'pessoas novas')}`);
      const juntou = partes.length ? `Juntei ${partes.join(' e ')} ao seu diário.` : '';
      const extras = [];
      if (completadas > 0) extras.push(`${completadas} ${plural(completadas, 'pessoa ganhou', 'pessoas ganharam')} informações que faltavam`);
      if (marcados > 0) extras.push(`${marcados} ${plural(marcados, 'registro passou', 'registros passaram')} a ficar 🔒 fora da IA`);
      frase = [juntou, extras.length ? `${extras.join(' e ')}.` : ''].filter(Boolean).join(' ');
      frase = frase ? frase.charAt(0).toUpperCase() + frase.slice(1) : 'Juntei ao seu diário o que era novo:';
    }
    limpar(caixa);
    inserir(caixa, h('div', { class: 'previa-importacao' },
      titulo,
      h('p', null, frase),
      textosRecursos.length ? h('ul', { class: 'numeros' },
        textosRecursos.map((t) => h('li', null, h('span', { 'aria-hidden': 'true' }, '🧩 '), t))) : null,
      avisos.length ? h('ul', { class: 'avisos-previa' }, avisos.map((a) => h('li', null, a))) : null,
      h('p', { class: 'nota-importante' }, h('span', { 'aria-hidden': 'true' }, '⚠️ '),
        'O arquivo JSON não tem criptografia. Se não precisar mais dele, apague-o (e esvazie a lixeira).')));
    avisar(mudou ? 'Importação concluída.' : 'Nada novo para juntar.');
    return titulo;
  }

  function baixarJson(dados, nome) {
    const blob = new Blob([JSON.stringify(dados, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: nome, class: 'so-leitor' }, nome);
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  function secaoHistorico() {
    const caixa = h('div', { 'aria-live': 'polite' });
    let carregado = false;
    const detalhes = h('details', { class: 'detalhes-historico' },
      h('summary', null, 'Ver o histórico de alterações'),
      h('p', { class: 'sutil' }, 'O histórico guarda só o que aconteceu (“registro #12 apagado”), nunca o conteúdo.'),
      caixa);
    detalhes.addEventListener('toggle', async () => {
      if (!detalhes.open || carregado) return;
      carregado = true;
      inserir(caixa, carregando());
      try {
        const dados = await api('GET', '/historico');
        const linhas = Array.isArray(dados) ? dados : lista(dados && dados.historico);
        limpar(caixa);
        inserir(caixa, linhas.length
          ? h('ul', { class: 'lista-historico' }, linhas.map((l) => h('li', null,
            h('span', { class: 'sutil' }, `${dataBr(l.quando)} ${horaBr(l.quando)}`), ' ', l.resumo || l.acao || '')))
          : vazioPequeno('Nada no histórico ainda.'));
      } catch (e) {
        carregado = false;
        if (e.bloqueio) return;
        limpar(caixa);
        inserir(caixa, h('p', { class: 'erro-campo' }, e.message));
      }
    });
    return h('section', { class: 'cartao', 'aria-labelledby': 'aj-historico' },
      h('h2', { id: 'aj-historico' }, '🕘 Histórico'), detalhes);
  }

  function secaoPrivacidade() {
    const textoIA = !iaConfigurada()
      ? 'Nenhuma IA configurada: nada é enviado para a internet.'
      : iaSaiDoComputador()
        ? 'Há uma IA na internet configurada. Ela só recebe texto quando você toca num botão ✨, e só o daquele pedido. Registros marcados 🔒 nunca vão.'
        : 'A IA configurada roda neste computador: mesmo quando você toca num ✨, os textos continuam aqui.';
    const linhas = [
      ['Seus registros', 'Num único arquivo criptografado (AES-256) neste computador, na pasta de dados do Entre Nós (normalmente %LOCALAPPDATA%\\EntreNos). Sem o PIN, o arquivo é ilegível.'],
      ['Cópias automáticas', 'Na subpasta “backups”, também criptografadas. Ficam só as mais recentes.'],
      ['O PIN', 'Não fica guardado em lugar nenhum. Ele abre a chave só enquanto o Entre Nós está desbloqueado. Não existe recuperação.'],
      ['Esta tela', 'Roda no seu navegador e conversa só com este computador (127.0.0.1). Ao bloquear, a chave sai da memória e a tela se apaga. Restos do que foi digitado podem ficar na memória do computador até o Entre Nós ser fechado: quando terminar, use “Fechar o Entre Nós”.'],
      ['Internet e IA', textoIA],
      ['Chave da IA', 'Guardada dentro do arquivo criptografado. Depois de salva, a tela mostra só os 4 últimos caracteres, e ela só vai para o endereço onde foi salva.'],
      ['Apagar', 'Registro apagado some de verdade: do banco, da busca, do arquivo e das cópias automáticas. Cópias que você guardou em outro lugar (como um pendrive) continuam como estavam.'],
      ['Backup e restauração', 'O backup sai criptografado e só abre com o PIN daquele momento. Restaurar troca o diário inteiro pelo do backup; antes, o diário de agora fica guardado na subpasta “backups”.'],
      ['Exportação e importação', 'O arquivo exportado sai sem criptografia; guarde com cuidado. Importar só junta registros e pessoas: ajustes, PIN e IA do arquivo ficam de fora. Depois de importar, apague o JSON se não precisar mais dele.'],
      ['Erros', 'O arquivo de erros guarda só o tipo do problema, nunca o que você escreveu.'],
    ];
    return h('section', { class: 'cartao', 'aria-labelledby': 'aj-privacidade' },
      h('h2', { id: 'aj-privacidade' }, '🔐 Privacidade: onde fica cada coisa'),
      h('dl', { class: 'lista-definicoes' }, linhas.map(([t, d]) => [
        h('dt', null, t),
        h('dd', null, d)])));
  }

  // ================================================================ Ajustes → 🤖 IA (seção 17.7)
  //
  // Sem PIN da IA: explica e oferece "Criar PIN da IA". Com PIN e fechada: pede o PIN da IA
  // (e "Esqueci o PIN da IA", que pede o principal). Liberada: IA principal, IA de reserva,
  // tempo limite, trocar o PIN da IA e "Fechar esta área". O que vem de GET /api/ia/config fica
  // em estado.iaConfig só enquanto a área está aberta nesta tela.

  function secaoIA() {
    const corpo = h('div', { class: 'area-ia' });
    const secaoEl = h('section', { class: 'cartao', 'aria-labelledby': 'aj-ia' },
      h('h2', { id: 'aj-ia' }, h('span', { 'aria-hidden': 'true' }, '🤖 '), 'IA'), corpo);
    const painel = {
      vez: 0,
      // opcoes: {mensagem, focar}
      async desenhar(opcoes = {}) {
        painel.vez += 1;
        const vez = painel.vez;
        const valendo = () => painelIA === painel && vez === painel.vez && estado.modo === 'app';
        if (!valendo()) return;
        limpar(corpo);
        inserir(corpo, carregando());
        let est;
        try {
          est = (await api('GET', '/ia/estado')) || {};
        } catch (e) {
          if (e.bloqueio || !valendo()) return;
          desenharErroIA(corpo, painel, e);
          return;
        }
        if (!valendo()) return;
        definirInfoIA(est);
        // A área se fecha 10 min depois de aberta (não renova com o uso): o relógio vem do servidor,
        // e não de quando esta página foi aberta ou recarregada.
        if (est.liberada && typeof est.segundos_restantes === 'number') {
          estado.iaAreaAte = Date.now() + Math.max(0, est.segundos_restantes) * 1000;
        }
        if (!est.pin_definido) {
          estado.iaConfig = null;
          desenharSemPinIA(corpo, painel, est, opcoes);
        } else if (!est.liberada) {
          estado.iaConfig = null;
          estado.iaAreaAte = 0;
          desenharAreaIAFechada(corpo, painel, est, opcoes);
        } else {
          await desenharAreaIAAberta(corpo, painel, opcoes, valendo);
        }
      },
    };
    painelIA = painel;
    painel.desenhar();
    return secaoEl;
  }

  function desenharErroIA(corpo, painel, e) {
    limpar(corpo);
    inserir(corpo, h('p', { class: 'erro-campo' }, (e && e.message) || 'Algo não deu certo.'),
      h('button', { type: 'button', class: 'botao botao-secundario', onclick: () => painel.desenhar({ focar: true }) }, 'Tentar de novo'));
  }

  function mensagemIA(texto) {
    return texto ? h('p', { class: 'nota-cuidado mensagem-ia', role: 'status' }, texto) : null;
  }

  function focarAreaIA(corpo) {
    const alvo = corpo.querySelector('input:not([type="checkbox"]), select, button');
    if (alvo) alvo.focus();
  }

  function introducaoIA(aberta) {
    return h('div', { class: 'intro-ia' },
      aberta ? null : [
        h('p', null, 'A IA é opcional. Sem ela, quem organiza seus registros são regras que rodam aqui mesmo: não entendem tudo, mas nunca inventam e nunca mandam nada para fora.'),
        h('p', null, 'Com uma IA configurada aparecem botões ✨: uma segunda leitura do que você escreveu, uma resposta mais natural na busca e uma ideia de gesto na página Hoje. A IA só roda quando você toca num ✨, e registros marcados 🔒 nunca vão para ela.')],
      h('details', { class: 'detalhes-ia' },
        h('summary', null, 'Quais IAs dá para usar?'),
        h('ul', null,
          h('li', null, h('strong', null, 'Neste computador: '), 'Ollama ou LM Studio. Os textos não saem daqui e não precisa de chave.'),
          h('li', null, h('strong', null, 'Na internet: '), 'Groq, OpenRouter, Google Gemini, OpenAI, Mistral ou outro serviço compatível com o formato da OpenAI. Você cola a chave do serviço (o Entre Nós reconhece qual é pelo começo dela), toca em “Buscar modelos” e escolhe um.'),
          h('li', null, 'Dá para ter uma IA principal e uma de reserva: se a principal não responder, a reserva tenta; se nenhuma responder, ficam as regras.'),
          h('li', null, 'O Claude (Anthropic) ainda não está disponível por aqui.')),
        h('p', null, 'Mesmo com IA, as regras continuam valendo: nada é salvo sem sua revisão, as datas são calculadas aqui, cada informação cita o trecho de onde veio e a IA não pode aumentar a certeza de nada.')));
  }

  // 401/423 numa rota que confere PIN: foi o PIN errado ou a sessão que acabou?
  async function sessaoContinua(erroApi) {
    const mensagem = 'O Entre Nós se bloqueou sozinho para proteger seus registros.';
    if (erroApi.status === 423) {
      irParaBloqueio(mensagem);
      return false;
    }
    try {
      await api('GET', '/ia/estado', undefined, { semBloqueio: true });
      return true;
    } catch (e) {
      if (e.status === 401 || e.status === 423) {
        irParaBloqueio(e.status === 423 ? mensagem : null);
        return false;
      }
      return true;
    }
  }

  // Erro numa ação da área da IA. true = já tratado (tela bloqueada ou área fechada).
  function tratarErroAreaIA(e) {
    if (e && e.bloqueio) return true;
    if (pedePinIA(e)) {
      estado.iaConfig = null;
      estado.iaAreaAte = 0;
      if (painelIA) {
        painelIA.desenhar({
          mensagem: `A área da IA se fechou (ela fecha sozinha depois de ${MINUTOS_AREA_IA} minutos). Digite o PIN da IA de novo.`,
          focar: true,
        });
      }
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- sem PIN da IA

  function desenharSemPinIA(corpo, painel, est, opcoes) {
    const erro = h('p', { class: 'erro-campo', id: 'ia-criar-erro', role: 'alert' });
    const novo = campoPin('ia-pin-novo', { 'aria-describedby': 'ia-criar-erro' });
    const repetir = campoPin('ia-pin-repetir');
    const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Criar PIN da IA');
    const form = h('form', { novalidate: true, class: 'form-ia' },
      h('h3', null, 'Para começar, crie o PIN da IA'),
      h('p', null, 'Ele fecha só esta área (qual IA está escolhida, o endereço, o modelo e a chave) para quem encontrar o Entre Nós aberto. Os botões ✨ funcionam para quem abriu o Entre Nós com o PIN principal.'),
      h('p', { class: 'nota-cuidado' }, 'O PIN da IA não é uma segunda senha do arquivo: a chave fica guardada junto com o diário, e quem souber o PIN principal consegue ler a chave. Não conte o PIN principal a quem não deve ver a chave.'),
      h('div', { class: 'grade-2' },
        campo('PIN da IA', novo, `Diferente do PIN principal, e não uma variação dele (só maiúsculas, espaços ou um número no fim). Pelo menos ${PIN_MINIMO} caracteres; uma frase curta é ótima.`),
        campo('Repita o PIN da IA', repetir)),
      h('p', { class: 'sutil' }, AVISO_NAO_SALVAR_PIN),
      erro,
      h('div', { class: 'acoes' }, botao));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (esperandoPin(botao)) return;
      erro.textContent = '';
      const problema = problemaDoPin(novo.value);
      if (problema) {
        erro.textContent = problema.replace('O PIN ', 'O PIN da IA ');
        novo.focus();
        return;
      }
      if (novo.value !== repetir.value) {
        erro.textContent = 'Os dois PINs não são iguais. Digite de novo com calma.';
        repetir.focus();
        return;
      }
      botao.disabled = true;
      botao.textContent = 'Criando…';
      let espera = 0;
      try {
        await api('POST', '/ia/pin', { novo: novo.value });
        form.reset();
        estado.iaAreaAte = Date.now() + MINUTOS_AREA_IA * 60000;
        avisar('PIN da IA criado. Agora é só escolher a IA.');
        painel.desenhar({ focar: true });
      } catch (erroApi) {
        if (erroApi.bloqueio) return;
        if (erroApi.status === 409) {
          form.reset();
          painel.desenhar({ mensagem: erroApi.message, focar: true });
          return;
        }
        // 429: comparar o PIN da IA com o principal muitas vezes seguidas também faz esperar.
        espera = segundosDeEspera(erroApi);
        erro.textContent = erroApi.message;
        novo.focus();
      } finally {
        botao.disabled = false;
        botao.textContent = 'Criar PIN da IA';
      }
      if (espera && form.isConnected) contarEsperaPin(botao, espera);
    });
    limpar(corpo);
    inserir(corpo, mensagemIA(opcoes.mensagem), situacaoIA(est),
      // Uma IA configurada numa versão antiga (sem PIN da IA) continua valendo até ganhar um PIN.
      est.configurada ? h('p', null, 'Já existe uma IA configurada. Crie o PIN da IA para ver ou mudar qual é.') : null,
      introducaoIA(false), form);
    if (opcoes.focar) focarAreaIA(corpo);
  }

  function situacaoIA(est) {
    if (est.configurada && est.utilizavel === false) {
      // Salva, mas não dá para usar (uma IA de versão antiga sem a chave, por exemplo): sem ✨.
      return h('div', null,
        h('p', { class: 'situacao-ia' }, h('span', { class: 'chip' }, 'IA configurada, mas incompleta')),
        h('p', { class: 'nota-cuidado' }, 'A IA salva não pode ser usada assim (falta a chave, ou o endereço precisa de https://). Os botões ✨ ficam escondidos até ela ser corrigida aqui.'));
    }
    return est.configurada
      ? h('p', { class: 'situacao-ia' }, h('span', { class: 'chip chip-ligada' }, 'IA configurada'), ' ', seloDestino(!est.sai_do_computador))
      : h('p', { class: 'situacao-ia' }, h('span', { class: 'chip' }, 'Nenhuma IA configurada'));
  }

  // ---------------------------------------------------------------- área fechada (pede o PIN da IA)

  function desenharAreaIAFechada(corpo, painel, est, opcoes) {
    const erro = h('p', { class: 'erro-campo', id: 'ia-entrar-erro', role: 'alert' });
    const pin = campoPin('ia-pin', { 'aria-describedby': 'ia-entrar-erro' });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Abrir a área da IA');
    const form = h('form', { novalidate: true, class: 'form-ia' }, campo('PIN da IA', pin), erro, h('div', { class: 'acoes' }, botao));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (esperandoPin(botao)) return;
      erro.textContent = '';
      if (!pin.value) {
        erro.textContent = 'Digite o PIN da IA.';
        pin.focus();
        return;
      }
      botao.disabled = true;
      botao.textContent = 'Conferindo…';
      let espera = 0;
      try {
        await api('POST', '/ia/entrar', { pin_ia: pin.value }, { semBloqueio: true });
        pin.value = '';
        estado.iaAreaAte = Date.now() + MINUTOS_AREA_IA * 60000;
        painel.desenhar({ focar: true });
      } catch (erroApi) {
        pin.value = '';
        if (erroApi.status === 401 || erroApi.status === 423) {
          if (!(await sessaoContinua(erroApi))) return;
          erro.textContent = erroApi.doServidor ? erroApi.message : 'PIN da IA incorreto.';
        } else if (erroApi.status === 409) {
          painel.desenhar({ mensagem: erroApi.message, focar: true });
          return;
        } else {
          erro.textContent = erroApi.message;
        }
        espera = segundosDeEspera(erroApi);
        pin.focus();
      } finally {
        botao.disabled = false;
        botao.textContent = 'Abrir a área da IA';
      }
      if (espera && form.isConnected) contarEsperaPin(botao, espera);
    });

    // "Esqueci o PIN da IA": com o PIN principal, apaga o PIN da IA e a configuração da IA.
    const erroRedef = h('p', { class: 'erro-campo', id: 'ia-redef-erro', role: 'alert' });
    const principal = campoPin('ia-redef-pin', { 'aria-describedby': 'ia-redef-erro' });
    const botaoRedef = h('button', { type: 'submit', class: 'botao botao-perigo' }, 'Apagar a configuração da IA');
    const formRedef = h('form', { novalidate: true, class: 'form-ia' },
      h('p', null, 'Com o PIN principal (o que abre o Entre Nós), dá para apagar o PIN da IA junto com a configuração da IA: serviço, endereço, modelo e chave. Seus registros não mudam. Depois é só criar um PIN da IA novo e configurar de novo.'),
      campo('PIN principal', principal), erroRedef, h('div', { class: 'acoes' }, botaoRedef));
    formRedef.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (esperandoPin(botaoRedef)) return;
      erroRedef.textContent = '';
      if (!principal.value) {
        erroRedef.textContent = 'Digite o PIN principal.';
        principal.focus();
        return;
      }
      const ok = await confirmar({
        titulo: 'Apagar a configuração da IA?',
        texto: 'O PIN da IA e a IA escolhida (serviço, endereço, modelo e chave) serão apagados.\nSeus registros continuam como estão.',
        ok: 'Apagar',
        perigo: true,
      });
      if (!ok) return;
      botaoRedef.disabled = true;
      let espera = 0;
      try {
        await api('POST', '/ia/redefinir', { pin: principal.value }, { semBloqueio: true });
        principal.value = '';
        estado.iaAreaAte = 0;
        avisar('Pronto: o PIN da IA e a configuração da IA foram apagados. Seus registros continuam aqui.');
        painel.desenhar({ mensagem: 'O PIN da IA foi apagado. Crie um novo para configurar a IA de novo.', focar: true });
      } catch (erroApi) {
        principal.value = '';
        if (erroApi.status === 401 || erroApi.status === 423) {
          if (!(await sessaoContinua(erroApi))) return;
          erroRedef.textContent = erroApi.doServidor ? erroApi.message : 'O PIN principal não confere.';
        } else {
          erroRedef.textContent = erroApi.message;
        }
        espera = segundosDeEspera(erroApi);
        principal.focus();
      } finally {
        botaoRedef.disabled = false;
      }
      if (espera && formRedef.isConnected) contarEsperaPin(botaoRedef, espera);
    });

    limpar(corpo);
    inserir(corpo, mensagemIA(opcoes.mensagem), situacaoIA(est),
      h('p', null, 'Esta área é protegida pelo PIN da IA. Digite-o para ver ou mudar a IA escolhida.'),
      form,
      h('details', { class: 'detalhes-ajuda' }, h('summary', null, 'Esqueci o PIN da IA'), formRedef),
      introducaoIA(true));
    if (opcoes.focar) pin.focus();
  }

  // ---------------------------------------------------------------- área aberta

  async function desenharAreaIAAberta(corpo, painel, opcoes, valendo) {
    let cfg;
    try {
      cfg = await api('GET', '/ia/config');
    } catch (e) {
      if (e.bloqueio || !valendo()) return;
      if (pedePinIA(e)) {
        estado.iaAreaAte = 0;
        painel.desenhar({ mensagem: 'A área da IA se fechou. Digite o PIN da IA de novo.', focar: opcoes.focar });
        return;
      }
      desenharErroIA(corpo, painel, e);
      return;
    }
    if (!valendo()) return;          // saiu de Ajustes enquanto isso: nada da área fica na página
    estado.iaConfig = cfg && typeof cfg === 'object' ? cfg : {};
    if (!estado.iaAreaAte) estado.iaAreaAte = Date.now() + MINUTOS_AREA_IA * 60000;
    limpar(corpo);
    inserir(corpo,
      mensagemIA(opcoes.mensagem),
      h('p', { class: 'sutil' }, `Área da IA aberta. Ela se fecha sozinha em até ${MINUTOS_AREA_IA} minutos, ao bloquear o Entre Nós ou em “Fechar esta área”.`),
      introducaoIA(true),
      cartaoIA('principal'),
      cartaoIA('reserva'),
      formTempoIA(),
      formTrocarPinIA(),
      h('div', { class: 'acoes' },
        h('button', { type: 'button', class: 'botao botao-secundario', onclick: () => fecharAreaIA(painel) }, '🔒 Fechar esta área')));
    if (opcoes.focar) focarAreaIA(corpo);
  }

  async function fecharAreaIA(painel) {
    try {
      await api('POST', '/ia/sair', {});
    } catch (e) {
      if (e.bloqueio) return;
      if (!pedePinIA(e)) {
        avisarErro(e);
        return;
      }
    }
    estado.iaConfig = null;
    estado.iaAreaAte = 0;
    falar('Área da IA fechada.');
    painel.desenhar({ mensagem: 'Área da IA fechada. A configuração saiu da tela.', focar: true });
  }

  // ---------------------------------------------------------------- chave, URL e modelo

  // Como provedores.limpar_chave: tira espaços, aspas e um "Bearer " colado junto.
  function limparChaveIA(valor) {
    let chave = String(valor || '').trim().replace(/^["']+|["']+$/g, '').trim();
    if (/^bearer\s/i.test(chave)) chave = chave.slice(7).trim();
    return chave;
  }

  // O serviço pelo começo da chave; o prefixo mais longo vence ("sk-or-" antes de "sk-").
  // Chave de serviço não suportado ("sk-ant-") volta com `aviso`; começo que vários serviços
  // usam ("sk-" sozinho) volta com `dica` e sem serviço (a tela só sugere, não escolhe).
  function detectarPelaChave(valor, provedores, naoSuportados, ambiguos) {
    const chave = limparChaveIA(valor);
    let melhor = { tamanho: 0, provedor: null, aviso: null, dica: null };
    if (!chave) return melhor;
    for (const p of provedores) {
      for (const prefixo of lista(p.prefixos)) {
        if (typeof prefixo === 'string' && prefixo && chave.startsWith(prefixo) && prefixo.length > melhor.tamanho) {
          melhor = { tamanho: prefixo.length, provedor: p, aviso: null, dica: null };
        }
      }
    }
    for (const [prefixo, mensagem] of Object.entries(naoSuportados || {})) {
      if (prefixo && chave.startsWith(prefixo) && prefixo.length > melhor.tamanho) {
        melhor = { tamanho: prefixo.length, provedor: null, aviso: String(mensagem || 'Esse serviço ainda não é suportado.'), dica: null };
      }
    }
    for (const [prefixo, dica] of Object.entries(ambiguos || {})) {
      if (prefixo && chave.startsWith(prefixo) && prefixo.length > melhor.tamanho) {
        melhor = { tamanho: prefixo.length, provedor: null, aviso: null, dica: String(dica || '') };
      }
    }
    return melhor;
  }

  // Endereço deste computador (localhost, 127.x.x.x, ::1)? A rede de casa não conta.
  function enderecoLocal(url) {
    const bruto = String(url || '').trim();
    if (!bruto) return false;
    let host;
    try {
      host = new URL(bruto.includes('://') ? bruto : `http://${bruto}`).hostname.toLowerCase();
    } catch {
      return false;
    }
    host = host.replace(/^\[|\]$/g, '');
    return host === 'localhost' || host === '::1' || /^127(\.\d{1,3}){3}$/.test(host);
  }

  // Modelos "-cloud"/":cloud" do Ollama rodam em ollama.com, mesmo chamados pelo Ollama local.
  function modeloNaNuvem(modelo) {
    return /[-:]cloud(?:$|[-:])/i.test(String(modelo || '').trim());
  }

  // A chave salva nunca volta: só os 4 últimos caracteres ("••••7f3a").
  function textoChaveSalva(ia) {
    return ia && ia.chave_final ? `••••${ia.chave_final}` : '••••';
  }

  function chaveSalva(ia) {
    return h('span', { class: 'chave-final' },
      h('span', { 'aria-hidden': 'true' }, '••••'),
      ia && ia.chave_final ? [h('span', { class: 'so-leitor' }, 'terminada em '), ia.chave_final]
        : h('span', { class: 'so-leitor' }, 'guardada'));
  }

  // ---------------------------------------------------------------- cartão de uma IA

  function cartaoIA(qual) {
    const cfg = estado.iaConfig || {};
    const provs = lista(cfg.provedores).filter((p) => p && typeof p.id === 'string');
    const naoSuportados = cfg.nao_suportados && typeof cfg.nao_suportados === 'object' ? cfg.nao_suportados : {};
    const ambiguos = cfg.ambiguos && typeof cfg.ambiguos === 'object' ? cfg.ambiguos : {};
    const achar = (id) => provs.find((p) => p.id === id) || null;
    const salva = () => (estado.iaConfig && estado.iaConfig[qual]) || null;
    const principal = qual === 'principal';
    const titulo = principal ? 'IA principal' : 'IA de reserva';
    const base = `ia-${qual}`;
    let sujo = false;              // mudanças ainda não salvas (o teste usa o que está salvo)
    // "Outro" cuja base não tem versão (https://models.github.ai/inference): o endereço vale como
    // foi digitado, sem o /v1 automático. Vem da IA salva ou de "Buscar modelos".
    let urlExata = false;

    const linhaSalva = h('div', { class: 'ia-salva' });
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const resultado = h('p', { class: 'resultado-ia', role: 'status' });
    const dicaChave = h('p', { class: 'dica', id: `${base}-chave-dica` });
    const reconhecida = h('p', { class: 'dica dica-reconhecida', role: 'status' });
    const avisoChave = h('p', { class: 'erro-campo', role: 'alert' });
    const dicaUrl = h('p', { class: 'dica', id: `${base}-url-dica` });
    const destino = h('div', { class: 'destino-servico' });

    const servico = h('select', { id: `${base}-servico` },
      h('option', { value: '' }, 'Escolha um serviço…'),
      provs.map((p) => h('option', { value: p.id }, p.nome || p.id)));
    // A chave: campo de senha, nunca preenchido com a salva, limpo depois de salvar.
    const chave = h('input', {
      id: `${base}-chave`, type: 'password', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
      class: 'campo-chave', maxlength: '4096', 'aria-describedby': `${base}-chave-dica`,
      'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', 'data-form-type': 'other',
    });
    const apagarChave = h('input', { id: `${base}-apagar`, type: 'checkbox' });
    const linhaApagar = h('label', { class: 'opcao-linha', for: `${base}-apagar` }, apagarChave, ' Apagar a chave salva');
    const url = h('input', {
      id: `${base}-url`, type: 'text', inputmode: 'url', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
      class: 'campo-url', maxlength: '500', 'aria-describedby': `${base}-url-dica`,
    });
    const modelo = h('input', {
      id: `${base}-modelo`, type: 'text', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', maxlength: '200',
    });
    const listaModelos = h('select', { id: `${base}-lista` });
    const campoLista = h('div', { class: 'campo', hidden: true }, h('label', { for: `${base}-lista` }, 'Modelos disponíveis'), listaModelos);
    const botaoBuscar = h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno' }, 'Buscar modelos');
    const botaoSalvar = h('button', { type: 'button', class: 'botao botao-primario' }, 'Salvar');
    const botaoTestar = h('button', { type: 'button', class: 'botao botao-secundario' }, 'Testar conexão');
    const botaoRemover = h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto' }, 'Remover');

    const escolhido = () => achar(servico.value);
    const urlPadrao = (p) => {
      const s = salva();
      return s && s.provedor === p.id && s.url ? s.url : (p.url || '');
    };
    const modeloPadrao = (p) => {
      const s = salva();
      return s && s.provedor === p.id ? (s.modelo || '') : '';
    };

    function esconderLista() {
      campoLista.hidden = true;
      limpar(listaModelos);
    }

    function preencherPeloServico() {
      const p = escolhido();
      const outro = Boolean(p && p.id === 'outro');
      const s = salva();
      url.readOnly = !outro;
      url.value = p ? urlPadrao(p) : '';
      urlExata = Boolean(outro && s && s.provedor === p.id && s.url_exata === true);
      url.placeholder = outro ? 'Ex.: https://api.exemplo.com/v1' : '';
      modelo.value = p ? modeloPadrao(p) : '';
      modelo.placeholder = p && p.local ? 'Ex.: qwen3:4b' : 'Busque a lista ou digite';
      esconderLista();
      atualizarDicas();
    }

    function atualizarDicas() {
      const p = escolhido();
      const s = salva();
      const mesmo = Boolean(s && p && s.provedor === p.id);
      const chaveGuardada = mesmo && s.tem_chave;
      // Chave
      limpar(dicaChave);
      let texto;
      if (!p) texto = 'Cole a chave que o serviço te deu: eu reconheço o serviço pelo começo dela.';
      else if (chaveGuardada) texto = `Chave salva: ${textoChaveSalva(s)}. Deixe em branco para manter.`;
      else if (p.precisa_chave) texto = 'Este serviço precisa de uma chave.';
      else if (p.local) texto = 'Serviço neste computador: não precisa de chave.';
      else texto = 'Chave opcional: só se o serviço pedir.';
      inserir(dicaChave, texto);
      if (p && typeof p.pagina_chave === 'string' && /^https:\/\//i.test(p.pagina_chave)) {
        inserir(dicaChave, ' Onde criar: ',
          h('a', { href: p.pagina_chave, target: '_blank', rel: 'noopener noreferrer' }, p.pagina_chave.replace(/^https:\/\//i, '')), '.');
      }
      inserir(dicaChave, ' Se o navegador oferecer para salvar a chave, escolha “Nunca”.');
      linhaApagar.hidden = !chaveGuardada;
      if (!chaveGuardada) apagarChave.checked = false;
      // URL
      dicaUrl.textContent = !p ? ''
        : p.id === 'outro'
          ? (urlExata
            ? 'Endereço usado exatamente como está (este serviço não usa /v1). Na internet, só com https://.'
            : 'O endereço base do serviço, normalmente terminado em /v1 (se o serviço não usar /v1, “Buscar modelos” descobre sozinho). Na internet, só com https://.')
          : 'Endereço padrão do serviço. Para usar outro, escolha “Outro (compatível com OpenAI)”.';
      // Onde a IA roda e o alerta do serviço, em destaque
      limpar(destino);
      if (!p) return;
      const nuvem = modeloNaNuvem(modelo.value);
      const semEndereco = p.id === 'outro' && !url.value.trim();
      const local = enderecoLocal(url.value || p.url) && !nuvem;
      inserir(destino,
        semEndereco ? null : h('p', { class: 'linha-destino' }, 'Com esta escolha, a IA roda ', seloDestino(local), '.'),
        p.aviso ? h('p', { class: 'nota-importante aviso-servico' }, h('strong', null, '⚠️ Atenção: '), p.aviso) : null,
        nuvem ? h('p', { class: 'nota-importante aviso-servico' }, h('strong', null, '⚠️ Atenção: '), 'modelos “-cloud” do Ollama rodam na internet, em ollama.com.') : null,
        !local && !semEndereco && !p.aviso && !nuvem ? h('p', { class: 'dica' }, 'O texto de cada pedido ✨ vai para esse serviço.') : null);
    }

    function desenharSalva() {
      limpar(linhaSalva);
      const s = salva();
      botaoTestar.disabled = !s;
      botaoRemover.hidden = !s;
      if (!s) {
        inserir(linhaSalva, h('p', { class: 'sutil' }, principal
          ? 'Nenhuma IA escolhida ainda.'
          : 'Sem reserva. Ela é opcional e só entra quando a principal não responde.'));
        return;
      }
      const problemas = (estado.iaConfig && estado.iaConfig.problemas) || {};
      const problema = typeof problemas[qual] === 'string' && problemas[qual] ? problemas[qual] : null;
      inserir(linhaSalva,
        h('p', { class: 'ia-resumo' },
          h('span', { class: 'rotulo-salva' }, 'Salva: '), seloDestino(Boolean(s.local)), ' ',
          h('strong', null, s.nome_provedor || s.provedor || 'serviço'), ` · ${s.modelo || 'sem modelo'}`,
          s.tem_chave ? [' · chave ', chaveSalva(s)] : null),
        s.url ? h('p', { class: 'sutil url-ia' }, s.url) : null,
        problema ? h('p', { class: 'erro-campo', role: 'alert' }, `Esta IA não pode ser usada assim: ${problema} Corrija e salve de novo.`) : null);
    }

    function mudou() {
      sujo = true;
      erro.textContent = '';
    }

    servico.addEventListener('change', () => {
      mudou();
      reconhecida.textContent = '';
      preencherPeloServico();
    });

    // Colar (ou digitar) a chave escolhe o serviço sozinho, pelo começo dela.
    chave.addEventListener('input', () => {
      mudou();
      avisoChave.textContent = '';
      reconhecida.textContent = '';
      const d = detectarPelaChave(chave.value, provs, naoSuportados, ambiguos);
      if (d.aviso) {
        avisoChave.textContent = d.aviso;
        return;
      }
      if (d.dica) {
        // "sk-" é de vários serviços: só sugere, e só quando o serviço ainda não foi escolhido.
        if (!servico.value) reconhecida.textContent = d.dica;
        return;
      }
      if (!d.provedor) return;
      const nome = d.provedor.nome || d.provedor.id;
      if (d.provedor.id === servico.value) {
        reconhecida.textContent = `Chave reconhecida: ${nome}.`;
      } else if (servico.value === 'outro') {
        reconhecida.textContent = `Essa chave parece ser do serviço ${nome}. Se for, escolha ${nome} na lista.`;
      } else {
        servico.value = d.provedor.id;
        preencherPeloServico();
        reconhecida.textContent = `Reconheci uma chave do serviço ${nome} e escolhi ele na lista.`;
      }
    });

    url.addEventListener('input', () => { mudou(); urlExata = false; atualizarDicas(); });
    modelo.addEventListener('input', () => { mudou(); atualizarDicas(); });
    apagarChave.addEventListener('change', mudou);
    listaModelos.addEventListener('change', () => {
      if (!listaModelos.value) return;
      modelo.value = listaModelos.value;
      mudou();
      atualizarDicas();
    });

    // Validações comuns a "Buscar modelos" e "Salvar". Devolve {p, u, k} ou null (erro já mostrado).
    function conferirCampos() {
      erro.textContent = '';
      const p = escolhido();
      if (!p) {
        erro.textContent = 'Escolha o serviço (ou cole a chave, que eu reconheço).';
        servico.focus();
        return null;
      }
      const u = url.value.trim();
      if (p.id === 'outro' && !u) {
        erro.textContent = 'Informe o endereço da IA.';
        url.focus();
        return null;
      }
      const k = limparChaveIA(chave.value);
      if (k) {
        const d = detectarPelaChave(k, provs, naoSuportados, ambiguos);
        if (d.aviso) {
          erro.textContent = d.aviso;
          chave.focus();
          return null;
        }
      }
      return { p, u, k };
    }

    botaoBuscar.addEventListener('click', async () => {
      const campos = conferirCampos();
      if (!campos) return;
      const { p, u, k } = campos;
      const corpo = { provedor: p.id };
      if (u) corpo.url = u;
      if (urlExata && p.id === 'outro') corpo.url_exata = true;
      const s = salva();
      if (k) {
        corpo.chave = k;
      } else if (s && s.provedor === p.id && s.tem_chave && !apagarChave.checked) {
        corpo.qual = qual;            // usa a chave salva (só no mesmo endereço)
      } else if (p.precisa_chave) {
        erro.textContent = 'Cole a chave antes de buscar os modelos: este serviço pede uma.';
        chave.focus();
        return;
      }
      botaoBuscar.disabled = true;
      botaoBuscar.textContent = 'Buscando…';
      try {
        const r = await api('POST', '/ia/modelos', corpo);
        const modelos = lista(r && r.modelos).filter((m) => typeof m === 'string' && m);
        if (r && r.url_exata === true && typeof r.url === 'string' && p.id === 'outro') {
          // O serviço só respondeu no endereço como foi digitado (sem /v1): é esse que vai ser salvo.
          url.value = r.url;
          urlExata = true;
          mudou();
          atualizarDicas();
        }
        esconderLista();
        if (!modelos.length) {
          erro.textContent = 'O serviço não mostrou nenhum modelo. Digite o nome do modelo à mão.';
          modelo.focus();
          return;
        }
        inserir(listaModelos, h('option', { value: '' }, `Escolha um modelo (${modelos.length})`),
          modelos.map((m) => h('option', { value: m }, m)));
        if (modelos.includes(modelo.value)) listaModelos.value = modelo.value;
        campoLista.hidden = false;
        listaModelos.focus();
        falar(modelos.length === 1 ? 'Encontrei 1 modelo.' : `Encontrei ${modelos.length} modelos.`);
      } catch (e) {
        if (tratarErroAreaIA(e)) return;
        erro.textContent = e.message;
      } finally {
        botaoBuscar.disabled = false;
        botaoBuscar.textContent = 'Buscar modelos';
      }
    });

    async function salvar() {
      if (botaoSalvar.disabled) return;
      const campos = conferirCampos();
      if (!campos) return;
      const { p, u, k } = campos;
      const m = modelo.value.trim();
      if (!m) {
        erro.textContent = 'Informe o modelo (toque em “Buscar modelos” para ver a lista).';
        modelo.focus();
        return;
      }
      const corpo = { provedor: p.id, modelo: m };
      if (u) corpo.url = u;
      if (urlExata && p.id === 'outro') corpo.url_exata = true;
      if (k) corpo.chave = k;
      else if (apagarChave.checked) corpo.chave = '';        // "" apaga; sem o campo, mantém
      const s = salva();
      const guardada = Boolean(s && s.provedor === p.id && s.tem_chave && !apagarChave.checked);
      if (p.precisa_chave && !k && !guardada) {
        erro.textContent = `O serviço ${p.nome || p.id} precisa de uma chave.`;
        chave.focus();
        return;
      }
      botaoSalvar.disabled = true;
      botaoSalvar.textContent = 'Salvando…';
      try {
        const r = await api('PUT', `/ia/config/${qual}`, corpo);
        chave.value = '';                    // a chave não fica na tela
        apagarChave.checked = false;
        avisoChave.textContent = '';
        reconhecida.textContent = '';
        if (!estado.iaConfig) return;        // a área fechou enquanto isso
        estado.iaConfig[qual] = r && typeof r === 'object' ? r : null;
        if (estado.iaConfig.problemas) estado.iaConfig.problemas[qual] = null;     // salvar já confere tudo
        sujo = false;
        if (r && typeof r.url === 'string') url.value = r.url;
        urlExata = Boolean(r && r.url_exata === true);
        if (r && typeof r.modelo === 'string') modelo.value = r.modelo;
        limpar(resultado);
        desenharSalva();
        atualizarDicas();
        avisar(`${titulo} salva.`);
        atualizarInfoIA();
      } catch (e) {
        if (tratarErroAreaIA(e)) return;
        erro.textContent = e.message;
      } finally {
        botaoSalvar.disabled = false;
        botaoSalvar.textContent = 'Salvar';
      }
    }
    botaoSalvar.addEventListener('click', salvar);

    botaoTestar.addEventListener('click', async () => {
      erro.textContent = '';
      if (!salva()) {
        erro.textContent = 'Salve a configuração antes de testar.';
        return;
      }
      botaoTestar.disabled = true;
      botaoTestar.textContent = 'Testando…';
      resultado.classList.remove('resultado-erro');
      resultado.textContent = 'Testando com uma frase inventada (nada dos seus registros)… '
        + textoEsperaIA(estado.iaConfig && estado.iaConfig.tempo_limite);
      try {
        const r = (await api('POST', '/ia/testar', { qual })) || {};
        const segundos = typeof r.segundos === 'number'
          ? ` em ${r.segundos.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} s` : '';
        const formato = r.formato ? ` (formato: ${NOMES_FORMATO_IA[r.formato] || r.formato})` : '';
        resultado.textContent = r.ok
          ? `✅ Funcionou: a IA respondeu${segundos}${formato}.`
          : `❌ Não funcionou: ${r.erro || 'a IA não respondeu direito.'}`;
        if (sujo) resultado.textContent += ' Testei o que está salvo; as mudanças ainda não salvas ficaram de fora.';
        resultado.classList.toggle('resultado-erro', !r.ok);
      } catch (e) {
        resultado.textContent = '';
        if (tratarErroAreaIA(e)) return;
        erro.textContent = e.message;
      } finally {
        botaoTestar.disabled = !salva();
        botaoTestar.textContent = 'Testar conexão';
      }
    });

    botaoRemover.addEventListener('click', async () => {
      const ok = await confirmar({
        titulo: principal ? 'Remover a IA principal?' : 'Remover a IA de reserva?',
        texto: 'O serviço, o endereço, o modelo e a chave desta IA saem do Entre Nós. Seus registros não mudam.',
        ok: 'Remover',
        perigo: true,
      });
      if (!ok) return;
      try {
        await api('DELETE', `/ia/config/${qual}`);
        if (!estado.iaConfig) return;
        estado.iaConfig[qual] = null;
        if (estado.iaConfig.problemas) estado.iaConfig.problemas[qual] = null;
        chave.value = '';
        servico.value = '';
        sujo = false;
        resultado.textContent = '';
        avisoChave.textContent = '';
        reconhecida.textContent = '';
        preencherPeloServico();
        desenharSalva();
        avisar(`${titulo} removida.`);
        atualizarInfoIA();
        servico.focus();
      } catch (e) {
        if (tratarErroAreaIA(e)) return;
        avisarErro(e);
      }
    });

    const inicial = salva();
    servico.value = inicial && achar(inicial.provedor) ? inicial.provedor : '';
    preencherPeloServico();
    desenharSalva();

    return h('section', {
      class: 'cartao-ia', 'aria-labelledby': `${base}-titulo`,
      // Enter num campo de texto salva (não há <form>: o navegador não trata a chave como senha de site).
      onkeydown: (e) => {
        const alvo = e.target;
        if (e.key === 'Enter' && alvo instanceof HTMLInputElement && alvo.type !== 'checkbox') {
          e.preventDefault();
          salvar();
        }
      },
    },
    h('h3', { id: `${base}-titulo` }, h('span', { 'aria-hidden': 'true' }, principal ? '⭐ ' : '🛟 '), titulo),
    h('p', { class: 'sutil' }, principal
      ? 'A que responde quando você toca num ✨.'
      : 'Opcional. Entra só quando a principal não responde (fora do ar, tempo esgotado ou chave recusada).'),
    linhaSalva,
    campo('Serviço', servico),
    h('div', { class: 'campo' }, h('label', { for: `${base}-chave` }, 'Chave (API key)'), dicaChave, chave, reconhecida, avisoChave, linhaApagar),
    h('div', { class: 'campo' }, h('label', { for: `${base}-url` }, 'Endereço (URL)'), dicaUrl, url),
    destino,
    h('div', { class: 'campo' }, h('label', { for: `${base}-modelo` }, 'Modelo'),
      h('div', { class: 'linha-modelo' }, modelo, botaoBuscar)),
    campoLista,
    erro,
    h('div', { class: 'acoes' }, botaoSalvar, botaoTestar, botaoRemover),
    h('p', { class: 'dica' }, '“Testar conexão” manda uma frase inventada, nunca seus registros.'),
    resultado);
  }

  // ---------------------------------------------------------------- tempo limite e PIN da IA

  function formTempoIA() {
    const cfg = estado.iaConfig || {};
    const tempo = h('input', {
      id: 'aj-ia-tempo', type: 'number', min: String(TEMPO_IA_MINIMO), max: String(TEMPO_IA_MAXIMO), step: '1',
      inputmode: 'numeric', class: 'numero-curto', value: String(Number(cfg.tempo_limite) || TEMPO_IA_PADRAO),
      'aria-describedby': 'aj-ia-tempo-dica',
    });
    const botao = h('button', { type: 'submit', class: 'botao botao-secundario botao-pequeno' }, 'Salvar');
    const form = h('form', { novalidate: true, class: 'form-ia form-ia-tempo' },
      h('h3', null, h('span', { 'aria-hidden': 'true' }, '⏱️ '), 'Tempo limite'),
      h('div', { class: 'campo-linha' },
        h('label', { for: 'aj-ia-tempo' }, 'Esperar cada IA até'), tempo, h('span', null, 'segundos'), botao),
      h('p', { class: 'dica', id: 'aj-ia-tempo-dica' }, `De ${TEMPO_IA_MINIMO} a ${TEMPO_IA_MAXIMO} segundos. Passou disso, tenta a reserva; se ela também não responder, ficam as regras.`));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const v = Number(tempo.value);
      if (!Number.isInteger(v) || v < TEMPO_IA_MINIMO || v > TEMPO_IA_MAXIMO) {
        avisar(`Use um número inteiro de ${TEMPO_IA_MINIMO} a ${TEMPO_IA_MAXIMO} segundos.`, 'erro');
        tempo.focus();
        return;
      }
      botao.disabled = true;
      try {
        const r = await api('PATCH', '/ia/config', { tempo_limite: v });
        const salvo = r && typeof r.tempo_limite === 'number' ? r.tempo_limite : v;
        if (estado.iaConfig) estado.iaConfig.tempo_limite = salvo;
        tempo.value = String(salvo);
        avisar('Tempo limite salvo.');
      } catch (erroApi) {
        if (tratarErroAreaIA(erroApi)) return;
        avisarErro(erroApi);
      } finally {
        botao.disabled = false;
      }
    });
    return form;
  }

  function formTrocarPinIA() {
    const atual = campoPin('aj-ia-pin-atual');
    const novo = campoPin('aj-ia-pin-novo');
    const repetir = campoPin('aj-ia-pin-repetir');
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const botao = h('button', { type: 'submit', class: 'botao botao-secundario' }, 'Trocar PIN da IA');
    const form = h('form', { novalidate: true, class: 'form-ia' },
      campo('PIN da IA atual', atual),
      h('div', { class: 'grade-2' },
        campo('PIN da IA novo', novo, 'Diferente do PIN principal, e não uma variação dele. Uma frase curta é ótima.'),
        campo('Repita o PIN da IA novo', repetir)),
      h('p', { class: 'sutil' }, AVISO_NAO_SALVAR_PIN),
      erro,
      h('div', { class: 'acoes' }, botao));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (esperandoPin(botao)) return;
      erro.textContent = '';
      if (!atual.value) {
        erro.textContent = 'Digite o PIN da IA atual.';
        atual.focus();
        return;
      }
      const problema = problemaDoPin(novo.value);
      if (problema) {
        erro.textContent = problema.replace('O PIN ', 'O PIN da IA novo ');
        novo.focus();
        return;
      }
      if (novo.value !== repetir.value) {
        erro.textContent = 'Os dois PINs novos não são iguais.';
        repetir.focus();
        return;
      }
      botao.disabled = true;
      let espera = 0;
      try {
        await api('POST', '/ia/pin/trocar', { atual: atual.value, novo: novo.value }, { semBloqueio: true });
        form.reset();
        avisar('PIN da IA trocado. Use o novo da próxima vez.');
      } catch (erroApi) {
        if (tratarErroAreaIA(erroApi)) return;
        if (erroApi.status === 401 || erroApi.status === 423) {
          if (!(await sessaoContinua(erroApi))) return;
          erro.textContent = erroApi.doServidor ? erroApi.message : 'O PIN da IA atual não confere.';
          atual.value = '';
          atual.focus();
        } else {
          erro.textContent = erroApi.message;          // 429: "Espere N s antes de tentar o PIN de novo."
        }
        espera = segundosDeEspera(erroApi);
      } finally {
        botao.disabled = false;
      }
      if (espera && form.isConnected) contarEsperaPin(botao, espera);
    });
    return h('details', { class: 'detalhes-ia detalhes-pin-ia' },
      h('summary', null, h('span', { 'aria-hidden': 'true' }, '🔑 '), 'Trocar o PIN da IA'), form);
  }

  function secaoSair() {
    const sair = async () => {
      const ok = await confirmar({
        titulo: 'Fechar o Entre Nós?',
        texto: 'Tudo é salvo, a tela é bloqueada e o programa é encerrado neste computador.',
        ok: 'Fechar',
      });
      if (!ok) return;
      try {
        await api('POST', '/sair', {});
      } catch (e) {
        if (e.bloqueio) return;
        if (e.status !== 0) {
          avisarErro(e);
          return;
        }
        // status 0: o servidor encerrou antes de responder, que é o esperado.
      }
      esquecerToken();
      telaSaiu();
    };
    return h('section', { class: 'cartao', 'aria-labelledby': 'aj-sair' },
      h('h2', { id: 'aj-sair' }, '👋 Sair'),
      h('p', null, 'Salva tudo e encerra o Entre Nós. Para abrir de novo, use o atalho “Abrir Entre Nos”.'),
      h('div', { class: 'acoes' },
        h('button', { type: 'button', class: 'botao botao-secundario', onclick: () => bloquearAgora() }, '🔒 Só bloquear'),
        h('button', { type: 'button', class: 'botao botao-primario', onclick: sair }, 'Fechar o Entre Nós')));
  }

  // ================================================================ início

  function ligarEventos() {
    document.body.append(
      h('datalist', { id: 'lista-relacoes' }, RELACOES_SUGERIDAS.map((r) => h('option', { value: r }))),
      h('datalist', { id: 'lista-humores' }, HUMORES.map((r) => h('option', { value: r }))));

    window.addEventListener('hashchange', () => rotear());

    document.addEventListener('click', (e) => {
      const alvo = e.target instanceof Element ? e.target : null;
      if (!alvo) return;
      const acao = alvo.closest('[data-acao]');
      if (acao && acao.dataset.acao === 'bloquear') {
        e.preventDefault();
        bloquearAgora();
        return;
      }
      const menu = $('#menu-mais');
      if (menu && !menu.hidden && !alvo.closest('#menu-mais') && !alvo.closest('#botao-mais')) fecharMenuMais();
    });

    const botaoMais = $('#botao-mais');
    if (botaoMais) botaoMais.addEventListener('click', () => alternarMenuMais());

    const pular = $('#pular');
    if (pular) {
      pular.addEventListener('click', (e) => {
        e.preventDefault();
        const alvo = estado.modo === 'app' ? $('#conteudo') : $('#portao');
        if (alvo) alvo.focus();
      });
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const menu = $('#menu-mais');
        if (menu && !menu.hidden) {
          fecharMenuMais();
          $('#botao-mais').focus();
        }
      }
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && estado.atalho && estado.modo === 'app') {
        const dialogo = $('#dialogo');
        if (dialogo && dialogo.open) return;
        e.preventDefault();
        estado.atalho();
      }
    });

    // Um toque logo depois de o aparelho acordar não pode contar como "estava em uso": se o tempo
    // sem uso já passou do limite, bloqueia antes de marcar (seção 19.7).
    const marcarAtividade = () => {
      if (passouDoTempoSemUso()) {
        bloquearAgora(true);
        return;
      }
      estado.ultimaAtividade = Date.now();
    };
    for (const evento of ['pointerdown', 'keydown', 'input', 'wheel', 'touchstart', 'scroll']) {
      document.addEventListener(evento, marcarAtividade, { passive: true, capture: true });
    }
    setInterval(verificarInatividade, 15000);
    // Com a aba escondida ou o celular dormindo, o setInterval quase não roda: ao voltar, confere
    // na hora (pelo relógio de parede, Date.now), antes de a pessoa ver ou tocar em alguma coisa.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') verificarInatividade();
    });
    window.addEventListener('pageshow', () => verificarInatividade());
    window.addEventListener('focus', () => verificarInatividade());
  }

  // ================================================================ recursos: window.EntreNos (seção 20.0)
  //
  // As telas dos recursos (web/telas/<nome>.js, carregadas depois deste arquivo) se encaixam por aqui.
  // Contrato:
  // - registrarTela(rota, render): `#rota/...` chama render(main, partes, atual), como as telas daqui
  //   (`atual()` diz se a tela ainda é a mesma depois de um await);
  // - registrarCartaoHoje(tipo, render): cartão do Hoje com `tipo` (ou do recurso com esse NOME):
  //   render(cartao, {dispensar, remover, recarregar, botaoDispensar}) -> Node (o miolo do cartão);
  // - registrarAbaEla(id, titulo, render): aba em Conhecendo Ela; render(painel, {perfil, pessoas, atual});
  // - registrarSecaoAjustes(id, titulo, render): cartão em Ajustes; render(corpo, {config, salvarConfig, atual});
  // - registrarRevisaoExtra(tipo, render): miolo do cartão de um extra na revisão;
  //   render(extra, {recurso, revisao, redesenhar}) -> Node (confirmar/não guardar são daqui);
  // - registrarMenu(rota, rotulo, icone, grupo): item no menu lateral e, no celular, em "Mais".
  // render que falha não derruba a tela: o cartão genérico (ou uma mensagem) fica no lugar.
  // Desde a integração (seção 20.10):
  // - registrarRevisaoExtra(tipo, render, {opcional: true}): extra só informativo (salvar sem decidir
  //   não pede confirmação);
  // - abrirRegistrar({texto, data, contexto, humor, privado, juntar, organizar, aoSalvar(id)}) ->
  //   {ok} | {ok: false, motivo: 'revisao'|'bloqueado'}; rascunhoAtual() -> cópia do que está escrito;
  // - registrarAcaoRegistrar(id, rotulo, acao(rascunho, {limpar, focar})): botão ao lado do Organizar;
  // - registrarModoRegistrar(id, rotulo, aoEscolher(rascunho), dica?()): no alto do Registrar,
  //   ao lado de "✍️ Texto livre";
  // - eventos em window: 'entrenos:registro-salvo' {id, privado} e 'entrenos:bloqueado' (a página
  //   bloqueou: esqueça o que a tela guardou).

  function idRecurso(valor, onde) {
    if (typeof valor !== 'string' || !RE_ID_RECURSO.test(valor)) throw new TypeError(`${onde}: identificador inválido`);
    return valor;
  }

  function funcaoRecurso(valor, onde) {
    if (typeof valor !== 'function') throw new TypeError(`${onde}: render precisa ser uma função`);
    return valor;
  }

  function textoRecurso(valor, onde) {
    if (typeof valor !== 'string' || !valor.trim()) throw new TypeError(`${onde}: texto vazio`);
    return valor.trim();
  }

  function registrarTela(rota, render) {
    idRecurso(rota, 'registrarTela');
    if (ROTAS_DO_NUCLEO.has(rota)) throw new TypeError('registrarTela: rota do núcleo');
    ROTAS[rota] = funcaoRecurso(render, 'registrarTela');
    // A página pode ter aberto já nesta rota (antes de o recurso se registrar).
    const pedida = location.hash.replace(/^#\/?/, '').split('/')[0];
    if (estado.modo === 'app' && pedida === rota) rotear();
  }

  function registrarCartaoHoje(tipo, render) {
    cartoesHoje.set(idRecurso(tipo, 'registrarCartaoHoje'), funcaoRecurso(render, 'registrarCartaoHoje'));
  }

  function registrarAbaEla(id, titulo, render) {
    idRecurso(id, 'registrarAbaEla');
    if (ABAS_DO_NUCLEO.has(id) || abasEla.some((a) => a.id === id)) throw new TypeError('registrarAbaEla: aba repetida');
    const [emoji, nome] = separarEmoji(textoRecurso(titulo, 'registrarAbaEla'));
    abasEla.push({ id, emoji, nome, render: funcaoRecurso(render, 'registrarAbaEla') });
  }

  function registrarSecaoAjustes(id, titulo, render) {
    idRecurso(id, 'registrarSecaoAjustes');
    if (secoesAjustes.some((s) => s.id === id)) throw new TypeError('registrarSecaoAjustes: seção repetida');
    const [emoji, nome] = separarEmoji(textoRecurso(titulo, 'registrarSecaoAjustes'));
    secoesAjustes.push({ id, emoji, nome, render: funcaoRecurso(render, 'registrarSecaoAjustes') });
  }

  function registrarRevisaoExtra(tipo, render, opcoes) {
    const id = idRecurso(tipo, 'registrarRevisaoExtra');
    revisaoExtras.set(id, funcaoRecurso(render, 'registrarRevisaoExtra'));
    if (opcoes && typeof opcoes === 'object' && opcoes.opcional) revisaoOpcionais.add(id);
    else revisaoOpcionais.delete(id);
  }

  function registrarAcaoRegistrar(id, rotulo, acao) {
    idRecurso(id, 'registrarAcaoRegistrar');
    if (acoesRegistrar.some((a) => a.id === id)) throw new TypeError('registrarAcaoRegistrar: ação repetida');
    acoesRegistrar.push({ id, rotulo: textoRecurso(rotulo, 'registrarAcaoRegistrar'), acao: funcaoRecurso(acao, 'registrarAcaoRegistrar') });
  }

  function registrarModoRegistrar(id, rotulo, aoEscolher, dica) {
    idRecurso(id, 'registrarModoRegistrar');
    if (modosRegistrar.some((m) => m.id === id)) throw new TypeError('registrarModoRegistrar: modo repetido');
    modosRegistrar.push({
      id, rotulo: textoRecurso(rotulo, 'registrarModoRegistrar'), aoEscolher: funcaoRecurso(aoEscolher, 'registrarModoRegistrar'),
      dica: typeof dica === 'function' ? dica : null,
    });
  }

  function registrarMenu(rota, rotulo, icone, grupo = 'recursos') {
    idRecurso(rota, 'registrarMenu');
    if (ROTAS_DO_NUCLEO.has(rota) || menusRecursos.some((m) => m.rota === rota)) throw new TypeError('registrarMenu: menu repetido');
    menusRecursos.push({
      rota, rotulo: textoRecurso(rotulo, 'registrarMenu'), icone: typeof icone === 'string' ? icone : '',
      grupo: typeof grupo === 'string' && grupo ? grupo : 'recursos',
    });
    desenharMenusRecursos();
  }

  window.EntreNos = Object.freeze({
    api,
    h,
    selo,
    navegar: irPara,
    registrarTela,
    registrarCartaoHoje,
    registrarAbaEla,
    registrarSecaoAjustes,
    registrarRevisaoExtra,
    registrarMenu,
    // Integração (seção 20.10): Registrar aberto e preenchido por um recurso, sem mexer nos campos da tela.
    abrirRegistrar,
    rascunhoAtual: copiaDoRascunho,
    registrarAcaoRegistrar,
    registrarModoRegistrar,
    // Ajudantes das telas daqui, para os recursos terem a mesma cara (e não copiarem código).
    util: Object.freeze({
      anexar: inserir, limpar, novoId, lista, encurtar, normalizar, capitalizar, plural,
      cabecalho, secao, campo, carregando, estadoVazio, vazioPequeno, caixaErro, citacao, linkRegistro,
      hrefRegistro, chipsCategorias, legendaSelos, linhaEntrada, avisar, avisarErro, falar, confirmar,
      hojeIso, isoDe, paraData, dataBr, dataLonga, diaMes, mesAno, diaComSemana, somarDias, diasAte, textoDias,
      salvarConfig, focarTitulo,
    }),
  });

  // Começa quando a página inteira (com as telas dos recursos, carregadas com `defer` depois deste
  // arquivo) já rodou: assim o primeiro desenho já tem os cartões, abas e menus de todos.
  function quandoPronto(fazer) {
    let feito = false;
    const uma = () => {
      if (feito) return;
      feito = true;
      fazer();
    };
    if (document.readyState === 'complete') {
      uma();
      return;
    }
    document.addEventListener('DOMContentLoaded', uma, { once: true });
    window.addEventListener('load', uma, { once: true });
  }

  function iniciar() {
    // O index.html mostra "Abrindo o Entre Nós… se esta mensagem não sumir, falta um arquivo":
    // se este script carregou, a mensagem sai (sem script inline, a CSP continua valendo).
    const carregando = $('#carregando');
    if (carregando) carregando.remove();
    aplicarTema(lerTema());
    ligarEventos();
    quandoPronto(carregarEstado);
  }

  iniciar();
})();
