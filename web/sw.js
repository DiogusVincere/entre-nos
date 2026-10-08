/* Entre Nós: service worker do app no celular e no navegador (DESIGN.md, seções 21.1 e 21.5).
 *
 * - Guarda o app e o Pyodide para abrir sem internet. Dois caches com versão: o do app
 *   (entrenos-app-<VERSAO>, a versão muda sempre que qualquer arquivo do site muda) e o do Pyodide
 *   (entrenos-pyodide-<versão do Pyodide>, imutável: pasta nova a cada versão).
 * - Pyodide: do cache primeiro. App: do cache da versão instalada (abre na hora, mesmo sem rede), e
 *   o navegador confere o sw.js a cada abertura: veio versão nova, ela se instala em segundo plano e
 *   a página mostra "Nova versão disponível — recarregar". Só troca quando a pessoa toca (nunca
 *   no meio do que ela está escrevendo).
 * - Só guarda resposta 200 do próprio site, sem redirecionamento (nada opaco, nada de outro lugar).
 * - Nunca toca em api/ (o servidor do computador) nem em pedido que não seja GET.
 * - Avisos: com o Periodic Background Sync (Chromium, app instalado), conta na agenda de avisos (fora
 *   do cofre, só datas e contagens) os lembretes de hoje e mostra "Você tem 2 lembretes hoje no Entre
 *   Nós". Nunca abre o diário: ele está cifrado e este arquivo não tem o PIN.
 *
 * O construir_web.py troca o bloco abaixo pela versão do site e pelas listas do que ele tem.
 */
'use strict';

// <construir_web>
const VERSAO = "dev";
const VERSAO_PYODIDE = "314.0.7";
const ARQUIVOS_APP = [
  "./",
  "agenda_avisos.js",
  "app.js",
  "entrenos-py.zip",
  "entrenos_pyodide.mjs",
  "estilo.css",
  "icones/apple-touch-icon.png",
  "icones/icone-192.png",
  "icones/icone-512.png",
  "icones/icone-maskable-192.png",
  "icones/icone-maskable-512.png",
  "manifest.webmanifest",
  "oauth.js",
  "sincronizar.js",
  "telas/ciclo.css",
  "telas/ciclo.js",
  "telas/eu.css",
  "telas/eu.js",
  "telas/lugares.css",
  "telas/lugares.js",
  "telas/marcos.css",
  "telas/marcos.js",
  "telas/perguntas.css",
  "telas/perguntas.js",
  "telas/presentes.css",
  "telas/presentes.js",
  "telas/promessas.css",
  "telas/promessas.js",
  "telas/rapida.css",
  "telas/rapida.js",
  "telas/semana.css",
  "telas/semana.js",
  "trabalhador.mjs",
];
const ARQUIVOS_PYODIDE = [
  "pyodide/314.0.7/pyodide.mjs",
  "pyodide/314.0.7/pyodide.asm.mjs",
  "pyodide/314.0.7/pyodide.asm.wasm",
  "pyodide/314.0.7/python_stdlib.zip",
  "pyodide/314.0.7/pyodide-lock.json",
  "pyodide/314.0.7/cffi-2.0.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
  "pyodide/314.0.7/cryptography-47.0.0-cp314-abi3-pyemscripten_2026_0_wasm32.whl",
  "pyodide/314.0.7/pycparser-3.0-py3-none-any.whl",
  "pyodide/314.0.7/six-1.17.0-py2.py3-none-any.whl",
];
// </construir_web>

const PREFIXO = 'entrenos-';
const CACHE_APP = `${PREFIXO}app-${VERSAO}`;
const CACHE_PYODIDE = `${PREFIXO}pyodide-${VERSAO_PYODIDE}`;
const ETIQUETA_AVISOS = 'entrenos-avisos';

importScripts('agenda_avisos.js');

function endereco(relativo) {
  return new URL(relativo, self.registration.scope).href;
}

// Só o que é do próprio site, completo e sem redirecionamento vai para o cache.
function guardavel(resposta) {
  return Boolean(resposta) && resposta.ok && resposta.status === 200 && resposta.type === 'basic' && !resposta.redirected;
}

async function buscarParaGuardar(url, modoCache) {
  const resposta = await fetch(new Request(url, { cache: modoCache, redirect: 'error', credentials: 'same-origin' }));
  if (!guardavel(resposta)) throw new Error(`resposta que não dá para guardar (${resposta.status})`);
  return resposta;
}

async function instalar() {
  // App: direto da rede ('reload'), para não guardar um arquivo velho do cache HTTP numa versão nova.
  const app = await caches.open(CACHE_APP);
  await Promise.all(ARQUIVOS_APP.map(async (relativo) => {
    const url = endereco(relativo);
    await app.put(url, await buscarParaGuardar(url, 'reload'));
  }));
  // Pyodide: imutável (a pasta tem a versão no nome). O cache HTTP pode servir: na primeira
  // abertura o Worker baixa os mesmos arquivos ao mesmo tempo, e assim eles não vêm duas vezes.
  const pyodide = await caches.open(CACHE_PYODIDE);
  await Promise.all(ARQUIVOS_PYODIDE.map(async (relativo) => {
    const url = endereco(relativo);
    if (await pyodide.match(url)) return;
    await pyodide.put(url, await buscarParaGuardar(url, 'default'));
  }));
}

async function ativar() {
  const manter = new Set([CACHE_APP, CACHE_PYODIDE]);
  for (const nome of await caches.keys()) {
    if (nome.startsWith(PREFIXO) && !manter.has(nome)) await caches.delete(nome);
  }
  await self.clients.claim();
}

async function doPyodide(pedido) {
  const cache = await caches.open(CACHE_PYODIDE);
  const guardada = await cache.match(pedido.url);
  if (guardada) return guardada;
  const resposta = await fetch(pedido);
  if (guardavel(resposta)) await cache.put(pedido.url, resposta.clone());
  return resposta;
}

async function doApp(pedido, comoPagina) {
  const cache = await caches.open(CACHE_APP);
  const guardada = await cache.match(comoPagina ? endereco('./') : pedido.url);
  if (guardada) return guardada;
  return fetch(pedido);
}

self.addEventListener('install', (evento) => {
  evento.waitUntil(instalar());
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(ativar());
});

self.addEventListener('fetch', (evento) => {
  const pedido = evento.request;
  if (pedido.method !== 'GET') return;
  const url = new URL(pedido.url);
  const escopo = new URL(self.registration.scope);
  if (url.origin !== escopo.origin || !url.pathname.startsWith(escopo.pathname)) return;
  const caminho = url.pathname.slice(escopo.pathname.length);
  if (caminho === 'api' || caminho.startsWith('api/')) return;
  if (caminho.startsWith(`pyodide/${VERSAO_PYODIDE}/`) && ARQUIVOS_PYODIDE.includes(caminho)) {
    evento.respondWith(doPyodide(pedido));
    return;
  }
  const pagina = pedido.mode === 'navigate' && (caminho === '' || caminho === 'index.html');
  if (pagina || ARQUIVOS_APP.includes(caminho)) evento.respondWith(doApp(pedido, pagina));
});

self.addEventListener('message', (evento) => {
  const dados = evento.data;
  if (dados && dados.tipo === 'pular-espera') self.skipWaiting();
});

// ---------------------------------------------------------------- avisos (seção 21.5)

async function avisarHoje() {
  const agenda = self.EntreNosAgenda;
  if (!agenda || !self.registration.showNotification) return;
  if (typeof Notification !== 'undefined' && Notification.permission !== 'granted') return;
  let n = 0;
  try {
    n = await agenda.lembretesParaAvisar(agenda.diaDe(new Date()));
  } catch {
    return;
  }
  if (!n) return;
  await self.registration.showNotification('Entre Nós', {
    body: agenda.textoDoAviso(n),
    tag: 'entrenos-hoje',
    icon: endereco('icones/icone-192.png'),
    lang: 'pt-BR',
  });
}

self.addEventListener('periodicsync', (evento) => {
  if (evento.tag === ETIQUETA_AVISOS) evento.waitUntil(avisarHoje());
});

self.addEventListener('notificationclick', (evento) => {
  evento.notification.close();
  evento.waitUntil((async () => {
    const janelas = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const janela of janelas) {
      if (janela.url.startsWith(self.registration.scope) && 'focus' in janela) {
        // O app já aberto em outra tela (Ajustes, por exemplo): a página vai para o Hoje sozinha, sem
        // recarregar (recarregar bloquearia o diário e perderia um rascunho).
        try {
          janela.postMessage({ tipo: 'abrir-hoje' });
        } catch {
          // sem mensagem, ao menos o foco
        }
        return janela.focus();
      }
    }
    return self.clients.openWindow(endereco('./#hoje'));
  })());
});
