"""Testes de entrenos.alertas e entrenos.perfil (DESIGN.md, seções 11 e 12).

Banco em memória, populado à mão e pelas regras reais. Data de referência fixa:
quarta-feira, 30/09/2026. Nunca date.today().
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta

from entrenos import alertas, perfil
from entrenos.banco import Banco
from entrenos.modelos import CATEGORIAS, Certeza, Item, Precisao, Relacao, TipoItem, para_json
from entrenos.regras.datas import extrair_datas
from entrenos.regras.itens import extrair_itens
from entrenos.regras.pessoas import extrair_pessoas
from entrenos.texto import data_br

REF = date(2026, 9, 30)  # quarta-feira
_ANTES = {}


def setUpModule():
    # Nada aqui grava em disco, mas se algo pedir a pasta de dados, ela é temporária.
    _ANTES["pasta"] = tempfile.mkdtemp(prefix="entrenos-teste-")
    _ANTES["env"] = os.environ.get("ENTRENOS_DADOS")
    os.environ["ENTRENOS_DADOS"] = _ANTES["pasta"]


def tearDownModule():
    if _ANTES["env"] is None:
        os.environ.pop("ENTRENOS_DADOS", None)
    else:
        os.environ["ENTRENOS_DADOS"] = _ANTES["env"]
    shutil.rmtree(_ANTES["pasta"], ignore_errors=True)


def dia(d: int, m: int = 9, a: int = 2026) -> date:
    return date(a, m, d)


def novo_item(tipo, resumo, trecho, certeza=Certeza.OBSERVADO, **campos) -> Item:
    return Item(tipo=tipo, resumo=resumo, trecho=trecho, certeza=certeza, **campos)


class Base(unittest.TestCase):
    def setUp(self):
        self.banco = Banco.novo()
        self.addCleanup(self.banco.fechar)

    # ------------------------------------------------------------ montar dados

    def registrar(self, texto: str, data: date = REF) -> int:
        """Grava como o app faria: regras reais de datas, pessoas e itens."""
        c = self.banco.config()
        pessoas = extrair_pessoas(texto, self.banco.pessoas(), nome_dela=c["nome_dela"])
        itens = extrair_itens(texto, data, pessoas, extrair_datas(texto, data))
        return self.banco.criar_registro(texto, data, itens, pessoas, [])

    def gravar(self, texto: str, data: date, itens: list[Item], relacoes=()) -> int:
        return self.banco.criar_registro(texto, data, itens, [], list(relacoes))

    def evento(self, nome: str, quando: date, registrado: date = dia(1), trecho: str | None = None,
               **campos) -> int:
        trecho = trecho or f"{nome} dia {data_br(quando)}."
        campos.setdefault("categorias", ["Eventos futuros"])
        it = novo_item(TipoItem.EVENTO, f"{nome} — {data_br(quando)}", trecho, data_evento=quando,
                       precisao=Precisao.DIA, data_expressao=data_br(quando), **campos)
        return self.gravar(trecho, registrado, [it])

    def itens_do(self, rid: int) -> list[Item]:
        return self.banco.registro(rid)["itens"]

    # ------------------------------------------------------------ consultar

    def hoje(self, ref: date = REF) -> dict:
        return alertas.hoje(self.banco, ref)

    def nivel(self, nivel: str, ref: date = REF) -> list:
        return [a for a in self.hoje(ref)["alertas"] if a.nivel == nivel]


# ====================================================================== alertas

class TestHojeVazio(Base):
    def test_banco_vazio_nao_inventa_nada(self):
        h = self.hoje()
        self.assertEqual(h, {"alertas": [], "proximos": [], "lembrar": [], "ultimos": []})

    def test_so_o_nome_dela_nao_gera_sugestao(self):
        self.banco.salvar_config(nome_dela="Ana")
        self.assertEqual(self.hoje()["alertas"], [])

    def test_registros_sem_data_nem_pendencia_nao_geram_alerta(self):
        self.registrar("Ela adora tulipas.", dia(20))
        self.registrar("Ela não gosta de sushi.", dia(21))
        h = self.hoje()
        self.assertEqual(h["alertas"], [])
        self.assertEqual(h["proximos"], [])
        self.assertEqual(len(h["ultimos"]), 2)

    def test_resultado_vira_json(self):
        self.banco.salvar_config(nome_dela="Ana", inicio_namoro=dia(7, 3), aniversario_dela=date(1998, 10, 3))
        self.registrar("A entrevista de emprego é amanhã e ela está nervosa.")
        self.registrar("Ela está esperando o resultado da prova.", dia(26))
        texto = json.dumps(para_json(self.hoje()), ensure_ascii=False)
        self.assertIn("entrevista", texto)
        self.assertIn('"nivel": "importante"', texto)


class TestImportantes(Base):
    def test_evento_amanha_pelas_regras(self):
        rid = self.registrar("A entrevista de emprego é amanhã.")
        imp = self.nivel("importante")
        self.assertEqual(len(imp), 1)
        a = imp[0]
        self.assertEqual(a.texto, "Amanhã é a entrevista de emprego.")
        self.assertEqual(a.data, dia(1, 10))
        self.assertEqual(a.registro_ids, [rid])
        self.assertTrue(90 <= a.relevancia <= 100)
        self.assertIn("30/09", a.porque)
        self.assertIn("“A entrevista de emprego é amanhã.”", a.porque)

    def test_evento_hoje_com_preocupacao_tem_relevancia_maxima(self):
        self.registrar("A prova é hoje e ela está nervosa.")
        imp = self.nivel("importante")
        self.assertEqual([a.texto for a in imp], ["Hoje é a prova."])
        self.assertEqual(imp[0].relevancia, 100)

    def test_hoje_vem_antes_de_amanha(self):
        self.evento("Consulta", dia(1, 10))
        self.evento("Exame", REF)
        imp = self.nivel("importante")
        self.assertEqual([a.texto for a in imp], ["Hoje é o exame.", "Amanhã é a consulta."])
        self.assertGreater(imp[0].relevancia, imp[1].relevancia)

    def test_preocupacao_sobe_a_relevancia(self):
        self.evento("Consulta", dia(1, 10))
        self.evento("Prova", dia(1, 10), trecho="A prova é amanhã e ela está ansiosa.")
        imp = {a.texto: a.relevancia for a in self.nivel("importante")}
        self.assertEqual(imp["Amanhã é a consulta."], 90)
        self.assertEqual(imp["Amanhã é a prova."], 95)

    def test_evento_de_outra_pessoa(self):
        self.registrar("A mãe dela vai fazer uma cirurgia amanhã.")
        imp = self.nivel("importante")
        self.assertEqual([a.texto for a in imp], ["Amanhã é a cirurgia da mãe dela."])

    def test_preocupacao_dela_com_evento_da_mae(self):
        self.registrar("A mãe dela vai fazer uma cirurgia amanhã. Ela está preocupada.")
        a = self.nivel("importante")[0]
        self.assertEqual(a.relevancia, 95)
        self.assertIn("No mesmo registro: “Ela está preocupada.”", a.porque)

    def test_evento_sem_artigo_conhecido_usa_dois_pontos(self):
        self.evento("Almoçar com a tia", dia(1, 10))
        self.assertEqual(self.nivel("importante")[0].texto, "Amanhã: Almoçar com a tia.")

    def test_evento_passado_resolvido_ou_descartado_nao_alerta(self):
        self.evento("Prova", dia(29))
        rid = self.evento("Consulta", dia(1, 10))
        self.assertEqual(len(self.nivel("importante")), 1)
        item = self.itens_do(rid)[0]
        self.banco.atualizar_item(item.id, status="resolvido")
        self.assertEqual(self.nivel("importante"), [])
        self.banco.atualizar_item(item.id, status="descartado")
        self.assertEqual(self.nivel("importante"), [])

    def test_data_so_com_mes_nao_vira_importante(self):
        self.registrar("A mãe dela vai fazer uma cirurgia em novembro.", dia(20))
        h = self.hoje(dia(31, 10))       # 01/11 é "amanhã", mas a data é só "novembro"
        self.assertEqual(h["alertas"], [])
        self.assertEqual([p["texto"] for p in h["proximos"]], ["Mãe: cirurgia em novembro/2026"])

    def test_preocupacao_por_relacao_de_contexto(self):
        padrao = novo_item(TipoItem.PADRAO, "Fica nervosa antes de apresentações",
                           "Ela fica nervosa antes de apresentações.", alvo="apresentações", polaridade=-1)
        antigo = self.gravar("Ela fica nervosa antes de apresentações.", dia(1, 8), [padrao])
        id_padrao = self.itens_do(antigo)[0].id
        texto = "Amanhã tem apresentação no trabalho."
        ev = novo_item(TipoItem.EVENTO, "Apresentação no trabalho — 01/10/2026", texto,
                       data_evento=dia(1, 10), precisao=Precisao.DIA, categorias=["Eventos futuros"])
        rel = Relacao(tipo="contexto", motivo="Em 01/08 você registrou que apresentações a deixam nervosa.",
                      item_novo=0, item_antigo_id=id_padrao, status="confirmada")
        novo = self.gravar(texto, REF, [ev], [rel])
        a = self.nivel("importante")[0]
        self.assertEqual(a.relevancia, 95)
        self.assertIn("apresentações a deixam nervosa", a.porque)
        self.assertEqual(a.registro_ids, [novo, antigo])

    def test_relacao_descartada_nao_conta(self):
        padrao = novo_item(TipoItem.PADRAO, "Fica nervosa antes de provas", "Ela fica nervosa antes de provas.",
                           polaridade=-1)
        antigo = self.gravar("Ela fica nervosa antes de provas.", dia(1, 8), [padrao])
        ev = novo_item(TipoItem.EVENTO, "Prova — 01/10/2026", "Amanhã tem prova.", data_evento=dia(1, 10),
                       precisao=Precisao.DIA)
        rel = Relacao(tipo="contexto", motivo="...", item_novo=0, item_antigo_id=self.itens_do(antigo)[0].id,
                      status="descartada")
        self.gravar("Amanhã tem prova.", REF, [ev], [rel])
        self.assertEqual(self.nivel("importante")[0].relevancia, 90)


class TestLembretes(Base):
    def test_antecedencias_padrao_30_15_7_3_1(self):
        self.evento("Apresentação no trabalho", dia(20, 10))
        for d in range(0, 40):
            ref = dia(20, 10) - timedelta(days=d)
            with self.subTest(dias=d):
                h = self.hoje(ref)
                lembretes = [a for a in h["alertas"] if a.nivel == "lembrete"]
                importantes = [a for a in h["alertas"] if a.nivel == "importante"]
                if d in (0, 1):
                    self.assertEqual(len(importantes), 1)
                    self.assertEqual(lembretes, [])
                elif d in (30, 15, 7, 3):
                    self.assertEqual(importantes, [])
                    self.assertEqual([a.texto for a in lembretes],
                                     [f"Daqui a {d} dias é a apresentação no trabalho."])
                    self.assertEqual(lembretes[0].data, dia(20, 10))
                    self.assertTrue(lembretes[0].chave.endswith(f":d{d}"))
                else:
                    self.assertEqual(h["alertas"], [])

    def test_exemplo_da_apresentacao_cita_o_nervosismo_e_o_apoio(self):
        self.registrar("Hoje ela me contou que está preocupada porque vai apresentar um projeto no trabalho "
                       "dia 20. Ela disse que odeia falar em público e que normalmente fica muito nervosa.")
        self.registrar("Ela disse que prefere que eu apenas fique perto dela e não tente resolver o problema.")
        lembretes = self.nivel("lembrete", dia(17, 10))
        self.assertEqual([a.texto for a in lembretes], ["Daqui a 3 dias é a apresentação no trabalho."])
        porque = lembretes[0].porque
        self.assertIn("No mesmo registro: “Ela disse que odeia falar em público e que normalmente fica muito "
                      "nervosa.”", porque)
        self.assertIn("prefere que você fique perto", porque)
        self.assertEqual(porque.count("No mesmo registro"), 1)

    def test_antecedencias_configuradas(self):
        self.banco.salvar_config(antecedencias=[10, 2])
        self.evento("Prova", dia(20, 10))
        for d, espera in ((10, 1), (2, 1), (7, 0), (3, 0), (30, 0)):
            with self.subTest(dias=d):
                self.assertEqual(len(self.nivel("lembrete", dia(20, 10) - timedelta(days=d))), espera)

    def test_sem_antecedencias_so_ficam_os_importantes(self):
        self.banco.salvar_config(antecedencias=[])
        self.evento("Prova", dia(20, 10))
        self.assertEqual(self.nivel("lembrete", dia(13, 10)), [])
        self.assertEqual(len(self.nivel("importante", dia(19, 10))), 1)

    def test_aniversario_da_mae_daqui_a_15_dias(self):
        rid = self.registrar("O aniversário da mãe dela é dia 15/10.", dia(1, 3))
        lembretes = self.nivel("lembrete")
        self.assertEqual([a.texto for a in lembretes], ["Daqui a 15 dias é o aniversário da mãe dela."])
        self.assertEqual(lembretes[0].data, dia(15, 10))
        self.assertEqual(lembretes[0].registro_ids, [rid])
        self.assertIn("“O aniversário da mãe dela é dia 15/10.”", lembretes[0].porque)

    def test_aniversario_se_repete_todo_ano(self):
        self.registrar("O aniversário da mãe dela é dia 15/10.", dia(1, 3))
        este = self.nivel("lembrete")[0]
        proximo = self.nivel("lembrete", date(2027, 9, 30))[0]
        self.assertEqual(proximo.data, date(2027, 10, 15))
        self.assertEqual(proximo.texto, "Daqui a 15 dias é o aniversário da mãe dela.")
        self.assertNotEqual(este.chave, proximo.chave)

    def test_aniversario_amanha_e_importante(self):
        self.registrar("O aniversário da mãe dela é dia 15/10.", dia(1, 3))
        self.assertEqual([a.texto for a in self.nivel("importante", dia(14, 10))],
                         ["Amanhã é o aniversário da mãe dela."])
        self.assertEqual([a.texto for a in self.nivel("importante", dia(15, 10))],
                         ["Hoje é o aniversário da mãe dela."])

    def test_aniversario_cadastrado_na_pessoa(self):
        pid = self.banco.criar_pessoa("Mariana", "melhor amiga")
        self.banco.atualizar_pessoa(pid, aniversario=date(1995, 10, 15))
        lembretes = self.nivel("lembrete")
        self.assertEqual([a.texto for a in lembretes],
                         ["Daqui a 15 dias é o aniversário da melhor amiga dela, Mariana."])
        self.assertEqual(lembretes[0].registro_ids, [])
        self.assertIn("15/10", lembretes[0].porque)

    def test_aniversario_do_item_e_do_cadastro_nao_duplicam(self):
        self.registrar("O aniversário da mãe dela é dia 15/10.", dia(1, 3))
        mae = self.banco.pessoas()[0]
        self.banco.atualizar_pessoa(mae["id"], aniversario=date(1960, 10, 15))
        self.assertEqual(len(self.nivel("lembrete")), 1)
        self.assertEqual(len([p for p in self.hoje()["proximos"] if p["tipo"] == "aniversario"]), 1)

    def test_aniversario_dela_nos_ajustes(self):
        self.banco.salvar_config(nome_dela="Ana", aniversario_dela=date(1998, 10, 15))
        self.registrar("O aniversário dela é 15/10.", dia(1, 2))          # o mesmo dia: não duplica
        lembretes = self.nivel("lembrete")
        self.assertEqual([a.texto for a in lembretes], ["Daqui a 15 dias é o aniversário da Ana."])
        self.assertEqual([p["tipo"] for p in self.hoje()["proximos"]], ["aniversario_dela"])

    def test_mesversario_7_meses(self):
        self.banco.salvar_config(inicio_namoro=dia(7, 3))
        lembretes = self.nivel("lembrete")
        self.assertEqual([a.texto for a in lembretes], ["Vocês completam 7 meses juntos daqui a 7 dias."])
        self.assertEqual(lembretes[0].data, dia(7, 10))
        self.assertEqual([a.texto for a in self.nivel("lembrete", dia(7, 10))],
                         ["Hoje vocês completam 7 meses juntos."])
        proximos = self.hoje()["proximos"]
        self.assertEqual([(p["texto"], p["tipo"], p["dias"]) for p in proximos],
                         [("7 meses juntos", "mesversario", 7)])

    def test_mesversario_so_7_dias_antes_e_no_dia(self):
        self.banco.salvar_config(inicio_namoro=dia(7, 3), antecedencias=[30, 15, 7, 3, 1])
        com_aviso = []
        for n in range(-10, 12):
            ref = dia(7, 10) - timedelta(days=n)
            if self.hoje(ref)["alertas"]:
                com_aviso.append(n)
        self.assertEqual(com_aviso, [0, 7])

    def test_aniversario_de_namoro(self):
        self.banco.salvar_config(inicio_namoro=date(2025, 10, 7))
        self.assertEqual([a.texto for a in self.nivel("lembrete")], ["Vocês completam 1 ano juntos daqui a 7 dias."])
        self.assertEqual([a.texto for a in self.nivel("lembrete", dia(7, 10))], ["Hoje vocês completam 1 ano juntos."])
        self.assertEqual([(p["texto"], p["tipo"]) for p in self.hoje()["proximos"]], [("1 ano de namoro", "namoro")])

    def test_mesversario_em_dia_que_nao_existe_no_mes(self):
        self.banco.salvar_config(inicio_namoro=date(2026, 1, 31))
        self.assertEqual([a.texto for a in self.nivel("lembrete", date(2026, 2, 28))],
                         ["Hoje vocês completam 1 mês juntos."])

    def test_item_de_namoro_nao_duplica_os_ajustes(self):
        self.banco.salvar_config(inicio_namoro=dia(7, 3))
        self.registrar("Nosso aniversário de namoro é 07/03.", dia(10, 3))
        self.registrar("O mesversário é dia 7.", dia(10, 3))
        ref = date(2027, 2, 28)
        self.assertEqual([a.texto for a in self.nivel("lembrete", ref)], ["Vocês completam 1 ano juntos daqui a 7 dias."])

    def test_mesversario_registrado_sem_ajustes(self):
        self.registrar("O mesversário é dia 7.", dia(10, 3))
        self.assertEqual([a.texto for a in self.nivel("lembrete")], ["Daqui a 7 dias é o mesversário de vocês."])
        self.assertEqual([a.texto for a in self.nivel("lembrete", dia(7, 11))], ["Hoje é o mesversário de vocês."])


class TestSugestoes(Base):
    def test_pendencia_vencida_vira_sugestao(self):
        rid = self.registrar("Ela está esperando o resultado da entrevista.", dia(27))
        sug = self.nivel("sugestao")
        self.assertEqual(len(sug), 1)
        s = sug[0]
        self.assertEqual(s.texto, "Há 3 dias você registrou que ela estava esperando o resultado da entrevista. "
                                  "Talvez seja um bom momento para perguntar como ficou.")
        self.assertTrue(s.chave.startswith("pendencia:"))
        self.assertEqual(s.registro_ids, [rid])
        self.assertIn("“Ela está esperando o resultado da entrevista.”", s.porque)

    def test_pendencia_antes_da_hora_so_fica_em_lembrar(self):
        rid = self.registrar("Ela está esperando o resultado da entrevista.", dia(27))
        h = self.hoje(dia(29))
        self.assertEqual(h["alertas"], [])
        self.assertEqual([(l["registro_id"], l["tipo"]) for l in h["lembrar"]], [(rid, "pendencia")])

    def test_pendencia_resolvida_some(self):
        rid = self.registrar("Ela está esperando o resultado da entrevista.", dia(27))
        self.banco.atualizar_item(self.itens_do(rid)[0].id, status="resolvido")
        h = self.hoje()
        self.assertEqual(h["alertas"], [])
        self.assertEqual(h["lembrar"], [])

    def test_pendencia_de_outra_pessoa(self):
        self.registrar("A mãe dela está esperando o resultado do exame.", dia(20))
        s = self.nivel("sugestao")[0]
        self.assertIn("você registrou que a mãe dela estava esperando o resultado do exame", s.texto)

    def test_evento_de_ontem_que_preocupava(self):
        rid = self.registrar("A apresentação no trabalho é amanhã e ela está nervosa.", dia(28))
        s = self.nivel("sugestao")
        self.assertEqual([a.texto for a in s],
                         ["Ontem foi a apresentação no trabalho. Talvez seja um bom momento para perguntar como foi."])
        self.assertEqual(s[0].data, dia(29))
        self.assertIn(rid, s[0].registro_ids)
        self.assertEqual([a.texto for a in self.nivel("sugestao", dia(1, 10))],
                         ["Anteontem foi a apresentação no trabalho. "
                          "Talvez seja um bom momento para perguntar como foi."])
        self.assertFalse([a for a in self.nivel("sugestao", dia(2, 10)) if a.chave.startswith("depois:")])

    def test_evento_passado_sem_preocupacao_nao_sugere(self):
        self.registrar("A entrevista de emprego é amanhã.", dia(28))
        self.assertEqual(self.nivel("sugestao"), [])

    def test_evento_registrado_depois_de_acontecer_nao_sugere(self):
        self.registrar("Fomos ao cinema ontem e ela estava nervosa.", dia(30))
        self.assertFalse([a for a in self.nivel("sugestao") if a.chave.startswith("depois:")])

    def test_estado_negativo_recente(self):
        rid = self.registrar("Ela estava cansada hoje.", dia(28))
        s = self.nivel("sugestao")
        self.assertEqual([a.texto for a in s],
                         ["Há 2 dias você registrou que ela estava cansada. "
                          "Talvez perguntar como ela está agora seja uma boa ideia."])
        self.assertEqual(s[0].registro_ids, [rid])
        self.assertEqual([a.texto for a in self.nivel("sugestao", dia(28))],
                         ["Hoje você registrou que ela estava cansada. "
                          "Talvez perguntar como foi o dia dela seja uma boa ideia."])
        self.assertEqual(len(self.nivel("sugestao", dia(5, 10))), 1)  # 7 dias: ainda vale
        self.assertEqual(self.nivel("sugestao", dia(6, 10)), [])      # 8 dias: não

    def test_estado_contado_por_ela_usa_comentou(self):
        self.registrar("Ela me disse que está cansada.", dia(29))
        self.assertTrue(self.nivel("sugestao")[0].texto.startswith("Ontem ela comentou que estava cansada."))

    def test_estado_bom_mais_recente_cancela_a_sugestao(self):
        self.registrar("Ela estava cansada hoje.", dia(27))
        self.registrar("Ela está feliz.", dia(29))
        self.assertEqual(self.nivel("sugestao"), [])

    def test_estado_de_outra_pessoa_nao_sugere(self):
        self.registrar("A mãe dela está cansada.", dia(29))
        self.assertEqual(self.nivel("sugestao"), [])

    def test_estado_ja_coberto_por_importante_nao_repete(self):
        self.registrar("A entrevista de emprego é amanhã e ela está nervosa.")
        h = self.hoje()
        self.assertEqual([a.nivel for a in h["alertas"]], ["importante"])

    def test_apoio_entra_no_porque(self):
        self.registrar("Ela estava triste hoje.", dia(29))
        apoio = self.registrar("Ela me disse que prefere que eu apenas fique perto quando ela está triste.", dia(1, 8))
        contra = self.registrar("Ela disse que não gosta que eu tente resolver os problemas dela.", dia(2, 8))
        s = self.nivel("sugestao")[0]
        self.assertIn("Você registrou que ela prefere que você fique perto quando ela está triste.", s.porque)
        self.assertIn("Você registrou que ela não gosta que você tente resolver os problemas dela.", s.porque)
        self.assertIn(apoio, s.registro_ids)
        self.assertIn(contra, s.registro_ids)

    def test_apoio_tambem_no_importante_que_preocupa(self):
        self.registrar("A prova é amanhã e ela está ansiosa.")
        self.registrar("Ela precisa de espaço quando está estressada.", dia(1, 8))
        a = self.nivel("importante")[0]
        self.assertIn("Você registrou que ela precisa de espaço quando está estressada.", a.porque)

    def test_sem_apoio_o_porque_so_cita_o_registro(self):
        self.registrar("Ela estava triste hoje.", dia(29))
        self.assertEqual(self.nivel("sugestao")[0].porque, "Você registrou em 29/09: “Ela estava triste hoje.”")

    def test_sugestao_e_sempre_possibilidade(self):
        self.registrar("Ela está esperando o resultado da entrevista.", dia(27))
        self.registrar("A apresentação no trabalho é amanhã e ela está nervosa.", dia(28))
        self.registrar("Ela estava cansada hoje.", dia(28))
        vistos = 0
        for _ in range(3):
            sug = self.nivel("sugestao")
            if not sug:
                break
            self.assertIn("Talvez", sug[0].texto)
            self.banco.dispensar_alerta(sug[0].chave)
            vistos += 1
        self.assertEqual(vistos, 3)


class TestAntiExcesso(Base):
    def test_todos_importantes_ate_3_lembretes_e_1_sugestao(self):
        for n, nome in enumerate(("Prova", "Consulta", "Exame", "Reunião", "Aula")):
            self.evento(nome, dia(1, 10), registrado=dia(1 + n))
        for d, nome in ((3, "Show"), (7, "Festa"), (15, "Viagem"), (30, "Formatura")):
            self.evento(nome, REF + timedelta(days=d))
        self.registrar("Ela está esperando o resultado da entrevista.", dia(20))
        self.registrar("A apresentação no trabalho é amanhã e ela está nervosa.", dia(28))
        self.registrar("Ela estava cansada hoje.", dia(29))

        h = self.hoje()
        niveis = [a.nivel for a in h["alertas"]]
        self.assertEqual(niveis, ["importante"] * 5 + ["lembrete"] * 3 + ["sugestao"])
        lembretes = [a for a in h["alertas"] if a.nivel == "lembrete"]
        self.assertEqual([(a.data - REF).days for a in lembretes], [3, 7, 15])
        sug = h["alertas"][-1]
        self.assertTrue(sug.chave.startswith("depois:"), sug.chave)      # a de maior relevância

    def test_dispensar_uma_sugestao_da_lugar_a_proxima(self):
        self.registrar("Ela está esperando o resultado da entrevista.", dia(20))
        self.registrar("A apresentação no trabalho é amanhã e ela está nervosa.", dia(28))
        primeira = self.nivel("sugestao")
        self.assertEqual(len(primeira), 1)
        self.banco.dispensar_alerta(primeira[0].chave)
        segunda = self.nivel("sugestao")
        self.assertEqual(len(segunda), 1)
        self.assertTrue(segunda[0].chave.startswith("pendencia:"))


class TestDispensar(Base):
    def test_alerta_dispensado_nao_volta(self):
        self.evento("Prova", dia(20, 10))
        ref = dia(13, 10)
        lembrete = self.nivel("lembrete", ref)[0]
        self.assertEqual(lembrete.chave, self.nivel("lembrete", ref)[0].chave)   # chave estável
        self.banco.dispensar_alerta(lembrete.chave)
        self.assertEqual(self.nivel("lembrete", ref), [])
        self.assertEqual(self.nivel("lembrete", ref), [])
        # a próxima antecedência é outro aviso
        seguinte = self.nivel("lembrete", dia(17, 10))
        self.assertEqual(len(seguinte), 1)
        self.assertNotEqual(seguinte[0].chave, lembrete.chave)

    def test_dispensar_amanha_nao_apaga_o_hoje(self):
        self.evento("Prova", dia(20, 10))
        amanha = self.nivel("importante", dia(19, 10))[0]
        self.banco.dispensar_alerta(amanha.chave)
        self.assertEqual(self.nivel("importante", dia(19, 10)), [])
        self.assertEqual([a.texto for a in self.nivel("importante", dia(20, 10))], ["Hoje é a prova."])

    def test_sugestao_dispensada_nao_volta(self):
        self.registrar("Ela estava cansada hoje.", dia(28))
        s = self.nivel("sugestao")[0]
        self.banco.dispensar_alerta(s.chave)
        self.assertEqual(self.nivel("sugestao"), [])
        self.assertEqual(self.nivel("sugestao", dia(1, 10)), [])


class TestProximos(Base):
    def test_proximos_30_dias(self):
        self.evento("Prova", dia(5, 10))
        self.evento("Show", dia(30, 10))
        self.evento("Festa", dia(31, 10))
        self.evento("Aula", dia(29))
        proximos = self.hoje()["proximos"]
        self.assertEqual([(p["texto"], p["dias"], p["tipo"]) for p in proximos],
                         [("Prova", 5, "evento"), ("Show", 30, "evento")])
        p = proximos[0]
        self.assertTrue({"data", "texto", "dias", "tipo", "item_id", "registro_id"} <= set(p))
        self.assertEqual(p["data"], dia(5, 10))
        self.assertIsNotNone(p["item_id"])

    def test_proximos_eventos_com_janela_maior(self):
        self.evento("Festa", dia(31, 10))
        self.registrar("O aniversário da mãe dela é dia 12/05.", dia(1, 2))
        self.assertEqual(alertas.proximos_eventos(self.banco, REF), [])
        um_ano = alertas.proximos_eventos(self.banco, REF, dias=365)
        self.assertEqual([(p["texto"], p["data"]) for p in um_ano],
                         [("Festa", dia(31, 10)), ("Aniversário da mãe dela", date(2027, 5, 12))])

    def test_evento_do_mes_em_andamento(self):
        self.registrar("A mãe dela vai fazer uma cirurgia em novembro.", dia(20))
        p = self.hoje(dia(10, 11))["proximos"]
        self.assertEqual([(x["texto"], x["dias"], x["precisao"]) for x in p],
                         [("Mãe: cirurgia em novembro/2026", -9, "mes")])
        self.assertEqual(self.hoje(dia(1, 12))["proximos"], [])

    def test_mesmo_evento_registrado_duas_vezes_vira_um(self):
        r1 = self.evento("Prova", dia(1, 10), registrado=dia(20))
        r2 = self.evento("Prova", dia(1, 10), registrado=dia(25))
        h = self.hoje()
        self.assertEqual(len(h["proximos"]), 1)
        self.assertEqual(h["proximos"][0]["registro_id"], r2)      # o registro mais recente
        imp = [a for a in h["alertas"] if a.nivel == "importante"]
        self.assertEqual(len(imp), 1)
        self.assertEqual(imp[0].registro_ids, [r2, r1])

    def test_data_comemorativa_que_muda_de_dia(self):
        it = novo_item(TipoItem.DATA_IMPORTANTE, "Dia das mães — 10/05", "No dia das mães ela almoça com a família.",
                       data_evento=date(2026, 5, 10), data_expressao="dia das mães", precisao=Precisao.DIA,
                       recorrente=True, categorias=["Datas importantes"])
        self.gravar("No dia das mães ela almoça com a família.", date(2026, 4, 1), [it])
        p = alertas.proximos_eventos(self.banco, date(2027, 4, 20))
        self.assertEqual([(x["texto"], x["data"]) for x in p], [("Dia das mães", date(2027, 5, 9))])

    def test_pendencia_com_data_aparece_nos_proximos(self):
        self.registrar("Ela vai receber a resposta dia 05/10.", dia(29))
        p = self.hoje()["proximos"]
        self.assertEqual([(x["tipo"], x["data"]) for x in p], [("pendencia", dia(5, 10))])
        self.assertEqual(self.nivel("importante", dia(4, 10)), [])


class TestLembrarEUltimos(Base):
    def test_lembrar_pendencias_e_estados_recentes(self):
        p = self.registrar("Ela está esperando o resultado da entrevista.", dia(27))
        e = self.registrar("Ela estava cansada hoje.", dia(28))
        self.registrar("Ela estava triste hoje.", dia(10))               # 20 dias: já não é recente
        self.registrar("Ela está aguardando a resposta do banco.", dia(1, 5))  # mais de 90 dias
        lembrar = self.hoje()["lembrar"]
        self.assertEqual([(l["registro_id"], l["tipo"]) for l in lembrar], [(p, "pendencia"), (e, "estado")])
        self.assertEqual(lembrar[0]["texto"], "Esperando o resultado da entrevista (há 3 dias)")
        self.assertEqual(lembrar[1]["texto"], "Estava cansada (há 2 dias)")
        self.assertTrue(all(l["item_id"] for l in lembrar))
        # Selo e citação em "Coisas para lembrar", como em todo lugar.
        self.assertEqual([(l["certeza"], l["trecho"]) for l in lembrar],
                         [("observado", "Ela está esperando o resultado da entrevista."),
                          ("observado", "Ela estava cansada hoje.")])

    def test_ultimos_registros(self):
        longo = "Ela contou muita coisa hoje. " * 20
        ids = [self.registrar(f"Registro número {n}.", dia(20 + n)) for n in range(6)]
        ultimo = self.registrar(longo, dia(29))
        ult = self.hoje()["ultimos"]
        self.assertEqual([u["id"] for u in ult], [ultimo, ids[5], ids[4], ids[3], ids[2]])
        self.assertLessEqual(len(ult[0]["resumo"]), 141)
        self.assertTrue(ult[0]["resumo"].endswith("…"))
        self.assertEqual(ult[1]["resumo"], "Registro número 5.")
        self.assertEqual(ult[1]["data_registro"], dia(25))


# ====================================================================== perfil

def pref(polaridade=1, certeza=Certeza.OBSERVADO, quando=REF, registro_id=1, status="ativo",
         tipo=TipoItem.PREFERENCIA, id=None) -> Item:
    return Item(tipo=tipo, resumo="Gosta de tulipas" if polaridade > 0 else "Não gosta de tulipas",
                trecho="...", certeza=certeza, alvo="tulipas", polaridade=polaridade, status=status,
                registro_id=registro_id, id=id, criado_em=datetime.combine(quando, datetime.min.time()))


class TestForcaPreferencia(unittest.TestCase):
    def forca(self, itens, ref=REF, **kw):
        return perfil.forca_preferencia(itens, ref, **kw)

    def test_um_confirmado_positivo_e_gosta(self):
        self.assertEqual(self.forca([pref(certeza=Certeza.CONFIRMADO)]), "gosta")

    def test_um_observado_e_comentou_uma_vez(self):
        self.assertEqual(self.forca([pref()]), "comentou_uma_vez")

    def test_uma_inferencia_e_comentou_uma_vez(self):
        self.assertEqual(self.forca([pref(certeza=Certeza.INFERENCIA)]), "comentou_uma_vez")
        self.assertEqual(self.forca([pref(certeza=Certeza.INFERENCIA, registro_id=1),
                                     pref(certeza=Certeza.INFERENCIA, registro_id=2)]), "comentou_uma_vez")

    def test_dois_observados_em_registros_diferentes_e_gosta(self):
        self.assertEqual(self.forca([pref(registro_id=1), pref(registro_id=2)]), "gosta")

    def test_dois_observados_no_mesmo_registro_nao_bastam(self):
        self.assertEqual(self.forca([pref(registro_id=1), pref(registro_id=1)]), "comentou_uma_vez")

    def test_observado_mais_inferencia_nao_vira_gosta(self):
        self.assertEqual(self.forca([pref(registro_id=1), pref(certeza=Certeza.INFERENCIA, registro_id=2)]),
                         "comentou_uma_vez")

    def test_contradicao_e_incerto(self):
        itens = [pref(certeza=Certeza.CONFIRMADO, registro_id=1), pref(-1, registro_id=2)]
        self.assertEqual(self.forca(itens), "incerto")

    def test_contradicao_com_restricao_e_incerto(self):
        itens = [pref(registro_id=1), pref(-1, tipo=TipoItem.RESTRICAO, registro_id=2)]
        self.assertEqual(self.forca(itens), "incerto")

    def test_item_que_mudou_nao_conta(self):
        itens = [pref(certeza=Certeza.CONFIRMADO, registro_id=1), pref(-1, registro_id=2, status="mudou")]
        self.assertEqual(self.forca(itens), "gosta")

    def test_treze_meses_sem_mencao_e_incerto(self):
        self.assertEqual(self.forca([pref(certeza=Certeza.CONFIRMADO, quando=date(2025, 8, 30))]), "incerto")

    def test_onze_meses_ainda_vale(self):
        self.assertEqual(self.forca([pref(certeza=Certeza.CONFIRMADO, quando=date(2025, 10, 30))]), "gosta")

    def test_exatamente_doze_meses_ainda_vale(self):
        self.assertEqual(self.forca([pref(certeza=Certeza.CONFIRMADO, quando=date(2025, 9, 30))]), "gosta")

    def test_mencao_nova_renova(self):
        itens = [pref(certeza=Certeza.CONFIRMADO, quando=date(2024, 1, 1), registro_id=1),
                 pref(quando=date(2026, 9, 1), registro_id=2)]
        self.assertEqual(self.forca(itens), "gosta")

    def test_so_negativo_e_nao_gosta(self):
        self.assertEqual(self.forca([pref(-1)]), "nao_gosta")
        self.assertEqual(self.forca([pref(-1, tipo=TipoItem.RESTRICAO)]), "nao_gosta")

    def test_datas_de_mencao_explicitas(self):
        item = pref(certeza=Certeza.CONFIRMADO, quando=REF, id=7)
        self.assertEqual(self.forca([item], datas_mencao={7: date(2025, 8, 1)}), "incerto")
        self.assertEqual(self.forca([item], datas_mencao={7: date(2026, 8, 1)}), "gosta")

    def test_lista_vazia_ou_sem_ativos(self):
        self.assertEqual(self.forca([]), "incerto")
        self.assertEqual(self.forca([pref(status="descartado")]), "incerto")


class TestConhecendoEla(Base):
    def perfil(self, ref: date = REF) -> dict:
        return perfil.conhecendo_ela(self.banco, ref)

    def test_banco_vazio(self):
        p = self.perfil()
        self.assertEqual(p, {
            "ela": {"nome": None, "aniversario": None, "inicio_namoro": None, "tempo_juntos": None},
            "pessoas": [],
            "preferencias": {},
            "como_ela_esta": {"situacao_atual": [], "o_que_ajuda": [], "o_que_nao_gosta": [],
                              "ultimos_acontecimentos": []},
        })

    def test_dados_dela_e_tempo_juntos(self):
        self.banco.salvar_config(nome_dela="Ana", aniversario_dela=date(1998, 5, 12), inicio_namoro=dia(7, 3))
        ela = self.perfil()["ela"]
        self.assertEqual(ela, {"nome": "Ana", "aniversario": date(1998, 5, 12), "inicio_namoro": dia(7, 3),
                               "tempo_juntos": "6 meses"})
        self.assertEqual(self.perfil(dia(7, 10))["ela"]["tempo_juntos"], "7 meses")
        self.banco.salvar_config(inicio_namoro=date(2025, 3, 7))
        self.assertEqual(self.perfil()["ela"]["tempo_juntos"], "1 ano e 6 meses")
        self.banco.salvar_config(inicio_namoro=dia(16))
        self.assertEqual(self.perfil()["ela"]["tempo_juntos"], "2 semanas")

    def test_mapa_de_preferencias(self):
        tulipas = self.registrar("Ela disse que adora tulipas.", dia(1, 6))
        self.registrar("Ela ama chocolate.", dia(10))
        self.registrar("Ela adora pizza.", dia(1, 8))
        self.registrar("Ela adora pizza.", dia(1, 9))
        self.registrar("Ela não gosta de sushi.", dia(1, 3))
        self.registrar("Ela adora sushi.", dia(1, 9))
        self.registrar("Ela é alérgica a camarão.", dia(1, 2))
        self.registrar("Ela adora doce de leite.", date(2025, 8, 1))           # 14 meses sem nova menção
        self.registrar("Acho que ela gosta de girassóis.", dia(20))
        self.registrar("A mãe dela adora orquídeas.", dia(20))                 # não é preferência dela

        prefs = self.perfil()["preferencias"]
        self.assertEqual([e["alvo"] for e in prefs["Presentes"]["gosta"]], ["tulipas"])
        t = prefs["Presentes"]["gosta"][0]
        self.assertEqual((t["certeza"], t["quando"], t["registro_id"]), ("confirmado", "há 4 meses", tulipas))
        self.assertEqual([e["alvo"] for e in prefs["Presentes"]["comentou_uma_vez"]], ["girassóis"])
        self.assertEqual(prefs["Presentes"]["comentou_uma_vez"][0]["certeza"], "inferencia")
        comidas = prefs["Comidas"]
        self.assertEqual([e["alvo"] for e in comidas["gosta"]], ["pizza"])
        self.assertEqual(comidas["gosta"][0]["mencoes"], 2)
        self.assertEqual([e["alvo"] for e in comidas["comentou_uma_vez"]], ["chocolate"])
        incertos = {e["alvo"]: e for e in comidas["incertos"]}
        self.assertEqual(set(incertos), {"sushi", "doce de leite"})
        self.assertEqual(incertos["sushi"]["certeza"], "incerto")
        self.assertIn("contrário", incertos["sushi"]["resumo"])
        self.assertIn("gosta", incertos["sushi"]["motivo"])
        self.assertEqual(len(incertos["sushi"]["registros"]), 2)
        self.assertIn("12 meses", incertos["doce de leite"]["motivo"])
        self.assertEqual([e["alvo"] for e in prefs["Saúde"]["restricoes"]], ["camarão"])
        todas = [e for cat in prefs.values() for grupo in cat.values() for e in grupo]
        self.assertNotIn("orquídeas", [e["alvo"] for e in todas])
        for e in todas:
            with self.subTest(alvo=e["alvo"]):
                self.assertIn(e["trecho"], self.banco.registro(e["registro_id"])["texto"])
                self.assertIn("quando", e)
                self.assertIn("certeza", e)
        self.assertEqual(list(prefs), sorted(prefs, key=CATEGORIAS.index))

    def test_atualizar_preferencia_tira_a_contradicao(self):
        antigo = self.registrar("Ela não gosta de sushi.", dia(1, 3))
        self.registrar("Ela adora sushi.", dia(1, 9))
        self.assertEqual(len(self.perfil()["preferencias"]["Comidas"]["incertos"]), 1)
        self.banco.atualizar_item(self.itens_do(antigo)[0].id, status="mudou")
        comidas = self.perfil()["preferencias"]["Comidas"]
        self.assertEqual(comidas["incertos"], [])
        self.assertEqual([e["alvo"] for e in comidas["comentou_uma_vez"]], ["sushi"])

    def test_restricao_e_nao_gosta_do_mesmo_alvo_ficam_so_na_restricao(self):
        self.registrar("Ela é alérgica a camarão.", dia(1, 2))
        self.registrar("Ela não gosta de camarão.", dia(1, 3))
        prefs = self.perfil()["preferencias"]
        todas = [(g, e["alvo"]) for cat in prefs.values() for g, lista in cat.items() for e in lista]
        self.assertEqual(todas, [("restricoes", "camarão")])

    def test_pessoas(self):
        r1 = self.registrar("A mãe dela, Sandra, faz aniversário dia 12/05.", dia(10, 2))
        r2 = self.registrar("A mãe dela vai fazer uma cirurgia em novembro.", dia(20))
        r3 = self.registrar("A mãe dela adora orquídeas.", dia(22))
        self.registrar("A Mariana é a melhor amiga dela.", dia(25))
        pessoas = self.perfil()["pessoas"]
        self.assertEqual([p["nome"] for p in pessoas], ["Sandra", "Mariana"])
        mae = pessoas[0]
        self.assertEqual((mae["relacao"], mae["mencoes"], mae["importancia"]), ("mãe", 3, "muito importante"))
        self.assertEqual([m["registro_id"] for m in mae["ultimas_mencoes"]], [r3, r2, r1])
        self.assertEqual(mae["ultimas_mencoes"][0]["data"], dia(22))
        self.assertIn("orquídeas", mae["ultimas_mencoes"][0]["trecho"])
        # depois de conhecida, as regras chamam a mãe pelo nome
        self.assertEqual([i["resumo"] for i in mae["infos"]], ["Sandra: gosta de orquídeas"])
        self.assertEqual([(x["texto"], x["data"]) for x in mae["datas"]],
                         [("Sandra: cirurgia em novembro/2026", dia(1, 11)),
                          ("Aniversário da mãe dela, Sandra", date(2027, 5, 12))])
        mari = pessoas[1]
        self.assertEqual((mari["relacao"], mari["mencoes"], mari["importancia"]),
                         ("melhor amiga", 1, "importante"))       # relação próxima dela
        self.assertEqual([i["tipo"] for i in mari["infos"]], ["pessoa_info"])

    def test_pessoa_cadastrada_a_mao(self):
        pid = self.banco.criar_pessoa("Carla", "chefe", observacoes="Chefe nova, desde agosto.")
        p = self.perfil()["pessoas"][0]
        self.assertEqual((p["id"], p["mencoes"], p["importancia"], p["infos"], p["ultimas_mencoes"]),
                         (pid, 0, "apareceu pouco", [], []))
        self.assertEqual(p["observacoes"], "Chefe nova, desde agosto.")
        self.banco.atualizar_pessoa(pid, aniversario=date(1980, 11, 3))
        p = self.perfil()["pessoas"][0]
        self.assertEqual(p["importancia"], "importante")
        self.assertEqual([(x["tipo"], x["data"]) for x in p["datas"]], [("aniversario", dia(3, 11))])

    def test_como_ela_esta(self):
        cansada = self.registrar("Ela estava cansada hoje.", dia(25))
        self.registrar("Ela estava triste hoje.", dia(10))                          # 20 dias: fora
        self.registrar("A mãe dela está cansada.", dia(28))                         # não é ela
        feliz = self.registrar("Ela está feliz.", dia(29))
        ajuda = self.registrar("Ela me disse que prefere que eu apenas fique perto quando ela está triste.",
                               dia(1, 5))
        nao = self.registrar("Ela disse que não gosta que eu tente resolver os problemas dela.", dia(1, 6))
        prova = self.registrar("A prova dela é amanhã.", dia(26))                   # aconteceu em 27/09
        self.registrar("A viagem dela é amanhã.", dia(10))                          # 11/09: fora
        self.registrar("A festa é dia 20/10.", dia(29))                             # futuro: fora

        como = self.perfil()["como_ela_esta"]
        self.assertEqual(set(como), {"situacao_atual", "o_que_ajuda", "o_que_nao_gosta", "ultimos_acontecimentos"})
        self.assertEqual([e["registro_id"] for e in como["situacao_atual"]], [feliz, cansada])
        self.assertEqual([e["trecho"] for e in como["situacao_atual"]], ["Ela está feliz.", "Ela estava cansada hoje."])
        self.assertEqual([e["registro_id"] for e in como["o_que_ajuda"]], [ajuda])
        self.assertEqual(como["o_que_ajuda"][0]["certeza"], "confirmado")
        self.assertEqual([e["registro_id"] for e in como["o_que_nao_gosta"]], [nao])
        self.assertEqual([e["registro_id"] for e in como["ultimos_acontecimentos"]], [prova])
        for lista in como.values():
            for e in lista:
                self.assertIn(e["trecho"], self.banco.registro(e["registro_id"])["texto"])

    def test_resultado_vira_json(self):
        self.banco.salvar_config(nome_dela="Ana", inicio_namoro=dia(7, 3))
        self.registrar("A mãe dela, Sandra, faz aniversário dia 12/05.", dia(10, 2))
        self.registrar("Ela disse que adora tulipas.", dia(1, 6))
        self.registrar("Ela estava cansada hoje.", dia(25))
        texto = json.dumps(para_json(self.perfil()), ensure_ascii=False)
        self.assertIn("tulipas", texto)
        self.assertIn("Sandra", texto)


if __name__ == "__main__":
    unittest.main()
