/* Entre Nós: agenda de avisos do celular (DESIGN.md, seção 21.5).
 *
 * O diário fica cifrado; o service worker não tem o PIN e não o abre. Para avisar "Você tem 2
 * lembretes hoje no Entre Nós" com o app fechado, o Worker guarda FORA do cofre só isto:
 *   ligado: true (os avisos estão ligados neste aparelho)
 *   agenda: {dias: {"AAAA-MM-DD": n}, gerada_em: "AAAA-MM-DD"} (só datas e contagens)
 *   avisado: "AAAA-MM-DD" (o último dia em que o aviso saiu, para não repetir no mesmo dia)
 * Nada de texto, tipo, nome, pessoa ou registro. Desligar os avisos (ou não haver diário) apaga tudo.
 *
 * Um script clássico, sem import/export, usado por dois lados: o service worker (importScripts) e o
 * Worker do Python (import). Expõe globalThis.EntreNosAgenda. A página não usa: pede ao Worker.
 */
'use strict';

(function (escopo) {
  const BANCO = 'entrenos-avisos';
  const LOJA = 'agenda';
  const MAX_DIAS = 400;
  const MAX_POR_DIA = 99;
  const RE_DIA = /^\d{4}-\d{2}-\d{2}$/;

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  // "AAAA-MM-DD" no fuso do aparelho (nunca toISOString, que é UTC).
  function diaDe(data) {
    const d = data instanceof Date ? data : new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  // Só o formato combinado passa: {"AAAA-MM-DD": inteiro de 1 a 99}, no máximo MAX_DIAS dias.
  function limparDias(dias) {
    const saida = {};
    if (!dias || typeof dias !== 'object' || Array.isArray(dias)) return saida;
    let quantos = 0;
    for (const [dia, n] of Object.entries(dias)) {
      if (quantos >= MAX_DIAS) break;
      if (!RE_DIA.test(dia) || !Number.isInteger(n) || n < 1) continue;
      saida[dia] = Math.min(n, MAX_POR_DIA);
      quantos += 1;
    }
    return saida;
  }

  function contarNoDia(agenda, dia) {
    const dias = agenda && agenda.dias ? limparDias(agenda.dias) : {};
    return dias[dia] || 0;
  }

  function textoDoAviso(n) {
    return n === 1 ? 'Você tem 1 lembrete hoje no Entre Nós.' : `Você tem ${n} lembretes hoje no Entre Nós.`;
  }

  // ---------------------------------------------------------------- IndexedDB (chave -> valor)

  function abrir(fabrica) {
    const idb = fabrica || escopo.indexedDB;
    return new Promise((resolve, reject) => {
      if (!idb) {
        reject(new Error('sem IndexedDB'));
        return;
      }
      const pedido = idb.open(BANCO, 1);
      pedido.onupgradeneeded = () => {
        if (!pedido.result.objectStoreNames.contains(LOJA)) pedido.result.createObjectStore(LOJA);
      };
      pedido.onsuccess = () => resolve(pedido.result);
      pedido.onerror = () => reject(pedido.error);
      pedido.onblocked = () => reject(new Error('banco de avisos bloqueado'));
    });
  }

  async function transacao(modo, fazer, fabrica) {
    const db = await abrir(fabrica);
    try {
      return await new Promise((resolve, reject) => {
        const t = db.transaction(LOJA, modo);
        const loja = t.objectStore(LOJA);
        let resultado;
        const pedido = fazer(loja);
        if (pedido) pedido.onsuccess = () => { resultado = pedido.result; };
        t.oncomplete = () => resolve(resultado);
        t.onerror = () => reject(t.error);
        t.onabort = () => reject(t.error || new Error('transação desfeita'));
      });
    } finally {
      db.close();
    }
  }

  const ler = (chave, fabrica) => transacao('readonly', (loja) => loja.get(chave), fabrica);
  const gravar = (chave, valor, fabrica) => transacao('readwrite', (loja) => loja.put(valor, chave), fabrica);

  async function ligado(fabrica) {
    try {
      return (await ler('ligado', fabrica)) === true;
    } catch {
      return false;
    }
  }

  async function ligar(fabrica) {
    await gravar('ligado', true, fabrica);
  }

  // Desligar apaga tudo o que estava fora do cofre (a agenda e o último aviso).
  async function apagarTudo(fabrica) {
    await transacao('readwrite', (loja) => loja.clear(), fabrica);
  }

  async function gravarAgenda(dias, hoje, fabrica) {
    if (!(await ligado(fabrica))) return false;
    await gravar('agenda', { dias: limparDias(dias), gerada_em: hoje || diaDe() }, fabrica);
    return true;
  }

  async function lerAgenda(fabrica) {
    const agenda = await ler('agenda', fabrica);
    return agenda && typeof agenda === 'object' ? { dias: limparDias(agenda.dias), gerada_em: agenda.gerada_em || null } : null;
  }

  // Para o service worker: quantos lembretes hoje, se ainda não avisou hoje (e marca que avisou).
  // Devolve 0 quando não há o que avisar.
  async function lembretesParaAvisar(hoje, fabrica) {
    if (!(await ligado(fabrica))) return 0;
    const dia = hoje || diaDe();
    if ((await ler('avisado', fabrica)) === dia) return 0;
    const n = contarNoDia(await lerAgenda(fabrica), dia);
    if (n > 0) await gravar('avisado', dia, fabrica);
    return n;
  }

  escopo.EntreNosAgenda = Object.freeze({
    BANCO, diaDe, limparDias, contarNoDia, textoDoAviso,
    ligado, ligar, apagarTudo, gravarAgenda, lerAgenda, lembretesParaAvisar,
  });
})(globalThis);
