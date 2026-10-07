"""Testes do motor de regras: pessoas, categorias, certeza e itens (seções 4 a 7 do DESIGN.md).

As datas são montadas à mão (regras/datas.py é outro módulo). Referência fixa:
quarta-feira, 30/09/2026.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import date

from entrenos.modelos import CATEGORIAS, Certeza, DataEncontrada, Precisao, TipoItem
from entrenos.motor import Conhecimento, MotorRegras
from entrenos.regras.categorias import LEXICO, RAIZ_MINIMA, classificar
from entrenos.regras.certeza import classificar_certeza, trechos_confirmados, trechos_nao_afirmados
from entrenos.regras.itens import extrair_itens
from entrenos.regras.pessoas import RELACOES, casar_pessoa, e_nome_dela, extrair_pessoas, mesma_relacao
from entrenos.texto import normalizar

REF = date(2026, 9, 30)
_PASTA: str | None = None
_ANTES: str | None = None


def setUpModule() -> None:
    # Estes testes não gravam nada, mas por garantia nada aponta para a pasta real de dados.
    global _PASTA, _ANTES
    _ANTES = os.environ.get("ENTRENOS_DADOS")
    _PASTA = tempfile.mkdtemp(prefix="entrenos_regras_")
    os.environ["ENTRENOS_DADOS"] = _PASTA


def tearDownModule() -> None:
    if _ANTES is None:
        os.environ.pop("ENTRENOS_DADOS", None)
    else:
        os.environ["ENTRENOS_DADOS"] = _ANTES
    if _PASTA:
        shutil.rmtree(_PASTA, ignore_errors=True)


def data(texto: str, expressao: str, quando: date | None, **kw) -> DataEncontrada:
    """DataEncontrada montada à mão, com posições reais no texto."""
    i = texto.index(expressao)
    return DataEncontrada(expressao=expressao, inicio=i, fim=i + len(expressao), data=quando, **kw)


def itens(texto: str, datas=(), conhecidas=()):
    pessoas = extrair_pessoas(texto, list(conhecidas))
    return extrair_itens(texto, REF, pessoas, list(datas))


def do_tipo(lista, tipo: TipoItem):
    return [i for i in lista if i.tipo == tipo]


APRESENTACAO = ("Hoje ela me contou que esta preocupada porque vai apresentar um projeto no trabalho "
                "dia 20. Ela disse que odeia falar em publico e que normalmente fica muito nervosa.")
MAE_CIRURGIA = ("Ela comentou hoje que a mae dela vai fazer uma cirurgia em novembro e que ela esta "
                "preocupada.")
MARIANA = "A Mariana, melhor amiga dela, faz aniversario dia 12/05."


# ====================================================================== pessoas

class TestPessoas(unittest.TestCase):
    def test_mae_dela(self):
        texto = "Ela comentou que a mãe dela vai viajar."
        ps = extrair_pessoas(texto, [])
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0].relacao, "mãe")
        self.assertIsNone(ps[0].nome)
        self.assertTrue(ps[0].nova)

    def test_mae_sem_acento(self):
        ps = extrair_pessoas(MAE_CIRURGIA, [])
        self.assertEqual([p.relacao for p in ps], ["mãe"])
        self.assertEqual(ps[0].trecho, "a mae dela")

    def test_minha_mae_e_do_usuario(self):
        ps = extrair_pessoas("Minha mae ligou.", [])
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0].relacao, "mãe (sua)")

    def test_meu_pai_e_do_usuario(self):
        ps = extrair_pessoas("Meu pai perguntou dela.", [])
        self.assertEqual(ps[0].relacao, "pai (seu)")

    def test_nome_e_relacao_depois(self):
        ps = extrair_pessoas(MARIANA, [])
        self.assertEqual(len(ps), 1)
        self.assertEqual((ps[0].nome, ps[0].relacao), ("Mariana", "melhor amiga"))
        self.assertEqual(ps[0].trecho, "Mariana, melhor amiga dela")

    def test_relacao_e_nome_depois(self):
        ps = extrair_pessoas("A mãe dela, Sandra, ligou para ela.", [])
        self.assertEqual(len(ps), 1)
        self.assertEqual((ps[0].nome, ps[0].relacao), ("Sandra", "mãe"))

    def test_amiga_dela_nome(self):
        ps = extrair_pessoas("Ela saiu com a amiga dela Júlia.", [])
        self.assertEqual([(p.nome, p.relacao) for p in ps], [("Júlia", "amiga")])

    def test_nome_com_relacao_entre_parenteses(self):
        texto = "Ela jantou com Mariana (melhor amiga) ontem."
        ps = extrair_pessoas(texto, [])
        self.assertEqual((ps[0].nome, ps[0].relacao), ("Mariana", "melhor amiga"))
        self.assertEqual(ps[0].trecho, "Mariana (melhor amiga)")

    def test_nome_composto(self):
        ps = extrair_pessoas("Ela almoçou com a Ana Paula.", [])
        self.assertEqual([p.nome for p in ps], ["Ana Paula"])

    def test_meses_dias_e_datas_nao_sao_nomes(self):
        ps = extrair_pessoas("Ela vai viajar em Novembro, na Sexta depois do Natal.", [])
        self.assertEqual(ps, [])

    def test_lugares_e_marcas_nao_sao_nomes(self):
        ps = extrair_pessoas("Ela assistiu Netflix em São Paulo e foi para Curitiba.", [])
        self.assertEqual(ps, [])

    def test_palavra_no_inicio_da_frase_nao_vira_nome(self):
        ps = extrair_pessoas("Talvez ela goste. Ontem ela riu. Tempo tá feio. Almoço foi ótimo. Comprei flores.", [])
        self.assertEqual(ps, [])

    def test_nome_no_inicio_seguido_de_verbo_vira_pessoa(self):
        # Sem isso, "Camila vai casar em maio" virava o casamento dela.
        for texto, nome in (("Mariana ligou.", "Mariana"), ("Camila vai casar em maio", "Camila"),
                            ("Mariana ligou pra ela chorando ontem", "Mariana"),
                            ("Pedro não veio ao jantar.", "Pedro")):
            with self.subTest(texto=texto):
                self.assertEqual([p.nome for p in extrair_pessoas(texto, [])], [nome])

    def test_nome_no_inicio_vale_se_cadastrado(self):
        ps = extrair_pessoas("Mariana ligou.", [{"id": 3, "nome": "Mariana", "relacao": "melhor amiga"}])
        self.assertEqual([(p.nome, p.pessoa_id, p.nova) for p in ps], [("Mariana", 3, False)])

    def test_nome_dela_nao_aparece(self):
        ps = extrair_pessoas("Hoje a Ana comentou que a Bia ligou.", [], nome_dela="Ana Souza")
        self.assertEqual([p.nome for p in ps], ["Bia"])

    def test_amiga_generica_nao_conta(self):
        self.assertEqual(extrair_pessoas("Ela saiu com uma amiga.", []), [])

    def test_medico_generico_nao_conta(self):
        self.assertEqual(extrair_pessoas("Ela foi ao médico hoje.", []), [])

    def test_cachorro_dela_com_nome(self):
        ps = extrair_pessoas("O cachorro dela, Thor, fugiu.", [])
        self.assertEqual([(p.nome, p.relacao) for p in ps], [("Thor", "cachorro")])

    def test_cachorro_que_ela_quer_nao_e_pessoa(self):
        self.assertEqual(extrair_pessoas("Ela quer um cachorro.", []), [])

    def test_avo_e_avo_sao_duas(self):
        ps = extrair_pessoas("O avô dela e a avó dela vieram.", [])
        self.assertEqual(sorted(p.relacao for p in ps), ["avó", "avô"])

    def test_irma_de_outra_pessoa(self):
        ps = extrair_pessoas("A irmã da Mariana casou.", [])
        self.assertIn("irmã de Mariana", [p.relacao for p in ps])
        self.assertIn("Mariana", [p.nome for p in ps])

    def test_dia_das_maes_nao_e_pessoa(self):
        self.assertEqual(extrair_pessoas("Ela ama o dia das mães.", []), [])

    def test_mesma_pessoa_nao_duplica(self):
        ps = extrair_pessoas("A mãe dela, Sandra, ligou. Depois a Sandra mandou mensagem. A mãe dela riu.", [])
        self.assertEqual(len(ps), 1)
        self.assertEqual((ps[0].nome, ps[0].relacao), ("Sandra", "mãe"))

    def test_trecho_e_literal(self):
        texto = "A mãe dela, Sandra, e o chefe dela brigaram. Minha irmã e a Mariana (amiga) riram."
        for p in extrair_pessoas(texto, []):
            self.assertEqual(texto[p.inicio:p.fim], p.trecho)

    def test_casar_por_apelido(self):
        conhecidas = [{"id": 3, "nome": "Mariana", "relacao": "melhor amiga", "apelidos": ["Mari"]}]
        ps = extrair_pessoas("A Mari ligou.", conhecidas)
        self.assertEqual((ps[0].pessoa_id, ps[0].nome, ps[0].nova), (3, "Mariana", False))
        self.assertEqual(casar_pessoa("Mari", conhecidas), 3)

    def test_casar_por_relacao_unica(self):
        conhecidas = [{"id": 4, "nome": None, "relacao": "mãe", "apelidos": []},
                      {"id": 5, "nome": "Carla", "relacao": "amiga", "apelidos": []}]
        self.assertEqual(casar_pessoa("a mãe dela", conhecidas), 4)
        ps = extrair_pessoas("A mãe dela ligou.", conhecidas)
        self.assertEqual((ps[0].pessoa_id, ps[0].nova), (4, False))

    def test_relacao_repetida_no_cadastro_nao_casa(self):
        conhecidas = [{"id": 1, "nome": "Carla", "relacao": "amiga"}, {"id": 2, "nome": "Bia", "relacao": "amiga"}]
        self.assertIsNone(casar_pessoa("amiga", conhecidas))

    def test_casar_nome_parecido(self):
        conhecidas = [{"id": 7, "nome": "Mariana", "relacao": "amiga"}]
        self.assertEqual(casar_pessoa("Marianna", conhecidas), 7)
        self.assertIsNone(casar_pessoa("Maria", conhecidas))

    def test_relacoes_normalizadas(self):
        self.assertEqual(RELACOES["mae"], "mãe")
        self.assertEqual(RELACOES["melhor amiga"], "melhor amiga")
        self.assertTrue(all(k == normalizar(k) for k in RELACOES))


# ====================================================================== categorias

class TestCategorias(unittest.TestCase):
    def test_lexico_cobre_as_32_categorias(self):
        self.assertEqual(set(LEXICO), set(CATEGORIAS))
        for cat in CATEGORIAS:
            minimo = 40 if cat in ("Comidas", "Saúde", "Trabalho", "Família") else 15
            self.assertGreaterEqual(len(LEXICO[cat]), minimo, cat)

    def test_termos_normalizados(self):
        for cat, termos in LEXICO.items():
            for t in termos:
                self.assertEqual(t, normalizar(t), (cat, t))

    def test_mae_cirurgia(self):
        cats = [c for c, _ in classificar(MAE_CIRURGIA)]
        for esperada in ("Família", "Saúde", "Eventos futuros", "Preocupações"):
            self.assertIn(esperada, cats)

    def test_raiz_pega_derivadas(self):
        self.assertGreaterEqual(len("apresent"), RAIZ_MINIMA)
        for frase in ("A apresentação foi ótima.", "Ela vai apresentar amanhã."):
            self.assertIn("Trabalho", [c for c, _ in classificar(frase)])

    def test_palavra_curta_so_inteira(self):
        self.assertNotIn("Família", [c for c, _ in classificar("O maestro chegou.")])
        self.assertNotIn("Estudos", [c for c, _ in classificar("Provavelmente sim.")])
        self.assertIn("Família", [c for c, _ in classificar("As mães chegaram.")])

    def test_ordem_e_faixa(self):
        res = classificar("Ela adora sushi, pizza e chocolate e odeia a comida do trabalho.")
        pontos = [p for _, p in res]
        self.assertEqual(pontos, sorted(pontos, reverse=True))
        self.assertTrue(all(0 < p <= 1 for p in pontos))
        self.assertEqual(res[0][0], "Comidas")

    def test_vazio(self):
        self.assertEqual(classificar(""), [])
        self.assertEqual(classificar("   "), [])

    def test_expressao_de_varias_palavras(self):
        self.assertIn("Medos", [c for c, _ in classificar("Ela odeia falar em público.")])

    def test_aniversario(self):
        cats = [c for c, _ in classificar("A Mariana faz aniversário dia 12.")]
        self.assertIn("Aniversários", cats)
        self.assertIn("Datas importantes", cats)

    def test_so_categorias_validas(self):
        for c, _ in classificar(APRESENTACAO + " " + MAE_CIRURGIA):
            self.assertIn(c, CATEGORIAS)


# ====================================================================== certeza

class TestCerteza(unittest.TestCase):
    def assertCerteza(self, frase, esperada):
        self.assertEqual(classificar_certeza(frase), esperada, frase)

    def test_ela_disse(self):
        self.assertCerteza("Ela disse que odeia falar em público.", Certeza.CONFIRMADO)

    def test_ela_me_contou_com_palavras_no_meio(self):
        self.assertCerteza("Hoje ela me contou que está preocupada.", Certeza.CONFIRMADO)
        self.assertCerteza("Ela comentou hoje que a mãe vai operar.", Certeza.CONFIRMADO)

    def test_outros_verbos_de_fala(self):
        for frase in ("Ela mencionou que gosta de jazz.", "Ela explicou que não come carne.",
                      "Ela confirmou que vai.", "Ela avisou que chega tarde.", "Ela respondeu que sim.",
                      "Ela garantiu que está bem.", "Me disse que ama sushi."):
            with self.subTest(frase=frase):
                self.assertCerteza(frase, Certeza.CONFIRMADO)

    def test_segundo_ela_e_nas_palavras_dela(self):
        self.assertCerteza("Segundo ela, o chefe é difícil.", Certeza.CONFIRMADO)
        self.assertCerteza("Nas palavras dela, foi o melhor dia.", Certeza.CONFIRMADO)

    def test_citacao_atribuida(self):
        self.assertCerteza('Ela: "odeio segunda-feira".', Certeza.CONFIRMADO)
        self.assertCerteza('"Eu amo girassóis", disse ela.', Certeza.CONFIRMADO)

    def test_talvez(self):
        self.assertCerteza("Talvez flores tenham significado especial para ela.", Certeza.INFERENCIA)

    def test_marcas_de_inferencia(self):
        for frase in ("Acho que ela gosta de girassóis.", "Acredito que ela prefira praia.",
                      "Provavelmente ela está cansada.", "Pode ser que ela goste.", "Ela deve gostar de jazz.",
                      "Imagino que foi difícil.", "Suponho que sim.", "Parece que ela ficou chateada.",
                      "Será que ela gosta de mim?", "Tenho a impressão de que ela está distante."):
            with self.subTest(frase=frase):
                self.assertCerteza(frase, Certeza.INFERENCIA)

    def test_inferencia_vence_confirmado(self):
        self.assertCerteza("Acho que ela disse que gosta de sushi.", Certeza.INFERENCIA)

    def test_observado(self):
        self.assertCerteza("Ela ficou muito feliz quando ganhou tulipas.", Certeza.OBSERVADO)
        self.assertCerteza("Ela pediu sushi.", Certeza.OBSERVADO)
        self.assertCerteza("Percebi que ela estava cansada.", Certeza.OBSERVADO)

    def test_sem_acento_tambem(self):
        self.assertCerteza("ela disse q nao gosta", Certeza.CONFIRMADO)
        self.assertCerteza("sera que ela gosta", Certeza.INFERENCIA)

    def test_nunca_incerto(self):
        for frase in ("", "Faz tempo que ela não fala nisso.", "Ela mudou de ideia sobre sushi."):
            self.assertNotEqual(classificar_certeza(frase), Certeza.INCERTO)

    def test_deve_de_obrigacao_nao_e_inferencia(self):
        self.assertCerteza("Ela deve entregar o TCC dia 20.", Certeza.OBSERVADO)


# ====================================================================== itens

class TestItensExemplos(unittest.TestCase):
    """As frases do enunciado, uma a uma."""

    def setUp(self):
        self.apresentacao = itens(APRESENTACAO, [data(APRESENTACAO, "Hoje", REF),
                                                 data(APRESENTACAO, "dia 20", date(2026, 10, 20))])

    def test_apresentacao_evento(self):
        (ev,) = do_tipo(self.apresentacao, TipoItem.EVENTO)
        self.assertEqual(ev.resumo, "Apresentação no trabalho — 20/10/2026")
        self.assertEqual(ev.data_evento, date(2026, 10, 20))
        self.assertEqual(ev.data_expressao, "dia 20")
        self.assertEqual(ev.precisao, Precisao.DIA)
        self.assertEqual(ev.categorias[0], "Eventos futuros")
        self.assertIn("Trabalho", ev.categorias)
        self.assertEqual(ev.importancia, 3)  # vem junto com preocupação
        self.assertEqual(ev.certeza, Certeza.CONFIRMADO)
        self.assertIsNone(ev.pessoa)

    def test_apresentacao_estado(self):
        (est,) = do_tipo(self.apresentacao, TipoItem.ESTADO)
        self.assertEqual(est.alvo, "preocupada")
        self.assertEqual(est.polaridade, -1)
        self.assertEqual(est.data_evento, REF)
        self.assertEqual(est.categorias[0], "Preocupações")

    def test_apresentacao_preferencia_negativa_confirmada(self):
        (pref,) = do_tipo(self.apresentacao, TipoItem.PREFERENCIA)
        self.assertEqual(pref.alvo, "falar em publico")
        self.assertEqual(pref.polaridade, -1)
        self.assertEqual(pref.certeza, Certeza.CONFIRMADO)
        self.assertEqual(pref.resumo, "Não gosta de falar em publico")
        self.assertEqual(pref.importancia, 3)  # "odeia"

    def test_apresentacao_padrao_nervosa(self):
        (pad,) = do_tipo(self.apresentacao, TipoItem.PADRAO)
        self.assertIn("nervosa", pad.resumo)
        self.assertIn("apresenta", normalizar(pad.resumo))
        self.assertEqual(pad.certeza, Certeza.CONFIRMADO)
        self.assertEqual(pad.trecho, "Ela disse que odeia falar em publico e que normalmente fica muito nervosa.")

    def test_tulipas_observado(self):
        (pref,) = itens("Ela ficou muito feliz quando ganhou tulipas.")
        self.assertEqual((pref.tipo, pref.alvo, pref.polaridade), (TipoItem.PREFERENCIA, "tulipas", 1))
        self.assertEqual(pref.certeza, Certeza.OBSERVADO)
        self.assertEqual(pref.categorias[0], "Coisas que deixam feliz")

    def test_reacao_nunca_confirmada(self):
        (pref,) = itens("Ela disse que ficou feliz quando ganhou flores.")
        self.assertEqual(pref.certeza, Certeza.OBSERVADO)

    def test_flores_inferencia(self):
        lista = itens("Talvez flores tenham significado especial para ela.")
        self.assertTrue(lista)
        self.assertTrue(all(i.certeza == Certeza.INFERENCIA for i in lista))
        self.assertEqual(lista[0].alvo, "flores")

    def test_mae_cirurgia(self):
        lista = itens(MAE_CIRURGIA, [data(MAE_CIRURGIA, "hoje", REF),
                                     data(MAE_CIRURGIA, "em novembro", date(2026, 11, 1), precisao=Precisao.MES)])
        (ev,) = do_tipo(lista, TipoItem.EVENTO)
        self.assertEqual(ev.pessoa, "mãe")
        self.assertEqual(ev.resumo, "Mãe: cirurgia em novembro/2026")
        self.assertEqual((ev.data_evento, ev.precisao), (date(2026, 11, 1), Precisao.MES))
        for cat in ("Família", "Saúde", "Eventos futuros"):
            self.assertIn(cat, ev.categorias)
        self.assertEqual(ev.importancia, 3)
        (est,) = do_tipo(lista, TipoItem.ESTADO)
        self.assertIsNone(est.pessoa)  # quem está preocupada é ela
        self.assertEqual(est.alvo, "preocupada")

    def test_apoio_mais_e_menos(self):
        lista = itens("Ela disse que prefere que eu apenas fique perto dela e nao tente resolver o problema.")
        apoios = do_tipo(lista, TipoItem.APOIO)
        self.assertEqual(len(lista), 2)
        mais = next(i for i in apoios if i.polaridade == 1)
        menos = next(i for i in apoios if i.polaridade == -1)
        self.assertEqual(mais.alvo, "que eu fique perto")
        self.assertEqual(menos.alvo, "que eu tente resolver o problema")
        self.assertEqual(mais.certeza, Certeza.CONFIRMADO)

    def test_alergia_camarao(self):
        (r,) = itens("Ela e alergica a camarao.")
        self.assertEqual((r.tipo, r.alvo, r.importancia, r.polaridade), (TipoItem.RESTRICAO, "camarao", 3, -1))
        self.assertEqual(r.categorias[:2], ["Saúde", "Comidas"])

    def test_aniversario_mariana(self):
        (d,) = itens(MARIANA, [data(MARIANA, "dia 12/05", date(2027, 5, 12), recorrente=True)])
        self.assertEqual(d.tipo, TipoItem.DATA_IMPORTANTE)
        self.assertTrue(d.recorrente)
        self.assertEqual(d.pessoa, "Mariana")
        self.assertEqual(d.data_evento, date(2027, 5, 12))
        self.assertEqual(d.resumo, "Aniversário de Mariana — 12/05")
        self.assertEqual(d.categorias[:2], ["Aniversários", "Datas importantes"])
        self.assertEqual(d.importancia, 3)

    def test_pendencia_entrevista(self):
        (p,) = itens("Ela esta esperando o resultado da entrevista.")
        self.assertEqual(p.tipo, TipoItem.PENDENCIA)
        self.assertEqual(p.lembrar_em, date(2026, 10, 3))
        self.assertEqual(p.categorias[0], "Situações pendentes")
        self.assertEqual(p.resumo, "Esperando o resultado da entrevista")

    def test_sushi_e_comida_japonesa(self):
        lista = itens("Ela gosta de sushi e de comida japonesa.")
        self.assertEqual([(i.tipo, i.alvo, i.polaridade) for i in lista],
                         [(TipoItem.PREFERENCIA, "sushi", 1), (TipoItem.PREFERENCIA, "comida japonesa", 1)])
        self.assertEqual(lista[0].resumo, "Gosta de sushi")
        self.assertIn("Comidas", lista[1].categorias)

    def test_minha_mae_nao_e_dela(self):
        (nota,) = itens("Minha mae ligou.")
        self.assertEqual(nota.tipo, TipoItem.NOTA)
        self.assertEqual(nota.pessoa, "mãe (sua)")


class TestItensPadroes(unittest.TestCase):
    """Cada linha da tabela da seção 7."""

    def test_nao_gosta_de(self):
        (p,) = itens("Ela não gosta de filme de terror.")
        self.assertEqual((p.alvo, p.polaridade, p.resumo), ("filme de terror", -1, "Não gosta de filme de terror"))

    def test_detesta_nao_suporta(self):
        for frase, alvo in (("Ela detesta coentro.", "coentro"), ("Ela não suporta barulho.", "barulho"),
                            ("Ela tem nojo de barata.", "barata")):
            with self.subTest(frase=frase):
                (p,) = itens(frase)
                self.assertEqual((p.tipo, p.alvo, p.polaridade), (TipoItem.PREFERENCIA, alvo, -1))

    def test_adora_e_fa_de(self):
        for frase, alvo in (("Ela adora jazz.", "jazz"), ("Ela é fã de Taylor Swift.", "Taylor Swift"),
                            ("Ela é apaixonada por girassóis.", "girassóis"), ("Ela curte vinho.", "vinho")):
            with self.subTest(frase=frase):
                (p,) = itens(frase)
                self.assertEqual((p.tipo, p.alvo, p.polaridade), (TipoItem.PREFERENCIA, alvo, 1))

    def test_lista_com_virgulas_e_mas(self):
        lista = itens("Ela adora tulipas, girassóis e chocolate, mas odeia rosas.")
        positivos = [i.alvo for i in lista if i.polaridade == 1]
        negativos = [i.alvo for i in lista if i.polaridade == -1]
        self.assertEqual(positivos, ["tulipas", "girassóis", "chocolate"])
        self.assertEqual(negativos, ["rosas"])
        self.assertNotIn("Comidas", lista[0].categorias)  # as categorias de chocolate não vazam

    def test_prefere_x_a_y(self):
        (p,) = itens("Ela prefere praia a campo.")
        self.assertEqual((p.alvo, p.polaridade), ("praia", 1))

    def test_acho_que_gosta_e_inferencia(self):
        (p,) = itens("Acho que ela gosta de girassóis.")
        self.assertEqual((p.tipo, p.certeza), (TipoItem.PREFERENCIA, Certeza.INFERENCIA))

    def test_restricoes_variadas(self):
        casos = [("Ela é vegetariana.", "carne"), ("Ela não come carne vermelha.", "carne vermelha"),
                 ("Ela é intolerante a lactose.", "lactose"), ("Ela não pode comer glúten.", "glúten")]
        for frase, alvo in casos:
            with self.subTest(frase=frase):
                (r,) = itens(frase)
                self.assertEqual((r.tipo, r.alvo, r.importancia), (TipoItem.RESTRICAO, alvo, 3))
                self.assertIn("Comidas", r.categorias)

    def test_alergia_a_remedio_nao_e_comida(self):
        lista = itens("Ela tem alergia a amendoim e a dipirona.")
        self.assertEqual([i.alvo for i in lista], ["amendoim", "dipirona"])
        self.assertIn("Comidas", lista[0].categorias)
        self.assertNotIn("Comidas", lista[1].categorias)
        self.assertIn("Saúde", lista[1].categorias)

    def test_reacao_negativa(self):
        (p,) = itens("Ela ficou chateada quando eu esqueci de ligar.")
        self.assertEqual((p.tipo, p.polaridade, p.certeza), (TipoItem.PREFERENCIA, -1, Certeza.OBSERVADO))
        self.assertEqual(p.categorias[0], "Coisas que incomodam")

    def test_amou_o_presente(self):
        (p,) = itens("Ela amou o presente.")
        self.assertEqual((p.alvo, p.polaridade, p.certeza), ("presente", 1, Certeza.OBSERVADO))

    def test_evento_prova_de_materia(self):
        texto = "Ela tem prova de matemática na sexta."
        (ev,) = itens(texto, [data(texto, "na sexta", date(2026, 10, 2))])
        self.assertEqual(ev.resumo, "Prova de matemática — 02/10/2026")
        self.assertIn("Estudos", ev.categorias)
        self.assertEqual(ev.importancia, 2)

    def test_viagem_com_destino(self):
        texto = "Ela vai viajar para Salvador em dezembro."
        (ev,) = itens(texto, [data(texto, "em dezembro", date(2026, 12, 1), precisao=Precisao.MES)])
        self.assertEqual(ev.resumo, "Viagem para Salvador em dezembro/2026")
        self.assertIn("Viagens", ev.categorias)

    def test_evento_passado_e_memoria(self):
        texto = "Ontem fomos ao cinema."
        (ev,) = itens(texto, [data(texto, "Ontem", date(2026, 9, 29), passado=True)])
        self.assertEqual(ev.tipo, TipoItem.EVENTO)
        self.assertEqual(ev.categorias[0], "Memórias compartilhadas")
        self.assertNotIn("Eventos futuros", ev.categorias)
        self.assertEqual(ev.data_evento, date(2026, 9, 29))

    def test_evento_passado_dela_e_experiencia(self):
        texto = "Ela fez a entrevista ontem."
        (ev,) = itens(texto, [data(texto, "ontem", date(2026, 9, 29), passado=True)])
        self.assertEqual(ev.categorias[0], "Experiências importantes")

    def test_data_ambigua_usa_o_palpite(self):
        texto = "Ela tem entrevista sexta que vem."
        d = data(texto, "sexta que vem", date(2026, 10, 2), ambigua=True,
                 alternativas=[date(2026, 10, 2), date(2026, 10, 9)])
        (ev,) = itens(texto, [d])
        self.assertEqual((ev.data_evento, ev.data_expressao), (date(2026, 10, 2), "sexta que vem"))

    def test_hoje_do_relato_nao_vira_data_do_evento(self):
        texto = "Hoje ela contou que tem consulta amanhã."
        lista = itens(texto, [data(texto, "Hoje", REF), data(texto, "amanhã", date(2026, 10, 1))])
        (ev,) = do_tipo(lista, TipoItem.EVENTO)
        self.assertEqual(ev.data_evento, date(2026, 10, 1))
        self.assertEqual(ev.importancia, 3)  # saúde

    def test_evento_sem_palavra_chave_com_data_futura(self):
        texto = "Amanhã ela vai na casa da avó."
        lista = itens(texto, [data(texto, "Amanhã", date(2026, 10, 1))])
        (ev,) = lista
        self.assertEqual(ev.tipo, TipoItem.EVENTO)
        self.assertIsNone(ev.pessoa)
        self.assertEqual(ev.data_evento, date(2026, 10, 1))

    def test_estado_positivo_com_causa(self):
        (e,) = itens("Ela está animada com a viagem.")
        self.assertEqual((e.tipo, e.polaridade), (TipoItem.ESTADO, 1))
        self.assertEqual(e.resumo, "Está animada com a viagem")
        self.assertEqual(e.categorias[0], "Coisas que deixam feliz")

    def test_estado_anda_cansada(self):
        (e,) = itens("Ela anda muito cansada com o trabalho.")
        self.assertEqual((e.alvo, e.polaridade), ("cansada", -1))
        self.assertIn("Trabalho", e.categorias)

    def test_estado_negado_nao_vira_estado(self):
        (n,) = itens("Ela não está preocupada.")
        self.assertEqual(n.tipo, TipoItem.NOTA)

    def test_estado_de_ontem_usa_a_data(self):
        texto = "Ontem ela estava triste."
        (e,) = itens(texto, [data(texto, "Ontem", date(2026, 9, 29), passado=True)])
        self.assertEqual((e.tipo, e.data_evento), (TipoItem.ESTADO, date(2026, 9, 29)))

    def test_padrao_com_contexto(self):
        (p,) = itens("Ela fica ansiosa quando tem prova.")
        self.assertEqual((p.tipo, p.resumo), (TipoItem.PADRAO, "Fica ansiosa quando tem prova"))

    def test_padrao_medo_duas_coisas(self):
        lista = itens("Ela tem medo de barata e de altura.")
        self.assertEqual([(i.tipo, i.alvo) for i in lista], [(TipoItem.PADRAO, "barata"), (TipoItem.PADRAO, "altura")])
        self.assertTrue(all(i.categorias[0] == "Medos" for i in lista))

    def test_padrao_costuma(self):
        (p,) = itens("Ela costuma acordar cedo aos sábados.")
        self.assertEqual((p.tipo, p.resumo), (TipoItem.PADRAO, "Costuma acordar cedo aos sábados"))

    def test_apoio_precisa_de_espaco(self):
        (a,) = itens("Ela precisa de espaço quando está estressada.")
        self.assertEqual((a.tipo, a.polaridade), (TipoItem.APOIO, 1))
        self.assertTrue(a.resumo.startswith("Precisa de espaço"))

    def test_apoio_negativo(self):
        for frase in ("Ela odeia quando eu chego atrasado.", "Ela não gosta que eu tente resolver tudo."):
            with self.subTest(frase=frase):
                (a,) = itens(frase)
                self.assertEqual((a.tipo, a.polaridade), (TipoItem.APOIO, -1))

    def test_apoio_o_que_ajuda(self):
        (a,) = itens("O que mais ajuda é um abraço.")
        self.assertEqual((a.tipo, a.polaridade, a.alvo), (TipoItem.APOIO, 1, "um abraço"))

    def test_pendencia_com_data(self):
        texto = "Ela vai saber o resultado da prova dia 15."
        lista = itens(texto, [data(texto, "dia 15", date(2026, 10, 15))])
        (p,) = do_tipo(lista, TipoItem.PENDENCIA)
        self.assertEqual(p.lembrar_em, date(2026, 10, 16))
        self.assertEqual(p.data_evento, date(2026, 10, 15))
        # A prova já foi; dia 15 é o resultado. A pendência leva a data: nada de evento "Prova — 15/10".
        self.assertEqual(do_tipo(lista, TipoItem.EVENTO), [])

    def test_pendencia_ainda_nao_sabe(self):
        (p,) = itens("Ela ainda não sabe se vai aceitar a proposta.")
        self.assertEqual(p.tipo, TipoItem.PENDENCIA)
        self.assertEqual(p.resumo, "Ainda não sabe se vai aceitar a proposta")

    def test_aniversario_dela(self):
        texto = "O aniversário dela é 12/06."
        (d,) = itens(texto, [data(texto, "12/06", date(2027, 6, 12), recorrente=True)])
        self.assertEqual((d.tipo, d.pessoa, d.resumo), (TipoItem.DATA_IMPORTANTE, None, "Aniversário dela — 12/06"))

    def test_aniversario_da_mae_dela(self):
        texto = "O aniversário da mãe dela é dia 3 de março."
        (d,) = itens(texto, [data(texto, "dia 3 de março", date(2027, 3, 3), recorrente=True)])
        self.assertEqual((d.pessoa, d.resumo), ("mãe", "Aniversário da mãe — 03/03"))
        self.assertIn("Família", d.categorias)

    def test_desejo_presente(self):
        (d,) = itens("Ela queria um fone novo.")
        self.assertEqual((d.tipo, d.resumo, d.alvo), (TipoItem.DESEJO, "Queria um fone novo", "fone novo"))
        self.assertEqual(d.categorias[0], "Presentes")

    def test_desejo_sonho(self):
        (d,) = itens("Ela sonha em morar na Itália.")
        self.assertEqual((d.tipo, d.categorias[0]), (TipoItem.DESEJO, "Sonhos"))

    def test_desejo_objetivo(self):
        (d,) = itens("Ela pretende terminar a faculdade.")
        self.assertEqual((d.categorias[0], d.alvo), ("Objetivos", "terminar a faculdade"))

    def test_pessoa_info_mora(self):
        (i,) = itens("A irmã dela mora em Curitiba.")
        self.assertEqual((i.tipo, i.pessoa, i.resumo), (TipoItem.PESSOA_INFO, "irmã", "Irmã: mora em Curitiba"))

    def test_pessoa_info_melhor_amiga(self):
        (i,) = itens("A Júlia é a melhor amiga dela.")
        self.assertEqual((i.tipo, i.pessoa), (TipoItem.PESSOA_INFO, "Júlia"))
        self.assertIn("Pessoas importantes", i.categorias)

    def test_pessoa_info_conheceram(self):
        (i,) = itens("Ela e a Carol se conheceram na faculdade.")
        self.assertEqual((i.tipo, i.pessoa), (TipoItem.PESSOA_INFO, "Carol"))

    def test_preferencia_de_outra_pessoa(self):
        (p,) = itens("A mãe dela adora cozinhar.")
        self.assertEqual((p.pessoa, p.alvo, p.resumo), ("mãe", "cozinhar", "Mãe: gosta de cozinhar"))

    def test_ele_e_o_homem_da_frase_anterior(self):
        texto = "O pai dela está doente. Ele vai operar dia 10."
        lista = itens(texto, [data(texto, "dia 10", date(2026, 10, 10))])
        (ev,) = do_tipo(lista, TipoItem.EVENTO)
        self.assertEqual((ev.pessoa, ev.resumo), ("pai", "Pai: cirurgia — 10/10/2026"))
        (est,) = do_tipo(lista, TipoItem.ESTADO)
        self.assertEqual(est.pessoa, "pai")

    def test_gosta_e_odeia_sem_virgula(self):
        lista = itens("Ela gosta de sushi e odeia pizza.")
        self.assertEqual([(i.alvo, i.polaridade) for i in lista], [("sushi", 1), ("pizza", -1)])

    def test_duas_restricoes_na_mesma_frase(self):
        lista = itens("Ela é alérgica a camarão e não come carne.")
        self.assertEqual([(i.tipo, i.alvo) for i in lista],
                         [(TipoItem.RESTRICAO, "camarão"), (TipoItem.RESTRICAO, "carne")])

    def test_padrao_toda_vez_que(self):
        (p,) = itens("Toda vez que chove ela fica triste.")
        self.assertEqual((p.tipo, p.resumo, p.alvo), (TipoItem.PADRAO, "Toda vez que chove ela fica triste", "chove"))

    def test_reacao_sem_alvo_vira_estado(self):
        (e,) = itens("O ex dela ligou de novo e ela ficou irritada com isso.")
        self.assertEqual((e.tipo, e.pessoa, e.resumo), (TipoItem.ESTADO, None, "Ficou irritada com isso"))

    def test_pessoa_id_do_cadastro(self):
        conhecidas = [{"id": 9, "nome": None, "relacao": "mãe", "apelidos": []}]
        (p,) = itens("A mãe dela adora cozinhar.", conhecidas=conhecidas)
        self.assertEqual(p.pessoa_id, 9)


class TestItensGarantias(unittest.TestCase):
    """O que vale para qualquer texto: nada se perde, nada se inventa."""

    TEXTOS = [
        APRESENTACAO, MAE_CIRURGIA, MARIANA, "Minha mae ligou.",
        "Ela gosta de sushi e de comida japonesa. Ontem choveu muito! Ela riu",
        "Ela disse: \"odeio segunda-feira\". A Dra. Ana atendeu bem.\nEla quer um gato.",
        "  Ela  ficou   feliz quando ganhou flores ;  depois dormiu.  ",
    ]

    def test_trecho_e_substring_literal(self):
        for texto in self.TEXTOS:
            for item in itens(texto):
                self.assertIn(item.trecho, texto)
                self.assertEqual(item.trecho, item.trecho.strip())

    def test_toda_frase_gera_ao_menos_um_item(self):
        from entrenos.texto import frases
        for texto in self.TEXTOS:
            trechos = {i.trecho for i in itens(texto)}
            for _, _, frase in frases(texto):
                self.assertIn(frase, trechos, frase)

    def test_frase_sem_estrutura_vira_nota(self):
        (n,) = itens("Ontem choveu muito.")
        self.assertEqual((n.tipo, n.importancia, n.resumo), (TipoItem.NOTA, 1, "Ontem choveu muito"))

    def test_categorias_validas_e_sem_repeticao(self):
        for texto in self.TEXTOS:
            for item in itens(texto):
                self.assertTrue(set(item.categorias) <= set(CATEGORIAS))
                self.assertEqual(len(item.categorias), len(set(item.categorias)))
                self.assertLessEqual(len(item.categorias), 5)

    def test_alvo_sai_do_texto(self):
        texto = "Ela adora tulipas e odeia pimenta. Ela tem medo de altura."
        for item in itens(texto):
            if item.alvo:
                self.assertIn(normalizar(item.alvo), normalizar(texto))

    def test_certeza_e_por_frase(self):
        lista = itens("Ela disse que ama sushi. Ela pediu pizza.")
        self.assertEqual(lista[0].certeza, Certeza.CONFIRMADO)
        self.assertEqual(lista[1].certeza, Certeza.OBSERVADO)

    def test_muito_importante(self):
        (n,) = itens("Isso é muito importante para ela.")
        self.assertEqual(n.importancia, 3)

    def test_nunca_sobe_importancia(self):
        (n,) = itens("Ela nunca foi à praia.")
        self.assertEqual(n.importancia, 3)

    def test_odeia_so_sobe_o_negativo(self):
        lista = itens("Ela adora tulipas, mas odeia rosas.")
        self.assertEqual({i.alvo: i.importancia for i in lista}, {"tulipas": 2, "rosas": 3})

    def test_texto_vazio(self):
        self.assertEqual(extrair_itens("", REF, [], []), [])
        self.assertEqual(extrair_itens("   \n ", REF, [], []), [])

    def test_nao_altera_as_entradas(self):
        texto = MARIANA
        d = data(texto, "dia 12/05", date(2027, 5, 12), recorrente=True)
        pessoas = extrair_pessoas(texto, [])
        antes = (repr(d), repr(pessoas))
        extrair_itens(texto, REF, pessoas, [d])
        self.assertEqual((repr(d), repr(pessoas)), antes)

    def test_varias_frases_no_mesmo_registro(self):
        texto = APRESENTACAO + " " + MAE_CIRURGIA
        lista = itens(texto, [data(texto, "dia 20", date(2026, 10, 20)),
                              data(texto, "em novembro", date(2026, 11, 1), precisao=Precisao.MES)])
        eventos = do_tipo(lista, TipoItem.EVENTO)
        self.assertEqual([e.data_evento for e in eventos], [date(2026, 10, 20), date(2026, 11, 1)])
        self.assertEqual([e.pessoa for e in eventos], [None, "mãe"])


# ====================================================================== revisão de português
# Frases que o revisor de português mostrou dando errado. Passam pelo motor inteiro (datas,
# pessoas e itens), com a namorada chamada Julia.

def analisar(texto: str, nome_dela: str | None = "Julia", conhecidas=()):
    return MotorRegras().analisar(texto, REF, Conhecimento(pessoas=list(conhecidas), nome_dela=nome_dela))


def prefs(proposta):
    return [(i.alvo, i.polaridade) for i in proposta.itens if i.tipo == TipoItem.PREFERENCIA]


class TestRevisaoCerteza(unittest.TestCase):
    def test_fala_negada_nao_confirma_nem_vira_item(self):
        for frase in ("ela nao disse que odeia sushi, só que nao tava com vontade ontem",
                      "Ela não disse que gosta de flores, fui eu que achei",
                      "Ela nunca disse que odeia rosas"):
            with self.subTest(frase=frase):
                p = analisar(frase)
                self.assertEqual(prefs(p), [])
                self.assertNotEqual(classificar_certeza(frase), Certeza.CONFIRMADO)
                self.assertEqual([i.tipo for i in p.itens], [TipoItem.NOTA])
                self.assertEqual(p.itens[0].importancia, 1)      # "odeia" negado não é importante

    def test_fala_de_outra_pessoa_e_observado(self):
        for frase, alvo in (("A Mariana me contou que ela odeia pepino", "pepino"),
                            ("a mãe dela me disse que ela é alérgica a dipirona", "dipirona")):
            with self.subTest(frase=frase):
                self.assertEqual(classificar_certeza(frase), Certeza.OBSERVADO)
                item = next(i for i in analisar(frase).itens if i.alvo == alvo)
                self.assertEqual(item.certeza, Certeza.OBSERVADO)

    def test_me_disse_sem_sujeito_continua_confirmado(self):
        for frase in ("Me disse que ama sushi.", "Hoje me contou que está cansada.", "No jantar me disse que odeia jiló."):
            with self.subTest(frase=frase):
                self.assertEqual(classificar_certeza(frase), Certeza.CONFIRMADO)

    def test_nao_me_disse_mas_eu_sei(self):
        p = analisar("Ela não me disse, mas eu sei que ela ama girassol")
        (item,) = p.itens
        self.assertEqual((item.alvo, item.polaridade), ("girassol", 1))
        self.assertNotEqual(item.certeza, Certeza.CONFIRMADO)

    def test_confirmado_vale_so_para_o_que_ela_disse(self):
        p = analisar("Ela disse que ta cansada e eu percebi que ela odeia o chefe")
        estado = next(i for i in p.itens if i.tipo == TipoItem.ESTADO)
        chefe = next(i for i in p.itens if i.tipo == TipoItem.PREFERENCIA)
        self.assertEqual(estado.certeza, Certeza.CONFIRMADO)
        self.assertEqual(chefe.certeza, Certeza.OBSERVADO)
        p = analisar("Ela disse que nunca gostou de sushi, mas hoje pediu")
        self.assertEqual([(i.alvo, i.polaridade, i.certeza) for i in p.itens], [("sushi", -1, Certeza.CONFIRMADO)])

    def test_trechos(self):
        frase = "Ela disse que ta cansada e eu percebi que ela odeia o chefe"
        ((a, b),) = trechos_confirmados(frase)
        self.assertIn("ta cansada", frase[a:b])
        self.assertNotIn("chefe", frase[a:b])
        self.assertEqual(trechos_confirmados("A Mariana me contou que ela odeia pepino"), [])
        negada = "Ela nunca disse que odeia rosas"
        ((a, b),) = trechos_nao_afirmados(negada)
        self.assertIn("odeia rosas", negada[a:b])

    def test_duvida_e_inferencia_sem_afirmar(self):
        for frase in ("nao sei se ela gosta de gato ou cachorro", "Não lembro se ela gosta de gato.",
                      "sei lá, ela gosta de gato"):
            with self.subTest(frase=frase):
                self.assertEqual(classificar_certeza(frase), Certeza.INFERENCIA)
        p = analisar("nao sei se ela gosta de gato ou cachorro")
        self.assertEqual(prefs(p), [])
        self.assertEqual(p.itens[0].certeza, Certeza.INFERENCIA)


class TestRevisaoPolaridade(unittest.TestCase):
    def test_n_abreviado_nega(self):
        self.assertEqual(prefs(analisar("ela n gosta de azeitona e odeia uva passa no arroz")),
                         [("azeitona", -1), ("uva passa no arroz", -1)])
        self.assertEqual(prefs(analisar("ela n curte funk")), [("funk", -1)])

    def test_nao_odeia_nao_e_desgosto(self):
        p = analisar("ela nao odeia sushi, só enjoou um pouco")
        self.assertEqual(prefs(p), [])

    def test_ironia(self):
        p = analisar("Ela adora o trabalho novo, só que não")
        self.assertEqual([i.tipo for i in p.itens], [TipoItem.NOTA])

    def test_nem_tanto(self):
        self.assertEqual(prefs(analisar("Ela adora a Mariana, a Camila nem tanto")), [("Mariana", 1)])

    def test_tudo_menos(self):
        self.assertEqual(prefs(analisar("ela gosta de tudo menos de peixe")), [("peixe", -1)])


class TestRevisaoAlvos(unittest.TestCase):
    def test_virgula_com_outra_oracao_nao_vira_alvo(self):
        casos = {
            "ela queria muito um kindle novo, o dela quebrou": ["kindle novo"],
            "ela ama gatos, o irmão dela é alérgico a gato": ["gatos"],
            "ela odeia barulho, a vizinha dela é insuportável": ["barulho"],
            "ela gosta de sorvete, o favorito é de morango": ["sorvete"],
            "ela ama cachorro, o Thor é o xodó dela": ["cachorro"],
        }
        for frase, alvos in casos.items():
            with self.subTest(frase=frase):
                p = analisar(frase)
                self.assertEqual([i.alvo for i in p.itens if i.tipo in (TipoItem.PREFERENCIA, TipoItem.DESEJO)],
                                 alvos)

    def test_lista_continua_valendo(self):
        self.assertEqual(prefs(analisar("Ela adora tulipas, girassóis e chocolate, mas odeia rosas.")),
                         [("tulipas", 1), ("girassóis", 1), ("chocolate", 1), ("rosas", -1)])

    def test_lixo_no_fim_do_alvo(self):
        self.assertEqual(prefs(analisar("a Julia ama pizza de calabresa kkkk")), [("pizza de calabresa", 1)])
        self.assertEqual(prefs(analisar("Ela é fã de Harry Potter desde criança")), [("Harry Potter", 1)])
        p = analisar("ela ama o cheiro de café mas nao bebe café de jeito nenhum")
        self.assertIn(("café", -1), [(i.alvo, i.polaridade) for i in p.itens if i.tipo == TipoItem.RESTRICAO])

    def test_nem_divide(self):
        p = analisar("Ela nao come glúten nem lactose")
        self.assertEqual([i.alvo for i in p.itens if i.tipo == TipoItem.RESTRICAO], ["glúten", "lactose"])

    def test_crase_de_locucao_nao_corta_o_prefere(self):
        self.assertEqual(prefs(analisar("Ela disse que prefere presente feito à mão do que coisa cara")),
                         [("presente feito à mão", 1)])
        self.assertEqual(prefs(analisar("Ela prefere praia à montanha.")), [("praia", 1)])

    def test_alvo_de_uma_palavra_vazia(self):
        self.assertEqual(prefs(analisar("minha mãe não gosta muito dela")), [])
        p = analisar("Eu acho que ela ficou meio chateada com o que eu falei")
        self.assertEqual(prefs(p), [])
        (estado,) = p.itens
        self.assertEqual((estado.tipo, estado.certeza), (TipoItem.ESTADO, Certeza.INFERENCIA))
        self.assertEqual(estado.resumo, "Ficou chateada com o que eu falei")


class TestRevisaoDono(unittest.TestCase):
    def test_virgula_com_outro_sujeito(self):
        p = analisar("Ela gosta de rosas, a mãe dela odeia rosas")
        self.assertEqual([(i.alvo, i.polaridade, i.pessoa) for i in p.itens], [("rosas", 1, None), ("rosas", -1, "mãe")])
        (alergia,) = [i for i in analisar("ela ama gatos, o irmão dela é alérgico a gato").itens
                      if i.tipo == TipoItem.RESTRICAO]
        self.assertEqual(alergia.pessoa, "irmão")

    def test_ela_objeto_nao_e_dona(self):
        (ev,) = [i for i in analisar("O Thiago, namorado da Mariana, vai pedir ela em casamento no natal").itens
                 if i.tipo == TipoItem.EVENTO]
        self.assertIsNotNone(ev.pessoa)
        self.assertFalse(ev.recorrente)        # casar no Natal não se repete todo ano

    def test_sujeito_composto_com_ela(self):
        (p,) = analisar("ela e a Mariana adoram karaokê").itens
        self.assertEqual((p.alvo, p.pessoa), ("karaokê", None))

    def test_defesa_e_exames_sao_dela(self):
        (ev,) = [i for i in analisar("Ela reclamou que a professora do TCC, a Dra. Helena, sumiu e a defesa é em "
                                     "15/11").itens if i.tipo == TipoItem.EVENTO]
        self.assertEqual((ev.pessoa, ev.data_evento), (None, date(2026, 11, 15)))
        (ev,) = [i for i in analisar("A médica dela pediu uns exames pra semana que vem").itens
                 if i.tipo == TipoItem.EVENTO]
        self.assertIsNone(ev.pessoa)

    def test_nome_no_inicio_e_dono(self):
        (ev,) = analisar("Camila vai casar em maio").itens
        self.assertEqual((ev.tipo, ev.pessoa), (TipoItem.EVENTO, "Camila"))
        self.assertNotIn("Sonhos", ev.categorias)


class TestRevisaoEstadosEventos(unittest.TestCase):
    def test_estado_hipotetico_nao_vira_estado(self):
        p = analisar("quando ela ta estressada ela prefere que eu so fique perto e nao tente resolver nada")
        self.assertEqual({i.tipo for i in p.itens}, {TipoItem.APOIO})

    def test_formas_coloquiais(self):
        casos = {
            "hj ela tava mt cansada": (TipoItem.ESTADO, "cansada", -1),
            "ela tava cansada": (TipoItem.ESTADO, "cansada", -1),
            "ela ta mto feliz": (TipoItem.ESTADO, "feliz", 1),
            "Ela nunca gostou de sertanejo": (TipoItem.PREFERENCIA, "sertanejo", -1),
            "ela ta amando o emprego novo": (TipoItem.PREFERENCIA, "emprego novo", 1),
            "ela começou a gostar de café": (TipoItem.PREFERENCIA, "café", 1),
            "Ela odeia quando o Uber cancela": (TipoItem.PREFERENCIA, "quando o Uber cancela", -1),
        }
        for frase, esperado in casos.items():
            with self.subTest(frase=frase):
                (item,) = analisar(frase).itens
                self.assertEqual((item.tipo, item.alvo, item.polaridade), esperado)

    def test_causa_do_estado(self):
        p = analisar("ela tava ansiosa ontem por causa da prova de amanhã")
        (estado,) = [i for i in p.itens if i.tipo == TipoItem.ESTADO]
        self.assertEqual((estado.resumo, estado.data_evento), ("Estava ansiosa por causa da prova", date(2026, 9, 29)))
        (estado,) = analisar("hj ela me disse q ta com medo de perder o emprego").itens
        self.assertEqual((estado.resumo, estado.certeza), ("Está com medo de perder o emprego", Certeza.CONFIRMADO))

    def test_reacao_a_algo_vago_e_estado(self):
        (item,) = analisar("ontem à noite ela ficou triste com a notícia").itens
        self.assertEqual((item.tipo, item.data_evento), (TipoItem.ESTADO, date(2026, 9, 29)))

    def test_evento_de_outra_acao_nao_pega_a_data(self):
        p = analisar("semana passada ela brigou com a Mariana por causa do casamento")
        self.assertEqual([i for i in p.itens if i.tipo == TipoItem.EVENTO], [])
        p = analisar("Ela tá esperando o resultado do exame de sangue, sai semana que vem")
        self.assertEqual([i.tipo for i in p.itens], [TipoItem.PENDENCIA])
        self.assertEqual(p.itens[0].data_evento, date(2026, 10, 5))

    def test_datas_de_conversa_viram_evento(self):
        for frase, data_evento in (("semana q vem ela viaja pra Salvador", date(2026, 10, 5)),
                                   ("mes q vem ela tem prova", date(2026, 10, 1)),
                                   ("no fim do mês ela tem uma reunião importante", date(2026, 9, 28))):
            with self.subTest(frase=frase):
                (ev,) = analisar(frase).itens
                self.assertEqual((ev.tipo, ev.data_evento), (TipoItem.EVENTO, data_evento))
                self.assertIn("Eventos futuros", ev.categorias)
        (ev,) = analisar("no fim do mês ela tem uma reunião importante").itens
        self.assertEqual(ev.importancia, 3)

    def test_lembranca_de_infancia(self):
        (item,) = analisar("Ela adorava ir no sítio do avô quando era criança").itens
        self.assertEqual((item.tipo, item.resumo), (TipoItem.FATO, "Adorava ir no sítio do avô"))
        self.assertEqual(item.categorias[0], "Infância")

    def test_categorias(self):
        (ev,) = [i for i in analisar("a melhor amiga dela, Mariana, vai casar em novembro").itens
                 if i.tipo == TipoItem.EVENTO]
        self.assertNotIn("Sonhos", ev.categorias)
        (d,) = analisar("Ela quer ganhar um perfume da Natura de aniversário").itens
        self.assertEqual(d.categorias[0], "Presentes")
        (p,) = analisar("Amou a costela").itens
        self.assertIn("Comidas", p.categorias)

    def test_nunca_esquecer_nao_e_importante(self):
        itens_ = analisar("Ela é alérgica a camarão!! nunca esquecer").itens
        self.assertEqual([(i.tipo, i.importancia) for i in itens_], [(TipoItem.RESTRICAO, 3), (TipoItem.NOTA, 1)])


class TestRevisaoPessoas(unittest.TestCase):
    def test_lugares_empresas_e_obras_nao_sao_pessoas(self):
        for frase in ("Ela passeou no Parque Taquaral", "Ela é de Piracicaba", "Ela mora no Maranhão",
                      "Ela voltou de Ribeirão ontem", "Ela tem entrevista na Natura amanhã", "Ela trabalha na Ambev",
                      "Ela é fã da Taylor Swift", "daqui a 2 semanas é o show do Coldplay que a gente vai junto",
                      "Ela assistiu Mindhunter inteiro", "Ela ama Harry Potter", "Ela curte Anitta",
                      "Ontem fomos ao Shopping Iguatemi.", "Fomos ao Parque Ibirapuera no domingo.",
                      "Ela assistiu Friends de novo.", "Ela me contou em segredo sobre o Projeto Mandragorix."):
            with self.subTest(frase=frase):
                self.assertEqual(extrair_pessoas(frase, []), [])

    def test_show_do_artista_nao_e_dele(self):
        (ev,) = analisar("daqui a 2 semanas é o show do Coldplay que a gente vai junto").itens
        self.assertEqual((ev.tipo, ev.pessoa), (TipoItem.EVENTO, None))

    def test_amiga_com_artigo_continua_pessoa(self):
        self.assertEqual([p.nome for p in extrair_pessoas("Ela adora a Mariana.", [])], ["Mariana"])
        self.assertEqual([p.nome for p in extrair_pessoas("Ela confia muito na Mariana.", [])], ["Mariana"])

    def test_relacao_virgula_nome(self):
        for frase, esperado in (("O pai dela, seu Roberto, trabalha como motorista de aplicativo", ("Roberto", "pai")),
                                ("o chefe dela, o Marcos, é um babaca", ("Marcos", "chefe")),
                                ("O irmão dela, o Pedro, mora em Curitiba", ("Pedro", "irmão"))):
            with self.subTest(frase=frase):
                self.assertEqual([(p.nome, p.relacao) for p in extrair_pessoas(frase, [])], [esperado])

    def test_apelido_dela_e_ela(self):
        for frase in ("A Ju ama tulipas amarelas mas não curte rosas vermelhas porque acha brega",
                      "a Julinha adora pão de queijo", "A Jú chegou."):
            with self.subTest(frase=frase):
                self.assertEqual(extrair_pessoas(frase, [], nome_dela="Julia"), [])
                self.assertTrue(all(i.pessoa is None for i in analisar(frase).itens))
        # Nome parecido, mas outra pessoa: "Maria" não é apelido de "Mariana".
        self.assertEqual([p.nome for p in extrair_pessoas("A Maria ligou.", [], nome_dela="Mariana")], ["Maria"])
        self.assertTrue(e_nome_dela("a Ju", "Julia"))
        self.assertFalse(e_nome_dela("Gabriel", "Gabriela"))

    def test_avo_e_avo_no_cadastro(self):
        cadastro = [{"id": 9, "nome": None, "relacao": "avó"}]
        self.assertIsNone(casar_pessoa("avô", cadastro))
        self.assertEqual(casar_pessoa("avó", cadastro), 9)
        self.assertEqual(casar_pessoa("a avo dela", cadastro), 9)      # sem acento: vale qualquer uma
        self.assertTrue(mesma_relacao("avó", "Avó"))
        self.assertFalse(mesma_relacao("avô", "avó"))


if __name__ == "__main__":
    unittest.main()
