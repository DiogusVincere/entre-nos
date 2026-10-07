"""Ciclo (DESIGN.md, seção 20.8) — ela sabe e concorda.

Só este recurso entra na lista (`recursos.usando(ciclo)`): os outros estão sendo escritos ao mesmo
tempo. Cobre a extração por regras (frases de conversa, gírias, sem acento, datas relativas, negação,
dúvida, hábito, futuro, outra pessoa, humor que nunca vira sintoma, o que ajuda), a gravação conferida
(🔒 sempre, itens da mesma frase fora de "Como ela está"), a previsão com séries sintéticas (regulares,
irregulares, registro esquecido, mês fora do padrão, menos de 3 ciclos, duração média, padrões de
sintomas, período fértil só regular e com o aviso fixo), o Hoje neutro (sem as palavras do ciclo), a IA
(nada do ciclo vai para ela), as rotas, exportar/importar/apagar (também nas cópias) e a tela.

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026 (quarta), scrypt pequeno.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from entrenos import alertas, cofre, motor, nucleo, perfil, recursos
from entrenos.banco import Banco
from entrenos.modelos import Resposta
from entrenos.motor import Conhecimento
from entrenos.recursos import ciclo
from entrenos.recursos.ciclo import Ciclo, Evento
from entrenos.regras.pessoas import plano
from testes.test_servidor import PIN, REF, Relogio

DATA = REF.isoformat()
RAIZ = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
IA = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": "modelo-de-teste", "tipo": "ollama",
      "chave": None}
AVISO_DA_SECAO = ("Estimativa aproximada, feita a partir de anotações. Não serve como método para evitar ou planejar "
                  "gravidez; para isso, o ideal é um ginecologista.")
PROIBIDAS_NO_HOJE = re.compile(r"menstru|ciclo|\btpm\b|colica|fluxo|fertil|ovula|absorvente")
DIAGNOSTICO = re.compile(r"diagnost|sindrome|endometri|doenca|policistic|anormal|gravida|atrasad|infertil|disfunc"
                         r"|hormon|tratamento|patolog")
_PADRAO = object()

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-ciclo-env-")
    os.environ["ENTRENOS_DADOS"] = _pasta_env      # garantia: nada cai em %LOCALAPPDATA%
    remendo = mock.patch.object(cofre, "SCRYPT_N", 2 ** 12)
    remendo.start()
    _remendos.append(remendo)


def tearDownModule() -> None:
    for remendo in _remendos:
        remendo.stop()
    _remendos.clear()
    if _env_antigo is None:
        os.environ.pop("ENTRENOS_DADOS", None)
    else:
        os.environ["ENTRENOS_DADOS"] = _env_antigo
    shutil.rmtree(_pasta_env, ignore_errors=True)


def extras(texto: str, referencia: date = REF, nome_dela: str | None = None) -> list[dict]:
    """O que o recurso propõe para o texto, como a revisão recebe (pelo motor e pela fundação)."""
    with recursos.usando(ciclo):
        proposta = motor.MotorRegras().analisar(texto, referencia, Conhecimento(pessoas=[], nome_dela=nome_dela))
    return proposta.extras.get("ciclo", [])


def tipos(texto: str, **kw) -> list[str]:
    return [e["tipo"] for e in extras(texto, **kw)]


def d(iso: str) -> date:
    return date.fromisoformat(iso)


def serie(ultimo: str, intervalos: list[int], duracao: int | None = 5, exatidoes: dict[int, str] | None = None,
          confirmados: tuple[int, ...] = ()) -> list[Ciclo]:
    """Ciclos sintéticos que terminam em `ultimo`, com os `intervalos` (em dias) entre os inícios."""
    inicios = [d(ultimo)]
    for i in reversed(intervalos):
        inicios.insert(0, inicios[0] - timedelta(days=i))
    saida = []
    for n, ini in enumerate(inicios, start=1):
        saida.append(Ciclo(inicio=ini, fim=ini + timedelta(days=duracao - 1) if duracao else None,
                           exatidao=(exatidoes or {}).get(n, "dia"), id=n, intervalo_confirmado=n in confirmados))
    return saida


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


# ======================================================================== extração: começo, durante, fim

class TestExtrairAcontecimentos(unittest.TestCase):
    def um(self, texto: str, tipo: str, **kw) -> dict:
        achados = [e for e in extras(texto, **kw) if e["tipo"] == tipo]
        self.assertEqual(len(achados), 1, extras(texto, **kw))
        e = achados[0]
        self.assertIn(e["trecho"], texto)                            # nunca inventar: trecho literal
        self.assertTrue(e["_privado"])                               # todo dado do ciclo pede 🔒
        return e

    def test_menstruou_ontem_com_colica(self):
        e = self.um("Ela menstruou ontem e ta com muita colica.", "ciclo_inicio")
        self.assertEqual((e["data"], e["exatidao"], e["certeza"], e["de_quem"]), ("2026-09-29", "dia", "observado", "ela"))
        self.assertEqual([(s["chave"], s["palavras"], s["data"]) for s in e["sintomas"]],
                         [("colica", "colica", DATA)])               # "tá com cólica" é agora
        self.assertEqual(e["resumo"], "Início: 29/09/2026 · colica")
        self.assertEqual(e["motivo"], "Você escreveu “menstruou”.")

    def test_desceu_hj_pra_ela_girias_sem_acento(self):
        e = self.um("desceu hj pra ela, ta mt cansada e com dor de cabeca", "ciclo_inicio")
        self.assertEqual(e["data"], DATA)
        self.assertEqual([s["palavras"] for s in e["sintomas"]], ["cansada", "dor de cabeca"])

    def test_veio_a_menstruacao_anteontem_e_ontem_virgula(self):
        self.assertEqual(self.um("Veio a menstruação dela anteontem.", "ciclo_inicio")["data"], "2026-09-28")
        self.assertEqual(self.um("Ontem, ela menstruou.", "ciclo_inicio")["data"], "2026-09-29")
        self.assertEqual(self.um("A menstruação dela chegou hoje cedo.", "ciclo_inicio")["data"], DATA)

    def test_desde_segunda_dito_na_quarta_e_a_segunda_que_passou(self):
        # O regras/datas.py põe "segunda" no futuro (05/10); o ciclo lê de novo no passado.
        e = self.um("Ela tá menstruada desde segunda.", "ciclo_durante")
        self.assertEqual((e["inicio"], e["data"]), ("2026-09-28", DATA))
        self.assertEqual(self.um("ela ta de chico desde sabado", "ciclo_durante")["inicio"], "2026-09-26")

    def test_dia_28_e_28_09_nunca_viram_o_ano_que_vem(self):
        self.assertEqual(self.um("Ela ficou menstruada dia 28.", "ciclo_inicio")["data"], "2026-09-28")
        self.assertEqual(self.um("Ela menstruou 28/09.", "ciclo_inicio")["data"], "2026-09-28")

    def test_segundo_dia_e_ha_3_dias_dizem_quando_comecou(self):
        e = self.um("Ela ta no segundo dia da menstruação.", "ciclo_durante")
        self.assertEqual((e["inicio"], e["dia_do_ciclo"]), ("2026-09-29", 2))
        self.assertEqual(self.um("ela ta menstruada, 3º dia, bem cansada", "ciclo_durante")["inicio"], "2026-09-28")
        self.assertEqual(self.um("Ela ta menstruada há 3 dias.", "ciclo_durante")["inicio"], "2026-09-27")

    def test_ta_menstruada_sem_data_pergunta_quando_comecou(self):
        e = self.um("ela tá naqueles dias", "ciclo_durante")
        self.assertIsNone(e["inicio"])
        self.assertEqual(e["resumo"], "Em andamento em 30/09/2026 (início não informado)")

    def test_terca_passada_dita_na_quarta_traz_a_outra_leitura(self):
        e = self.um("Ela menstruou terça passada.", "ciclo_inicio")
        self.assertEqual((e["data"], e["alternativas"]), ("2026-09-29", ["2026-09-22"]))

    def test_mes_passado_e_aproximado_e_acho_que_e_talvez(self):
        e = self.um("Ela menstruou mes passado.", "ciclo_inicio")
        self.assertEqual((e["exatidao"], e["precisao"]), ("aproximada", "mes"))
        self.assertIn("(aproximado)", e["resumo"])
        e = self.um("Acho que ela menstruou ontem.", "ciclo_inicio")
        self.assertEqual((e["certeza"], e["exatidao"]), ("inferencia", "talvez"))

    def test_fala_dela_e_confirmado(self):
        e = self.um("Ela me contou que menstruou ontem.", "ciclo_inicio")
        self.assertEqual(e["certeza"], "confirmado")

    def test_comecou_sexta_e_acabou_ontem_na_mesma_frase(self):
        texto = "A menstruação dela começou sexta e acabou ontem."
        self.assertEqual(tipos(texto), ["ciclo_inicio", "ciclo_fim"])
        self.assertEqual(self.um(texto, "ciclo_inicio")["data"], "2026-09-25")
        self.assertEqual(self.um(texto, "ciclo_fim")["data"], "2026-09-29")
        self.assertEqual(self.um("Acabou a menstruação dela hoje.", "ciclo_fim")["data"], DATA)
        self.assertEqual(self.um("Ela ficou menstruada sexta e já parou ontem.", "ciclo_fim")["data"], "2026-09-29")

    def test_sem_data_usa_o_dia_do_registro_e_avisa(self):
        e = self.um("Ela menstruou.", "ciclo_inicio")
        self.assertEqual((e["data"], e["sem_data"]), (DATA, True))
        outro = date(2026, 8, 12)
        self.assertEqual(self.um("Ela menstruou.", "ciclo_inicio", referencia=outro)["data"], "2026-08-12")

    def test_ainda_nao_acabou_continua(self):
        self.assertEqual(tipos("A menstruação dela ainda não acabou."), ["ciclo_durante"])
        self.assertEqual(tipos("ainda nao acabou a menstruação"), ["ciclo_durante"])


# ======================================================================== extração: o que NÃO é (ou é só comentário)

class TestExtrairNaoE(unittest.TestCase):
    def test_desceu_de_algum_lugar_nao_e_inicio(self):
        for texto in ("Ela desceu do carro correndo.", "O elevador desceu ontem.", "Ela desceu.",
                      "ela desceu pra praia no sabado"):
            with self.subTest(texto=texto):
                self.assertEqual(extras(texto), [])

    def test_parou_e_acabou_de_outra_coisa_nao_e_fim(self):
        self.assertEqual(extras("o carro parou ontem"), [])
        self.assertEqual(tipos("Ela menstruou ontem e a aula acabou cedo."), ["ciclo_inicio"])
        self.assertEqual(tipos("Ela menstruou ontem e a dor parou com o remédio."), ["ciclo_inicio"])
        # A data de outra oração não vira a data do período.
        e = extras("o carro parou ontem e ela ta menstruada")[0]
        self.assertEqual((e["tipo"], e["data"]), ("ciclo_durante", DATA))

    def test_negacao_vira_comentario_sem_data_e_sem_falar_em_atraso(self):
        for texto in ("Ainda nao desceu pra ela.", "ela nao menstruou ainda", "A menstruação dela ainda não veio."):
            with self.subTest(texto=texto):
                [e] = extras(texto)
                self.assertEqual((e["tipo"], e["valor"]), ("ciclo_mencao", "atraso"))
                self.assertNotIn("atras", plano(e["resumo"]))           # nunca "atrasada"
                self.assertTrue(e["_privado"])

    def test_duvida_habito_e_futuro_nao_tem_data(self):
        casos = {"nao sei se ela menstruou": "duvida", "Ela sempre fica menstruada no comeco do mes.": "habito",
                 "Ela vai menstruar semana que vem.": "futuro"}
        for texto, valor in casos.items():
            with self.subTest(texto=texto):
                [e] = extras(texto)
                self.assertEqual((e["tipo"], e["valor"]), ("ciclo_mencao", valor))
                self.assertNotIn("inicio", e)

    def test_outra_pessoa_e_voce_mesmo_nao_entram_no_dela(self):
        for texto in ("A irmã dela menstruou pela primeira vez.", "A Mariana menstruou ontem.",
                      "Mariana, a melhor amiga dela, ficou menstruada ontem.", "Minha menstruação atrasou.",
                      "Hoje eu menstruei.", "A irmã dela, que mora em SP, menstruou ontem."):
            with self.subTest(texto=texto):
                [e] = extras(texto)
                self.assertEqual((e["tipo"], e["valor"], e["de_quem"]), ("ciclo_mencao", "outra", "outra"))
                self.assertTrue(e["_privado"])                           # mesmo assim, fora da IA

    def test_ela_continua_sendo_o_sujeito(self):
        for texto in ("Ela contou pra mãe dela que menstruou ontem.", "Eu levei chá pra ela porque ela menstruou ontem.",
                      "Ela levou a Mariana ao medico e menstruou ontem."):
            with self.subTest(texto=texto):
                self.assertEqual(tipos(texto), ["ciclo_inicio"])

    def test_humor_nunca_vira_sintoma(self):
        e = extras("Ela ficou menstruada dia 28, ta irritada e com os seios sensiveis.")[0]
        self.assertEqual([s["chave"] for s in e["sintomas"]], ["seios_sensiveis"])
        texto = json.dumps(extras("Ela menstruou ontem, ta triste, chorona, sensivel e irritada."), ensure_ascii=False)
        for humor in ("irritada", "triste", "chorona", "sensivel"):
            self.assertNotIn(f'"palavras": "{humor}"', texto)
        self.assertNotIn("sintomas", extras("Ela menstruou ontem, ta triste, chorona, sensivel e irritada.")[0])

    def test_sem_nada_do_ciclo_nao_gera_nada(self):
        for texto in ("Ela tá irritada hoje.", "Ela tá cansada por causa do trabalho.", "Ela adora tulipas amarelas.",
                      "Ela teve cólica renal semana passada.", "O ciclo de palestras dela começa sexta."):
            with self.subTest(texto=texto):
                self.assertEqual(extras(texto), [])

    def test_tpm_e_so_comentario_sem_virar_padrao(self):
        [e] = extras("Ela ta de TPM, bem sensivel.")
        self.assertEqual((e["tipo"], e["valor"]), ("ciclo_mencao", "tpm"))


# ======================================================================== extração: sintomas, fluxo e o que ajuda

class TestExtrairSintomasEAjuda(unittest.TestCase):
    def test_sintoma_negado_fica_de_fora(self):
        [e] = extras("Ela não tá com cólica, ainda bem, menstruou ontem.")
        self.assertNotIn("sintomas", e)

    def test_frase_vizinha_conta_menos_quando_tem_outra_causa(self):
        self.assertEqual(tipos("Ela menstruou ontem. Ta com muita colica."), ["ciclo_inicio", "ciclo_sintoma"])
        self.assertEqual(tipos("Ela menstruou ontem. Ta cansada por causa do trabalho."), ["ciclo_inicio"])
        self.assertEqual(tipos("Ela adora tulipas. Ela menstruou ontem."), ["ciclo_inicio"])

    def test_colica_sozinha_vira_sintoma_para_voce_confirmar(self):
        [e] = extras("Ela ta com colica.")
        self.assertEqual((e["tipo"], e["sintomas"][0]["chave"]), ("ciclo_sintoma", "colica"))

    def test_fluxo_so_com_a_palavra_fluxo(self):
        e = extras("Ela menstruou dia 28/09 e o fluxo ta forte")[0]
        self.assertEqual((e["fluxo"], e["resumo"]), ("intenso", "Início: 28/09/2026 · fluxo intenso"))
        [s] = extras("Ela disse que o fluxo ta leve e que ta com desejo de doce.")
        self.assertEqual((s["tipo"], s["fluxo"], s["certeza"]), ("ciclo_sintoma", "leve", "confirmado"))
        self.assertNotIn("fluxo", extras("Ela menstruou ontem e ta descendo muito.")[0])

    def test_quando_colica_bolsa_de_agua_quente_ajuda(self):
        achados = extras("Quando ela está com cólica, bolsa de água quente ajuda.")
        [a] = [e for e in achados if e["tipo"] == "ciclo_ajuda"]
        self.assertEqual((a["valor"], a["polaridade"], a["resumo"]), ("bolsa de água quente", 1, "Ajuda: bolsa de água quente"))

    def test_o_que_ajuda_e_cha_dito_por_ela(self):
        [a] = [e for e in extras("Ela disse que o que ajuda é chá de camomila quando ta com colica.")
               if e["tipo"] == "ciclo_ajuda"]
        self.assertEqual((a["valor"], a["certeza"]), ("chá de camomila", "confirmado"))

    def test_o_que_nao_ajuda(self):
        [a] = [e for e in extras("Ela odeia que eu fique perguntando quando ta com colica.") if e["tipo"] == "ciclo_ajuda"]
        self.assertEqual((a["valor"], a["polaridade"]), ("que eu fique perguntando", -1))
        [b] = [e for e in extras("Ela ta com muita colica e o remedio nao ajuda.") if e["tipo"] == "ciclo_ajuda"]
        self.assertEqual((b["valor"], b["polaridade"]), ("o remedio", -1))
        [c] = [e for e in extras("quando ela ta com colica ela nao gosta que eu fique dando conselho")
               if e["tipo"] == "ciclo_ajuda"]
        self.assertEqual((c["valor"], c["polaridade"]), ("que eu fique dando conselho", -1))

    def test_gosto_so_vira_ajuda_ligado_ao_ciclo(self):
        achados = extras("Ela gosta de chocolate quando ta de TPM.")
        self.assertEqual([(e["tipo"], e.get("valor")) for e in achados], [("ciclo_ajuda", "chocolate"), ("ciclo_mencao", "tpm")])
        self.assertEqual(tipos("Ela ama chocolate. Ela menstruou ontem."), ["ciclo_inicio"])
        achados = extras("Hoje desceu pra ela. Bolsa de agua quente ajuda muito ela.")
        self.assertEqual([e.get("valor") for e in achados if e["tipo"] == "ciclo_ajuda"], ["Bolsa de agua quente"])

    def test_tudo_cita_pedacos_literais(self):
        textos = ["Ela menstruou ontem e ta com muita colica.", "Desceu hoje pra ela, ta bem cansada e com dor de cabeca.",
                  "Quando ela está com cólica, bolsa de água quente ajuda.", "Ela tá de TPM, com espinhas e inchada.",
                  "ela disse que quando ta menstruada prefere ficar quietinha em casa",
                  "Ela menstruou ontem. O fluxo ta intenso, ta com enxaqueca e náusea."]
        for texto in textos:
            for e in extras(texto):
                with self.subTest(texto=texto, tipo=e["tipo"]):
                    self.assertIn(e["trecho"], texto)
                    if e.get("valor") and e["tipo"] == "ciclo_ajuda":
                        self.assertIn(e["valor"], texto)
                    for s in e.get("sintomas", []):
                        self.assertIn(s["palavras"], texto)

    def test_habito_e_condicao_nao_viram_sintoma_do_dia(self):
        # Revisão de 07/10: o hábito e a condição viravam sintoma do dia do registro (e entravam nos padrões).
        self.assertEqual([(e["tipo"], e.get("valor"), e.get("sintomas")) for e in
                          extras("Ela sempre fica com enxaqueca quando menstrua.")],
                         [("ciclo_mencao", "habito", None)])
        self.assertEqual([(e["tipo"], e.get("valor")) for e in
                          extras("Ela disse que quando está com cólica, chá de camomila ajuda.")],
                         [("ciclo_ajuda", "chá de camomila")])
        # O dia continua: "menstruou ontem e tá com cólica" é sintoma.
        [e] = extras("Ela menstruou ontem e tá com muita cólica.")
        self.assertEqual([s["chave"] for s in e["sintomas"]], ["colica"])

    def test_terminou_de_menstruar_e_fim_com_a_data(self):
        [e] = extras("Ela terminou de menstruar anteontem.")
        self.assertEqual((e["tipo"], e["data"]), ("ciclo_fim", "2026-09-28"))

    def test_um_vocabulario_so_para_a_revisao_e_o_fora_da_ia(self):
        # Revisão de 07/10: o 403 do ✨ Analisar reconhecia estas frases, mas o fora_da_ia (pelo texto)
        # não, e elas iam para a IA quando o extra não era confirmado.
        for texto in ("O ciclo dela atrasou essa semana e ela ficou preocupada com isso.",
                      "Desceu hoje pra ela, tá com cólica e bem cansada.",
                      "Ela tá no período dela e anda bem sensível.",
                      "A regra dela veio forte esse mês.",
                      "Ela tá com cólica hoje.",
                      "A irmã dela menstruou pela primeira vez."):
            with self.subTest(texto=texto):
                self.assertTrue(extras(texto))
                self.assertTrue(ciclo.texto_fala_do_ciclo(texto))
        for texto in ("O elevador desceu.", "A regra do jogo mudou.", "Ela tá estudando no período da tarde.",
                      "Ela anda de bicicleta todo dia.", "Ela teve cólica renal ontem."):
            with self.subTest(texto=texto):
                self.assertEqual(extras(texto), [])
                self.assertFalse(ciclo.texto_fala_do_ciclo(texto))

    def test_texto_grande_tem_limite(self):
        texto = " ".join(f"Ela menstruou dia {n % 28 + 1}." for n in range(80))
        self.assertLessEqual(len(extras(texto)), 50)


# ======================================================================== previsão (séries sintéticas)

class TestPrevisao(unittest.TestCase):
    def test_regulares(self):
        c = ciclo.calcular(serie("2026-09-28", [28, 28, 28, 28]), REF)
        p = c["previsao"]
        self.assertEqual((p["inicio"], p["fim"], p["provavel"]), (d("2026-10-24"), d("2026-10-28"), d("2026-10-26")))
        self.assertEqual((p["irregular"], p["ciclos"], p["mediana"], p["certeza"]), (False, 4, 28, "inferencia"))
        self.assertEqual(p["texto"], "Próximo início: entre 24/10 e 28/10 (mais provável 26/10).")
        self.assertIn("últimos 4 ciclos registrados (de 28 a 28 dias; mediana de 28)", p["porque"])
        self.assertIsNone(p["aviso"])

    def test_faixa_segue_a_variacao_com_minimo_de_2_dias(self):
        p = ciclo.calcular(serie("2026-09-28", [27, 31, 29, 28]), REF)["previsao"]
        self.assertEqual((p["inicio"], p["fim"]), (d("2026-10-25"), d("2026-10-29")))     # 27..31
        self.assertEqual(p["mediana"], 28.5)
        self.assertIn("mediana de 28,5", p["porque"])

    def test_irregulares(self):
        c = ciclo.calcular(serie("2026-09-28", [24, 33, 27, 36]), REF)
        p = c["previsao"]
        self.assertTrue(p["irregular"])
        self.assertEqual((p["variacao"], p["inicio"], p["fim"]), (12, d("2026-10-22"), d("2026-11-03")))
        self.assertIn("variaram 12 dias", p["aviso"])
        self.assertIsNone(c["fertil"])
        self.assertIn("irregulares", c["fertil_motivo"])

    def test_menos_de_3_ciclos_nao_preve(self):
        for intervalos in ([], [28], [28, 29]):
            with self.subTest(intervalos=intervalos):
                c = ciclo.calcular(serie("2026-09-28", intervalos), REF)
                self.assertIsNone(c["previsao"])
                self.assertIsNone(c["fertil"])
                self.assertIn(f"por enquanto, {len(intervalos)}", c["sem_previsao"])
        self.assertIn("Falta registrar mais 1 início.", ciclo.calcular(serie("2026-09-28", [28, 29]), REF)["sem_previsao"])

    def test_registro_esquecido_pergunta_e_fica_fora_da_conta(self):
        c = ciclo.calcular(serie("2026-09-28", [28, 29, 57, 28]), REF)
        situacoes = [i["situacao"] for i in c["intervalos"]]
        self.assertEqual(situacoes, ["usado", "usado", "esquecido", "usado"])
        [q] = c["perguntas"]
        self.assertEqual(q["dias"], 57)
        self.assertTrue(q["texto"].endswith("quase o dobro do comum (28). Faltou registrar um mês?"), q["texto"])
        p = c["previsao"]
        self.assertEqual((p["ciclos"], p["maior"]), (3, 29))
        self.assertIn("1 intervalo que parece ter um mês sem registro", p["porque"])

    def test_intervalo_que_voce_confirmou_nao_pergunta_mais(self):
        c = ciclo.calcular(serie("2026-09-28", [28, 29, 57, 28], confirmados=(4,)), REF)
        self.assertEqual(c["perguntas"], [])
        self.assertEqual(c["intervalos"][2]["situacao"], "longo_confirmado")
        self.assertEqual(c["previsao"]["ciclos"], 3)

    def test_mes_fora_do_padrao_e_descartado(self):
        c = ciclo.calcular(serie("2026-09-28", [28, 29, 28, 45, 28]), REF)
        self.assertEqual([i["situacao"] for i in c["intervalos"]], ["usado", "usado", "usado", "fora_do_padrao", "usado"])
        p = c["previsao"]
        # A faixa sai dos ciclos parecidos (28 e 29), mas o mês bem diferente conta como irregularidade:
        # aviso e nada de período fértil (revisão de 07/10).
        self.assertEqual((p["menor"], p["maior"], p["irregular"]), (28, 29, True))
        self.assertIn("1 fora do padrão", p["porque"])
        self.assertIn("variaram 17 dias (de 28 a 45)", p["aviso"])
        self.assertIsNone(c["fertil"])

    def test_fertil_so_com_ciclos_regulares_de_verdade(self):
        # Os casos da revisão de 07/10: um ciclo fora do padrão (40 e 18), um longo que você confirmou
        # (56) e um de 45 deixavam o período fértil aparecer como se tudo fosse regular.
        casos = (([28, 28, 28, 28, 40, 18], ()), ([28, 28, 28, 56], (5,)), ([28, 27, 29, 45], ()))
        for intervalos, confirmados in casos:
            with self.subTest(intervalos=intervalos):
                c = ciclo.calcular(serie("2026-09-20", intervalos, confirmados=confirmados), REF)
                self.assertTrue(c["previsao"]["irregular"])
                self.assertIsNotNone(c["previsao"]["aviso"])
                self.assertIsNone(c["fertil"])
                self.assertIn("irregulares", c["fertil_motivo"])
        # Controle: regulares de verdade continuam com o período fértil.
        self.assertIsNotNone(ciclo.calcular(serie("2026-09-20", [28, 29, 28, 27]), REF)["fertil"])


    def test_poucos_ciclos_nao_descartam_nada(self):
        # Com 3 intervalos só, nada é "fora do padrão": sobraria pouco para prever.
        c = ciclo.calcular(serie("2026-09-28", [28, 29, 40]), REF)
        self.assertEqual([i["situacao"] for i in c["intervalos"]], ["usado", "usado", "usado"])
        self.assertTrue(c["previsao"]["irregular"])

    def test_datas_aproximadas_e_talvez_nao_entram_na_conta(self):
        ciclos = serie("2026-09-28", [28, 28, 28, 28, 28], exatidoes={3: "aproximada"})
        c = ciclo.calcular(ciclos, REF)
        self.assertEqual([i["situacao"] for i in c["intervalos"]], ["usado", "aproximado", "aproximado", "usado", "usado"])
        self.assertEqual(c["previsao"]["ciclos"], 3)
        ciclos = serie("2026-09-28", [28, 28, 28], exatidoes={4: "talvez"})
        c = ciclo.calcular(ciclos, REF)
        self.assertIsNone(c["previsao"])
        self.assertEqual(c["atual"]["inicio"], d("2026-08-31"))         # "talvez" não vira o último início

    def test_inicio_aproximado_alarga_a_faixa_e_nao_estima_fertil(self):
        # "Ela já estava menstruada em 28/09" (sem saber o começo): a base da previsão é aproximada.
        ciclos = serie("2026-08-31", [28, 28, 28, 28]) + [Ciclo(inicio=d("2026-09-28"), exatidao="aproximada", id=9)]
        c = ciclo.calcular(ciclos, REF)
        p = c["previsao"]
        self.assertTrue(p["base_aproximada"])
        self.assertEqual(p["inicio"], d("2026-10-21"))                 # 26 - 3 dias
        self.assertIsNone(c["fertil"])
        self.assertIn("sem a data exata", c["fertil_motivo"])

    def test_duracao_media_pelos_fins(self):
        self.assertIsNone(ciclo.duracao(serie("2026-09-28", [28, 28], duracao=None), REF))
        ciclos = serie("2026-09-28", [28, 28, 28], duracao=None)
        ciclos[0].fim = ciclos[0].inicio + timedelta(days=4)
        self.assertEqual(ciclo.duracao(ciclos, REF)["texto"], "Só um fim registrado até agora: 5 dias.")
        for c, dias in zip(ciclos[1:], (6, 4, 5)):
            c.fim = c.inicio + timedelta(days=dias - 1)
        dur = ciclo.duracao(ciclos, REF)
        self.assertEqual((dur["media"], dur["minimo"], dur["maximo"], dur["ciclos"]), (5, 4, 6, 4))
        self.assertEqual(dur["texto"], "Duração média: 5 dias (de 4 a 6, em 4 ciclos com fim registrado).")

    def test_periodo_fertil_so_regular_e_com_o_aviso_fixo(self):
        c = ciclo.calcular(serie("2026-09-28", [28, 28, 28, 28]), REF)
        f = c["fertil"]
        self.assertEqual(f["aviso"], AVISO_DA_SECAO)
        self.assertEqual(ciclo.AVISO_FERTIL, AVISO_DA_SECAO)
        self.assertEqual((f["ovulacao_inicio"], f["ovulacao_fim"]), (d("2026-10-08"), d("2026-10-16")))   # 14 ± 2
        self.assertEqual((f["inicio"], f["fim"]), (d("2026-10-03"), d("2026-10-17")))                  # -5..+1
        self.assertEqual(f["certeza"], "inferencia")

    def test_faixa_que_ja_passou_sem_novo_inicio(self):
        c = ciclo.calcular(serie("2026-08-01", [28, 28, 28, 28]), REF)
        p = c["previsao"]
        self.assertTrue(p["passou"])
        self.assertIn("Talvez ele só não tenha sido anotado.", p["texto_passou"])
        self.assertIsNone(c["fertil"])

    def test_agora_em_andamento(self):
        c = ciclo.calcular(serie("2026-09-28", [28, 28, 28], duracao=None), REF)
        self.assertEqual((c["atual"]["dia"], c["atual"]["em_andamento"]), (3, True))
        c = ciclo.calcular(serie("2026-09-20", [28, 28, 28], duracao=5), REF)
        self.assertEqual((c["atual"]["dia"], c["atual"]["em_andamento"]), (11, False))

    def test_no_momento_antes_de_comecar(self):
        ciclos = serie("2026-09-02", [28, 28, 28])
        padroes = [{"dias": [-2, -2], "texto": "Inchada em 2 dos últimos 4 ciclos, sempre 2 dias antes de começar."}]
        calc = ciclo.calcular(ciclos, d("2026-09-28"))
        self.assertEqual(calc["previsao"]["provavel"], d("2026-09-30"))
        self.assertEqual(ciclo.no_momento(calc, padroes, d("2026-09-28")),
                         ["Faltam 2 dias para a data mais provável. Inchada em 2 dos últimos 4 ciclos, sempre 2 dias antes de começar."])
        self.assertEqual(ciclo.no_momento(calc, padroes, d("2026-09-27")), [])

    def test_padroes_de_sintomas(self):
        ciclos = serie("2026-09-28", [28, 28, 28])                        # 4 ciclos
        eventos = []
        for c, dia_ in ((ciclos[0], 1), (ciclos[1], 1), (ciclos[3], 2)):
            eventos.append(Evento(data=c.inicio + timedelta(days=dia_ - 1), tipo="sintoma", valor="colica",
                                  palavras="cólica", trecho="t"))
        for c in ciclos[:2]:
            eventos.append(Evento(data=c.inicio - timedelta(days=2), tipo="sintoma", valor="inchaco", palavras="inchada",
                                  trecho="t"))
        eventos.append(Evento(data=ciclos[2].inicio, tipo="sintoma", valor="nausea", palavras="enjoo", trecho="t"))
        eventos.append(Evento(data=ciclos[1].inicio, tipo="sintoma", valor="colica", palavras="cólica", trecho="t",
                              de_quem="outra"))
        padroes = {p["chave"]: p["texto"] for p in ciclo.padroes_sintomas(ciclos, eventos, REF)}
        self.assertEqual(padroes["colica"], "Cólica em 3 dos últimos 4 ciclos, quase sempre no 1º dia.")
        self.assertEqual(padroes["inchaco"], "Inchada em 2 dos últimos 4 ciclos, sempre 2 dias antes de começar.")
        self.assertNotIn("nausea", padroes)                              # uma vez só não é padrão
        self.assertEqual(ciclo.padroes_sintomas(ciclos[:1], eventos, REF), [])


# ======================================================================== o núcleo (sem HTTP)

class BaseNucleo(unittest.TestCase):
    def setUp(self):
        self.enterContext(recursos.usando(ciclo))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-ciclo-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: REF, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(self.app.bloquear)
        self.token = None
        r = self.pedir("POST", "/api/configurar", {"pin": PIN, "nome_dela": "Julia"})
        self.assertEqual(r.status, 200, r.dados)
        self.token = r.dados["token"]

    @property
    def banco(self) -> Banco:
        return self.app._banco

    def pedir(self, metodo, caminho, corpo=None, token=_PADRAO, **kw) -> nucleo.RespostaApi:
        bruto = None if corpo is None else json.dumps(corpo).encode("utf-8")
        resposta = self.app.tratar(metodo, caminho, corpo=bruto, token=self.token if token is _PADRAO else token, **kw)
        self.assertEqual(json.loads(resposta.corpo.decode("utf-8")), resposta.dados)
        return resposta

    def analisar(self, texto: str, data: str = DATA) -> dict:
        r = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": data})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def registrar(self, texto: str, data: str = DATA, confirmar: bool = True, mudar: dict | None = None,
                  **mais) -> nucleo.RespostaApi:
        p = self.analisar(texto, data)
        escolhidos = [dict(e, **(mudar or {})) for e in p["extras"].get("ciclo", [])] if confirmar else []
        corpo = {"texto": p["texto"], "data_registro": data, "itens": p["itens"], "pessoas": p["pessoas"],
                 "relacoes": p["relacoes"], "extras": {"ciclo": escolhidos} if escolhidos else {}, **mais}
        return self.pedir("POST", "/api/registros", corpo)

    def rid(self, texto: str, data: str = DATA, **kw) -> int:
        r = self.registrar(texto, data, **kw)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def ciclos(self) -> list[dict]:
        return self.banco.consultar("SELECT * FROM ciclos ORDER BY inicio")

    def eventos(self) -> list[dict]:
        return self.banco.consultar("SELECT * FROM ciclo_eventos ORDER BY id")

    def painel(self, data: str = DATA) -> dict:
        r = self.pedir("GET", "/api/ciclo", consulta={"data": data})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def manual(self, inicio: str, fim: str | None = None) -> int:
        r = self.pedir("POST", "/api/ciclo/ciclos", {"inicio": inicio, "fim": fim})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def cinco_ciclos(self) -> None:
        """Inícios a cada 28 dias até 28/09 (o último ainda em andamento; próximo: 24/10 a 28/10)."""
        for n in range(4, -1, -1):
            ini = d("2026-09-28") - timedelta(days=28 * n)
            self.manual(ini.isoformat(), (ini + timedelta(days=4)).isoformat() if n else None)

    def hoje(self, data: str) -> list[dict]:
        r = self.pedir("GET", "/api/hoje", consulta={"data": data})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["alertas"]


class TestGravar(BaseNucleo):
    def test_registro_vira_cadeado_mesmo_desmarcado(self):
        r = self.registrar("Ela menstruou ontem e ta com muita colica.", privado=False)
        self.assertEqual(r.status, 200, r.dados)
        self.assertTrue(r.dados["privado"])
        self.assertEqual(r.dados["avisos"][0], recursos.AVISO_PRIVADO_GRAVADO)
        rid = r.dados["id"]
        self.assertEqual(self.pedir("PATCH", f"/api/registros/{rid}", {"privado": False}).status, 200)
        self.assertIn(rid, nucleo.fora_da_ia(self.banco))            # desmarcar não manda para a IA
        self.assertIs(self.pedir("GET", f"/api/registros/{rid}").dados["fora_da_ia"], True)

    def test_registro_e_ciclo_numa_gravacao_so(self):
        with mock.patch.object(self.app.cofre, "salvar", wraps=self.app.cofre.salvar) as salvar:
            r = self.registrar("Ela ficou menstruada dia 28, ta irritada e com colica. Ela disse que chá ajuda quando ta com colica.")
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(salvar.call_count, 1)                     # registro + ciclo + itens afastados: um arquivo
        self.assertTrue(self.ciclos())

    def test_grava_o_ciclo_e_as_anotacoes(self):
        rid = self.rid("Ela menstruou ontem e ta com muita colica. Ela disse que bolsa de água quente ajuda.")
        [c] = self.ciclos()
        self.assertEqual((c["inicio"], c["fim"], c["exatidao"], c["origem"], c["registro_id"]),
                         ("2026-09-29", None, "dia", "registro", rid))
        evs = [(e["tipo"], e["valor"], e["data"], e["certeza"], e["ciclo_id"]) for e in self.eventos()]
        self.assertEqual(evs, [("inicio", "dia", "2026-09-29", "observado", c["id"]),
                               ("sintoma", "colica", DATA, "observado", c["id"]),
                               ("ajuda", "bolsa de água quente", DATA, "confirmado", c["id"])])

    def test_o_mesmo_inicio_em_dois_registros_e_um_ciclo_so(self):
        self.rid("Ela menstruou hoje.", data="2026-09-29")
        self.rid("Ela ficou menstruada anteontem, segundo ela.", data="2026-09-30")    # 28/09: mais cedo
        [c] = self.ciclos()
        self.assertEqual(c["inicio"], "2026-09-28")
        self.assertEqual(len([e for e in self.eventos() if e["tipo"] == "inicio"]), 2)

    def test_fim_completa_o_ciclo_e_durante_sem_inicio_e_aproximado(self):
        self.rid("Ela menstruou dia 25.")
        rid_fim = self.rid("A menstruação dela acabou ontem.")
        [c] = self.ciclos()
        self.assertEqual((c["fim"], c["fim_registro_id"]), ("2026-09-29", rid_fim))
        self.rid("ela tá naqueles dias", data="2026-08-20")
        aproximado = [c for c in self.ciclos() if c["inicio"] == "2026-08-20"]
        self.assertEqual(aproximado[0]["exatidao"], "aproximada")

    def test_durante_entra_no_ciclo_que_ja_comecou_e_fim_sem_inicio_fica_como_anotacao(self):
        self.rid("Ela menstruou anteontem.")
        self.rid("Ela ta menstruada ainda, com colica.")
        self.assertEqual(len(self.ciclos()), 1)
        self.assertEqual({e["ciclo_id"] for e in self.eventos()}, {self.ciclos()[0]["id"]})
        self.rid("A menstruação dela acabou ontem.", data="2026-07-10")            # sem início anotado
        [a] = self.painel()["anotacoes"]
        self.assertEqual((a["tipo"], a["data"]), ("fim", "2026-07-09"))
        self.assertEqual(len(self.ciclos()), 1)

    def test_a_tela_pode_corrigir_a_data_e_dizer_quando_comecou(self):
        self.rid("Ela menstruou.", mudar={"data": "2026-09-27", "precisao": "dia"})
        self.assertEqual(self.ciclos()[0]["inicio"], "2026-09-27")
        self.rid("ela ta de chico", data="2026-08-31", mudar={"inicio": "2026-08-29"})
        self.assertEqual([c["inicio"] for c in self.ciclos()], ["2026-08-29", "2026-09-27"])

    def test_dado_que_nao_serve_400_e_nada_gravado(self):
        casos = {
            "data depois do registro": ({"data": "2026-10-05"}, ciclo.DATA_DEPOIS_DO_REGISTRO),
            "data longe demais": ({"data": "2024-01-01"}, ciclo.DATA_LONGE_DO_REGISTRO),
            "data estranha": ({"data": "ontem"}, ciclo.DATA_INVALIDA),
            "fluxo estranho": ({"fluxo": "muito"}, ciclo.FLUXO_INVALIDO),
            "sintoma inventado": ({"sintomas": [{"chave": "gripe", "palavras": "gripe"}]}, ciclo.SINTOMAS_INVALIDOS),
            "certeza estranha": ({"certeza": "certeza"}, ciclo.CERTEZA_INVALIDA),
        }
        for nome, (mudar, mensagem) in casos.items():
            with self.subTest(caso=nome):
                r = self.registrar("Ela menstruou ontem e ta com colica.", mudar=mudar)
                self.assertEqual((r.status, r.dados["erro"]), (400, mensagem))
        r = self.registrar("Quando ela está com cólica, bolsa de água quente ajuda.", mudar={"polaridade": 3})
        self.assertEqual(r.status, 400)
        self.assertEqual((self.ciclos(), self.eventos(), self.banco.registros()), ([], [], []))

    def test_itens_da_mesma_frase_saem_de_como_ela_esta_e_do_hoje(self):
        rid = self.rid("Ela ficou menstruada dia 28, ta irritada e com os seios sensiveis. Ela tá animada com a viagem.")
        itens = {i["resumo"]: i["status"] for i in self.pedir("GET", f"/api/registros/{rid}").dados["itens"]}
        self.assertEqual(itens.get("Está irritada"), "descartado")       # continua no registro
        como = perfil.conhecendo_ela(self.banco, REF)["como_ela_esta"]
        texto = json.dumps(como, ensure_ascii=False, default=str)
        self.assertNotIn("menstruada", texto)
        self.assertNotIn("irritada", texto)
        self.assertIn("animada", texto)                                 # a outra frase continua valendo
        pagina = self.pedir("GET", "/api/hoje").dados
        # "Últimos registros" (do núcleo) mostra o começo do texto; alertas e "Coisas para lembrar" não.
        hoje = json.dumps({k: pagina[k] for k in ("alertas", "proximos", "lembrar")}, ensure_ascii=False)
        self.assertNotIn("irritada", hoje)
        self.assertNotIn("menstru", hoje)
        self.assertIn("animada", hoje)

    def test_sem_confirmar_nada_fica_so_o_registro_mas_fora_da_ia(self):
        rid = self.rid("Ela menstruou ontem e ta com muita colica.", confirmar=False)
        self.assertEqual((self.ciclos(), self.eventos()), ([], []))
        self.assertIn(rid, nucleo.fora_da_ia(self.banco))               # o texto fala do ciclo
        # E o humor da frase não aparece nas telas centrais, mesmo sem confirmar (seção 20.10).
        outro = self.rid("Ela menstruou ontem e tá muito irritada.", confirmar=False)
        self.assertIn(outro, ciclo.discretos(self.banco))
        perfil_ = json.dumps(self.pedir("GET", "/api/perfil").dados, ensure_ascii=False)
        pagina = self.pedir("GET", "/api/hoje").dados
        hoje = json.dumps({k: pagina[k] for k in ("alertas", "proximos", "lembrar", "ultimos")}, ensure_ascii=False)
        for texto in (perfil_, hoje):
            self.assertNotIn("irritada", texto)
            self.assertNotIn("menstru", texto)


    def test_outra_pessoa_guarda_so_o_comentario(self):
        rid = self.rid("A irmã dela menstruou pela primeira vez.")
        self.assertEqual(self.ciclos(), [])
        self.assertEqual([(e["tipo"], e["de_quem"]) for e in self.eventos()], [("mencao", "outra")])
        self.assertIn(rid, nucleo.fora_da_ia(self.banco))
        p = self.painel()
        self.assertFalse(p["tem_dados"])
        self.assertEqual(p["anotacoes"], [])

    def test_erro_ao_gravar_nao_perde_o_registro_e_nao_loga_conteudo(self):
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        with mock.patch.object(ciclo, "_gravar_um", side_effect=RuntimeError("ela menstruou ontem")):
            r = self.registrar("Ela menstruou ontem.")
        self.assertEqual(r.status, 200, r.dados)
        self.assertIn(recursos.AVISO_GRAVAR.format(rotulo="Ciclo"), r.dados["avisos"])
        self.assertTrue(self.banco.registro(r.dados["id"])["privado"])
        self.assertTrue(any("RuntimeError" in m for m in coletor.mensagens))
        self.assertFalse(any("menstru" in m for m in coletor.mensagens))
        historico = json.dumps(self.banco.historico(), ensure_ascii=False, default=str)
        self.assertNotIn("menstru", historico)


# ======================================================================== o cartão (GET /api/ciclo)

class TestPainel(BaseNucleo):
    def test_data_estranha_na_consulta_e_400(self):
        r = self.pedir("GET", "/api/ciclo", consulta={"data": "amanha"})
        self.assertEqual((r.status, r.dados["erro"]), (400, ciclo.DATA_INVALIDA))

    def test_vazio_acolhedor(self):
        p = self.painel()
        self.assertFalse(p["tem_dados"])
        self.assertEqual(p["aviso_consentimento"], ciclo.AVISO_CONSENTIMENTO)
        self.assertIn("ela sabe e concorda", p["aviso_consentimento"])
        self.assertIsNone(p["previsao"])

    def test_tudo_junto_e_citando_a_origem(self):
        self.cinco_ciclos()
        rid = self.rid("Ela disse que quando ta com colica, bolsa de água quente ajuda.", data="2026-09-29")
        p = self.painel()
        self.assertEqual(p["previsao"]["texto"], "Próximo início: entre 24/10 e 28/10 (mais provável 26/10).")
        self.assertEqual(p["duracao"]["media"], 5)
        self.assertEqual(p["fertil"]["aviso"], AVISO_DA_SECAO)
        [a] = p["ajudas"]
        self.assertEqual((a["valor"], a["certeza"], a["registro_id"], a["polaridade"]), ("bolsa de água quente", "confirmado", rid, 1))
        self.assertIn(a["trecho"], self.banco.registro(rid)["texto"])
        self.assertEqual([h["inicio"] for h in p["historico"]][:2], ["2026-09-28", "2026-08-31"])
        self.assertEqual(p["historico"][0]["intervalo"], 28)

    def test_no_momento_lembra_o_padrao_do_dia(self):
        self.cinco_ciclos()
        for n in range(1, 4):                                           # cólica no 1º dia de 3 ciclos
            dia_ = d("2026-09-28") - timedelta(days=28 * n)
            self.rid("Ela ta com cólica.", data=dia_.isoformat())        # o padrão usa as palavras dela
        self.assertEqual(self.painel("2026-09-28")["no_momento"],
                         ["Hoje é o 1º dia. Cólica em 3 dos últimos 4 ciclos, sempre no 1º dia."])
        self.assertEqual(self.painel("2026-09-30")["no_momento"], [])

    def test_ajuda_com_os_dois_lados_fica_incerta(self):
        self.rid("Ela disse que quando ta com colica, chá ajuda.", data="2026-09-01")
        self.rid("Quando ela ta com colica, chá não ajuda.", data="2026-09-20")
        [a] = self.painel()["ajudas"]
        self.assertEqual((a["certeza"], a["vezes"]), ("incerto", 2))
        self.assertIn("ajuda e de que não ajuda", a["motivo"])

    def test_planejamento_so_com_algo_que_ela_disse(self):
        self.cinco_ciclos()
        self.rid("A apresentação dela é dia 25 de outubro.")
        self.assertEqual(self.painel()["planejamento"], [])               # nada dito por ela ainda
        self.rid("Ela disse que quando ta com colica, bolsa de água quente ajuda.")
        [pl] = self.painel()["planejamento"]
        self.assertEqual(pl["data"], "2026-10-25")
        self.assertIn("cai perto dos dias previstos (24/10 a 28/10)", pl["texto"])
        self.assertTrue(pl["texto"].endswith("Talvez valha ter isso em mente ao planejar."))
        self.assertIn("Ela disse em 30/09 que ajuda: bolsa de água quente.", pl["lembrar"])
        self.assertNotIn("planejamento", json.dumps(self.pedir("GET", "/api/hoje", consulta={"data": "2026-10-23"}).dados))

    def test_nada_de_diagnostico_nem_explicacao_de_humor(self):
        self.cinco_ciclos()
        self.rid("Ela menstruou dia 28 e ta irritada, triste e com colica.", data="2026-09-30")
        p = self.painel()
        texto = plano(json.dumps({k: v for k, v in p.items() if k != "fertil"}, ensure_ascii=False))
        self.assertIsNone(DIAGNOSTICO.search(texto), DIAGNOSTICO.search(texto))
        self.assertNotIn("humor", texto)
        nomes = [x["texto"] for x in p["padroes"]] + [x["palavras"] for x in p["sintomas"]]
        self.assertEqual(nomes, ["colica"])                              # humor nunca vira sintoma nem padrão


# ======================================================================== Hoje (neutro)

class TestHoje(BaseNucleo):
    def cartoes(self, data: str) -> list[dict]:
        return [a for a in self.hoje(data) if a.get("recurso") == "ciclo"]

    def test_um_lembrete_1_ou_2_dias_antes_da_faixa(self):
        self.cinco_ciclos()                                               # faixa: 24/10 a 28/10
        quantos = {dia_: len(self.cartoes(dia_)) for dia_ in ("2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24")}
        self.assertEqual(quantos, {"2026-10-21": 0, "2026-10-22": 1, "2026-10-23": 1, "2026-10-24": 0})
        [c] = self.cartoes("2026-10-23")
        self.assertEqual((c["nivel"], c["tipo"], c["texto"]), ("lembrete", "ciclo_cuidado", ciclo.TEXTO_HOJE))
        self.assertEqual(c["registro_ids"], [])

    def test_o_hoje_nunca_usa_as_palavras_do_ciclo(self):
        self.cinco_ciclos()
        self.rid("Ela disse que quando ta com colica, bolsa de água quente ajuda.")
        self.rid("Ela disse que quando ta menstruada, remédio pra cólica ajuda.")
        for dia_ in ("2026-10-22", "2026-10-23"):
            pagina = self.pedir("GET", "/api/hoje", consulta={"data": dia_}).dados
            [c] = [a for a in pagina["alertas"] if a.get("recurso") == "ciclo"]
            tudo = plano(json.dumps({k: c[k] for k in ("texto", "porque", "dados", "chave")}, ensure_ascii=False))
            self.assertIsNone(PROIBIDAS_NO_HOJE.search(tudo.replace("ciclo:cuidado", "")), tudo)
            self.assertIn("bolsa de água quente", c["texto"])
            self.assertNotIn("remédio", c["texto"])                         # tem palavra do ciclo: fica de fora
            self.assertTrue(ciclo.sem_palavras_do_ciclo(c["texto"] + c["porque"]))

    def test_sugestao_so_do_que_ela_disse(self):
        self.cinco_ciclos()
        self.rid("Quando ela ta com colica, bolsa de água quente ajuda.")           # você percebeu, ela não disse
        [c] = self.cartoes("2026-10-23")
        self.assertEqual(c["texto"], ciclo.TEXTO_HOJE)                      # sem fala dela, sem sugestão
        self.assertEqual(c["dados"], {"sugestoes": []})
        self.rid("Ela disse que quando ta com colica, chá de camomila ajuda.")
        [c] = self.cartoes("2026-10-23")
        self.assertEqual(c["texto"], ciclo.TEXTO_HOJE + " Talvez ter por perto o que ela disse que ajuda: chá de camomila.")

    def test_sem_previsao_nao_ha_cartao_e_o_fertil_nunca_aparece(self):
        for n in range(2, -1, -1):
            self.manual((d("2026-09-28") - timedelta(days=28 * n)).isoformat())
        self.assertEqual(self.cartoes("2026-10-23"), [])
        self.manual("2026-06-08")
        self.manual("2026-05-11")
        textos = []
        for n in range(0, 40):
            textos += [plano(a["texto"] + a.get("porque", "")) for a in self.hoje((REF + timedelta(days=n)).isoformat())]
        self.assertTrue(textos)
        self.assertFalse(any("fertil" in t or "gravidez" in t or "ovula" in t for t in textos))

    def test_desligar_o_lembrete_e_dispensar(self):
        self.cinco_ciclos()
        [c] = self.cartoes("2026-10-23")
        self.assertEqual(self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]}).status, 200)
        self.assertEqual(self.cartoes("2026-10-23"), [])
        self.pedir("POST", "/api/ciclo/apagar-tudo", {"confirmar": True})      # apagar esquece a chave dispensada
        self.assertFalse(any(k.startswith("ciclo:") for k in self.banco.alertas_dispensados()))
        self.cinco_ciclos()
        self.assertEqual(len(self.cartoes("2026-10-23")), 1)
        self.assertEqual(self.pedir("PATCH", "/api/ciclo/ajustes", {"lembrete_hoje": False}).dados,
                         {"lembrete_hoje": False, "mostrar_fertil": True})
        self.assertEqual(self.cartoes("2026-10-23"), [])


# ======================================================================== IA: nada do ciclo vai para ela

class CadeiaFalsa:
    """No lugar da IA: anota o que receberia (nada sai do computador)."""

    def __init__(self):
        self.analises: list = []
        self.respostas: list = []
        self.contextos: list = []

    def analisar(self, texto, referencia, conhecimento, base):
        self.analises.append(copy.deepcopy(base))
        return base, "principal", []

    def responder(self, pergunta, trechos):
        self.respostas.append(copy.deepcopy(trechos))
        return Resposta(pergunta=pergunta, texto="Achei.", encontrados=list(trechos)), "principal", []

    def sugerir(self, contexto, referencia):
        self.contextos.append(copy.deepcopy(contexto))
        return [], "principal", []


class TestIA(BaseNucleo):
    def setUp(self):
        super().setUp()
        self.banco.salvar_config(ia_principal=IA)
        self.cadeia = CadeiaFalsa()
        self.enterContext(mock.patch.object(motor, "obter_cadeia_ia", lambda ajustes: self.cadeia))
        self.do_ciclo = self.rid("Ela menstruou ontem e ta com colica, coitada. Ela adora tulipas.")
        self.pedir("PATCH", f"/api/registros/{self.do_ciclo}", {"privado": False})
        self.sem_confirmar = self.rid("A menstruação dela veio forte, ela pediu tulipas.", confirmar=False)
        self.pedir("PATCH", f"/api/registros/{self.sem_confirmar}", {"privado": False})
        self.comum = self.rid("Ela adora tulipas amarelas e ficou feliz ontem.")

    def test_fora_da_ia_tem_os_registros_do_ciclo(self):
        self.assertEqual(ciclo.fora_da_ia(self.banco), {self.do_ciclo, self.sem_confirmar})
        self.assertEqual(nucleo.fora_da_ia(self.banco), {self.do_ciclo, self.sem_confirmar})

    def test_busca_com_ia_nao_manda_nada_do_ciclo(self):
        busca = self.pedir("GET", "/api/busca", consulta={"q": "tulipas"}).dados
        privados = {e["registro_id"]: e["privado"] for e in busca["encontrados"]}
        self.assertTrue(privados[self.do_ciclo])
        r = self.pedir("POST", "/api/ia/busca", {"q": "tulipas"})
        self.assertEqual(r.status, 200, r.dados)
        enviado = json.dumps(self.cadeia.respostas, ensure_ascii=False, default=str)
        self.assertNotIn("menstru", enviado)
        self.assertNotIn("colica", plano(enviado))
        self.assertEqual({t["registro_id"] for t in self.cadeia.respostas[-1]}, {self.comum})

    def test_sugestao_com_ia_nao_manda_nada_do_ciclo(self):
        self.assertEqual(self.pedir("POST", "/api/ia/sugestao", {}).status, 200)
        enviado = json.dumps(self.cadeia.contextos, ensure_ascii=False, default=str)
        self.assertNotIn("menstru", enviado)
        self.assertTrue(self.cadeia.contextos[-1])
        self.assertEqual({c["registro_id"] for c in self.cadeia.contextos[-1]}, {self.comum})

    def test_analisar_com_ia_recusa_texto_do_ciclo(self):
        antes = len(self.cadeia.analises)
        r = self.pedir("POST", "/api/ia/analisar", {"texto": "Desceu hoje pra ela, ta com colica.", "data_registro": DATA,
                                                    "privado": False})
        self.assertEqual((r.status, r.dados["erro"]), (403, nucleo.TEXTO_FICA_FORA_DA_IA))
        self.assertEqual(len(self.cadeia.analises), antes)
        r = self.pedir("POST", "/api/ia/analisar", {"texto": "Ela adora girassóis.", "data_registro": DATA})
        self.assertEqual(r.status, 200, r.dados)

    def test_recurso_que_falha_ao_dizer_o_que_fica_fora_bloqueia_a_ia(self):
        with mock.patch.object(ciclo, "fora_da_ia", side_effect=RuntimeError("x")):
            r = self.pedir("POST", "/api/ia/busca", {"q": "tulipas"})
        self.assertEqual((r.status, r.dados["erro"]), (503, nucleo.FORA_DA_IA_INDISPONIVEL))


# ======================================================================== rotas

class TestRotas(BaseNucleo):
    def test_anotar_um_inicio_a_mao_e_o_que_ela_recusa(self):
        cid = self.manual("2026-09-01", "2026-09-05")
        [c] = self.ciclos()
        self.assertEqual((c["id"], c["origem"], c["exatidao"], c["registro_id"]), (cid, "manual", "dia", None))
        casos = {
            "no futuro": ({"inicio": "2026-10-02"}, ciclo.DATA_NO_FUTURO),
            "fim antes": ({"inicio": "2026-08-10", "fim": "2026-08-01"}, ciclo.FIM_ANTES),
            "fim longe": ({"inicio": "2026-08-01", "fim": "2026-08-30"}, ciclo.FIM_LONGE),
            "repetido": ({"inicio": "2026-09-04"}, ciclo.CICLO_REPETIDO.format(data="01/09")),
            "sem início": ({"fim": "2026-08-05"}, ciclo.SEM_INICIO),
            "campo estranho": ({"inicio": "2026-08-01", "nota": "x"}, ciclo.CAMPOS_CICLO),
            "data estranha": ({"inicio": "01/08/2026"}, ciclo.DATA_INVALIDA),
        }
        for nome, (corpo, mensagem) in casos.items():
            with self.subTest(caso=nome):
                r = self.pedir("POST", "/api/ciclo/ciclos", corpo)
                self.assertEqual((r.status, r.dados["erro"]), (400, mensagem))
        self.assertEqual(len(self.ciclos()), 1)

    def test_editar_um_ciclo_que_veio_de_registro_vira_seu(self):
        rid = self.rid("Ela menstruou mes passado.")
        [c] = self.ciclos()
        self.assertEqual((c["exatidao"], c["registro_id"]), ("aproximada", rid))
        self.assertEqual(self.pedir("PATCH", f"/api/ciclo/ciclos/{c['id']}", {"inicio": "2026-08-03", "fim": "2026-08-07"}).status, 200)
        [c] = self.ciclos()
        self.assertEqual((c["inicio"], c["fim"], c["exatidao"], c["origem"], c["registro_id"]),
                         ("2026-08-03", "2026-08-07", "dia", "manual", None))
        self.assertEqual(self.pedir("PATCH", f"/api/ciclo/ciclos/{c['id']}", {"fim": None}).status, 200)
        self.assertIsNone(self.ciclos()[0]["fim"])
        self.assertEqual(self.pedir("PATCH", "/api/ciclo/ciclos/999", {"fim": None}).status, 404)
        self.assertEqual(self.pedir("PATCH", f"/api/ciclo/ciclos/{c['id']}", {"cor": "azul"}).status, 400)

    def test_nao_foi_um_mes_esquecido(self):
        for ini in ("2026-05-08", "2026-06-05", "2026-07-04", "2026-08-31", "2026-09-28"):
            self.manual(ini)
        [q] = self.painel()["perguntas"]
        r = self.pedir("POST", f"/api/ciclo/ciclos/{q['ciclo_id']}/intervalo", {"real": True})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(self.painel()["perguntas"], [])
        self.assertEqual(self.pedir("POST", f"/api/ciclo/ciclos/{q['ciclo_id']}/intervalo", {"real": "sim"}).status, 400)

    def test_apagar_um_ciclo_leva_as_anotacoes_e_limpa_as_copias(self):
        self.rid("Ela menstruou ontem e ta com colica.")
        [c] = self.ciclos()
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/ciclo/ciclos/{c['id']}").status, 200)
        self.assertEqual((self.ciclos(), self.eventos()), ([], []))
        self.assertEqual(len(repetir), 1)
        self.assertTrue(repetir[0](copia))                               # o que o cofre faria em cada cópia
        self.assertEqual(copia.consultar("SELECT * FROM ciclos") + copia.consultar("SELECT * FROM ciclo_eventos"), [])
        self.assertEqual(self.pedir("DELETE", f"/api/ciclo/ciclos/{c['id']}").status, 404)

    def test_apagar_uma_anotacao(self):
        self.rid("Ela menstruou ontem e ta com colica.")
        sintoma = [e for e in self.eventos() if e["tipo"] == "sintoma"][0]
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/ciclo/eventos/{sintoma['id']}").status, 200)
        self.assertEqual([e["tipo"] for e in self.eventos()], ["inicio"])
        self.assertEqual(len(repetir), 1)
        r = self.pedir("DELETE", f"/api/ciclo/eventos/{sintoma['id']}")
        self.assertEqual((r.status, r.dados["erro"]), (404, ciclo.SEM_ANOTACAO))

    def test_apagar_todos_os_dados_do_ciclo(self):
        self.cinco_ciclos()
        rid = self.rid("Ela tá com colica.")
        self.assertEqual([e["tipo"] for e in self.eventos()], ["sintoma"])
        self.pedir("PATCH", "/api/ciclo/ajustes", {"mostrar_fertil": False})
        self.banco.dispensar_alerta("ciclo:cuidado:2026-10-24")
        self.assertEqual(self.pedir("POST", "/api/ciclo/apagar-tudo", {}).status, 400)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("POST", "/api/ciclo/apagar-tudo", {"confirmar": True}).status, 200)
        self.assertEqual((self.ciclos(), self.eventos()), ([], []))
        self.assertEqual(self.banco.consultar("SELECT * FROM ciclo_ajustes"), [])
        self.assertEqual(self.banco.alertas_dispensados(), set())
        self.assertEqual(len(repetir), 1)
        self.assertIsNotNone(self.banco.registro(rid))                   # o diário fica (e fora da IA)
        self.assertIn(rid, nucleo.fora_da_ia(self.banco))
        exportado = json.dumps(self.pedir("GET", "/api/exportar").dados["recursos"]["ciclo"], ensure_ascii=False)
        self.assertNotIn("colica", exportado)

    def test_ajustes_e_rotas_sem_token(self):
        self.assertEqual(self.pedir("PATCH", "/api/ciclo/ajustes", {"lembrete_hoje": "não"}).status, 400)
        self.assertEqual(self.pedir("PATCH", "/api/ciclo/ajustes", {"cor": True}).status, 400)
        self.assertEqual(self.pedir("PATCH", "/api/ciclo/ajustes", {"mostrar_fertil": False}).status, 200)
        self.cinco_ciclos()
        p = self.painel()
        self.assertIsNone(p["fertil"])
        self.assertEqual(p["fertil_motivo"], "Você escolheu não mostrar o período fértil.")
        for metodo, caminho in (("GET", "/api/ciclo"), ("POST", "/api/ciclo/apagar-tudo")):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.pedir(metodo, caminho, {"confirmar": True} if metodo == "POST" else None,
                                            token="errado").status, 401)
        self.assertTrue(self.ciclos())


# ======================================================================== exportar, importar, apagar registro

class TestExportarImportarApagar(BaseNucleo):
    def test_exportar_e_juntar_noutro_diario_sem_duplicar(self):
        rid = self.rid("Ela menstruou ontem e ta com colica. Ela disse que chá ajuda quando ta com colica.")
        self.manual("2026-08-31", "2026-09-04")
        exportado = self.pedir("GET", "/api/exportar").dados
        dados = exportado["recursos"]["ciclo"]
        self.assertEqual(len(dados["ciclos"]), 2)
        self.assertEqual({e["registro_id"] for e in dados["eventos"]}, {rid})
        outra = tempfile.mkdtemp(prefix="entrenos-teste-ciclo-outro-")
        self.addCleanup(shutil.rmtree, outra, True)
        app2 = nucleo.Nucleo(outra, hoje=lambda: REF, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(app2.bloquear)
        token = app2.tratar("POST", "/api/configurar", corpo=json.dumps({"pin": PIN}).encode()).dados["token"]
        app2._banco.criar_registro("Um registro de antes.", REF, [], [], [])          # os ids mudam
        texto = json.dumps(exportado, ensure_ascii=False)
        r = app2.tratar("POST", "/api/importar", corpo=json.dumps({"json": texto, "confirmar": True}).encode(), token=token)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["recursos"]["ciclo"]["novos"], 2 + len(dados["eventos"]))
        novo_rid = app2._banco.consultar("SELECT id FROM registros WHERE texto LIKE 'Ela menstruou%'")[0]["id"]
        self.assertNotEqual(novo_rid, rid)
        self.assertEqual({e["registro_id"] for e in app2._banco.consultar("SELECT * FROM ciclo_eventos")}, {novo_rid})
        self.assertIn(novo_rid, nucleo.fora_da_ia(app2._banco))
        r = app2.tratar("POST", "/api/importar", corpo=json.dumps({"json": texto, "confirmar": True}).encode(), token=token)
        self.assertEqual(r.dados.get("recursos", {}).get("ciclo", {}).get("novos", 0), 0)   # juntar de novo: nada repete
        self.assertEqual(len(app2._banco.consultar("SELECT * FROM ciclos")), 2)

    def test_importar_tolerante(self):
        resumo = ciclo.importar(self.banco, {"ciclos": [{"inicio": "ontem"}, "x", {"inicio": "2026-09-01", "fim": "2026-12-01"},
                                                        {"inicio": "2026-09-01", "fim": "2026-09-05"}],
                                             "eventos": [{"tipo": "inventado", "data": DATA, "trecho": "t"},
                                                         {"tipo": "sintoma", "data": DATA, "trecho": "t", "registro_id": 77}]}, {})
        self.assertEqual((resumo["ciclos"], resumo["eventos"]), (1, 0))   # evento de registro que não veio: fora
        with self.assertRaises(ValueError):
            ciclo.importar(self.banco, ["não é objeto"], {})

    def test_apagar_o_registro_apaga_o_que_veio_dele(self):
        rid = self.rid("Ela menstruou ontem e ta com colica.")
        self.banco.dispensar_alerta("ciclo:cuidado:2026-10-24")
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertEqual((self.ciclos(), self.eventos()), ([], []))
        self.assertEqual(self.banco.alertas_dispensados(), set())
        self.assertNotIn(rid, ciclo.fora_da_ia(self.banco))
        exportado = json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False)
        self.assertNotIn("colica", exportado)

    def test_o_inicio_contado_por_outro_registro_fica_com_a_data_dele(self):
        primeiro = self.rid("Ela menstruou anteontem.")                            # 28/09
        segundo = self.rid("Ela menstruou dia 29.")                                # mesmo ciclo (fica o 28/09)
        self.assertEqual([c["inicio"] for c in self.ciclos()], ["2026-09-28"])
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{primeiro}").status, 200)
        [c] = self.ciclos()
        self.assertEqual((c["inicio"], c["exatidao"], c["registro_id"]), ("2026-09-29", "dia", segundo))
        # "tá no 2º dia" também diz quando começou: vale como início exato.
        a = self.rid("Ela menstruou dia 3.", data="2026-08-05")
        b = self.rid("Ela ta no segundo dia da menstruação.", data="2026-08-05")   # começou em 04/08
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{a}").status, 200)
        agosto = [c for c in self.ciclos() if c["inicio"].startswith("2026-08")]
        self.assertEqual([(c["inicio"], c["exatidao"], c["registro_id"]) for c in agosto], [("2026-08-04", "dia", b)])

    def test_apagar_o_registro_do_fim_tira_o_fim(self):
        self.rid("Ela menstruou dia 25.")
        fim = self.rid("A menstruação dela acabou ontem.")
        self.assertEqual(self.ciclos()[0]["fim"], "2026-09-29")
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{fim}").status, 200)
        [c] = self.ciclos()
        self.assertEqual((c["fim"], c["fim_registro_id"]), (None, None))

    def test_apagar_o_registro_tambem_nas_copias(self):
        rid = self.rid("Ela menstruou ontem e ta com colica.")
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        copia.apagar_registro(rid)                                   # o que o núcleo faz em cada cópia
        self.assertEqual(copia.consultar("SELECT * FROM ciclos") + copia.consultar("SELECT * FROM ciclo_eventos"), [])


# ======================================================================== no navegador (Pyodide)

ROTEIRO = r'''
import json, sys, threading, time, _thread
from datetime import date


def proibido(*_a, **_k):
    raise RuntimeError("no navegador não dá para iniciar thread nem dormir")


threading.Thread.start = proibido
time.sleep = proibido
for nome in ("start_new_thread", "start_joinable_thread", "start_new"):
    if hasattr(_thread, nome):
        setattr(_thread, nome, proibido)
sys.path.insert(0, sys.argv[1])
from entrenos import cofre, ponte

cofre.SCRYPT_N = 2 ** 12
ponte.iniciar_no_navegador(sys.argv[2], hoje=lambda: date(2026, 9, 30))


def pedir(metodo, caminho, corpo=None, token=None):
    return json.loads(ponte.tratar_json(metodo, caminho, None if corpo is None else json.dumps(corpo), token))


passos = {}
token = pedir("POST", "/api/configurar", {"pin": sys.argv[3]})["dados"]["token"]
texto = "Ela menstruou ontem e ta com colica. Ela disse que quando ta com colica, cha ajuda."
p = pedir("POST", "/api/analisar", {"texto": texto, "data_registro": "2026-09-30"}, token)["dados"]
passos["extras"] = [e["tipo"] for e in p["extras"].get("ciclo", [])]
r = pedir("POST", "/api/registros", {"texto": texto, "data_registro": "2026-09-30", "itens": p["itens"],
                                     "pessoas": p["pessoas"], "relacoes": p["relacoes"],
                                     "extras": {"ciclo": p["extras"]["ciclo"]}}, token)
passos["registrar"] = [r["status"], r["dados"].get("privado"), r["gravou"]]
for ini in ("2026-06-05", "2026-07-03", "2026-07-31", "2026-08-29"):
    pedir("POST", "/api/ciclo/ciclos", {"inicio": ini}, token)
r = pedir("GET", "/api/ciclo", token=token)
passos["painel"] = [r["status"], r["dados"]["previsao"]["texto"] if r["dados"]["previsao"] else None]
hoje = pedir("GET", "/api/hoje?data=2026-10-24", token=token)["dados"]
passos["hoje"] = [a["texto"] for a in hoje["alertas"] if a.get("recurso") == "ciclo"]
passos["apagar"] = pedir("POST", "/api/ciclo/apagar-tudo", {"confirmar": True}, token)["status"]
passos["threads"] = threading.active_count()
print(json.dumps(passos, ensure_ascii=False))
'''


class TestNavegador(unittest.TestCase):
    """O ciclo inteiro pela ponte, num Python separado em que iniciar thread e dormir dão erro."""

    def test_ciclo_pela_ponte_sem_thread_nem_sleep(self):
        import subprocess
        import sys
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-ciclo-navegador-")
        self.addCleanup(shutil.rmtree, pasta, True)
        ambiente = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "ENTRENOS_DADOS": pasta, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-c", ROTEIRO, str(RAIZ), pasta, PIN], capture_output=True, timeout=120,
                           cwd=str(RAIZ), env=ambiente)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-3000:])
        passos = json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])
        # "quando ta com colica, cha ajuda" é condição: só o que ajuda (a cólica do dia está na 1ª frase).
        self.assertEqual(passos["extras"], ["ciclo_inicio", "ciclo_ajuda"])
        self.assertEqual(passos["registrar"], [200, True, True])
        # Intervalos 28, 28, 29 e 31 (mediana 28,5): faixa de 27 a 31 dias depois de 29/09.
        self.assertEqual(passos["painel"], [200, "Próximo início: entre 26/10 e 30/10 (mais provável 28/10)."])
        self.assertEqual(passos["hoje"], [ciclo.TEXTO_HOJE + " Talvez ter por perto o que ela disse que ajuda: cha."])
        self.assertEqual((passos["apagar"], passos["threads"]), (200, 1))


# ======================================================================== contrato e tela

class TestContratoETela(unittest.TestCase):
    def test_contrato_do_recurso(self):
        with recursos.usando(ciclo):
            self.assertEqual(recursos.falhas(), {})
            self.assertEqual(recursos.nomes_carregados(), ["ciclo"])
        self.assertEqual(ciclo.ESQUEMA_VERSAO, len(ciclo.ESQUEMA))
        self.assertTrue(all(t.startswith("ciclo_") for t in ciclo.TIPOS_EXTRAS))
        self.assertTrue(ciclo.marca_privado([{"tipo": "ciclo_mencao"}]))
        self.assertFalse(ciclo.marca_privado([]))
        caminhos = {(r.metodo, r.caminho) for r in ciclo.rotas()}
        self.assertIn(("POST", "apagar-tudo"), caminhos)

    def test_banco_novo_tem_as_tabelas_e_passa_na_integridade(self):
        with recursos.usando(ciclo):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            nomes = {l["name"] for l in b.consultar("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertTrue({"ciclos", "ciclo_eventos", "ciclo_ajustes"} <= nomes)
            b.conferir_integridade()
            self.assertEqual(ciclo.fora_da_ia(b), set())
            self.assertEqual(ciclo.cartoes_hoje(b, REF), [])

    def test_tela_se_encaixa_sem_html_nem_estilo_inline(self):
        js = (RAIZ / "web" / "telas" / "ciclo.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("style=", js)
        self.assertNotIn("fetch(", js)                                  # tudo pela api() do app
        for tipo in ciclo.TIPOS_EXTRAS:
            if tipo == ciclo.EXTRA_PLANEJAMENTO:                        # só informação (integração, 20.10)
                self.assertIn("E.registrarRevisaoExtra('ciclo_planejamento', revisaoPlanejamento, { opcional: true })", js)
                continue
            self.assertIn(f"{tipo}: [", js)                             # cada tipo tem o seu desenho
        self.assertIn("E.registrarCartaoHoje('ciclo_cuidado'", js)
        self.assertIn("E.registrarAbaEla('ciclo', '🌙 Ciclo'", js)
        self.assertNotIn("ginecologista", js)                           # o aviso vem do servidor, igual sempre
        cartao = js[js.index("function cartaoCuidado"):js.index("// ================================================================ a aba")]
        visivel = re.sub(r"href: '[^']*'", "", cartao)
        self.assertIsNone(PROIBIDAS_NO_HOJE.search(plano(visivel)), visivel)


if __name__ == "__main__":
    unittest.main()
