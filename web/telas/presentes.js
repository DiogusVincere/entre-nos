/* Ideias de presentes (DESIGN.md, seção 20.2): a tela do recurso "presentes".
 *
 * - Tela #presentes: o que ela comentou que gostaria (com "há 8 meses" e "talvez já dado"), ideias
 *   possíveis 🔵 (com o porquê e a origem), o que já está separado, o que ela já recebeu (de você e
 *   de outros), gostou / não gostou e o que não é recomendado. Tudo cita os registros.
 * - Cartão do Hoje "presentes_ocasiao": ocasião em até 45 dias com o que ela mencionou.
 * - Revisão: o miolo dos extras "presente" e "presente_desejo" (os campos podem ser corrigidos).
 *
 * Só JS puro com EntreNos.h (texto do usuário nunca vira HTML), sem estilo inline (CSP), com as classes do
 * estilo.css. O recurso se encaixa por window.EntreNos. */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E) return;
  const { api, h, selo } = E;
  const U = E.util;

  const REACOES = { gostou: 'Gostou', nao_gostou: 'Não gostou', neutra: 'Achou normal', nao_sei: 'Ainda não sei' };
  const EMOJI_REACAO = { gostou: '💚', nao_gostou: '💔', neutra: '😐', nao_sei: '❔' };
  const OCASIOES = {
    aniversario_dela: 'Aniversário dela',
    namoro: 'Aniversário de namoro',
    natal: 'Natal',
    dia_dos_namorados: 'Dia dos Namorados',
    pascoa: 'Páscoa',
    formatura: 'Formatura',
    dia_da_mulher: 'Dia da Mulher',
    sem_motivo: 'Sem data especial',
    outra: 'Outra ocasião',
  };
  const EMOJI_OCASIAO = {
    aniversario_dela: '🎂', namoro: '💑', natal: '🎄', dia_dos_namorados: '💘', pascoa: '🐣', formatura: '🎓',
    dia_da_mulher: '🌷', sem_motivo: '🎈', outra: '🎁',
  };
  const NAO_RECOMENDADO = {
    restricao: ['⚠️', 'Restrição'],
    nao_gosta: ['🚫', 'Ela não gosta'],
    nao_quer: ['🙅', 'Ela disse que não quer'],
    nao_gostou: ['💔', 'Não gostou quando ganhou'],
    ja_dado: ['🔁', 'Você já deu'],
    ja_ganhou: ['🔁', 'Ela ganhou há pouco'],
  };
  const POSSIBILIDADE = 'É só uma possibilidade: quem conhece ela é você.';

  // ================================================================ pedaços

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function capitalizar(s) {
    return U.capitalizar(String(s || ''));
  }

  function rotuloOcasiao(codigo) {
    return OCASIOES[codigo] || '';
  }

  function chipOcasiao(codigo) {
    if (!codigo || !OCASIOES[codigo]) return null;
    return h('span', { class: 'chip' }, h('span', { 'aria-hidden': 'true' }, `${EMOJI_OCASIAO[codigo] || '🎁'} `),
      OCASIOES[codigo]);
  }

  function chipReacao(reacao) {
    const r = REACOES[reacao] ? reacao : 'nao_sei';
    return h('span', { class: r === 'gostou' ? 'chip chip-confirmado' : 'chip' },
      h('span', { 'aria-hidden': 'true' }, `${EMOJI_REACAO[r]} `), REACOES[r]);
  }

  // "Você registrou há 8 meses · ver registro"
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

  function citacaoComOrigem(o) {
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, o.certeza ? selo(o.certeza) : null),
      citar(o),
      rodapeOrigem(o));
  }

  function porque(titulo, ...conteudo) {
    return h('details', { class: 'porque' }, h('summary', null, titulo), conteudo);
  }

  function quandoDoPresente(p) {
    if (p.planejado) {
      return p.data ? `para ${U.dataBr(p.data)}` : (p.ocasiao ? `para o ${rotuloOcasiao(p.ocasiao).toLowerCase()}` : 'ainda vai dar');
    }
    if (p.data) return `em ${U.dataBr(p.data)}${p.quando ? ` (${p.quando})` : ''}`;
    return p.quando ? `registrado ${p.quando}` : '';
  }

  function deQuem(p) {
    if (p.planejado) return 'Separado por você';
    if (p.de_quem === 'eu') return 'Você deu';
    return p.quem_texto ? `Ela ganhou ${p.quem_texto}` : 'Ela ganhou de outra pessoa';
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

  function selectOcasiao(id, valor, rotuloVazio = 'Sem ocasião') {
    return h('select', { id, class: 'select-pequeno', value: valor || '' },
      h('option', { value: '' }, rotuloVazio),
      Object.entries(OCASIOES).map(([k, v]) => h('option', { value: k }, v)));
  }

  function selectReacao(id, valor) {
    return h('select', { id, class: 'select-pequeno', value: REACOES[valor] ? valor : 'nao_sei' },
      Object.entries(REACOES).map(([k, v]) => h('option', { value: k }, v)));
  }

  // ================================================================ tela #presentes

  // `foco`: depois de redesenhar, o foco volta a este elemento (um select que acabou de ser salvo) em vez
  // de ir para o título: quem usa teclado não perde o lugar na página.
  async function telaPresentes(main, partes, atual, foco) {
    U.anexar(main, U.carregando('Juntando o que você registrou sobre presentes…'));
    let dados;
    try {
      dados = await api('GET', '/presentes');
    } catch (e) {
      if (e.bloqueio || !atual()) return;
      U.limpar(main);
      U.anexar(main, U.caixaErro(e));
      return;
    }
    if (!atual()) return;
    const recarregar = (seletor) => {
      if (!atual()) return;
      U.limpar(main);
      telaPresentes(main, partes, atual, typeof seletor === 'string' ? seletor : null);
    };
    U.limpar(main);
    U.anexar(main, U.cabecalho('Ideias de presentes', 'Só o que você registrou, com a origem de cada coisa.', '🎁'));
    if (dados.vazio) {
      U.anexar(main,
        U.estadoVazio('Ainda não há nada sobre presentes. Quando você registrar “ela queria um fone novo” ou “dei um colar pra ela e ela amou”, aparece aqui.', true),
        // O que você escondeu continua aparecendo, para dar para mostrar de novo (o aviso promete isso).
        lista(dados.ocultos).length ? secaoOcultos(lista(dados.ocultos), recarregar) : null,
        U.legendaSelos());
      U.focarTitulo(main);
      return;
    }
    // Desejo que talvez já foi realizado (ela ganhou, você deu) fica num grupo à parte, com o "É esse".
    const desejos = lista(dados.desejos);
    const talvezRealizados = desejos.filter((d) => d && d.talvez_dado);
    U.anexar(main,
      secaoOcasioes(lista(dados.ocasioes)),
      dados.momento ? secaoMomento(dados.momento) : null,
      secaoDesejos(desejos.filter((d) => d && !d.talvez_dado), recarregar),
      talvezRealizados.length
        ? U.secao('✔️ Talvez já realizado',
          h('p', { class: 'sutil' }, 'Ela comentou que queria, e depois você registrou um presente parecido. Se for isso mesmo, confirme.'),
          h('ul', { class: 'lista-cartoes' }, talvezRealizados.map((d) => cartaoDesejo(d, recarregar))))
        : null,
      secaoIdeias(lista(dados.ideias), recarregar),
      lista(dados.separados).length ? secaoSeparados(lista(dados.separados), recarregar) : null,
      secaoRecebidos(dados.recebidos || {}, recarregar),
      secaoReacoes(lista(dados.gostou), lista(dados.nao_gostou)),
      secaoNaoRecomendados(lista(dados.nao_recomendados)),
      lista(dados.ocultos).length ? secaoOcultos(lista(dados.ocultos), recarregar) : null,
      U.legendaSelos());
    const alvo = foco ? main.querySelector(foco) : null;
    if (alvo) alvo.focus();
    else U.focarTitulo(main);
  }

  function secaoOcasioes(ocasioes) {
    if (!ocasioes.length) return null;
    return h('section', { class: 'cartao', 'aria-labelledby': 'pr-ocasioes' },
      h('h2', { id: 'pr-ocasioes' }, h('span', { 'aria-hidden': 'true' }, '📅 '), 'Próximas ocasiões'),
      h('ul', { class: 'chips-info' }, ocasioes.slice(0, 4).map((o) => h('li', { class: 'chip-info' },
        h('span', { 'aria-hidden': 'true' }, `${EMOJI_OCASIAO[o.codigo] || '🎁'} `),
        `${o.titulo} · ${U.dataBr(o.data)} · ${U.textoDias(o.dias)}`))),
      ocasioes.some((o) => o.perto)
        ? h('p', { class: 'dica' }, 'Tem uma ocasião chegando: as listas abaixo podem ajudar a escolher com calma.')
        : null);
  }

  function secaoMomento(m) {
    const e = m.estado || {};
    return h('section', { class: 'cartao', 'aria-labelledby': 'pr-momento' },
      h('h2', { id: 'pr-momento' }, h('span', { 'aria-hidden': 'true' }, '💙 '), 'Para este momento'),
      h('p', null, `Você registrou ${e.quando || 'há pouco'}: `, h('strong', null, e.resumo || e.texto || '')),
      citar(e),
      h('p', null, 'E você registrou o que costuma ajudar:'),
      h('ul', { class: 'lista-entradas' }, lista(m.apoios).map((a) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, a.certeza ? selo(a.certeza) : null, h('span', { class: 'entrada-texto' }, a.texto)),
        citar(a),
        rodapeOrigem(a)))),
      h('p', { class: 'sutil possibilidade' }, 'Talvez um gesto pequeno ligado a isso seja uma boa ideia agora. ' + POSSIBILIDADE));
  }

  // ---------------------------------------------------------------- ela comentou que gostaria

  function secaoDesejos(desejos, recarregar) {
    return U.secao('💬 Ela comentou que gostaria',
      desejos.length
        ? h('ul', { class: 'lista-cartoes' }, desejos.map((d) => cartaoDesejo(d, recarregar)))
        : U.vazioPequeno('Nada por aqui ainda. Quando ela comentar que queria alguma coisa, aparece aqui.'));
  }

  function caminhoDesejo(d) {
    return `/presentes/${d.origem === 'item' ? 'itens' : 'desejos'}/${encodeURIComponent(String(d.id))}`;
  }

  function cartaoDesejo(d, recarregar) {
    const li = h('li', { class: 'cartao' });
    const idData = U.novoId('pr-dei-data');
    const idOcasiao = U.novoId('pr-dei-oc');
    const data = h('input', { id: idData, type: 'date', value: U.hojeIso(), max: U.hojeIso() });
    const ocasiao = selectOcasiao(idOcasiao, '');
    const confirmar = h('button', { type: 'button', class: 'botao botao-primario botao-pequeno' }, '🎁 Confirmar');
    const form = h('div', { class: 'form-item' },
      h('div', { class: 'grade-2' }, U.campo('Quando você deu', data), U.campo('Ocasião', ocasiao)),
      h('div', { class: 'acoes' }, confirmar,
        h('button', { type: 'button', class: 'botao botao-texto', onclick: () => { form.hidden = true; botaoDei.hidden = false; botaoDei.focus(); } }, 'Cancelar')));
    form.hidden = true;
    const botaoDei = h('button', {
      type: 'button', class: 'botao botao-secundario botao-pequeno',
      onclick: () => { form.hidden = false; botaoDei.hidden = true; data.focus(); },
    }, '🎁 Dei para ela');
    confirmar.addEventListener('click', () => agir(confirmar,
      () => api('POST', `${caminhoDesejo(d)}/dei`, { data: data.value || null, ocasiao: ocasiao.value || null }),
      `Anotado: você deu ${d.descricao}. Agora está em “Já recebeu”.`, recarregar));
    const ocultar = h('button', { type: 'button', class: 'botao botao-texto' }, '🙈 Não mostrar mais');
    ocultar.addEventListener('click', () => agir(ocultar, () => api('POST', `${caminhoDesejo(d)}/ocultar`, {}),
      'Escondido. Dá para mostrar de novo no fim da página.', recarregar));
    const vezes = d.vezes > 1 ? ` · comentou ${d.vezes} vezes` : '';
    const talvez = d.talvez_dado;
    U.anexar(li,
      h('div', { class: 'entrada-topo' }, selo(d.certeza), h('h3', { class: 'entrada-texto' }, capitalizar(d.descricao))),
      h('p', { class: 'sutil' }, `Ela comentou ${d.quando || (d.data_registro ? `em ${U.dataBr(d.data_registro)}` : '')}${vezes}`),
      d.certeza === 'inferencia'
        ? h('p', { class: 'dica' }, '🔵 É uma interpretação do que você registrou: ela não disse com essas palavras que quer.') : null,
      lista(d.origens).length > 1
        ? porque('De onde veio', h('ul', { class: 'lista-entradas' }, lista(d.origens).map(citacaoComOrigem)))
        : [citar(d), rodapeOrigem(d)],
      talvez ? h('div', { class: 'nota-cuidado' },
        h('p', null, `Talvez já tenha acontecido: ${talvez.de_quem === 'eu' ? 'você deu' : `ela ganhou${talvez.quem_texto ? ` ${talvez.quem_texto}` : ''}`} “${talvez.descricao}” ${quandoDoPresente(talvez)}.`),
        h('button', {
          type: 'button', class: 'botao botao-secundario botao-pequeno',
          onclick: (ev) => agir(ev.currentTarget, () => api('POST', `${caminhoDesejo(d)}/dei`, { presente_id: talvez.id }),
            'Pronto: esse desejo foi realizado.', recarregar),
        }, '✔️ É esse')) : null,
      d.ja_separado ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '🎀 '),
        `Você já separou: ${d.ja_separado.descricao}.`) : null,
      h('div', { class: 'acoes' }, botaoDei, ocultar),
      form);
    return li;
  }

  // ---------------------------------------------------------------- ideias possíveis (🔵)

  function secaoIdeias(ideias, recarregar) {
    return U.secao('🔵 Ideias possíveis',
      h('p', { class: 'sutil' }, 'Tiradas só do que ela gosta com força (dito por ela ou registrado mais de uma vez) e dos hobbies dela. Cada uma mostra de onde veio.'),
      ideias.length
        ? h('ul', { class: 'lista-cartoes' }, ideias.map((i) => cartaoIdeia(i, recarregar)))
        : U.vazioPequeno('Ainda não tenho informação suficiente para sugerir algo específico.'));
  }

  function cartaoIdeia(i, recarregar) {
    const ocultar = h('button', { type: 'button', class: 'botao botao-texto' }, '🙈 Não mostrar mais');
    ocultar.addEventListener('click', () => agir(ocultar,
      () => api('POST', `/presentes/ideias/${encodeURIComponent(String(i.item_id))}/ocultar`, {}),
      'Escondida. Dá para mostrar de novo no fim da página.', recarregar));
    return h('li', { class: 'cartao' },
      h('div', { class: 'entrada-topo' }, selo('inferencia'), h('h3', { class: 'entrada-texto' }, i.frase)),
      i.categoria ? h('ul', { class: 'chips', 'aria-label': 'Categoria' }, h('li', { class: 'chip' }, i.categoria)) : null,
      lista(i.avisos).map((a) => h('p', { class: 'nota-cuidado' }, a)),
      porque('Por que essa ideia?',
        lista(i.motivos).length ? h('ul', { class: 'lista-simples' }, lista(i.motivos).map((m) => h('li', null, m))) : null,
        h('ul', { class: 'lista-entradas' }, lista(i.origens).map(citacaoComOrigem))),
      h('p', { class: 'sutil possibilidade' }, POSSIBILIDADE),
      h('div', { class: 'acoes' }, ocultar));
  }

  // ---------------------------------------------------------------- presentes (separados e recebidos)

  function secaoSeparados(separados, recarregar) {
    return U.secao('🎀 Já separado para dar',
      h('ul', { class: 'lista-cartoes' }, separados.map((p) => cartaoPresente(p, recarregar))));
  }

  function secaoRecebidos(recebidos, recarregar) {
    const deVoce = lista(recebidos.de_voce);
    const deOutros = lista(recebidos.de_outros);
    if (!deVoce.length && !deOutros.length) {
      return U.secao('🎁 Já recebeu', U.vazioPequeno('Quando você registrar um presente que ela ganhou, aparece aqui com a reação dela.'));
    }
    return U.secao('🎁 Já recebeu',
      h('h3', { class: 'titulo-pequeno' }, 'De você'),
      deVoce.length ? h('ul', { class: 'lista-cartoes' }, deVoce.map((p) => cartaoPresente(p, recarregar)))
        : U.vazioPequeno('Nenhum registrado ainda.'),
      h('h3', { class: 'titulo-pequeno' }, 'De outras pessoas'),
      deOutros.length ? h('ul', { class: 'lista-cartoes' }, deOutros.map((p) => cartaoPresente(p, recarregar)))
        : U.vazioPequeno('Nenhum registrado ainda.'));
  }

  function cartaoPresente(p, recarregar) {
    const caminho = `/presentes/${encodeURIComponent(String(p.id))}`;
    // Id estável (o presente pode aparecer em "Separados" ou "Já recebeu"): o foco volta para ele.
    const idReacao = `pr-reacao-${String(p.id).replace(/[^0-9a-z_-]/gi, '')}`;
    const reacao = selectReacao(idReacao, p.reacao);
    // Salvar só com o botão: no Windows as setas num select fechado disparam "change" a cada tecla, e
    // quem usa teclado gravaria o valor do meio e perderia o lugar na página (revisão de 07/10).
    const salvarReacao = h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno', disabled: true },
      'Salvar');
    reacao.addEventListener('change', () => { salvarReacao.disabled = reacao.value === (REACOES[p.reacao] ? p.reacao : 'nao_sei'); });
    salvarReacao.addEventListener('click', () => agir(salvarReacao, () => api('PATCH', caminho, { reacao: reacao.value }),
      'Reação guardada.', () => recarregar(`#${idReacao}`)));
    const apagar = h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto' }, '🗑️ Apagar');
    apagar.addEventListener('click', async () => {
      const ok = await U.confirmar({
        titulo: `Apagar “${p.descricao}”?`,
        texto: 'Sai daqui e das cópias automáticas. O registro de onde ele veio continua guardado. Não dá para desfazer.',
        ok: 'Apagar', perigo: true,
      });
      if (ok) agir(apagar, () => api('DELETE', caminho), 'Presente apagado.', recarregar);
    });
    const jaDei = p.planejado ? h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno' }, '🎁 Já dei') : null;
    if (jaDei) {
      jaDei.addEventListener('click', () => agir(jaDei, () => api('PATCH', caminho, { planejado: false }),
        'Anotado. Depois conte como ela reagiu.', recarregar));
    }
    return h('li', { class: 'cartao' },
      h('div', { class: 'entrada-topo' }, p.certeza ? selo(p.certeza) : null, h('h3', { class: 'entrada-texto' }, capitalizar(p.descricao))),
      h('p', { class: 'sutil' }, [deQuem(p), quandoDoPresente(p)].filter(Boolean).join(' · ')),
      h('div', { class: 'chips' }, chipOcasiao(p.ocasiao), p.planejado ? null : chipReacao(p.reacao)),
      citar(p),
      p.registro_id != null ? h('p', { class: 'entrada-rodape sutil' }, U.linkRegistro(p.registro_id, 'ver registro')) : null,
      p.planejado ? null : h('div', { class: 'campo campo-linha' }, h('label', { for: idReacao }, 'Como ela reagiu?'), reacao,
        salvarReacao),
      h('div', { class: 'acoes' }, jaDei, apagar));
  }

  function secaoReacoes(gostou, naoGostou) {
    const linha = (p) => h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, h('span', { class: 'entrada-texto' }, capitalizar(p.descricao))),
      h('p', { class: 'entrada-rodape sutil' }, [deQuem(p), quandoDoPresente(p)].filter(Boolean).join(' · '),
        p.registro_id != null ? [' · ', U.linkRegistro(p.registro_id, 'ver registro')] : null));
    return h('div', { class: 'grade-2' },
      U.secao('💚 Gostou', gostou.length ? h('ul', { class: 'lista-entradas' }, gostou.map(linha))
        : U.vazioPequeno('Nada registrado ainda.')),
      U.secao('💔 Não gostou', naoGostou.length ? h('ul', { class: 'lista-entradas' }, naoGostou.map(linha))
        : U.vazioPequeno('Nada registrado ainda.')));
  }

  function secaoNaoRecomendados(itens) {
    return U.secao('🚫 Não recomendados',
      h('p', { class: 'sutil' }, 'O que ela não gosta, restrições, o que ela disse que não quer e o que ela acabou de ganhar (para não repetir).'),
      itens.length
        ? h('ul', { class: 'lista-entradas' }, itens.map((n) => {
          const [emoji, rotulo] = NAO_RECOMENDADO[n.tipo] || ['🚫', 'Não recomendado'];
          return h('li', { class: 'entrada' },
            h('div', { class: 'entrada-topo' }, n.certeza ? selo(n.certeza) : null,
              h('span', { class: 'entrada-texto' }, capitalizar(n.texto))),
            h('p', { class: 'sutil' }, h('span', { 'aria-hidden': 'true' }, `${emoji} `), n.motivo || rotulo),
            citar(n),

            rodapeOrigem(n));
        }))
        : U.vazioPequeno('Nada registrado.'));
  }

  function secaoOcultos(ocultos, recarregar) {
    return h('details', { class: 'mais-detalhes' },
      h('summary', null, `Coisas que você escondeu (${ocultos.length})`),
      h('ul', { class: 'lista-entradas' }, ocultos.map((o) => {
        const mostrar = h('button', { type: 'button', class: 'botao botao-texto' }, '👀 Mostrar de novo');
        mostrar.addEventListener('click', () => agir(mostrar, async () => {
          for (const id of lista(o.ids)) await api('DELETE', `/presentes/ocultos/${encodeURIComponent(String(id))}`);
        }, 'Voltou para a lista.', recarregar));
        return h('li', { class: 'entrada' },
          h('div', { class: 'entrada-topo' }, h('span', { class: 'entrada-texto' }, capitalizar(o.texto)),
            h('span', { class: 'sutil' }, o.tipo === 'ideia' ? ' (ideia)' : ' (desejo)')),
          h('div', { class: 'acoes' }, mostrar));
      })));
  }

  // ================================================================ cartão do Hoje

  function cartaoOcasiao(a, ctx) {
    const d = a.dados || {};
    const ids = lista(a.registro_ids);
    const linhas = [
      ...lista(d.desejos).map((x) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, selo(x.certeza), h('span', { class: 'entrada-texto' }, capitalizar(x.descricao))),
        h('p', { class: 'entrada-rodape sutil' }, `ela comentou ${x.quando || ''}`.trim(),
          x.registro_id != null ? [' · ', U.linkRegistro(x.registro_id, 'ver registro')] : null))),
      ...lista(d.ideias).map((x) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, selo('inferencia'), h('span', { class: 'entrada-texto' }, x.frase)),
        lista(x.motivos).length ? h('p', { class: 'entrada-rodape sutil' }, lista(x.motivos).join(' · ')) : null)),
    ];
    return [
      h('p', { class: 'alerta-texto' }, a.texto || ''),
      a.data ? h('p', { class: 'sutil' }, `${U.diaComSemana(a.data)} · ${U.textoDias(U.diasAte(a.data))}`) : null,
      linhas.length ? h('ul', { class: 'lista-entradas' }, linhas) : null,
      lista(d.separados).length ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '🎀 '),
        `Você já separou: ${lista(d.separados).map((s) => s.descricao).join(', ')}.`) : null,
      h('div', { class: 'alerta-acoes' },
        a.porque || ids.length
          ? porque('Por quê?', a.porque ? h('p', null, a.porque) : null,
            ids.length ? h('p', { class: 'links-registros' }, ids.map((rid, n) => [n ? ' · ' : '',
              U.linkRegistro(rid, ids.length > 1 ? `ver registro (${n + 1} de ${ids.length})` : 'ver o registro')])) : null)
          : h('span'),
        h('a', { href: '#presentes', class: 'botao botao-secundario botao-pequeno' }, '🎁 Ver ideias'),
        ctx && typeof ctx.botaoDispensar === 'function' ? ctx.botaoDispensar() : null),
      h('p', { class: 'sutil possibilidade' }, POSSIBILIDADE),
    ];
  }

  // ================================================================ revisão (extras)

  function revisaoExtra(e, ctx) {
    if (e.tipo === 'presente_desejo') return revisaoDesejo(e);
    if (e.tipo === 'presente') return revisaoPresente(e, ctx);
    return null;
  }

  function resumoDoPresente(e) {
    const desc = e.descricao || '…';
    let base;
    if (e.planejado) base = `Para dar a ela: ${desc}`;
    else if (e.de_quem === 'eu') base = `Você deu a ela: ${desc}`;
    else base = `Ela ganhou${e.quem ? ` (de ${e.quem})` : ''}: ${desc}`;
    if (e.ocasiao && OCASIOES[e.ocasiao] && !['sem_motivo', 'outra'].includes(e.ocasiao)) base += ` (${OCASIOES[e.ocasiao].toLowerCase()})`;
    if (!e.planejado && e.reacao && e.reacao !== 'nao_sei') base += ` — ${REACOES[e.reacao].toLowerCase()}`;
    return base;
  }

  function revisaoPresente(e) {
    const resumo = h('p', { class: 'extra-resumo' }, e.resumo || resumoDoPresente(e));
    const atualizar = () => {
      e.resumo = resumoDoPresente(e);
      resumo.textContent = e.resumo;
    };
    const ids = { desc: U.novoId('pr-desc'), quem: U.novoId('pr-quem'), de: U.novoId('pr-de'), reacao: U.novoId('pr-reac'),
      ocasiao: U.novoId('pr-oc'), data: U.novoId('pr-data'), plan: U.novoId('pr-plan') };
    const descricao = h('input', {
      id: ids.desc, type: 'text', maxlength: '120', autocomplete: 'off', value: e.descricao || '',
      oninput: (ev) => { e.descricao = ev.target.value.trim(); atualizar(); },
    });
    const quem = h('input', {
      id: ids.quem, type: 'text', maxlength: '80', autocomplete: 'off', value: e.quem || '',
      oninput: (ev) => { e.quem = ev.target.value.trim() || null; atualizar(); },
    });
    const campoQuem = U.campo('Quem deu', quem);
    campoQuem.hidden = e.de_quem !== 'outro';
    const de = h('select', {
      id: ids.de, class: 'select-pequeno', value: e.de_quem === 'outro' ? 'outro' : 'eu',
      onchange: (ev) => {
        e.de_quem = ev.target.value;
        campoQuem.hidden = e.de_quem !== 'outro';
        if (e.de_quem !== 'outro') e.quem = null;
        if (e.de_quem === 'outro' && e.planejado) {
          e.planejado = false;
          plan.checked = false;
          reacao.disabled = false;
        }
        atualizar();
      },
    }, h('option', { value: 'eu' }, 'Você'), h('option', { value: 'outro' }, 'Outra pessoa'));
    const reacao = selectReacao(ids.reacao, e.reacao);
    reacao.disabled = Boolean(e.planejado);
    reacao.addEventListener('change', () => { e.reacao = reacao.value; atualizar(); });
    const ocasiao = selectOcasiao(ids.ocasiao, e.ocasiao);
    ocasiao.addEventListener('change', () => { e.ocasiao = ocasiao.value || null; atualizar(); });
    const data = h('input', {
      id: ids.data, type: 'date', value: e.data || '',
      onchange: (ev) => { e.data = ev.target.value || null; },
    });
    const plan = h('input', {
      id: ids.plan, type: 'checkbox', checked: Boolean(e.planejado),
      onchange: (ev) => {
        e.planejado = ev.target.checked;
        if (e.planejado) {
          e.de_quem = 'eu';
          de.value = 'eu';
          campoQuem.hidden = true;
          e.reacao = 'nao_sei';
          reacao.value = 'nao_sei';
        }
        reacao.disabled = e.planejado;
        atualizar();
      },
    });
    return [
      h('div', { class: 'item-topo' },
        h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, e.planejado ? '🎀 ' : '🎁 '), 'Presente'),
        e.certeza ? selo(e.certeza) : null),
      resumo,
      h('div', { class: 'form-item' },
        h('div', { class: 'grade-2' }, U.campo('O presente', descricao), U.campo('De quem', de)),
        campoQuem,
        h('div', { class: 'grade-2' }, U.campo('Como ela reagiu', reacao), U.campo('Ocasião', ocasiao)),
        h('div', { class: 'grade-2' }, U.campo('Quando', data, e.data_expressao ? `Pelo texto: “${e.data_expressao}”.` : 'Deixe em branco se não sabe.'),
          h('div', { class: 'campo' }, h('label', { class: 'opcao-linha', for: ids.plan }, plan, h('span', null, 'Ainda vou dar (separado)'))))),
      e.trecho ? U.citacao(e.trecho) : null,
    ];
  }

  function revisaoDesejo(e) {
    const resumo = h('p', { class: 'extra-resumo' }, e.resumo || `Ela gostaria de ganhar: ${e.descricao || ''}`);
    const id = U.novoId('pr-desejo');
    const descricao = h('input', {
      id, type: 'text', maxlength: '120', autocomplete: 'off', value: e.descricao || '',
      oninput: (ev) => {
        e.descricao = ev.target.value.trim();
        e.resumo = `Ela gostaria de ganhar: ${e.descricao}`;
        resumo.textContent = e.resumo;
      },
    });
    return [
      h('div', { class: 'item-topo' },
        h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, '💬 '), 'Ela gostaria de ganhar'),
        e.certeza ? selo(e.certeza) : null),
      resumo,
      e.certeza === 'inferencia'
        ? h('p', { class: 'dica' }, '🔵 É uma interpretação (ela não disse com essas palavras que quer). Guarde só se fizer sentido para você.')
        : null,
      h('div', { class: 'form-item' }, U.campo('O que ela gostaria', descricao)),
      e.trecho ? U.citacao(e.trecho) : null,
    ];
  }

  // ================================================================ encaixe

  E.registrarMenu('presentes', 'Presentes', '🎁');
  E.registrarTela('presentes', telaPresentes);
  E.registrarCartaoHoje('presentes_ocasiao', cartaoOcasiao);
  // Pelo NOME do recurso (o tipo do extra vem primeiro no mapa da revisão; "presente" e
  // "presente_desejo" não são registrados por tipo para não disputar nome com outro recurso).
  E.registrarRevisaoExtra('presentes', revisaoExtra);
})();
