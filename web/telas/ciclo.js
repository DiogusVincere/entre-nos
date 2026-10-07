/* Ciclo (DESIGN.md, seção 20.8): a tela do recurso "ciclo" — ela sabe e concorda.
 *
 * Encaixa no app por window.EntreNos:
 * - aba "🌙 Ciclo" em Conhecendo Ela (#ela/ciclo): agora, faixa prevista (🔵, com o porquê), duração,
 *   planejamento, sintomas (padrões dos registros, sem diagnóstico), o que ajuda, período fértil com o
 *   aviso fixo, histórico (início, fim, duração e intervalo de cada ciclo; editar e apagar), anotar um
 *   início, "faltou registrar um mês?", ajustes e "apagar todos os dados do ciclo";
 * - miolo dos extras na revisão (ciclo_inicio, ciclo_durante, ciclo_fim, ciclo_fluxo, ciclo_sintoma,
 *   ciclo_ajuda, ciclo_mencao): data editável (e "sabe quando começou?"), sintomas com as palavras do
 *   registro, "foi ela quem disse?" no que ajuda, e o aviso do que sai de "Como ela está";
 * - o lembrete neutro do Hoje (ciclo_cuidado), sem as palavras do ciclo.
 * Nada daqui vai para a IA. Tudo por nós de texto (h); sem estilo próprio (classes do estilo.css). */
(function () {
  'use strict';

  const E = window.EntreNos;
  if (!E || !E.util) return;
  const { api, h, selo } = E;
  const u = E.util;

  const ROTULOS = {
    ciclo_inicio: ['🌙', 'Começo do período'],
    ciclo_durante: ['🌙', 'Período em andamento'],
    ciclo_fim: ['🌙', 'Fim do período'],
    ciclo_fluxo: ['🌙', 'Fluxo'],
    ciclo_sintoma: ['🌙', 'Como ela se sentiu'],
    ciclo_ajuda: ['💙', 'O que ajuda'],
    ciclo_mencao: ['🌙', 'Comentário sobre o ciclo'],
  };
  const FLUXOS = { leve: 'Leve', moderado: 'Moderado', intenso: 'Intenso' };
  const MENCOES = {
    tpm: 'Ela comentou sobre TPM. Fica no histórico do 🌙, sem data e sem virar padrão.',
    atraso: 'Fica guardado só como comentário, sem data. Nada é calculado a partir dele.',
    habito: 'É como costuma ser, não um dia específico: fica como comentário, sem entrar na previsão.',
    duvida: 'Como não foi afirmado, fica só como comentário, sem data.',
    futuro: 'Ainda não aconteceu: fica como comentário, sem entrar na previsão.',
    geral: 'Fica como comentário, sem data.',
    outra: 'É de outra pessoa: não entra no 🌙 dela. Guardar só deixa este registro fora da IA.',
  };
  const SITUACAO_INTERVALO = {
    aproximado: 'uma das datas é aproximada: fica fora da conta',
    curto: 'curto demais: fica fora da conta',
    pausa: 'longo demais (pausa nas anotações): fica fora da conta',
    esquecido: 'quase o dobro do comum: parece faltar um mês anotado',
    longo_confirmado: 'você disse que foi assim mesmo: fica fora da conta',
    fora_do_padrao: 'fora do padrão: fica fora da conta',
  };

  function lista(v) {
    return Array.isArray(v) ? v : [];
  }

  function erroNaTela(e) {
    if (e && e.bloqueio) return;
    u.avisarErro(e);
  }

  function rotulo(tipo) {
    const [emoji, nome] = ROTULOS[tipo] || ROTULOS.ciclo_mencao;
    return h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, `${emoji} `), nome);
  }

  function ordinal(n) {
    return `${n}º`;
  }

  // "28/09" (ou "28/09/2025" em outro ano).
  function dia(iso) {
    if (!iso) return '';
    const ano = String(iso).slice(0, 4);
    return ano === u.hojeIso().slice(0, 4) ? u.diaMes(iso) : u.dataBr(iso);
  }

  function botaoAcao(texto, classe, fazer, depois) {
    const botao = h('button', { type: 'button', class: classe }, texto);
    botao.addEventListener('click', async () => {
      botao.disabled = true;
      try {
        await fazer();
        if (depois) await depois();
      } catch (e) {
        erroNaTela(e);
      } finally {
        if (botao.isConnected) botao.disabled = false;
      }
    });
    return botao;
  }

  function campoData(texto, valor, extras, aoMudar, dica) {
    const input = h('input', Object.assign({ id: u.novoId('ciclo-data'), type: 'date', value: valor || '' }, extras || {}));
    input.addEventListener('change', (ev) => aoMudar(ev.target.value, ev.target));
    return u.campo(texto, input, dica);
  }

  function chipsSintomas(sintomas, remover) {
    const itens = lista(sintomas);
    if (!itens.length) return null;
    return h('ul', { class: 'chips', 'aria-label': 'Como ela se sentiu' }, itens.map((s, i) => h('li', { class: 'chip' },
      s.palavras || s.rotulo || '',
      remover ? h('button', {
        type: 'button', class: 'chip-x', 'aria-label': `Não guardar “${s.palavras || s.rotulo || ''}”`,
        onclick: () => remover(i),
      }, '×') : null)));
  }

  // ================================================================ revisão (o miolo de cada extra)

  function sobrepoe(a, b) {
    return Boolean(a && b) && (a.includes(b) || b.includes(a));
  }

  // Os cartões das regras tirados da mesma frase (o servidor os deixa "descartados" ao guardar).
  function itensDaFrase(extra, revisao) {
    return lista(revisao && revisao.itens).filter((it) => it && !it._removido && it.tipo !== 'nota'
      && sobrepoe(String(it.trecho || ''), String(extra.trecho || '')));
  }

  function textoData(extra) {
    const aprox = extra.exatidao === 'aproximada' ? ' (aproximado)' : '';
    if (extra.tipo === 'ciclo_inicio') return `Início: ${u.dataBr(extra.data)}${aprox}`;
    if (extra.tipo === 'ciclo_fim') return `Fim: ${u.dataBr(extra.data)}${aprox}`;
    return extra.inicio ? `Em andamento em ${u.dataBr(extra.data)} (começou em ${u.dataBr(extra.inicio)})`
      : `Em andamento em ${u.dataBr(extra.data)} (início não informado)`;
  }

  function atualizarResumo(extra) {
    const partes = [textoData(extra)];
    const sintomas = lista(extra.sintomas).map((s) => s.palavras).filter(Boolean);
    if (sintomas.length) partes.push(sintomas.join(', '));
    if (extra.fluxo) partes.push(`fluxo ${extra.fluxo}`);
    extra.resumo = partes.join(' · ');
  }

  function camposDeData(extra, ctx) {
    const rev = ctx.revisao || {};
    const max = rev.data_registro || u.hojeIso();
    const partes = [];
    if (extra.tipo === 'ciclo_durante') {
      partes.push(campoData('Sabe quando começou?', extra.inicio, { max: extra.data || max }, (v) => {
        extra.inicio = v || null;
        delete extra.dia_do_ciclo;
        atualizarResumo(extra);
        ctx.redesenhar();
        u.falar(v ? `Começou em ${u.dataBr(v)}.` : 'Sem data de início.');
      }, extra.dia_do_ciclo
        ? `Pelo “${ordinal(extra.dia_do_ciclo)} dia” do texto. Se não souber, deixe em branco: entra como “já estava em andamento”.`
        : 'Se não souber, deixe em branco: entra como “já estava em andamento” e não conta na previsão.'));
      if (extra.inicio) {
        partes.push(h('button', {
          type: 'button', class: 'botao botao-texto',
          onclick: () => { extra.inicio = null; delete extra.dia_do_ciclo; atualizarResumo(extra); ctx.redesenhar(); u.falar('Sem data de início.'); },
        }, 'Não sei quando começou'));
      }
      return partes;
    }
    const nome = extra.tipo === 'ciclo_fim' ? 'Quando terminou' : 'Quando começou';
    let dica = null;
    if (extra.sem_data) dica = 'O texto não diz o dia: usei o dia do registro. Ajuste se foi outro dia.';
    else if (extra.exatidao === 'aproximada') dica = 'O texto não diz o dia exato. Se souber, escolha o dia: assim ele conta na previsão.';
    else if (extra.data_expressao) dica = `Pelo “${extra.data_expressao}” do texto.`;
    partes.push(campoData(nome, extra.data, { max }, (v) => {
      if (!v) return;
      extra.data = v;
      extra.precisao = 'dia';
      if (extra.exatidao !== 'talvez') extra.exatidao = 'dia';
      extra.sem_data = false;
      atualizarResumo(extra);
      ctx.redesenhar();
      u.falar(`Data: ${u.dataBr(v)}.`);
    }, dica));
    const alternativas = lista(extra.alternativas).filter((a) => a && a !== extra.data);
    if (alternativas.length) {
      partes.push(h('p', { class: 'sutil' }, 'Ou foi outro dia? ',
        alternativas.map((a) => h('button', {
          type: 'button', class: 'botao botao-texto botao-pequeno',
          onclick: () => { extra.data = a; atualizarResumo(extra); ctx.redesenhar(); u.falar(`Data: ${u.dataBr(a)}.`); },
        }, u.diaComSemana(a)))));
    }
    if (extra.exatidao === 'talvez') {
      partes.push(h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '🔵 '),
        'Você escreveu como dúvida: fica como “talvez” e não entra na previsão até você confirmar a data no 🌙.'));
    }
    return partes;
  }

  function revisaoExtra(extra, ctx) {
    const partes = [h('div', { class: 'item-topo' }, rotulo(extra.tipo), extra.certeza ? selo(extra.certeza) : null)];
    partes.push(h('p', { class: 'extra-resumo' }, extra.resumo || ''));
    if (['ciclo_inicio', 'ciclo_durante', 'ciclo_fim'].includes(extra.tipo)) partes.push(camposDeData(extra, ctx));
    if (lista(extra.sintomas).length) {
      const podeTirar = extra.tipo !== 'ciclo_sintoma' || lista(extra.sintomas).length > 1;
      partes.push(h('p', { class: 'rotulo-pequeno' }, 'Com as palavras do registro:'),
        chipsSintomas(extra.sintomas, podeTirar ? (i) => {
          extra.sintomas = lista(extra.sintomas).filter((_, n) => n !== i);
          if (extra.tipo === 'ciclo_sintoma') extra.resumo = `Sintomas: ${extra.sintomas.map((s) => s.palavras).join(', ')}`;
          else atualizarResumo(extra);
          ctx.redesenhar();
          u.falar('Tirado.');
        } : null));
    }
    if (extra.fluxo !== undefined && extra.fluxo !== null) {
      const sel = h('select', { id: u.novoId('ciclo-fluxo'), value: extra.fluxo },
        Object.entries(FLUXOS).map(([v, t]) => h('option', { value: v }, t)));
      sel.addEventListener('change', (ev) => {
        extra.fluxo = ev.target.value;
        if (extra.tipo === 'ciclo_fluxo') extra.resumo = `Fluxo ${extra.fluxo}`;
        else if (extra.tipo !== 'ciclo_sintoma') atualizarResumo(extra);
      });
      partes.push(u.campo('Fluxo (como ela disse)', sel));
    }
    if (extra.tipo === 'ciclo_ajuda') {
      const id = u.novoId('ciclo-disse');
      const caixa = h('input', { id, type: 'checkbox', checked: extra.certeza === 'confirmado' });
      caixa.addEventListener('change', (ev) => {
        extra.certeza = ev.target.checked ? 'confirmado' : (extra.certeza === 'inferencia' ? 'inferencia' : 'observado');
        ctx.redesenhar();
        u.falar(ev.target.checked ? 'Marcado: foi ela quem disse.' : 'Desmarcado.');
      });
      partes.push(h('label', { class: 'opcao-linha', for: id }, caixa, h('span', null, 'Foi ela quem disse')),
        h('p', { class: 'dica' }, 'No Hoje, só o que ela disse que ajuda vira sugestão (e sem citar o motivo).'));
    }
    if (extra.tipo === 'ciclo_mencao') {
      partes.push(h('p', { class: 'dica' }, MENCOES[extra.valor] || MENCOES.geral));
    }
    if (extra.trecho) partes.push(u.citacao(extra.trecho));
    if (extra.motivo) partes.push(h('p', { class: 'sutil' }, extra.motivo));
    const junto = itensDaFrase(extra, ctx.revisao);
    if (junto.length && extra.de_quem !== 'outra') {
      partes.push(h('p', { class: 'nota-cuidado' },
        'Ao guardar, as outras informações desta frase ficam só no registro (fora de “Como ela está”, do Hoje e das ligações com outros registros): ',
        junto.map((it) => `“${u.encurtar(it.resumo || it.trecho, 60)}”`).join(', '), '.'));
    } else if (junto.length) {
      partes.push(h('p', { class: 'nota-cuidado' },
        'Ao guardar, as outras informações desta frase ficam só no registro, fora de “Como ela está” e do Hoje.'));
    }
    return partes;
  }

  // ================================================================ Hoje (sem as palavras do ciclo)

  function cartaoCuidado(c, ctx) {
    return [
      h('p', { class: 'alerta-texto' }, c.texto || ''),
      h('div', { class: 'alerta-acoes' },
        c.porque ? h('details', { class: 'porque' }, h('summary', null, 'Por quê?'), h('p', null, c.porque)) : h('span'),
        h('div', { class: 'acoes' },
          h('a', { href: '#ela/ciclo', class: 'botao botao-texto' }, 'Ver detalhes'),
          ctx.botaoDispensar())),
      h('p', { class: 'sutil possibilidade' }, 'É só uma possibilidade: quem conhece ela é você.'),
    ];
  }

  // ================================================================ a aba 🌙 Ciclo

  function secao(titulo, ...conteudo) {
    return u.secao(titulo, ...conteudo);
  }

  function blocoData(iso) {
    const d = u.paraData(iso);
    const meses = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
    return h('span', { class: 'data-bloco', 'aria-hidden': 'true' },
      h('span', { class: 'data-dia' }, d ? String(d.getDate()).padStart(2, '0') : '?'),
      h('span', { class: 'data-mes' }, d ? meses[d.getMonth()] : ''));
  }

  function linkRegistros(ids) {
    const lista_ = lista(ids).filter((x) => x != null);
    if (!lista_.length) return null;
    return h('span', { class: 'links-registros' }, lista_.map((rid, i) => [i ? ' · ' : '',
      u.linkRegistro(rid, lista_.length > 1 ? `registro ${i + 1}` : 'ver registro')]));
  }

  function desenharAgora(d, recarregar, abrirFormulario) {
    const partes = [];
    const a = d.atual;
    if (a && a.em_andamento) {
      partes.push(h('p', { class: 'item-resumo' },
        `Em andamento: ${ordinal(a.dia)} dia (começou em ${dia(a.inicio)}${a.aproximado ? ', data aproximada' : ''}).`));
    } else if (a) {
      partes.push(h('p', null, `Último início anotado: ${dia(a.inicio)} (${u.textoDias(-(a.dia - 1))})${a.fim ? `, terminou em ${dia(a.fim)}` : ''}.`));
    }
    for (const texto of lista(d.no_momento)) {
      partes.push(h('p', { class: 'nota-cuidado' }, h('span', { 'aria-hidden': 'true' }, '💙 '), texto));
    }
    const p = d.previsao;
    if (p) {
      partes.push(h('div', { class: 'cartao' },
        h('div', { class: 'item-topo' }, h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, '📅 '), 'Faixa prevista'),
          selo('inferencia')),
        h('p', { class: 'item-resumo' }, p.texto),
        p.aviso ? h('p', { class: 'dica' }, h('span', { 'aria-hidden': 'true' }, '〰️ '), 'Irregular: ', p.aviso) : null,
        p.texto_passou ? h('p', { class: 'nota-cuidado' }, p.texto_passou) : null,
        h('details', { class: 'porque' }, h('summary', null, 'Por quê?'), h('p', null, p.porque))));
    } else if (d.sem_previsao) {
      partes.push(h('p', { class: 'sutil' }, d.sem_previsao));
    }
    for (const q of lista(d.perguntas)) {
      partes.push(h('div', { class: 'cartao aviso-suave' },
        h('p', null, q.texto),
        h('div', { class: 'acoes' },
          h('button', { type: 'button', class: 'botao botao-secundario botao-pequeno', onclick: () => abrirFormulario(q) },
            '✍️ Anotar o início que faltou'),
          botaoAcao('Não, foi assim mesmo', 'botao botao-texto',
            () => api('POST', `/ciclo/ciclos/${encodeURIComponent(q.ciclo_id)}/intervalo`, { real: true }),
            async () => { u.avisar('Combinado: esse intervalo fica fora da conta e a pergunta não volta.'); await recarregar(); }))));
    }
    if (d.duracao) partes.push(h('p', null, d.duracao.texto));
    return partes.length ? secao('🌙 Agora', partes) : null;
  }

  function desenharPlanejamento(d) {
    const itens = lista(d.planejamento);
    if (!itens.length) return null;
    return secao('🗓️ Para planejar',
      h('ul', { class: 'lista-entradas' }, itens.map((pl) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, selo('inferencia'), h('span', { class: 'entrada-texto' }, pl.texto)),
        lista(pl.lembrar).length ? h('ul', { class: 'lista-explica' }, pl.lembrar.map((t) => h('li', null, t))) : null,
        pl.registro_id != null ? h('p', { class: 'entrada-rodape sutil' }, u.linkRegistro(pl.registro_id, 'ver o evento')) : null))));
  }

  // `ids`: uma anotação ou todas as de um grupo ("O que ajuda" junta as vezes em que apareceu): apagar
  // tira todas, para o item não voltar com uma vez a menos (apagar é apagar).
  function apagarAnotacao(ids, recarregar, texto) {
    const todos = (Array.isArray(ids) ? ids : [ids]).filter((x) => x != null);
    const varias = todos.length > 1;
    return botaoAcao('Apagar', 'botao botao-texto botao-perigo-texto', async () => {
      const ok = await u.confirmar({
        titulo: varias ? `Apagar as ${todos.length} anotações?` : 'Apagar esta anotação?',
        texto: `“${u.encurtar(texto, 80)}” sai do 🌙 Ciclo${varias ? ` (as ${todos.length} vezes em que foi anotado)` : ''} `
          + `e das cópias automáticas. ${varias ? 'Os registros de onde vieram continuam' : 'O registro de onde veio continua'} no diário.`,
        ok: 'Apagar', perigo: true,
      });
      if (!ok) throw Object.assign(new Error(''), { bloqueio: true });
      for (const id of todos) await api('DELETE', `/ciclo/eventos/${encodeURIComponent(id)}`);
    }, async () => { u.avisar('Apagado.'); await recarregar(); });
  }

  function desenharSintomas(d, recarregar) {
    const padroes = lista(d.padroes);
    const recentes = lista(d.sintomas);
    if (!padroes.length && !recentes.length) return null;
    return secao('🌙 Como ela se sente',
      padroes.length ? [h('p', { class: 'sutil' }, 'O que os registros mostram (contagem das anotações, sem diagnóstico):'),
        h('ul', { class: 'lista-simples' }, padroes.map((p) => h('li', { class: 'lembrar' }, p.texto)))] : null,
      recentes.length ? [h('h3', { class: 'titulo-pequeno' }, 'Anotado por último'),
        h('ul', { class: 'lista-entradas' }, recentes.map((s) => h('li', { class: 'entrada' },
          h('div', { class: 'entrada-topo' }, selo(s.certeza), h('span', { class: 'entrada-texto' }, s.palavras || '')),
          s.trecho ? u.citacao(s.trecho) : null,
          h('p', { class: 'entrada-rodape sutil' }, `em ${dia(s.data)}`,
            s.registro_id != null ? [' · ', u.linkRegistro(s.registro_id, 'ver registro')] : null, ' ',
            apagarAnotacao(s.id, recarregar, s.palavras || ''))))) ] : null);
  }

  function desenharAjudas(d, recarregar) {
    const ajudas = lista(d.ajudas);
    if (!ajudas.length) return null;
    return secao('💙 O que ajuda',
      h('p', { class: 'sutil' }, 'Do que você registrou. No Hoje, só o que ela disse (🟢) vira sugestão.'),
      h('ul', { class: 'lista-entradas' }, ajudas.map((a) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, selo(a.certeza),
          h('span', { class: 'entrada-texto' }, `${a.polaridade > 0 ? 'Ajuda' : 'Não ajuda'}: ${a.valor}`)),
        a.motivo ? h('p', { class: 'dica' }, a.motivo) : null,
        a.trecho ? u.citacao(a.trecho) : null,
        h('p', { class: 'entrada-rodape sutil' }, a.quando || '',
          a.vezes > 1 ? ` · ${a.vezes} vezes` : '',
          a.registro_id != null ? [' · ', u.linkRegistro(a.registro_id, 'ver registro')] : null, ' ',
          apagarAnotacao(lista(a.ids).length ? lista(a.ids) : a.id, recarregar, a.valor || ''))))));

  }

  function desenharFertil(d) {
    const f = d.fertil;
    if (!f) {
      return d.previsao && d.fertil_motivo ? h('p', { class: 'sutil' }, h('span', { 'aria-hidden': 'true' }, '🔵 '), d.fertil_motivo) : null;
    }
    return secao('🔵 Período fértil (estimativa)',
      h('div', { class: 'cartao' },
        h('div', { class: 'item-topo' }, selo('inferencia')),
        h('p', { class: 'item-resumo' }, f.texto, f.passou ? ' (já passou neste ciclo)' : ''),
        h('p', { class: 'nota-importante', role: 'note' }, f.aviso),
        h('details', { class: 'porque' }, h('summary', null, 'Como é calculado?'), h('p', null, f.porque))));
  }

  function formularioCiclo(inicial, aoSalvar, aoCancelar, titulo) {
    const erro = h('p', { class: 'erro-campo', role: 'alert' });
    const ini = h('input', { id: u.novoId('ciclo-inicio'), type: 'date', required: true, value: inicial.inicio || '', max: u.hojeIso() });
    const fim = h('input', { id: u.novoId('ciclo-fim'), type: 'date', value: inicial.fim || '', max: u.hojeIso() });
    const form = h('form', { class: 'cartao form-item', novalidate: true },
      titulo ? h('h3', { class: 'titulo-pequeno' }, titulo) : null,
      erro,
      h('div', { class: 'grade-2' },
        u.campo('Começou em', ini),
        u.campo('Terminou em (se souber)', fim)),
      h('div', { class: 'acoes' },
        h('button', { type: 'submit', class: 'botao botao-primario botao-pequeno' }, '💾 Salvar'),
        aoCancelar ? h('button', { type: 'button', class: 'botao botao-texto', onclick: aoCancelar }, 'Cancelar') : null));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      erro.textContent = '';
      if (!ini.value) {
        erro.textContent = 'Escolha o dia em que começou.';
        ini.focus();
        return;
      }
      const botao = form.querySelector('button[type="submit"]');
      botao.disabled = true;
      try {
        await aoSalvar({ inicio: ini.value, fim: fim.value || null });
      } catch (e) {
        if (e && e.bloqueio) return;
        erro.textContent = (e && e.message) || 'Não deu para salvar agora.';
        ini.focus();
      } finally {
        if (botao.isConnected) botao.disabled = false;
      }
    });
    return form;
  }

  function desenharHistorico(d, recarregar, area) {
    const ciclos = lista(d.historico);
    const ul = h('ul', { class: 'lista-proximos' }, ciclos.map((c) => {
      const li = h('li');
      const mostrar = () => {
        u.limpar(li);
        const titulo = c.fim ? `${dia(c.inicio)} → ${dia(c.fim)}` : `Começou em ${dia(c.inicio)}`;
        const detalhes = [];
        if (c.duracao) detalhes.push(`${c.duracao} ${c.duracao === 1 ? 'dia' : 'dias'}`);
        else detalhes.push('fim não anotado');
        if (c.intervalo != null) {
          const obs = SITUACAO_INTERVALO[c.situacao_intervalo];
          detalhes.push(`${c.intervalo} dias desde o início anterior${obs ? ` (${obs})` : ''}`);
        }
        if (c.exatidao === 'aproximada') detalhes.push('início aproximado');
        if (c.exatidao === 'talvez') detalhes.push('🔵 talvez (você escreveu como dúvida)');
        if (c.origem === 'manual') detalhes.push('anotado por você');
        u.anexar(li,
          h('div', { class: 'proximo-link' }, blocoData(c.inicio),
            h('span', { class: 'proximo-corpo' },
              h('span', { class: 'proximo-texto' }, titulo),
              h('span', { class: 'sutil' }, detalhes.join(' · ')),
              lista(c.sintomas).length ? h('span', { class: 'sutil' }, `Anotado: ${c.sintomas.join(', ')}`) : null,
              lista(c.fluxos).length ? h('span', { class: 'sutil' }, `Fluxo: ${c.fluxos.join(', ')}`) : null)),
          h('div', { class: 'acoes' },
            linkRegistros(lista(c.registros).length ? c.registros : [c.registro_id, c.fim_registro_id].filter((x) => x != null)),
            h('button', { type: 'button', class: 'botao botao-texto', onclick: editar }, '✏️ Editar'),
            botaoAcao('🗑️ Apagar', 'botao botao-texto botao-perigo-texto', async () => {
              const ok = await u.confirmar({
                titulo: 'Apagar este ciclo?',
                texto: `O ciclo que começou em ${u.dataBr(c.inicio)} e as anotações ligadas a ele saem do 🌙 (e das cópias automáticas). Os registros do diário continuam.`,
                ok: 'Apagar', perigo: true,
              });
              if (!ok) throw Object.assign(new Error(''), { bloqueio: true });
              await api('DELETE', `/ciclo/ciclos/${encodeURIComponent(c.id)}`);
            }, async () => { u.avisar('Ciclo apagado.'); await recarregar(); })));
      };
      const editar = () => {
        u.limpar(li);
        u.anexar(li, formularioCiclo({ inicio: c.inicio, fim: c.fim }, async (valores) => {
          const corpo = {};
          if (valores.inicio !== c.inicio || c.exatidao !== 'dia') corpo.inicio = valores.inicio;
          if ((valores.fim || null) !== (c.fim || null)) corpo.fim = valores.fim;
          if (Object.keys(corpo).length) await api('PATCH', `/ciclo/ciclos/${encodeURIComponent(c.id)}`, corpo);
          u.avisar('Ciclo atualizado.');
          await recarregar();
        }, () => { mostrar(); const b = li.querySelector('button'); if (b) b.focus(); }, `Editar o ciclo de ${dia(c.inicio)}`));
        const primeiro = li.querySelector('input');
        if (primeiro) primeiro.focus();
      };
      mostrar();
      return li;
    }));
    const novo = h('div');
    const abrir = (pergunta) => {
      u.limpar(novo);
      u.anexar(novo, formularioCiclo({}, async (valores) => {
        await api('POST', '/ciclo/ciclos', valores);
        u.avisar('Início anotado. 💙');
        await recarregar();
      }, () => { u.limpar(novo); u.anexar(novo, botaoNovo); botaoNovo.focus(); },
      pergunta ? `Anotar o início que faltou (entre ${dia(pergunta.de)} e ${dia(pergunta.ate)})` : 'Anotar um início'));
      const primeiro = novo.querySelector('input');
      if (primeiro) primeiro.focus();
      if (pergunta && novo.scrollIntoView) novo.scrollIntoView({ block: 'center' });
    };
    const botaoNovo = h('button', { type: 'button', class: 'botao botao-secundario', onclick: () => abrir(null) }, '✍️ Anotar um início');
    u.anexar(novo, botaoNovo);
    area.abrirFormulario = abrir;
    return secao('📆 Histórico',
      ciclos.length ? ul : u.vazioPequeno('Nenhum ciclo anotado ainda.'),
      novo);
  }

  function desenharAnotacoes(d, recarregar) {
    const itens = lista(d.anotacoes);
    if (!itens.length) return null;
    const textos = { mencao: 'Comentário', fim: 'Fim sem início anotado', durante: 'Em andamento (sem início anotado)' };
    return h('details', { class: 'mais-detalhes' },
      h('summary', null, `Outras anotações (${itens.length})`),
      h('ul', { class: 'lista-entradas' }, itens.map((e) => h('li', { class: 'entrada' },
        h('div', { class: 'entrada-topo' }, selo(e.certeza),
          h('span', { class: 'entrada-texto' }, `${textos[e.tipo] || 'Anotação'} · ${dia(e.data)}`)),
        e.trecho ? u.citacao(e.trecho) : null,
        h('p', { class: 'entrada-rodape sutil' },
          e.registro_id != null ? u.linkRegistro(e.registro_id, 'ver registro') : null, ' ',
          apagarAnotacao(e.id, recarregar, e.trecho || ''))))));
  }

  function desenharAjustes(d, recarregar) {
    const conf = d.ajustes || {};
    const opcao = (chave, texto, dica) => {
      const id = u.novoId('ciclo-ajuste');
      const caixa = h('input', { id, type: 'checkbox', checked: conf[chave] !== false, 'aria-describedby': `${id}-dica` });
      caixa.addEventListener('change', async (ev) => {
        const alvo = ev.target;
        alvo.disabled = true;
        try {
          await api('PATCH', '/ciclo/ajustes', { [chave]: alvo.checked });
          u.falar('Ajuste salvo.');
          await recarregar();
        } catch (e) {
          alvo.checked = !alvo.checked;
          erroNaTela(e);
        } finally {
          if (alvo.isConnected) alvo.disabled = false;
        }
      });
      return [h('label', { class: 'opcao-linha', for: id }, caixa, h('span', null, texto)), h('p', { class: 'dica', id: `${id}-dica` }, dica)];
    };
    return h('details', { class: 'mais-detalhes' },
      h('summary', null, '⚙️ Ajustes do 🌙'),
      opcao('lembrete_hoje', 'Lembrete de cuidado no Hoje', '1 a 2 dias antes da faixa prevista, um aviso neutro (sem as palavras do ciclo), com o que ela disse que ajuda.'),
      opcao('mostrar_fertil', 'Mostrar a estimativa do período fértil', 'Só aqui, nunca no Hoje. É aproximada e não serve para evitar ou planejar gravidez.'));
  }

  function desenharZonaPerigo(recarregar) {
    return h('div', { class: 'zona-perigo' },
      h('h3', { class: 'titulo-pequeno' }, 'Apagar todos os dados do ciclo'),
      h('p', { class: 'sutil' }, 'Ciclos, sintomas, o que ajuda e os ajustes deste espaço saem do diário e das cópias automáticas. Os registros onde você escreveu sobre isso continuam em Registros (e continuam fora da IA); apague-os lá se quiser.'),
      botaoAcao('🗑️ Apagar todos os dados do ciclo', 'botao botao-perigo', async () => {
        const ok = await u.confirmar({
          titulo: 'Apagar todos os dados do ciclo?',
          texto: 'Não dá para desfazer. Cópias que você guardou em outro lugar não mudam.',
          ok: 'Apagar tudo', perigo: true,
        });
        if (!ok) throw Object.assign(new Error(''), { bloqueio: true });
        await api('POST', '/ciclo/apagar-tudo', { confirmar: true });
      }, async () => { u.avisar('Pronto: os dados do ciclo foram apagados.'); await recarregar(); }));
  }

  async function desenharAba(caixa, ctx) {
    const atual = ctx && typeof ctx.atual === 'function' ? ctx.atual : () => true;
    u.limpar(caixa);
    u.anexar(caixa, u.carregando('Abrindo o 🌙 Ciclo…'));
    let d;
    try {
      d = await api('GET', '/ciclo');
    } catch (e) {
      if (e && e.bloqueio) return;
      throw e;
    }
    if (!atual() || !caixa.isConnected) return;
    u.limpar(caixa);
    const recarregar = async () => {
      if (atual() && caixa.isConnected) await desenharAba(caixa, ctx);
    };
    const area = { abrirFormulario: () => {} };
    const historico = desenharHistorico(d, recarregar, area);
    u.anexar(caixa,
      h('p', { class: 'nota-cuidado' }, d.aviso_consentimento || ''),
      d.tem_dados ? null : h('div', { class: 'vazio' },
        h('div', { class: 'vazio-icone', 'aria-hidden': 'true' }, '🌙'),
        h('p', null, 'Nada anotado sobre o ciclo ainda. Quando ela comentar, é só escrever em Registrar, do jeito que for (“ela menstruou ontem”, “tá com cólica, bolsa de água quente ajuda”). Ou anote um início aqui embaixo.'),
        h('a', { href: '#registrar', class: 'botao botao-primario' }, '✍️ Registrar algo')),
      d.tem_dados ? desenharAgora(d, recarregar, (q) => area.abrirFormulario(q)) : null,
      desenharPlanejamento(d),
      desenharSintomas(d, recarregar),
      desenharAjudas(d, recarregar),
      desenharFertil(d),
      historico,
      desenharAnotacoes(d, recarregar),
      desenharAjustes(d, recarregar),
      d.tem_dados ? desenharZonaPerigo(recarregar) : null);
  }

  // O aviso de planejamento na revisão de um acontecimento que cai nos dias previstos (20.8): só
  // informação (não é dado do ciclo, não deixa o registro 🔒, não grava nada).
  function revisaoPlanejamento(e) {
    const lembrar = Array.isArray(e.lembrar) ? e.lembrar.filter((t) => typeof t === 'string' && t) : [];
    return [
      h('div', { class: 'item-topo' }, h('span', { class: 'tipo' }, h('span', { 'aria-hidden': 'true' }, '🌙 '), 'Para planejar com carinho')),
      h('p', { class: 'extra-resumo' }, e.resumo || ''),
      lembrar.length ? h('ul', { class: 'lista-simples' }, lembrar.map((t) => h('li', null, t))) : null,
      u.citacao(e.trecho),
    ];
  }

  for (const tipo of Object.keys(ROTULOS)) E.registrarRevisaoExtra(tipo, revisaoExtra);
  E.registrarRevisaoExtra('ciclo_planejamento', revisaoPlanejamento, { opcional: true });
  E.registrarRevisaoExtra('ciclo', revisaoExtra);
  E.registrarCartaoHoje('ciclo_cuidado', cartaoCuidado);
  E.registrarAbaEla('ciclo', '🌙 Ciclo', (painel, ctx) => desenharAba(painel, ctx));
})();
