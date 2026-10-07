/* Área 🪞 Eu (DESIGN.md, seção 20.9): a tela do recurso "eu" — como estou sendo com ela.
 *
 * Encaixa no app por window.EntreNos:
 * - menu e telas #eu (visão geral), #eu/checkin[/AAAA-Www], #eu/mes[/AAAA-MM], #eu/aprendizados,
 *   #eu/reparos e #eu/cartas;
 * - miolo dos extras na revisão: atitude, aprendizado, reparo (só a sua parte), admiração e o lembrete
 *   de um aprendizado seu que combina com o texto;
 * - cartões do Hoje (check-in, reflexão, intenção, "vocês conversaram?", tema repetido, desentendimentos
 *   intensos, aprendizado, cuidar de você, carta) e a seção "🪞 Área Eu" em Ajustes.
 * Sem nota, sem placar: a presença aparece só como emoji, e tudo cita de onde veio (links para os
 * registros). Nada daqui vai para a IA. Tudo por nós de texto (h), sem HTML montado nem estilo inline. */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E || !E.util) return;
  const { api, h, selo, navegar } = E;
  const u = E.util;

  const RE_SEMANA = /^\d{4}-W\d{2}$/;
  const RE_MES = /^\d{4}-\d{2}$/;
  const SUBTELAS = [
    ['', '🪞', 'Visão geral'], ['checkin', '📝', 'Check-in'], ['mes', '📆', 'Mês'],
    ['aprendizados', '💡', 'Aprendizados'], ['reparos', '🌧️', 'Desentendimentos'], ['cartas', '💌', 'Cartas'],
  ];
  const INTENSIDADES = [[1, 'Leve'], [2, 'Média'], [3, 'Forte']];
  const SO_SEU = '🔒 Só seu: nada da área Eu vai para a IA. Sem nota, sem placar.';

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function erroNaTela(e) {
    if (e && e.bloqueio) return;
    u.avisarErro(e);
  }

  function enc(v) {
    return encodeURIComponent(String(v));
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

  function linksRegistros(ids) {
    const validos = lista(ids).filter((r) => r != null);
    if (!validos.length) return null;
    return h('p', { class: 'links-registros' }, validos.map((rid, i) => [i ? ' · ' : '',
      u.linkRegistro(rid, validos.length > 1 ? `ver registro (${i + 1} de ${validos.length})` : 'ver o registro')]));
  }

  function porqueDe(c) {
    const ids = lista(c.registro_ids);
    if (!c.porque && !ids.length) return null;
    return h('details', { class: 'porque' }, h('summary', null, 'Por quê?'),
      c.porque ? h('p', null, c.porque) : null, linksRegistros(ids));
  }

  function tipoRotulo(emoji, nome) {
    return h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, `${emoji} `), nome);
  }

  function data(iso) {
    return iso ? u.dataBr(iso) : '';
  }

  // Depois de uma citação que já termina em ponto, não vem outro ("…trabalho.” Talvez…").
  function fim(texto) {
    return /[.!?…]["”]?$/.test(String(texto || '').trim()) ? '' : '.';
  }

  function subnav(atual) {
    return h('nav', { 'aria-label': 'Partes da área Eu' },
      h('ul', { class: 'chips' }, SUBTELAS.map(([rota, emoji, nome]) => h('li', null,
        h('a', {
          href: rota ? `#eu/${rota}` : '#eu', class: 'chip chip-link',
          'aria-current': rota === atual ? 'page' : null,
        }, h('span', { 'aria-hidden': 'true' }, `${emoji} `), nome)))));
  }

  function topo(main, titulo, subtitulo, emoji, atual) {
    u.anexar(main, u.cabecalho(titulo, subtitulo, emoji), subnav(atual),
      h('p', { class: 'dica', role: 'note' }, SO_SEU));
  }

  function tirarCarregando(main) {
    const c = main.querySelector('.carregando');
    if (c) c.remove();
  }

  function selosDe(certeza) {
    return certeza ? selo(certeza) : null;
  }

  // ---------------------------------------------------------------- espelho (linhas com citação)

  function linhaEspelho(l) {
    const detalhes = lista(l.detalhes);
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, h('span', { 'aria-hidden': 'true' }, l.emoji || '•'),
        h('span', { class: 'entrada-texto' }, l.texto)),
      detalhes.length
        ? h('details', { class: 'porque' }, h('summary', null, l.pergunta ? 'De onde veio' : 'Ver de onde veio'),
          h('ul', { class: 'lista-simples' }, detalhes.map((d) => h('li', null,
            d.certeza ? [selo(d.certeza), ' '] : null,
            d.emoji ? `${d.emoji} ` : null,
            d.texto || '',
            d.data ? h('span', { class: 'sutil' }, ` · ${data(d.data)}`) : null,
            lista(d.combina).length ? h('p', { class: 'dica' }, '💙 Combina com o que ela disse: ',
              `“${d.combina[0].trecho}”`, d.combina[0].registro_id != null ? [' ', u.linkRegistro(d.combina[0].registro_id, 'ver')] : null) : null,
            d.registro_id != null ? [' ', u.linkRegistro(d.registro_id, 'ver registro')] : null,
            d.retorno_registro_id != null ? [' · ', u.linkRegistro(d.retorno_registro_id, 'quando você voltou ao assunto')] : null))))
        : linksRegistros(l.registro_ids));
  }

  function listaEspelho(linhas, vazio) {
    const ls = lista(linhas);
    if (!ls.length) return u.vazioPequeno(vazio || 'Ainda não há registros nestes dias para olhar.');
    return h('ul', { class: 'lista-entradas' }, ls.map(linhaEspelho));
  }

  // ---------------------------------------------------------------- escala de presença (só sua)

  function escalaPresenca(escala, valor, aoMudar) {
    const idLegenda = u.novoId('eu-presenca');
    let atual = valor == null ? null : Number(valor);
    const botoes = lista(escala).map((op) => {
      const b = h('button', {
        type: 'button', class: 'botao botao-secundario botao-escolha', 'aria-pressed': String(atual === op.valor),
        'aria-label': op.rotulo, title: op.rotulo,
      }, h('span', { 'aria-hidden': 'true' }, op.emoji));
      b.addEventListener('click', () => {
        atual = atual === op.valor ? null : op.valor;
        botoes.forEach((x, i) => x.setAttribute('aria-pressed', String(lista(escala)[i].valor === atual)));
        aoMudar(atual);
        u.falar(atual == null ? 'Sem escolha de presença.' : `Escolhido: ${op.rotulo}.`);
      });
      return b;
    });
    return h('div', { class: 'campo' },
      h('p', { id: idLegenda, class: 'titulo-pequeno' }, 'Como você se sentiu presente com ela nesta semana?'),
      h('div', { class: 'opcoes', role: 'group', 'aria-labelledby': idLegenda }, botoes),
      h('p', { class: 'dica' }, 'Só sua: nunca vira nota dela, e não aparece como número em lugar nenhum.'));
  }

  // ---------------------------------------------------------------- formulário de check-in (semanal e mensal)

  function formCheckin(vista, aoSalvar, podeResponder = true) {
    const respostas = {};
    let presenca = vista.feito ? vista.feito.presenca : null;
    let mudouPresenca = false;
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const campos = lista(vista.perguntas).map((p) => {
      const id = u.novoId(`eu-q-${p.id}`);
      const caixa = h('textarea', {
        id, rows: '3', maxlength: '2000', value: p.resposta || '', disabled: !podeResponder,
        oninput: (ev) => { respostas[p.id] = ev.target.value; },
      });
      caixa.classList.add('texto-livre');
      const bloco = u.campo(p.texto, caixa, p.porque || null);
      const links = linksRegistros(p.registro_ids);
      if (links) u.anexar(bloco, links);
      return bloco;
    });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario', disabled: !podeResponder },
      vista.feito ? 'Salvar mudanças' : (vista.tipo === 'mensal' ? 'Guardar a reflexão' : 'Guardar o check-in'));
    const form = h('form', { class: 'form-item', novalidate: true },
      campos,
      vista.tipo === 'semanal' ? escalaPresenca(vista.escala, presenca, (v) => { presenca = v; mudouPresenca = true; }) : null,
      erro,
      h('div', { class: 'acoes' }, botao));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      erro.textContent = '';
      const corpo = { tipo: vista.tipo, periodo: vista.periodo, respostas };
      if (vista.tipo === 'semanal' && (mudouPresenca || !vista.feito)) corpo.presenca = presenca;
      botao.disabled = true;
      try {
        const r = await api('POST', '/eu/checkin', corpo);
        u.avisar(vista.tipo === 'semanal' ? 'Guardado. Obrigado por tirar esse minuto para você. 💙' : 'Reflexão guardada. 💙');
        await aoSalvar(r);
      } catch (e) {
        botao.disabled = false;
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    return form;
  }

  // ---------------------------------------------------------------- intenção do mês

  function blocoIntencao(dados, recarregar) {
    const atual = dados && dados.atual;
    const caixa = h('div', { class: 'cartao' }, h('h2', null, h('span', { 'aria-hidden': 'true' }, '🎯 '), `Intenção de ${dados.rotulo || 'este mês'}`));
    if (atual) {
      u.anexar(caixa,
        h('p', { class: 'alerta-texto' }, `“${atual.texto}”`),
        atual.revisao ? h('p', { class: 'sutil' }, `Como foi: ${atual.revisao}`) : null,
        formRevisao(atual, recarregar),
        h('div', { class: 'acoes' },
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => formEscrever(caixa, dados, recarregar, atual.texto) }, '✏️ Trocar'),
          botaoAcao('🗑️ Tirar', 'botao botao-texto botao-perigo-texto', () => api('DELETE', `/eu/intencao/${enc(atual.id)}`),
            () => { u.avisar('Intenção tirada.'); recarregar(); })));
      return caixa;
    }
    const sugestoes = lista(dados.sugestoes);
    const deDados = sugestoes.filter((s) => s.de_dados);
    u.anexar(caixa,
      h('p', { class: 'sutil' }, 'Uma intenção pequena para o mês, escolhida por você. No meio do mês o Hoje lembra, com carinho.'),
      sugestoes.length ? h('ul', { class: 'lista-entradas' }, sugestoes.map((s) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, h('span', { 'aria-hidden': 'true' }, s.de_dados ? '💡' : '🌱'),
          h('span', { class: 'entrada-texto' }, s.texto)),
        h('p', { class: 'dica' }, s.porque), linksRegistros(s.registro_ids),
        h('div', { class: 'acoes' }, botaoAcao('Escolher esta', 'botao botao-secundario botao-pequeno',
          () => api('POST', '/eu/intencao', { mes: dados.mes, texto: s.texto, sugestao: s.id }),
          () => { u.avisar('Intenção escolhida. 💙'); recarregar(); }))))) : null,
      !deDados.length && sugestoes.length ? h('p', { class: 'dica' }, 'Ainda não há registros suficientes para sugerir algo específico: estas são ideias gerais.') : null,
      h('div', { class: 'acoes' }, h('button', { type: 'button', class: 'botao botao-texto', onclick: () => formEscrever(caixa, dados, recarregar, '') }, '✍️ Escrever a minha')));
    return caixa;
  }

  // "Como está indo?": o lembrete do meio do mês leva até aqui (PATCH /eu/intencao/{id} {revisao}).
  function formRevisao(atual, recarregar) {
    const id = u.novoId('eu-intencao-revisao');
    const caixa = h('textarea', { id, rows: '2', maxlength: '2000', value: atual.revisao || '',
      placeholder: 'Ex.: notei mais as coisas pequenas que a deixam feliz' });
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const form = h('form', { class: 'form-item', novalidate: true },
      u.campo(atual.revisao ? 'Como está indo? (dá para mudar)' : 'Como está indo?', caixa,
        'Só para você: ajuda a olhar o mês com carinho, sem nota.'),
      erro,
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-secundario botao-pequeno' }, 'Guardar')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      try {
        await api('PATCH', `/eu/intencao/${enc(atual.id)}`, { revisao: caixa.value.trim() || null });
        u.avisar('Guardado. 💙');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    return form;
  }

  function formEscrever(caixa, dados, recarregar, valor) {
    // Um formulário só por cartão: tocar de novo em "Trocar" ou "Escrever a minha" leva ao que já está aberto.
    const aberto = caixa.querySelector('form.form-intencao');
    if (aberto) {
      const campo = aberto.querySelector('input');
      if (campo) campo.focus();
      return;
    }
    const id = u.novoId('eu-intencao');
    const entrada = h('input', { id, type: 'text', maxlength: '200', value: valor || '', placeholder: 'Ex.: notar o que a deixa feliz' });
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const form = h('form', { class: ['form-item', 'form-intencao'], novalidate: true },
      u.campo('Minha intenção para o mês', entrada), erro,
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, 'Guardar'),
        h('button', { type: 'button', class: 'botao botao-texto', onclick: () => recarregar() }, 'Cancelar')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!entrada.value.trim()) {
        erro.textContent = 'Escreva a intenção em poucas palavras.';
        entrada.focus();
        return;
      }
      try {
        await api('POST', '/eu/intencao', { mes: dados.mes, texto: entrada.value });
        u.avisar('Intenção guardada. 💙');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    u.anexar(caixa, form);
    entrada.focus();
  }

  // ---------------------------------------------------------------- tela: visão geral

  function itemAtitude(a, aoApagar) {
    const combina = lista(a.combina)[0];
    const descompasso = lista(a.descompasso)[0];
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, h('span', { class: 'chip' }, a.jeito_nome || 'Atitude'),
        h('span', { class: 'entrada-texto' }, a.texto)),
      combina ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '💙 '),
        `Combina com o que ela disse que ajuda${combina.data ? ` (${data(combina.data)})` : ''}: `, selosDe(combina.certeza), ' ',
        `“${combina.trecho}”`, combina.registro_id != null ? [' ', u.linkRegistro(combina.registro_id, 'ver')] : null) : null,
      descompasso ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '🤔 '),
        'Ela disse algo diferente sobre isso: ', `“${descompasso.trecho}”${fim(descompasso.trecho)} Como foi?`) : null,
      h('p', { class: 'entrada-rodape sutil' }, data(a.data),
        a.registro_id != null ? [' · ', u.linkRegistro(a.registro_id, 'ver registro')] : null,
        aoApagar ? [' · ', botaoAcao('Apagar', 'botao botao-texto botao-perigo-texto', () => api('DELETE', `/eu/atitudes/${enc(a.id)}`), aoApagar)] : null));
  }

  function formAtitude(recarregar) {
    const idTexto = u.novoId('eu-atitude');
    const idData = u.novoId('eu-atitude-data');
    const texto = h('input', { id: idTexto, type: 'text', maxlength: '300', placeholder: 'Ex.: fiz um chá pra ela quando chegou cansada' });
    const quando = h('input', { id: idData, type: 'date', value: u.hojeIso(), max: u.hojeIso() });
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const form = h('form', { class: 'form-item', novalidate: true },
      h('p', { class: 'sutil' }, 'Quase sempre elas aparecem sozinhas quando você registra “levei…”, “fiquei com ela…”. Aqui é para anotar direto.'),
      u.campo('O que você fez', texto), u.campo('Quando', quando), erro,
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, 'Guardar')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!texto.value.trim()) {
        erro.textContent = 'Escreva o que você fez.';
        texto.focus();
        return;
      }
      try {
        await api('POST', '/eu/atitudes', { texto: texto.value, data: quando.value || null });
        u.avisar('Anotado. 💙');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    return h('details', { class: 'cartao mais-detalhes' }, h('summary', null, '➕ Anotar uma atitude'), form);
  }

  function formAdmiracao(recarregar) {
    const id = u.novoId('eu-admiracao');
    const texto = h('input', { id, type: 'text', maxlength: '300', placeholder: 'Ex.: o jeito que ela cuida da avó' });
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const form = h('form', { class: 'form-item', novalidate: true },
      u.campo('O que você admira nela (uma linha)', texto), erro,
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, 'Guardar')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!texto.value.trim()) {
        erro.textContent = 'Escreva em uma linha o que você admira.';
        texto.focus();
        return;
      }
      try {
        await api('POST', '/eu/admiracoes', { texto: texto.value });
        u.avisar('Guardado. Isso pode virar uma carta um dia. ✨');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    return form;
  }

  function botoesConversaram(r, depois) {
    return h('div', { class: 'acoes' },
      botaoAcao('💬 Sim, conversamos', 'botao botao-secundario botao-pequeno', () => api('PATCH', `/eu/reparos/${enc(r.id)}`, { conversamos: true }),
        () => { u.avisar('Que bom que vocês conversaram. 💙'); depois(); }),
      botaoAcao('Ainda não', 'botao botao-texto', () => api('PATCH', `/eu/reparos/${enc(r.id)}`, { conversamos: false }),
        () => { u.avisar('Tudo bem. Quando fizer sentido, um momento calmo ajuda.'); depois(); }));
  }

  async function telaVisao(main, atual) {
    topo(main, 'Eu', 'Como estou sendo com ela.', '🪞', '');
    u.anexar(main, u.carregando());
    const v = (await api('GET', '/eu')) || {};
    if (!atual()) return;
    tirarCarregando(main);
    const recarregar = () => navegar('#eu');
    if (v.momento_dela) {
      u.anexar(main, h('p', { class: 'nota-cuidado', role: 'note' }, h('span', { 'aria-hidden': 'true' }, '🫶 '),
        `${v.momento_dela.texto}${fim(v.momento_dela.texto)} Talvez esta semana o mais importante seja estar perto.`,
        v.momento_dela.registro_id != null ? [' ', u.linkRegistro(v.momento_dela.registro_id, 'ver registro')] : null));
    }
    if (v.cuidar) {
      u.anexar(main, h('div', { class: 'cartao' },
        h('h2', null, h('span', { 'aria-hidden': 'true' }, '💙 '), 'Cuidar de você'),
        h('p', { class: 'alerta-texto' }, v.cuidar.texto), h('p', { class: 'sutil' }, v.cuidar.porque)));
    }
    const semana = v.semana || {};
    u.anexar(main, h('div', { class: 'cartao' },
      h('h2', null, h('span', { 'aria-hidden': 'true' }, '📝 '), `Check-in da ${semana.rotulo || 'semana'}`),
      semana.feito
        ? h('p', null, `Feito ${semana.emoji || ''}. Dá para completar quando quiser.`)
        : [h('p', { class: 'sutil' }, 'Cerca de um minuto, com perguntas escolhidas pela sua semana:'),
          h('ul', { class: 'lista-simples' }, lista(semana.perguntas).map((p) => h('li', null, `• ${p.texto}`)))],
      h('div', { class: 'acoes' }, h('a', { href: `#eu/checkin/${enc(semana.periodo || '')}`, class: semana.feito ? 'botao botao-texto' : 'botao botao-primario' },
        semana.feito ? 'Ver ou completar' : 'Fazer o check-in'))));
    const mes = v.mes || {};
    u.anexar(main, h('div', { class: 'cartao' },
      h('h2', null, h('span', { 'aria-hidden': 'true' }, '📆 '), `Reflexão de ${mes.rotulo || 'mês'}`),
      h('p', { class: 'sutil' }, mes.feito ? 'Feita. O espelho do mês e o que ela disse sobre vocês continuam lá.'
        : 'O espelho do mês, a sua reflexão e, lado a lado, o que ela disse sobre vocês.'),
      h('div', { class: 'acoes' }, h('a', { href: `#eu/mes/${enc(mes.mes || '')}`, class: 'botao botao-secundario' }, mes.feito ? 'Ver o mês' : 'Abrir a reflexão'))));
    u.anexar(main, blocoIntencao(v.intencao || {}, recarregar));
    u.anexar(main, u.secao('🔍 Espelho desta semana', listaEspelho(v.espelho_semana,
      'Nada registrado nesta semana ainda. O espelho só mostra o que está nos seus registros.'),
    h('p', { class: 'dica' }, 'Observações tiradas dos registros, com o link de onde veio cada uma. Não é avaliação.')));
    const reparos = v.reparos || {};
    const acompanhar = lista(reparos.acompanhar);
    if (acompanhar.length || lista(reparos.temas).length || reparos.intensos) {
      u.anexar(main, u.secao('🌧️ Desentendimentos',
        acompanhar.map((r) => h('div', { class: 'cartao' },
          h('p', { class: 'alerta-texto' }, `Vocês conversaram sobre aquilo ${r.de_quando || `de ${data(r.data)}`}?`),
          h('p', { class: 'sutil' }, r.aconteceu), botoesConversaram(r, recarregar))),
        lista(reparos.temas).map((t) => h('p', { class: 'nota-cuidado' }, t.texto, ' ', h('span', { class: 'sutil' }, t.porque))),
        reparos.intensos ? h('p', { class: 'nota-cuidado' }, reparos.intensos.texto) : null,
        h('p', null, h('a', { href: '#eu/reparos' }, 'Ver os desentendimentos'))));
    }
    const descompassos = lista(v.descompassos);
    if (descompassos.length) {
      u.anexar(main, u.secao('🤔 Para pensar com carinho',
        h('ul', { class: 'lista-entradas' }, descompassos.map((d) => h('li', { class: 'entrada' },
          h('p', { class: 'entrada-texto' }, d.texto), d.apoio ? h('p', { class: 'dica' }, 'Certeza do que ela disse: ', selosDe(d.apoio.certeza)) : null,
          linksRegistros(d.registro_ids)))),
        h('p', { class: 'dica possibilidade' }, 'É uma pergunta, não uma cobrança: só você sabe como foi.')));
    }
    u.anexar(main, u.secao('🫶 Minhas atitudes',
      lista(v.atitudes).length ? h('ul', { class: 'lista-entradas' }, lista(v.atitudes).map((a) => itemAtitude(a, () => { u.avisar('Apagado.'); recarregar(); })))
        : u.vazioPequeno('Nenhuma atitude anotada nos últimos 60 dias. Elas aparecem quando você registra algo como “levei café pra ela”.'),
      formAtitude(recarregar)));
    const aprendizados = lista(v.aprendizados);
    u.anexar(main, u.secao('💡 Aprendizados',
      aprendizados.length ? h('ul', { class: 'lista-entradas' }, aprendizados.map((a) => h('li', { class: 'entrada' },
        h('span', { class: 'entrada-texto' }, a.texto),
        lista(a.voltou).length ? h('p', { class: 'dica' }, `Voltou ${lista(a.voltou).length === 1 ? '1 vez' : `${lista(a.voltou).length} vezes`} nos últimos 30 dias.`) : null)))
        : u.vazioPequeno('Quando você escreve “aprendi que…” ou “da próxima vez…”, o aprendizado fica aqui e volta na hora certa.'),
      h('p', null, h('a', { href: '#eu/aprendizados' }, v.total_aprendizados ? `Ver todos (${v.total_aprendizados})` : 'Anotar um aprendizado'))));
    u.anexar(main, u.secao('✨ O que eu admiro nela',
      lista(v.admiracoes).length ? h('ul', { class: 'lista-simples' }, lista(v.admiracoes).map((a) => h('li', null, `“${a.texto}”`,
        h('span', { class: 'sutil' }, ` · ${data(a.data)}`),
        ' ', botaoAcao('Apagar', 'botao botao-texto botao-perigo-texto', () => api('DELETE', `/eu/admiracoes/${enc(a.id)}`), () => { u.avisar('Apagado.'); recarregar(); }))))
        : u.vazioPequeno('Uma linha de cada vez. Isso alimenta as cartas e a reflexão do mês.'),
      formAdmiracao(recarregar)));
    const presencas = lista(v.presencas);
    if (presencas.length) {
      u.anexar(main, h('details', { class: 'cartao mais-detalhes' },
        h('summary', null, '🪞 Seus últimos check-ins'),
        h('ul', { class: 'lista-simples' }, presencas.map((p) => h('li', null, `${p.rotulo}: `, h('span', { role: 'img', 'aria-label': 'presença marcada' }, p.emoji)))),
        h('p', { class: 'dica' }, 'Só para você se acompanhar. Não é nota.')));
    }
  }

  // ---------------------------------------------------------------- tela: check-in

  async function telaCheckin(main, periodo, atual) {
    topo(main, 'Check-in da semana', 'Cerca de um minuto. As perguntas são escolhidas pela sua semana real.', '📝', 'checkin');
    u.anexar(main, u.carregando());
    const rota = RE_SEMANA.test(periodo || '') ? `/eu/checkin?periodo=${enc(periodo)}` : '/eu/checkin';
    const v = (await api('GET', rota)) || {};
    if (!atual()) return;
    tirarCarregando(main);
    u.anexar(main,
      h('div', { class: 'acoes' },
        v.anterior ? h('a', { href: `#eu/checkin/${enc(v.anterior)}`, class: 'botao botao-texto' }, '← semana anterior') : null,
        v.seguinte ? h('a', { href: `#eu/checkin/${enc(v.seguinte)}`, class: 'botao botao-texto' }, 'semana seguinte →') : null),
      h('div', { class: 'cartao' },
        h('h2', null, u.capitalizar(v.rotulo || 'esta semana')),
        v.feito ? h('p', { class: 'sutil' }, `Feito ${v.feito.emoji || ''}. Dá para completar ou mudar.`) : null,
        v.pode_responder === false ? h('p', { class: 'dica' }, 'Esta semana é antiga demais para responder agora; dá para ler.') : null,
        formCheckin(v, () => navegar(`#eu/checkin/${enc(v.periodo)}`), v.pode_responder !== false)),
      v.feito ? h('div', { class: 'acoes' }, botaoAcao('🗑️ Apagar este check-in', 'botao botao-texto botao-perigo-texto', async () => {
        const ok = await u.confirmar({ titulo: 'Apagar este check-in?', texto: 'As respostas desta semana saem do diário e das cópias automáticas. Não dá para desfazer.', ok: 'Apagar', perigo: true });
        if (!ok) throw Object.assign(new Error(''), { bloqueio: true });
        return api('DELETE', `/eu/checkins/${enc(v.feito.id)}`);
      }, () => { u.avisar('Check-in apagado.'); navegar('#eu/checkin'); })) : null);
  }

  // ---------------------------------------------------------------- tela: mês

  function itemEu(x) {
    return h('li', { class: 'entrada' },
      h('p', { class: 'sutil' }, x.texto, x.rotulo ? ` · ${x.rotulo}` : ''),
      h('p', { class: 'texto-livre' }, x.resposta));
  }

  function itemEla(x) {
    return h('li', { class: 'entrada' },
      h('p', { class: 'sutil' }, x.pergunta || 'Pergunta sobre vocês dois', x.data ? ` · ${data(x.data)}` : ''),
      // Recusou: só isso (sem "a resposta está no registro"). Registro apagado: sem a frase nem o link.
      x.recusou ? h('p', { class: 'dica' }, 'Ela preferiu não responder. Tudo bem.')
        : (x.resposta ? [selosDe(x.certeza), ' ', u.citacao(x.resposta)]
          : (x.registro_id != null ? h('p', { class: 'dica' }, 'A resposta está no registro.') : null)),

      x.registro_id != null ? u.linkRegistro(x.registro_id, 'ver registro') : null);
  }

  function blocoLadoALado(lado) {
    const pares = lista(lado.pares);
    const meus = lista(lado.eu);
    const dela = lista(lado.ela);
    const partes = [];
    pares.forEach((p) => {
      partes.push(h('div', { class: 'cartao' }, h('h3', null, p.titulo),
        h('div', { class: 'grade-2' },
          h('div', null, h('h4', null, 'O que eu achei'), h('ul', { class: 'lista-entradas' }, lista(p.eu).map(itemEu))),
          h('div', null, h('h4', null, 'O que ela disse'), h('ul', { class: 'lista-entradas' }, lista(p.ela).map(itemEla))))));
    });
    if (meus.length || dela.length) {
      partes.push(h('div', { class: 'grade-2' },
        h('div', null, h('h4', null, 'O que eu achei'), meus.length ? h('ul', { class: 'lista-entradas' }, meus.map(itemEu)) : u.vazioPequeno('Sem outras respostas suas neste mês.')),
        h('div', null, h('h4', null, 'O que ela disse'), dela.length ? h('ul', { class: 'lista-entradas' }, dela.map(itemEla)) : u.vazioPequeno('Sem outras respostas dela neste mês.'))));
    }
    if (!pares.length && !meus.length && !dela.length) partes.push(u.vazioPequeno('Ainda não há check-ins seus nem respostas dela sobre vocês dois neste mês.'));
    if (lado.sugestao_pergunta) {
      partes.push(h('div', { class: 'nota-cuidado' },
        h('p', null, h('span', { 'aria-hidden': 'true' }, '💞 '), lado.sugestao_pergunta.porque),
        h('p', null, h('strong', null, `“${lado.sugestao_pergunta.texto}”`)),
        h('p', { class: 'sutil possibilidade' }, lado.sugestao_pergunta.possibilidade || 'É só uma possibilidade.')));
    }
    return partes;
  }

  async function telaMes(main, mes, atual) {
    topo(main, 'Reflexão do mês', 'O espelho do mês, a sua reflexão e o que ela disse sobre vocês.', '📆', 'mes');
    u.anexar(main, u.carregando());
    const rota = RE_MES.test(mes || '') ? `/eu/mes?mes=${enc(mes)}` : '/eu/mes';
    const v = (await api('GET', rota)) || {};
    if (!atual()) return;
    tirarCarregando(main);
    const recarregar = () => navegar(`#eu/mes/${enc(v.mes)}`);
    const intencao = await api('GET', `/eu/intencao?mes=${enc(v.mes)}`).catch(() => null);
    if (!atual()) return;
    const espelho = v.espelho || {};
    u.anexar(main,
      h('div', { class: 'acoes' },
        v.anterior ? h('a', { href: `#eu/mes/${enc(v.anterior)}`, class: 'botao botao-texto' }, '← mês anterior') : null,
        v.seguinte ? h('a', { href: `#eu/mes/${enc(v.seguinte)}`, class: 'botao botao-texto' }, 'mês seguinte →') : null),
      h('h2', null, u.capitalizar(v.rotulo || '')),
      intencao ? blocoIntencao(intencao, recarregar) : null,
      u.secao('🔍 Espelho do mês', listaEspelho(espelho.linhas),
        lista(espelho.tendencia).length ? h('details', { class: 'porque' }, h('summary', null, 'Ao longo dos meses'),
          h('ul', { class: 'lista-simples' }, lista(espelho.tendencia).map((t) => h('li', null, t)))) : null),
      u.secao('💞 Lado a lado', h('p', { class: 'sutil' }, 'O que você respondeu nos check-ins e o que ela respondeu às perguntas sobre vocês dois (🟢 quando foi ela que disse).'),
        blocoLadoALado(v.lado_a_lado || {})),
      u.secao('📝 Sua reflexão', v.pode_responder === false
        ? h('p', { class: 'dica' }, 'Este mês é antigo demais para responder agora.')
        : formCheckin(v.reflexao || {}, recarregar)));
  }

  // ---------------------------------------------------------------- tela: aprendizados

  function formAprendizado(valor, aoSalvar, rotuloBotao) {
    const id = u.novoId('eu-aprendizado');
    const texto = h('textarea', { id, rows: '3', maxlength: '1000', value: valor || '', placeholder: 'Ex.: quando ela chega cansada, o melhor é deixar ela descansar' });
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, rotuloBotao);
    const form = h('form', { class: 'form-item', novalidate: true },
      u.campo('O que você aprendeu', texto, 'Com “quando ela…” ou “se ela…”, ele volta sozinho quando um registro novo falar disso.'),
      erro, h('div', { class: 'acoes' }, botao));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!texto.value.trim()) {
        erro.textContent = 'Escreva o aprendizado.';
        texto.focus();
        return;
      }
      botao.disabled = true;
      try {
        await aoSalvar(texto.value);
      } catch (e) {
        botao.disabled = false;
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    return form;
  }

  function cartaoAprendizado(a, recarregar) {
    const li = h('li', { class: 'cartao' });
    function desenhar() {
      u.limpar(li);
      u.anexar(li,
        h('div', { class: 'item-topo' }, tipoRotulo('💡', 'Aprendizado'), a.data ? h('span', { class: 'sutil' }, data(a.data)) : null),
        h('p', { class: 'alerta-texto' }, a.texto),
        lista(a.gatilhos).length ? h('p', { class: 'dica' }, 'Volta quando: ', lista(a.gatilhos).join(', '), '.')
          : h('p', { class: 'dica' }, 'Sem gatilho: para ele voltar sozinho, use “quando ela…” ou “se ela…”.'),
        a.trecho ? u.citacao(a.trecho) : null,
        lista(a.voltou).length ? h('details', { class: 'porque' }, h('summary', null, `Voltou ${lista(a.voltou).length === 1 ? '1 vez' : `${lista(a.voltou).length} vezes`} nos últimos 30 dias`),
          h('ul', { class: 'lista-simples' }, lista(a.voltou).map((x) => h('li', null, data(x.data), ': ', x.trecho ? `“${x.trecho}”` : '',
            x.motivos ? h('span', { class: 'sutil' }, ` (${lista(x.motivos).join(', ')})`) : null, ' ', u.linkRegistro(x.registro_id, 'ver'))))) : null,
        h('div', { class: 'acoes' },
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => {
            u.limpar(li);
            u.anexar(li, formAprendizado(a.texto, async (t) => {
              await api('PATCH', `/eu/aprendizados/${enc(a.id)}`, { texto: t });
              u.avisar('Aprendizado atualizado.');
              recarregar();
            }, 'Salvar'), h('button', { type: 'button', class: 'botao botao-texto', onclick: desenhar }, 'Cancelar'));
          } }, '✏️ Editar'),
          botaoAcao('🗑️ Apagar', 'botao botao-texto botao-perigo-texto', async () => {
            const ok = await u.confirmar({ titulo: 'Apagar este aprendizado?', texto: 'Ele sai do diário e das cópias automáticas. Não dá para desfazer.', ok: 'Apagar', perigo: true });
            if (!ok) throw Object.assign(new Error(''), { bloqueio: true });
            return api('DELETE', `/eu/aprendizados/${enc(a.id)}`);
          }, () => { u.avisar('Aprendizado apagado.'); recarregar(); }),
          a.registro_id != null ? u.linkRegistro(a.registro_id, 'de onde veio') : null));
    }
    desenhar();
    return li;
  }

  async function telaAprendizados(main, atual) {
    topo(main, 'Aprendizados', 'O que você aprendeu com ela, para voltar na hora certa (na revisão e no Hoje).', '💡', 'aprendizados');
    const recarregar = () => navegar('#eu/aprendizados');
    u.anexar(main, h('details', { class: 'cartao mais-detalhes' }, h('summary', null, '➕ Anotar um aprendizado'),
      formAprendizado('', async (t) => {
        await api('POST', '/eu/aprendizados', { texto: t });
        u.avisar('Aprendizado guardado. 💡');
        recarregar();
      }, 'Guardar')), u.carregando());
    const itens = lista(await api('GET', '/eu/aprendizados'));
    if (!atual()) return;
    tirarCarregando(main);
    u.anexar(main, itens.length ? h('ul', { class: 'lista-cartoes' }, itens.map((a) => cartaoAprendizado(a, recarregar)))
      : u.estadoVazio('Nenhum aprendizado ainda. Quando você registrar “aprendi que quando ela chega cansada…”, ele aparece aqui.'));
  }

  // ---------------------------------------------------------------- tela: desentendimentos (só a sua parte)

  function campoTexto(rotulo, valor, dica, max = '1000') {
    const id = u.novoId('eu-rep');
    const el = h('textarea', { id, rows: '2', maxlength: max, value: valor || '' });
    return [u.campo(rotulo, el, dica), el];
  }

  function radiosIntensidade(valor, aoMudar) {
    const nome = u.novoId('eu-intensidade');
    return h('fieldset', { class: 'opcoes-relacao' }, h('legend', null, 'Intensidade'),
      h('div', { class: 'opcoes' }, INTENSIDADES.map(([v, rotulo]) => {
        const id = u.novoId('eu-int');
        return h('label', { class: 'opcao-linha', for: id },
          h('input', { type: 'radio', id, name: nome, value: String(v), checked: Number(valor) === v, onchange: () => aoMudar(v) }),
          h('span', null, rotulo));
      })));
  }

  function formReparo(recarregar) {
    let intensidade = 1;
    const idData = u.novoId('eu-rep-data');
    const quando = h('input', { id: idData, type: 'date', value: u.hojeIso(), max: u.hojeIso() });
    const [cAconteceu, aconteceu] = campoTexto('O que aconteceu', '', 'Em poucas palavras, do seu jeito.');
    const [cSenti, senti] = campoTexto('O que eu senti', '');
    const [cEla, ela] = campoTexto('O que ela pode ter sentido', '', '🔵 É o que você imagina, até ela dizer.');
    const idDisse = u.novoId('eu-rep-disse');
    const disse = h('input', { id: idDisse, type: 'checkbox' });
    const [cFaria, faria] = campoTexto('O que eu faria diferente', '');
    const [cReparo, reparo] = campoTexto('O que fiz para reparar (se fiz)', '');
    const idTema = u.novoId('eu-rep-tema');
    const tema = h('input', { id: idTema, type: 'text', maxlength: '200', placeholder: 'Ex.: a mãe dela, o celular, horários' });
    const idConv = u.novoId('eu-rep-conv');
    const conversamos = h('select', { id: idConv },
      h('option', { value: '' }, 'Ainda não sei'), h('option', { value: 'sim' }, 'Sim'), h('option', { value: 'nao' }, 'Ainda não'));
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const form = h('form', { class: 'form-item', novalidate: true },
      h('p', { class: 'sutil' }, 'Só a sua parte: não existe campo para o que ela errou. É para você entender e cuidar, não para julgar.'),
      u.campo('Quando', quando), cAconteceu, cSenti, cEla,
      h('label', { class: 'opcao-linha', for: idDisse }, disse, h('span', null, 'Ela me disse isso (🟢)')),
      cFaria, cReparo, u.campo('Tema (opcional)', tema, 'Ajuda a perceber quando um assunto volta.'),
      radiosIntensidade(intensidade, (v) => { intensidade = v; }),
      u.campo('Vocês conversaram depois?', conversamos), erro,
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario' }, 'Guardar')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!aconteceu.value.trim()) {
        erro.textContent = 'Conte em poucas palavras o que aconteceu.';
        aconteceu.focus();
        return;
      }
      const corpo = { aconteceu: aconteceu.value, data: quando.value || null, intensidade };
      if (senti.value.trim()) corpo.eu_senti = senti.value;
      if (ela.value.trim()) {
        corpo.ela_pode_ter_sentido = ela.value;
        corpo.ela_disse = disse.checked;
      }
      if (faria.value.trim()) corpo.faria_diferente = faria.value;
      if (reparo.value.trim()) corpo.reparo = reparo.value;
      if (tema.value.trim()) corpo.tema = tema.value;
      if (conversamos.value) corpo.conversamos = conversamos.value === 'sim';
      try {
        await api('POST', '/eu/reparos', corpo);
        u.avisar('Guardado. Cuidar disso já é um passo. 💙');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    return h('details', { class: 'cartao mais-detalhes' }, h('summary', null, '➕ Anotar um desentendimento (só a sua parte)'), form);
  }

  function formEditarReparo(r, li, desenhar, recarregar) {
    const [cSenti, senti] = campoTexto('O que eu senti', r.eu_senti);
    const [cFaria, faria] = campoTexto('O que eu faria diferente', r.faria_diferente);
    const [cReparo, reparo] = campoTexto('O que fiz para reparar', r.reparo);
    const [cEla, ela] = campoTexto('O que ela pode ter sentido', r.ela_pode_ter_sentido, '🔵 Até ela dizer, é o que você imagina.');
    const idDisse = u.novoId('eu-rep-disse');
    const disse = h('input', { id: idDisse, type: 'checkbox', checked: r.ela_certeza === 'confirmado' });
    const idTema = u.novoId('eu-rep-tema');
    const tema = h('input', { id: idTema, type: 'text', maxlength: '200', value: r.tema || '' });
    let intensidade = r.intensidade;
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const form = h('form', { class: 'form-item', novalidate: true },
      cSenti, cEla, h('label', { class: 'opcao-linha', for: idDisse }, disse, h('span', null, 'Ela me disse isso (🟢)')),
      cFaria, cReparo, u.campo('Tema', tema), radiosIntensidade(intensidade, (v) => { intensidade = v; }), erro,
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, 'Salvar'),
        h('button', { type: 'button', class: 'botao botao-texto', onclick: desenhar }, 'Cancelar')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const corpo = { eu_senti: senti.value.trim() || null, faria_diferente: faria.value.trim() || null, reparo: reparo.value.trim() || null,
        tema: tema.value.trim() || null, intensidade, ela_pode_ter_sentido: ela.value.trim() || null };
      if (ela.value.trim() && (ela.value.trim() !== (r.ela_pode_ter_sentido || '') || disse.checked !== (r.ela_certeza === 'confirmado'))) corpo.ela_disse = disse.checked;
      try {
        await api('PATCH', `/eu/reparos/${enc(r.id)}`, corpo);
        u.avisar('Atualizado.');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    u.limpar(li);
    u.anexar(li, form);
  }

  function cartaoReparo(r, recarregar) {
    const li = h('li', { class: 'cartao' });
    function desenhar() {
      u.limpar(li);
      const intensidade = (INTENSIDADES.find(([v]) => v === Number(r.intensidade)) || [0, ''])[1];
      u.anexar(li,
        h('div', { class: 'item-topo' }, tipoRotulo('🌧️', 'Desentendimento'), h('span', { class: 'chip' }, intensidade),
          r.tema ? h('span', { class: 'chip' }, `Tema: ${r.tema}`) : null),
        h('p', { class: 'sutil' }, data(r.data), r.quando ? ` · ${r.quando}` : ''),
        r.trecho ? u.citacao(r.aconteceu) : h('p', { class: 'alerta-texto' }, r.aconteceu),
        h('dl', { class: 'lista-definicoes' },
          r.eu_senti ? [h('dt', null, 'O que eu senti'), h('dd', null, r.eu_senti)] : null,
          r.ela_pode_ter_sentido ? [h('dt', null, 'O que ela pode ter sentido'), h('dd', null, selo(r.ela_certeza || 'inferencia'), ' ', r.ela_pode_ter_sentido)] : null,
          r.faria_diferente ? [h('dt', null, 'O que eu faria diferente'), h('dd', null, r.faria_diferente)] : null,
          r.reparo ? [h('dt', null, 'O reparo'), h('dd', null, r.reparo)] : null,
          h('dt', null, 'Conversaram?'), h('dd', null, r.conversamos === true ? 'Sim 💙' : (r.conversamos === false ? 'Ainda não' : 'Ainda não sei'))),
        r.acompanhar ? [h('p', { class: 'alerta-texto' }, 'Vocês conversaram sobre aquilo?'), botoesConversaram(r, recarregar)] : null,
        h('div', { class: 'acoes' },
          r.conversamos !== true && !r.acompanhar ? botaoAcao('💬 Conversamos', 'botao botao-texto', () => api('PATCH', `/eu/reparos/${enc(r.id)}`, { conversamos: true }),
            () => { u.avisar('Que bom. 💙'); recarregar(); }) : null,
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => formEditarReparo(r, li, desenhar, recarregar) }, '✏️ Completar'),
          botaoAcao('🗑️ Apagar', 'botao botao-texto botao-perigo-texto', async () => {
            const ok = await u.confirmar({ titulo: 'Apagar este desentendimento?', texto: 'Ele sai do diário e das cópias automáticas. O registro de onde veio continua. Não dá para desfazer.', ok: 'Apagar', perigo: true });
            if (!ok) throw Object.assign(new Error(''), { bloqueio: true });
            return api('DELETE', `/eu/reparos/${enc(r.id)}`);
          }, () => { u.avisar('Apagado.'); recarregar(); }),
          r.registro_id != null ? u.linkRegistro(r.registro_id, 'ver registro') : null));
    }
    desenhar();
    return li;
  }

  async function telaReparos(main, atual) {
    topo(main, 'Desentendimentos e reparos', 'Só a sua parte, com calma. Para entender e cuidar, nunca para julgar.', '🌧️', 'reparos');
    const recarregar = () => navegar('#eu/reparos');
    u.anexar(main, formReparo(recarregar), u.carregando());
    const v = (await api('GET', '/eu/reparos')) || {};
    if (!atual()) return;
    tirarCarregando(main);
    if (v.intensos) {
      u.anexar(main, h('div', { class: 'nota-cuidado', role: 'note' }, h('p', null, h('span', { 'aria-hidden': 'true' }, '🫶 '), v.intensos.texto),
        h('p', { class: 'sutil' }, v.intensos.porque)));
    }
    lista(v.temas).forEach((t) => u.anexar(main, h('div', { class: 'nota-cuidado', role: 'note' },
      h('p', null, h('span', { 'aria-hidden': 'true' }, '🔁 '), t.texto), h('p', { class: 'sutil' }, t.porque), linksRegistros(t.registro_ids))));
    const reparos = lista(v.reparos);
    u.anexar(main, reparos.length ? h('ul', { class: 'lista-cartoes' }, reparos.map((r) => cartaoReparo(r, recarregar)))
      : u.estadoVazio('Nenhum desentendimento anotado. Quando você registra “a gente brigou…”, ele aparece para você completar a sua parte.'));
  }

  // ---------------------------------------------------------------- tela: cartas

  function blocoEscolha(b, escolhidos) {
    const id = u.novoId('eu-bloco');
    return h('li', { class: 'entrada' },
      h('label', { class: 'opcao-linha', for: id },
        h('input', { type: 'checkbox', id, onchange: (ev) => { if (ev.target.checked) escolhidos.add(b.id); else escolhidos.delete(b.id); } }),
        h('span', null, h('span', { class: 'sutil' }, `${b.titulo}: `), b.certeza ? [selo(b.certeza), ' '] : null, b.texto,
          b.data ? h('span', { class: 'sutil' }, ` · ${data(b.data)}`) : null)),
      b.trecho && b.trecho !== b.texto ? u.citacao(b.trecho) : null,
      b.registro_id != null ? u.linkRegistro(b.registro_id, 'ver registro') : null);
  }

  function montadorDeCarta(ocasioes, recarregar) {
    const idOcasiao = u.novoId('eu-ocasiao');
    const ocasiao = h('select', { id: idOcasiao }, lista(ocasioes).map((o) => h('option', { value: o.id }, o.nome)));
    const area = h('div', { 'aria-live': 'polite' });
    const ver = botaoAcao('Ver as lembranças', 'botao botao-secundario', () => api('GET', `/eu/cartas/blocos?ocasiao=${enc(ocasiao.value)}`),
      (r) => { ver.disabled = false; desenharBlocos(area, ocasiao.value, lista(r && r.blocos), recarregar); });
    return h('div', { class: 'cartao' },
      h('h2', null, h('span', { 'aria-hidden': 'true' }, '💌 '), 'Escrever uma carta'),
      h('p', { class: 'sutil' }, 'Escolha a ocasião, marque as lembranças que quer usar e o app monta um esqueleto só com elas, para você reescrever do seu jeito.'),
      u.campo('Ocasião', ocasiao), h('div', { class: 'acoes' }, ver), area);
  }

  function desenharBlocos(area, ocasiao, blocos, recarregar) {
    u.limpar(area);
    if (!blocos.length) {
      u.anexar(area, u.vazioPequeno('Ainda não há lembranças registradas para esta ocasião. Dá para escrever a carta do zero mesmo assim.'));
    }
    const escolhidos = new Set();
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const montar = botaoAcao('Montar o esqueleto', 'botao botao-primario', () => api('POST', '/eu/cartas/esqueleto', { ocasiao, blocos: [...escolhidos] }),
      (r) => { montar.disabled = false; editorCarta(area, ocasiao, (r && r.texto) || '', lista(r && r.blocos).map((b) => b.id), recarregar); });
    u.anexar(area,
      blocos.length ? h('fieldset', null, h('legend', null, 'Lembranças para marcar'), h('ul', { class: 'lista-entradas' }, blocos.map((b) => blocoEscolha(b, escolhidos)))) : null,
      erro, h('div', { class: 'acoes' }, montar));
  }

  function editorCarta(area, ocasiao, texto, blocos, recarregar) {
    u.limpar(area);
    const idTitulo = u.novoId('eu-carta-titulo');
    const idTexto = u.novoId('eu-carta-texto');
    const titulo = h('input', { id: idTitulo, type: 'text', maxlength: '120', placeholder: 'Opcional' });
    const corpo = h('textarea', { id: idTexto, rows: '14', maxlength: '20000', value: texto });
    corpo.classList.add('texto-livre');
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const form = h('form', { class: 'form-item', novalidate: true },
      h('p', { class: 'dica' }, 'As frases entre parênteses são o que você registrou: reescreva com as suas palavras. Nada foi inventado.'),
      u.campo('Título', titulo), u.campo('Carta', corpo), erro,
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario' }, 'Guardar a carta')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!corpo.value.trim()) {
        erro.textContent = 'Escreva a carta.';
        corpo.focus();
        return;
      }
      try {
        await api('POST', '/eu/cartas', { ocasiao, titulo: titulo.value.trim() || null, texto: corpo.value, blocos });
        u.avisar('Carta guardada. Ela fica só aqui. 💌');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    });
    u.anexar(area, form);
    corpo.focus();
  }

  function cartaGuardada(c, recarregar) {
    const li = h('li', { class: 'cartao' });
    function desenhar() {
      u.limpar(li);
      u.anexar(li,
        h('div', { class: 'item-topo' }, tipoRotulo('💌', c.ocasiao_nome || 'Carta'), h('span', { class: 'sutil' }, data(c.atualizado_em || c.criado_em))),
        c.titulo ? h('h3', null, c.titulo) : null,
        h('details', { class: 'porque' }, h('summary', null, 'Ler a carta'), h('p', { class: 'texto-livre' }, c.texto)),
        h('div', { class: 'acoes' },
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => {
            const id = u.novoId('eu-carta-edit');
            const caixa = h('textarea', { id, rows: '14', maxlength: '20000', value: c.texto });
            caixa.classList.add('texto-livre');
            const erro = h('p', { class: 'erro-campo', role: 'alert' });
            const form = h('form', { class: 'form-item', novalidate: true }, u.campo('Carta', caixa), erro,
              h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, 'Salvar'),
                h('button', { type: 'button', class: 'botao botao-texto', onclick: desenhar }, 'Cancelar')));
            form.addEventListener('submit', async (ev) => {
              ev.preventDefault();
              try {
                await api('PATCH', `/eu/cartas/${enc(c.id)}`, { texto: caixa.value });
                u.avisar('Carta atualizada.');
                recarregar();
              } catch (e) {
                if (e && e.bloqueio) return;
                erro.textContent = e.message;
              }
            });
            u.limpar(li);
            u.anexar(li, form);
            caixa.focus();
          } }, '✏️ Editar'),
          botaoAcao('🗑️ Apagar', 'botao botao-texto botao-perigo-texto', async () => {
            const ok = await u.confirmar({ titulo: 'Apagar esta carta?', texto: 'Ela sai do diário e das cópias automáticas. Não dá para desfazer.', ok: 'Apagar', perigo: true });
            if (!ok) throw Object.assign(new Error(''), { bloqueio: true });
            return api('DELETE', `/eu/cartas/${enc(c.id)}`);
          }, () => { u.avisar('Carta apagada.'); recarregar(); })));
    }
    desenhar();
    return li;
  }

  async function telaCartas(main, atual) {
    topo(main, 'Cartas', 'Ajuda para escrever com o que você registrou. As cartas ficam só aqui.', '💌', 'cartas');
    u.anexar(main, u.carregando());
    const v = (await api('GET', '/eu/cartas')) || {};
    if (!atual()) return;
    tirarCarregando(main);
    const recarregar = () => navegar('#eu/cartas');
    const cartas = lista(v.cartas);
    u.anexar(main, montadorDeCarta(v.ocasioes, recarregar),
      u.secao(`💌 Cartas guardadas${cartas.length ? ` (${cartas.length})` : ''}`,
        cartas.length ? h('ul', { class: 'lista-cartoes' }, cartas.map((c) => cartaGuardada(c, recarregar)))
          : u.vazioPequeno('Nenhuma carta guardada ainda.')));
  }

  // ---------------------------------------------------------------- roteamento das telas

  async function telaEu(main, partes, atual) {
    const [sub, extra] = lista(partes);
    if (sub === 'checkin') return telaCheckin(main, extra, atual);
    if (sub === 'mes') return telaMes(main, extra, atual);
    if (sub === 'aprendizados') return telaAprendizados(main, atual);
    if (sub === 'reparos') return telaReparos(main, atual);
    if (sub === 'cartas') return telaCartas(main, atual);
    return telaVisao(main, atual);
  }

  // ---------------------------------------------------------------- revisão (extras)

  function rodapeEu() {
    return h('p', { class: 'dica' }, 'Fica na área 🪞 Eu (só sua).');
  }

  function revisaoAtitude(e) {
    return [
      h('div', { class: 'item-topo' }, tipoRotulo('🫶', 'Atitude sua'), e.jeito_nome ? h('span', { class: 'chip' }, e.jeito_nome) : null),
      h('p', { class: 'extra-resumo' }, e.texto || e.resumo),
      e.data ? h('p', { class: 'sutil' }, `📅 ${data(e.data)}${e.data_expressao ? ` (“${e.data_expressao}”)` : ''}`) : null,
      u.citacao(e.trecho), rodapeEu(),
    ];
  }

  function revisaoAprendizado(e) {
    const id = u.novoId('rev-eu-ap');
    const caixa = h('textarea', { id, rows: '2', maxlength: '1000', value: e.texto || e.resumo || '',
      oninput: (ev) => { e.texto = ev.target.value; e.resumo = ev.target.value; } });
    // Os rótulos já vêm prontos do servidor ("ela cansada"; os gatilhos crus são raízes, como "cansad").
    const g = e.gatilhos || {};
    const volta = lista(e.gatilhos_rotulos).length ? lista(e.gatilhos_rotulos)
      : [...lista(g.termos).map((x) => `“${x}”`), ...lista(g.categorias)];

    return [
      h('div', { class: 'item-topo' }, tipoRotulo('💡', 'Aprendizado seu')),
      u.campo('O aprendizado', caixa, volta.length ? `Vai voltar quando um registro falar de: ${volta.join(', ')}.` : 'Com “quando ela…”, ele volta sozinho na hora certa.'),
      u.citacao(e.trecho), rodapeEu(),
    ];
  }

  function revisaoReparo(e, ctx) {
    const idTema = u.novoId('rev-eu-tema');
    const tema = h('input', { id: idTema, type: 'text', maxlength: '200', value: e.tema || '', oninput: (ev) => { e.tema = ev.target.value || null; } });
    const idSenti = u.novoId('rev-eu-senti');
    const senti = h('input', { id: idSenti, type: 'text', maxlength: '1000', value: e.eu_senti || '', oninput: (ev) => { e.eu_senti = ev.target.value || null; } });
    const idFaria = u.novoId('rev-eu-faria');
    const faria = h('input', { id: idFaria, type: 'text', maxlength: '1000', value: e.faria_diferente || '', oninput: (ev) => { e.faria_diferente = ev.target.value || null; } });
    return [
      h('div', { class: 'item-topo' }, tipoRotulo('🌧️', 'Desentendimento (só a sua parte)')),
      u.citacao(e.trecho),
      e.data ? h('p', { class: 'sutil' }, `📅 ${data(e.data)}`) : null,
      u.campo('Tema', tema, 'Ajuda a perceber quando um assunto volta.'),
      radiosIntensidade(e.intensidade || 1, (v) => { e.intensidade = v; if (ctx && ctx.redesenhar) u.falar(`Intensidade ${v}.`); }),
      u.campo('O que eu senti', senti),
      u.campo('O que eu faria diferente', faria),
      e.ela_pode_ter_sentido ? h('p', { class: 'dica' }, 'O que ela pode ter sentido: ', selo(e.ela_certeza || 'inferencia'), ' ', `“${e.ela_pode_ter_sentido}”`) : null,
      e.reparo ? h('p', { class: 'dica' }, `O reparo: “${e.reparo}”`) : null,
      h('p', { class: 'dica' }, 'Não existe campo para o que ela errou: é só a sua parte. Depois de uns dias, o Hoje pergunta se vocês conversaram.'),
    ];
  }

  function revisaoAdmiracao(e) {
    return [h('div', { class: 'item-topo' }, tipoRotulo('✨', 'Algo que você admira nela')),
      h('p', { class: 'extra-resumo' }, e.texto || e.resumo), u.citacao(e.trecho), rodapeEu()];
  }

  function revisaoLembrete(e) {
    return [
      h('div', { class: 'item-topo' }, tipoRotulo('💡', 'Um aprendizado seu combina com isto')),
      u.citacao(e.aprendizado || ''),
      lista(e.motivos).length ? h('p', { class: 'dica' }, `Porque: ${lista(e.motivos).join(', ')}. No seu texto: “${e.trecho}”`) : null,
      h('p', { class: 'dica' }, 'Confirmar só anota que ele voltou na hora certa. Não muda o registro e não o tira da IA.'),
    ];
  }

  // ---------------------------------------------------------------- cartões do Hoje

  function cartaoSimples(emoji, link, rotuloLink) {
    return (c, ctx) => [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, `${emoji} `), c.texto),
      h('div', { class: 'alerta-acoes' }, porqueDe(c) || h('span'),
        h('div', { class: 'acoes' }, link ? h('a', { href: typeof link === 'function' ? link(c) : link, class: 'botao botao-texto' }, rotuloLink) : null,
          ctx.botaoDispensar())),
    ];
  }

  function cartaoCheckin(c, ctx) {
    const d = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '🪞 '), c.texto),
      h('div', { class: 'alerta-acoes' }, porqueDe(c) || h('span'),
        h('div', { class: 'acoes' }, h('a', { href: `#eu/checkin/${enc(d.periodo || '')}`, class: 'botao botao-secundario botao-pequeno' }, 'Fazer agora'),
          ctx.botaoDispensar())),
    ];
  }

  function cartaoIntencao(c, ctx) {
    const d = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '🎯 '), c.texto),
      h('div', { class: 'alerta-acoes' }, porqueDe(c) || h('span'),
        h('div', { class: 'acoes' },
          d.id == null && d.texto ? botaoAcao('Escolher esta', 'botao botao-secundario botao-pequeno',
            () => api('POST', '/eu/intencao', { mes: d.mes, texto: d.texto, sugestao: d.sugestao }),
            () => { ctx.remover(); u.avisar('Intenção escolhida. 💙'); }) : null,
          h('a', { href: d.id == null ? '#eu' : `#eu/mes/${enc(d.mes || '')}`, class: 'botao botao-texto' }, d.id == null ? 'Ver outras ideias' : 'Contar como está'),
          ctx.botaoDispensar())),
      h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: quem decide é você.'),
    ];
  }

  function hojeReparo(c, ctx) {
    const d = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '🌧️ '), c.texto),
      h('div', { class: 'alerta-acoes' }, porqueDe(c) || h('span'),
        h('div', { class: 'acoes' },
          d.id != null ? botaoAcao('💬 Sim, conversamos', 'botao botao-secundario botao-pequeno', () => api('PATCH', `/eu/reparos/${enc(d.id)}`, { conversamos: true }),
            () => { ctx.remover(); u.avisar('Que bom que vocês conversaram. 💙'); }) : null,
          d.id != null ? botaoAcao('Ainda não', 'botao botao-texto', () => api('PATCH', `/eu/reparos/${enc(d.id)}`, { conversamos: false }),
            () => { ctx.remover(); u.avisar('Tudo bem. Quando fizer sentido, um momento calmo ajuda.'); }) : null)),
    ];
  }

  function hojeAprendizado(c, ctx) {
    const d = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '💡 '), c.texto),
      h('div', { class: 'alerta-acoes' }, porqueDe(c) || h('span'),
        h('div', { class: 'acoes' },
          d.id != null ? botaoAcao('Lembrei, obrigado', 'botao botao-secundario botao-pequeno', async () => {
            await api('POST', `/eu/aprendizados/${enc(d.id)}/lembrei`);
            return ctx.dispensar();
          }, () => { u.falar('Combinado.'); }) : null,
          ctx.botaoDispensar())),
    ];
  }

  function cartaoCuidado(emoji, link, rotuloLink) {
    const base = cartaoSimples(emoji, link, rotuloLink);
    return (c, ctx) => [...base(c, ctx), h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: quem conhece vocês é você.')];
  }

  // ---------------------------------------------------------------- Ajustes

  function secaoAjustes(corpo, ctx) {
    const config = (ctx && ctx.config) || {};
    const caixa = h('input', {
      id: 'aj-eu-lembretes', type: 'checkbox', checked: config.eu_lembretes !== false,
      onchange: (e) => ctx.salvarConfig({ eu_lembretes: e.target.checked }, 'Preferência da área Eu salva.'),
    });
    u.anexar(corpo,
      h('p', { class: 'sutil' }, 'A área 🪞 Eu é só sua: nada dela vai para a IA, e não existe nota nem placar.'),
      h('label', { class: 'opcao-linha', for: 'aj-eu-lembretes' }, caixa,
        h('span', null, 'Lembrar no Hoje do check-in da semana, da reflexão do mês e da intenção')),
      h('p', { class: 'dica' }, 'Os avisos sobre desentendimentos, aprendizados e “cuidar de você” continuam, e cada um pode ser dispensado.'));
  }

  // ---------------------------------------------------------------- encaixe

  E.registrarMenu('eu', 'Eu', '🪞', 'eu');
  E.registrarTela('eu', telaEu);
  E.registrarRevisaoExtra('atitude', revisaoAtitude);
  E.registrarRevisaoExtra('aprendizado', revisaoAprendizado);
  E.registrarRevisaoExtra('reparo', revisaoReparo);
  E.registrarRevisaoExtra('admiracao', revisaoAdmiracao);
  // Só um lembrete: salvar sem decidir não pede confirmação (confirmar só anota que você viu).
  E.registrarRevisaoExtra('aprendizado_lembrete', revisaoLembrete, { opcional: true });
  E.registrarCartaoHoje('eu_checkin', cartaoCheckin);
  E.registrarCartaoHoje('eu_mes', cartaoSimples('📆', (c) => `#eu/mes/${enc((c.dados || {}).mes || '')}`, 'Abrir a reflexão'));
  E.registrarCartaoHoje('eu_intencao', cartaoIntencao);
  E.registrarCartaoHoje('eu_reparo', hojeReparo);
  E.registrarCartaoHoje('eu_tema', cartaoCuidado('🔁', '#eu/reparos', 'Ver os desentendimentos'));
  E.registrarCartaoHoje('eu_cuidado_relacao', cartaoCuidado('🫶', '#eu/reparos', 'Ver os desentendimentos'));
  E.registrarCartaoHoje('eu_aprendizado', hojeAprendizado);
  E.registrarCartaoHoje('eu_cuidar', cartaoCuidado('💙', '#eu', 'Abrir a área Eu'));
  E.registrarCartaoHoje('eu_carta', cartaoSimples('💌', '#eu/cartas', 'Escrever uma carta'));
  E.registrarSecaoAjustes('eu', '🪞 Área Eu', secaoAjustes);
})();
