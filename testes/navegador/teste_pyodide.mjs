/* Teste do Entre Nós no Pyodide DE VERDADE, sem navegador (DESIGN.md, seção 21).
 *
 * Roda com qualquer Node 18+ (aqui, o do VS Code):
 *   $env:ELECTRON_RUN_AS_NODE='1'; & "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe" testes\navegador\teste_pyodide.mjs
 * Variáveis: ENTRENOS_PYTHON (o Python do PC; padrão: o do projeto, senão "python").
 *
 * O que faz (tudo numa pasta temporária, apagada no fim; nada de rede, nada em %LOCALAPPDATA%):
 *  1. monta o site com o construir_web.py (o mesmo do Cloudflare) e usa o Pyodide e o entrenos-py.zip
 *     de lá;
 *  2. carrega o Pyodide 314.0.7 + cryptography pelo web/entrenos_pyodide.mjs (o código do Worker) e
 *     mede a carga e o scrypt (aqui e no CPython);
 *  3. autoteste; e o fluxo pela ponte: configurar -> analisar -> registros (com extras de recurso) ->
 *     hoje -> busca -> bloquear -> desbloquear (PIN errado 401, de novo 429) -> exportar -> baixar a
 *     cópia -> agenda de avisos (só datas e números);
 *  4. .cofre criado pelo CPython do PC abre no Pyodide (restaurar e desbloquear) com o mesmo PIN, e o
 *     criado no Pyodide abre no CPython;
 *  5. o transporte do Worker (web/trabalhador.mjs) com um "self" de mentira: mensagens, fila, trava
 *     do diário (Web Locks), agenda depois de gravar e o bloqueio por inatividade;
 *  6. a agenda de avisos (web/agenda_avisos.js) com um IndexedDB de mentira, e o service worker
 *     (web/sw.js do site) num contexto isolado: pré-cache, nada de redirect/opaco, rotas, limpeza de
 *     caches velhos e o aviso "Você tem N lembretes hoje no Entre Nós".
 *
 * No Node não há IDBFS (usa MEMFS, com o mesmo FS.syncfs), Worker de navegador nem IndexedDB.
 * Windows: o Pyodide no Node chama process.getuid(), que só existe no Linux/macOS; sem ele o Rust do
 * cryptography aborta ao importar ("panic in a function that cannot unwind"). No navegador isso não
 * acontece (lá a chamada devolve 0); aqui o teste põe o mesmo 0.
 */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import vm from 'node:vm';

for (const nome of ['getuid', 'geteuid', 'getgid', 'getegid']) {
  if (typeof process[nome] !== 'function') process[nome] = () => 0;
}

const AQUI = path.dirname(fileURLToPath(import.meta.url));
const RAIZ = path.resolve(AQUI, '..', '..');
const PYTHON_PADRAO = path.join(os.homedir(), 'AppData', 'Local', 'Python', 'pythoncore-3.14-64', 'python.exe');
const PYTHON = process.env.ENTRENOS_PYTHON || (fs.existsSync(PYTHON_PADRAO) ? PYTHON_PADRAO : 'python');
const PIN = 'girassol na janela azul';
const TEXTO = 'A mãe dela, Sandra, vai fazer uma cirurgia em novembro. Ela disse que adora tulipas. '
  + 'Prometi levar ela ao cinema na sexta. Ela adora pipoca.';
const TEXTO_PC = 'Ela adora girassóis amarelos e chá de hibisco.';

const TEMPO = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'entrenos-pyodide-')));
// A pasta temporária sai mesmo se um passo falhar.
process.on('exit', () => fs.rmSync(TEMPO, { recursive: true, force: true }));
const medidas = {};
let passos = 0;

function ok(descricao) {
  passos += 1;
  console.log(`ok ${passos} - ${descricao}`);
}

function hojeIso(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function somarDias(dias) {
  const d = new Date();
  d.setDate(d.getDate() + dias);
  return d;
}

function python(args, entrada) {
  const r = spawnSync(PYTHON, args, {
    cwd: RAIZ, input: entrada, encoding: 'utf-8', timeout: 300000,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8', ENTRENOS_DADOS: path.join(TEMPO, 'env') },
  });
  if (r.error) throw r.error;
  if (r.status !== 0) throw new Error(`Python saiu com ${r.status}: ${r.stderr}`);
  return r.stdout;
}

// Um diário do PC (CPython, o mesmo núcleo do servidor): criar com um registro ou abrir e listar.
const SCRIPT_PC = String.raw`
import json, os, sys, time
sys.path.insert(0, os.getcwd())
from entrenos.nucleo import Nucleo
pasta, pin, acao, texto = sys.argv[1:5]
n = Nucleo(pasta, atraso_pin=0)
def pedir(metodo, caminho, corpo=None, token=None):
    r = n.tratar(metodo, caminho, corpo=None if corpo is None else json.dumps(corpo).encode("utf-8"), token=token)
    return r.status, json.loads(r.corpo)
saida = {}
if acao == "criar":
    st, d = pedir("POST", "/api/configurar", {"pin": pin, "nome_dela": "Bia"})
    token = d["token"]
    st, p = pedir("POST", "/api/analisar", {"texto": texto, "data_registro": "2026-10-01"}, token)
    st, r = pedir("POST", "/api/registros", {"texto": texto, "data_registro": "2026-10-01", "itens": p["itens"],
                                             "pessoas": p["pessoas"], "relacoes": []}, token)
    saida = {"status": st}
elif acao == "abrir":
    st, d = pedir("POST", "/api/desbloquear", {"pin": pin})
    saida = {"status": st}
    if st == 200:
        st, regs = pedir("GET", "/api/registros", token=d["token"])
        saida["textos"] = [r["texto"] for r in regs]
elif acao == "scrypt":
    from entrenos import cofre
    inicio = time.perf_counter()
    cofre._derivar(pin, os.urandom(16), cofre.SCRYPT_N, cofre.SCRYPT_R, cofre.SCRYPT_P)
    saida = {"ms": round((time.perf_counter() - inicio) * 1000)}
n.encerrar()
print(json.dumps(saida, ensure_ascii=False))
`;

function noPC(acao, pasta, pin = PIN, texto = TEXTO_PC) {
  fs.mkdirSync(pasta, { recursive: true });
  return JSON.parse(python(['-c', SCRIPT_PC, pasta, pin, acao, texto]).trim().split('\n').pop());
}

// ---------------------------------------------------------------- IndexedDB de mentira (agenda)

function criarIndexedDBFalso() {
  const bancos = new Map();
  const depois = (f) => setTimeout(f, 0);
  return {
    open(nome, versao) {
      const pedido = { result: null, error: null };
      depois(() => {
        let banco = bancos.get(nome);
        if (!banco) {
          banco = { versao: 0, lojas: new Map() };
          bancos.set(nome, banco);
        }
        const db = {
          objectStoreNames: { contains: (n) => banco.lojas.has(n) },
          createObjectStore: (n) => banco.lojas.set(n, new Map()),
          close() {},
          transaction(loja) {
            const mapa = banco.lojas.get(loja);
            const t = { error: null };
            const op = (fazer) => {
              const r = { result: undefined };
              depois(() => {
                r.result = fazer();
                if (r.onsuccess) r.onsuccess();
              });
              return r;
            };
            t.objectStore = () => ({
              get: (k) => op(() => (mapa.has(k) ? structuredClone(mapa.get(k)) : undefined)),
              put: (v, k) => op(() => { mapa.set(k, structuredClone(v)); return k; }),
              clear: () => op(() => { mapa.clear(); }),
            });
            depois(() => depois(() => { if (t.oncomplete) t.oncomplete(); }));
            return t;
          },
        };
        pedido.result = db;
        if (banco.versao < versao) {
          banco.versao = versao;
          if (pedido.onupgradeneeded) pedido.onupgradeneeded();
        }
        if (pedido.onsuccess) pedido.onsuccess();
      });
      return pedido;
    },
  };
}

// ---------------------------------------------------------------- 1. o site

const SITE = path.join(TEMPO, 'site');
{
  const t = Date.now();
  python(['construir_web.py', '--destino', SITE]);
  medidas.construir_web_ms = Date.now() - t;
  for (const nome of ['index.html', 'sw.js', '_headers', 'trabalhador.mjs', 'entrenos_pyodide.mjs', 'agenda_avisos.js',
    'entrenos-py.zip', 'manifest.webmanifest', 'pyodide/314.0.7/pyodide.asm.wasm']) {
    assert.ok(fs.existsSync(path.join(SITE, nome)), `falta ${nome} no site`);
  }
  ok('construir_web.py montou o site');
}

const { prepararPython, PASTA_DADOS } = await import(pathToFileURL(path.join(SITE, 'entrenos_pyodide.mjs')).href);
const { iniciarTrabalhador, pedirTrava, NOME_TRAVA } = await import(pathToFileURL(path.join(SITE, 'trabalhador.mjs')).href);
const indexURL = path.join(SITE, 'pyodide', '314.0.7').split(path.sep).join('/') + '/';
const { loadPyodide } = await import(pathToFileURL(path.join(SITE, 'pyodide', '314.0.7', 'pyodide.mjs')).href);

// ---------------------------------------------------------------- 2. carregar e medir

const progresso = [];
let app;
{
  const t = Date.now();
  app = await prepararPython({
    loadPyodide, indexURL, pacoteApp: fs.readFileSync(path.join(SITE, 'entrenos-py.zip')),
    aoAndar: (fracao, fase) => progresso.push([fracao, fase]),
    opcoesPyodide: { stderr: (linha) => console.log('[python]', linha) },
  });
  medidas.carga_total_ms = Date.now() - t;
  Object.assign(medidas, app.medidas);
  assert.deepEqual(progresso.map((p) => p[1]), ['inicio', 'python', 'criptografia', 'app']);
  assert.ok(progresso.every((p, i) => i === 0 || p[0] > progresso[i - 1][0]), 'o progresso só anda para a frente');
  ok(`Pyodide + cryptography + entrenos-py.zip carregados (${medidas.carga_total_ms} ms)`);
}
const py = app.pyodide;
{
  const ms = JSON.parse(py.runPython(`
import json, os, time
from entrenos import cofre
inicio = time.perf_counter()
cofre._derivar("${PIN}", os.urandom(16), cofre.SCRYPT_N, cofre.SCRYPT_R, cofre.SCRYPT_P)
json.dumps(round((time.perf_counter() - inicio) * 1000))`));
  medidas.scrypt_pyodide_ms = ms;
  medidas.scrypt_cpython_ms = noPC('scrypt', path.join(TEMPO, 'scrypt')).ms;
  ok(`scrypt do PIN (2^16, r=8): Pyodide ${ms} ms, CPython ${medidas.scrypt_cpython_ms} ms`);
}

// ---------------------------------------------------------------- 3. autoteste e o fluxo pela ponte

{
  const r = app.autoteste();
  assert.equal(r.ok, true, `autoteste falhou: ${JSON.stringify(r.falhas)}`);
  assert.deepEqual(r.falhas, []);
  assert.equal(r.python, '3.14.2');
  ok(`autoteste (Python ${r.python}, SQLite ${r.sqlite}) em ${app.medidas.autoteste_ms} ms`);
}

await app.montarDados('memfs');
app.iniciar();
let token = null;
const pedir = async (metodo, caminho, corpo, extras = {}) => app.tratar({
  metodo, caminho, corpo: corpo === undefined ? undefined : JSON.stringify(corpo), token, ...extras,
});

{
  let r = await pedir('GET', '/api/ping');
  assert.equal(r.status, 200);
  assert.equal(r.dados.app, 'entre-nos');
  r = await pedir('GET', '/api/estado');
  assert.equal(r.dados.configurado, false);
  assert.equal(r.dados.plataforma, 'navegador');
  assert.equal(r.dados.backup_em_pasta, false);
  assert.equal(r.dados.limite_arquivo, 16 * 1024 * 1024);
  const aniversario = somarDias(3);
  const t = Date.now();
  r = await pedir('POST', '/api/configurar', {
    pin: PIN, nome_dela: 'Ana',
    aniversario_dela: `2000-${String(aniversario.getMonth() + 1).padStart(2, '0')}-${String(aniversario.getDate()).padStart(2, '0')}`,
  });
  medidas.configurar_ms = Date.now() - t;
  assert.equal(r.status, 200, JSON.stringify(r.dados));
  assert.equal(r.gravou, true);
  token = r.dados.token;
  assert.ok(py.FS.readdir(PASTA_DADOS).includes('entrenos.cofre'));
  ok(`configurar (${medidas.configurar_ms} ms; o cofre ficou em ${PASTA_DADOS})`);

  const proposta = await pedir('POST', '/api/analisar', { texto: TEXTO, data_registro: hojeIso() });
  assert.equal(proposta.status, 200);
  assert.ok(proposta.dados.itens.length > 0);
  const extras = {};
  for (const [nome, lista] of Object.entries(proposta.dados.extras || {})) {
    const confirmados = lista.filter((e) => !e.informativo);
    if (confirmados.length) extras[nome] = confirmados;
  }
  assert.ok(extras.promessas && extras.promessas.length, `a promessa não veio nos extras: ${Object.keys(proposta.dados.extras || {})}`);
  r = await pedir('POST', '/api/registros', {
    texto: TEXTO, data_registro: hojeIso(), itens: proposta.dados.itens, pessoas: proposta.dados.pessoas,
    relacoes: [], extras,
  });
  assert.equal(r.status, 200, JSON.stringify(r.dados));
  assert.equal(r.gravou, true);
  r = await pedir('GET', '/api/promessas');
  assert.equal(r.status, 200);
  assert.ok(JSON.stringify(r.dados).includes('cinema'), 'a promessa (extra do recurso) não ficou gravada');
  ok(`analisar -> registros com extras dos recursos (${Object.keys(extras).join(', ')})`);

  r = await pedir('GET', '/api/hoje');
  assert.equal(r.status, 200);
  assert.ok(Array.isArray(r.dados.alertas) && r.dados.alertas.length > 0, 'o aniversário dela não apareceu no Hoje');
  r = await pedir('GET', '/api/busca?q=tulipas');
  assert.equal(r.status, 200);
  assert.ok(JSON.stringify(r.dados).includes('tulipas'));
  r = await pedir('GET', '/api/busca', undefined, { consulta: { q: 'cirurgia' } });
  assert.ok(JSON.stringify(r.dados).toLowerCase().includes('cirurgia'));
  ok('hoje e busca (pelo caminho e pela consulta)');

  const agenda = await app.agenda();
  assert.equal(agenda.aberto, true);
  const dias = Object.entries(agenda.dias);
  assert.ok(dias.length > 0, 'a agenda de avisos veio vazia');
  for (const [dia, n] of dias) {
    assert.match(dia, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(Number.isInteger(n) && n > 0 && n < 100);
  }
  assert.ok(agenda.dias[hojeIso(somarDias(3))] >= 1, 'o dia do aniversário não está na agenda');
  const bruto = JSON.stringify(agenda);
  for (const proibido of ['Ana', 'anivers', 'Sandra', 'cinema', 'tulipas']) assert.ok(!bruto.includes(proibido), `a agenda tem "${proibido}"`);
  ok(`agenda de avisos só com datas e números (${dias.length} dias)`);

  r = await pedir('POST', '/api/bloquear', {});
  assert.equal(r.status, 200);
  assert.equal((await app.agenda()).aberto, false);
  token = null;
  r = await pedir('POST', '/api/desbloquear', { pin: 'pin errado de propósito' });
  assert.equal(r.status, 401);
  assert.ok(r.dados.espere_segundos >= 1);
  r = await pedir('POST', '/api/desbloquear', { pin: PIN });
  assert.equal(r.status, 429, 'antes da hora deveria ser 429, mesmo com o PIN certo');
  assert.ok(Number(r.cabecalhos['Retry-After']) >= 1);
  await new Promise((resolve) => setTimeout(resolve, (r.dados.espere_segundos + 0.2) * 1000));
  const t2 = Date.now();
  r = await pedir('POST', '/api/desbloquear', { pin: PIN });
  medidas.desbloquear_ms = Date.now() - t2;
  assert.equal(r.status, 200, JSON.stringify(r.dados));
  token = r.dados.token;
  ok(`bloquear -> PIN errado 401 -> 429 com Retry-After -> desbloquear (${medidas.desbloquear_ms} ms)`);

  // Logo depois de abrir (conexão nova, de bytes), a primeira rota de recurso que usa json_each pelo
  // Banco.consultar: no SQLite 3.39 do Pyodide dava 500 até outra rota usar json_each antes (21.7).
  r = await pedir('GET', '/api/perguntas');
  assert.equal(r.status, 200, `perguntas logo depois de desbloquear: ${JSON.stringify(r.dados)}`);
  r = await pedir('GET', '/api/semana');
  assert.equal(r.status, 200, JSON.stringify(r.dados));
  assert.equal(r.gravou, false, 'ler a semana gravou algo (um erro no erros.log?)');
  r = await pedir('GET', '/api/hoje');
  assert.equal(r.status, 200);
  assert.equal(r.gravou, false, 'abrir o Hoje gravou algo (um recurso falhou e foi para o erros.log?)');
  assert.ok(!py.FS.readdir(PASTA_DADOS).includes('erros.log') || !py.FS.readFile(`${PASTA_DADOS}/erros.log`, { encoding: 'utf8' }).includes('perguntas'),
    'o recurso perguntas falhou (erros.log)');
  ok('logo depois de desbloquear: perguntas, semana e Hoje sem erro (json_each montado na conexão nova)');

  r = await pedir('GET', '/api/exportar');
  assert.equal(r.status, 200);
  assert.ok(r.dados.registros.some((x) => x.texto === TEXTO));
  r = await pedir('POST', '/api/backup', { pasta: 'E:\\' });
  assert.equal(r.status, 409);
  assert.equal(r.dados.baixar, true);
  r = await pedir('POST', '/api/backup/baixar', {});
  assert.equal(r.status, 200);
  const copia = Buffer.from(r.dados.arquivo_b64, 'base64');
  assert.ok(copia.subarray(0, 9).equals(Buffer.from('ENTRENOS1')));
  assert.equal(copia.length, r.dados.tamanho);
  r = await pedir('POST', '/api/ia/analisar', { texto: 'teste' });
  assert.equal(r.status, 503);
  ok('exportar, backup só baixando (409 na pasta), cópia .cofre e IA indisponível (503)');
}

// ---------------------------------------------------------------- 4. o mesmo .cofre no PC e no celular

{
  const pastaPc = path.join(TEMPO, 'pc');
  assert.equal(noPC('criar', pastaPc).status, 200);
  const doPc = fs.readFileSync(path.join(pastaPc, 'entrenos.cofre'));

  // (a) restaurar no primeiro uso, como a tela faz: o arquivo em bytes, sem base64.
  py.FS.mkdirTree('/celular-novo');
  app.ponte.iniciar_no_navegador('/celular-novo');
  token = null;
  let r = await app.restaurar(new Uint8Array(doPc), JSON.stringify({ pin: 'outro pin qualquer' }));
  assert.equal(r.status, 401);
  await new Promise((resolve) => setTimeout(resolve, (r.dados.espere_segundos + 0.2) * 1000));
  r = await app.restaurar(new Uint8Array(doPc), JSON.stringify({ pin: PIN }));
  assert.equal(r.status, 200, JSON.stringify(r.dados));
  token = r.dados.token;
  r = await pedir('GET', '/api/registros');
  assert.ok(r.dados.some((x) => x.texto === TEXTO_PC), 'o registro do PC não voltou');
  ok('backup .cofre do CPython restaurado no Pyodide com o mesmo PIN');

  // (b) o arquivo do PC direto na pasta de dados (como depois de uma cópia), e desbloquear.
  py.FS.mkdirTree('/celular-copia');
  py.FS.writeFile('/celular-copia/entrenos.cofre', new Uint8Array(doPc));
  app.ponte.iniciar_no_navegador('/celular-copia');
  token = null;
  r = await pedir('POST', '/api/desbloquear', { pin: PIN });
  assert.equal(r.status, 200, JSON.stringify(r.dados));
  token = r.dados.token;
  r = await pedir('GET', '/api/registros');
  assert.ok(r.dados.some((x) => x.texto === TEXTO_PC));
  ok('.cofre do CPython abre no Pyodide (desbloquear com o mesmo PIN)');

  // (c) e o contrário: o diário criado no Pyodide (passo 3) abre no CPython.
  app.ponte.iniciar_no_navegador(PASTA_DADOS);      // bloqueia /celular-copia e volta ao primeiro
  const pastaVolta = path.join(TEMPO, 'pc-volta');
  fs.mkdirSync(pastaVolta, { recursive: true });
  fs.writeFileSync(path.join(pastaVolta, 'entrenos.cofre'), py.FS.readFile(`${PASTA_DADOS}/entrenos.cofre`));
  const aberto = noPC('abrir', pastaVolta);
  assert.equal(aberto.status, 200);
  assert.ok(aberto.textos.includes(TEXTO), 'o registro do celular não abriu no PC');
  assert.equal(noPC('abrir', pastaVolta, 'pin errado mesmo').status, 401);
  ok('.cofre criado no Pyodide abre no CPython com o mesmo PIN (e não abre com outro)');
}

// ---------------------------------------------------------------- 5. o transporte do Worker

{
  const travas = (() => {
    const donos = new Map();
    const fila = new Map();
    return {
      request(nome, opcoes, funcao) {
        if (typeof opcoes === 'function') {
          funcao = opcoes;
          opcoes = {};
        }
        const segurar = () => {
          donos.set(nome, true);
          return Promise.resolve(funcao({ name: nome })).finally(() => {
            donos.delete(nome);
            const proximo = (fila.get(nome) || []).shift();
            if (proximo) proximo();
          });
        };
        if (!donos.has(nome)) return segurar();
        if (opcoes.ifAvailable) return Promise.resolve(funcao(null));
        return new Promise((resolve) => {
          fila.set(nome, [...(fila.get(nome) || []), () => resolve(segurar())]);
        });
      },
      _soltar(nome) {
        donos.delete(nome);
        const proximo = (fila.get(nome) || []).shift();
        if (proximo) proximo();
      },
    };
  })();

  // Outra aba já é dona do diário: este Worker avisa "ocupado" e espera.
  let segunda = null;
  travas.request(NOME_TRAVA, () => new Promise((resolve) => { segunda = resolve; }));
  globalThis.indexedDB = criarIndexedDBFalso();
  const Agenda = globalThis.EntreNosAgenda;
  assert.ok(Agenda, 'agenda_avisos.js não foi carregado pelo trabalhador.mjs');
  await Agenda.ligar();

  const recebidas = [];
  const transferidos = [];
  const escopo = {
    postMessage: (m, transferir) => { recebidas.push(m); transferidos.push(transferir || []); },
    addEventListener() {}, navigator: { locks: travas },
  };
  app.ponte.iniciar_no_navegador(PASTA_DADOS);
  const trabalhador = iniciarTrabalhador(escopo, {
    carregarPyodide: async () => async () => py,       // o mesmo Pyodide (carregar outro levaria a memória toda de novo)
    indexURL, baixarApp: async () => fs.readFileSync(path.join(SITE, 'entrenos-py.zip')),
    tipoDados: 'memfs', intervaloInatividade: 0, opcoesNucleo: { minutos_bloqueio: 0.05 },
  });
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.ok(recebidas.some((m) => m.tipo === 'ocupado'), 'não avisou que outra janela tem o diário');
  assert.ok(!recebidas.some((m) => m.tipo === 'pronto'), 'abriu o diário sem ter a trava');
  segunda();                                          // a outra aba fechou
  await trabalhador.pronto;
  const pronto = recebidas.find((m) => m.tipo === 'pronto');
  assert.ok(pronto && pronto.esperou === true);
  assert.ok(recebidas.filter((m) => m.tipo === 'progresso').map((m) => m.fracao).includes(1));
  ok('Worker: Web Locks (a segunda janela espera), progresso e "pronto"');

  let resposta = await trabalhador.receber({ id: 7, tipo: 'api', metodo: 'POST', caminho: '/api/desbloquear', corpo: JSON.stringify({ pin: PIN }) });
  assert.equal(resposta.ok, true);
  assert.equal(resposta.resposta.status, 200);
  assert.ok(recebidas.some((m) => m.id === 7 && m.ok === true), 'a resposta não voltou com o id');
  const tokenW = resposta.resposta.dados.token;
  await trabalhador.atualizarAgenda();
  const guardada = await Agenda.lerAgenda();
  assert.ok(guardada && Object.keys(guardada.dias).length > 0, 'o Worker não guardou a agenda ao desbloquear');
  assert.ok(guardada.dias[hojeIso(somarDias(3))] >= 1);

  resposta = await trabalhador.receber({ id: 8, tipo: 'restaurar', arquivo: new Uint8Array(3).buffer, corpo: JSON.stringify({ pin: PIN }), token: tokenW });
  assert.equal(resposta.resposta.status, 400);
  // A cópia do diário em bytes (22.2): ArrayBuffer transferido para a página, sem base64 no JSON.
  resposta = await trabalhador.receber({ id: 12, tipo: 'baixar', token: tokenW });
  assert.equal(resposta.resposta.status, 200, JSON.stringify(resposta.resposta.dados));
  const copiaW = resposta.resposta.arquivo;
  assert.ok(copiaW instanceof ArrayBuffer, 'a cópia não veio como ArrayBuffer');
  assert.equal(copiaW.byteLength, resposta.resposta.dados.tamanho);
  assert.ok(Buffer.from(copiaW).subarray(0, 9).equals(Buffer.from('ENTRENOS1')));
  assert.ok(!('arquivo_b64' in resposta.resposta.dados));
  const indice = recebidas.findIndex((m) => m.id === 12);
  assert.deepEqual(transferidos[indice], [copiaW], 'o ArrayBuffer não foi transferido');
  resposta = await trabalhador.receber({ id: 13, tipo: 'baixar', token: 'token-errado' });
  assert.equal(resposta.resposta.status, 401);
  assert.equal(resposta.resposta.arquivo, null);
  resposta = await trabalhador.receber({ id: 9, tipo: 'avisos-estado' });
  assert.deepEqual(resposta.resposta, { ligado: true, disponivel: true });
  resposta = await trabalhador.receber({ id: 10, tipo: 'avisos', ligar: false });
  assert.equal(resposta.resposta.ligado, false);
  assert.equal(await Agenda.lerAgenda(), null, 'desligar os avisos não apagou a agenda');
  ok('Worker: pedidos com id, restaurar em bytes, a cópia em bytes (transferida), agenda depois de abrir e desligar apaga a agenda');

  await new Promise((resolve) => setTimeout(resolve, 3500));   // minutos_bloqueio 0,05 = 3 s
  assert.equal(await trabalhador.verificarInatividade(), true);
  assert.ok(recebidas.some((m) => m.tipo === 'bloqueou'));
  resposta = await trabalhador.receber({ id: 11, tipo: 'api', metodo: 'GET', caminho: '/api/hoje', token: tokenW });
  assert.equal(resposta.resposta.status, 423);
  trabalhador.parar();
  ok('Worker: inatividade bloqueia sem pedido nenhum e avisa a página');

  assert.equal(await pedirTrava(null), 'sem-travas');
}

// ---------------------------------------------------------------- 6. agenda e service worker

{
  const A = globalThis.EntreNosAgenda;
  assert.deepEqual(A.limparDias({ '2026-10-08': 2, '2026-13': 1, 'x': 3, '2026-10-09': 0, '2026-10-10': 1.5, '2026-10-11': 500 }),
    { '2026-10-08': 2, '2026-10-11': 99 });
  assert.equal(A.textoDoAviso(1), 'Você tem 1 lembrete hoje no Entre Nós.');
  assert.equal(A.textoDoAviso(2), 'Você tem 2 lembretes hoje no Entre Nós.');
  const fabrica = criarIndexedDBFalso();
  assert.equal(await A.gravarAgenda({ '2026-10-08': 2 }, '2026-10-07', fabrica), false, 'gravou com os avisos desligados');
  await A.ligar(fabrica);
  assert.equal(await A.gravarAgenda({ '2026-10-08': 2 }, '2026-10-07', fabrica), true);
  assert.equal(await A.lembretesParaAvisar('2026-10-08', fabrica), 2);
  assert.equal(await A.lembretesParaAvisar('2026-10-08', fabrica), 0, 'avisou duas vezes no mesmo dia');
  assert.equal(await A.lembretesParaAvisar('2026-10-09', fabrica), 0);
  ok('agenda_avisos.js: só o formato combinado, só com os avisos ligados, um aviso por dia');

  const escopo = 'https://entrenos.exemplo/';
  const handlers = {};
  const notificacoes = [];
  const abertas = [];
  const caches = new Map();
  let respostaDe = () => ({ ok: true, status: 200, type: 'basic', redirected: false });
  const buscas = [];
  const ctx = {
    console, URL, setTimeout, structuredClone,
    indexedDB: criarIndexedDBFalso(),
    Notification: { permission: 'granted' },
    registration: { scope: escopo, showNotification: async (titulo, op) => { notificacoes.push([titulo, op]); } },
    clients: { claim: async () => {}, matchAll: async () => [], openWindow: async (u) => { abertas.push(u); } },
    skipWaiting: () => { ctx.pulou = true; },
    addEventListener: (tipo, f) => { handlers[tipo] = f; },
    importScripts: (...nomes) => { for (const n of nomes) vm.runInContext(fs.readFileSync(path.join(SITE, n), 'utf-8'), ctx); },
    Request: class { constructor(url, op) { this.url = url; Object.assign(this, op); } },
    fetch: async (pedido) => {
      buscas.push(pedido);
      const r = { url: pedido.url, ...respostaDe(pedido.url) };
      r.clone = () => r;
      return r;
    },
    caches: {
      open: async (nome) => {
        if (!caches.has(nome)) caches.set(nome, new Map());
        const mapa = caches.get(nome);
        return { put: async (url, r) => { mapa.set(url, r); }, match: async (url) => mapa.get(typeof url === 'string' ? url : url.url) };
      },
      keys: async () => [...caches.keys()],
      delete: async (nome) => caches.delete(nome),
    },
  };
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(SITE, 'sw.js'), 'utf-8'), ctx);
  for (const tipo of ['install', 'activate', 'fetch', 'message', 'periodicsync', 'notificationclick']) assert.ok(handlers[tipo], `sw.js sem ${tipo}`);

  const disparar = async (tipo, dados = {}) => {
    let espera = null;
    let resposta = null;
    handlers[tipo]({ ...dados, waitUntil: (p) => { espera = p; }, respondWith: (p) => { resposta = p; } });
    if (espera) await espera;
    return resposta;
  };
  caches.set('entrenos-app-velha', new Map());
  caches.set('outro-site', new Map());
  await disparar('install');
  const listaApp = vm.runInContext('ARQUIVOS_APP', ctx);
  const listaPy = vm.runInContext('ARQUIVOS_PYODIDE', ctx);
  const versao = vm.runInContext('VERSAO', ctx);
  assert.notEqual(versao, 'dev');
  assert.equal(caches.get(`entrenos-app-${versao}`).size, listaApp.length);
  assert.equal(caches.get('entrenos-pyodide-314.0.7').size, listaPy.length);
  assert.ok(buscas.every((b) => b.redirect === 'error'));
  assert.ok(buscas.filter((b) => b.url.includes('/pyodide/')).every((b) => b.cache === 'default'));
  assert.ok(buscas.filter((b) => !b.url.includes('/pyodide/')).every((b) => b.cache === 'reload'));
  await disparar('activate');
  assert.deepEqual([...caches.keys()].sort(), ['entrenos-app-' + versao, 'entrenos-pyodide-314.0.7', 'outro-site']);
  ok(`sw.js: pré-cache da versão ${versao} (${listaApp.length} do app, ${listaPy.length} do Pyodide) e limpeza dos velhos`);

  for (const ruim of [{ redirected: true }, { type: 'opaque', ok: false, status: 0 }, { ok: false, status: 404 }]) {
    caches.clear();
    respostaDe = (url) => (url.endsWith('app.js') ? { ok: true, status: 200, type: 'basic', redirected: false, ...ruim } : { ok: true, status: 200, type: 'basic', redirected: false });
    await assert.rejects(disparar('install'), `instalou com uma resposta ${JSON.stringify(ruim)}`);
  }
  respostaDe = () => ({ ok: true, status: 200, type: 'basic', redirected: false });
  caches.clear();
  await disparar('install');

  const pedidoDe = (relativo, extras = {}) => ({ request: { method: 'GET', url: escopo + relativo, mode: 'cors', ...extras } });
  assert.equal(await disparar('fetch', pedidoDe('api/ping')), null, 'o service worker não pode responder api/');
  assert.equal(await disparar('fetch', pedidoDe('app.js', { method: 'POST' })), null);
  assert.equal(await disparar('fetch', { request: { method: 'GET', url: 'https://outro.exemplo/app.js', mode: 'cors' } }), null);
  assert.equal(await disparar('fetch', pedidoDe('pyodide/314.0.7/licencas/LEIA-ME.txt', { mode: 'navigate' })), null);
  const doCache = await disparar('fetch', pedidoDe('app.js'));
  assert.ok(doCache && (await doCache).url.endsWith('/app.js'));
  const pagina = await (await disparar('fetch', pedidoDe('', { mode: 'navigate' })));
  assert.equal(pagina.url, escopo);
  const wasm = await (await disparar('fetch', pedidoDe('pyodide/314.0.7/pyodide.asm.wasm')));
  assert.ok(wasm.url.endsWith('pyodide.asm.wasm'));
  await disparar('message', { data: { tipo: 'pular-espera' } });
  assert.equal(ctx.pulou, true);
  ok('sw.js: nunca responde api/, POST nem outro site; página, app e Pyodide do cache; pula a espera só a pedido');

  const hoje = hojeIso();
  await disparar('periodicsync', { tag: 'entrenos-avisos' });
  assert.equal(notificacoes.length, 0, 'avisou com os avisos desligados');
  await vm.runInContext('self.EntreNosAgenda.ligar()', ctx);
  await vm.runInContext(`self.EntreNosAgenda.gravarAgenda({"${hoje}": 2}, "${hoje}")`, ctx);
  ctx.Notification.permission = 'denied';
  await disparar('periodicsync', { tag: 'entrenos-avisos' });
  assert.equal(notificacoes.length, 0, 'avisou sem permissão');
  ctx.Notification.permission = 'granted';
  await disparar('periodicsync', { tag: 'outra-coisa' });
  await disparar('periodicsync', { tag: 'entrenos-avisos' });
  assert.equal(notificacoes.length, 1);
  assert.equal(notificacoes[0][0], 'Entre Nós');
  assert.equal(notificacoes[0][1].body, 'Você tem 2 lembretes hoje no Entre Nós.');
  await disparar('periodicsync', { tag: 'entrenos-avisos' });
  assert.equal(notificacoes.length, 1, 'avisou duas vezes no mesmo dia');
  await disparar('notificationclick', { notification: { close() {} } });
  assert.deepEqual(abertas, [escopo + '#hoje']);
  ok('sw.js: "Você tem 2 lembretes hoje no Entre Nós" só com avisos ligados, permissão e uma vez por dia');
}

// ---------------------------------------------------------------- fim

fs.rmSync(TEMPO, { recursive: true, force: true });
console.log('\nmedidas:', JSON.stringify(medidas));
console.log(`\nTUDO OK (${passos} passos)`);
