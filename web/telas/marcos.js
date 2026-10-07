/* Marcos, linha do tempo e “Neste dia” (DESIGN.md, seção 20.6): a tela do recurso "marcos".
 *
 * Encaixa no app por window.EntreNos:
 * - menu e tela #tempo (#tempo/tudo mostra também o que é menos marcante): o tempo juntos e os
 *   próximos marcos de tempo (100 dias, 6 meses, 1 ano, 500 dias, 1.000 dias...), o “Neste dia”,
 *   guardar um marco à mão e a linha do tempo por ano (marcos, marcos de tempo, acontecimentos e
 *   registros importantes), cada ponto com o trecho, as pessoas e o contexto do registro;
 * - miolo do extra "marco" na revisão (título, data, tipo e descrição ajustáveis; aviso de marco
 *   parecido já guardado; de onde veio a data);
 * - cartões do Hoje: marco de tempo chegando, aniversário de um marco de vocês, "relembrar" no dia de
 *   6 meses / 1 ano e "Neste dia" (com cuidado no aniversário de uma perda dela).
 * Tudo por nós de texto (h), sem HTML montado nem estilo inline (CSP). */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E || !E.util) return;
  const { api, h, navegar } = E;
  const u = E.util;

  const TIPOS = [
    ['primeiro_encontro', '☕', 'Primeiro encontro'],
    ['namoro', '💑', 'Namoro'],
    ['viagem', '✈️', 'Viagem'],
    ['familia', '👪', 'Família'],
    ['eu_te_amo', '❤️', 'Eu te amo'],
    ['outro', '⭐', 'Outro marco'],
  ];

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function erroNaTela(e) {
    if (e && e.bloqueio) return;
    u.avisarErro(e);
  }

  function tipoDe(id) {
    return TIPOS.find((t) => t[0] === id) || TIPOS[TIPOS.length - 1];
  }

  function rotuloTipo(id) {
    const [, emoji, nome] = tipoDe(id);
    return h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, `${emoji} `), nome);
  }

  function selectTipo(id, valor, aoMudar) {
    return h('select', { id, onchange: (ev) => aoMudar(ev.target.value) },
      TIPOS.map(([v, emoji, nome]) => h('option', { value: v, selected: v === valor }, `${emoji} ${nome}`)));
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

  function recarregar() {
    navegar(location.hash && location.hash.startsWith('#tempo') ? location.hash : '#tempo');
  }

  function textoDias(n) {
    if (typeof n !== 'number') return '';
    if (n === 0) return 'hoje';
    if (n === 1) return 'amanhã';
    return `em ${n} dias`;
  }

  // ---------------------------------------------------------------- a tela #tempo

  function cartaoVoces(t) {
    const id = u.novoId('voces');
    const caixa = h('section', { class: 'cartao', 'aria-labelledby': id });
    u.anexar(caixa, h('h2', { id }, h('span', { 'aria-hidden': 'true' }, '💑 '), 'Vocês'));
    if (!t || !t.inicio) {
      u.anexar(caixa,
        h('p', null, 'Quando vocês começaram a namorar? Com essa data eu acompanho os marcos de tempo de vocês: 100 dias, 6 meses, 1 ano, 500 dias, 1.000 dias…'),
        h('p', { class: 'sutil' }, 'Conte em ', h('a', { href: '#ajustes' }, 'Ajustes'),
          ', ou registre o dia em que você pediu ela em namoro.'));
      return caixa;
    }
    const dias = typeof t.dias === 'number' ? t.dias : null;
    u.anexar(caixa,
      t.tempo_juntos ? h('p', { class: 'alerta-texto' }, t.tempo_juntos === 'desde hoje'
        ? 'Juntos desde hoje 💙' : `${t.tempo_juntos} juntos 💙`) : null,
      dias != null ? h('p', { class: 'sutil' }, `${dias === 1 ? '1 dia' : `${dias} dias`} desde ${u.dataBr(t.inicio)}.`) : null,
      t.texto ? h('p', { class: 'dica' }, t.texto) : null);
    const proximos = lista(t.proximos);
    if (proximos.length) {
      u.anexar(caixa, h('h3', { class: 'titulo-pequeno' }, 'Os próximos marcos'),
        h('ul', { class: 'lista-simples' }, proximos.map((p) => h('li', null,
          h('span', { 'aria-hidden': 'true' }, '⏳ '), p.titulo, ' · ',
          h('span', { class: 'sutil' }, `${u.dataBr(p.data)} (${textoDias(p.dias)})`)))));
    }
    if (t.base === 'marco') {
      u.anexar(caixa, h('div', { class: 'acoes' },
        botaoAcao(`Guardar ${u.dataBr(t.inicio)} como início do namoro`, 'botao botao-secundario botao-pequeno',
          () => u.salvarConfig({ inicio_namoro: t.inicio }, 'Pronto: o início do namoro ficou guardado em Ajustes.'),
          (ok) => { if (ok) recarregar(); }),
        t.marco && t.marco.registro_id != null ? u.linkRegistro(t.marco.registro_id, 'ver o registro') : null));
    }
    const dif = t.diferente;
    if (dif) {
      u.anexar(caixa, h('p', { class: 'nota-cuidado', role: 'note' },
        h('span', { 'aria-hidden': 'true' }, '🔎 '),
        `O início do namoro em Ajustes (${u.dataBr(t.inicio)}) é diferente do marco “${dif.titulo}” (${u.dataBr(dif.data)}). `,
        'Se um deles estiver errado, dá para corrigir em ', h('a', { href: '#ajustes' }, 'Ajustes'), ' ou no marco.',
        dif.registro_id != null ? [' ', u.linkRegistro(dif.registro_id, 'ver o registro')] : null));
    }
    return caixa;
  }

  function secaoNesteDia(dados) {
    const lembrancas = lista(dados && dados.lembrancas);
    const tempoHoje = lista(dados && dados.tempo_hoje);
    const conteudo = [];
    for (const t of tempoHoje) {
      conteudo.push(h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '⏳ '), `Hoje vocês completam ${t} 💙`));
    }
    const m = dados && dados.momento;
    if (m && lembrancas.length) {
      conteudo.push(h('p', { class: 'nota-cuidado', role: 'note' }, h('span', { 'aria-hidden': 'true' }, '🫶 '),
        m.data ? `Em ${u.dataBr(m.data)} você registrou: “${m.trecho}”. ` : `Você registrou: “${m.trecho}”. `,
        'Por isso as lembranças ficam aqui só para você, sem convite.'));
    }
    if (lembrancas.length) {
      conteudo.push(h('ul', { class: 'lista-entradas' }, lembrancas.map((l) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' },
          h('span', { 'aria-hidden': 'true' }, l.tom === 'luto' ? '🕊️' : l.fonte === 'marco' ? '⭐' : '💛'),
          h('span', { class: 'entrada-texto' }, l.texto || '')),
        l.fonte === 'marco' && l.trecho ? u.citacao(l.trecho) : null,
        lista(l.motivos).length ? h('p', { class: 'sutil' }, `Por que voltou: ${lista(l.motivos).join(', ')}.`) : null,
        l.registro_id != null ? h('p', { class: 'entrada-rodape sutil' }, u.linkRegistro(l.registro_id, 'ver o registro')) : null))));
    } else if (!tempoHoje.length) {
      conteudo.push(u.vazioPequeno('Nada registrado neste dia em anos anteriores, nem há 6 meses. Com o tempo, este cantinho vai se enchendo de histórias.'));
    }
    return u.secao('📅 Neste dia', conteudo);
  }

  function formNovo() {
    const idTitulo = u.novoId('marco-titulo');
    const idData = u.novoId('marco-data');
    const idTipo = u.novoId('marco-tipo');
    const idDesc = u.novoId('marco-desc');
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const titulo = h('input', { id: idTitulo, type: 'text', maxlength: '120', placeholder: 'Ex.: primeira vez que cozinhamos juntos' });
    const data = h('input', { id: idData, type: 'date', max: u.hojeIso() });
    let tipo = 'outro';
    const select = selectTipo(idTipo, tipo, (v) => { tipo = v; });
    const descricao = h('input', { id: idDesc, type: 'text', maxlength: '300' });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario' }, 'Guardar marco');
    const guardar = async (ev) => {
      ev.preventDefault();
      if (!titulo.value.trim()) {
        erro.textContent = 'Escreva o nome do marco.';
        titulo.focus();
        return;
      }
      if (!data.value) {
        erro.textContent = 'Escolha o dia em que aconteceu.';
        data.focus();
        return;
      }
      botao.disabled = true;
      try {
        await api('POST', '/marcos', { titulo: titulo.value, data: data.value, tipo, descricao: descricao.value || null });
        u.avisar('Guardado na linha do tempo 💙');
        recarregar();
      } catch (e) {
        botao.disabled = false;
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    };
    return h('details', { class: 'cartao mais-detalhes' },
      h('summary', null, '➕ Guardar um marco'),
      h('form', { class: 'form-item', novalidate: true, onsubmit: guardar },
        h('p', { class: 'sutil' }, 'Quase sempre os marcos aparecem sozinhos quando você registra “nosso primeiro encontro”, “pedi ela em namoro” ou “conheci a família dela”. Aqui é para guardar um direto.'),
        u.campo('Que marco foi?', titulo),
        u.campo('Quando aconteceu?', data),
        u.campo('Tipo', select),
        u.campo('Um detalhe (opcional)', descricao, 'Ex.: o lugar, a cidade.'),
        erro,
        h('div', { class: 'acoes' }, botao)));
  }

  function formEditar(p, li, desenhar) {
    const idTitulo = u.novoId('ed-marco-titulo');
    const idData = u.novoId('ed-marco-data');
    const idTipo = u.novoId('ed-marco-tipo');
    const idDesc = u.novoId('ed-marco-desc');
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const titulo = h('input', { id: idTitulo, type: 'text', maxlength: '120', value: p.titulo || '' });
    const data = h('input', { id: idData, type: 'date', max: u.hojeIso(), value: p.data || '' });
    let tipo = p.marco_tipo || 'outro';
    const select = selectTipo(idTipo, tipo, (v) => { tipo = v; });
    const descricao = h('input', { id: idDesc, type: 'text', maxlength: '300', value: p.descricao || '' });
    const salvar = async (ev) => {
      ev.preventDefault();
      if (!titulo.value.trim()) {
        erro.textContent = 'Escreva o nome do marco.';
        titulo.focus();
        return;
      }
      const corpo = { titulo: titulo.value, tipo, descricao: descricao.value || null };
      if (data.value && data.value !== p.data) corpo.data = data.value;
      try {
        await api('PATCH', `/marcos/${encodeURIComponent(String(p.marco_id))}`, corpo);
        u.avisar('Pronto, marco atualizado.');
        recarregar();
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = e.message;
      }
    };
    u.limpar(li);
    u.anexar(li, h('div', { class: 'marco-link' },
      h('form', { class: 'form-item', novalidate: true, onsubmit: salvar },
        u.campo('Nome do marco', titulo),
        u.campo('Quando aconteceu?', data, p.precisao && p.precisao !== 'dia' ? `Hoje vale ${p.quando}. Escolher um dia troca por esse dia.` : null),
        u.campo('Tipo', select),
        u.campo('Um detalhe (opcional)', descricao),
        erro,
        h('div', { class: 'acoes' },
          h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, 'Salvar'),
          h('button', { type: 'button', class: 'botao botao-texto', onclick: () => desenhar() }, 'Cancelar')))));
    titulo.focus();
  }

  function botaoApagar(p) {
    const b = h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto' }, '🗑️ Apagar');
    b.addEventListener('click', async () => {
      const ok = await u.confirmar({
        titulo: 'Apagar este marco?',
        texto: 'Ele sai da linha do tempo e das cópias automáticas. O registro de onde ele veio continua. Não dá para desfazer.',
        ok: 'Apagar',
        perigo: true,
      });
      if (!ok) return;
      b.disabled = true;
      try {
        await api('DELETE', `/marcos/${encodeURIComponent(String(p.marco_id))}`);
        u.avisar('Marco apagado.');
        recarregar();
      } catch (e) {
        if (b.isConnected) b.disabled = false;
        erroNaTela(e);
      }
    });
    return b;
  }

  // O contexto do ponto: o registro de onde veio, as pessoas e as relações (lido só quando abre).
  function detalhesContexto(registroId) {
    const corpo = h('div', { class: 'item-detalhes-contexto' });
    const det = h('details', { class: 'porque' }, h('summary', null, 'Ver o contexto'), corpo);
    let lido = false;
    det.addEventListener('toggle', async () => {
      if (!det.open || lido) return;
      lido = true;
      u.anexar(corpo, u.carregando('Abrindo o registro…'));
      try {
        const reg = (await api('GET', `/registros/${encodeURIComponent(String(registroId))}`)) || {};
        u.limpar(corpo);
        const pessoas = lista(reg.pessoas).map((p) => (p.nome && p.relacao ? `${p.nome} (${p.relacao})` : p.nome || p.relacao)).filter(Boolean);
        const relacoes = lista(reg.relacoes).filter((r) => r && r.motivo);
        u.anexar(corpo,
          h('p', { class: 'sutil' }, `Registro de ${u.dataBr(reg.data_registro)}`),
          h('p', { class: 'texto-livre' }, u.encurtar(reg.texto || '', 600)),
          pessoas.length ? h('p', { class: 'sutil' }, h('span', { 'aria-hidden': 'true' }, '👥 '), pessoas.join(', ')) : null,
          relacoes.length ? h('ul', { class: 'lista-simples' }, relacoes.slice(0, 3).map((r) => h('li', { class: 'sutil' },
            h('span', { 'aria-hidden': 'true' }, '🔗 '), r.motivo))) : null,
          h('p', null, u.linkRegistro(registroId, 'Abrir o registro')));
      } catch (e) {
        u.limpar(corpo);
        lido = false;
        if (e && e.bloqueio) return;
        u.anexar(corpo, h('p', { class: 'erro-campo' }, e.status === 404 ? 'Esse registro não existe mais.' : e.message));
      }
    });
    return det;
  }

  function pontoDaLinha(p) {
    const li = h('li', { class: 'marco' });
    function desenhar() {
      u.limpar(li);
      const meta = [
        p.rotulo || null,
        p.data_origem === 'registro' ? 'data do registro' : null,
        p.data_origem === 'ajustada' ? 'data calculada pelo texto' : null,
      ].filter(Boolean).join(' · ');
      const titulo = p.tipo === 'registro' ? `“${p.titulo || ''}”` : (p.titulo || '');
      u.anexar(li, h('div', { class: 'marco-link' },
        h('span', { class: 'marco-data' }, h('span', { 'aria-hidden': 'true' }, `${p.icone || '📌'} `), p.quando || u.dataBr(p.data)),
        h('span', { class: 'item-resumo' }, titulo),
        p.certeza ? h('span', null, E.selo(p.certeza)) : null,
        meta ? h('span', { class: 'sutil' }, meta) : null,
        p.descricao ? h('span', null, p.descricao) : null,
        p.trecho && p.tipo !== 'registro' && p.trecho !== p.titulo ? u.citacao(p.trecho) : null,
        lista(p.pessoas).length ? h('span', { class: 'sutil' }, h('span', { 'aria-hidden': 'true' }, '👥 '), lista(p.pessoas).join(', ')) : null,
        lista(p.motivos).length ? h('span', { class: 'sutil' }, `Está aqui porque é ${lista(p.motivos).join(', ')}.`) : null,
        p.registro_id != null ? detalhesContexto(p.registro_id) : null,
        p.tipo === 'marco' && p.marco_id != null
          ? h('div', { class: 'acoes' },
            h('button', { type: 'button', class: 'botao botao-texto', onclick: () => formEditar(p, li, desenhar) }, '✏️ Editar'),
            botaoApagar(p))
          : null));
    }
    desenhar();
    return li;
  }

  function linhaDoTempo(anos) {
    const caixa = h('div', { class: 'linha-tempo' });
    for (const ano of anos) {
      const id = u.novoId('ano');
      u.anexar(caixa,
        h('h2', { class: 'mes-titulo', id }, String(ano.ano)),
        h('ul', { class: 'lista-registros', 'aria-labelledby': id }, lista(ano.pontos).map(pontoDaLinha)));
    }
    return caixa;
  }

  function filtro(tudo) {
    const botao = (rotulo, alvo, ativo) => h('button', {
      type: 'button', class: 'botao botao-escolha botao-pequeno', 'aria-pressed': ativo ? 'true' : 'false',
      onclick: () => { if (!ativo) navegar(alvo); },
    }, rotulo);
    return h('div', { class: 'barra-acoes', role: 'group', 'aria-label': 'O que mostrar na linha do tempo' },
      botao('O essencial', '#tempo', !tudo),
      botao('Tudo', '#tempo/tudo', tudo));
  }

  async function telaTempo(main, partes, atual) {
    const tudo = partes[0] === 'tudo';
    u.anexar(main,
      u.cabecalho('Linha do tempo', 'A história de vocês, ano a ano: os marcos, o tempo juntos, o que aconteceu e o que você guardou. Só o que foi registrado.', '🕰️'),
      u.carregando());
    const [linha, neste] = await Promise.all([
      api('GET', `/marcos/linha${tudo ? '?tudo=1' : ''}`),
      api('GET', '/marcos/neste-dia'),
    ]);
    if (!atual()) return;
    const carregando = main.querySelector('.carregando');
    if (carregando) carregando.remove();
    const dados = linha || {};
    const anos = lista(dados.anos);
    u.anexar(main, cartaoVoces(dados.tempo), secaoNesteDia(neste || {}), formNovo());
    const contagem = dados.contagem || {};
    const resumo = [
      contagem.marco ? u.plural(contagem.marco, '1 marco', `${contagem.marco} marcos`) : null,
      contagem.evento ? u.plural(contagem.evento, '1 acontecimento', `${contagem.evento} acontecimentos`) : null,
      contagem.registro ? u.plural(contagem.registro, '1 registro marcante', `${contagem.registro} registros marcantes`) : null,
    ].filter(Boolean).join(', ');
    u.anexar(main, u.secao('🕰️ Ano a ano',
      filtro(tudo),
      resumo ? h('p', { class: 'sutil' }, `${u.capitalizar(resumo)}.`) : null,
      anos.length
        ? linhaDoTempo(anos)
        : u.estadoVazio(tudo
          ? 'Ainda não há nada na linha do tempo. Quando você registrar o primeiro encontro, uma viagem ou um dia especial, ele aparece aqui.'
          : 'Ainda não há nada marcante por aqui. Experimente “Tudo”, ou registre o primeiro encontro, uma viagem ou um dia especial.', true)));
  }

  // ---------------------------------------------------------------- revisão (o extra "marco")

  function dicaData(e) {
    if (e._dataMudou) return 'Você escolheu esta data.';
    const partes = [];
    if (e.data_expressao) partes.push(`Você escreveu “${e.data_expressao}”.`);
    if (e.precisao === 'mes') partes.push('Vale o mês todo, não um dia certo.');
    if (e.precisao === 'ano') partes.push('Vale o ano, não um dia certo.');
    if (e.precisao === 'semana') partes.push('Vale a semana, não um dia certo.');
    if (e.nota_data) partes.push(e.nota_data);
    return partes.join(' ') || null;
  }

  function revisaoMarco(e, ctx) {
    if (e._dataOriginal === undefined) e._dataOriginal = e.data || '';
    const idTitulo = u.novoId('rev-marco-titulo');
    const idData = u.novoId('rev-marco-data');
    const idTipo = u.novoId('rev-marco-tipo');
    const idDesc = u.novoId('rev-marco-desc');
    const limite = ctx && ctx.revisao && ctx.revisao.data_registro ? ctx.revisao.data_registro : u.hojeIso();
    const parecidos = h('div', { class: 'parecidos', 'aria-live': 'polite' });
    const titulo = h('input', {
      id: idTitulo, type: 'text', maxlength: '120', value: e.titulo || e.resumo || '',
      oninput: (ev) => { e.titulo = ev.target.value; e.resumo = ev.target.value; },
      onchange: () => { e._parecidos = undefined; desenharParecidos(e, parecidos); },
    });
    const data = h('input', {
      id: idData, type: 'date', max: limite, value: e.data || '',
      onchange: (ev) => {
        e.data = ev.target.value || null;
        e._dataMudou = (ev.target.value || '') !== e._dataOriginal;
        if (e._dataMudou) {
          e.precisao = 'dia';
          e.data_expressao = null;
          e.data_origem = 'manual';
        }
        e._parecidos = undefined;
        desenharParecidos(e, parecidos);
      },
    });
    const tipoAtual = h('span', null, rotuloTipo(e.marco_tipo));
    const select = selectTipo(idTipo, e.marco_tipo || 'outro', (v) => {
      e.marco_tipo = v;
      u.limpar(tipoAtual);
      u.anexar(tipoAtual, rotuloTipo(v));
      e._parecidos = undefined;
      desenharParecidos(e, parecidos);
    });
    const descricao = h('input', {
      id: idDesc, type: 'text', maxlength: '300', value: e.descricao || '',
      oninput: (ev) => { e.descricao = ev.target.value || null; },
    });
    desenharParecidos(e, parecidos);
    return [
      h('div', { class: 'item-topo' }, tipoAtual,
        h('span', { class: 'chip' }, h('span', { 'aria-hidden': 'true' }, '🕰️ '), 'Marco de vocês'),
        e.forca === 'fraca' ? h('span', { class: 'chip' }, '🔵 confira') : null),
      u.campo('Nome do marco', titulo),
      u.campo('Quando aconteceu?', data, dicaData(e)),
      u.campo('Tipo', select),
      u.campo('Um detalhe (opcional)', descricao, 'Ex.: o lugar, a cidade.'),
      e.pessoa ? h('p', { class: 'sutil' }, `Quem: ${e.pessoa}`) : null,
      e.motivo ? h('p', { class: 'dica' }, e.motivo) : null,
      u.citacao(e.trecho),
      h('p', { class: 'sutil' }, 'Guardado, ele entra na linha do tempo e volta no “Neste dia”.'),
      parecidos,
    ];
  }

  // Já existe um marco parecido? (consulta uma vez por mudança; a resposta fica guardada no extra)
  function desenharParecidos(e, caixa) {
    const mostrar = (itens) => {
      u.limpar(caixa);
      const igual = lista(itens).find((p) => p && p.igual);
      const parecido = igual || lista(itens)[0];
      if (!parecido) return;
      u.anexar(caixa, h('p', { class: 'dica' },
        igual ? 'Este marco já está guardado: ' : 'Você já guardou um marco parecido: ',
        `“${parecido.titulo}” (${parecido.quando})`, '. ',
        igual ? 'Confirmar aqui não cria outro igual.' : 'Se for o mesmo, talvez não precise guardar de novo.'));
    };
    if (Array.isArray(e._parecidos)) {
      mostrar(e._parecidos);
      return;
    }
    if (e._buscandoParecidos) return;
    e._buscandoParecidos = true;
    api('POST', '/marcos/parecidos', { tipo: e.marco_tipo || null, titulo: String(e.titulo || e.resumo || ''), data: e.data || null })
      .then((r) => { e._parecidos = lista(r); if (caixa.isConnected) mostrar(e._parecidos); })
      .catch(() => { e._parecidos = []; })
      .finally(() => { e._buscandoParecidos = false; });
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

  function listaMomentos(momentos) {
    const itens = lista(momentos);
    if (!itens.length) return null;
    return h('ul', { class: 'lista-simples' }, itens.map((m) => h('li', null,
      h('span', { 'aria-hidden': 'true' }, '⭐ '), m.titulo || '', ' ',
      h('span', { class: 'sutil' }, `(${m.quando || u.dataBr(m.data)})`),
      m.registro_id != null ? [' · ', u.linkRegistro(m.registro_id, 'ver')] : null)));
  }

  function rodape(c, ctx, extra) {
    return h('div', { class: 'alerta-acoes' },
      porqueDe(c) || h('span'),
      h('div', { class: 'acoes' },
        extra || null,
        h('a', { href: '#tempo', class: 'botao botao-texto' }, 'Linha do tempo'),
        ctx.botaoDispensar()));
  }

  function cartaoTempo(c, ctx) {
    const d = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '⏳ '), c.texto),
      lista(d.momentos).length ? h('p', { class: 'sutil' }, 'Alguns momentos de vocês que você guardou:') : null,
      listaMomentos(d.momentos),
      rodape(c, ctx),
    ];
  }

  function cartaoRelembrar(c, ctx) {
    const d = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '💞 '), c.texto),
      listaMomentos(d.momentos),
      rodape(c, ctx),
      h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: quem sabe a hora é você.'),
    ];
  }

  function cartaoAniversario(c, ctx) {
    const d = c.dados || {};
    const m = d.marco || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, `${m.icone || '⭐'} `), c.texto),
      d.trecho ? u.citacao(d.trecho) : null,
      rodape(c, ctx),
    ];
  }

  function cartaoNesteDia(c, ctx) {
    const d = c.dados || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, d.fonte === 'marco' ? '⭐ ' : '📅 '), c.texto),
      rodape(c, ctx),
      h('p', { class: 'sutil possibilidade' }, 'É só uma lembrança do que você registrou.'),
    ];
  }

  function cartaoLuto(c, ctx) {
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '🕊️ '), c.texto),
      rodape(c, ctx),
      h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: quem conhece ela é você.'),
    ];
  }

  E.registrarMenu('tempo', 'Linha do tempo', '🕰️');
  E.registrarTela('tempo', telaTempo);
  E.registrarRevisaoExtra('marco', revisaoMarco);
  E.registrarCartaoHoje('marco_tempo', cartaoTempo);
  E.registrarCartaoHoje('marco_relembrar', cartaoRelembrar);
  E.registrarCartaoHoje('marco_aniversario', cartaoAniversario);
  E.registrarCartaoHoje('neste_dia', cartaoNesteDia);
  E.registrarCartaoHoje('neste_dia_luto', cartaoLuto);
})();
