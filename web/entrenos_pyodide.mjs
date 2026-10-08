/* Entre Nós no navegador: o Python (Pyodide) e o diário, sem saber de Worker nem de mensagens
 * (DESIGN.md, seções 19.5 e 21.1).
 *
 * O trabalhador.mjs (o transporte, dentro do Web Worker) usa isto; o teste do Node
 * (testes/navegador/teste_pyodide.mjs) usa o mesmo código com o Pyodide de verdade, trocando só o
 * sistema de arquivos (no Node não há IndexedDB: MEMFS no lugar do IDBFS).
 *
 * Texto entra, texto sai: cada pedido vai para entrenos.ponte.tratar_json e volta em JSON, sem
 * objetos Python atravessando. Quando a resposta diz que algum arquivo da pasta de dados mudou
 * (`gravou`), FS.syncfs(false) leva o IDBFS para o armazenamento do navegador ANTES de a resposta
 * sair; os syncfs nunca se cruzam (uma fila só). Nada daqui registra conteúdo, PIN ou token.
 */

export const VERSAO_PYODIDE = '314.0.7';
export const PASTA_DADOS = '/dados';
export const PASTA_APP = '/app';
export const PACOTES = ['cryptography'];

// Tarefas uma de cada vez, na ordem de chegada; a falha de uma não trava as seguintes.
export function criarFila() {
  let ultima = Promise.resolve();
  return function enfileirar(tarefa) {
    const vez = ultima.then(() => tarefa());
    ultima = vez.catch(() => {});
    return vez;
  };
}

// O progresso da abertura, em fração de 0 a 1, por fase (o download é a maior parte da primeira vez).
export const PROGRESSO = Object.freeze({
  inicio: 0.03,
  python: 0.55,          // pyodide.asm.wasm + biblioteca padrão carregados
  criptografia: 0.75,    // cryptography e dependências
  app: 0.82,             // entrenos-py.zip aberto em /app
  autoteste: 0.92,
  dados: 0.97,           // IDBFS montado e lido
  pronto: 1,
});

/* Carrega o Pyodide, o cryptography e o código do Entre Nós. `pacoteApp`: o entrenos-py.zip
 * (ArrayBuffer ou Uint8Array). `aoAndar(fracao, fase)`. Devolve um AppPython. */
export async function prepararPython({ loadPyodide, indexURL, pacoteApp, aoAndar = () => {}, opcoesPyodide = {} }) {
  const medidas = {};
  const t0 = Date.now();
  aoAndar(PROGRESSO.inicio, 'inicio');
  const pyodide = await loadPyodide({
    indexURL,
    fullStdLib: false,
    // Nada do que o Python escrever vai para o console (o app não escreve conteúdo, mas a regra é
    // não arriscar; os erros ficam no erros.log, só com o tipo). O teste do Node troca para depurar.
    stdout: () => {},
    stderr: () => {},
    ...opcoesPyodide,
  });
  medidas.python_ms = Date.now() - t0;
  aoAndar(PROGRESSO.python, 'python');
  const t1 = Date.now();
  await pyodide.loadPackage(PACOTES, { messageCallback: () => {}, errorCallback: () => {} });
  // O loadPackage não levanta quando uma roda não chega (só avisa): sem ela, é falha de carga.
  if (!pyodide.runPython('import importlib.util\nimportlib.util.find_spec("cryptography") is not None')) {
    throw new Error('cryptography não carregou');
  }
  medidas.criptografia_ms = Date.now() - t1;
  aoAndar(PROGRESSO.criptografia, 'criptografia');
  pyodide.unpackArchive(comoBytes(pacoteApp), 'zip', { extractDir: PASTA_APP });
  pyodide.runPython(`import sys\nif ${JSON.stringify(PASTA_APP)} not in sys.path:\n    sys.path.insert(0, ${JSON.stringify(PASTA_APP)})`);
  const ponte = pyodide.pyimport('entrenos.ponte');
  aoAndar(PROGRESSO.app, 'app');
  medidas.carga_ms = Date.now() - t0;
  return new AppPython(pyodide, ponte, medidas);
}

// Bytes como um Uint8Array comum (o Pyodide recusa subclasses, como o Buffer do Node).
export function comoBytes(valor) {
  if (valor instanceof ArrayBuffer) return new Uint8Array(valor);
  if (ArrayBuffer.isView(valor)) return new Uint8Array(valor.buffer, valor.byteOffset, valor.byteLength);
  return new Uint8Array(0);
}

function lerJson(texto) {
  return JSON.parse(String(texto));
}

export class AppPython {
  constructor(pyodide, ponte, medidas = {}) {
    this.pyodide = pyodide;
    this.ponte = ponte;
    this.medidas = medidas;
    this.montado = false;
    this.iniciado = false;
    this.encerrado = false;
    this._filaSync = criarFila();
  }

  get FS() {
    return this.pyodide.FS;
  }

  // {ok, falhas, python, sqlite}: FTS5, serialize, scrypt e AES-GCM com valores conhecidos,
  // o formato .cofre e os módulos (entrenos.ponte.autoteste). Antes de abrir qualquer diário.
  autoteste() {
    const t = Date.now();
    const r = lerJson(this.ponte.autoteste());
    this.medidas.autoteste_ms = Date.now() - t;
    return r;
  }

  /* Monta a pasta de dados. tipo: 'idbfs' (navegador: o armazenamento do site), 'memfs' (testes:
   * só na memória) ou 'nodefs' (testes no Node: uma pasta de verdade, em `raiz`). Depois do mount,
   * syncfs(true) traz o que já estava guardado, antes de qualquer pedido. */
  async montarDados(tipo = 'idbfs', raiz = null) {
    const FS = this.FS;
    FS.mkdirTree(PASTA_DADOS);
    if (tipo === 'idbfs') FS.mount(FS.filesystems.IDBFS, {}, PASTA_DADOS);
    else if (tipo === 'nodefs') FS.mount(FS.filesystems.NODEFS, { root: raiz }, PASTA_DADOS);
    else if (tipo !== 'memfs') throw new Error(`sistema de arquivos desconhecido: ${tipo}`);
    this.montado = true;
    await this.sincronizar(true);
  }

  // FS.syncfs em fila: true = do armazenamento para a memória; false = da memória para o armazenamento.
  sincronizar(trazer = false) {
    return this._filaSync(() => new Promise((resolve, reject) => {
      this.FS.syncfs(Boolean(trazer), (erro) => (erro ? reject(erro) : resolve()));
    }));
  }

  // Cria (ou recria, depois de /api/sair) o núcleo do navegador em /dados. opcoes: as do Nucleo que
  // fazem sentido vindas do JavaScript (atraso_pin, atraso_pin_maximo, minutos_bloqueio).
  iniciar(opcoes = {}) {
    const permitidas = {};
    for (const nome of ['atraso_pin', 'atraso_pin_maximo', 'minutos_bloqueio']) {
      if (typeof opcoes[nome] === 'number' && Number.isFinite(opcoes[nome])) permitidas[nome] = opcoes[nome];
    }
    const nucleo = this.ponte.iniciar_no_navegador.callKwargs(PASTA_DADOS, permitidas);
    if (nucleo && typeof nucleo.destroy === 'function') nucleo.destroy();
    this._opcoes = permitidas;
    this.iniciado = true;
    this.encerrado = false;
  }

  async _depois(resposta) {
    if (resposta.gravou) await this.sincronizar(false);
    if (resposta.encerrado) this.encerrado = true;
    return resposta;
  }

  /* Um pedido da página: {metodo, caminho, corpo (texto JSON), token, consulta (objeto de textos)}.
   * Devolve {status, cabecalhos, gravou, dados[, encerrado]} já com o syncfs feito. */
  async tratar({ metodo, caminho, corpo, token, consulta } = {}) {
    if (this.encerrado) this.iniciar(this._opcoes || {});
    const texto = this.ponte.tratar_json(
      String(metodo || 'GET'), String(caminho || ''),
      typeof corpo === 'string' ? corpo : undefined,
      typeof token === 'string' && token ? token : undefined,
      consulta && typeof consulta === 'object' ? JSON.stringify(consulta) : undefined);
    return this._depois(lerJson(texto));
  }

  /* POST /api/restaurar com o .cofre em bytes (Uint8Array), sem base64: `corpo` traz só os PINs. */
  async restaurar(arquivo, corpo, token) {
    if (this.encerrado) this.iniciar(this._opcoes || {});
    const texto = this.ponte.restaurar_arquivo(comoBytes(arquivo), typeof corpo === 'string' ? corpo : undefined,
      typeof token === 'string' && token ? token : undefined);
    return this._depois(lerJson(texto));
  }

  /* POST /api/backup/baixar com o .cofre em bytes, sem base64 (seção 22.2): {status, cabecalhos,
   * gravou, dados: {nome, tamanho}, arquivo: Uint8Array (com buffer próprio, para transferir) | null}. */
  async baixarCopia(token) {
    if (this.encerrado) this.iniciar(this._opcoes || {});
    const r = lerJson(this.ponte.baixar_copia(typeof token === 'string' && token ? token : undefined));
    let arquivo = null;
    if (r.status === 200) {
      const proxy = this.ponte.copia_em_bytes();
      try {
        arquivo = proxy.toJs();                 // bytes -> Uint8Array (uma cópia, com buffer próprio)
      } finally {
        if (proxy && typeof proxy.destroy === 'function') proxy.destroy();
      }
    }
    await this._depois(r);
    return { ...r, arquivo };
  }

  // O temporizador do Worker chama de tempos em tempos: {bloqueou, gravou}.
  async verificarInatividade() {
    const r = lerJson(this.ponte.verificar_inatividade());
    if (r.gravou) await this.sincronizar(false);
    return r;
  }

  // A agenda de avisos (só datas e contagens): {aberto, dias, gravou}.
  async agenda() {
    const r = lerJson(this.ponte.agenda_de_avisos());
    if (r.gravou) await this.sincronizar(false);
    return r;
  }
}
