/* Lugares (DESIGN.md, seção 20.5): a tela do recurso "lugares".
 *
 * - Aba "📍 Lugares" em Conhecendo Ela: a sugestão da semana (com o porquê), o que ela quer conhecer
 *   (em ordem de uma pontuação explicada, com a origem de cada coisa), os lugares de vocês com a
 *   opinião dela e as ressalvas, o que é melhor evitar e as pistas do que ela curte. Filtros por tipo
 *   e por opinião; corrigir, "Fomos!", apagar um lugar ou uma menção que não é bem isso.
 * - Cartão do Hoje "lugares_sugestao": no máximo um lugar por semana, como possibilidade.
 * - Revisão: o miolo do extra "lugar" (nome, tipo, cidade, situação e opinião podem ser corrigidos).
 *
 * Só JS puro com EntreNos.h (texto do usuário nunca vira HTML), sem estilo inline (CSP), com as classes
 * do estilo.css. O recurso se encaixa por window.EntreNos. */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E) return;
  const { api, h, selo } = E;
  const U = E.util;

  const TIPOS = {
    restaurante: ['Restaurante', '🍽️'],
    bar: ['Bar e bebidas', '🍻'],
    cafe: ['Café e padaria', '☕'],
    doces: ['Doces e sorvete', '🍦'],
    praia: ['Praia', '🏖️'],
    natureza: ['Natureza', '🌳'],
    cultura: ['Cultura', '🖼️'],
    cinema: ['Cinema', '🎬'],
    show: ['Show e festa', '🎶'],
    compras: ['Compras e feiras', '🛍️'],
    hospedagem: ['Hospedagem', '🏨'],
    cidade: ['Cidade ou viagem', '🧳'],
    passeio: ['Passeio', '📍'],
    outro: ['Outro', '📍'],
  };
  const QUEM_QUER = { ela: 'Ela quer', nos: 'Vocês querem', eu: 'Ideia sua' };
  const OPINIOES = { 1: ['💚', 'Ela gostou'], '-1': ['💔', 'Ela não gostou'], '': ['❔', 'Ainda não sei'] };
  const POSSIBILIDADE = 'É só uma possibilidade: quem conhece ela é você.';
  const SEM_SUGESTAO = {
    dispensada: 'Você deixou a sugestão desta semana para depois. Na semana que vem pode aparecer outra.',
    saiu: 'Vocês já saíram esta semana. 💙 A próxima sugestão vem na semana que vem.',
  };

  // ================================================================ pedaços

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function capitalizar(s) {
    return U.capitalizar(String(s || ''));
  }

  function tipoDe(codigo) {
    return TIPOS[codigo] || TIPOS.outro;
  }

  function chip(texto, emoji, classe) {
    return h('li', { class: classe || 'chip' }, emoji ? h('span', { 'aria-hidden': 'true' }, `${emoji} `) : null, texto);
  }

  function chipOpiniao(opiniao) {
    const [emoji, texto] = OPINIOES[opiniao === 1 || opiniao === -1 ? String(opiniao) : ''];
    return chip(texto, emoji, opiniao === 1 ? 'chip chip-confirmado' : 'chip');
  }

  function porque(titulo, ...conteudo) {
    return h('details', { class: 'porque' }, h('summary', null, titulo), conteudo);
  }

  function rodapeOrigem(o) {
    const partes = [];
    if (o.quando) partes.push(`registrado ${o.quando}`);
    else if (o.data_registro) partes.push(`registrado em ${U.dataBr(o.data_registro)}`);
    return h('p', { class: 'entrada-rodape sutil' }, partes.join(' · '),
      o.registro_id != null ? [partes.length ? ' · ' : '', U.linkRegistro(o.registro_id, 'ver registro')] : null);
  }

  // A frase literal, ou "frase reservada" quando o servidor não manda (registro discreto, seção 20.10).
  function citar(o) {
    if (o && o.trecho) return U.citacao(o.trecho);
    return o && o.reservado ? h('p', { class: 'sutil' }, '🔒 Frase reservada (abra o registro para ver).') : null;
  }

  function citacaoComOrigem(o, extra) {
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, o.certeza ? selo(o.certeza) : null),
      citar(o),
      rodapeOrigem(o),
      extra || null);
  }

  // A data de uma viagem com a precisão dita ("novembro", e não 01/11): o servidor manda o rótulo.
  function dataViagem(v) {
    return (v && v.rotulo) || U.dataBr(v && v.data);
  }

  async function agir(botao, fazer, mensagem, recarregar) {
    if (botao) botao.disabled = true;
    try {
      await fazer();
      if (mensagem) U.avisar(mensagem);
      if (recarregar) recarregar();
    } catch (e) {
      if (botao) botao.disabled = false;
      if (!e.bloqueio) U.avisarErro(e);
    }
  }

  function selectTipo(id, valor) {
    return h('select', { id, class: 'select-pequeno', value: TIPOS[valor] ? valor : 'outro' },
      Object.entries(TIPOS).map(([k, [rotulo, emoji]]) => h('option', { value: k }, `${emoji} ${rotulo}`)));
  }

  function selectOpiniao(id, valor) {
    const atual = valor === 1 || valor === -1 ? String(valor) : '';
    return h('select', { id, class: 'select-pequeno', value: atual },
      h('option', { value: '' }, 'Ainda não sei'),
      h('option', { value: '1' }, 'Ela gostou'),
      h('option', { value: '-1' }, 'Ela não gostou'));
  }

  function valorOpiniao(texto) {
    return texto === '1' ? 1 : texto === '-1' ? -1 : null;
  }

  function caminhoLugar(id) {
    return `/lugares/${encodeURIComponent(String(id))}`;
  }

  // ================================================================ aba "Lugares" em Conhecendo Ela

  function abaLugares(painel, ctx) {
    const atual = ctx && typeof ctx.atual === 'function' ? ctx.atual : () => true;
    const filtros = { tipo: null, opiniao: 'todos' };
    // `foco`: o seletor de um elemento para receber o foco depois de redesenhar (o select que acabou
    // de ser salvo), para quem usa teclado não perder o lugar na página.
    const recarregar = (foco) => {
      if (!atual() || !painel.isConnected) return;
      carregar(typeof foco === 'string' ? foco : null);
    };
    async function carregar(foco) {
      U.limpar(painel);
      U.anexar(painel, U.carregando('Juntando os lugares que você registrou…'));
      let dados;
      try {
        dados = await api('GET', '/lugares');
      } catch (e) {
        if (e.bloqueio || !atual()) return;
        U.limpar(painel);
        U.anexar(painel, h('p', { class: 'erro-campo', role: 'alert' }, (e && e.message) || 'Esta parte não abriu agora.'));
        return;
      }
      if (!atual()) return;
      desenhar(dados);
      const alvo = foco ? painel.querySelector(foco) : null;
      if (alvo) alvo.focus();
    }
    function desenhar(dados) {
      U.limpar(painel);
      if (dados.vazio) {
        U.anexar(painel,
          U.estadoVazio('Ainda não há lugares. Quando você registrar “fomos no Coco Bambu e ela amou” ou “ela quer conhecer o Beco do Batman”, eles aparecem aqui.', true));
        return;
      }
      const tipos = Object.keys(dados.contagem_tipos || {}).filter((t) => TIPOS[t]);
      const corpo = h('div');
      const redesenharListas = () => {
        U.limpar(corpo);
        U.anexar(corpo,
          secaoQuerConhecer(lista(dados.quer_conhecer).filter(passaTipo), recarregar),
          secaoFomos(lista(dados.fomos).filter(passaTipo), recarregar, filtros, redesenharListas));
      };
      function passaTipo(l) {
        return !filtros.tipo || l.tipo === filtros.tipo;
      }
      U.anexar(painel,
        h('p', { class: 'sutil' }, 'Só o que você registrou, com a origem de cada coisa. As sugestões são possibilidades, não ordens.'),
        dados.momento ? secaoMomento(dados.momento) : null,
        dados.sugestao ? secaoSugestao(dados.sugestao, recarregar)
          : (SEM_SUGESTAO[dados.sem_sugestao] ? h('p', { class: 'dica' }, SEM_SUGESTAO[dados.sem_sugestao]) : null),
        tipos.length > 1 ? barraTipos(tipos, dados.contagem_tipos, filtros, redesenharListas) : null,
        corpo,
        secaoEvitar(lista(dados.evitar_lugares), lista((dados.pistas || {}).evitar), recarregar),
        secaoPistas(lista((dados.pistas || {}).curte)));
      redesenharListas();
    }
    carregar();
  }

  function barraTipos(tipos, contagem, filtros, redesenhar) {
    const botoes = [];
    const marcar = () => botoes.forEach((b) => b.setAttribute('aria-pressed', String((b.dataset.tipo || null) === filtros.tipo)));
    const botao = (codigo, texto) => {
      const b = h('button', {
        type: 'button', class: 'botao botao-escolha botao-pequeno', 'data-tipo': codigo || '', 'aria-pressed': 'false',
        onclick: () => { filtros.tipo = codigo; marcar(); redesenhar(); },
      }, texto);
      botoes.push(b);
      return b;
    };
    const barra = h('div', { class: 'opcoes', role: 'group', 'aria-label': 'Mostrar só um tipo de lugar' },
      botao(null, 'Todos'),
      tipos.map((t) => botao(t, `${tipoDe(t)[1]} ${tipoDe(t)[0]} (${contagem[t]})`)));
    marcar();
    return barra;
  }

  function secaoMomento(m) {
    return h('section', { class: 'cartao', 'aria-labelledby': 'lg-momento' },
      h('h2', { id: 'lg-momento' }, h('span', { 'aria-hidden': 'true' }, '💙 '), 'Para este momento'),
      h('p', null, `Você registrou ${m.quando || 'há pouco'}: `, h('strong', null, m.texto || '')),
      citar(m),
      m.registro_id != null ? h('p', { class: 'entrada-rodape sutil' }, U.linkRegistro(m.registro_id, 'ver registro')) : null,
      h('p', { class: 'sutil' }, m.bloqueia
        ? 'Por isso, nenhuma sugestão de sair por enquanto. A lista continua aqui para quando fizer sentido.'
        : 'Talvez um lugar mais tranquilo combine mais agora: eles aparecem um pouco antes na lista.'));
  }

  function linhaMotivos(motivos) {
    return lista(motivos).length ? h('ul', { class: 'lista-simples' }, lista(motivos).map((m) => h('li', null, m))) : null;
  }

  function secaoSugestao(s, recarregar) {
    const [rotulo, emoji] = tipoDe(s.tipo);
    const fomos = botaoFomos(s.lugar_id, recarregar, '✔️ Fomos!');
    return h('section', { class: 'cartao', 'aria-labelledby': 'lg-sugestao' },
      h('h2', { id: 'lg-sugestao' }, h('span', { 'aria-hidden': 'true' }, '💡 '), 'Sugestão da semana'),
      h('p', { class: 'alerta-texto' }, s.texto || ''),
      h('ul', { class: 'chips', 'aria-label': 'Sobre o lugar' }, chip(rotulo, emoji),
        s.cidade ? chip(s.cidade, '🏙️') : null),
      lista(s.avisos).map((a) => h('p', { class: 'nota-cuidado' }, a)),
      porque('Por que este lugar?', linhaMotivos(s.motivos),
        h('ul', { class: 'lista-entradas' }, lista(s.origens).map((o) => citacaoComOrigem(o)))),
      h('p', { class: 'sutil possibilidade' }, POSSIBILIDADE),
      h('div', { class: 'acoes' }, fomos.botao), fomos.form);
  }

  // "Fomos!": com a data (hoje por padrão; nunca no futuro).
  function botaoFomos(id, recarregar, rotulo) {
    const idData = U.novoId('lg-fomos-data');
    const data = h('input', { id: idData, type: 'date', value: U.hojeIso(), max: U.hojeIso() });
    const confirmar = h('button', { type: 'button', class: 'botao botao-primario botao-pequeno' }, '✔️ Confirmar');
    const form = h('div', { class: 'form-item' },
      U.campo('Quando vocês foram', data, 'Depois, se quiser, conte em Registrar como foi.'),
      h('div', { class: 'acoes' }, confirmar,
        h('button', { type: 'button', class: 'botao botao-texto', onclick: () => { form.hidden = true; botao.hidden = false; botao.focus(); } }, 'Cancelar')));
    form.hidden = true;
    const botao = h('button', {
      type: 'button', class: 'botao botao-secundario botao-pequeno',
      onclick: () => { form.hidden = false; botao.hidden = true; data.focus(); },
    }, rotulo || '✔️ Fomos');
    confirmar.addEventListener('click', () => agir(confirmar,
      () => api('POST', `${caminhoLugar(id)}/fomos`, { data: data.value || null }),
      'Anotado! Agora ele está nos lugares de vocês.', recarregar));
    return { botao, form };
  }

  function formCorrigir(l, recarregar) {
    const ids = { nome: U.novoId('lg-nome'), tipo: U.novoId('lg-tipo'), cidade: U.novoId('lg-cidade') };
    const nome = h('input', { id: ids.nome, type: 'text', maxlength: '80', autocomplete: 'off', value: l.nome || '' });
    const tipo = selectTipo(ids.tipo, l.tipo);
    const cidade = h('input', { id: ids.cidade, type: 'text', maxlength: '60', autocomplete: 'off', value: l.cidade || '' });
    const salvar = h('button', { type: 'button', class: 'botao botao-primario botao-pequeno' }, 'Salvar');
    const form = h('div', { class: 'form-item' },
      U.campo('Nome do lugar', nome),
      h('div', { class: 'grade-2' }, U.campo('Tipo', tipo), U.campo('Cidade', cidade, 'Deixe em branco se não importa.')),
      h('p', { class: 'dica' }, 'O que você corrigir aqui vale mais do que os registros.'),
      h('div', { class: 'acoes' }, salvar,
        h('button', { type: 'button', class: 'botao botao-texto', onclick: () => { form.hidden = true; abrir.hidden = false; abrir.focus(); } }, 'Cancelar')));
    form.hidden = true;
    const abrir = h('button', {
      type: 'button', class: 'botao botao-texto',
      onclick: () => { form.hidden = false; abrir.hidden = true; nome.focus(); },
    }, '✏️ Corrigir');
    salvar.addEventListener('click', () => {
      const mudancas = {};
      if (nome.value.trim() !== (l.nome || '')) mudancas.nome = nome.value.trim();
      if (tipo.value !== l.tipo) mudancas.tipo = tipo.value;
      if (cidade.value.trim() !== (l.cidade || '')) mudancas.cidade = cidade.value.trim() || null;
      if (!Object.keys(mudancas).length) {
        form.hidden = true;
        abrir.hidden = false;
        return;
      }
      agir(salvar, () => api('PATCH', caminhoLugar(l.id), mudancas), 'Correção guardada.', recarregar);
    });
    return { abrir, form };
  }

  function botaoApagar(l, recarregar) {
    const apagar = h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto' }, '🗑️ Apagar');
    apagar.addEventListener('click', async () => {
      const ok = await U.confirmar({
        titulo: `Apagar “${l.nome}”?`,
        texto: 'O lugar sai daqui e das cópias automáticas. Os registros de onde ele veio continuam guardados. Não dá para desfazer.',
        ok: 'Apagar', perigo: true,
      });
      if (ok) agir(apagar, () => api('DELETE', caminhoLugar(l.id)), 'Lugar apagado.', recarregar);
    });
    return apagar;
  }

  // As menções (cada registro em que o lugar apareceu), com "isto não é sobre este lugar".
  function deOndeVeio(l, recarregar) {
    const mencoes = lista(l.mencoes);
    if (!mencoes.length) return null;
    return porque(mencoes.length > 1 ? `De onde veio (${mencoes.length} registros)` : 'De onde veio',
      h('ul', { class: 'lista-entradas' }, mencoes.map((m) => {
        const tirar = h('button', { type: 'button', class: 'botao botao-texto' }, 'Não é sobre este lugar');
        tirar.addEventListener('click', async () => {
          const ok = await U.confirmar({
            titulo: 'Tirar esta menção do lugar?',
            texto: 'O registro continua guardado; só deixa de contar para este lugar. Se era a única menção, o lugar sai da lista.',
            ok: 'Tirar',
          });
          if (ok) agir(tirar, () => api('DELETE', `/lugares/mencoes/${encodeURIComponent(String(m.id))}`), 'Pronto.', recarregar);
        });
        const extra = [];
        if (m.reacao) extra.push(h('p', { class: 'sutil' }, `Reação dela: “${m.reacao}”`));
        else if (m.reservado && !m.trecho) extra.push(h('p', { class: 'sutil' }, 'Reação reservada (abra o registro para ver).'));
        if (lista(m.ressalvas).length) extra.push(h('p', { class: 'sutil' }, `Ressalvas: ${lista(m.ressalvas).join(', ')}`));
        if (m.data) extra.push(h('p', { class: 'sutil' }, `📅 ${U.dataBr(m.data)}${m.data_expressao ? ` (“${m.data_expressao}”)` : ''}`));
        else if (m.data_expressao) extra.push(h('p', { class: 'sutil' }, `📅 “${m.data_expressao}”`));
        extra.push(h('div', { class: 'acoes' }, tirar));
        return citacaoComOrigem(m, extra);
      })));
  }

  // ---------------------------------------------------------------- ela quer conhecer

  function secaoQuerConhecer(itens, recarregar) {
    return U.secao('✨ Ela quer conhecer (e vocês querem ir)',
      h('p', { class: 'sutil' }, 'Primeiro o que ela mesma quer, o que ela disse com as palavras dela, o que apareceu mais vezes ou há pouco e o que combina com o que ela gosta. Cada um mostra o porquê.'),
      itens.length
        ? h('ul', { class: 'lista-cartoes' }, itens.map((l) => cartaoQuer(l, recarregar)))
        : U.vazioPequeno('Nada por aqui ainda. Quando ela comentar que quer conhecer um lugar, ele aparece aqui.'));
  }

  function cartaoQuer(l, recarregar) {
    const [rotulo, emoji] = tipoDe(l.tipo);
    const fomos = botaoFomos(l.id, recarregar);
    const corrigir = formCorrigir(l, recarregar);
    const chips = [chip(rotulo, emoji)];
    if (l.cidade) chips.push(chip(l.cidade, '🏙️'));
    if (QUEM_QUER[l.quem_quer]) chips.push(chip(QUEM_QUER[l.quem_quer], '💭', l.quem_quer === 'ela' ? 'chip chip-confirmado' : 'chip'));
    if (l.quer_voltar) chips.push(chip('Quer voltar', '🔁'));
    if (l.vezes > 1) chips.push(chip(`${l.vezes} vezes`, '🗨️'));
    if (l.planejado) chips.push(chip(`Combinado: ${U.dataBr(l.planejado.data)}`, '📅', 'chip chip-ligada'));
    return h('li', { class: 'cartao' },
      h('div', { class: 'entrada-topo' }, selo(l.certeza), h('h3', { class: 'entrada-texto' }, capitalizar(l.nome))),
      h('ul', { class: 'chips', 'aria-label': 'Sobre o lugar' }, chips),
      l.quando ? h('p', { class: 'sutil' }, `Comentado ${l.quando}`) : null,
      l.planejado ? h('p', { class: 'dica' }, l.planejado.texto) : null,
      l.viagem ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '🧳 '),
        `Vocês têm uma viagem para ${l.viagem.cidade} (${dataViagem(l.viagem)}).`) : null,
      lista(l.avisos).map((a) => h('p', { class: 'nota-cuidado' }, a)),
      lista(l.motivos).length ? porque('Por que aparece aqui?', linhaMotivos(l.motivos)) : null,
      deOndeVeio(l, recarregar),
      h('div', { class: 'acoes' }, fomos.botao, corrigir.abrir, botaoApagar(l, recarregar)),
      fomos.form, corrigir.form);
  }

  // ---------------------------------------------------------------- lugares de vocês

  function secaoFomos(itens, recarregar, filtros, redesenhar) {
    const passa = (l) => filtros.opiniao === 'todos'
      || (filtros.opiniao === 'gostou' && l.opiniao === 1)
      || (filtros.opiniao === 'nao_gostou' && l.opiniao === -1)
      || (filtros.opiniao === 'sem' && l.opiniao !== 1 && l.opiniao !== -1);
    const opcoes = [['todos', 'Todos'], ['gostou', '💚 Ela gostou'], ['nao_gostou', '💔 Não gostou'], ['sem', '❔ Sem opinião']];
    // A barra é desenhada de novo com a lista: o foco volta ao botão tocado (quem usa teclado não o perde).
    const barra = itens.length > 1 ? h('div', { class: 'opcoes', role: 'group', 'aria-label': 'Mostrar pela opinião dela' },
      opcoes.map(([valor, texto]) => h('button', {
        type: 'button', class: 'botao botao-escolha botao-pequeno', 'aria-pressed': String(filtros.opiniao === valor),
        'data-filtro-opiniao': valor,
        onclick: (ev) => {
          const grupo = ev.currentTarget.closest('section') || document;
          const raiz = grupo.parentNode || document;
          filtros.opiniao = valor;
          redesenhar();
          const novo = raiz.querySelector(`[data-filtro-opiniao="${valor}"]`);
          if (novo) novo.focus();
        },
      }, texto))) : null;
    const mostrados = itens.filter(passa);
    return U.secao('📍 Lugares de vocês (e os que ela conhece)',
      barra,
      mostrados.length
        ? h('ul', { class: 'lista-cartoes' }, mostrados.map((l) => cartaoFomos(l, recarregar)))
        : U.vazioPequeno(itens.length ? 'Nenhum lugar com essa opinião.' : 'Quando você registrar um lugar onde vocês foram, ele aparece aqui com a reação dela.'));
  }

  function cartaoFomos(l, recarregar) {
    const [rotulo, emoji] = tipoDe(l.tipo);
    const corrigir = formCorrigir(l, recarregar);
    // Id estável (o foco volta para ele) e salvar só com o botão: no Windows as setas num select fechado
    // disparam "change" a cada tecla (revisão de 07/10).
    const idOpiniao = `lg-opiniao-${String(l.id).replace(/[^0-9a-z_-]/gi, '')}`;
    const opiniao = selectOpiniao(idOpiniao, l.opiniao);
    const inicial = opiniao.value;
    const salvarOpiniao = h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno', disabled: true },
      'Salvar');
    opiniao.addEventListener('change', () => { salvarOpiniao.disabled = opiniao.value === inicial; });
    salvarOpiniao.addEventListener('click', () => agir(salvarOpiniao,
      () => api('PATCH', caminhoLugar(l.id), { opiniao: valorOpiniao(opiniao.value) }), 'Opinião guardada.',
      () => recarregar(`#${idOpiniao}`)));
    const naoFomos = h('button', { type: 'button', class: 'botao botao-texto' }, '↩️ Ainda não fomos');
    naoFomos.addEventListener('click', () => agir(naoFomos,
      () => api('PATCH', caminhoLugar(l.id), { status: 'queremos_ir' }), 'Voltou para a lista de lugares para ir.', recarregar));
    const chips = [chip(rotulo, emoji), chip(l.juntos ? 'Vocês foram' : 'Ela conhece', l.juntos ? '💑' : '🙋‍♀️'),
      chipOpiniao(l.opiniao)];
    if (l.cidade) chips.push(chip(l.cidade, '🏙️'));
    if (l.quer_voltar) chips.push(chip('Ela quer voltar', '🔁', 'chip chip-confirmado'));
    if (l.visitas > 1) chips.push(chip(`${l.visitas} idas`, '🗓️'));
    const quando = l.ultima_visita
      ? `Última ida: ${U.dataBr(l.ultima_visita)}${l.ultima_visita_quando ? ` (${l.ultima_visita_quando})` : ''}`
      : (l.quando ? `Registrado ${l.quando}` : '');
    return h('li', { class: 'cartao' },
      h('div', { class: 'entrada-topo' }, selo(l.certeza), h('h3', { class: 'entrada-texto' }, capitalizar(l.nome))),
      h('ul', { class: 'chips', 'aria-label': 'Sobre o lugar' }, chips),
      quando ? h('p', { class: 'sutil' }, quando) : null,
      lista(l.ressalvas).length ? h('p', { class: 'nota-cuidado' }, `Ressalvas dela: ${lista(l.ressalvas).join(', ')}`) : null,
      lista(l.reacoes).length ? h('ul', { class: 'lista-entradas' }, lista(l.reacoes).slice(0, 2).map((r) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, selo(r.certeza), h('span', { class: 'entrada-texto' }, `“${r.texto}”`)),
        rodapeOrigem(r)))) : null,
      deOndeVeio(l, recarregar),
      h('div', { class: 'campo campo-linha' }, h('label', { for: idOpiniao }, 'O que ela achou?'), opiniao, salvarOpiniao),
      h('div', { class: 'acoes' }, corrigir.abrir, naoFomos, botaoApagar(l, recarregar)),
      corrigir.form);
  }

  // ---------------------------------------------------------------- evitar e pistas

  function secaoEvitar(lugares, pistas, recarregar) {
    if (!lugares.length && !pistas.length) return null;
    return U.secao('🙅 Melhor evitar',
      h('p', { class: 'sutil' }, 'Lugares que ela disse que não quer e o que ela não curte (ou não pode) — com o que você registrou.'),
      lugares.length ? h('ul', { class: 'lista-cartoes' }, lugares.map((l) => h('li', { class: 'cartao' },
        h('div', { class: 'entrada-topo' }, selo(l.certeza), h('h3', { class: 'entrada-texto' }, capitalizar(l.nome))),
        l.excluido ? h('p', { class: 'nota-cuidado' }, capitalizar(l.excluido) + '.') : null,
        deOndeVeio(l, recarregar),
        h('div', { class: 'acoes' }, botaoApagar(l, recarregar))))) : null,
      pistas.length ? h('ul', { class: 'lista-entradas' }, pistas.map((p) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, selo(p.certeza), h('span', { class: 'entrada-texto' }, p.texto || p.alvo || '')),
        citar(p),
        rodapeOrigem(p)))) : null);
  }

  function secaoPistas(pistas) {
    if (!pistas.length) return null;
    return U.secao('💡 Pistas do que ela curte',
      h('p', { class: 'sutil' }, 'Do que você já registrou sobre lugares e viagens. Ajuda a escolher quando surgir a ocasião.'),
      h('ul', { class: 'lista-entradas' }, pistas.map((p) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, selo(p.certeza), h('span', { class: 'entrada-texto' }, p.texto || p.alvo || '')),
        citar(p),
        rodapeOrigem(p)))));
  }

  // ================================================================ cartão do Hoje

  function cartaoSugestao(a, ctx) {
    const d = a.dados || {};
    const ids = lista(a.registro_ids);
    const [rotulo, emoji] = tipoDe(d.tipo);
    const fomos = h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno' }, '✔️ Fomos!');
    fomos.addEventListener('click', () => agir(fomos, async () => {
      await api('POST', `${caminhoLugar(d.lugar_id)}/fomos`, {});
      if (ctx && typeof ctx.remover === 'function') ctx.remover();
    }, 'Que bom! Ele foi para os lugares de vocês. Se quiser, conte como foi em Registrar.'));
    return [
      h('p', { class: 'alerta-texto' }, a.texto || ''),
      h('ul', { class: 'chips', 'aria-label': 'Sobre o lugar' }, chip(rotulo, emoji),
        d.cidade ? chip(d.cidade, '🏙️') : null,
        d.ocasiao ? chip(`${d.ocasiao.texto} · ${U.dataBr(d.ocasiao.data)}`, '💑') : null,
        d.viagem ? chip(`Viagem: ${dataViagem(d.viagem)}`, '🧳') : null),

      lista(d.avisos).map((x) => h('p', { class: 'nota-cuidado' }, x)),
      h('div', { class: 'alerta-acoes' },
        a.porque || ids.length
          ? porque('Por quê?', linhaMotivos(d.motivos),
            lista(d.origens).length ? h('ul', { class: 'lista-entradas' }, lista(d.origens).map((o) => citacaoComOrigem(o))) : null,
            ids.length ? h('p', { class: 'links-registros' }, ids.map((rid, n) => [n ? ' · ' : '',
              U.linkRegistro(rid, ids.length > 1 ? `ver registro (${n + 1} de ${ids.length})` : 'ver o registro')])) : null)
          : h('span'),
        fomos,
        h('a', { href: '#ela/lugares', class: 'botao botao-texto' }, '📍 Ver lugares'),
        ctx && typeof ctx.botaoDispensar === 'function' ? ctx.botaoDispensar() : null),
      h('p', { class: 'sutil possibilidade' }, `${POSSIBILIDADE} No máximo uma sugestão de lugar por semana.`),
    ];
  }

  // ================================================================ revisão (extras)

  function resumoDoLugar(e) {
    const nome = e.nome || '…';
    let base;
    if (e.mencao === 'visita') base = e.quem === 'ela' ? `Ela foi: ${nome}` : `Vocês foram: ${nome}`;
    else if (e.mencao === 'vontade') {
      if (e.quem === 'ela') base = `Ela quer ${e.voltar ? 'voltar' : (e.generico ? 'ir' : 'conhecer')}: ${nome}`;
      else if (e.quem === 'nos') base = `Vocês querem ${e.voltar ? 'voltar' : 'ir'}: ${nome}`;
      else base = `Você quer levar ela: ${nome}`;
    } else {
      base = e.opiniao === 1 ? `Ela gosta de: ${nome}` : (e.opiniao === -1 ? `Ela não gosta de: ${nome}` : `Ela comentou: ${nome}`);
    }
    if (e.cidade && !U.normalizar(nome).includes(U.normalizar(e.cidade))) base += ` (${e.cidade})`;
    if (e.mencao === 'visita' && (e.opiniao === 1 || e.opiniao === -1)) base += e.opiniao === 1 ? ' — ela gostou' : ' — ela não gostou';
    if (e.data && e.mencao === 'vontade') base += ` — ${U.diaMes(e.data)}`;
    return base;
  }

  function revisaoExtra(e) {
    if (e.tipo !== 'lugar') return null;
    const resumo = h('p', { class: 'extra-resumo' }, e.resumo || resumoDoLugar(e));
    const atualizar = () => {
      e.resumo = resumoDoLugar(e);
      resumo.textContent = e.resumo;
    };
    const ids = { nome: U.novoId('lg-r-nome'), tipo: U.novoId('lg-r-tipo'), cidade: U.novoId('lg-r-cidade'),
      status: U.novoId('lg-r-status'), opiniao: U.novoId('lg-r-op') };
    const nome = h('input', {
      id: ids.nome, type: 'text', maxlength: '80', autocomplete: 'off', value: e.nome || '',
      oninput: (ev) => { e.nome = ev.target.value.trim(); atualizar(); },
    });
    const tipo = selectTipo(ids.tipo, e.tipo_lugar);
    tipo.addEventListener('change', () => { e.tipo_lugar = tipo.value; });
    const cidade = h('input', {
      id: ids.cidade, type: 'text', maxlength: '60', autocomplete: 'off', value: e.cidade || '',
      oninput: (ev) => { e.cidade = ev.target.value.trim() || null; atualizar(); },
    });
    const opiniao = selectOpiniao(ids.opiniao, e.opiniao);
    const campoOpiniao = U.campo('O que ela achou', opiniao);
    const status = h('select', { id: ids.status, class: 'select-pequeno', value: e.status === 'queremos_ir' ? 'queremos_ir' : 'fomos' },
      h('option', { value: 'fomos' }, e.quem === 'ela' && e.mencao !== 'vontade' ? 'Ela já foi / conhece' : 'Vocês já foram'),
      h('option', { value: 'queremos_ir' }, 'Ainda vão (querem ir)'));
    const mostrarOpiniao = () => { campoOpiniao.hidden = e.status === 'queremos_ir'; };
    status.addEventListener('change', () => {
      e.status = status.value;
      // A data do texto era de outra situação ("vamos sábado" não é o dia em que foram).
      e.data = null;
      e.data_expressao = null;
      if (e.status === 'queremos_ir') {
        e.mencao = 'vontade';
        e.opiniao = null;
        opiniao.value = '';
      } else if (e.mencao === 'vontade') {
        e.mencao = 'visita';
      }
      mostrarOpiniao();
      atualizar();
    });
    opiniao.addEventListener('change', () => { e.opiniao = valorOpiniao(opiniao.value); atualizar(); });
    mostrarOpiniao();
    const [, emoji] = tipoDe(e.tipo_lugar);
    const detalhes = [];
    if (e.reacao) detalhes.push(h('p', { class: 'sutil' }, `Reação dela no texto: “${e.reacao}”`));
    if (lista(e.ressalvas).length) detalhes.push(h('p', { class: 'sutil' }, `Ressalvas no texto: ${lista(e.ressalvas).join(', ')}`));
    if (e.data_expressao) detalhes.push(h('p', { class: 'sutil' }, `Quando, pelo texto: “${e.data_expressao}”${e.data ? ` (${U.dataBr(e.data)})` : ''}`));
    if (e.generico) detalhes.push(h('p', { class: 'dica' }, 'É um tipo de lugar, sem nome. Se souber qual é, corrija o nome.'));
    return [
      h('div', { class: 'item-topo' },
        h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, `${emoji} `), 'Lugar'),
        e.certeza ? selo(e.certeza) : null),
      resumo,
      h('div', { class: 'form-item' },
        h('div', { class: 'grade-2' }, U.campo('O lugar', nome), U.campo('Tipo', tipo)),
        h('div', { class: 'grade-2' }, U.campo('Situação', status), U.campo('Cidade', cidade, 'Opcional.')),
        campoOpiniao),
      detalhes,
      e.trecho ? U.citacao(e.trecho) : null,
    ];
  }

  // ================================================================ encaixe

  E.registrarAbaEla('lugares', '📍 Lugares', abaLugares);
  E.registrarCartaoHoje('lugares_sugestao', cartaoSugestao);
  // Pelo NOME do recurso (o extra tem tipo "lugar"; registrar pelo nome evita disputar com outro recurso).
  E.registrarRevisaoExtra('lugares', revisaoExtra);
})();
