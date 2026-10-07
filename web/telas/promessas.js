/* Promessas (DESIGN.md, seção 20.3): a tela do recurso "promessas".
 *
 * Encaixa no app por window.EntreNos:
 * - menu e tela #promessas: em aberto (as mais urgentes primeiro), cumpridas 💙 e deixadas de lado,
 *   cada uma com o trecho de onde veio, o prazo com a precisão que foi dita e, quando um registro
 *   depois conta a mesma ação no passado, "parece que você já cumpriu" (citando o registro);
 * - miolo dos extras na revisão: "promessa" (texto e prazo editáveis, aviso de promessa igual já em
 *   aberto) e "promessa_cumprida" (você escolhe qual promessa foi cumprida; nada é ligado sozinho);
 * - cartões do Hoje: prazo chegando, sugestão gentil e "parece que você cumpriu".
 * Sem placar nem porcentagem: é uma lista para nada ficar esquecido. Tudo por nós de texto (h). */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E || !E.util) return;
  const { api, h, navegar } = E;
  const u = E.util;

  const FORMAS = {
    prometi: ['🤝', 'Promessa'],
    fiquei_de: ['📌', 'Você ficou de fazer'],
    combinei: ['🤝', 'Combinado'],
    vou: ['📌', 'Compromisso'],
    disse_que: ['💬', 'Você disse que ia'],
    manual: ['✍️', 'Anotada por você'],
  };
  const SITUACOES = {
    atrasada: 'O prazo passou',
    hoje: 'É hoje',
    chegando: 'Está chegando',
  };

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function erroNaTela(e) {
    if (e && e.bloqueio) return;
    u.avisarErro(e);
  }

  function rotuloForma(forma) {
    const [emoji, nome] = FORMAS[forma] || FORMAS.prometi;
    return h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, `${emoji} `), nome);
  }

  // O prazo como foi dito: "sexta (02/10)", "em dezembro" (a precisão nunca vira um dia inventado).
  function linhaPrazo(p) {
    const partes = [];
    if (p.prazo_texto) {
      partes.push(`📅 ${u.capitalizar(p.prazo_texto)}`);
      if (p.prazo_expressao) partes.push(`(“${p.prazo_expressao}”)`);
    } else if (p.status === 'aberta') {
      partes.push('Sem prazo');
    }
    if (p.feita_quando) partes.push(`${partes.length ? '· ' : ''}${p.forma === 'manual' ? 'anotada' : 'prometida'} ${p.feita_quando}`);
    return h('p', { class: 'sutil' }, partes.join(' '));
  }

  function paraQuem(p) {
    return p.pessoa ? h('p', { class: 'sutil' }, `Para: ${p.pessoa}`) : null;
  }

  // Botão que chama a API, desliga enquanto espera e, no fim, faz `depois(resposta)`.
  function botaoAcao(rotulo, classe, chamar, depois) {
    const b = h('button', { type: 'button', class: classe }, rotulo);
    b.addEventListener('click', async () => {
      if (b.disabled) return;
      b.disabled = true;
      try {
        const r = await chamar();
        await depois(r);
      } catch (e) {
        if (b.isConnected) b.disabled = false;
        erroNaTela(e);
      }
    });
    return b;
  }

  function mudarStatus(id, status, registroId) {
    const corpo = { status };
    if (registroId != null) corpo.registro_id = registroId;
    return api('PATCH', `/promessas/${encodeURIComponent(String(id))}`, corpo);
  }

  function recarregar() {
    navegar('#promessas');
  }

  // ---------------------------------------------------------------- a tela #promessas

  function caixaIndicio(p) {
    const ind = p.indicio;
    if (!ind) return null;
    return h('div', { class: 'nota-cuidado' },
      h('p', null, h('span', { 'aria-hidden': 'true' }, '💙 '), 'Parece que você já cumpriu esta: ',
        ind.data_registro ? `em ${u.dataBr(ind.data_registro)} você registrou` : 'você registrou',
        ind.motivo ? ` (${ind.motivo})` : '', ':'),
      // Frase do ciclo num registro discreto: o servidor não manda o trecho (20.10).
      ind.trecho ? u.citacao(ind.trecho) : h('p', { class: 'sutil' }, '🔒 Um registro reservado (abra para ver).'),
      h('div', { class: 'acoes' },
        botaoAcao('✔️ Sim, cumpri', 'botao botao-secundario botao-pequeno',
          () => mudarStatus(p.id, 'cumprida', ind.registro_id),
          () => { u.avisar('Que bom! 💙 Marquei como cumprida.'); recarregar(); }),
        u.linkRegistro(ind.registro_id, 'ver o registro')));
  }

  function formEditar(p, li, desenhar) {
    const idTexto = u.novoId('prom-texto');
    const idPrazo = u.novoId('prom-prazo');
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const texto = h('input', { id: idTexto, type: 'text', maxlength: '300', value: p.texto, required: true });
    const prazo = h('input', { id: idPrazo, type: 'date', value: p.prazo || '' });
    const salvar = async (ev) => {
      ev.preventDefault();
      if (!texto.value.trim()) {
        erro.textContent = 'Escreva o que foi prometido.';
        texto.focus();
        return;
      }
      const corpo = { texto: texto.value };
      if ((prazo.value || '') !== (p.prazo || '')) corpo.prazo = prazo.value || null;
      try {
        await api('PATCH', `/promessas/${encodeURIComponent(String(p.id))}`, corpo);
        u.avisar('Pronto, promessa atualizada.');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    };
    const form = h('form', { class: 'form-item', novalidate: true, onsubmit: salvar },
      u.campo('O que foi prometido', texto),
      u.campo('Prazo (opcional)', prazo, 'Deixe vazio se não tem dia certo.'),
      erro,
      h('div', { class: 'acoes' },
        h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, 'Salvar'),
        h('button', { type: 'button', class: 'botao botao-texto', onclick: () => { desenhar(); } }, 'Cancelar')));
    u.limpar(li);
    u.anexar(li, form);
    texto.focus();
  }

  function cartaoAberta(p) {
    const li = h('li', { class: 'cartao' });
    function desenhar() {
      u.limpar(li);
      const situacao = SITUACOES[p.situacao];
      u.anexar(li,
        h('div', { class: 'item-topo' }, rotuloForma(p.forma),
          situacao ? h('span', { class: p.situacao === 'atrasada' ? 'chip chip-importante' : 'chip' }, situacao) : null),
        h('p', { class: 'alerta-texto' }, p.texto),
        paraQuem(p),
        linhaPrazo(p),
        p.registro_id != null && p.trecho ? u.citacao(p.trecho) : null,
        lista(p.conflitos).map((c) => h('p', { class: 'dica' },
          h('span', { 'aria-hidden': 'true' }, '⚠️ '), c.texto, ' Talvez valha combinar com ela se o dia ainda funciona.',
          c.registro_id != null ? [' ', u.linkRegistro(c.registro_id, 'ver registro')] : null)),
        caixaIndicio(p),
        h('div', { class: 'acoes' },
          botaoAcao('✔️ Cumpri', 'botao botao-secundario botao-pequeno', () => mudarStatus(p.id, 'cumprida'),
            () => { u.avisar('Que bom! 💙 Marquei como cumprida.'); recarregar(); }),
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => formEditar(p, li, desenhar) }, '✏️ Editar ou mudar o prazo'),
          botaoAcao('🙅 Deixar de lado', 'botao botao-texto', () => mudarStatus(p.id, 'desisti'),
            () => { u.avisar('Tudo bem mudar de ideia. Ficou em “Deixadas de lado”.'); recarregar(); }),
          botaoApagar(p),
          p.registro_id != null ? u.linkRegistro(p.registro_id, 'ver registro') : null));
    }
    desenhar();
    return li;
  }

  function botaoApagar(p) {
    const b = h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto' }, '🗑️ Apagar');
    b.addEventListener('click', async () => {
      const ok = await u.confirmar({
        titulo: 'Apagar esta promessa?',
        texto: 'Ela sai da lista e das cópias automáticas. O registro de onde ela veio continua. Não dá para desfazer.',
        ok: 'Apagar',
        perigo: true,
      });
      if (!ok) return;
      b.disabled = true;
      try {
        await api('DELETE', `/promessas/${encodeURIComponent(String(p.id))}`);
        u.avisar('Promessa apagada.');
        recarregar();
      } catch (e) {
        if (b.isConnected) b.disabled = false;
        erroNaTela(e);
      }
    });
    return b;
  }

  function cartaoConcluida(p) {
    const cumprida = p.status === 'cumprida';
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' },
        h('span', { 'aria-hidden': 'true' }, cumprida ? '💙' : '🍃'),
        h('span', { class: 'entrada-texto' }, p.texto)),
      p.pessoa ? h('p', { class: 'sutil' }, `Para: ${p.pessoa}`) : null,
      h('p', { class: 'entrada-rodape sutil' },
        cumprida ? (p.concluida_em ? `Cumprida em ${u.dataBr(p.concluida_em)}` : 'Cumprida')
          : (p.concluida_em ? `Deixada de lado em ${u.dataBr(p.concluida_em)}` : 'Deixada de lado'),
        p.concluida_registro_id != null ? [' · ', u.linkRegistro(p.concluida_registro_id, 'o registro em que você contou')] : null,
        p.registro_id != null ? [' · ', u.linkRegistro(p.registro_id, 'onde foi prometida')] : null),
      h('div', { class: 'acoes' },
        botaoAcao('↩️ Reabrir', 'botao botao-texto', () => mudarStatus(p.id, 'aberta'),
          () => { u.avisar('Voltou para “Em aberto”.'); recarregar(); }),
        cumprida ? null : botaoApagar(p)));
  }

  function formNova() {
    const idTexto = u.novoId('nova-prom');
    const idPrazo = u.novoId('nova-prom-prazo');
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const texto = h('input', { id: idTexto, type: 'text', maxlength: '300', placeholder: 'Ex.: levar ela para ver o mar' });
    const prazo = h('input', { id: idPrazo, type: 'date' });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Guardar promessa');
    const guardar = async (ev) => {
      ev.preventDefault();
      if (!texto.value.trim()) {
        erro.textContent = 'Escreva o que você prometeu.';
        texto.focus();
        return;
      }
      botao.disabled = true;
      try {
        await api('POST', '/promessas', { texto: texto.value, prazo: prazo.value || null });
        u.avisar('Guardada. Ela aparece aqui e no Hoje quando o prazo chegar.');
        recarregar();
      } catch (e) {
        botao.disabled = false;
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    };
    return h('details', { class: 'cartao mais-detalhes' },
      h('summary', null, '➕ Anotar uma promessa'),
      h('form', { class: 'form-item', novalidate: true, onsubmit: guardar },
        h('p', { class: 'sutil' }, 'Quase sempre as promessas aparecem sozinhas quando você registra “prometi…” ou “fiquei de…”. Aqui é para anotar uma direto.'),
        u.campo('O que você prometeu?', texto),
        u.campo('Prazo (opcional)', prazo),
        erro,
        h('div', { class: 'acoes' }, botao)));
  }

  async function telaPromessas(main, _partes, atual) {
    u.anexar(main,
      u.cabecalho('Promessas', 'O que você prometeu, ficou de fazer ou combinou com ela, tirado dos seus registros. Sem placar: é só para nada ficar esquecido.', '🤝'),
      formNova(),
      u.carregando());
    const dados = (await api('GET', '/promessas')) || {};
    if (!atual()) return;
    const carregando = main.querySelector('.carregando');
    if (carregando) carregando.remove();
    const abertas = lista(dados.abertas);
    const cumpridas = lista(dados.cumpridas);
    const deixadas = lista(dados.deixadas);
    if (!abertas.length && !cumpridas.length && !deixadas.length) {
      u.anexar(main, u.estadoVazio('Nenhuma promessa por aqui ainda. Quando você registrar “prometi levar ela ao cinema” ou “fiquei de ligar pra ela”, ela aparece aqui, com o trecho de onde veio.', true));
      return;
    }
    const m = dados.momento;
    if (m && abertas.length) {
      u.anexar(main, h('p', { class: 'nota-cuidado', role: 'note' },
        h('span', { 'aria-hidden': 'true' }, '🫶 '),
        m.data ? `Em ${u.dataBr(m.data)} você registrou: “${m.trecho}”. ` : `Você registrou: “${m.trecho}”. `,
        'Sem pressa com as promessas: talvez o mais importante agora seja estar perto.',
        m.registro_id != null ? [' ', u.linkRegistro(m.registro_id, 'ver registro')] : null));
    }
    u.anexar(main, u.secao(`🤝 Em aberto${abertas.length ? ` (${abertas.length})` : ''}`,
      abertas.length
        ? h('ul', { class: 'lista-cartoes' }, abertas.map(cartaoAberta))
        : u.vazioPequeno('Nenhuma promessa em aberto agora.')));
    if (cumpridas.length) {
      u.anexar(main, u.secao(`💙 Cumpridas (${cumpridas.length})`,
        h('ul', { class: 'lista-entradas' }, cumpridas.map(cartaoConcluida))));
    }
    if (deixadas.length) {
      u.anexar(main, h('details', { class: 'cartao mais-detalhes' },
        h('summary', null, `🍃 Deixadas de lado (${deixadas.length})`),
        h('p', { class: 'sutil' }, 'Tudo bem mudar de ideia. Dá para reabrir ou apagar.'),
        h('ul', { class: 'lista-entradas' }, deixadas.map(cartaoConcluida))));
    }
  }

  // ---------------------------------------------------------------- revisão (extras)

  // Uma promessa nova: o texto e o prazo podem ser ajustados antes de guardar.
  function revisaoPromessa(e) {
    if (e._prazoOriginal === undefined) e._prazoOriginal = e.prazo || '';
    const idTexto = u.novoId('rev-prom');
    const idPrazo = u.novoId('rev-prom-prazo');
    const texto = h('input', {
      id: idTexto, type: 'text', maxlength: '300', value: e.texto || e.resumo || '',
      oninput: (ev) => { e.texto = ev.target.value; e.resumo = ev.target.value; },
    });
    const prazo = h('input', {
      id: idPrazo, type: 'date', value: e.prazo || '',
      onchange: (ev) => {
        e.prazo = ev.target.value || null;
        const igual = (ev.target.value || '') === e._prazoOriginal;
        if (!igual) {
          e.precisao = ev.target.value ? 'dia' : null;
          e.prazo_expressao = null;
        }
      },
    });
    const dicaPrazo = e.prazo_expressao && e.prazo === e._prazoOriginal
      ? `Você escreveu “${e.prazo_expressao}”.` + (e.precisao && e.precisao !== 'dia' ? ' Vale o período todo, não um dia certo.' : '')
      : 'Deixe vazio se não tem dia certo.';
    const alternativas = lista(e.prazo_alternativas).filter((d) => typeof d === 'string');
    const parecidas = h('div', { class: 'parecidas', 'aria-live': 'polite' });
    desenharParecidas(e, parecidas);
    return [
      h('div', { class: 'item-topo' }, rotuloForma(e.forma),
        e.forca === 'fraca' ? h('span', { class: 'chip' }, '🔵 confira') : null),
      u.campo('O que foi prometido', texto),
      u.campo('Prazo', prazo, dicaPrazo),
      alternativas.length
        ? h('p', { class: 'dica' }, 'A data pode ser outra: ', alternativas.map((d, i) => [i ? ' ' : '',
          h('button', {
            type: 'button', class: 'botao botao-texto',
            onclick: () => { e.prazo = d; e.precisao = 'dia'; prazo.value = d; u.falar(`Prazo trocado para ${u.dataBr(d)}.`); },
          }, `${u.dataBr(d)}?`)]))
        : null,
      e.pessoa ? h('p', { class: 'sutil' }, `Para: ${e.pessoa}`) : null,
      e.motivo ? h('p', { class: 'dica' }, e.motivo) : null,
      u.citacao(e.trecho),
      parecidas,
    ];
  }

  // Já existe uma promessa igual em aberto? (consulta uma vez por extra; a resposta fica guardada)
  function desenharParecidas(e, caixa) {
    const mostrar = (itens) => {
      u.limpar(caixa);
      const igual = lista(itens).find((p) => p && p.igual);
      const parecida = igual || lista(itens)[0];
      if (!parecida) return;
      u.anexar(caixa, h('p', { class: 'dica' },
        igual ? 'Já existe uma promessa igual em aberto: ' : 'Parecida com uma promessa em aberto: ',
        `“${parecida.texto}”`, parecida.feita_em ? ` (${u.dataBr(parecida.feita_em)})` : '', '. ',
        igual ? 'Confirmar aqui não cria outra.' : ''));
    };
    if (Array.isArray(e._parecidas)) {
      mostrar(e._parecidas);
      return;
    }
    const texto = String(e.texto || e.resumo || '').trim();
    if (!texto || e._buscandoParecidas) return;
    e._buscandoParecidas = true;
    api('POST', '/promessas/parecidas', { texto })
      .then((r) => { e._parecidas = lista(r); if (caixa.isConnected) mostrar(e._parecidas); })
      .catch(() => { e._parecidas = []; })
      .finally(() => { e._buscandoParecidas = false; });
  }

  // "Como prometi": você escolhe qual promessa em aberto foi cumprida (ou nenhuma).
  function revisaoCumprida(e, ctx) {
    const caixa = h('div', { class: 'candidatas' });
    const desenharOpcoes = (candidatas) => {
      u.limpar(caixa);
      if (!candidatas.length) {
        u.anexar(caixa, h('p', { class: 'dica' }, 'Não achei promessa em aberto parecida com isso. Confirmar aqui não muda nada; dá para marcar uma promessa na tela Promessas.'));
        return;
      }
      const grupo = u.novoId('cumprida');
      const opcao = (valor, rotulo, detalhe) => {
        const id = u.novoId('cumprida-op');
        return h('label', { class: 'opcao', for: id },
          h('input', {
            type: 'radio', id, name: grupo, value: String(valor),
            checked: valor === '' ? e.promessa_id == null : e.promessa_id === valor,
            onchange: () => {
              if (valor === '') delete e.promessa_id;
              else e.promessa_id = valor;
              u.falar(valor === '' ? 'Nenhuma promessa escolhida.' : 'Promessa escolhida.');
            },
          }),
          h('span', null, rotulo, detalhe ? h('span', { class: 'sutil' }, ` ${detalhe}`) : null));
      };
      u.anexar(caixa, h('fieldset', { class: 'opcoes-relacao' },
        h('legend', null, 'Qual promessa foi cumprida?'),
        h('div', { class: 'opcoes' },
          candidatas.map((c) => opcao(c.id, `“${c.texto}”`,
            [c.feita_em ? `de ${u.dataBr(c.feita_em)}` : '', lista(c.motivos).length ? `· ${lista(c.motivos).join(' e ')}` : '']
              .filter(Boolean).join(' '))),
          opcao('', 'Nenhuma destas')),
        h('p', { class: 'dica' }, 'Só a escolhida é marcada como cumprida, e só se você confirmar.')));
    };
    if (Array.isArray(e._candidatas)) {
      desenharOpcoes(e._candidatas);
    } else {
      u.anexar(caixa, u.carregando('Procurando as promessas em aberto…'));
      api('POST', '/promessas/candidatas', { texto: e.trecho })
        .then((r) => {
          e._candidatas = lista(r);
          // Sugere a mais provável só quando a ação E o assunto batem (você ainda confirma).
          if (e.promessa_id === undefined && e._candidatas[0] && e._candidatas[0].pontos >= 70) e.promessa_id = e._candidatas[0].id;
          if (caixa.isConnected) desenharOpcoes(e._candidatas);
          else if (ctx && typeof ctx.redesenhar === 'function') ctx.redesenhar();
        })
        .catch((erro) => {
          e._candidatas = [];
          if (erro && erro.bloqueio) return;
          if (caixa.isConnected) {
            u.limpar(caixa);
            u.anexar(caixa, h('p', { class: 'erro-campo' }, 'Não consegui procurar as promessas agora.'));
          }
        });
    }
    return [
      h('div', { class: 'item-topo' }, h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, '💙 '), 'Promessa cumprida')),
      e.motivo ? h('p', { class: 'dica' }, e.motivo) : null,
      u.citacao(e.trecho),
      caixa,
    ];
  }

  // ---------------------------------------------------------------- cartões do Hoje

  function porqueDe(c) {
    const ids = lista(c.registro_ids).filter((r) => r != null);
    if (!c.porque && !ids.length) return null;
    return h('details', { class: 'porque' }, h('summary', null, 'Por quê?'),
      c.porque ? h('p', null, c.porque) : null,
      ids.length ? h('p', { class: 'links-registros' }, ids.map((rid, i) => [i ? ' · ' : '',
        u.linkRegistro(rid, ids.length > 1 ? `ver registro (${i + 1} de ${ids.length})` : 'ver o registro')])) : null);
  }

  function cartaoPrazo(c, ctx) {
    const p = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '🤝 '), c.texto),
      lista(p.conflitos).length ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '⚠️ '), p.conflitos[0].texto) : null,
      h('div', { class: 'alerta-acoes' },
        porqueDe(c) || h('span'),
        h('div', { class: 'acoes' },
          p.id != null ? botaoAcao('✔️ Cumpri', 'botao botao-secundario botao-pequeno', () => mudarStatus(p.id, 'cumprida'),
            () => { ctx.remover(); u.avisar('Que bom! 💙 Marquei como cumprida.'); }) : null,
          h('a', { href: '#promessas', class: 'botao botao-texto' }, 'Ver promessas'),
          ctx.botaoDispensar())),
    ];
  }

  function cartaoGentil(c, ctx) {
    const p = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '🤝 '), c.texto),
      h('div', { class: 'alerta-acoes' },
        porqueDe(c) || h('span'),
        h('div', { class: 'acoes' },
          p.id != null ? botaoAcao('✔️ Já cumpri', 'botao botao-secundario botao-pequeno', () => mudarStatus(p.id, 'cumprida'),
            () => { ctx.remover(); u.avisar('Que bom! 💙 Marquei como cumprida.'); }) : null,
          p.id != null ? botaoAcao('🙅 Deixar de lado', 'botao botao-texto', () => mudarStatus(p.id, 'desisti'),
            () => { ctx.remover(); u.avisar('Tudo bem mudar de ideia.'); }) : null,
          ctx.botaoDispensar())),
      h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: quem sabe o momento é você.'),
    ];
  }

  function cartaoCumpriuTalvez(c, ctx) {
    const p = c.dados || {};
    const ind = p.indicio || {};
    return [
      h('p', { class: 'alerta-texto' }, c.texto),
      ind.trecho ? u.citacao(ind.trecho)
        : (ind.discreto ? h('p', { class: 'sutil' }, '🔒 Um registro reservado (abra para ver).') : null),

      h('div', { class: 'alerta-acoes' },
        porqueDe(c) || h('span'),
        h('div', { class: 'acoes' },
          p.id != null && ind.registro_id != null
            ? botaoAcao('✔️ Sim, cumpri', 'botao botao-secundario botao-pequeno', () => mudarStatus(p.id, 'cumprida', ind.registro_id),
              () => { ctx.remover(); u.avisar('Que bom! 💙 Marquei como cumprida.'); })
            : null,
          botaoAcao('Ainda não', 'botao botao-texto', () => ctx.dispensar(), () => { u.falar('Combinado: este aviso não volta.'); }))),
    ];
  }

  E.registrarMenu('promessas', 'Promessas', '🤝');
  E.registrarTela('promessas', telaPromessas);
  E.registrarRevisaoExtra('promessa', revisaoPromessa);
  E.registrarRevisaoExtra('promessa_cumprida', revisaoCumprida);
  E.registrarCartaoHoje('promessa_prazo', cartaoPrazo);
  E.registrarCartaoHoje('promessa_gentil', cartaoGentil);
  E.registrarCartaoHoje('promessa_cumprida_talvez', cartaoCumpriuTalvez);
})();
