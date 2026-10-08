/* Teste da sincronização pela página (web/sincronizar.js), sem navegador e sem o Google de verdade
 * (DESIGN.md, seção 22).
 *
 * Roda com qualquer Node 18+ (aqui, o do VS Code):
 *   $env:ELECTRON_RUN_AS_NODE='1'; & "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe" testes\navegador\teste_sincronizar.mjs
 * Variáveis: ENTRENOS_PYTHON (o Python do PC, para montar o site); SEM_PYODIDE=1 pula a parte B.
 *
 * Parte A (rápida): um Drive de MENTIRA (servidor HTTP local, porta livre, que imita os endereços da
 * Drive API v3 que o Entre Nós usa: listar a appDataFolder, baixar, criar em multipart, atualizar e
 * apagar, e o "about") e o motor da página com um núcleo de mentira: só o próprio arquivo é
 * atualizado, o que não mudou nem é baixado, 401 (token vencido) pede um toque, sem rede fica na
 * fila e vai quando a rede volta, erros do Google viram frases humanas, 30 s de agrupamento depois de
 * salvar, a corrida de dois aparelhos criando a configuração, as revisões antigas do próprio arquivo
 * apagadas, os arquivos antigos deste aparelho apagados, arquivo grande demais nem baixado, e o login do
 * Google (a janela do fluxo de token, de mentira, sem biblioteca do Google, seção 22.2): os parâmetros,
 * o `state` conferido, escopo além do drive.appdata recusado e revogado, janela bloqueada, cancelada
 * ou sem resposta, e o token vence.
 *
 * Parte B (Pyodide de verdade, ~20 s): três aparelhos com o núcleo Python de verdade (sincronizacao.py
 * rodando no Pyodide), cada um com o seu motor da página, todos no mesmo Drive de mentira: senha
 * (errada e certa), registros novos de cada lado, apagar num e sumir nos outros sem voltar, e nada
 * em claro no Drive.
 *
 * Nada de rede de verdade, nada de navegador, nada em %LOCALAPPDATA%; a pasta temporária sai no fim.
 */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
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
const TEMPO = fs.mkdtempSync(path.join(os.tmpdir(), 'entrenos-sync-'));
process.on('exit', () => fs.rmSync(TEMPO, { recursive: true, force: true }));

let passos = 0;
function ok(descricao) {
  passos += 1;
  console.log(`ok ${passos} - ${descricao}`);
}

// Tudo o que o teste escreve no console passa por aqui: no fim, nenhum token apareceu.
const ditos = [];
for (const nivel of ['log', 'info', 'warn', 'error', 'debug']) {
  const original = console[nivel].bind(console);
  console[nivel] = (...args) => {
    ditos.push(args.map(String).join(' '));
    original(...args);
  };
}

vm.runInThisContext(fs.readFileSync(path.join(RAIZ, 'web', 'sincronizar.js'), 'utf-8'), { filename: 'sincronizar.js' });
const S = globalThis.EntreNosSincronizar;
assert.ok(S && typeof S.criarSincronizador === 'function', 'sincronizar.js não expôs o motor');
assert.equal(S.ESCOPO, 'https://www.googleapis.com/auth/drive.appdata');
assert.equal(S.URL_AUTORIZAR, 'https://accounts.google.com/o/oauth2/v2/auth');
assert.equal(S.URL_REVOGAR, 'https://oauth2.googleapis.com/revoke');
assert.ok(!('URL_GIS' in S), 'a biblioteca do Google voltou para a página');

// ---------------------------------------------------------------- o Drive de mentira

function criarDriveFalso() {
  const arquivos = new Map();       // id -> {id, name, bytes, mimeType, createdTime, modifiedTime, md5Checksum, parents}
  const tokens = new Set();
  const pedidos = [];
  const falhas = [];                // [{status, razao}] para os próximos pedidos
  let contador = 0;
  let relogio = Date.parse('2026-10-07T12:00:00Z');
  let paginaMaxima = 1000;

  function info(a) {
    const { bytes, parents, revisoes, ...resto } = a;
    return { ...resto, size: String(bytes.length) };
  }

  function responder(res, status, corpo, tipo = 'application/json') {
    const dados = Buffer.isBuffer(corpo) ? corpo : Buffer.from(typeof corpo === 'string' ? corpo : JSON.stringify(corpo));
    res.writeHead(status, { 'Content-Type': tipo, 'Content-Length': dados.length });
    res.end(dados);
  }

  function erro(res, status, razao) {
    responder(res, status, { error: { code: status, message: 'falso', status: razao, errors: [{ reason: razao }] } });
  }

  function novoArquivo(nome, bytes, mimeType, parents) {
    contador += 1;
    relogio += 1000;
    const id = `arq${String(contador).padStart(4, '0')}${randomBytes(3).toString('hex')}`;
    const agora = new Date(relogio).toISOString();
    const a = { id, name: nome, bytes, mimeType, parents, createdTime: agora, modifiedTime: agora,
      md5Checksum: createHash('md5').update(bytes).digest('hex'), revisoes: [`rev${contador}a`] };
    arquivos.set(id, a);
    return a;
  }

  function multipart(corpo, tipo) {
    const limite = /boundary=([^;]+)/.exec(tipo || '');
    if (!limite) return null;
    const sep = Buffer.from(`--${limite[1]}`);
    const partes = [];
    let inicio = corpo.indexOf(sep);
    while (inicio >= 0) {
      const proximo = corpo.indexOf(sep, inicio + sep.length);
      if (proximo < 0) break;
      const parte = corpo.subarray(inicio + sep.length + 2, proximo - 2);   // sem \r\n do começo e do fim
      const fimCabeca = parte.indexOf('\r\n\r\n');
      partes.push({ cabeca: parte.subarray(0, fimCabeca).toString(), corpo: parte.subarray(fimCabeca + 4) });
      inicio = proximo;
    }
    return partes;
  }

  const servidor = http.createServer((req, res) => {
    const pedacos = [];
    req.on('data', (p) => pedacos.push(p));
    req.on('end', () => {
      const corpo = Buffer.concat(pedacos);
      const url = new URL(req.url, 'http://drive.falso');
      const autorizacao = /^Bearer (.+)$/.exec(req.headers.authorization || '');
      pedidos.push({ metodo: req.method, caminho: url.pathname, busca: url.search, token: autorizacao && autorizacao[1] });
      if (!autorizacao || !tokens.has(autorizacao[1])) {
        erro(res, 401, 'UNAUTHENTICATED');
        return;
      }
      if (falhas.length) {
        const f = falhas.shift();
        erro(res, f.status, f.razao);
        return;
      }
      const m = /^\/(upload\/)?drive\/v3\/(files|about)(?:\/([^/]+)(?:\/(revisions)(?:\/([^/]+))?)?)?$/.exec(url.pathname);
      if (!m) {
        erro(res, 404, 'notFound');
        return;
      }
      const [, envio, recurso, id, revisoes, revisao] = m;
      if (revisoes) {
        const a = arquivos.get(decodeURIComponent(id));
        if (!a) {
          erro(res, 404, 'notFound');
          return;
        }
        if (req.method === 'GET' && !revisao) {
          responder(res, 200, { revisions: a.revisoes.map((r) => ({ id: r })) });
          return;
        }
        if (req.method === 'DELETE' && revisao) {
          if (a.revisoes.length <= 1) {
            erro(res, 403, 'cannotDeleteLastRevision');
            return;
          }
          a.revisoes = a.revisoes.filter((r) => r !== decodeURIComponent(revisao));
          res.writeHead(204);
          res.end();
          return;
        }
        erro(res, 400, 'badRequest');
        return;
      }
      if (recurso === 'about' && req.method === 'GET') {
        responder(res, 200, { user: { displayName: 'Pessoa de Teste', emailAddress: 'pessoa@exemplo.com' } });
        return;
      }
      if (!envio && !id && req.method === 'GET') {
        assert.equal(url.searchParams.get('spaces'), 'appDataFolder', 'listou fora da pasta do app');
        const todos = [...arquivos.values()].sort((a, b) => (a.createdTime < b.createdTime ? -1 : 1));
        const desde = Number(url.searchParams.get('pageToken') || 0);
        const tamanho = Math.min(Number(url.searchParams.get('pageSize') || 100), paginaMaxima);
        const pagina = todos.slice(desde, desde + tamanho).map(info);
        responder(res, 200, { files: pagina, ...(desde + tamanho < todos.length ? { nextPageToken: String(desde + tamanho) } : {}) });
        return;
      }
      if (!envio && id && req.method === 'GET' && url.searchParams.get('alt') === 'media') {
        const a = arquivos.get(decodeURIComponent(id));
        if (!a) erro(res, 404, 'notFound');
        else responder(res, 200, a.bytes, a.mimeType);
        return;
      }
      if (!envio && id && req.method === 'DELETE') {
        if (!arquivos.delete(decodeURIComponent(id))) erro(res, 404, 'notFound');
        else {
          res.writeHead(204);
          res.end();
        }
        return;
      }
      if (envio && !id && req.method === 'POST' && url.searchParams.get('uploadType') === 'multipart') {
        const partes = multipart(corpo, req.headers['content-type']);
        assert.ok(partes && partes.length === 2, 'envio multipart com duas partes');
        const meta = JSON.parse(partes[0].corpo.toString('utf-8'));
        assert.deepEqual(meta.parents, ['appDataFolder'], 'criou fora da pasta do app');
        const a = novoArquivo(meta.name, Buffer.from(partes[1].corpo), meta.mimeType, meta.parents);
        responder(res, 200, info(a));
        return;
      }
      if (envio && id && req.method === 'PATCH' && url.searchParams.get('uploadType') === 'media') {
        const a = arquivos.get(decodeURIComponent(id));
        if (!a) {
          erro(res, 404, 'notFound');
          return;
        }
        relogio += 1000;
        a.bytes = corpo;
        a.revisoes.push(`rev${relogio}`);
        a.modifiedTime = new Date(relogio).toISOString();
        a.md5Checksum = createHash('md5').update(corpo).digest('hex');
        responder(res, 200, info(a));
        return;
      }
      erro(res, 400, 'badRequest');
    });
  });

  return {
    arquivos, tokens, pedidos, falhas,
    set paginaMaxima(n) { paginaMaxima = n; },
    porNome: (nome) => [...arquivos.values()].filter((a) => a.name === nome),
    novoArquivo,
    async ligar() {
      await new Promise((resolve) => servidor.listen(0, '127.0.0.1', resolve));
      const { port } = servidor.address();
      return { base: `http://127.0.0.1:${port}/drive/v3`, baseEnvio: `http://127.0.0.1:${port}/upload/drive/v3`, port };
    },
    desligar: () => new Promise((resolve) => servidor.close(resolve)),
  };
}

// Um relógio de mentira para os temporizadores do motor (30 s de agrupamento, fila, novas tentativas).
function criarRelogio() {
  const tarefas = new Map();
  let proximo = 1;
  return {
    agendar(f, ms) {
      const id = proximo++;
      tarefas.set(id, { f, ms });
      return id;
    },
    cancelar(id) {
      tarefas.delete(id);
    },
    pendentes: () => [...tarefas.values()].map((t) => t.ms),
    async disparar() {
      const lista = [...tarefas.entries()];
      tarefas.clear();
      for (const [, t] of lista) await t.f();
    },
  };
}

// Uma conta de mentira (o GIS fica para o teste da conta): token na mão do teste.
function contaFalsa(token) {
  const c = { valor: token, esquecida: 0 };
  return Object.assign(c, {
    tokenValido: () => Boolean(c.valor),
    token: () => c.valor,
    esquecer: () => {
      c.valor = null;
      c.esquecida += 1;
    },
  });
}

// ---------------------------------------------------------------- Parte A: o motor com um núcleo de mentira

const DRIVE = criarDriveFalso();
const ENDERECOS = await DRIVE.ligar();
let semRede = false;
const buscar = (url, opcoes) => (semRede ? Promise.reject(new TypeError('fetch failed')) : fetch(url, opcoes));

/* Um núcleo de mentira: guarda o que recebeu e devolve um "arquivo" que é só o nome do aparelho e uma
 * contagem (o de verdade é testado na parte B e em testes/test_sincronizacao.py). */
function nucleoFalso(aparelho) {
  const n = {
    aparelho, configurada: false, chamadas: [], recebidos: [], etiquetas: {}, versao: 0, syncJson: null,
    apagar: [], limite: 64 * 1024 * 1024, enviados: [], erroReceber: null,
    async api(metodo, rota, corpo) {
      n.chamadas.push(`${metodo} ${rota}`);
      const arquivo = `entrenos-${aparelho}.sync`;
      if (rota === '/sync/estado') {
        return { configurada: n.configurada, sync_json: n.syncJson, automatica: true, apagar: n.apagar, limite_arquivo: n.limite };
      }
      if (rota === '/sync/senha') {
        if (corpo.senha !== 'senha boa de verdade') {
          const e = new Error('Essa não é a senha de sincronização dos seus outros aparelhos.');
          e.status = 401;
          e.dados = { erro: e.message, espere_segundos: 1 };
          throw e;
        }
        n.configurada = true;
        n.syncJson = corpo.sync_json || `{"falso":"${aparelho}"}`;
        return { aparelho, arquivo, sync_json: corpo.sync_json ? null : n.syncJson, criado: !corpo.sync_json };
      }
      if (rota === '/sync/iniciar') return { aparelho, arquivo, etiquetas: { ...n.etiquetas } };
      if (rota === '/sync/receber') {
        const texto = Buffer.from(corpo.conteudo_b64, 'base64').toString('utf-8');
        if (texto === 'estragado' || texto === 'antigo') {
          const e = new Error(texto === 'antigo' ? 'Um arquivo de sincronização no Google Drive é mais antigo.'
            : 'Um arquivo de sincronização no Google Drive não abre com a sua senha.');
          e.status = 400;
          e.dados = { erro: e.message, arquivo_invalido: true };
          throw e;
        }
        n.recebidos.push(texto);
        n.etiquetas[corpo.nome.slice(9, 25)] = corpo.etiqueta;
        return { registros_novos: 1, apagados: 0, atualizados: 0, conflitos: 0, avisos: [] };
      }
      if (rota === '/sync/preparar') {
        n.versao += 1;
        return { nome: arquivo, conteudo_b64: Buffer.from(`${aparelho}#${n.versao}`).toString('base64') };
      }
      if (rota === '/sync/enviado') {
        n.enviados.push(corpo);
        if (Array.isArray(corpo.apagados)) n.apagar = n.apagar.filter((x) => !corpo.apagados.includes(x));
        return { ultima: '2026-10-07T12:00:00+00:00' };
      }
      throw new Error(`rota inesperada ${rota}`);
    },
  };
  return n;
}

function montar(aparelho, token, extras = {}) {
  DRIVE.tokens.add(token);
  const nucleo = nucleoFalso(aparelho);
  const conta = contaFalsa(token);
  const drive = S.criarDrive({ buscar, token: () => conta.token(), base: ENDERECOS.base, baseEnvio: ENDERECOS.baseEnvio });
  const relogio = criarRelogio();
  const mudancas = [];
  const motor = S.criarSincronizador({ api: nucleo.api, drive, conta, relogio, online: () => !semRede,
    aoMudar: (e) => mudancas.push(e), ...extras });
  return { nucleo, conta, drive, relogio, motor, mudancas, token };
}

const A = montar('aaaaaaaaaaaaaaaa', `tok-a-${randomBytes(8).toString('hex')}`);
const B = montar('bbbbbbbbbbbbbbbb', `tok-b-${randomBytes(8).toString('hex')}`);

{
  // Primeiro aparelho: não há entrenos-sync.json -> cria; senha errada não liga nada.
  assert.equal(await A.motor.precisaDeSenha(), 'criar');
  await assert.rejects(A.motor.ligarComSenha('senha ruim'), (e) => e.status === 401);
  assert.equal(DRIVE.porNome('entrenos-sync.json').length, 0, 'criou a configuração com a senha errada');
  assert.deepEqual(await A.motor.ligarComSenha('senha boa de verdade'), { criou: true });
  assert.equal(DRIVE.porNome('entrenos-sync.json').length, 1);
  assert.equal(await B.motor.precisaDeSenha(), 'digitar');
  assert.deepEqual(await B.motor.ligarComSenha('senha boa de verdade'), { criou: false });
  assert.equal(B.nucleo.syncJson, A.nucleo.syncJson, 'o segundo aparelho não usou a configuração do primeiro');
  ok('senha: o primeiro cria o entrenos-sync.json; o segundo usa o dele; senha errada não muda nada');
}

{
  const r = await A.motor.sincronizar();
  assert.equal(r.recebidos, 0);
  assert.equal(DRIVE.porNome('entrenos-aaaaaaaaaaaaaaaa.sync').length, 1);
  const rb = await B.motor.sincronizar();
  assert.equal(rb.recebidos, 1);
  assert.deepEqual(B.nucleo.recebidos, ['aaaaaaaaaaaaaaaa#1']);
  // De novo: o arquivo do A não mudou (a mesma etiqueta) e nem é baixado.
  const baixados = () => DRIVE.pedidos.filter((p) => p.busca.includes('alt=media') && p.token === B.token).length;
  const antes = baixados();
  await B.motor.sincronizar();
  assert.equal(baixados(), antes + 1, 'baixou mais do que o entrenos-sync.json');
  await A.motor.sincronizar();
  await B.motor.sincronizar();
  assert.deepEqual(B.nucleo.recebidos, ['aaaaaaaaaaaaaaaa#1', 'aaaaaaaaaaaaaaaa#2']);
  // Cada aparelho só atualizou o próprio arquivo; nunca apagou o de outro.
  const idDe = (nome) => DRIVE.porNome(nome)[0].id;
  for (const [tok, nome] of [[A.token, 'entrenos-aaaaaaaaaaaaaaaa.sync'], [B.token, 'entrenos-bbbbbbbbbbbbbbbb.sync']]) {
    const atualizados = DRIVE.pedidos.filter((p) => p.metodo === 'PATCH' && p.token === tok).map((p) => decodeURIComponent(p.caminho.split('/').pop()));
    assert.ok(atualizados.length >= 1);
    assert.ok(atualizados.every((id) => id === idDe(nome)), 'atualizou o arquivo de outro aparelho');
  }
  assert.equal(DRIVE.pedidos.filter((p) => p.metodo === 'DELETE' && !p.caminho.includes('/revisions/')).length, 0, 'apagou um arquivo');
  assert.equal(DRIVE.porNome('entrenos-bbbbbbbbbbbbbbbb.sync')[0].bytes.toString(), `bbbbbbbbbbbbbbbb#${B.nucleo.versao}`);
  ok('sincronizar: baixa só o que mudou, cria e depois atualiza SÓ o próprio arquivo, nunca apaga o dos outros');
}

{
  // 22.2: depois de atualizar, as revisões antigas do PRÓPRIO arquivo saem do Drive (o que foi apagado
  // não fica lá, nem embaralhado); as dos outros nunca são tocadas.
  for (const [x, nome] of [[A, 'entrenos-aaaaaaaaaaaaaaaa.sync'], [B, 'entrenos-bbbbbbbbbbbbbbbb.sync']]) {
    assert.deepEqual(DRIVE.porNome(nome)[0].revisoes.length, 1, `${nome}: ficaram revisões antigas`);
    const apagadas = DRIVE.pedidos.filter((p) => p.metodo === 'DELETE' && p.token === x.token);
    assert.ok(apagadas.length > 0 && apagadas.every((p) => p.caminho.includes(DRIVE.porNome(nome)[0].id)), 'apagou revisão de outro arquivo');
  }
  // Um arquivo antigo DESTE aparelho (de uma senha anterior): sai do Drive e o núcleo fica sabendo.
  const velho = DRIVE.novoArquivo('entrenos-aaaaaaaaaaaa0000.sync', Buffer.from('de outra senha'), 'application/octet-stream', ['appDataFolder']);
  A.nucleo.apagar = [velho.name];
  const antes = A.nucleo.recebidos.length;
  assert.ok(await A.motor.sincronizar());
  assert.equal(DRIVE.porNome(velho.name).length, 0);
  assert.deepEqual(A.nucleo.enviados.at(-1).apagados, [velho.name]);
  assert.deepEqual(A.nucleo.apagar, []);
  assert.ok(!A.nucleo.recebidos.includes('de outra senha'), 'o arquivo antigo foi entregue ao núcleo como de outro aparelho');
  ok('revisões antigas do próprio arquivo apagadas; o arquivo antigo deste aparelho (outra senha) também');
}

{
  // Arquivo de outro aparelho maior que o limite do núcleo: nem é baixado (antes, 70 MB a cada vez).
  const grande = DRIVE.novoArquivo('entrenos-eeeeeeeeeeeeeeee.sync', Buffer.alloc(4096, 1), 'application/octet-stream', ['appDataFolder']);
  B.nucleo.limite = 1024;
  const baixou = () => DRIVE.pedidos.filter((p) => p.caminho.endsWith(grande.id) && p.busca.includes('alt=media')).length;
  for (let vez = 0; vez < 2; vez += 1) {
    const r = await B.motor.sincronizar();
    assert.ok(r.avisos.includes(S.MENSAGENS.grande_outro));
  }
  assert.equal(baixou(), 0);
  B.nucleo.limite = 64 * 1024 * 1024;
  DRIVE.arquivos.delete(grande.id);
  // Um arquivo que o núcleo recusa (antigo, mexido): a frase do núcleo, e não é baixado de novo
  // enquanto não mudar.
  const antigo = DRIVE.novoArquivo('entrenos-ffffffffffffffff.sync', Buffer.from('antigo'), 'application/octet-stream', ['appDataFolder']);
  const vezes = () => DRIVE.pedidos.filter((p) => p.caminho.endsWith(antigo.id) && p.busca.includes('alt=media')).length;
  let r = await B.motor.sincronizar();
  assert.ok(r.avisos.includes('Um arquivo de sincronização no Google Drive é mais antigo.'), JSON.stringify(r.avisos));
  r = await B.motor.sincronizar();
  assert.ok(r.avisos.includes('Um arquivo de sincronização no Google Drive é mais antigo.'));
  assert.equal(vezes(), 1);
  DRIVE.arquivos.delete(antigo.id);
  ok('arquivo grande demais nem é baixado; arquivo recusado não é baixado de novo enquanto não mudar');
}

{
  // Listagem em várias páginas (nextPageToken).
  DRIVE.paginaMaxima = 1;
  const nomes = (await A.drive.listar()).map((a) => a.name).sort();
  DRIVE.paginaMaxima = 1000;
  assert.deepEqual(nomes, ['entrenos-aaaaaaaaaaaaaaaa.sync', 'entrenos-bbbbbbbbbbbbbbbb.sync', 'entrenos-sync.json']);
  assert.equal(await A.drive.conta(), 'pessoa@exemplo.com');
  ok('Drive: listar em páginas e o "about" (para mostrar "conectado como")');
}

{
  // Token vencido: 401 do Google -> esquece o token e pede um toque; nada fica pela metade.
  DRIVE.tokens.delete(A.token);
  const r = await A.motor.sincronizar({ motivo: 'salvou' });
  assert.equal(r, null);
  const e = A.motor.estado();
  assert.equal(e.situacao, 'precisa_entrar');
  assert.equal(e.erro, S.MENSAGENS.token);
  assert.equal(A.conta.esquecida, 1);
  assert.equal(A.conta.token(), null);
  // Sem token, nem tenta (a janela do Google só abre com um toque, na tela).
  const pedidosAntes = DRIVE.pedidos.length;
  await A.motor.sincronizar({ motivo: 'abriu' });
  assert.equal(DRIVE.pedidos.length, pedidosAntes);
  assert.equal(A.motor.estado().situacao, 'precisa_entrar');
  // Um toque depois (token novo): segue.
  A.conta.valor = `tok-a2-${randomBytes(8).toString('hex')}`;
  DRIVE.tokens.add(A.conta.valor);
  assert.ok(await A.motor.sincronizar());
  assert.equal(A.motor.estado().situacao, 'ok');
  ok('401 (token vencido): esquece o token, mostra "toque para sincronizar" e não tenta sozinho');
}

{
  // Sem rede: fica na fila; quando a rede volta, vai.
  semRede = true;
  B.motor.depoisDeSalvar();
  assert.deepEqual(B.relogio.pendentes(), [S.AGRUPAR_MS]);
  await B.relogio.disparar();
  assert.equal(B.motor.estado().situacao, 'na_fila');
  assert.equal(B.motor.estado().erro, S.MENSAGENS.rede);
  semRede = false;
  const versao = B.nucleo.versao;
  B.motor.voltou();
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(B.motor.estado().situacao, 'ok');
  assert.equal(B.nucleo.versao, versao + 1);
  // Uma falha de rede de verdade (conexão recusada) também vira "sem internet".
  const morto = S.criarDrive({ buscar: fetch, token: () => 'x', base: 'http://127.0.0.1:9/drive/v3', baseEnvio: 'http://127.0.0.1:9/upload/drive/v3' });
  await assert.rejects(morto.listar(), (e) => e.tipo === 'rede' && e.message === S.MENSAGENS.rede);
  ok('sem internet: fica na fila (com nova tentativa marcada) e vai quando a rede volta');
}

{
  // Agrupamento: salvar três vezes seguidas dá UMA sincronização, 30 s depois da última.
  const antes = B.nucleo.versao;
  B.motor.depoisDeSalvar();
  B.motor.depoisDeSalvar();
  B.motor.depoisDeSalvar();
  assert.deepEqual(B.relogio.pendentes(), [S.AGRUPAR_MS]);
  await B.relogio.disparar();
  assert.equal(B.nucleo.versao, antes + 1);
  // Salvou no meio de uma sincronização: termina e marca mais uma (agrupada).
  const andamento = B.motor.sincronizar();
  B.motor.depoisDeSalvar();
  await andamento;
  assert.deepEqual(B.relogio.pendentes(), [S.AGRUPAR_MS]);
  await B.relogio.disparar();
  assert.equal(B.nucleo.versao, antes + 3);
  // Bloqueou: nada fica marcado.
  B.motor.depoisDeSalvar();
  B.motor.bloqueou();
  assert.deepEqual(B.relogio.pendentes(), []);
  ok('depois de salvar: ~30 s de agrupamento, uma sincronização só; bloquear cancela');
}

{
  // Erros do Google em frases humanas (e os passageiros voltam sozinhos mais tarde).
  const casos = [
    [{ status: 403, razao: 'accessNotConfigured' }, 'api_desligada', 'erro'],
    [{ status: 403, razao: 'insufficientPermissions' }, 'permissao', 'erro'],
    [{ status: 403, razao: 'storageQuotaExceeded' }, 'espaco', 'erro'],
    [{ status: 429, razao: 'rateLimitExceeded' }, 'limite', 'na_fila'],
    [{ status: 403, razao: 'userRateLimitExceeded' }, 'limite', 'na_fila'],
    [{ status: 500, razao: 'backendError' }, 'servidor', 'na_fila'],
  ];
  for (const [falha, tipo, situacao] of casos) {
    DRIVE.falhas.push(falha);
    await A.motor.sincronizar();
    const e = A.motor.estado();
    assert.equal(e.tipoErro, tipo, JSON.stringify(falha));
    assert.equal(e.situacao, situacao);
    assert.equal(e.erro, S.MENSAGENS[tipo]);
    assert.ok(!/[{}]|error|reason|http/i.test(e.erro), 'mensagem técnica na tela');
    if (situacao === 'na_fila') {
      assert.ok(A.relogio.pendentes().length === 1 && A.relogio.pendentes()[0] >= 60000, 'sem nova tentativa marcada');
    }
  }
  A.relogio.cancelar(1);
  DRIVE.falhas.length = 0;
  assert.ok(await A.motor.sincronizar());
  ok('erros do Google: Drive API desligada, permissão, espaço, limite e falha passageira, cada um com a sua frase');
}

{
  // Arquivo de outro aparelho que não abre: fica de fora com aviso, o resto segue.
  const estranho = DRIVE.novoArquivo('entrenos-cccccccccccccccc.sync', Buffer.from('estragado'), 'application/octet-stream', ['appDataFolder']);
  const r = await B.motor.sincronizar();
  assert.ok(r, JSON.stringify(B.motor.estado()));
  assert.ok(r.avisos.includes('Um arquivo de sincronização no Google Drive não abre com a sua senha.'), JSON.stringify(r.avisos));   // a frase do núcleo
  assert.equal(B.motor.estado().situacao, 'ok');
  DRIVE.arquivos.delete(estranho.id);
  ok('arquivo que não abre (mexido ou de outra senha): fica de fora com aviso, a sincronização termina');
}

{
  // O entrenos-sync.json sumiu do Drive (alguém apagou): o aparelho já ligado recria igual.
  const [cfg] = DRIVE.porNome('entrenos-sync.json');
  DRIVE.arquivos.delete(cfg.id);
  assert.ok(await A.motor.sincronizar());
  assert.equal(DRIVE.porNome('entrenos-sync.json').length, 1);
  assert.equal(DRIVE.porNome('entrenos-sync.json')[0].bytes.toString(), A.nucleo.syncJson);
  ok('o entrenos-sync.json que sumiu volta igual (do aparelho que já sincroniza)');
}

{
  // Dois aparelhos criando a configuração ao mesmo tempo: vale a mais antiga; a outra sai.
  const D2 = criarDriveFalso();
  const e2 = await D2.ligar();
  const tok = `tok-c-${randomBytes(8).toString('hex')}`;
  D2.tokens.add(tok);
  const antiga = D2.novoArquivo('entrenos-sync.json', Buffer.from('{"falso":"antiga"}'), 'application/json', ['appDataFolder']);
  const nucleo = nucleoFalso('cccccccccccccccc');
  const conta = contaFalsa(tok);
  let listagens = 0;
  // A primeira listagem não vê a configuração do outro (ela ainda não existia quando listou).
  const driveC = S.criarDrive({ buscar, token: () => conta.token(), base: e2.base, baseEnvio: e2.baseEnvio });
  const listar = driveC.listar;
  driveC.listar = async () => {
    listagens += 1;
    const todos = await listar();
    return listagens === 1 ? todos.filter((a) => a.id !== antiga.id) : todos;
  };
  const motor = S.criarSincronizador({ api: nucleo.api, drive: driveC, conta, relogio: criarRelogio() });
  assert.deepEqual(await motor.ligarComSenha('senha boa de verdade'), { criou: false });
  assert.deepEqual(D2.porNome('entrenos-sync.json').map((a) => a.id), [antiga.id]);
  assert.equal(nucleo.syncJson, '{"falso":"antiga"}');
  await D2.desligar();
  ok('corrida: dois aparelhos criando a configuração juntos -> fica a mais antiga, a nossa sai');
}

{
  // O login do Google (22.2): uma janela do Google que volta para o oauth.html; nada do Google na página.
  let abertas = [];
  let ouvinte = null;
  let tarefas = [];
  const revogados = [];
  let agora = 1_000_000;
  const conta = S.criarConta({
    abrirJanela: (url) => { abertas.push(url); return { close() {} }; },
    ouvirRetorno: (f) => { ouvinte = f; return () => { ouvinte = null; }; },
    retorno: () => 'https://entre-nos.exemplo/oauth.html',
    aleatorio: () => `estado-${abertas.length}`,
    buscar: async (url, op) => { revogados.push([url, String(op.body), op.mode, op.credentials]); return { ok: true }; },
    agora: () => agora,
    agendar: (f, ms) => { tarefas.push({ f, ms }); return tarefas.length; },
    cancelar: (id) => { if (tarefas[id - 1]) tarefas[id - 1].f = null; },
  });
  const devolver = (dados) => ouvinte && ouvinte({ tipo: 'entrenos-oauth', ...dados });
  assert.equal(conta.pronta(), false);
  await assert.rejects(conta.entrar(), (e) => e.tipo === 'sem_id');
  conta.definirId('123-abc.apps.googleusercontent.com');
  assert.equal(conta.pronta(), true);
  // Os parâmetros do pedido.
  let p = conta.entrar({ dica: 'pessoa@exemplo.com' });
  const url = new URL(abertas[0]);
  assert.equal(`${url.origin}${url.pathname}`, S.URL_AUTORIZAR);
  assert.deepEqual(Object.fromEntries(url.searchParams), {
    client_id: '123-abc.apps.googleusercontent.com', redirect_uri: 'https://entre-nos.exemplo/oauth.html',
    response_type: 'token', scope: S.ESCOPO, include_granted_scopes: 'false', state: 'estado-0', login_hint: 'pessoa@exemplo.com',
  });
  assert.equal(conta.conectando(), true);
  // Uma resposta com outro state (não é a deste pedido): ignorada.
  devolver({ state: 'outro', access_token: 'segredo-x', expires_in: '3599', scope: S.ESCOPO });
  assert.equal(conta.tokenValido(), false);
  // Recusou na janela do Google.
  devolver({ state: 'estado-0', error: 'access_denied' });
  await assert.rejects(p, (e) => e.tipo === 'permissao');
  assert.equal(conta.conectando(), false);
  // Escopo a mais (o Drive inteiro, já concedido ao mesmo ID): recusado e revogado.
  p = conta.entrar();
  devolver({ state: 'estado-1', access_token: 'segredo-1', expires_in: '3599', scope: `https://www.googleapis.com/auth/drive ${S.ESCOPO}` });
  await assert.rejects(p, (e) => e.tipo === 'permissao_demais' && e.message === S.MENSAGENS.permissao_demais);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(revogados.at(-1), [S.URL_REVOGAR, 'token=segredo-1', 'no-cors', 'omit']);
  assert.equal(conta.tokenValido(), false);
  // Sem a permissão do Drive.
  p = conta.entrar();
  devolver({ state: 'estado-2', access_token: 'segredo-2', expires_in: '3599', scope: 'openid' });
  await assert.rejects(p, (e) => e.tipo === 'permissao');
  // Janela bloqueada pelo navegador.
  const bloqueada = S.criarConta({ abrirJanela: () => null, ouvirRetorno: () => () => {}, retorno: () => 'https://entre-nos.exemplo/oauth.html' });
  bloqueada.definirId('123-abc.apps.googleusercontent.com');
  await assert.rejects(bloqueada.entrar(), (e) => e.tipo === 'popup_bloqueado' && e.message === S.MENSAGENS.popup_bloqueado);
  // Sem resposta (a janela foi fechada): depois do tempo, "fechada"; e "Cancelar".
  p = conta.entrar();
  tarefas.filter((t) => t.f).at(-1).f();
  await assert.rejects(p, (e) => e.tipo === 'popup_fechado');
  p = conta.entrar();
  conta.cancelar();
  await assert.rejects(p, (e) => e.tipo === 'cancelado');
  assert.equal(ouvinte, null, 'o ouvinte da volta ficou ligado');
  // Certo: o token fica só na memória e vence.
  p = conta.entrar();
  devolver({ state: `estado-${abertas.length - 1}`, access_token: 'segredo-3', expires_in: '3599', scope: S.ESCOPO });
  assert.equal(await p, true);
  assert.equal(conta.tokenValido(), true);
  assert.equal(conta.token(), 'segredo-3');
  agora += 3599 * 1000 - 30000;          // falta menos de 1 min: já pede outro
  assert.equal(conta.tokenValido(), false);
  agora -= 3599 * 1000 - 30000;
  conta.esquecer();                       // bloqueou: esquece (sem revogar)
  assert.equal(conta.token(), null);
  p = conta.entrar();
  devolver({ state: `estado-${abertas.length - 1}`, access_token: 'segredo-4', expires_in: '3599', scope: S.ESCOPO });
  await p;
  const antes = revogados.length;
  conta.revogar();                        // Desconectar/Parar: esquece e revoga
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(conta.token(), null);
  assert.equal(revogados.length, antes + 1);
  assert.equal(revogados.at(-1)[1], 'token=segredo-4');
  ok('login (janela do fluxo de token): parâmetros, state conferido, escopo a mais recusado e revogado, janela bloqueada, sem resposta e cancelada, o token vence, esquecer e revogar');
}

{
  // A frase sugerida para a senha de sincronização: 4 palavras diferentes + 2 algarismos, aceita pela
  // regra do núcleo (aqui, a cópia dela na página).
  assert.ok(S.PALAVRAS.length >= 500, `poucas palavras: ${S.PALAVRAS.length}`);
  assert.equal(new Set(S.PALAVRAS).size, S.PALAVRAS.length);
  assert.ok(S.PALAVRAS.every((x) => /^[a-z]{3,12}$/.test(x)));
  const frases = new Set();
  for (let i = 0; i < 200; i += 1) {
    const f = S.gerarFrase();
    assert.match(f, /^([a-z]+ ){4}\d{2}$/);
    assert.equal(new Set(f.split(' ').slice(0, 4)).size, 4);
    assert.equal(S.problemaDaSenha(f), null, f);
    frases.add(f);
  }
  assert.equal(frases.size, 200);
  for (const fraca of ['flores', 'camila123456789', 'amor amor amor amor']) assert.ok(S.problemaDaSenha(fraca), fraca);
  ok(`frase sugerida: ${S.PALAVRAS.length} palavras, 4 + 2 algarismos, sempre aceita; senhas fracas recusadas na página`);
}

{
  // base64 de arquivos grandes (bytes de 0 a 255) vai e volta igual.
  const bytes = new Uint8Array(randomBytes(300000));
  assert.deepEqual(S.b64ParaBytes(S.bytesParaB64(bytes)), bytes);
  ok('base64 de 300 kB vai e volta igual');
}

await DRIVE.desligar();

// ---------------------------------------------------------------- Parte B: três aparelhos com o núcleo de verdade (Pyodide)

if (process.env.SEM_PYODIDE === '1') {
  console.log('\n(parte B pulada: SEM_PYODIDE=1)');
} else {
  const SITE = path.join(TEMPO, 'site');
  const r = spawnSync(PYTHON, ['construir_web.py', '--destino', SITE], {
    cwd: RAIZ, encoding: 'utf-8', timeout: 300000, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } });
  if (r.status !== 0) throw new Error(`construir_web.py falhou: ${r.stderr}`);
  const { prepararPython } = await import(pathToFileURL(path.join(SITE, 'entrenos_pyodide.mjs')).href);
  const { loadPyodide } = await import(pathToFileURL(path.join(SITE, 'pyodide', '314.0.7', 'pyodide.mjs')).href);
  const indexURL = path.join(SITE, 'pyodide', '314.0.7').split(path.sep).join('/') + '/';
  const t0 = Date.now();
  const app = await prepararPython({ loadPyodide, indexURL, pacoteApp: fs.readFileSync(path.join(SITE, 'entrenos-py.zip')) });
  const carga = Date.now() - t0;
  const py = app.pyodide;
  const pedirPy = py.runPython(`
import json
from entrenos import cofre, sincronizacao
from entrenos.nucleo import Nucleo
cofre.SCRYPT_N = 2 ** 12
sincronizacao.SCRYPT_N = 2 ** 12
_nucleos = {}
def _criar(nome):
    _nucleos[nome] = Nucleo("/" + nome, atraso_pin=0, plataforma="navegador", minutos_bloqueio=10 ** 6)
def _pedir(nome, metodo, caminho, corpo, token):
    if nome not in _nucleos:
        _criar(nome)
    caminho, _, q = caminho.partition("?")
    consulta = dict(x.split("=", 1) for x in q.split("&") if x) if q else None
    r = _nucleos[nome].tratar(metodo, caminho, corpo=None if corpo is None else corpo.encode("utf-8"),
                              token=token or None, consulta=consulta)
    return json.dumps({"status": r.status, "dados": r.dados}, ensure_ascii=False)
_pedir
`);
  const D3 = criarDriveFalso();
  const e3 = await D3.ligar();

  function aparelho(nome) {
    let token = null;
    const api = async (metodo, rota, corpo) => {
      // undefined (e não null) vira None no Python: no Pyodide 314 o null do JavaScript é o "jsnull".
      const texto = ['POST', 'PATCH', 'PUT', 'DELETE'].includes(metodo) ? JSON.stringify(corpo === undefined ? {} : corpo) : undefined;
      const resposta = JSON.parse(pedirPy(nome, metodo, `/api${rota}`, texto, token || undefined));
      if (resposta.status >= 200 && resposta.status < 300) return resposta.dados;
      const e = new Error((resposta.dados && resposta.dados.erro) || `HTTP ${resposta.status}`);
      e.status = resposta.status;
      e.dados = resposta.dados;
      throw e;
    };
    const tok = `tok-${nome}-${randomBytes(8).toString('hex')}`;
    D3.tokens.add(tok);
    const conta = contaFalsa(tok);
    const drive = S.criarDrive({ buscar: fetch, token: () => conta.token(), base: e3.base, baseEnvio: e3.baseEnvio });
    const motor = S.criarSincronizador({ api, drive, conta, relogio: criarRelogio() });
    return {
      nome, api, motor, tok,
      async configurar() {
        token = (await api('POST', '/configurar', { pin: 'girassol na janela azul' })).token;
      },
      async registrar(texto) {
        const p = await api('POST', '/analisar', { texto, data_registro: '2026-10-05' });
        await api('POST', '/registros', { texto, data_registro: '2026-10-05', itens: p.itens, pessoas: p.pessoas,
          relacoes: [], extras: p.extras || {} });
      },
      async textos() {
        return (await api('GET', '/registros?limite=1000')).map((x) => x.texto).sort();
      },
    };
  }

  const [pa, pb, pc] = ['celular', 'computador', 'tablet'].map(aparelho);
  for (const x of [pa, pb, pc]) await x.configurar();
  const SENHA = 'biscoito de polvilho';
  assert.deepEqual(await pa.motor.ligarComSenha(SENHA), { criou: true });
  await assert.rejects(pb.motor.ligarComSenha('outra senha qualquer'), (e) => e.status === 401 && /senha de sincronização/.test(e.message));
  assert.equal((await pb.api('GET', '/sync/estado')).configurada, false);
  assert.deepEqual(await pb.motor.ligarComSenha(SENHA), { criou: false });
  assert.deepEqual(await pc.motor.ligarComSenha(SENHA), { criou: false });
  ok(`Pyodide (${carga} ms para carregar): senha criada no celular; errada no computador dá 401 e não liga; certa liga os outros dois`);

  const TEXTOS = {
    celular: 'Ela adora tulipas amarelas.',
    computador: 'A mãe dela, Sandra, vai fazer uma cirurgia em novembro.',
    tablet: 'Ela ficou menstruada ontem e está com cólica.',
  };
  for (const x of [pa, pb, pc]) await x.registrar(TEXTOS[x.nome]);
  const tempos = [];
  for (let volta = 0; volta < 2; volta += 1) {
    for (const x of [pa, pb, pc]) {
      const t = Date.now();
      const resumo = await x.motor.sincronizar();
      tempos.push(Date.now() - t);
      assert.ok(resumo, `${x.nome}: ${JSON.stringify(x.motor.estado())}`);
    }
  }
  const todos = Object.values(TEXTOS).sort();
  for (const x of [pa, pb, pc]) assert.deepEqual(await x.textos(), todos, x.nome);
  // O registro do ciclo chegou 🔒 nos outros, com os dados do ciclo.
  const ciclo = (await pa.api('GET', '/registros')).find((x) => x.texto === TEXTOS.tablet);
  assert.equal(ciclo.privado, true);
  assert.equal((await pa.api('GET', '/ciclo')).tem_dados, true);
  ok(`três aparelhos: os registros de cada um chegaram a todos (🔒 e ciclo junto); sincronizar levou ${Math.min(...tempos)}-${Math.max(...tempos)} ms`);

  // Apagar no computador: some nos outros e não volta (o tablet sincroniza depois do celular).
  const alvo = (await pb.api('GET', '/registros')).find((x) => x.texto === TEXTOS.celular);
  await pb.api('DELETE', `/registros/${alvo.id}`);
  await pb.motor.sincronizar();
  await pa.motor.sincronizar();
  await pc.motor.sincronizar();
  for (let volta = 0; volta < 2; volta += 1) for (const x of [pa, pb, pc]) await x.motor.sincronizar();
  const restantes = todos.filter((t) => t !== TEXTOS.celular);
  for (const x of [pa, pb, pc]) assert.deepEqual(await x.textos(), restantes, x.nome);
  ok('apagar num aparelho: some nos outros e não volta');

  // O que o Google guarda: o entrenos-sync.json (sem segredo) e um arquivo embaralhado por aparelho.
  const nomes = [...D3.arquivos.values()].map((a) => a.name).sort();
  assert.equal(nomes.length, 4);
  assert.ok(nomes.includes('entrenos-sync.json'));
  assert.equal(nomes.filter((n) => /^entrenos-[0-9a-f]{16}\.sync$/.test(n)).length, 3);
  for (const a of D3.arquivos.values()) {
    const texto = a.bytes.toString('latin1');
    for (const proibido of ['tulipas', 'Sandra', 'menstruada', 'cólica', SENHA, 'girassol', 'celular', 'computador']) {
      assert.ok(!texto.includes(proibido) && !a.bytes.toString('utf-8').includes(proibido), `"${proibido}" em claro no Drive (${a.name})`);
    }
  }
  const config = JSON.parse(D3.porNome('entrenos-sync.json')[0].bytes.toString('utf-8'));
  assert.deepEqual(Object.keys(config).sort(), ['app', 'formato', 'kdf', 'tipo', 'verificador']);
  ok('no Drive: só o entrenos-sync.json (sal, parâmetros, verificador) e um arquivo embaralhado por aparelho');

  // Cada aparelho atualizou só o seu arquivo.
  for (const x of [pa, pb, pc]) {
    const meu = (await x.api('GET', '/sync/estado')).arquivo;
    const idMeu = D3.porNome(meu)[0].id;
    const atualizados = D3.pedidos.filter((p) => p.metodo === 'PATCH' && p.token === x.tok).map((p) => decodeURIComponent(p.caminho.split('/').pop()));
    assert.ok(atualizados.length > 0 && atualizados.every((id) => id === idMeu), `${x.nome} atualizou o arquivo de outro`);
  }
  assert.equal(D3.pedidos.filter((p) => p.metodo === 'DELETE' && !p.caminho.includes('/revisions/')).length, 0, 'apagou um arquivo');
  ok('cada aparelho atualizou só o próprio arquivo; ninguém apagou nada no Drive');
  await D3.desligar();
}

// ---------------------------------------------------------------- Parte C: a tela (Ajustes → ☁️ Sincronização) num DOM de mentira

{
  // Um DOM mínimo: o que o h() da página e a seção usam (sem innerHTML: a seção nem teria como usar).
  let raiz = null;
  class No {
    constructor() { this.filhos = []; this.pai = null; }
    appendChild(n) { if (n.pai) n.pai.removeChild(n); n.pai = this; this.filhos.push(n); return n; }
    removeChild(n) { this.filhos = this.filhos.filter((f) => f !== n); n.pai = null; }
    replaceChildren(...ns) { for (const f of this.filhos) f.pai = null; this.filhos = []; ns.forEach((n) => this.appendChild(n)); }
    remove() { if (this.pai) this.pai.removeChild(this); }
    get isConnected() { let p = this; while (p.pai) p = p.pai; return p === raiz; }
    get textContent() { return this.filhos.map((f) => f.textContent).join(''); }
    set textContent(t) { this.replaceChildren(new Texto(String(t))); }
    *todos() { for (const f of this.filhos) { yield f; yield* f.todos(); } }
  }
  class Texto extends No {
    constructor(t) { super(); this.dado = t; }
    get textContent() { return this.dado; }
  }
  class Elemento extends No {
    constructor(tag) { super(); this.tagName = tag.toUpperCase(); this.atributos = {}; this.ouvintes = {}; this.value = ''; this.checked = false; }
    setAttribute(k, v) {
      this.atributos[k] = String(v);
      if (k === 'id') this.id = String(v);
      if (k === 'disabled') this.disabled = true;
      if (k === 'type') this.type = String(v);
    }
    getAttribute(k) { return k in this.atributos ? this.atributos[k] : null; }
    set className(v) { this.atributos.class = v; }
    addEventListener(t, f) { (this.ouvintes[t] ||= []).push(f); }
    async disparar(t, extras = {}) { for (const f of this.ouvintes[t] || []) await f({ preventDefault() {}, target: this, ...extras }); }
    focus() { globalThis.__focado = this; }
    reset() { for (const n of this.todos()) if (n instanceof Elemento && n.tagName === 'INPUT') n.value = ''; }
    querySelector(sel) {
      for (const n of this.todos()) {
        if (!(n instanceof Elemento)) continue;
        if (sel.startsWith('input:not') ? (n.tagName === 'INPUT' && n.type !== 'checkbox') || n.tagName === 'BUTTON' : n.tagName === sel.toUpperCase()) return n;
      }
      return null;
    }
  }
  const documento = {
    createElement: (tag) => new Elemento(tag),
    createTextNode: (t) => new Texto(t),
    head: new Elemento('head'),
    addEventListener() {},
    visibilityState: 'visible',
  };
  raiz = new Elemento('html');
  // O h() da página (web/app.js), igual: texto vira nó de texto, nunca HTML.
  function h(tag, props, ...filhos) {
    const el = documento.createElement(tag);
    const depois = [];
    for (const [k, v] of Object.entries(props || {})) {
      if (k.startsWith('aria-') || k.startsWith('data-')) { if (v != null) el.setAttribute(k, String(v)); continue; }
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
      el.appendChild(f instanceof No ? f : new Texto(String(f)));
    }
  }
  const avisos = [];
  const util = {
    esperando: () => false, segundosDeEspera: (e) => Number(e && e.dados && e.dados.espere_segundos) || 0, contarEspera() {},
    isoDe: (d) => d.toISOString().slice(0, 10), linkRegistro: (id, t) => h('a', { href: `#registros/${id}` }, t),
    anexar: (el, ...f) => anexar(el, f), limpar: (el) => el.replaceChildren(), encurtar: (s) => String(s),
    capitalizar: (s) => (s ? s[0].toUpperCase() + s.slice(1) : s), dataBr: (iso) => String(iso).split('-').reverse().join('/'),
    carregando: (t = 'Carregando…') => h('p', { class: 'carregando', role: 'status' }, t),
    campo: (rotulo, controle, dica) => h('div', { class: 'campo' }, h('label', { for: controle.id }, rotulo), dica ? h('p', { class: 'dica' }, dica) : null, controle),
    avisar: (t) => avisos.push(t), avisarErro: (e) => avisos.push(e.message), falar() {}, confirmar: async () => true,
  };

  // O núcleo de mentira da tela (como o da parte A) e o Drive de mentira no lugar do Google.
  const DT = criarDriveFalso();
  const et = await DT.ligar();
  const nucleo = nucleoFalso('dddddddddddddddd');
  nucleo.idApp = null;
  nucleo.conflitos = [];
  const apiTela = async (metodo, rota, corpo) => {
    if (rota === '/sync/estado') return { configurada: nucleo.configurada, sync_json: nucleo.syncJson, automatica: true,
      id_app: nucleo.idApp, nome: 'Celular', conta: nucleo.conta || null, ultima: null,
      arquivo: nucleo.configurada ? 'entrenos-dddddddddddddddd.sync' : null, religavel: !nucleo.configurada && Boolean(nucleo.syncJson),
      aparelhos: nucleo.configurada ? [{ aparelho: 'eeeeeeeeeeeeeeee', nome: 'Computador', gerado_em: new Date().toISOString() }] : [],
      conflitos: nucleo.conflitos.length };
    if (rota === '/sync/ajustes') {
      if ('id_app' in corpo) nucleo.idApp = corpo.id_app;
      if ('conta' in corpo) nucleo.conta = corpo.conta;
      return apiTela('GET', '/sync/estado');
    }
    if (rota === '/sync/conflitos') return nucleo.conflitos;
    if (rota.startsWith('/sync/conflitos/')) { nucleo.conflitos = []; return {}; }
    return nucleo.api(metodo, rota, corpo);
  };
  let render = null;
  const ouvintesJanela = {};
  const ouvintesGlobais = {};
  const janelasAbertas = [];
  const revogacoes = [];
  const contexto = {
    console, URL, URLSearchParams, Blob, TextEncoder, TextDecoder, atob, btoa, setTimeout, clearTimeout,
    setInterval, clearInterval, Promise, crypto: globalThis.crypto,
    document: documento,
    location: { origin: 'https://entre-nos.exemplo', hostname: 'entre-nos.exemplo', port: '', protocol: 'https:',
      href: 'https://entre-nos.exemplo/#ajustes' },
    navigator: { userAgent: 'Mozilla/5.0 (Linux; Android 14)', platform: 'Linux', maxTouchPoints: 5, onLine: true,
      clipboard: { writeText: async () => {} } },
    matchMedia: () => ({ matches: false }),
    CustomEvent: class { constructor(t) { this.type = t; } },
    // A janela do Google (o fluxo de token) e a volta do oauth.html (postMessage da mesma origem).
    open: (url) => { janelasAbertas.push(new URL(url)); return { close() {} }; },
    addEventListener: (t, f) => { (ouvintesGlobais[t] ||= []).push(f); },
    removeEventListener: (t, f) => { ouvintesGlobais[t] = (ouvintesGlobais[t] || []).filter((x) => x !== f); },
    fetch: (url, op) => {
      if (String(url).startsWith('https://oauth2.googleapis.com/revoke')) {
        revogacoes.push(String(op.body));
        return Promise.resolve({ ok: true });
      }
      return fetch(String(url).replace('https://www.googleapis.com', `http://127.0.0.1:${et.port}`), op);
    },
  };
  const voltaDoGoogle = (dados, origem = 'https://entre-nos.exemplo') => {
    for (const f of [...(ouvintesGlobais.message || [])]) f({ origin: origem, data: { tipo: 'entrenos-oauth', ...dados } });
  };
  contexto.window = {
    addEventListener: (t, f) => { (ouvintesJanela[t] ||= []).push(f); },
    EntreNos: { h, api: apiTela, util, registrarSecaoAjustes: (id, titulo, r) => { assert.equal(id, 'sincronizacao'); assert.equal(titulo, '☁️ Sincronização'); render = r; } },
  };
  contexto.globalThis = contexto;
  vm.createContext(contexto);
  vm.runInContext(fs.readFileSync(path.join(RAIZ, 'web', 'sincronizar.js'), 'utf-8'), contexto, { filename: 'sincronizar.js' });
  assert.equal(typeof render, 'function', 'a seção não se registrou em Ajustes');
  for (const evento of ['entrenos:gravou', 'entrenos:desbloqueado', 'entrenos:bloqueado', 'online']) {
    assert.ok(ouvintesJanela[evento], `sem ouvinte para ${evento}`);
  }

  const corpo = h('div');
  raiz.appendChild(corpo);
  const esperar = () => new Promise((resolve) => setTimeout(resolve, 60));
  const texto = () => corpo.textContent;
  const botoes = () => [...corpo.todos()].filter((n) => n.tagName === 'BUTTON');
  const botao = (rotulo) => botoes().find((b) => b.textContent.includes(rotulo));
  function conferirTela(momento) {
    const t = texto();
    for (const ruim of ['undefined', 'null', 'NaN', '[object']) assert.ok(!t.includes(ruim), `"${ruim}" na tela (${momento})`);
    const ids = new Set([...corpo.todos()].filter((n) => n.tagName === 'LABEL').map((n) => n.getAttribute('for')));
    for (const n of corpo.todos()) {
      if (n instanceof Elemento && ['INPUT', 'SELECT', 'TEXTAREA'].includes(n.tagName)) {
        const dentro = (() => { let p = n.pai; while (p) { if (p.tagName === 'LABEL') return true; p = p.pai; } return false; })();
        assert.ok(dentro || (n.id && ids.has(n.id)), `campo sem rótulo (${momento}): ${n.tagName} ${n.id || ''}`);
      }
    }
    assert.ok(botoes().every((b) => b.getAttribute('type')), `botão sem type (${momento})`);
  }

  render(corpo, { config: {}, salvarConfig() {}, atual: () => true });
  await esperar();
  conferirTela('sem ID do app');
  assert.ok(texto().includes('Passo a passo'));
  assert.ok(texto().includes('https://entre-nos.exemplo') && texto().includes('http://localhost:8787'), 'o passo a passo não mostra as origens');
  assert.ok(texto().includes('https://entre-nos.exemplo/oauth.html') && texto().includes('http://localhost:8787/oauth.html'),
    'o passo a passo não mostra os URIs de redirecionamento');
  assert.ok(!texto().includes('127.0.0.1'), 'o passo a passo manda cadastrar 127.0.0.1');
  assert.ok(texto().includes('Dropbox (em breve)'));
  assert.equal(janelasAbertas.length, 0, 'abriu o login do Google antes de ligar');
  const campoId = [...corpo.todos()].find((n) => n.id === 'sync-id-app');
  campoId.value = 'isto não é um id';
  const formId = campoId.pai.pai;
  await formId.disparar('submit');
  assert.ok(texto().includes('termina em .apps.googleusercontent.com'));
  campoId.value = '123456789012-abcdef.apps.googleusercontent.com';
  await formId.disparar('submit');
  await esperar();
  conferirTela('com ID do app, sem conectar');
  assert.equal(nucleo.idApp, '123456789012-abcdef.apps.googleusercontent.com');
  const conectar = botao('Conectar ao Google');
  assert.ok(conectar && !conectar.disabled);
  // Toque em Conectar: a janela do Google abre (no toque) e o botão diz "Conectando…".
  DT.tokens.add('tok-tela-123');
  const clique = conectar.disparar('click');
  assert.equal(janelasAbertas.length, 1);
  const pedido = janelasAbertas[0];
  assert.equal(pedido.searchParams.get('client_id'), nucleo.idApp);
  assert.equal(pedido.searchParams.get('scope'), S.ESCOPO);
  assert.equal(pedido.searchParams.get('redirect_uri'), 'https://entre-nos.exemplo/oauth.html');
  assert.equal(conectar.textContent, 'Conectando ao Google…');
  assert.ok(conectar.disabled);
  // Uma "volta" de outra origem, ou com outro state, é ignorada; a certa entra.
  voltaDoGoogle({ state: pedido.searchParams.get('state'), access_token: 'tok-falso', expires_in: '3599', scope: S.ESCOPO }, 'https://outro.exemplo');
  voltaDoGoogle({ state: 'adivinhado', access_token: 'tok-falso', expires_in: '3599', scope: S.ESCOPO });
  voltaDoGoogle({ state: pedido.searchParams.get('state'), access_token: 'tok-tela-123', expires_in: '3599', scope: S.ESCOPO });
  await clique;
  await esperar();
  conferirTela('conectado, sem senha');
  assert.equal(nucleo.conta, 'pessoa@exemplo.com');
  assert.ok(texto().includes('Conectado como pessoa@exemplo.com'));
  assert.ok(texto().includes('Crie a senha de sincronização'));
  // Duas contas do Google: a tela diz em qual conta procurou e pede para confirmar o primeiro aparelho.
  assert.ok(texto().includes('Não encontrei a sincronização no Google Drive de pessoa@exemplo.com'));
  assert.ok(texto().includes('Sugestão (quatro palavras ao acaso e um número)'));
  const [senha, repetir, primeiro] = ['sync-senha', 'sync-senha-repetir', 'sync-primeiro'].map((id) => [...corpo.todos()].find((n) => n.id === id));
  assert.equal(senha.getAttribute('autocomplete'), 'one-time-code');
  const form = senha.pai.pai;
  senha.value = 'flores';
  repetir.value = 'flores';
  await form.disparar('submit');
  assert.ok(texto().includes('14 caracteres'), 'a página aceitou uma senha fraca');
  senha.value = 'senha boa de verdade';
  repetir.value = 'outra';
  await form.disparar('submit');
  assert.ok(texto().includes('não são iguais'));
  repetir.value = 'senha boa de verdade';
  await form.disparar('submit');
  assert.ok(texto().includes('Confirme que este é o primeiro aparelho'));
  primeiro.checked = true;
  senha.value = 'senha boa de verdade';
  repetir.value = 'senha boa de verdade';
  await form.disparar('submit');
  await esperar();
  await esperar();
  conferirTela('ligada');
  assert.equal(nucleo.configurada, true);
  assert.equal(DT.porNome('entrenos-sync.json').length, 1);
  assert.ok(botao('Sincronizar agora') && botao('Desconectar do Google') && botao('Parar de sincronizar'));
  assert.ok(texto().includes('Computador') && texto().includes('Celular'));
  // Uma pergunta (conflito): os dois lados, com nomes de campo e valores legíveis.
  nucleo.conflitos = [{ id: 7, tipo: 'item', aparelho: 'eeeeeeeeeeeeeeee', nome_aparelho: 'Computador', resumo: 'Gosta de tulipas',
    contexto: { registro_id: 3, data_registro: '2026-10-05' },
    campos: [{ campo: 'status', aqui: 'resolvido', la: 'ativo' }, { campo: 'categorias', aqui: ['Flores'], la: [] }] }];
  await botao('Sincronizar agora').disparar('click');
  await esperar();
  conferirTela('com pergunta');
  assert.ok(texto().includes('1 pergunta'));
  assert.ok(texto().includes('Aqui: resolvido') && texto().includes('Em Computador: ativo') && texto().includes('(vazio)'));
  assert.ok(texto().includes('Situação') && texto().includes('Categorias'));
  assert.ok(botao('Fica a versão deste aparelho').getAttribute('aria-describedby'), 'o botão não diz de qual pergunta é');
  await botao('Fica a versão de: Computador').disparar('click');
  await esperar();
  assert.equal(nucleo.conflitos.length, 0);
  conferirTela('pergunta respondida');
  // O token não aparece em lugar nenhum da tela.
  assert.ok(!texto().includes('tok-tela-123'));
  // Parar: com o Google conectado, apaga o arquivo deste aparelho no Drive, revoga o acesso e não
  // insiste em pedir a senha de novo.
  assert.ok(DT.porNome('entrenos-dddddddddddddddd.sync').length === 1);
  nucleo.api = ((original) => async (metodo, rota, corpo) => {
    if (rota === '/sync/desligar') {
      nucleo.configurada = false;
      return { configurada: false, religavel: true, id_app: nucleo.idApp, nome: 'Celular', aparelhos: [] };
    }
    return original(metodo, rota, corpo);
  })(nucleo.api);
  await botao('Parar de sincronizar').disparar('click');
  await esperar();
  await esperar();
  conferirTela('parada');
  assert.equal(DT.porNome('entrenos-dddddddddddddddd.sync').length, 0, 'o arquivo deste aparelho ficou no Drive');
  assert.deepEqual(revogacoes, ['token=tok-tela-123']);
  assert.ok(!texto().includes('Digite a senha de sincronização'), 'depois de parar, a tela já pede a senha');
  assert.ok(botao('Conectar ao Google'));
  await DT.desligar();
  ok('tela (DOM de mentira): ID do app -> janela do Google só no toque -> volta conferida (origem e state) -> senha forte, primeiro aparelho -> ligada; pergunta com os dois lados; parar apaga o arquivo e revoga; todo campo com rótulo, nada de "undefined", o token nunca na tela');
}

{
  // Nada de HTML montado nem estilo inline na seção (CSP e o jeito do projeto).
  // O código sem os comentários (os comentários podem dizer "nunca em localStorage").
  const fonte = fs.readFileSync(path.join(RAIZ, 'web', 'sincronizar.js'), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '').split('\n').filter((l) => !/^\s*\/\//.test(l)).join('\n');
  for (const proibido of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'style=', '.style.', 'eval(', 'new Function',
    'localStorage', 'sessionStorage', 'indexedDB', 'accounts.google.com/gsi', 'createElement(\'script\')']) {
    assert.ok(!fonte.includes(proibido), `sincronizar.js usa ${proibido}`);
  }
  // O oauth.html: estático, sem o app, sem script inline; o oauth.js só fala com a mesma origem.
  const html = fs.readFileSync(path.join(RAIZ, 'web', 'oauth.html'), 'utf-8');
  assert.ok(!/<script>/.test(html) && /<script src="oauth\.js" defer><\/script>/.test(html));
  assert.ok(!/app\.js|sincronizar\.js/.test(html), 'o oauth.html carrega o app');
  const volta = fs.readFileSync(path.join(RAIZ, 'web', 'oauth.js'), 'utf-8');
  assert.ok(volta.includes('postMessage(dados, location.origin)') && !volta.includes("postMessage(dados, '*')"));
  for (const proibido of ['localStorage', 'sessionStorage', 'indexedDB', 'console.', 'fetch(', 'innerHTML']) {
    assert.ok(!volta.includes(proibido), `oauth.js usa ${proibido}`);
  }
  // O oauth.js rodando: entrega à página que abriu (só para a mesma origem) e pelo canal, tira o
  // fragmento do histórico e fecha a janela.
  const mensagens = [];
  const canal = [];
  let historico = null;
  let fechou = false;
  const paragrafo = { textContent: '' };
  const ctx = {
    URLSearchParams, setTimeout: (f) => f(),
    location: { hash: '#access_token=tok-volta&token_type=Bearer&expires_in=3599&scope=x&state=abc', search: '',
      pathname: '/oauth.html', origin: 'https://entre-nos.exemplo' },
    history: { replaceState: (_a, _b, url) => { historico = url; } },
    document: { getElementById: (id) => (id === 'oauth-mensagem' ? paragrafo : null) },
    BroadcastChannel: class { postMessage(d) { canal.push({ ...d }); } close() {} },
  };
  ctx.window = { opener: { postMessage: (d, alvo) => mensagens.push([{ ...d }, alvo]) }, close: () => { fechou = true; } };
  ctx.window.opener.closed = false;
  vm.createContext(ctx);
  vm.runInContext(volta, ctx, { filename: 'oauth.js' });
  assert.equal(mensagens.length, 1);
  assert.deepEqual(mensagens[0][1], 'https://entre-nos.exemplo');
  assert.equal(mensagens[0][0].access_token, 'tok-volta');
  assert.equal(mensagens[0][0].state, 'abc');
  assert.equal(canal.length, 1);
  assert.equal(historico, '/oauth.html');
  assert.ok(fechou);
  assert.ok(!paragrafo.textContent.includes('tok-volta'));
  assert.ok(!/\bconsole\.(?:log|info|warn|error|debug|trace)\b/.test(fonte), 'sincronizar.js escreve no console');
  ok('sincronizar.js: sem innerHTML, sem estilo inline, sem eval, sem guardar o token no navegador, sem console; oauth.html estático e o oauth.js só entrega à mesma origem');
}

// ---------------------------------------------------------------- fim

const tudo = ditos.join('\n');
for (const proibido of ['tok-a-', 'tok-b-', 'tok-c-', 'segredo-1', 'segredo-2', 'segredo-3', 'segredo-4', 'tok-tela-123', 'Bearer']) {
  assert.ok(!tudo.includes(proibido), `o console mostrou "${proibido}"`);
}
fs.rmSync(TEMPO, { recursive: true, force: true });
console.log(`\nTUDO OK (${passos} passos)`);
// O motor da tela (parte C) fica tentando de novo de minuto em minuto, como numa página aberta sem o
// Drive: aqui o teste acabou.
process.exit(0);
