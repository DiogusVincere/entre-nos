/* Entre Nós: o Web Worker do modo navegador (DESIGN.md, seção 21.1). Worker `type: "module"`.
 *
 * A página (app.js) não tem servidor no celular: cada pedido da API vem para cá por mensagem e vai
 * para o mesmo núcleo Python do computador, rodando no Pyodide (entrenos_pyodide.mjs). Aqui fica só
 * o transporte:
 * - abrir: Pyodide + cryptography + entrenos-py.zip, autoteste (falhou: avisa a página e não abre
 *   diário nenhum), trava do diário (Web Locks: uma aba/janela dona só; a outra ouve "já está aberto
 *   em outra janela" e abre sozinha quando a primeira fechar), IDBFS em /dados com syncfs(true) e
 *   o núcleo;
 * - atender `{id, tipo: 'api', metodo, caminho, corpo, token, consulta}`, `{id, tipo: 'restaurar',
 *   arquivo, corpo, token}` e `{id, tipo: 'baixar', token}` (a cópia do diário em bytes, com o
 *   ArrayBuffer transferido para a página, seção 21.7), um de cada vez; quando algum arquivo mudou,
 *   syncfs(false) termina antes de a resposta sair;
 * - de tempos em tempos, a conferência de inatividade (bloqueou: avisa a página);
 * - a agenda de avisos (seção 21.5): com os avisos ligados, depois de cada gravação (e ao abrir o
 *   diário) guarda fora do cofre só os dias com lembretes e quantos.
 * Mensagens para a página: progresso, ocupado, pronto, falhou, bloqueou. Nada de conteúdo, PIN ou
 * token em log nenhum.
 */
import { AppPython, PROGRESSO, VERSAO_PYODIDE, criarFila, prepararPython } from './entrenos_pyodide.mjs';
import './agenda_avisos.js';

export const NOME_TRAVA = 'entrenos-cofre';
const INTERVALO_INATIVIDADE = 30000;
const ABRE_O_DIARIO = /^\/api\/(?:desbloquear|configurar|restaurar)$/;
const FALHA_GERAL = 'O Entre Nós teve um problema neste navegador. Feche e abra de novo.';
const NAO_ABRIU = 'O Entre Nós não conseguiu abrir neste navegador.';

// Segura a trava do diário enquanto o Worker viver. Devolve 'dono' (ou 'sem-travas', em navegador
// sem Web Locks); `aoEsperar()` quando outra aba/janela já é dona (e espera ela soltar).
export function pedirTrava(travas, aoEsperar = () => {}) {
  if (!travas || typeof travas.request !== 'function') return Promise.resolve('sem-travas');
  return new Promise((resolve, reject) => {
    const segurar = () => new Promise(() => {});
    travas.request(NOME_TRAVA, { ifAvailable: true }, (trava) => {
      if (trava) {
        resolve('dono');
        return segurar();
      }
      aoEsperar();
      travas.request(NOME_TRAVA, () => {
        resolve('dono');
        return segurar();
      }).catch(reject);
      return undefined;
    }).catch(reject);
  });
}

function caminhoSemConsulta(caminho) {
  return String(caminho || '').split('#')[0].split('?')[0];
}

/* Liga o trabalhador em `escopo` (o self do Worker; nos testes do Node, um objeto de mentira).
 * opcoes (para os testes): carregarPyodide, indexURL, baixarApp, tipoDados, raizDados, agenda,
 * travas, intervaloInatividade (0 = sem temporizador), opcoesNucleo, opcoesPyodide, ouvir (false =
 * não liga o addEventListener). Devolve {pronto, receber(mensagem), verificarInatividade(), parar(),
 * app()}. */
export function iniciarTrabalhador(escopo, opcoes = {}) {
  const fila = criarFila();
  const agenda = 'agenda' in opcoes ? opcoes.agenda : (globalThis.EntreNosAgenda || null);
  const travas = 'travas' in opcoes ? opcoes.travas : ((escopo.navigator && escopo.navigator.locks) || null);
  const enviar = (mensagem, transferir) => escopo.postMessage(mensagem, transferir || []);
  let app = null;
  let falha = null;
  let temporizador = null;

  const carregarPyodide = opcoes.carregarPyodide
    || (async () => (await import(`./pyodide/${VERSAO_PYODIDE}/pyodide.mjs`)).loadPyodide);
  const indexURL = opcoes.indexURL || new URL(`./pyodide/${VERSAO_PYODIDE}/`, import.meta.url).href;
  const baixarApp = opcoes.baixarApp || (async () => {
    const resposta = await fetch(new URL('./entrenos-py.zip', import.meta.url), { credentials: 'same-origin' });
    if (!resposta.ok) throw new Error(`entrenos-py.zip: ${resposta.status}`);
    return resposta.arrayBuffer();
  });

  const andar = (fracao, fase) => enviar({ tipo: 'progresso', fracao, fase });

  async function abrir() {
    const t0 = Date.now();
    // A trava é pedida já (o Pyodide carrega enquanto outra aba não solta), mas o diário só é
    // tocado depois de ter a trava.
    let esperou = false;
    // Sem Web Locks que funcionem, segue sem trava: o núcleo ainda recusa gravar por cima do que
    // outra aba gravou (CofreMudou, seção 19.7).
    const trava = pedirTrava(travas, () => {
      esperou = true;
      enviar({ tipo: 'ocupado' });
    }).catch(() => 'sem-travas');
    let python;
    try {
      const [loadPyodide, pacoteApp] = await Promise.all([carregarPyodide(), baixarApp()]);
      python = await prepararPython({ loadPyodide, indexURL, pacoteApp, aoAndar: andar, opcoesPyodide: opcoes.opcoesPyodide || {} });
    } catch (erro) {
      falha = { motivo: 'carga' };
      enviar({ tipo: 'falhou', motivo: 'carga', tipo_erro: erro && erro.name ? String(erro.name) : 'Error' });
      throw erro;
    }
    let teste;
    try {
      teste = python.autoteste();
    } catch {
      teste = { ok: false, falhas: ['autoteste: erro'] };
    }
    if (!teste || teste.ok !== true) {
      falha = { motivo: 'autoteste' };
      enviar({ tipo: 'falhou', motivo: 'autoteste', falhas: Array.isArray(teste && teste.falhas) ? teste.falhas : [] });
      throw new Error('autoteste');
    }
    andar(PROGRESSO.autoteste, 'autoteste');
    await trava;
    if (esperou) andar(PROGRESSO.autoteste, 'trava');       // a outra janela soltou: segue abrindo
    try {
      const t1 = Date.now();
      await python.montarDados(opcoes.tipoDados || 'idbfs', opcoes.raizDados || null);
      python.iniciar(opcoes.opcoesNucleo || {});
      python.medidas.dados_ms = Date.now() - t1;
    } catch (erro) {
      falha = { motivo: 'dados' };
      enviar({ tipo: 'falhou', motivo: 'dados', tipo_erro: erro && erro.name ? String(erro.name) : 'Error' });
      throw erro;
    }
    app = python;
    await semDiarioSemAgenda();
    python.medidas.total_ms = Date.now() - t0;
    andar(PROGRESSO.pronto, 'pronto');
    enviar({ tipo: 'pronto', medidas: { ...python.medidas }, esperou });
    const intervalo = opcoes.intervaloInatividade ?? INTERVALO_INATIVIDADE;
    if (intervalo > 0) temporizador = setInterval(() => { verificarInatividade().catch(() => {}); }, intervalo);
    return python;
  }

  // Sem diário neste aparelho, a agenda de avisos (e o "ligado") não fica para trás (seção 21.5).
  async function semDiarioSemAgenda() {
    if (!agenda) return;
    try {
      const estado = await app.tratar({ metodo: 'GET', caminho: '/api/estado' });
      if (estado.status === 200 && estado.dados && estado.dados.configurado === false) await agenda.apagarTudo();
    } catch {
      // a agenda fica como estava; nada disso impede o diário de abrir
    }
  }

  const pronto = abrir();
  pronto.catch(() => {});

  async function atualizarAgenda() {
    if (!agenda || !app || !(await agenda.ligado())) return false;
    const r = await app.agenda();
    if (!r.aberto) return false;
    return agenda.gravarAgenda(r.dias, agenda.diaDe(new Date()));
  }

  function verificarInatividade() {
    return fila(async () => {
      if (!app) return false;
      const r = await app.verificarInatividade();
      if (r.bloqueou) enviar({ tipo: 'bloqueou' });
      return r.bloqueou;
    });
  }

  async function atender(mensagem) {
    try {
      await pronto;
    } catch {
      return { ok: false, erro: NAO_ABRIU, falha: falha && falha.motivo };
    }
    const tipo = mensagem.tipo;
    if (tipo === 'api' || tipo === 'restaurar') {
      const resposta = await fila(() => (tipo === 'api'
        ? app.tratar(mensagem)
        : app.restaurar(mensagem.arquivo, mensagem.corpo, mensagem.token)));
      const caminho = tipo === 'api' ? caminhoSemConsulta(mensagem.caminho) : '/api/restaurar';
      if (agenda && (resposta.gravou || (resposta.status === 200 && ABRE_O_DIARIO.test(caminho)))) {
        fila(atualizarAgenda).catch(() => {});
      }
      return { ok: true, resposta: { status: resposta.status, cabecalhos: resposta.cabecalhos, dados: resposta.dados, encerrado: Boolean(resposta.encerrado) } };
    }
    if (tipo === 'baixar') {
      // A cópia do diário em bytes (sem base64 dentro do JSON), transferida para a página (22.2).
      const resposta = await fila(() => app.baixarCopia(mensagem.token));
      const arquivo = resposta.arquivo && resposta.arquivo.buffer ? resposta.arquivo.buffer : null;
      return { ok: true, resposta: { status: resposta.status, cabecalhos: resposta.cabecalhos, dados: resposta.dados, arquivo },
        transferir: arquivo ? [arquivo] : [] };
    }
    if (tipo === 'avisos') {
      if (!agenda) return { ok: false, erro: 'Os avisos não funcionam neste navegador.' };
      if (mensagem.ligar) {
        await agenda.ligar();
        await fila(atualizarAgenda);
      } else {
        await agenda.apagarTudo();
      }
      return { ok: true, resposta: { ligado: await agenda.ligado() } };
    }
    if (tipo === 'avisos-estado') {
      return { ok: true, resposta: { ligado: agenda ? await agenda.ligado() : false, disponivel: Boolean(agenda) } };
    }
    return { ok: false, erro: 'Pedido desconhecido.' };
  }

  async function receber(mensagem) {
    if (!mensagem || typeof mensagem !== 'object') return null;
    let resultado;
    try {
      resultado = await atender(mensagem);
    } catch (erro) {
      if (erro && erro.pyodide_fatal_error) enviar({ tipo: 'falhou', motivo: 'fatal' });
      resultado = { ok: false, erro: FALHA_GERAL };
    }
    const { transferir, ...saida } = resultado || {};
    if (mensagem.id != null) enviar({ id: mensagem.id, ...saida }, transferir);
    return saida;
  }

  if (opcoes.ouvir !== false) escopo.addEventListener('message', (evento) => { receber(evento.data); });

  return {
    pronto,
    receber,
    verificarInatividade,
    atualizarAgenda: () => fila(atualizarAgenda),
    app: () => app,
    parar() {
      if (temporizador) clearInterval(temporizador);
      temporizador = null;
    },
  };
}

// No Worker de verdade, liga sozinho. Importado por um teste (Node), só exporta.
if (typeof WorkerGlobalScope !== 'undefined' && globalThis instanceof WorkerGlobalScope) {
  iniciarTrabalhador(globalThis);
}

export { AppPython };
