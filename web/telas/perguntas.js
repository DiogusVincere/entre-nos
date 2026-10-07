/* Perguntas profundas (DESIGN.md, seção 20.1) e as perguntas "Nós dois" (seção 20.9, item 5).
 *
 * Encaixa no app por window.EntreNos:
 * - menu e tela #perguntas: a sugestão de agora, as guardadas, as já perguntadas (com a resposta
 *   dela citada do registro) e o banco por categoria;
 * - cartão "💬 Conheça melhor" no Hoje (tipo "pergunta"): Vou perguntar / Perguntei → registrar a
 *   resposta / Outra / Agora não;
 * - miolo dos extras na revisão ("pergunta_feita", "pergunta_guardada");
 * - Ajustes → 💬 Perguntas (dias entre sugestões, perguntas sobre nós dois).
 * Tudo por nós de texto (h): nada de HTML montado com dado do usuário. */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E || !E.util) return;
  const { api, h, selo, navegar } = E;
  const u = E.util;

  const ESTADOS = {
    feita: ['✔️', 'Já perguntada'],
    guardada: ['🙋', 'Guardada para perguntar'],
    sugerida: ['💬', 'Sugerida agora'],
    pulada: ['⏭️', 'Pulada'],
  };
  const TITULOS = { profundas: 'Conheça melhor', nos_dois: 'Nós dois', apoio: 'Com cuidado' };
  const EMOJIS = { profundas: '💬', nos_dois: '💞', apoio: '🫶' };

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function erroNaTela(e) {
    if (e && e.bloqueio) return;
    u.avisarErro(e);
  }

  // "2026-10-05" -> "em 05/10/2026" (sem data, quando o registro foi apagado: vazio).
  function quando(iso, prefixo = 'em ') {
    return iso ? `${prefixo}${u.dataBr(iso)}` : '';
  }

  // ---------------------------------------------------------------- Registrar com a pergunta

  // Abre Registrar com "Eu perguntei: “...”" e "Ela respondeu: " (EntreNos.abrirRegistrar, com o
  // cursor no fim). O que a pessoa já estava escrevendo nunca se perde: a pergunta entra embaixo, e
  // não se repete. As regras do app não tiram item da linha da pergunta, e a revisão propõe ligar o
  // registro à pergunta (com a resposta citada).
  function abrirRegistrar(texto) {
    const primeira = String(texto || '').split('\n')[0];
    const antes = E.rascunhoAtual();
    const ja = !antes.emRevisao && antes.texto.includes(primeira);
    const aberto = E.abrirRegistrar(ja ? {} : { texto: String(texto || ''), juntar: true });
    if (!aberto.ok) {
      if (aberto.motivo === 'revisao') {
        u.avisar(`A pergunta ficou marcada como feita. Para guardar a resposta, termine o registro que está em revisão e comece um novo com: ${primeira}`, 'erro');
      }
      return;
    }
    u.falar('A pergunta está em Registrar. Escreva o que ela respondeu e toque em Organizar.');
  }

  // ---------------------------------------------------------------- o cartão (Hoje e tela)

  function linhaMotivo(m) {
    const ids = lista(m.registro_ids).filter((r) => r != null);
    return h('li', null, m.texto,
      ids.length ? [' ', ids.map((rid, i) => [i ? ' · ' : '', u.linkRegistro(rid, ids.length > 1 ? `ver registro ${i + 1}` : 'ver registro')])] : null);
  }

  // Desenha o cartão de uma pergunta em `caixa`. ctx.remover() tira o cartão; ctx.trocar(dados)
  // desenha a próxima pergunta no lugar.
  function desenharCartao(caixa, dados, ctx) {
    u.limpar(caixa);
    const estado = dados.estado;
    const motivos = lista(dados.motivos).filter((m) => m && typeof m.texto === 'string' && m.texto);
    const acoes = h('div', { class: 'acoes' });
    const corpo = { origem: estado === 'nova' || estado === 'sugerida' ? 'sugestao' : 'tela' };

    const agir = async (botao, caminho, corpoPedido, depois) => {
      const botoes = [...acoes.querySelectorAll('button')];
      botoes.forEach((b) => { b.disabled = true; });
      const texto = botao.textContent;
      botao.textContent = 'Um instante…';
      try {
        const r = (await api('POST', caminho, corpoPedido)) || {};
        await depois(r);
      } catch (e) {
        if (caixa.isConnected) {
          botoes.forEach((b) => { b.disabled = false; });
          botao.textContent = texto;
        }
        erroNaTela(e);
      }
    };
    const seguir = (r, mensagem) => {
      if (r.cartao && r.cartao.dados && r.cartao.dados.pergunta_id) ctx.trocar(r.cartao.dados);
      else ctx.remover();
      if (mensagem) u.avisar(mensagem);
    };
    const botao = (rotulo, classe, aoTocar) => {
      const b = h('button', { type: 'button', class: classe }, rotulo);
      b.addEventListener('click', () => aoTocar(b));
      return b;
    };
    const vou = botao('🙋 Vou perguntar', 'botao botao-secundario botao-pequeno', (b) =>
      agir(b, `/perguntas/${dados.pergunta_id}/guardar`, corpo, async (r) => {
        seguir(r, 'Guardada. Quando perguntar, toque em “Perguntei”.');
        u.falar('Pergunta guardada.');
      }));
    const perguntei = botao('✅ Perguntei: registrar a resposta', 'botao botao-primario botao-pequeno', (b) =>
      agir(b, `/perguntas/${dados.pergunta_id}/perguntei`, corpo, async (r) => {
        await abrirRegistrar(r.texto_registrar || dados.texto_registrar || '');
      }));
    const outra = botao('🔄 Outra', 'botao botao-texto', (b) =>
      agir(b, '/perguntas/outra', { pergunta_id: dados.pergunta_id }, async (r) => {
        seguir(r, r.cartao ? null : 'Por enquanto não tenho outra pergunta com motivo para sugerir.');
        u.falar(r.cartao ? 'Outra pergunta no lugar.' : 'Sem outra pergunta agora.');
      }));
    const agoraNao = botao('Agora não', 'botao botao-texto', (b) =>
      agir(b, `/perguntas/${dados.pergunta_id}/agora-nao`, corpo, async (r) => {
        ctx.remover();
        u.avisar(r.volta_em ? `Tudo bem. Volto a sugerir a partir de ${u.dataBr(r.volta_em)}.` : 'Tudo bem.');
      }));
    const desistir = botao('Desistir desta', 'botao botao-texto', (b) =>
      agir(b, `/perguntas/${dados.pergunta_id}/pular`, corpo, async (r) => {
        seguir(r, 'Pronto, ela não volta como sugestão.');
      }));

    if (estado === 'guardada') u.anexar(acoes, perguntei, agoraNao, desistir);
    else if (estado === 'apoio') u.anexar(acoes, perguntei, outra, agoraNao);
    else u.anexar(acoes, vou, perguntei, outra, agoraNao);

    u.anexar(caixa,
      h('div', { class: 'item-topo' },
        h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, `${EMOJIS[dados.trilha] || '💬'} `),
          dados.titulo || TITULOS[dados.trilha] || 'Conheça melhor'),
        dados.categoria_nome && dados.trilha === 'profundas'
          ? h('span', { class: 'chip' }, h('span', { 'aria-hidden': 'true' }, `${dados.emoji || ''} `), dados.categoria_nome)
          : null,
        estado === 'guardada' ? h('span', { class: 'chip chip-ligada' }, h('span', { 'aria-hidden': 'true' }, '🙋 '), 'Guardada') : null),
      h('p', { class: 'alerta-texto' }, `“${dados.pergunta || ''}”`),
      estado === 'apoio' ? h('p', { class: 'sutil' }, 'Talvez não seja dia de perguntas profundas. Se fizer sentido, algo leve.') : null,
      motivos.length
        ? h('details', { class: 'porque' }, h('summary', null, 'Por quê?'),
          h('ul', { class: 'lista-simples' }, motivos.map(linhaMotivo)))
        : null,
      h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: você escolhe se, quando e como perguntar.'),
      acoes);
  }

  // Cartão do Hoje: o miolo é daqui; "Outra" e "Vou perguntar" desenham no mesmo lugar.
  function cartaoHoje(cartao, ctx) {
    const dados = cartao && cartao.dados;
    if (!dados || !dados.pergunta_id) return null;          // sem dados: o cartão genérico
    const caixa = h('div', { class: 'corpo-recurso' });
    const contexto = {
      remover: () => ctx.remover(),
      trocar: (novos) => {
        desenharCartao(caixa, novos, contexto);
        const primeiro = caixa.querySelector('button');
        if (primeiro) primeiro.focus();
      },
    };
    desenharCartao(caixa, dados, contexto);
    return caixa;
  }

  // ---------------------------------------------------------------- revisão (extras)

  function revisaoExtra(extra, ctx) {
    const feita = extra.tipo === 'pergunta_feita';
    const partes = [
      h('div', { class: 'item-topo' },
        h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, feita ? '💬 ' : '🙋 '),
          feita ? 'Pergunta feita' : 'Pergunta para fazer'),
        extra.categoria_nome
          ? h('span', { class: 'chip' }, h('span', { 'aria-hidden': 'true' }, `${extra.emoji || ''} `), extra.categoria_nome)
          : null),
      h('p', { class: 'extra-resumo' }, `“${extra.pergunta || extra.resumo || ''}”`),
    ];
    if (extra.forma && extra.forma !== 'literal') {
      partes.push(h('p', { class: 'sutil' }, 'Reconheci a pergunta pelo jeito que você escreveu:'), u.citacao(extra.trecho));
    }
    if (feita) {
      if (extra.recusou) {
        partes.push(h('p', null, 'Ela preferiu não responder, e tudo bem. Esse assunto descansa um tempo nas sugestões.'));
      } else if (extra.resposta) {
        partes.push(h('div', { class: 'entrada-topo' }, extra.certeza ? selo(extra.certeza) : null,
          h('span', { class: 'entrada-texto' }, 'O que ela respondeu:')), u.citacao(extra.resposta));
      } else {
        partes.push(h('p', { class: 'sutil' }, 'Sem resposta escrita: a pergunta fica marcada como feita.'));
      }
      if (extra.trilha === 'nos_dois') {
        partes.push(h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '💞 '),
          'É o que ela pensa sobre vocês dois: aparece na área Eu, ao lado do que você achou do mês, e fica fora da IA.'));
      }
      // Uma pergunta não é algo que ela disse: se as regras tiraram item da frase da pergunta, avisa.
      const itens = lista(ctx && ctx.revisao && ctx.revisao.itens);
      if (itens.some((it) => it && !it._removido && it.trecho === extra.trecho && it.tipo !== 'nota')) {
        partes.push(h('p', { class: 'nota-cuidado' },
          'A pergunta em si não é algo que ela disse: confira os itens tirados da frase da pergunta e remova o que não for dela.'));
      }
    } else {
      partes.push(h('p', { class: 'sutil' }, 'Confirmando, ela entra em “Guardadas para perguntar” e o Hoje lembra dela.'));
    }
    return partes;
  }

  // ---------------------------------------------------------------- tela #perguntas

  function chipEstado(estado) {
    const e = ESTADOS[estado];
    return e ? h('span', { class: 'chip' }, h('span', { 'aria-hidden': 'true' }, `${e[0]} `), e[1]) : null;
  }

  // Botão que faz um pedido; volta a funcionar depois (a tela costuma ser redesenhada antes).
  function botaoAcao(rotulo, aoTocar, classe = 'botao botao-texto') {
    const b = h('button', { type: 'button', class: classe }, rotulo);
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        await aoTocar();
      } catch (e) {
        erroNaTela(e);
      } finally {
        if (b.isConnected) b.disabled = false;
      }
    });
    return b;
  }

  function chipCategoria(l) {
    return h('span', { class: 'chip' }, h('span', { 'aria-hidden': 'true' }, `${l.emoji || '💬'} `), l.categoria_nome || '');
  }

  function linhaFeita(l, recarregar) {
    const esquecer = botaoAcao('Esquecer esta', async () => {
      const ok = await u.confirmar({
        titulo: 'Esquecer esta pergunta?',
        texto: 'Ela sai da lista de perguntas feitas, com a resposta guardada aqui (também das cópias automáticas). O registro, se houver, continua no diário.',
        ok: 'Esquecer',
        perigo: true,
      });
      if (!ok) return;
      await api('DELETE', `/perguntas/linhas/${l.linha_id}`);
      u.avisar('Pronto, esquecida.');
      recarregar();
    }, 'botao botao-texto botao-perigo-texto');
    let resposta;
    if (l.recusou) {
      resposta = h('p', { class: 'sutil' }, 'Ela preferiu não responder.');
    } else if (l.resposta) {
      resposta = [h('div', { class: 'entrada-topo' }, l.certeza ? selo(l.certeza) : null,
        h('span', { class: 'sutil' }, 'O que ela respondeu:')), u.citacao(l.resposta)];
    } else {
      resposta = h('p', { class: 'sutil' }, 'Sem resposta registrada.');
    }
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, chipCategoria(l), h('span', { class: 'entrada-texto' }, `“${l.pergunta}”`)),
      resposta,
      h('p', { class: 'entrada-rodape sutil' },
        l.feita_em ? `Perguntada ${quando(l.feita_em)}` : 'Já perguntada',
        l.registro_id != null ? [' · ', u.linkRegistro(l.registro_id, 'ver registro')] : null,
        ' · ', esquecer));
  }

  async function perguntei(perguntaId) {
    const r = (await api('POST', `/perguntas/${perguntaId}/perguntei`, { origem: 'tela' })) || {};
    await abrirRegistrar(r.texto_registrar || '');
  }

  async function voltar(perguntaId, recarregar) {
    await api('POST', `/perguntas/${perguntaId}/voltar`, {});
    u.avisar('A pergunta voltou para a lista.');
    recarregar();
  }

  function linhaGuardada(l, recarregar) {
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, chipCategoria(l), h('span', { class: 'entrada-texto' }, `“${l.pergunta}”`)),
      h('p', { class: 'entrada-rodape sutil' }, l.guardada_em ? `Guardada ${quando(l.guardada_em)}` : 'Guardada'),
      h('div', { class: 'acoes' },
        botaoAcao('✅ Perguntei: registrar a resposta', () => perguntei(l.pergunta_id), 'botao botao-secundario botao-pequeno'),
        botaoAcao('Voltar para a lista', () => voltar(l.pergunta_id, recarregar))));
  }

  function linhaBanco(p, recarregar) {
    const acoes = [];
    if (!p.estado || p.estado === 'sugerida') {
      acoes.push(botaoAcao('🙋 Vou perguntar', async () => {
        await api('POST', `/perguntas/${p.id}/guardar`, { origem: 'tela' });
        u.avisar('Guardada. O Hoje lembra dela nos próximos dias.');
        recarregar();
      }), botaoAcao('✅ Perguntei', () => perguntei(p.id)));
    } else if (p.estado === 'pulada' || p.estado === 'guardada') {
      acoes.push(botaoAcao('Voltar para a lista', () => voltar(p.id, recarregar)));
    } else if (p.estado === 'feita') {
      acoes.push(botaoAcao('✅ Perguntei de novo', () => perguntei(p.id)));
    }
    const respostas = lista(p.feitas).filter((f) => f.resposta && !f.recusou);
    return h('li', { class: 'entrada' },
      h('div', { class: 'entrada-topo' }, h('span', { class: 'entrada-texto' }, p.texto), chipEstado(p.estado)),
      respostas.map((f) => [u.citacao(f.resposta),
        h('p', { class: 'entrada-rodape sutil' }, f.certeza ? [selo(f.certeza), ' '] : null, quando(f.feita_em, ''),
          f.registro_id != null ? [f.feita_em ? ' · ' : '', u.linkRegistro(f.registro_id, 'ver registro')] : null)]),
      acoes.length ? h('div', { class: 'acoes' }, acoes) : null);
  }

  function secaoCategoria(c, recarregar) {
    let explica = `Para conhecer ${c.sobre}.`;
    if (c.trilha === 'nos_dois') explica = 'Perguntas para fazer a ela sobre vocês dois. A resposta é dela e aparece na área Eu, ao lado do seu check-in.';
    if (c.trilha === 'apoio') explica = 'Perguntas leves, que eu só sugiro em dias difíceis, quando você ainda não sabe o que a ajuda.';
    return h('details', { class: 'mais-detalhes' },
      h('summary', null, h('span', { 'aria-hidden': 'true' }, `${c.emoji} `), `${c.nome} · ${c.feitas} de ${c.total} já perguntadas`),
      h('p', { class: 'sutil' }, explica),
      h('ul', { class: 'lista-entradas' }, lista(c.perguntas).map((p) => linhaBanco(p, recarregar))));
  }

  function secaoAgora(dados, recarregar) {
    if (dados.cartao && dados.cartao.pergunta_id) {
      const caixa = h('div', { class: 'cartao' });
      desenharCartao(caixa, dados.cartao, { remover: recarregar, trocar: recarregar });
      return u.secao('✨ Para agora', caixa);
    }
    if (Number(dados.registros) < Number(dados.minimo_registros)) {
      return u.secao('✨ Para agora', u.estadoVazio(
        `Depois de ${dados.minimo_registros} registros eu começo a sugerir perguntas, cada uma com o motivo. Por enquanto, escolha uma da lista abaixo, se quiser.`, true));
    }
    const momento = dados.momento || {};
    let texto = 'Nenhuma pergunta com motivo para sugerir agora. A lista abaixo continua à mão.';
    if (momento.sensivel) texto = `Hoje não sugiro perguntas profundas. ${momento.texto || ''} Cuidar dela vem antes.`;
    else if (dados.proxima_em) texto = `A próxima sugestão vem a partir de ${u.dataBr(dados.proxima_em)}. Se quiser, escolha uma da lista abaixo.`;
    return u.secao('✨ Para agora', u.vazioPequeno(texto.replace(/\s+/g, ' ').trim()));
  }

  function comoEscolho(dias) {
    return h('details', { class: 'mais-detalhes' },
      h('summary', null, 'Como eu escolho a pergunta'),
      h('ul', null,
        h('li', null, 'Começo pelo que você ainda sabe pouco sobre ela (o que já está registrado conta).'),
        h('li', null, 'Uso o que ela contou nos últimos 14 dias: se falou do trabalho, talvez uma pergunta sobre futuro ou valores.'),
        h('li', null, `Nunca repito uma pergunta, e sugiro no máximo uma nova a cada ${dias} ${u.plural(dias, 'dia', 'dias')} (dá para mudar em Ajustes).`),
        h('li', null, 'Se ela anda num momento difícil, nada de perguntas profundas: no máximo algo leve, e só quando você ainda não sabe o que a ajuda.'),
        h('li', null, 'Perto do fim do mês, uma pergunta sobre vocês dois, para a sua reflexão do mês.'),
        h('li', null, 'As respostas guardadas são trechos do que você escreveu: nada é inventado.')));
  }

  async function telaPerguntas(main, _partes, atual) {
    u.anexar(main, u.carregando());
    const dados = (await api('GET', '/perguntas')) || {};
    if (!atual()) return;
    u.limpar(main);
    const recarregar = () => {
      if (atual()) navegar('#perguntas');
    };
    const dias = Number(dados.ajustes && dados.ajustes.dias_entre_perguntas) || 3;
    const feitas = lista(dados.feitas);
    const guardadas = lista(dados.guardadas);
    u.anexar(main,
      u.cabecalho('Perguntas', 'Para conhecer melhor quem ela é, e ouvir o que ela pensa sobre vocês dois. Você decide se e quando perguntar.', '💬'),
      secaoAgora(dados, recarregar),
      guardadas.length
        ? u.secao(`🙋 Guardadas para perguntar (${guardadas.length})`,
          h('ul', { class: 'lista-entradas' }, guardadas.map((l) => linhaGuardada(l, recarregar))))
        : null,
      u.secao('📖 Já perguntadas',
        feitas.length
          ? h('ul', { class: 'lista-entradas' }, feitas.map((l) => linhaFeita(l, recarregar)))
          : u.vazioPequeno('Nenhuma ainda. Quando perguntar, toque em “Perguntei” e registre o que ela respondeu.')),
      u.secao('📚 Todas as perguntas',
        h('p', { class: 'sutil' }, '“Vou perguntar” guarda para depois; “Perguntei” abre Registrar com a pergunta.'),
        lista(dados.categorias).map((c) => secaoCategoria(c, recarregar)),
        comoEscolho(dias)));
  }

  // ---------------------------------------------------------------- Ajustes

  function secaoAjustes(corpo, ctx) {
    const config = (ctx && ctx.config) || {};
    const salvo = Number(config.dias_entre_perguntas);
    const atualDias = Number.isInteger(salvo) && salvo >= 1 && salvo <= 30 ? salvo : 3;
    const dias = h('input', {
      id: 'aj-perguntas-dias', type: 'number', min: '1', max: '30', step: '1', inputmode: 'numeric',
      class: 'numero-curto', value: String(atualDias), 'aria-describedby': 'aj-perguntas-erro',
    });
    const erro = h('p', { class: 'erro-campo', id: 'aj-perguntas-erro', role: 'alert' });
    const form = h('form', { novalidate: true },
      u.campo('Dias entre uma sugestão e outra', dias, 'De 1 a 30. Uma pergunta guardada continua aparecendo enquanto isso.'),
      erro,
      h('div', { class: 'acoes' }, h('button', { type: 'submit', class: 'botao botao-primario' }, 'Salvar')));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const n = Number(dias.value);
      if (!Number.isInteger(n) || n < 1 || n > 30) {
        erro.textContent = 'Use um número inteiro de 1 a 30.';
        dias.focus();
        return;
      }
      erro.textContent = '';
      await ctx.salvarConfig({ dias_entre_perguntas: n }, 'Preferência das perguntas salva.');
    });
    const nosDois = h('input', {
      id: 'aj-perguntas-nos-dois', type: 'checkbox', checked: config.perguntas_nos_dois !== false,
      onchange: (e) => ctx.salvarConfig({ perguntas_nos_dois: e.target.checked }, 'Preferência das perguntas salva.'),
    });
    u.anexar(corpo,
      h('p', { class: 'sutil' }, 'O cartão “💬 Conheça melhor” do Hoje.'),
      form,
      h('label', { class: 'opcao-linha', for: 'aj-perguntas-nos-dois' }, nosDois,
        h('span', null, 'Incluir perguntas para ela sobre vocês dois (perto do fim do mês, para a reflexão da área Eu)')));
  }

  // ---------------------------------------------------------------- encaixe

  E.registrarMenu('perguntas', 'Perguntas', '💬', 'conhecer');
  E.registrarTela('perguntas', telaPerguntas);
  E.registrarCartaoHoje('pergunta', cartaoHoje);
  E.registrarRevisaoExtra('pergunta_feita', revisaoExtra);
  E.registrarRevisaoExtra('pergunta_guardada', revisaoExtra);
  E.registrarSecaoAjustes('perguntas', '💬 Perguntas', secaoAjustes);
})();
