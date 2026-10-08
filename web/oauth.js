/* Entre Nós: a volta do login do Google (DESIGN.md, seção 22.2).
 *
 * A página do Entre Nós abre uma janela do Google (accounts.google.com/o/oauth2/v2/auth, fluxo de
 * token para apps web, escopo só drive.appdata) que volta para cá com o acesso no fragmento do
 * endereço (#access_token=...&state=...). Nenhum código do Google roda no app: esta página estática
 * só entrega o que veio à página que abriu a janela, no MESMO endereço (postMessage para a mesma
 * origem e, se a janela perdeu a ligação com a página, um BroadcastChannel, que também só fala com a
 * mesma origem), tira o fragmento do histórico e se fecha. Quem confere o `state` (um número
 * aleatório que só a página sabe) é a página: um acesso que ela não pediu é ignorado.
 * Nada aqui guarda o acesso (nem em armazenamento do navegador) nem escreve no console do navegador.
 */
'use strict';

(() => {
  const CANAL = 'entrenos-oauth';
  const bruto = String(location.hash || '').replace(/^#/, '') || String(location.search || '').replace(/^\?/, '');
  const p = new URLSearchParams(bruto);
  const dados = {
    tipo: 'entrenos-oauth',
    state: p.get('state') || '',
    access_token: p.get('access_token') || '',
    expires_in: p.get('expires_in') || '',
    scope: p.get('scope') || '',
    error: p.get('error') || '',
  };
  // O acesso sai do endereço (e do histórico) antes de qualquer outra coisa.
  try {
    history.replaceState(null, '', location.pathname);
  } catch {
    // sem history: a janela fecha logo
  }
  let entregue = false;
  try {
    if (window.opener && window.opener !== window) {
      window.opener.postMessage(dados, location.origin);
      entregue = true;
    }
  } catch {
    // a janela perdeu a ligação com a página (o navegador isola janelas de sites diferentes)
  }
  try {
    const canal = new BroadcastChannel(CANAL);
    canal.postMessage(dados);
    canal.close();
    entregue = true;
  } catch {
    // navegador sem BroadcastChannel
  }
  dados.access_token = '';
  const mensagem = document.getElementById('oauth-mensagem');
  if (mensagem) {
    mensagem.textContent = entregue
      ? 'Pronto. Esta janela fecha sozinha; se não fechar, feche e volte ao Entre Nós.'
      : 'Não consegui avisar o Entre Nós. Feche esta janela, volte ao Entre Nós e toque de novo.';
  }
  setTimeout(() => {
    try {
      window.close();
    } catch {
      // a pessoa fecha
    }
  }, 400);
})();
