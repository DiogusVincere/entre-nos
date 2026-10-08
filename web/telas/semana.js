/* Diário de conversa e resumo da semana (DESIGN.md, seção 20.7).
 *
 * Encaixa no app por window.EntreNos:
 * - Registrar ganha o modo "🗣️ Depois de uma conversa" (botões no alto do formulário e a tela
 *   #conversa): um formulário curto que o servidor monta num texto só (POST /api/semana/conversa) e que
 *   entra na revisão normal do Registrar, analisado pelas regras como sempre. Nada é gravado antes de
 *   revisar; o rascunho da conversa fica só na memória desta página;
 * - tela #semana (e #semana/AAAA-Www): o resumo da semana e as outras semanas;
 * - cartões do Hoje: "semana_resumo" (no dia do resumo e nos dois seguintes) e "semana_lembrete";
 * - miolo dos extras na revisão: "conversa_lembrete" (texto e data editáveis) e "conversa_assuntos";
 * - Ajustes → 🗓️ Resumo da semana (o dia e se aparece no Hoje).
 * Tudo por nós de texto (h): nada de HTML montado com dado do usuário, nada de estilo inline. */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E || !E.util) return;
  const { api, h, selo, navegar } = E;
  const u = E.util;

  const DIAS = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo'];
  const MESES_CURTOS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  const RE_SEMANA = /^\d{4}-[wW]\d{1,2}$/;
  const EMOJIS = {
    'Família': '👨‍👩‍👧', 'Amigos': '🧑‍🤝‍🧑', 'Trabalho': '💼', 'Estudos': '📚', 'Saúde': '🩺', 'Viagens': '✈️',
    'Datas importantes': '📅', 'Preferências': '💛', 'Comidas': '🍽️', 'Bebidas': '☕', 'Filmes': '🎬',
    'Séries': '📺', 'Música': '🎵', 'Livros': '📖', 'Hobbies': '🎨', 'Lugares': '📍', 'Presentes': '🎁',
    'Sonhos': '🌠', 'Objetivos': '🎯', 'Medos': '🫣', 'Preocupações': '😟', 'Coisas que deixam feliz': '😊',
    'Coisas que incomodam': '😣', 'Experiências importantes': '⭐', 'Infância': '🧸', 'Relacionamento': '💞',
    'Conversas importantes': '🗣️', 'Situações pendentes': '⏳', 'Eventos futuros': '🗓️',
    'Pessoas importantes': '🫂', 'Aniversários': '🎂', 'Memórias compartilhadas': '📸', 'Outras coisas': '🧩',
  };

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function erroNaTela(e) {
    if (e && e.bloqueio) return;
    u.avisarErro(e);
  }

  function emoji(categoria) {
    return EMOJIS[categoria] || '🧩';
  }

  function periodoTexto(inicio, fim) {
    return `${u.diaMes(inicio)} a ${u.diaMes(fim)}`;
  }

  function porQue(texto, ids) {
    const registros = lista(ids).filter((r) => r != null);
    if (!texto && !registros.length) return null;
    return h('details', { class: 'porque' }, h('summary', null, 'Por quê?'),
      texto ? h('p', null, texto) : null,
      registros.length ? h('p', { class: 'links-registros' }, registros.map((rid, i) => [i ? ' · ' : '',
        u.linkRegistro(rid, registros.length > 1 ? `ver registro (${i + 1} de ${registros.length})` : 'ver o registro')])) : null);
  }

  // ================================================================ Depois de uma conversa

  // O que a pessoa está escrevendo no formulário da conversa: só na memória desta página (como o
  // rascunho do Registrar). `origem`: o texto que veio do Registrar ao trocar de modo.
  const CAMPOS = ['onde', 'contou', 'humor', 'assuntos', 'lembrar'];
  const rascunho = { onde: '', contou: '', humor: '', assuntos: '', lembrar: '', data: '', privado: false, origem: '' };

  function rascunhoVazio() {
    return !CAMPOS.some((k) => String(rascunho[k] || '').trim());
  }

  function limparRascunho() {
    Object.assign(rascunho, { onde: '', contou: '', humor: '', assuntos: '', lembrar: '', data: '', privado: false, origem: '' });
  }

  // Do Registrar (texto livre) para a conversa: o que já estava escrito vem junto, nada se perde.
  // `atual` é a cópia que o app entrega (EntreNos.rascunhoAtual / o modo do Registrar).
  function trazerDoRegistrar(atual) {
    const r = atual || E.rascunhoAtual();
    const texto = String(r.texto || '').trim();
    if (texto && !u.normalizar(rascunho.contou).includes(u.normalizar(texto))) {
      rascunho.contou = rascunho.contou.trim() ? `${rascunho.contou.trim()}\n\n${texto}` : texto;
    }
    if (texto) rascunho.origem = texto;
    if (!rascunho.onde.trim()) rascunho.onde = r.contexto || '';
    if (!rascunho.humor.trim()) rascunho.humor = r.humor || '';
    if (r.data && !rascunho.data) rascunho.data = r.data;
    if (r.privado) rascunho.privado = true;
  }

  // "✍️ Texto livre" | "🗣️ Depois de uma conversa", no alto do Registrar e da tela da conversa.
  function seletorDeModo(atual) {
    const botao = (modo, rotulo, ir) => h('button', {
      type: 'button', class: 'botao botao-secundario botao-escolha', 'aria-pressed': String(atual === modo),
      onclick: () => { if (atual !== modo) ir(); },
    }, rotulo);
    return h('div', { class: 'escolha-inicio', role: 'group', 'aria-label': 'Jeito de registrar', id: `semana-modo-${atual}` },
      botao('livre', '✍️ Texto livre', () => navegar('#registrar')),
      botao('conversa', '🗣️ Depois de uma conversa', () => {
        trazerDoRegistrar();
        navegar('#conversa');
      }));
  }

  // No alto do Registrar (que é do núcleo), pelo contrato: "✍️ Texto livre" | "🗣️ Depois de uma
  // conversa". Escolher a conversa traz o que já estava escrito; a dica lembra da anotação guardada.
  E.registrarModoRegistrar('conversa', '🗣️ Depois de uma conversa', (atual) => {
    trazerDoRegistrar(atual);
    navegar('#conversa');
  }, () => (rascunhoVazio() ? null : 'Sua anotação da conversa continua guardada em “Depois de uma conversa”.'));

  async function telaConversa(main) {
    if (!rascunho.data) rascunho.data = u.hojeIso();
    const erro = h('p', { class: 'erro-campo', id: 'conv-erro', role: 'alert' });
    const mudar = (chave) => (e) => {
      rascunho[chave] = e.target.value;
      erro.textContent = '';
    };
    const data = h('input', { id: 'conv-data', type: 'date', value: rascunho.data, onchange: mudar('data') });
    const onde = h('input', {
      id: 'conv-onde', type: 'text', maxlength: '200', value: rascunho.onde, autocomplete: 'off',
      placeholder: 'no jantar, por mensagem, no carro…', oninput: mudar('onde'),
    });
    const contou = h('textarea', {
      id: 'conv-contou', rows: '6', maxlength: '20000', value: rascunho.contou, 'aria-describedby': 'conv-erro',
      placeholder: 'Ex.: Ela disse que a chefe pegou no pé de novo. A irmã dela vai casar em maio.',
      oninput: mudar('contou'),
    });
    const humor = h('input', {
      id: 'conv-humor', type: 'text', maxlength: '300', list: 'lista-humores', value: rascunho.humor,
      autocomplete: 'off', placeholder: 'cansada, animada com a viagem…', oninput: mudar('humor'),
    });
    const assuntos = h('input', {
      id: 'conv-assuntos', type: 'text', maxlength: '2000', value: rascunho.assuntos, autocomplete: 'off',
      placeholder: 'trabalho, a viagem, a irmã dela', oninput: mudar('assuntos'),
    });
    const lembrar = h('textarea', {
      id: 'conv-lembrar', rows: '3', maxlength: '2000', value: rascunho.lembrar,
      placeholder: 'perguntar do exame na sexta\nreservar o restaurante', oninput: mudar('lembrar'),
    });
    const privado = h('input', {
      id: 'conv-privado', type: 'checkbox', checked: rascunho.privado, 'aria-describedby': 'conv-privado-dica',
      onchange: (e) => { rascunho.privado = e.target.checked; },
    });
    const botao = h('button', { type: 'submit', class: 'botao botao-primario botao-grande', 'aria-keyshortcuts': 'Control+Enter' },
      '🗂️ Organizar');

    const organizar = async () => {
      if (botao.disabled) return;
      if (rascunhoVazio()) {
        erro.textContent = 'Conte pelo menos um pouco da conversa antes de organizar.';
        contou.focus();
        return;
      }
      botao.disabled = true;
      botao.textContent = 'Montando o texto…';
      form.setAttribute('aria-busy', 'true');
      let montado;
      try {
        montado = (await api('POST', '/semana/conversa', {
          onde: rascunho.onde, contou: rascunho.contou, humor: rascunho.humor, assuntos: rascunho.assuntos,
          lembrar: rascunho.lembrar,
        })) || {};
      } catch (e) {
        if (!e.bloqueio && form.isConnected) {
          erro.textContent = e.message;
          contou.focus();
        }
        return;
      } finally {
        if (form.isConnected) {
          botao.disabled = false;
          botao.textContent = '🗂️ Organizar';
          form.removeAttribute('aria-busy');
        }
      }
      await levarParaRegistrar(montado);
    };

    const form = h('form', {
      class: 'cartao form-registro', novalidate: true,
      onsubmit: (e) => { e.preventDefault(); organizar(); },
      onkeydown: (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          organizar();
        }
      },
    },
    seletorDeModo('conversa'),
    h('div', { class: 'grade-2' }, h('div', { class: 'campo campo-data' }, h('label', { for: 'conv-data' }, 'Quando foi a conversa?'), data),
      u.campo('Onde ou como foi', onde)),
    u.campo('O que ela contou', contou, 'Do jeito que ela falou: “ela disse que…” vira 🟢 Confirmado.'),
    erro,
    u.campo('Como ela estava', humor, 'Vira “Ela estava …” no texto, com as suas palavras.'),
    u.campo('Assuntos importantes', assuntos, 'Separe com vírgulas.'),
    u.campo('O que preciso lembrar', lembrar, 'Um por linha. Com data (“na sexta”, “dia 12”), aparece no Hoje no dia.'),
    h('div', { class: 'opcao-privado' },
      h('label', { class: 'opcao-linha', for: 'conv-privado' }, privado,
        h('span', null, h('span', { 'aria-hidden': 'true' }, '🔒 '), 'Não enviar este registro para a IA')),
      h('p', { class: 'dica', id: 'conv-privado-dica' }, 'Marcado, ele nunca vai para a IA. As regras continuam usando o registro normalmente (e, com a sincronização ligada, ele vai embaralhado para os seus outros aparelhos).')),
    h('div', { class: 'acoes' }, botao,
      h('button', {
        type: 'button', class: 'botao botao-texto',
        onclick: async () => {
          if (rascunhoVazio()) return;
          const ok = await u.confirmar({ titulo: 'Limpar a anotação da conversa?', texto: 'O que está escrito aqui sai desta tela. Nada foi salvo ainda.', ok: 'Limpar' });
          if (!ok) return;
          limparRascunho();
          navegar('#conversa');
        },
      }, 'Limpar'),
      h('span', { class: 'sutil atalho' }, 'ou Ctrl+Enter')));

    u.anexar(main,
      u.cabecalho('Depois de uma conversa', 'Anote do jeito que lembrar; tudo vira um texto só, que você confere antes de guardar.', '🗣️'),
      form,
      h('section', { class: 'cartao dicas', 'aria-labelledby': 'conv-dicas' },
        h('h2', { id: 'conv-dicas' }, 'Como funciona'),
        h('ul', null,
          h('li', null, 'Os campos viram um texto só, que vai para a revisão do Registrar. Nada é salvo antes de você conferir.'),
          h('li', null, 'Os assuntos entram no resumo da semana; o que você precisa lembrar vira lembrete seu.'),
          h('li', null, 'Os campos vazios ficam de fora: escreva só o que fizer sentido.'))));
  }

  async function levarParaRegistrar(montado) {
    const texto = typeof montado.texto === 'string' ? montado.texto : '';
    if (!texto.trim()) return;
    const dataConversa = rascunho.data || u.hojeIso();
    const privado = rascunho.privado;
    const origem = rascunho.origem;
    const atual = E.rascunhoAtual();
    if (atual.emRevisao) {
      E.abrirRegistrar({});                     // mostra a revisão que está aberta
      u.avisar('Há uma revisão aberta em Registrar. Salve ou descarte ela antes; a anotação da conversa continua guardada em “Depois de uma conversa”.', 'erro');
      return;
    }
    const antes = atual.texto;
    let final = texto;
    if (antes.trim() && u.normalizar(antes) !== u.normalizar(origem || '')
      && !u.normalizar(texto).includes(u.normalizar(antes))) {
      // Outro texto foi escrito em Registrar: nada se perde, ele fica em cima e a conversa embaixo.
      const ok = await u.confirmar({
        titulo: 'Já tem um texto em Registrar',
        texto: 'O texto que já estava em Registrar fica em cima e a conversa entra embaixo, num registro só. Seguir?',
        ok: 'Juntar e organizar',
        cancelar: 'Agora não',
      });
      if (!ok) return;
      final = `${antes.replace(/\s+$/, '')}\n\n${texto}`;
    }
    const pedido = { texto: final, data: dataConversa, organizar: true };
    if (montado.contexto) pedido.contexto = String(montado.contexto);
    if (montado.humor) pedido.humor = String(montado.humor);
    if (privado) pedido.privado = true;
    const aberto = E.abrirRegistrar(pedido);
    if (!aberto.ok) return;
    limparRascunho();
    u.falar('A conversa virou um texto e está sendo organizada. Confira antes de salvar.');
  }

  // ================================================================ a tela #semana

  function linhaItem(e, comMotivos) {
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, e.certeza ? selo(e.certeza) : null,
        h('span', { class: 'entrada-texto' }, e.resumo || e.trecho || '')),
      e.trecho && e.trecho !== e.resumo ? u.citacao(e.trecho) : null,
      h('p', { class: 'entrada-rodape sutil' },
        comMotivos && lista(e.motivos).length ? `${u.capitalizar(lista(e.motivos).join(', '))} · ` : null,
        e.data_registro ? `${u.diaComSemana(e.data_registro)} · ` : null,
        e.registro_id != null ? u.linkRegistro(e.registro_id, 'ver registro') : null));
  }

  function blocoContou(r) {
    const grupos = lista(r.contou);
    if (!grupos.length) return u.secao('💬 O que ela contou', u.vazioPequeno('Nada separado do que ela contou nesta semana. Os textos continuam nos registros.'));
    return u.secao('💬 O que ela contou',
      h('p', { class: 'sutil' }, 'Por assunto, com o trecho de onde veio.'),
      grupos.map((g) => h('div', { class: 'bloco-entradas' },
        h('h3', null, h('span', { 'aria-hidden': 'true' }, `${emoji(g.categoria)} `), g.categoria,
          h('span', { class: 'sutil' }, ` · ${g.total} ${u.plural(g.total, 'coisa', 'coisas')}`)),
        h('ul', { class: 'lista-entradas' }, lista(g.itens).map((e) => linhaItem(e, false))),
        g.mais ? h('p', { class: 'sutil' }, `E mais ${g.mais} nos registros da semana.`) : null)));
  }

  function blocoDestaques(r) {
    const destaques = lista(r.destaques);
    if (!destaques.length) return null;
    return u.secao('⭐ Para não esquecer',
      h('p', { class: 'sutil' }, 'O que mais pesa na semana: o que ela mesma disse, o que é importante, o que tem data ou voltou mais de uma vez.'),
      h('ul', { class: 'lista-entradas' }, destaques.map((e) => linhaItem(e, true))));
  }

  function blocoEstados(r) {
    const estados = lista(r.estados);
    if (!estados.length) return null;
    return u.secao('🌦️ Como ela estava',
      h('p', { class: 'sutil' }, 'Com as palavras que você registrou, na ordem dos dias.'),
      h('ul', { class: 'lista-entradas' }, estados.map((e) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, e.certeza ? selo(e.certeza) : null,
          h('span', { class: 'entrada-texto' }, `${e.data ? `${u.diaComSemana(e.data)}: ` : ''}${e.texto || e.resumo || ''}`)),
        e.trecho ? u.citacao(e.trecho) : null,
        e.registro_id != null ? h('p', { class: 'entrada-rodape sutil' }, u.linkRegistro(e.registro_id, 'ver registro')) : null))));
  }

  function blocoConversas(r) {
    const conversas = lista(r.conversas);
    const assuntos = lista(r.assuntos);
    const voltaram = lista(r.voltaram);
    if (!conversas.length && !assuntos.length && !voltaram.length) return null;
    return u.secao('🗣️ Conversas e assuntos',
      conversas.length ? h('ul', { class: 'lista-entradas' }, conversas.map((c) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, h('span', { class: 'entrada-texto' },
          [u.diaComSemana(c.data), c.contexto ? ` · ${c.contexto}` : null, c.humor ? ` · ela estava ${c.humor}` : null])),
        lista(c.assuntos).length ? h('ul', { class: 'chips', 'aria-label': 'Assuntos da conversa' },
          lista(c.assuntos).map((a) => h('li', { class: 'chip' }, a))) : null,
        h('p', { class: 'entrada-rodape sutil' }, u.linkRegistro(c.registro_id, 'ver registro'))))) : null,
      assuntos.some((a) => a.semanas_antes > 0) ? h('p', { class: 'sutil' }, 'Voltaram de outras semanas: ',
        assuntos.filter((a) => a.semanas_antes > 0).map((a, i) => [i ? ', ' : '',
          `“${a.assunto}” (em ${a.semanas_antes} ${u.plural(a.semanas_antes, 'semana', 'semanas')} das últimas 8)`]), '.') : null,
      voltaram.length ? [h('h3', null, 'Apareceu mais de uma vez'),
        h('ul', { class: 'chips', 'aria-label': 'Assuntos e pessoas que apareceram em mais de um registro' },
          voltaram.map((v) => h('li', { class: 'chip' }, `${v.tipo === 'categoria' ? `${emoji(v.rotulo)} ` : '👤 '}${v.rotulo} · ${v.registros} registros`)))] : null);
  }

  function linhaPendencia(p) {
    const li = h('li', { class: 'entrada lembrar' });
    const resolver = async (ev) => {
      const botao = ev.currentTarget;
      botao.disabled = true;
      try {
        await api('PATCH', `/itens/${encodeURIComponent(String(p.item_id))}`, { status: 'resolvido' });
        li.remove();
        u.avisar('Pronto, marquei como resolvido.');
      } catch (e) {
        if (botao.isConnected) botao.disabled = false;
        erroNaTela(e);
      }
    };
    u.anexar(li,
      h('div', { class: 'entrada-topo' }, p.certeza ? selo(p.certeza) : null, h('span', { class: 'entrada-texto' }, p.texto || '')),
      p.trecho ? u.citacao(p.trecho) : null,
      h('p', { class: 'entrada-rodape sutil' }, p.desde ? `desde ${u.diaComSemana(p.desde)} · ` : null,
        p.registro_id != null ? [u.linkRegistro(p.registro_id, 'ver registro'), ' · '] : null,
        p.item_id != null ? h('button', { type: 'button', class: 'botao botao-texto', onclick: resolver }, '✔️ Já resolvido') : null));
    return li;
  }

  // O prazo com a precisão dita ("dezembro de 2026", não "ter, 01/12"), como a seção 16 pede.
  function textoPrazo(p) {
    if (p.prazo_texto) return p.prazo_texto;
    if (p.precisao === 'mes') return u.mesAno(p.prazo);
    if (p.precisao === 'ano') return String(p.prazo).slice(0, 4);
    if (p.precisao === 'semana') return `semana de ${u.dataBr(p.prazo)}`;
    return u.diaComSemana(p.prazo);
  }

  function linhaPromessa(p) {
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, h('span', { class: 'entrada-texto' }, h('span', { 'aria-hidden': 'true' }, '🤝 '), p.texto || '')),
      p.trecho && p.trecho !== p.texto ? u.citacao(p.trecho) : null,
      h('p', { class: 'entrada-rodape sutil' }, p.prazo ? `prazo: ${textoPrazo(p)} · ` : 'sem prazo · ',
        h('a', { href: '#promessas', class: 'link-registro' }, 'ver promessas'),
        p.registro_id != null ? [' · ', u.linkRegistro(p.registro_id, 'ver registro')] : null));
  }

  function linhaLembrete(l) {
    const li = h('li', { class: 'entrada' });
    const url = `/semana/lembretes/${encodeURIComponent(String(l.id))}`;
    const agir = async (ev, fazer, aviso) => {
      const botao = ev.currentTarget;
      botao.disabled = true;
      try {
        if (!(await fazer())) {
          botao.disabled = false;
          return;
        }
        li.remove();
        u.avisar(aviso);
      } catch (e) {
        if (botao.isConnected) botao.disabled = false;
        erroNaTela(e);
      }
    };
    const feito = (ev) => agir(ev, async () => { await api('PATCH', url, { status: 'feito' }); return true; }, 'Feito! 💙');
    // Apagar é apagar: sai do diário e das cópias automáticas (o registro de onde veio continua).
    const apagar = (ev) => agir(ev, async () => {
      const ok = await u.confirmar({
        titulo: 'Apagar este lembrete?',
        texto: 'Ele sai do diário e das cópias automáticas. O registro de onde ele veio continua. Não dá para desfazer.',
        ok: 'Apagar', perigo: true,
      });
      if (ok) await api('DELETE', url);
      return ok;
    }, 'Lembrete apagado.');
    u.anexar(li,
      h('div', { class: 'entrada-topo' }, h('span', { class: 'entrada-texto' }, h('span', { 'aria-hidden': 'true' }, '📝 '), l.texto || '')),
      l.trecho ? u.citacao(l.trecho) : null,
      h('p', { class: 'entrada-rodape sutil' },
        l.quando ? `${l.atrasado ? 'era para' : 'para'} ${l.quando} · ` : 'sem data · ',
        l.registro_id != null ? [u.linkRegistro(l.registro_id, 'ver registro'), ' · '] : null,
        h('button', { type: 'button', class: 'botao botao-texto', onclick: feito }, '✔️ Feito'),
        ' ',
        h('button', { type: 'button', class: 'botao botao-texto botao-perigo-texto', onclick: apagar }, '🗑️ Apagar')));
    return li;
  }

  function blocoAbertos(r) {
    const a = r.abertos || {};
    const pendencias = lista(a.pendencias);
    const promessas = lista(a.promessas);
    const lembretes = lista(a.lembretes);
    if (!pendencias.length && !promessas.length && !lembretes.length) {
      return u.secao('📌 Ficou em aberto', u.vazioPequeno(r.atual ? 'Nenhum assunto em aberto agora.' : 'Nada daquela semana continua em aberto.'));
    }
    return u.secao('📌 Ficou em aberto',
      r.atual ? null : h('p', { class: 'sutil' }, 'O que veio daquela semana e continua em aberto.'),
      pendencias.length ? [h('h3', null, 'Assuntos dela'), h('ul', { class: 'lista-entradas' }, pendencias.map(linhaPendencia))] : null,
      promessas.length ? [h('h3', null, 'Promessas'), h('ul', { class: 'lista-entradas' }, promessas.map(linhaPromessa))] : null,
      lembretes.length ? [h('h3', null, 'Seus lembretes'), h('ul', { class: 'lista-entradas' }, lembretes.map((l) => linhaLembrete(l)))] : null);
  }

  function linhaProxima(p) {
    const d = u.paraData(p.data);
    const conteudo = [
      h('span', { class: 'data-bloco', 'aria-hidden': 'true' },
        h('span', { class: 'data-dia' }, d ? String(d.getDate()).padStart(2, '0') : '?'),
        h('span', { class: 'data-mes' }, d ? MESES_CURTOS[d.getMonth()] : '')),
      h('span', { class: 'proximo-corpo' },
        h('span', { class: 'proximo-texto' }, p.texto || ''),
        h('span', { class: 'sutil' }, [u.diaComSemana(p.data), u.textoDias(p.dias)].filter(Boolean).join(' · '))),
    ];
    return h('li', { class: ['proximo', p.dias != null && p.dias <= 1 ? 'proximo-perto' : null] },
      p.registro_id != null
        ? h('a', { href: u.hrefRegistro(p.registro_id), class: 'proximo-link' }, conteudo)
        : h('div', { class: 'proximo-link' }, conteudo));
  }

  function blocoProximos(r) {
    if (!r.atual) return null;
    const proximos = lista(r.proximos);
    return u.secao('📅 Próximos 7 dias', proximos.length
      ? h('ul', { class: 'lista-proximos' }, proximos.map(linhaProxima))
      : u.vazioPequeno('Nada marcado para os próximos 7 dias.'));
  }

  function blocoSugestoes(r) {
    const sugestoes = lista(r.sugestoes);
    if (!r.atual || !sugestoes.length) return null;
    return u.secao('💡 Talvez…',
      h('ul', { class: 'lista-cartoes lista-sugestoes-ia' }, sugestoes.map((s) => h('li', { class: 'sugestao-ia' },
        h('p', { class: 'sugestao-ia-texto' }, s.texto || ''),
        porQue(s.porque, s.registro_ids)))),
      h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: quem conhece ela é você.'));
  }

  function blocoPergunta(r) {
    const p = r.pergunta;
    if (!r.atual || !p || !p.pergunta) return null;
    const motivos = lista(p.motivos).filter((m) => m && m.texto);
    return u.secao('❓ Uma pergunta para os próximos dias',
      h('p', { class: 'alerta-texto' }, `“${p.pergunta}”`),
      p.categoria ? h('p', { class: 'sutil' }, p.categoria) : null,
      motivos.length ? h('details', { class: 'porque' }, h('summary', null, 'Por quê?'),
        h('ul', null, motivos.map((m) => h('li', null, m.texto)))) : null,
      h('p', null, h('a', { href: '#perguntas', class: 'link-registro' }, 'Abrir as perguntas')));
  }

  function blocoHistorico(hist, atualId) {
    const semanas = lista(hist && hist.semanas).filter((s) => s.semana !== atualId);
    if (!semanas.length) return null;
    return u.secao('📚 Outras semanas',
      h('ul', { class: 'lista-linhas' }, semanas.map((s) => h('li', null,
        h('a', { href: `#semana/${encodeURIComponent(s.semana)}`, class: 'linha-link' },
          `${periodoTexto(s.inicio, s.fim)} · ${s.registros} ${u.plural(s.registros, 'registro', 'registros')}`
          + (s.conversas ? ` · ${s.conversas} ${u.plural(s.conversas, 'conversa', 'conversas')}` : ''))))));
  }

  function navegacao(r) {
    return h('nav', { class: 'barra-acoes', 'aria-label': 'Outras semanas' },
      h('a', { href: `#semana/${encodeURIComponent(r.anterior)}`, class: 'botao botao-secundario botao-pequeno' }, '← Semana anterior'),
      r.atual ? null : h('a', { href: '#semana', class: 'botao botao-secundario botao-pequeno' }, 'Esta semana'),
      r.seguinte ? h('a', { href: `#semana/${encodeURIComponent(r.seguinte)}`, class: 'botao botao-secundario botao-pequeno' }, 'Semana seguinte →') : null);
  }

  async function telaSemana(main, partes, atual) {
    const pedida = partes && partes[0] ? String(partes[0]) : '';
    if (pedida && !RE_SEMANA.test(pedida)) {
      navegar('#semana');
      return;
    }
    u.anexar(main, u.carregando());
    const consulta = pedida ? `?semana=${encodeURIComponent(pedida.toUpperCase())}` : '';
    const [r, hist] = await Promise.all([
      api('GET', `/semana${consulta}`),
      api('GET', '/semana/historico').catch((e) => { if (e && e.bloqueio) throw e; return null; }),
    ]);
    if (!atual()) return;
    u.limpar(main);
    const dados = r || {};
    const subtitulo = dados.inicio
      ? `${periodoTexto(dados.inicio, dados.fim)}${dados.em_andamento ? ' · em andamento' : ''}${dados.futura ? ' · ainda não começou' : ''}`
      : '';
    u.anexar(main, u.cabecalho('Resumo da semana', subtitulo, '🗓️'), dados.anterior ? navegacao(dados) : null);
    if (dados.vazia) {
      u.anexar(main, u.estadoVazio(dados.futura ? 'Essa semana ainda não começou.'
        : 'Nenhum registro nesta semana. Quando você registrar algo, ele aparece aqui, organizado por assunto.', !dados.futura));
      if (dados.atual) u.anexar(main, blocoAbertos(dados), blocoProximos(dados), blocoPergunta(dados));
      u.anexar(main, blocoHistorico(hist, dados.semana));
      return;
    }
    const momento = dados.momento && dados.momento.sensivel
      ? h('div', { class: 'nota-cuidado', role: 'note' }, h('span', { 'aria-hidden': 'true' }, '💙 '), dados.momento.texto || '')
      : null;
    u.anexar(main,
      h('p', { class: 'sutil' }, `${dados.registros} ${u.plural(dados.registros, 'registro', 'registros')} nesta semana. Só entra o que está nos registros.`),
      momento,
      blocoSugestoes(dados),
      blocoDestaques(dados),
      blocoContou(dados),
      blocoEstados(dados),
      blocoConversas(dados),
      blocoAbertos(dados),
      blocoProximos(dados),
      blocoPergunta(dados),
      blocoHistorico(hist, dados.semana),
      u.legendaSelos());
  }

  // ================================================================ Hoje

  function cartaoResumo(c, ctx) {
    const d = c.dados || {};
    const categorias = lista(d.categorias);
    const destaques = lista(d.destaques);
    const proximos = lista(d.proximos);
    const abertos = d.abertos || {};
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '🗓️ '), c.texto || ''),
      d.momento ? h('p', { class: 'nota-cuidado' }, h('span', { 'aria-hidden': 'true' }, '💙 '), d.momento) : null,
      categorias.length ? h('ul', { class: 'chips', 'aria-label': 'O que ela contou, por assunto' },
        categorias.map((x) => h('li', { class: 'chip' }, `${emoji(x.nome)} ${x.nome} (${x.total})`))) : null,
      destaques.length ? h('ul', { class: 'lista-entradas' }, destaques.map((e) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, e.certeza ? selo(e.certeza) : null, h('span', { class: 'entrada-texto' }, e.resumo || '')),
        e.trecho && e.trecho !== e.resumo ? u.citacao(e.trecho) : null,
        e.registro_id != null ? h('p', { class: 'entrada-rodape sutil' }, u.linkRegistro(e.registro_id, 'ver registro')) : null))) : null,
      abertos.total ? h('p', null, h('span', { 'aria-hidden': 'true' }, '📌 '),
        `Em aberto: ${abertos.total}`, lista(abertos.primeiros).length ? ` (${lista(abertos.primeiros).join('; ')}${abertos.total > lista(abertos.primeiros).length ? '…' : ''})` : '') : null,
      proximos.length ? h('p', null, h('span', { 'aria-hidden': 'true' }, '📅 '), 'Próximos dias: ',
        proximos.map((p, i) => [i ? '; ' : '', `${p.texto} (${u.textoDias(p.dias)})`])) : null,
      d.pergunta && d.pergunta.pergunta ? h('p', null, h('span', { 'aria-hidden': 'true' }, '❓ '), `Uma pergunta: “${d.pergunta.pergunta}”`) : null,
      d.sugestao ? h('p', { class: 'possibilidade' }, h('span', { 'aria-hidden': 'true' }, '💡 '), d.sugestao) : null,
      h('div', { class: 'alerta-acoes' },
        porQue(c.porque, []) || h('span'),
        h('div', { class: 'acoes' },
          h('a', { href: `#semana/${encodeURIComponent(d.semana || '')}`, class: 'botao botao-secundario botao-pequeno' }, 'Ver o resumo'),
          ctx.botaoDispensar())),
    ];
  }

  function cartaoLembrete(c, ctx) {
    const d = c.dados || {};
    const feito = h('button', {
      type: 'button', class: 'botao botao-secundario botao-pequeno',
      onclick: async (ev) => {
        const botao = ev.currentTarget;
        botao.disabled = true;
        try {
          await api('PATCH', `/semana/lembretes/${encodeURIComponent(String(d.id))}`, { status: 'feito' });
          ctx.remover();
          u.avisar('Feito! 💙');
        } catch (e) {
          if (botao.isConnected) botao.disabled = false;
          erroNaTela(e);
        }
      },
    }, '✔️ Feito');
    return [
      h('p', { class: 'alerta-texto' }, h('span', { 'aria-hidden': 'true' }, '📝 '), c.texto || ''),
      c.data ? h('p', { class: 'sutil' }, `${u.diaComSemana(c.data)} · ${u.textoDias(u.diasAte(c.data))}`) : null,
      h('div', { class: 'alerta-acoes' },
        // Sensível: o resumo da semana não o mostra; o caminho é abrir o próprio registro.
        d.sensivel ? (lista(c.registro_ids).length ? h('p', null, u.linkRegistro(lista(c.registro_ids)[0], 'Abrir o registro'))
          : h('span'))
          : (porQue(c.porque, c.registro_ids) || h('span')),

        h('div', { class: 'acoes' }, d.id != null ? feito : null, ctx.botaoDispensar())),
    ];
  }

  // ================================================================ revisão (o miolo dos extras)

  function revisaoLembrete(e, ctx) {
    const idTexto = u.novoId('sem-lembrete');
    const idData = u.novoId('sem-data');
    const atualizarResumo = () => {
      e.resumo = e.data ? `${e.texto} — ${u.diaComSemana(e.data)}` : e.texto;
    };
    const texto = h('input', {
      id: idTexto, type: 'text', maxlength: '300', value: e.texto || e.resumo || '', autocomplete: 'off',
      oninput: (ev) => { e.texto = ev.target.value; atualizarResumo(); },
    });
    const data = h('input', {
      id: idData, type: 'date', value: e.data || '',
      onchange: (ev) => {
        e.data = ev.target.value || null;
        e.precisao = e.data ? 'dia' : null;
        atualizarResumo();
      },
    });
    const alternativas = lista(e.data_alternativas).filter((x) => typeof x === 'string');
    const escolha = alternativas.length > 1 ? h('div', { class: 'opcoes', role: 'group', 'aria-label': 'Qual data é?' },
      alternativas.map((alt) => h('button', {
        type: 'button', class: 'botao botao-secundario botao-pequeno botao-escolha', 'aria-pressed': String(e.data === alt),
        onclick: () => {
          e.data = alt;
          e.precisao = 'dia';
          atualizarResumo();
          ctx.redesenhar();
        },
      }, u.diaComSemana(alt)))) : null;
    const precisaoDita = e.precisao && e.precisao !== 'dia' && e.data
      ? h('p', { class: 'dica' }, `Foi dito ${e.precisao === 'mes' ? `“${u.mesAno(e.data)}”` : e.precisao === 'semana' ? `“semana de ${u.diaMes(e.data)}”` : 'só o ano'}: ele aparece no resumo, não no Hoje.`)
      : null;
    return [
      h('div', { class: 'item-topo' },
        h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, '📝 '), 'Lembrete seu'),
        e.pedido_dela ? h('span', { class: 'chip chip-info' }, 'ela pediu') : null),
      u.campo('O que lembrar', texto),
      h('div', { class: 'campo campo-data' }, h('label', { for: idData }, 'Quando (opcional)'), data),
      escolha,
      precisaoDita,
      e.trecho ? u.citacao(e.trecho) : null,
      h('p', { class: 'dica' }, 'Com data, aparece no Hoje no dia (e na véspera). Sem data, fica em “Ficou em aberto” no resumo da semana.'),
    ];
  }

  function revisaoAssuntos(e, ctx) {
    const assuntos = lista(e.assuntos).filter((a) => typeof a === 'string');
    const tirar = (a) => {
      e.assuntos = assuntos.filter((x) => x !== a);
      e.resumo = e.assuntos.length ? `Assuntos da conversa: ${e.assuntos.join(', ')}` : e.trecho;
      ctx.redesenhar();
      u.falar(`“${a}” tirado dos assuntos.`);
    };
    return [
      h('div', { class: 'item-topo' }, h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, '🗣️ '), 'Assuntos da conversa')),
      assuntos.length
        ? h('ul', { class: 'chips', 'aria-label': 'Assuntos' }, assuntos.map((a) => h('li', { class: 'chip' }, a,
          h('button', { type: 'button', class: 'chip-x', 'aria-label': `Tirar o assunto ${a}`, onclick: () => tirar(a) }, '×'))))
        : h('p', { class: 'sutil' }, 'Nenhum assunto ficou. Se não quiser guardar, use “Não guardar”.'),
      e.trecho ? u.citacao(e.trecho) : null,
      h('p', { class: 'dica' }, 'Entram no resumo da semana, e o app conta quando um assunto volta.'),
    ];
  }

  // ================================================================ Ajustes

  function secaoAjustes(corpo, ctx) {
    const config = (ctx && ctx.config) || {};
    const salvo = typeof config.semana_dia === 'string' ? u.normalizar(config.semana_dia).replace('-feira', '') : '';
    const atualDia = DIAS.find((d) => u.normalizar(d) === salvo) || 'domingo';
    const dia = h('select', {
      id: 'aj-semana-dia', class: 'select-pequeno', value: atualDia,
      onchange: (e) => ctx.salvarConfig({ semana_dia: e.target.value }, 'Dia do resumo salvo.'),
    }, DIAS.map((d) => h('option', { value: d }, u.capitalizar(d))));
    const noHoje = h('input', {
      id: 'aj-semana-hoje', type: 'checkbox', checked: config.semana_no_hoje !== false,
      onchange: (e) => ctx.salvarConfig({ semana_no_hoje: e.target.checked }, 'Preferência do resumo salva.'),
    });
    u.anexar(corpo,
      h('p', { class: 'sutil' }, 'Uma vez por semana, o Entre Nós junta o que você registrou: o que ela contou, o que ficou em aberto e o que vem por aí.'),
      h('div', { class: 'campo-linha' }, h('label', { for: 'aj-semana-dia' }, 'Dia do resumo'), dia),
      h('label', { class: 'opcao-linha', for: 'aj-semana-hoje' }, noHoje,
        h('span', null, 'Mostrar o resumo no Hoje (no dia e nos dois seguintes). Os seus lembretes aparecem sempre.')),
      h('p', null, h('a', { href: '#semana', class: 'link-registro' }, 'Abrir o resumo desta semana')));
  }

  // ================================================================ encaixe

  E.registrarMenu('conversa', 'Depois de uma conversa', '🗣️', 'registrar');
  E.registrarMenu('semana', 'Resumo da semana', '🗓️');
  E.registrarTela('conversa', telaConversa);
  E.registrarTela('semana', telaSemana);
  E.registrarCartaoHoje('semana_resumo', cartaoResumo);
  E.registrarCartaoHoje('semana_lembrete', cartaoLembrete);
  E.registrarRevisaoExtra('conversa_lembrete', revisaoLembrete);
  E.registrarRevisaoExtra('conversa_assuntos', revisaoAssuntos);
  E.registrarSecaoAjustes('semana', '🗓️ Resumo da semana', secaoAjustes);
})();
