"""Testes de entrenos.regras.datas (DESIGN.md, seção 3).

Referência fixa: quarta-feira, 30/09/2026. Nunca date.today().
"""
import unittest
from datetime import date

from entrenos.modelos import DataEncontrada, Precisao
from entrenos.regras.datas import extrair_datas, pascoa, proxima_ocorrencia

REF = date(2026, 9, 30)  # quarta-feira


def d(dia: int, mes: int, ano: int = 2026) -> date:
    return date(ano, mes, dia)


class BaseDatas(unittest.TestCase):
    def todas(self, texto: str, ref: date = REF) -> list[DataEncontrada]:
        achadas = extrair_datas(texto, ref)
        for a in achadas:
            self.assertEqual(texto[a.inicio:a.fim], a.expressao, texto)
        return achadas

    def unica(self, texto: str, ref: date = REF) -> DataEncontrada:
        achadas = self.todas(texto, ref)
        self.assertEqual(len(achadas), 1, f"{texto!r} -> {achadas}")
        return achadas[0]

    def confere(self, texto: str, data: date, precisao: Precisao = Precisao.DIA,
                ref: date = REF, expressao: str | None = None,
                passado: bool | None = None) -> DataEncontrada:
        a = self.unica(texto, ref)
        self.assertEqual(a.data, data, texto)
        self.assertEqual(a.precisao, precisao, texto)
        if expressao is not None:
            self.assertEqual(a.expressao, expressao)
        if passado is not None:
            self.assertEqual(a.passado, passado, texto)
        return a

    def nenhuma(self, texto: str, ref: date = REF) -> None:
        self.assertEqual(extrair_datas(texto, ref), [], texto)


class TestRelativasSimples(BaseDatas):
    def test_hoje(self):
        self.confere("Hoje ela acordou cedo", d(30, 9), expressao="Hoje", passado=False)

    def test_amanha(self):
        self.confere("ela tem prova amanhã", d(1, 10), expressao="amanhã", passado=False)

    def test_depois_de_amanha(self):
        self.confere("o voo é depois de amanhã", d(2, 10), expressao="depois de amanhã")

    def test_ontem(self):
        self.confere("ontem ela chorou", d(29, 9), passado=True)

    def test_anteontem(self):
        self.confere("anteontem jantamos fora", d(28, 9), passado=True)

    def test_antes_de_ontem(self):
        self.confere("antes de ontem ela ligou", d(28, 9), expressao="antes de ontem")


class TestDiaDoMes(BaseDatas):
    def test_dia_20_vai_para_o_mes_seguinte(self):
        self.confere("a apresentação é dia 20", d(20, 10), expressao="dia 20")

    def test_dia_30_e_hoje(self):
        self.confere("dia 30 ela tem consulta", d(30, 9), passado=False)

    def test_dia_ainda_por_vir_fica_neste_mes(self):
        self.confere("dia 20 ela viaja", d(20, 9), ref=d(15, 9))

    def test_dia_31_pula_mes_sem_31(self):
        self.confere("dia 31 tem festa", d(31, 10))

    def test_dia_por_extenso(self):
        self.confere("a prova é dia vinte e cinco", d(25, 10), expressao="dia vinte e cinco")

    def test_dia_primeiro(self):
        self.confere("começa dia primeiro", d(1, 10), expressao="dia primeiro")

    def test_dia_no_passado(self):
        self.confere("Foi dia 20 que ela começou no emprego novo", d(20, 9), passado=True)

    def test_dia_inexistente(self):
        self.nenhuma("dia 32 não existe")


class TestDiaMes(BaseDatas):
    def test_barra(self):
        self.confere("prova 20/10", d(20, 10), expressao="20/10")

    def test_barra_ano_curto(self):
        self.confere("prova 20/10/26", d(20, 10), expressao="20/10/26")

    def test_barra_ano_longo(self):
        self.confere("prova 20/10/2026", d(20, 10), expressao="20/10/2026")

    def test_hifen(self):
        self.confere("prova 20-10", d(20, 10), expressao="20-10")

    def test_ponto(self):
        self.confere("prova 20.10", d(20, 10), expressao="20.10")

    def test_com_palavra_dia(self):
        self.confere("no dia 20/10 ela viaja", d(20, 10), expressao="dia 20/10")

    def test_ano_de_dois_digitos_do_seculo_passado(self):
        self.confere("nasceu em 12/05/98", d(12, 5, 1998), passado=True)

    def test_ja_passou_vai_para_o_proximo_ano(self):
        self.confere("Ela tem consulta 15/09", d(15, 9, 2027), passado=False)

    def test_contexto_passado_fica_neste_ano(self):
        self.confere("Dia 12/09 fomos ao parque", d(12, 9), expressao="Dia 12/09", passado=True)

    def test_com_ontem_na_frase(self):
        achadas = self.todas("Ontem, 29/09, ela ficou em casa")
        self.assertEqual([a.data for a in achadas], [d(29, 9), d(29, 9)])
        self.assertTrue(all(a.passado for a in achadas))

    def test_iso(self):
        self.confere("marcado para 2026-10-20", d(20, 10), expressao="2026-10-20")

    def test_mes_barra_ano(self):
        self.confere("começou em 11/2025", d(1, 11, 2025), Precisao.MES, passado=True)


class TestDiaDeMes(BaseDatas):
    def test_20_de_outubro(self):
        self.confere("a festa é 20 de outubro", d(20, 10), expressao="20 de outubro")

    def test_20_de_outubro_de_2027(self):
        self.confere("casamento em 20 de outubro de 2027", d(20, 10, 2027),
                     expressao="20 de outubro de 2027")

    def test_1o_de_maio(self):
        self.confere("1º de maio é feriado", d(1, 5, 2027), expressao="1º de maio")

    def test_primeiro_de_maio_por_extenso(self):
        self.confere("primeiro de maio", d(1, 5, 2027))

    def test_dia_de_mes_por_extenso(self):
        self.confere("dia vinte e cinco de dezembro", d(25, 12), expressao="dia vinte e cinco de dezembro")

    def test_mes_abreviado(self):
        self.confere("20 de out. ela vai", d(20, 10), expressao="20 de out.")

    def test_dia_da_semana_junto_vira_uma_expressao(self):
        self.confere("terça, 20/10, tem reunião", d(20, 10), expressao="terça, 20/10")


class TestMeses(BaseDatas):
    def test_em_novembro(self):
        self.confere("cirurgia em novembro", d(1, 11), Precisao.MES, expressao="em novembro")

    def test_no_mes_de_novembro(self):
        self.confere("no mês de novembro", d(1, 11), Precisao.MES, expressao="no mês de novembro")

    def test_mes_ja_passado_vai_para_o_ano_seguinte(self):
        self.confere("ela vai viajar em março", d(1, 3, 2027), Precisao.MES)

    def test_mes_atual(self):
        self.confere("ainda em setembro", d(1, 9), Precisao.MES, passado=False)

    def test_novembro_de_2025(self):
        self.confere("novembro de 2025", d(1, 11, 2025), Precisao.MES, passado=True)

    def test_mes_passado_com_contexto(self):
        self.confere("em agosto fomos à praia", d(1, 8), Precisao.MES, passado=True)

    def test_mes_depois_de_preposicao(self):
        self.confere("fica até novembro", d(1, 11), Precisao.MES, expressao="novembro")


class TestDiasDaSemana(BaseDatas):
    def test_sexta(self):
        self.confere("sexta ela tem prova", d(2, 10), expressao="sexta")

    def test_sexta_feira(self):
        self.confere("sexta-feira", d(2, 10), expressao="sexta-feira")

    def test_na_sexta(self):
        self.confere("vamos sair na sexta", d(2, 10), expressao="sexta")

    def test_mesmo_dia_da_semana_e_na_semana_seguinte(self):
        self.confere("quarta tem jogo", d(7, 10))

    def test_sabado_e_domingo(self):
        self.confere("sábado", d(3, 10))
        self.confere("domingo", d(4, 10))

    def test_proxima_sexta_ambigua(self):
        a = self.confere("na próxima sexta", d(2, 10), expressao="próxima sexta")
        self.assertTrue(a.ambigua)
        self.assertEqual(a.alternativas, [d(2, 10), d(9, 10)])

    def test_sexta_que_vem_ambigua(self):
        a = self.confere("sexta que vem", d(2, 10))
        self.assertTrue(a.ambigua)
        self.assertEqual(a.alternativas, [d(2, 10), d(9, 10)])

    def test_sexta_passada(self):
        self.confere("sexta passada", d(25, 9), passado=True)

    def test_na_ultima_sexta(self):
        self.confere("na última sexta", d(25, 9), expressao="última sexta", passado=True)

    def test_sexta_da_semana_que_vem_nao_e_ambigua(self):
        a = self.confere("sexta da semana que vem", d(9, 10))
        self.assertFalse(a.ambigua)

    def test_contexto_passado(self):
        self.confere("Na sexta fomos ao cinema", d(25, 9), passado=True)

    def test_nao_ambigua_nao_tem_alternativas(self):
        a = self.confere("sexta", d(2, 10))
        self.assertFalse(a.ambigua)
        self.assertEqual(a.alternativas, [])


class TestSemanasMesesAnos(BaseDatas):
    def test_semana_que_vem(self):
        self.confere("semana que vem", d(5, 10), Precisao.SEMANA, passado=False)

    def test_proxima_semana(self):
        self.confere("na próxima semana", d(5, 10), Precisao.SEMANA, expressao="próxima semana")

    def test_semana_passada(self):
        self.confere("semana passada", d(21, 9), Precisao.SEMANA, passado=True)

    def test_esta_semana(self):
        self.confere("esta semana", d(28, 9), Precisao.SEMANA, passado=False)

    def test_mes_que_vem(self):
        self.confere("mês que vem", d(1, 10), Precisao.MES)

    def test_proximo_mes(self):
        self.confere("próximo mês", d(1, 10), Precisao.MES)

    def test_mes_passado(self):
        self.confere("mês passado", d(1, 8), Precisao.MES, passado=True)

    def test_ano_que_vem(self):
        self.confere("ano que vem", d(1, 1, 2027), Precisao.ANO)

    def test_proximo_ano(self):
        self.confere("próximo ano", d(1, 1, 2027), Precisao.ANO)

    def test_ano_passado(self):
        self.confere("ano passado", d(1, 1, 2025), Precisao.ANO, passado=True)

    def test_em_2019(self):
        self.confere("em 2019", d(1, 1, 2019), Precisao.ANO, expressao="em 2019", passado=True)


class TestDuracoes(BaseDatas):
    def test_daqui_a_3_dias(self):
        self.confere("daqui a 3 dias", d(3, 10))

    def test_em_3_dias(self):
        self.confere("em 3 dias", d(3, 10))

    def test_daqui_tres_dias(self):
        self.confere("daqui três dias", d(3, 10), expressao="daqui três dias")

    def test_daqui_a_2_semanas(self):
        self.confere("daqui a 2 semanas", d(14, 10))

    def test_em_duas_semanas(self):
        self.confere("em duas semanas", d(14, 10))

    def test_daqui_a_um_mes(self):
        self.confere("daqui a um mês", d(30, 10))

    def test_daqui_2_meses(self):
        self.confere("daqui 2 meses", d(30, 11))

    def test_ha_3_dias(self):
        self.confere("há 3 dias", d(27, 9), passado=True)

    def test_3_dias_atras(self):
        self.confere("3 dias atrás", d(27, 9), passado=True)

    def test_ha_uma_semana(self):
        self.confere("ha uma semana", d(23, 9))

    def test_faz_idade_nao_e_data(self):
        a = self.unica("ela faz 20 anos amanhã")
        self.assertEqual(a.expressao, "amanhã")


class TestFimDeSemana(BaseDatas):
    def test_fim_de_semana(self):
        self.confere("no fim de semana", d(3, 10), expressao="fim de semana")

    def test_final_de_semana(self):
        self.confere("final de semana", d(3, 10))

    def test_fim_de_semana_passado(self):
        self.confere("fim de semana passado", d(26, 9), passado=True)

    def test_fim_de_semana_que_vem_ambiguo(self):
        a = self.confere("fim de semana que vem", d(3, 10))
        self.assertEqual(a.alternativas, [d(3, 10), d(10, 10)])


class TestComemorativas(BaseDatas):
    def test_natal(self):
        a = self.confere("no natal", d(25, 12))
        self.assertTrue(a.recorrente)

    def test_vespera_de_natal(self):
        self.confere("véspera de natal", d(24, 12), expressao="véspera de natal")

    def test_ano_novo(self):
        a = self.confere("ano novo", d(1, 1, 2027))
        self.assertTrue(a.recorrente)

    def test_reveillon(self):
        self.confere("réveillon", d(31, 12))

    def test_dia_dos_namorados(self):
        a = self.confere("Dia dos Namorados", d(12, 6, 2027))
        self.assertTrue(a.recorrente)

    def test_dia_das_maes_2027(self):
        a = self.confere("dia das mães", d(9, 5, 2027))  # 2º domingo de maio de 2027
        self.assertTrue(a.recorrente)

    def test_dia_das_maes_ainda_este_ano(self):
        self.confere("dia das mães", d(10, 5, 2026), ref=d(1, 5, 2026))

    def test_dia_dos_pais(self):
        self.confere("dia dos pais", d(8, 8, 2027))  # 2º domingo de agosto de 2027

    def test_pascoa_2027(self):
        self.confere("na Páscoa", d(28, 3, 2027))

    def test_carnaval_2027(self):
        self.confere("carnaval", d(9, 2, 2027))  # terça-feira, 47 dias antes da Páscoa

    def test_natal_passado(self):
        self.confere("natal passado", d(25, 12, 2025), passado=True)

    def test_natal_de_ano_explicito(self):
        self.confere("natal de 2030", d(25, 12, 2030))


class TestAniversario(BaseDatas):
    def test_aniversario_dd_mm(self):
        a = self.confere("o aniversario dela e 12/05", d(12, 5, 2027), expressao="12/05")
        self.assertTrue(a.recorrente)
        self.assertFalse(a.passado)

    def test_aniversario_com_acento(self):
        a = self.confere("O aniversário dela é 12/05.", d(12, 5, 2027))
        self.assertTrue(a.recorrente)

    def test_faz_aniversario_dia_3_de_marco(self):
        a = self.confere("a mãe dela faz aniversário dia 3 de março", d(3, 3, 2027),
                         expressao="dia 3 de março")
        self.assertTrue(a.recorrente)

    def test_todo_ano(self):
        a = self.confere("todo ano ela viaja dia 15 de novembro", d(15, 11))
        self.assertTrue(a.recorrente)

    def test_data_comum_nao_e_recorrente(self):
        self.assertFalse(self.confere("prova dia 15 de novembro", d(15, 11)).recorrente)


class TestPosicoesESobreposicao(BaseDatas):
    def test_posicoes_exatas(self):
        texto = "Amanhã ela tem prova e dia 20/10 a viagem."
        achadas = self.todas(texto)
        self.assertEqual([(a.inicio, a.fim) for a in achadas], [(0, 6), (23, 32)])
        self.assertEqual([a.expressao for a in achadas], ["Amanhã", "dia 20/10"])

    def test_posicoes_com_acentos_e_texto_antes(self):
        texto = "Ela está preocupada: a cirurgia da mãe é no mês de novembro."
        a = self.unica(texto)
        self.assertEqual(a.inicio, texto.index("no mês"))
        self.assertEqual(a.fim, texto.index("."))

    def test_expressao_mais_longa_ganha(self):
        for texto, expressao in [("depois de amanhã", "depois de amanhã"),
                                 ("sexta que vem", "sexta que vem"),
                                 ("20 de outubro de 2027", "20 de outubro de 2027"),
                                 ("fim de semana que vem", "fim de semana que vem"),
                                 ("sexta-feira santa", "sexta-feira santa"),
                                 ("há 3 dias atrás", "há 3 dias atrás")]:
            with self.subTest(texto=texto):
                self.assertEqual(self.unica(texto).expressao, expressao)

    def test_nao_se_sobrepoem(self):
        texto = "sexta que vem, dia 20 de outubro, e depois de amanhã e fim de semana que vem"
        achadas = self.todas(texto)
        for anterior, seguinte in zip(achadas, achadas[1:]):
            self.assertLessEqual(anterior.fim, seguinte.inicio)
        self.assertEqual(len(achadas), 4)

    def test_varias_datas_no_mesmo_texto(self):
        texto = "Ontem ela contou que a prova é dia 20 e a viagem em novembro. Sábado tem festa."
        achadas = self.todas(texto)
        self.assertEqual([a.expressao for a in achadas], ["Ontem", "dia 20", "em novembro", "Sábado"])
        self.assertEqual([a.data for a in achadas], [d(29, 9), d(20, 10), d(1, 11), d(3, 10)])

    def test_devolve_dataencontrada(self):
        a = self.unica("amanhã")
        self.assertIsInstance(a, DataEncontrada)

    def test_texto_vazio(self):
        self.nenhuma("")
        self.nenhuma("   ")
        self.nenhuma("Ela gosta de tulipas.")


class TestMaiusculasEAcentos(BaseDatas):
    def test_variacoes(self):
        for texto, data in [("Amanhã", d(1, 10)), ("amanha", d(1, 10)), ("AMANHÃ", d(1, 10)),
                            ("SEXTA", d(2, 10)), ("Sexta-Feira", d(2, 10)),
                            ("Depois De Amanhã", d(2, 10)), ("PROXIMA SEXTA", d(2, 10)),
                            ("Terca", d(6, 10)), ("SÁBADO", d(3, 10)), ("mes que vem", d(1, 10)),
                            ("Março de 2027", d(1, 3, 2027)), ("reveillon", d(31, 12))]:
            with self.subTest(texto=texto):
                a = self.unica(texto)
                self.assertEqual(a.data, data)
                self.assertEqual(a.expressao, texto)


class TestNaoSaoDatas(BaseDatas):
    def test_tenho_20_anos(self):
        self.nenhuma("tenho 20 anos")

    def test_reais(self):
        self.nenhuma("custou R$ 20,50")
        self.nenhuma("custou R$ 20.10")

    def test_2_filhos(self):
        self.nenhuma("ela quer 2 filhos")

    def test_10h(self):
        self.nenhuma("a aula começa 10h")
        self.nenhuma("às 15:30")

    def test_nota_10(self):
        self.nenhuma("tirou nota 10")
        self.nenhuma("deu nota 8/10 pro filme")

    def test_3_vezes(self):
        self.nenhuma("ligou 3 vezes")

    def test_fracao_e_24_7(self):
        self.nenhuma("1/2 xícara de açúcar")
        self.nenhuma("trabalha 24/7")

    def test_hoje_em_dia(self):
        self.nenhuma("hoje em dia ela prefere chá")

    def test_repeticao(self):
        self.nenhuma("toma remédio de 3 em 3 dias")
        self.nenhuma("trabalha de segunda a sexta")
        self.nenhuma("toda sexta ela faz yoga")

    def test_segunda_ordinal(self):
        self.nenhuma("foi a segunda vez que ela chorou")
        self.nenhuma("é a quinta tentativa")

    def test_marco_nome(self):
        self.nenhuma("o Marco ligou")

    def test_natal_cidade_e_pre_natal(self):
        self.assertEqual(self.unica("Vamos para Natal em novembro").expressao, "em novembro")
        self.assertEqual(self.unica("consulta de pré-natal amanhã").expressao, "amanhã")


class TestDatasInvalidas(BaseDatas):
    def test_31_02_ignorado(self):
        self.nenhuma("31/02")
        self.nenhuma("dia 31/02")

    def test_30_de_fevereiro_ignorado(self):
        self.nenhuma("30 de fevereiro")

    def test_mes_13_ignorado(self):
        self.nenhuma("20/13")

    def test_invalida_nao_apaga_as_outras(self):
        a = self.unica("31/02 não existe, mas amanhã sim")
        self.assertEqual(a.expressao, "amanhã")


class TestViradaDeAno(BaseDatas):
    REF_DEZ = date(2026, 12, 20)

    def test_dia_5(self):
        self.confere("dia 5", d(5, 1, 2027), ref=self.REF_DEZ)

    def test_em_janeiro(self):
        self.confere("em janeiro", d(1, 1, 2027), Precisao.MES, ref=self.REF_DEZ)

    def test_proxima_semana(self):
        # 20/12/2026 é domingo: a semana seguinte começa na segunda, 21/12.
        self.confere("próxima semana", d(21, 12), Precisao.SEMANA, ref=self.REF_DEZ)

    def test_mes_que_vem(self):
        self.confere("mês que vem", d(1, 1, 2027), Precisao.MES, ref=self.REF_DEZ)

    def test_daqui_a_2_semanas(self):
        self.confere("daqui a 2 semanas", d(3, 1, 2027), ref=self.REF_DEZ)


class TestTempoVerbal(BaseDatas):
    def test_remarcada_e_futuro(self):
        self.confere("foi remarcada para dia 20", d(20, 10))

    def test_ficou_de_e_futuro(self):
        self.confere("ela ficou de responder até dia 25", d(25, 10))

    def test_passado_e_futuro_na_mesma_frase(self):
        achadas = self.todas("A prova foi dia 20 e a próxima é dia 27")
        self.assertEqual([a.data for a in achadas], [d(20, 9), d(27, 10)])

    def test_verbo_de_fala_nao_conta(self):
        a = self.unica("ela me contou que vai ao médico 15/09")
        self.assertEqual(a.data, d(15, 9, 2027))

    def test_ontem_com_dia(self):
        achadas = self.todas("Ontem, dia 29, fomos jantar")
        self.assertEqual([a.data for a in achadas], [d(29, 9), d(29, 9)])


class TestProximaOcorrencia(BaseDatas):
    def test_ainda_este_ano(self):
        self.assertEqual(proxima_ocorrencia(20, 10, REF), d(20, 10))

    def test_ja_passou(self):
        self.assertEqual(proxima_ocorrencia(12, 5, REF), d(12, 5, 2027))

    def test_hoje_conta(self):
        self.assertEqual(proxima_ocorrencia(30, 9, REF), REF)

    def test_29_de_fevereiro(self):
        self.assertEqual(proxima_ocorrencia(29, 2, REF), d(28, 2, 2027))
        self.assertEqual(proxima_ocorrencia(29, 2, d(1, 1, 2028)), d(29, 2, 2028))

    def test_invalida(self):
        with self.assertRaises(ValueError):
            proxima_ocorrencia(31, 4, REF)
        with self.assertRaises(ValueError):
            proxima_ocorrencia(1, 13, REF)


class TestAbreviacoesERevisao(BaseDatas):
    """Casos do revisor de português: abreviações de conversa e "<dia> passada" ambígua."""

    def test_hj(self):
        self.confere("hj ela foi ao médico", d(30, 9), expressao="hj")

    def test_que_abreviado(self):
        self.confere("semana q vem ela viaja pra Salvador", d(5, 10), Precisao.SEMANA, expressao="semana q vem")
        self.confere("mes q vem ela tem prova", d(1, 10), Precisao.MES, expressao="mes q vem")
        self.confere("ano q vem ela se forma", d(1, 1, 2027), Precisao.ANO)
        a = self.confere("sexta q vem ela tem entrevista", d(2, 10), expressao="sexta q vem")
        self.assertTrue(a.ambigua)
        self.assertEqual(a.alternativas, [d(2, 10), d(9, 10)])

    def test_fds(self):
        self.confere("fds ela vai pra casa da mãe dela", d(3, 10), expressao="fds")

    def test_fim_do_mes(self):
        a = self.confere("no fim do mês ela tem uma reunião importante", d(28, 9), Precisao.SEMANA,
                         expressao="no fim do mês")
        self.assertFalse(a.passado)          # a semana de 28/09 ainda não acabou no dia 30
        self.confere("até o final do mês que vem ela entrega", d(26, 10), Precisao.SEMANA)
        self.confere("no fim do ano ela viaja", d(1, 12), Precisao.MES)

    def test_dia_da_semana_passada_que_foi_ontem_e_ambigua(self):
        a = self.confere("Na terça passada ela teve crise de ansiedade", d(29, 9), passado=True)
        self.assertTrue(a.ambigua)
        self.assertEqual(a.alternativas, [d(29, 9), d(22, 9)])

    def test_dia_da_semana_passada_antes_de_ontem_nao_e_ambigua(self):
        a = self.confere("segunda passada ela chorou", d(28, 9))
        self.assertFalse(a.ambigua)


class TestPascoa(unittest.TestCase):
    def test_anos_conhecidos(self):
        for ano, data in [(2024, d(31, 3, 2024)), (2025, d(20, 4, 2025)), (2026, d(5, 4, 2026)),
                          (2027, d(28, 3, 2027)), (2028, d(16, 4, 2028))]:
            with self.subTest(ano=ano):
                self.assertEqual(pascoa(ano), data)


if __name__ == "__main__":
    unittest.main()
