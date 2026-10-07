/* Anotação rápida (DESIGN.md, seção 20.4): a tela do recurso "rapida".
 *
 * Encaixa no app por window.EntreNos:
 * - menu e tela #anotar: um campo e um botão (Ctrl+Enter guarda) e a lista "Para organizar";
 * - no computador, Alt+N abre a tela de qualquer lugar (fora de um campo de texto);
 * - no celular, o "+" da barra leva a Registrar, onde aparece "📝 Guardar para organizar depois"
 *   (EntreNos.registrarAcaoRegistrar): o texto vira anotação sem passar pela revisão, com a data do
 *   campo "Quando foi?";
 * - cartão do Hoje "📝 N anotações para organizar".
 * Organizar abre a revisão normal com a data de quando foi anotado (para "amanhã" continuar certo),
 * por EntreNos.abrirRegistrar; depois de salvo (aoSalvar), a anotação sai da lista (o servidor confere
 * que o registro é mesmo ela). Esta página não guarda texto nenhum. Tudo por nós de texto (h). */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E || !E.util) return;
  const { api, h, navegar } = E;
  const u = E.util;

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function erroNaTela(e) {
    if (e && e.bloqueio) return;
    u.avisarErro(e);
  }

  function caminho(id, resto = '') {
    return `/rapida/rascunhos/${encodeURIComponent(String(id))}${resto}`;
  }

  // ---------------------------------------------------------------- organizar (a revisão normal)

  const REVISAO_ABERTA = 'Há uma revisão aberta em Registrar. Salve ou descarte ela antes de organizar esta anotação.';

  async function organizar(r) {
    const atual = E.rascunhoAtual();
    if (atual.emRevisao) {
      E.abrirRegistrar({});                     // mostra a revisão que está aberta
      u.avisar(REVISAO_ABERTA, 'erro');
      return;
    }
    const antes = atual.texto;
    if (antes.trim() && u.normalizar(antes) !== u.normalizar(r.texto)) {
      // O que já estava sendo escrito não se perde: vira outra anotação.
      const ok = await u.confirmar({
        titulo: 'Já tem um texto em Registrar',
        texto: 'Para organizar a anotação, o texto que está em Registrar vai ser guardado como outra anotação rápida (nada se perde). Seguir?',
        ok: 'Guardar e seguir',
        cancelar: 'Agora não',
      });
      if (!ok) return;
      try {
        await api('POST', '/rapida/rascunhos', {
          texto: antes, referencia: atual.data || u.hojeIso(), privado: atual.privado,
          contexto: atual.contexto || null, humor: atual.humor || null,
        });
      } catch (e) {
        erroNaTela(e);
        return;
      }
    }
    const aberto = E.abrirRegistrar({
      texto: r.texto, data: r.referencia || u.hojeIso(), contexto: r.contexto || '', humor: r.humor || '',
      privado: Boolean(r.privado), organizar: true, aoSalvar: (registroId) => concluir(r.id, registroId),
    });
    if (!aberto.ok) {
      if (aberto.motivo === 'revisao') u.avisar(REVISAO_ABERTA, 'erro');
      return;
    }
    u.falar('A anotação foi para a revisão, com a data de quando você anotou.');
  }

  async function concluir(id, registroId) {
    try {
      const r = await api('POST', caminho(id, '/organizado'), { registro_id: registroId });
      if (r && r.organizado) u.avisar('📝 A anotação saiu da lista: agora ela é um registro.');
      else if (r && r.motivo) u.avisar(r.motivo, 'erro');
    } catch (e) {
      if (e && e.status === 404) return;        // já tinha saído (apagada em outra tela)
      erroNaTela(e);
    }
  }

  async function organizarPorId(id) {
    try {
      const dados = (await api('GET', '/rapida/rascunhos')) || {};
      const r = lista(dados.rascunhos).find((x) => x && x.id === id);
      if (!r) {
        u.avisar('Essa anotação não está mais na lista.', 'erro');
        return;
      }
      await organizar(r);
    } catch (e) {
      erroNaTela(e);
    }
  }

  // ---------------------------------------------------------------- "Guardar para organizar depois" em Registrar

  // O botão entra ao lado do "Organizar" pelo contrato (registrarAcaoRegistrar): recebe uma cópia do
  // que está escrito e, guardado, esvazia o formulário.
  async function guardarDoRegistrar(rascunho, ctx) {
    if (!rascunho.texto.trim()) {
      u.avisar('Escreva alguma coisa antes de guardar.', 'erro');
      ctx.focar();
      return;
    }
    try {
      await api('POST', '/rapida/rascunhos', {
        texto: rascunho.texto, referencia: rascunho.data || u.hojeIso(), privado: rascunho.privado,
        contexto: rascunho.contexto || null, humor: rascunho.humor || null,
      });
    } catch (e) {
      erroNaTela(e);
      return;
    }
    ctx.limpar();
    u.avisar('📝 Anotado. Fica em “Anotação rápida” até você organizar, com a data de quando foi.');
  }

  // Alt+N abre a anotação rápida (fora de um campo de texto, para não atrapalhar quem digita).
  document.addEventListener('keydown', (ev) => {
    if (!ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.code !== 'KeyN') return;
    const alvo = ev.target instanceof Element ? ev.target : null;
    if (alvo && alvo.closest('input, textarea, select, [contenteditable="true"], dialog[open]')) return;
    const app = document.querySelector('#app');
    if (!app || app.hidden) return;
    ev.preventDefault();
    navegar('#anotar');
  });

  // ---------------------------------------------------------------- a tela #anotar

  function textoData(d) {
    if (d.precisao === 'mes') return u.mesAno(d.data);
    if (d.precisao === 'ano') return String(d.data).slice(0, 4);
    if (d.precisao === 'semana') return `semana de ${u.dataBr(d.data)}`;
    return u.dataBr(d.data);
  }

  function chipsDatas(r) {
    const datas = lista(r.datas).filter((d) => d && d.expressao && d.data);
    if (!datas.length) return null;
    return h('ul', { class: 'chips', 'aria-label': 'Datas que a anotação cita, pela data em que foi anotada' },
      datas.map((d) => h('li', { class: 'chip' }, `“${d.expressao}” = ${textoData(d)}`)));
  }

  function itemRascunho(r) {
    const li = h('li', { class: 'cartao' });
    function desenhar() {
      u.limpar(li);
      const urgente = r.urgente === 'hoje' ? 'Ela fala de hoje.' : r.urgente === 'amanha' ? 'Ela fala de amanhã.' : null;
      u.anexar(li,
        h('p', { class: 'texto-livre' }, r.texto),
        h('p', { class: 'sutil' },
          r.dias === 0 ? 'Anotada hoje'
            : r.dias === 1 ? `Anotada ontem (${u.dataBr(r.referencia)})`
              : r.dias > 1 ? `Anotada em ${u.dataBr(r.referencia)} (${r.quando})` : `Anotada para ${u.dataBr(r.referencia)}`,
          r.privado ? ' · 🔒 não vai para a IA' : ''),
        chipsDatas(r),
        urgente ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '⏰ '), urgente) : null,
        r.organizada
          ? h('div', { class: 'nota-cuidado' },
            h('p', null, 'Parece que esta anotação já virou um registro',
              r.organizada.data_registro ? ` de ${u.dataBr(r.organizada.data_registro)}` : '', '. ',
              u.linkRegistro(r.organizada.registro_id, 'ver o registro')),
            h('div', { class: 'acoes' }, botaoTirar(r, 'Tirar da lista')))
          : null,
        h('div', { class: 'acoes' },
          h('button', { type: 'button', class: 'botao botao-primario botao-pequeno', onclick: () => organizar(r) }, '🗂️ Organizar'),
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => editar() }, '✏️ Editar'),
          botaoTirar(r, '🗑️ Apagar', true)));
    }
    function editar() {
      const idTexto = u.novoId('rapida-editar');
      const idData = u.novoId('rapida-editar-data');
      const erro = h('p', { class: 'erro-campo', role: 'alert' });
      const texto = h('textarea', { id: idTexto, rows: '4', value: r.texto });
      const data = h('input', { id: idData, type: 'date', value: r.referencia || '', max: u.hojeIso() });
      const salvar = async (ev) => {
        ev.preventDefault();
        if (!texto.value.trim()) {
          erro.textContent = 'Escreva alguma coisa (ou apague a anotação).';
          texto.focus();
          return;
        }
        try {
          const resp = (await api('PATCH', caminho(r.id), { texto: texto.value, referencia: data.value || null })) || {};
          if (resp.rascunho) Object.assign(r, resp.rascunho);
          desenhar();
          u.avisar('Anotação atualizada.');
        } catch (e) {
          if (e && e.bloqueio) return;
          erro.textContent = e.message;
        }
      };
      u.limpar(li);
      u.anexar(li, h('form', { class: 'form-item', novalidate: true, onsubmit: salvar },
        u.campo('Texto da anotação', texto),
        u.campo('Quando foi?', data, '“Amanhã” e “sexta” são contados a partir deste dia.'),
        erro,
        h('div', { class: 'acoes' },
          h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, 'Salvar'),
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => desenhar() }, 'Cancelar'))));
      texto.focus();
    }
    desenhar();
    return li;
  }

  function botaoTirar(r, rotulo, perguntar = false) {
    const b = h('button', { type: 'button', class: perguntar ? 'botao botao-texto botao-perigo-texto' : 'botao botao-secundario botao-pequeno' }, rotulo);
    b.addEventListener('click', async () => {
      if (perguntar) {
        const ok = await u.confirmar({
          titulo: 'Apagar esta anotação?',
          texto: 'Ela sai do diário e das cópias automáticas. Não dá para desfazer.',
          ok: 'Apagar',
          perigo: true,
        });
        if (!ok) return;
      }
      b.disabled = true;
      try {
        await api('DELETE', caminho(r.id));
        u.avisar(perguntar ? 'Anotação apagada.' : 'Pronto, saiu da lista.');
        navegar('#anotar');
      } catch (e) {
        if (b.isConnected) b.disabled = false;
        erroNaTela(e);
      }
    });
    return b;
  }

  function formAnotar() {
    const erro = h('p', { class: 'erro-campo', id: 'rapida-erro', role: 'alert' });
    const texto = h('textarea', {
      id: 'rapida-texto', rows: '5', 'aria-describedby': 'rapida-erro', 'aria-keyshortcuts': 'Control+Enter',
      placeholder: 'Ex.: ela comentou que a prova é amanhã e tá nervosa',
      oninput: () => { erro.textContent = ''; },
    });
    const data = h('input', { id: 'rapida-data', type: 'date', value: u.hojeIso(), max: u.hojeIso() });
    const privado = h('input', { id: 'rapida-privado', type: 'checkbox', 'aria-describedby': 'rapida-privado-dica' });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario botao-grande', 'aria-keyshortcuts': 'Control+Enter' }, '📝 Guardar');
    const guardar = async () => {
      if (botao.disabled) return;
      if (!texto.value.trim()) {
        erro.textContent = 'Escreva alguma coisa antes de guardar.';
        texto.focus();
        return;
      }
      botao.disabled = true;
      try {
        await api('POST', '/rapida/rascunhos', { texto: texto.value, referencia: data.value || null, privado: privado.checked });
        u.avisar('📝 Anotado. Organize quando tiver um tempinho.');
        navegar('#anotar');
      } catch (e) {
        botao.disabled = false;
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
        texto.focus();
      }
    };
    texto.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        ev.stopPropagation();
        guardar();
      }
    });
    return h('form', { class: 'cartao form-registro', novalidate: true, onsubmit: (ev) => { ev.preventDefault(); guardar(); } },
      u.campo('O que você quer lembrar?', texto),
      erro,
      h('details', { class: 'mais-detalhes' },
        h('summary', null, 'Outro dia ou 🔒 (opcional)'),
        u.campo('Quando foi?', data, '“Amanhã” e “sexta” vão ser contados a partir deste dia, mesmo que você organize depois.'),
        h('div', { class: 'opcao-privado' },
          h('label', { class: 'opcao-linha', for: 'rapida-privado' }, privado,
            h('span', null, h('span', { 'aria-hidden': 'true' }, '🔒 '), 'Organizar já marcado para não ir para a IA')),
          h('p', { class: 'dica', id: 'rapida-privado-dica' }, 'A anotação em si nunca vai para a IA; isto vale para o registro que ela virar.'))),
      h('div', { class: 'acoes' }, botao,
        h('span', { class: 'sutil atalho' }, 'Ctrl+Enter guarda · Alt+N abre esta tela')));
  }

  async function telaAnotar(main, _partes, atual) {
    u.anexar(main,
      u.cabecalho('Anotação rápida', 'Escreva do jeito que vier. Fica guardado, criptografado, até você organizar, com a data de hoje: assim “amanhã” continua certo depois.', '📝'),
      formAnotar(),
      u.carregando());
    const dados = (await api('GET', '/rapida/rascunhos')) || {};
    if (!atual()) return;
    const carregando = main.querySelector('.carregando');
    if (carregando) carregando.remove();
    const rascunhos = lista(dados.rascunhos);
    u.anexar(main, u.secao(`🗂️ Para organizar${rascunhos.length ? ` (${rascunhos.length})` : ''}`,
      rascunhos.length
        ? [h('p', { class: 'sutil' }, 'As mais antigas primeiro. Organizar abre a revisão de sempre, com a data de quando você anotou.'),
          h('ul', { class: 'lista-cartoes' }, rascunhos.map(itemRascunho))]
        : u.estadoVazio('Nenhuma anotação esperando. Quando estiver sem tempo, anote aqui e organize depois.')));
    // Anotação rápida é para escrever na hora: o cursor vai para o campo (depois do título, que a
    // troca de tela foca primeiro para o leitor de tela anunciar onde está).
    setTimeout(() => {
      const campo = main.querySelector('#rapida-texto');
      if (campo && atual() && !campo.value) campo.focus();
    }, 0);
  }

  // ---------------------------------------------------------------- cartão do Hoje

  function cartaoHoje(c, ctx) {
    const dados = c.dados || {};
    const ids = lista(dados.rascunhos).filter((r) => r && r.id != null);
    const total = Number(dados.total) || ids.length;
    return [
      h('p', { class: 'alerta-texto' }, c.texto),
      ids.length ? h('ul', { class: 'lista-entradas' }, ids.map((r) => h('li', { class: 'entrada' },
        h('p', { class: 'entrada-texto' }, r.inicio || ''),
        h('p', { class: 'entrada-rodape sutil' },
          r.referencia ? `anotada em ${u.dataBr(r.referencia)}` : '',
          r.urgente ? ` · fala de ${r.urgente === 'hoje' ? 'hoje' : 'amanhã'}` : '', ' ',
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => organizarPorId(r.id) }, '🗂️ Organizar'))))) : null,
      h('div', { class: 'alerta-acoes' },
        c.porque ? h('details', { class: 'porque' }, h('summary', null, 'Por quê?'), h('p', null, c.porque)) : h('span'),
        h('div', { class: 'acoes' },
          h('a', { href: '#anotar', class: 'botao botao-texto' }, total > ids.length ? `Ver todas (${total})` : 'Ver anotações'),
          ctx.botaoDispensar())),
    ];
  }

  E.registrarMenu('anotar', 'Anotação rápida', '📝', 'registrar');
  E.registrarTela('anotar', telaAnotar);
  E.registrarCartaoHoje('rapida', cartaoHoje);
  E.registrarAcaoRegistrar('rapida-guardar', '📝 Guardar para organizar depois', guardarDoRegistrar);
})();
