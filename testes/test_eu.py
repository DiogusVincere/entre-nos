"""Área 🪞 Eu (DESIGN.md, seção 20.9).

Só este recurso entra na lista (`recursos.usando(eu, ...)`), com módulos FALSOS de `perguntas`,
`promessas`, `marcos` e `ciclo` que criam só as colunas do contrato da seção 20 (os de verdade estão
sendo escritos ao mesmo tempo). Um teste opcional usa o `perguntas` de verdade (pula se ele não abrir).

Cobre a extração por regras (frases de conversa, gírias, sem acento, datas relativas, negação,
dúvida, ironia, terceira pessoa, o que NÃO é da área Eu), a gravação conferida (trecho literal, "só a
sua parte", 🔵 até ela dizer), o check-in escolhido pela semana real, o espelho, o lado a lado com o
que ela disse, a intenção do mês, os desentendimentos, os aprendizados que voltam na revisão e no
Hoje, as cartas, o "cuidar de você", exportar/importar/apagar e os limites de privacidade (nada vai
para a IA, log e histórico sem texto, sem nota nem placar).

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026 (quarta), scrypt pequeno.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import types
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from entrenos import cofre, motor, nucleo, recursos
from entrenos.banco import Banco
from entrenos.modelos import Certeza, Item, Resposta, TipoItem
from entrenos.motor import Conhecimento
from entrenos.recursos import eu
from testes.test_servidor import PIN, REF, Relogio

DATA = REF.isoformat()
RAIZ = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
IA = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": "modelo-de-teste", "tipo": "ollama",
      "chave": None}
_PADRAO = object()

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-eu-env-")
    os.environ["ENTRENOS_DADOS"] = _pasta_env      # garantia: nada cai em %LOCALAPPDATA%
    remendo = mock.patch.object(cofre, "SCRYPT_N", 2 ** 12)
    remendo.start()
    _remendos.append(remendo)


def tearDownModule() -> None:
    for remendo in _remendos:
        remendo.stop()
    _remendos.clear()
    eu.esquecer_diario()
    if _env_antigo is None:
        os.environ.pop("ENTRENOS_DADOS", None)
    else:
        os.environ["ENTRENOS_DADOS"] = _env_antigo
    shutil.rmtree(_pasta_env, ignore_errors=True)


def dia(n: int) -> date:
    return REF + timedelta(days=n)


# ---------------------------------------------------------------- módulos falsos (só o contrato da seção 20)

def falso_perguntas(com_extras: bool = True) -> types.ModuleType:
    """`perguntas_feitas(id, pergunta_id, sugerida_em, status, feita_em, registro_id)` (+ as colunas
    a mais que o recurso de verdade tem, quando `com_extras`)."""
    m = types.ModuleType("testes.falso_perguntas")
    m.NOME = "perguntas"
    extras = (", trilha TEXT, categoria TEXT, pergunta TEXT, resposta TEXT, certeza TEXT, recusou INTEGER NOT NULL DEFAULT 0"
              if com_extras else "")
    m.ESQUEMA = [f"CREATE TABLE perguntas_feitas (id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta_id INTEGER NOT NULL,"
                 f" sugerida_em TEXT, status TEXT NOT NULL, feita_em TEXT, registro_id INTEGER{extras})"]
    m.ESQUEMA_VERSAO = 1
    return m


def falso_promessas() -> types.ModuleType:
    m = types.ModuleType("testes.falso_promessas")
    m.NOME = "promessas"
    m.ESQUEMA = ["CREATE TABLE promessas (id INTEGER PRIMARY KEY AUTOINCREMENT, texto TEXT NOT NULL, prazo TEXT,"
                 " status TEXT NOT NULL, registro_id INTEGER, criado_em TEXT NOT NULL, concluida_em TEXT)"]
    m.ESQUEMA_VERSAO = 1
    return m


def falso_marcos() -> types.ModuleType:
    m = types.ModuleType("testes.falso_marcos")
    m.NOME = "marcos"
    m.ESQUEMA = ["CREATE TABLE marcos (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT NOT NULL, data TEXT NOT NULL,"
                 " tipo TEXT NOT NULL DEFAULT 'outro', descricao TEXT, registro_id INTEGER, criado_em TEXT NOT NULL)"]
    m.ESQUEMA_VERSAO = 1
    return m


def falso_ciclo() -> types.ModuleType:
    """Um ciclo de mentira: deixa fora da IA os registros em `m.ids` (o Eu nunca os usa para explicar)."""
    m = types.ModuleType("testes.falso_ciclo")
    m.NOME = "ciclo"
    m.ids = set()
    m.fora_da_ia = lambda banco: set(m.ids)
    return m


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


def extras(texto: str, referencia: date = REF, nome_dela: str | None = "Julia") -> list[dict]:
    """O que o recurso propõe para o texto, como a revisão recebe (motor + fundação)."""
    with recursos.usando(eu):
        proposta = motor.MotorRegras().analisar(texto, referencia, Conhecimento(pessoas=[], nome_dela=nome_dela))
    return proposta.extras.get("eu", [])


def do_tipo(texto: str, tipo: str, **kw) -> list[dict]:
    return [e for e in extras(texto, **kw) if e["tipo"] == tipo]


# ======================================================================== extração: atitudes

class TestExtrairAtitudes(unittest.TestCase):
    def setUp(self):
        eu.esquecer_diario()

    def uma(self, texto: str, **kw) -> dict:
        achadas = do_tipo(texto, "atitude", **kw)
        self.assertEqual(len(achadas), 1, (texto, achadas))
        e = achadas[0]
        self.assertIn(e["trecho"], texto)                       # nunca inventar: trecho literal
        self.assertIn(e["texto"][1:], e["trecho"])               # e o texto é um pedaço dele
        return e

    def nenhuma(self, texto: str, **kw) -> None:
        self.assertEqual(do_tipo(texto, "atitude", **kw), [], texto)

    def test_levei_cafe_pra_ela(self):
        e = self.uma("Levei café pra ela de manhã porque ela tava cansada.")
        self.assertEqual((e["texto"], e["jeito"], e["data"]), ("Levei café pra ela de manhã", "cuidado", DATA))
        self.assertEqual(e["jeito_nome"], "Cuidado")

    def test_fiquei_com_ela_no_hospital(self):
        e = self.uma("Fiquei com ela no hospital a noite toda.")
        self.assertEqual((e["texto"], e["jeito"]), ("Fiquei com ela no hospital a noite toda", "presenca"))

    def test_escutei_sem_dar_conselho_e_escuta_nao_conselho(self):
        e = self.uma("Escutei ela sem dar conselho.")
        self.assertEqual(e["jeito"], "escuta")

    def test_nome_dela_e_data_relativa(self):
        e = self.uma("ontem fiz uma sopa pra Julia, ela tava gripada")
        self.assertEqual((e["texto"], e["data"], e["data_expressao"]), ("Fiz uma sopa pra Julia", "2026-09-29", "ontem"))

    def test_girias_sem_acento(self):
        casos = {"mano, busquei ela no trampo hj": ("Busquei ela no trampo hj", "ajuda"),
                 "fiz um cafune nela ate ela dormir": ("Fiz um cafune nela ate ela dormir", "carinho"),
                 "deixei ela desabafar tudo, nao falei nada": ("Deixei ela desabafar tudo", "escuta"),
                 "perguntei como foi a entrevista dela": ("Perguntei como foi a entrevista dela", "interesse")}
        for texto, (esperado, jeito) in casos.items():
            with self.subTest(texto=texto):
                e = self.uma(texto)
                self.assertEqual((e["texto"], e["jeito"]), (esperado, jeito))

    def test_dei_conselho_fica_como_conselho(self):
        e = self.uma("Dei conselho de novo e ela ficou chateada.")
        self.assertEqual((e["texto"], e["jeito"]), ("Dei conselho de novo", "conselho"))

    def test_negacao(self):
        for texto in ("Não levei café pra ela hoje.", "nem escutei ela direito", "esqueci de buscar ela no trabalho",
                      "Nunca fiquei com ela no hospital.", "n busquei ela ontem"):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_nao_insisti_e_dar_espaco(self):
        self.assertEqual(self.uma("Ela tava quieta e não insisti.")["jeito"], "espaco")
        self.assertEqual(self.uma("Deixei ela sozinha um pouco, como ela pediu.")["jeito"], "espaco")

    def test_duvida_pergunta_e_ironia(self):
        for texto in ("Será que eu devia ter levado café pra ela?", "Levei café pra ela?", "levei cafe pra ela sqn",
                      "Se eu buscar ela amanhã ela vai gostar."):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_terceira_pessoa_e_futuro_nao_sao_atitudes_suas(self):
        for texto in ("A mãe dela levou café pra ela.", "Ela fez uma sopa pra mim.", "Vou levar café pra ela amanhã.",
                      "Prometi buscar ela no trabalho sexta."):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_presente_comprado_e_do_recurso_presentes(self):
        self.nenhuma("Comprei flores pra ela.")
        self.assertEqual(self.uma("Comprei remédio pra ela na farmácia.")["jeito"], "cuidado")
        self.assertEqual(self.uma("Levei flores pra ela no trabalho.")["jeito"], "carinho")

    def test_tarefa_de_casa_so_quando_fala_dela(self):
        self.nenhuma("Lavei a louça.")
        self.assertEqual(self.uma("Lavei a louça pra ela descansar.")["jeito"], "ajuda")

    def test_duas_atitudes_na_mesma_frase(self):
        achadas = do_tipo("Fiz um chá pra ela e depois fiquei com ela no sofá.", "atitude")
        self.assertEqual([(e["texto"], e["jeito"]) for e in achadas],
                         [("Fiz um chá pra ela", "cuidado"), ("fiquei com ela no sofá".capitalize(), "presenca")])

    def test_dei_um_tempo_nao_e_espaco(self):
        self.nenhuma("A gente deu um tempo no namoro ano passado.")
        self.nenhuma("Dei um tempo das redes sociais.")

    def test_pergunta_profunda_e_do_recurso_perguntas(self):
        self.nenhuma("Perguntei pra ela qual era o maior sonho dela.")


# ======================================================================== extração: o resto

class TestExtrairOutros(unittest.TestCase):
    def setUp(self):
        eu.esquecer_diario()

    def test_desentendimento_com_tema_intensidade_e_a_sua_parte(self):
        texto = "A gente brigou feio ontem por causa da mãe dela. Eu fiquei magoado. Devia ter ouvido mais."
        [r] = do_tipo(texto, "reparo")
        self.assertEqual((r["tema"], r["intensidade"], r["eu_senti"], r["faria_diferente"], r["data"]),
                         ("a mãe dela", 3, "Fiquei magoado", "Devia ter ouvido mais", "2026-09-29"))
        self.assertEqual(r["aconteceu"], "A gente brigou feio ontem por causa da mãe dela")
        self.assertIn(r["trecho"], texto)
        self.assertNotIn("ela_errou", r)                        # só a sua parte

    def test_intensidade_pelas_palavras_do_texto(self):
        casos = {"A gente discutiu ontem, seria bom conversar com calma.": 2,
                 "Brigamos e eu gritei, me arrependi.": 3,
                 "Discutimos de novo, sério?! Dessa vez foi pesado.": 3,
                 "Ficou um clima estranho entre a gente no jantar.": 1}
        for texto, intensidade in casos.items():
            with self.subTest(texto=texto):
                [r] = do_tipo(texto, "reparo")
                self.assertEqual(r["intensidade"], intensidade)

    def test_briguinha_leve_e_pazes(self):
        [r] = do_tipo("Rolou uma briguinha boba entre a gente sobre a louça. Pedi desculpas e conversamos depois.", "reparo")
        self.assertEqual((r["intensidade"], r["tema"], r["reparo"], r["conversamos"]),
                         (1, "a louça", "Pedi desculpas e conversamos depois", True))

    def test_o_que_ela_sentiu_tem_a_certeza_da_frase(self):
        casos = {"Discutimos ontem. Ela disse que se sentiu sozinha.": ("Ela disse que se sentiu sozinha", "confirmado"),
                 "A gente discutiu e ela ficou chateada.": ("ela ficou chateada", "observado"),
                 "Brigamos de novo. Ela parecia magoada.": ("Ela parecia magoada", "inferencia")}
        for texto, (sentiu, certeza) in casos.items():
            with self.subTest(texto=texto):
                [r] = do_tipo(texto, "reparo")
                self.assertEqual((r["ela_pode_ter_sentido"], r["ela_certeza"]), (sentiu, certeza))

    def test_o_que_nao_e_desentendimento_de_voces(self):
        for texto in ("A mãe dela brigou com a irmã.", "Discutimos o orçamento da viagem.", "nao brigamos, foi so um mal entendido",
                      "A gente nunca briga.", "Ela discutiu com o chefe hoje.", "Será que a gente brigou à toa?"):
            with self.subTest(texto=texto):
                self.assertEqual(do_tipo(texto, "reparo"), [])

    def test_aprendizado_com_gatilho(self):
        [a] = do_tipo("Aprendi que quando ela chega cansada, o melhor é deixar ela descansar.", "aprendizado")
        self.assertEqual(a["texto"], "Quando ela chega cansada, o melhor é deixar ela descansar")
        self.assertEqual(a["gatilhos"]["estados"], ["cansad"])

    def test_da_proxima_vez_e_nota_mental(self):
        self.assertEqual(do_tipo("Da próxima vez vou perguntar antes de opinar.", "aprendizado")[0]["texto"],
                         "Da próxima vez vou perguntar antes de opinar")
        self.assertEqual(do_tipo("Nota mental: não marcar nada cedo no domingo.", "aprendizado")[0]["texto"],
                         "Não marcar nada cedo no domingo")

    def test_da_proxima_vez_que_e_aprendi_com_ela(self):
        # Revisão de 07/10: o jeito mais natural de escrever um aprendizado com gatilho não era reconhecido.
        [a] = do_tipo("Da próxima vez que ela estiver nervosa, vou só abraçar ela e não falar nada.", "aprendizado")
        self.assertEqual(a["gatilhos"]["estados"], ["nervos"])
        self.assertIn("ela nervosa", a["gatilhos_rotulos"])
        [a] = do_tipo("Da proxima vez q ela tiver triste, vou fazer um chocolate quente pra ela.", "aprendizado")
        self.assertEqual(a["gatilhos"]["estados"], ["trist"])
        self.assertEqual(len(do_tipo("Próxima vez que ela chorar, eu só vou ficar perto.", "aprendizado")), 1)
        self.assertEqual(do_tipo("Aprendi com ela que silêncio também é carinho.", "aprendizado")[0]["texto"],
                         "Silêncio também é carinho")
        # "Aprendi a" só quando é sobre ela (fazer lasanha não é).
        self.assertEqual(do_tipo("Aprendi a fazer lasanha.", "aprendizado"), [])
        self.assertEqual(len(do_tipo("Aprendi a não interromper ela quando ela tá contando algo.", "aprendizado")), 1)

    def test_gatilho_sem_risada_nem_verbo_e_com_a_condicao_de_antes(self):
        [a] = do_tipo("Quando o assunto for a mãe dela, aprendi que é melhor ouvir kkk.", "aprendizado")
        self.assertEqual(a["gatilhos"]["termos"], ["mae"])
        self.assertNotIn("kkk", eu.gatilhos("Quando ela tá triste kkk, melhor ouvir")["termos"])
        self.assertNotIn("for", eu.gatilhos("Quando o assunto for trabalho, melhor ouvir")["termos"])

    def test_observacao_sobre_ela_nao_e_aprendizado_seu(self):
        for texto in ("Percebi que ela odeia o chefe.", "Aprendi que ela gosta de tulipas.", "Não aprendi nada com isso.",
                      "Descobri que ela tem um irmão."):
            with self.subTest(texto=texto):
                self.assertEqual(do_tipo(texto, "aprendizado"), [])

    def test_admiracoes(self):
        casos = {"Admiro muito a coragem dela.": "Admiro muito a coragem dela",
                 "Tenho muito orgulho dela por ter passado na prova.": "Tenho muito orgulho dela por ter passado na prova",
                 "Ela é tão forte, sério.": "Ela é tão forte",
                 "eu admiro como ela cuida da avó": "Admiro como ela cuida da avó"}
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                [a] = do_tipo(texto, "admiracao")
                self.assertEqual(a["texto"], esperado)

    def test_o_que_nao_e_admiracao(self):
        for texto in ("Ela é muito ansiosa.", "Não admiro isso nela.", "A mãe dela é muito forte.",
                      "Eu admiro meu pai.", "Admiro a mãe dela.", "Admiro muito o meu chefe."):
            with self.subTest(texto=texto):
                self.assertEqual(do_tipo(texto, "admiracao"), [])

    def test_todo_trecho_e_literal_e_todo_extra_deixa_fora_da_ia(self):
        textos = ["Levei café pra ela. A gente brigou por causa da viagem. Admiro a paciência dela.",
                  "aprendi que quando ela ta estressada eu preciso so ouvir", "Fiquei do lado dela no velório da avó."]
        for texto in textos:
            for e in extras(texto):
                with self.subTest(texto=texto, tipo=e["tipo"]):
                    self.assertIn(e["trecho"], texto)
                    self.assertTrue(e.get("_privado"))            # a fundação marcou: fica 🔒

    def test_marca_privado(self):
        for tipo in ("atitude", "aprendizado", "reparo", "admiracao", "desconhecido"):
            self.assertTrue(eu.marca_privado([{"tipo": tipo}]), tipo)
        self.assertFalse(eu.marca_privado([{"tipo": "aprendizado_lembrete"}]))

    def test_texto_sem_nada_da_area_eu(self):
        with recursos.usando(eu):
            p = motor.MotorRegras().analisar("Ela adora tulipas amarelas e vai ter prova sexta.", REF, Conhecimento())
        self.assertEqual(p.extras.get("eu", []), [])
        self.assertNotIn(recursos.AVISO_PRIVADO, p.avisos)


# ======================================================================== ajudantes puros

class TestAjudantes(unittest.TestCase):
    def test_semanas_e_meses(self):
        self.assertEqual(eu.semana(REF), "2026-W40")
        self.assertEqual(eu.limites("2026-W40"), ("semana", date(2026, 9, 28), date(2026, 10, 4)))
        self.assertEqual(eu.limites("2026-02"), ("mes", date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(eu.semana_do_checkin(REF), "2026-W39")              # quarta: a semana que passou
        self.assertEqual(eu.semana_do_checkin(dia(2)), "2026-W40")           # sexta: a que está acabando
        self.assertEqual((eu.mes_da_reflexao(REF), eu.mes_da_reflexao(date(2026, 10, 7))), ("2026-09", "2026-09"))
        self.assertEqual(eu.rotulo_periodo("2026-W40"), "semana de 28/09 a 04/10")
        self.assertEqual(eu.rotulo_periodo("2026-10"), "outubro de 2026")
        self.assertEqual(eu._semanas_do_mes("2026-10"), ["2026-W40", "2026-W41", "2026-W42", "2026-W43", "2026-W44"])
        for ruim in ("2026-W60", "2026-13", "semana passada", None, "1999-01"):
            with self.subTest(ruim=ruim):
                with self.assertRaises(ValueError):
                    eu.limites(ruim)

    def test_gatilhos(self):
        self.assertEqual(eu.gatilhos("Quando ela chega cansada, o melhor é deixar ela descansar")["estados"], ["cansad"])
        g = eu.gatilhos("Quando o assunto é a mãe dela, melhor só ouvir")
        self.assertEqual((g["estados"], g["termos"]), ([], ["mae"]))
        self.assertIn("Família", g["categorias"])

    def test_gatilho_volta_so_com_ela_no_estado(self):
        # Revisão de 07/10: negação, outra pessoa e "termo + categoria" sem o estado traziam o aprendizado.
        g = eu.gatilhos("Aprendi que quando ela chega cansada do trabalho é melhor não puxar assunto pesado.")
        self.assertEqual((g["estados"], g["termos"]), (["cansad"], ["trabalho"]))
        for texto in ("Ela não tá cansada hoje, tá ótima.", "Mãe dela chegou cansada da viagem.",
                      "Eu cheguei cansado do trabalho.", "Ela foi promovida no trabalho!! Muito feliz."):
            with self.subTest(texto=texto):
                self.assertEqual(eu.casar_gatilhos(g, texto), (0, [], None))
        pontos, motivos, _ = eu.casar_gatilhos(g, "Ela chegou exausta do trabalho hoje.")
        self.assertGreaterEqual(pontos, eu.LIMIAR_GATILHO)
        self.assertIn("ela está exausta", motivos)                       # sinônimo, com a palavra do registro
        self.assertGreaterEqual(eu.casar_gatilhos(g, "Chegou cansada do trabalho e foi dormir.")[0], eu.LIMIAR_GATILHO)

    def test_casar_gatilhos_e_dela_e_explicavel(self):

        g = eu.gatilhos("Quando ela chega cansada, o melhor é deixar ela descansar")
        pontos, motivos, pos = eu.casar_gatilhos(g, "Hoje ela chegou super cansada do trampo.")
        self.assertEqual((pontos, motivos, pos), (3, ["ela está cansada"], 0))
        self.assertEqual(eu.casar_gatilhos(g, "Eu tava cansado hoje.")[0], 0)            # é você, não ela
        self.assertEqual(eu.casar_gatilhos(g, "A gente foi ao cinema.")[0], 0)

    def test_jeitos_do_que_ela_disse(self):
        pos, neg = eu.jeitos_do_apoio("Ela disse que prefere que eu nao tente resolver, so que eu escute.", -1)
        self.assertEqual((pos, neg), ({"escuta"}, {"conselho"}))
        self.assertEqual(eu.jeitos_do_apoio("Ela me disse que o que ajuda e ficar perto dela.", 1), ({"presenca"}, set()))
        self.assertEqual(eu.jeitos_do_apoio("Ela acha que tudo bem.", 1), (set(), set()))     # "acha" não é chá

    def test_combina_e_descompasso_so_com_o_que_ela_disse_antes(self):
        apoio = {"id": 1, "registro_id": 1, "trecho": "Ela disse que prefere que eu nao tente resolver, so que eu escute.",
                 "resumo": "x", "certeza": "confirmado", "_data": dia(-5), "_pos": {"escuta"}, "_neg": {"conselho"},
                 "_termos": set()}
        self.assertEqual(len(eu.combinacoes({"jeito": "escuta", "texto": "Escutei ela", "data": DATA}, [apoio])[0]), 1)
        self.assertEqual(len(eu.combinacoes({"jeito": "conselho", "texto": "Dei conselho", "data": DATA}, [apoio])[1]), 1)
        antes = {"jeito": "escuta", "texto": "Escutei ela", "data": dia(-10).isoformat()}
        self.assertEqual(eu.combinacoes(antes, [apoio]), ([], []))


# ======================================================================== núcleo

class BaseNucleo(unittest.TestCase):
    modulos_extras: tuple = ()

    def setUp(self):
        eu.esquecer_diario()
        self.addCleanup(eu.esquecer_diario)
        self.f_perguntas, self.f_promessas, self.f_marcos, self.f_ciclo = (
            falso_perguntas(), falso_promessas(), falso_marcos(), falso_ciclo())
        self.enterContext(recursos.usando(eu, self.f_perguntas, self.f_promessas, self.f_marcos, self.f_ciclo))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-eu-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.dia = REF
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: self.dia, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(self.app.bloquear)
        self.token = None
        r = self.pedir("POST", "/api/configurar", {"pin": PIN, "nome_dela": "Julia", "aniversario_dela": "1998-10-10",
                                                   "inicio_namoro": "2024-03-12"})
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

    def ok(self, metodo, caminho, corpo=None, **kw):
        r = self.pedir(metodo, caminho, corpo, **kw)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def analisar(self, texto: str, data: str = DATA) -> dict:
        return self.ok("POST", "/api/analisar", {"texto": texto, "data_registro": data})

    def registrar(self, texto: str, data: str = DATA, confirmar=("atitude", "aprendizado", "reparo", "admiracao",
                                                                 "aprendizado_lembrete"),
                  mudar=None, **mais) -> nucleo.RespostaApi:
        p = self.analisar(texto, data)
        escolhidos = [dict(e) for e in p["extras"].get("eu", []) if e["tipo"] in confirmar]
        for e in escolhidos:
            e.update(mudar or {})
        corpo = {"texto": p["texto"], "data_registro": data, "itens": p["itens"], "pessoas": p["pessoas"],
                 "relacoes": p["relacoes"], "extras": {"eu": escolhidos} if escolhidos else {}, **mais}
        return self.pedir("POST", "/api/registros", corpo)

    def rid(self, texto: str, data: str = DATA, **kw) -> int:
        r = self.registrar(texto, data, **kw)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def linhas(self, tabela: str) -> list[dict]:
        return self.banco.consultar(f"SELECT * FROM {tabela} ORDER BY id")

    def hoje(self, data: date = REF) -> list[dict]:
        r = self.ok("GET", "/api/hoje", consulta={"data": data.isoformat()})
        return [a for a in r["alertas"] if a.get("recurso") == "eu"]

    def cartoes(self, data: date = REF) -> list[dict]:
        """Os cartões daqui antes do anti-excesso central (que deixa passar só 1 sugestão)."""
        return eu.cartoes_hoje(self.banco, data)

    def estado(self, texto: str, quando: date, polaridade: int = -1, importancia: int = 2, pessoa=None,
               certeza: Certeza = Certeza.OBSERVADO) -> int:
        alvo = texto.split()[-1].strip(".")
        return self.banco.criar_registro(texto, quando, [Item(
            tipo=TipoItem.ESTADO, resumo=texto.rstrip("."), trecho=texto, certeza=certeza,
            categorias=["Preocupações"] if polaridade < 0 else ["Coisas que deixam feliz"], data_evento=quando,
            polaridade=polaridade, importancia=importancia, alvo=alvo, pessoa=pessoa)], [], [])

    def promessa(self, texto: str, prazo: str | None = None, status: str = "aberta", criado: date = REF,
                 concluida: date | None = None, registro_id: int | None = None) -> int:
        return self.banco.inserir("promessas", {"texto": texto, "prazo": prazo, "status": status, "registro_id": registro_id,
                                                "criado_em": f"{criado.isoformat()}T10:00:00", "concluida_em": concluida})

    def pergunta_feita(self, pergunta_id: int, feita: date, registro_id: int | None = None, **mais) -> int:
        return self.banco.inserir("perguntas_feitas", {"pergunta_id": pergunta_id, "status": "feita", "feita_em": feita,
                                                       "registro_id": registro_id, **mais})


class TestGravar(BaseNucleo):
    def test_atitude_confirmada_grava_e_deixa_o_registro_fora_da_ia(self):
        r = self.registrar("Levei café pra ela de manhã porque ela tava cansada.")
        self.assertEqual(r.status, 200, r.dados)
        self.assertTrue(r.dados["privado"])
        self.assertEqual(r.dados["avisos"][0], recursos.AVISO_PRIVADO_GRAVADO)
        [a] = self.linhas("atitudes")
        self.assertEqual((a["texto"], a["jeito"], a["data"], a["registro_id"], a["trecho"]),
                         ("Levei café pra ela de manhã", "cuidado", DATA, r.dados["id"],
                          "Levei café pra ela de manhã porque ela tava cansada."))
        self.assertTrue(self.ok("GET", f"/api/registros/{r.dados['id']}")["fora_da_ia"])

    def test_sem_confirmar_nada_e_gravado_e_o_registro_continua_livre(self):
        r = self.registrar("Levei café pra ela de manhã.", confirmar=())
        self.assertNotIn("privado", r.dados)
        self.assertEqual(self.linhas("atitudes"), [])
        self.assertFalse(self.ok("GET", f"/api/registros/{r.dados['id']}")["fora_da_ia"])

    def test_texto_inventado_pela_tela_nao_entra(self):
        self.registrar("Levei café pra ela de manhã.", mudar={"texto": "Levei ela pra Paris", "resumo": "Levei ela pra Paris"})
        self.assertEqual(self.linhas("atitudes")[0]["texto"], "Levei café pra ela de manhã")

    def test_desentendimento_so_a_sua_parte(self):
        texto = "A gente brigou feio ontem por causa da mãe dela. Eu fiquei magoado."
        self.rid(texto, mudar={"tema": "a família dela", "ela_pode_ter_sentido": "Ela se sentiu cobrada"})
        [r] = self.linhas("reparos")
        self.assertEqual((r["aconteceu"], r["tema"], r["intensidade"], r["eu_senti"], r["data"]),
                         ("A gente brigou feio ontem por causa da mãe dela", "a família dela", 3, "Fiquei magoado", "2026-09-29"))
        self.assertEqual((r["ela_pode_ter_sentido"], r["ela_certeza"]), ("Ela se sentiu cobrada", "inferencia"))   # 🔵
        ruim = self.registrar("Discutimos ontem.", mudar={"ela_errou": "tudo"})
        self.assertEqual((ruim.status, ruim.dados["erro"]), (400, eu.SO_A_SUA_PARTE))
        self.assertEqual(len(self.linhas("reparos")), 1)
        self.assertEqual(len(self.ok("GET", "/api/registros")), 1)                     # nada foi gravado

    def test_o_que_ela_disse_que_sentiu_fica_verde(self):
        self.rid("Discutimos ontem. Ela disse que se sentiu sozinha.")
        self.assertEqual(self.linhas("reparos")[0]["ela_certeza"], "confirmado")

    def test_o_que_voce_viu_nela_fica_amarelo_e_nunca_sobe_sozinho(self):
        self.rid("A gente discutiu e ela ficou chateada.")
        self.rid("Brigamos de novo. Ela parecia magoada.", data="2026-09-29")
        self.assertEqual([r["ela_certeza"] for r in self.linhas("reparos")], ["observado", "inferencia"])

    def test_intensidade_que_nao_e_numero_e_400(self):
        r = self.registrar("A gente brigou ontem.", mudar={"intensidade": "forte"})
        self.assertEqual((r.status, r.dados["erro"]), (400, eu.INTENSIDADE_INVALIDA))

    def test_aprendizado_e_admiracao(self):
        self.rid("Aprendi que quando ela chega cansada, o melhor é deixar ela descansar. Admiro muito a paciência dela.")
        [a] = self.linhas("aprendizados")
        self.assertEqual((a["texto"], json.loads(a["gatilhos"])["estados"]),
                         ("Quando ela chega cansada, o melhor é deixar ela descansar", ["cansad"]))
        self.assertEqual(self.linhas("admiracoes")[0]["texto"], "Admiro muito a paciência dela")

    def test_aprendizado_volta_na_revisao_e_nao_deixa_o_registro_fora_da_ia(self):
        self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela chega cansada, o melhor é deixar ela descansar"})
        p = self.analisar("Hoje ela chegou muito cansada do trabalho.")
        [l] = [e for e in p["extras"]["eu"] if e["tipo"] == "aprendizado_lembrete"]
        self.assertEqual((l["aprendizado"], l["trecho"], l["motivos"][0]),
                         ("Quando ela chega cansada, o melhor é deixar ela descansar",
                          "Hoje ela chegou muito cansada do trabalho.", "ela está cansada"))
        self.assertNotIn("_privado", l)
        r = self.registrar("Hoje ela chegou muito cansada do trabalho.", data="2026-10-01")
        self.assertNotIn("privado", r.dados)
        self.assertEqual(self.linhas("aprendizados")[0]["mostrado_em"], "2026-10-01")

    def test_a_revisao_le_o_diario_pelo_conhecimento(self):
        # Desde a integração o núcleo passa a leitura do diário no `conhecimento` (20.0.1): o lembrete
        # volta na revisão mesmo sem o Hoje ou uma rota daqui terem rodado nesta sessão.
        self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela chega cansada, o melhor é deixar ela descansar"})
        eu.esquecer_diario()
        p = self.analisar("Hoje ela chegou muito cansada do trabalho.")
        self.assertEqual([e["tipo"] for e in p["extras"]["eu"]], ["aprendizado_lembrete"])

    def test_sem_o_diario_aberto_a_revisao_segue_sem_lembrete(self):
        self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela chega cansada, o melhor é deixar ela descansar"})
        eu.esquecer_diario()
        # O motor chamado sem diário (sem `consultar` no conhecimento) e sem a referência fraca: nada.
        self.assertEqual(eu._lembretes("Ela chegou cansada.", eu._frases("Ela chegou cansada."), [], set()), [])
        self.app.bloquear()                                     # bloqueou: o diário fechou
        self.assertEqual(eu._lembretes("Ela chegou cansada.", eu._frases("Ela chegou cansada."), [], set()), [])

        def fechado(*_a, **_k):
            raise RuntimeError("diário fechado")
        self.assertEqual(eu._lembretes("Ela chegou cansada.", eu._frases("Ela chegou cansada."), [], set(), fechado), [])


class TestCheckin(BaseNucleo):
    def test_sem_gancho_coisa_boa_e_faria_diferente(self):
        v = self.ok("GET", "/api/eu/checkin")
        self.assertEqual((v["periodo"], v["rotulo"]), ("2026-W39", "semana de 21/09 a 27/09"))
        self.assertEqual([p["id"] for p in v["perguntas"]], ["coisa_boa", "faria_diferente"])
        self.assertEqual([e["emoji"] for e in v["escala"]], ["😶‍🌫️", "😕", "🙂", "😊", "🥰"])
        self.assertIsNone(v["feito"])

    def test_semana_dificil_dela_cita_os_registros(self):
        a = self.estado("Ela tá muito estressada com o trabalho.", dia(-8))
        b = self.estado("Ela chorou de cansaço ontem, tá exausta.", dia(-6))
        v = self.ok("GET", "/api/eu/checkin", consulta={"periodo": "2026-W39"})
        p = v["perguntas"][0]
        self.assertEqual((p["id"], p["texto"]), ("semana_dificil", "Como você a apoiou na semana difícil dela?"))
        self.assertEqual(p["registro_ids"], [a, b])
        self.assertIn("“Ela tá muito estressada com o trabalho.”", p["porque"])
        self.assertEqual(v["perguntas"][-1]["id"], "coisa_boa")                # comemora mais do que cobra

    def test_o_ciclo_nunca_vira_semana_dificil(self):
        a = self.estado("Ela tá com muita cólica, coitada, triste.", dia(-8))
        b = self.estado("Ela continua triste.", dia(-7))
        self.f_ciclo.ids = {a, b}
        ids = [p["id"] for p in self.ok("GET", "/api/eu/checkin", consulta={"periodo": "2026-W39"})["perguntas"]]
        self.assertNotIn("semana_dificil", ids)

    def test_promessa_vencendo_e_apagar_e_apagar(self):
        pid = self.promessa("Levar ela ao cinema", prazo="2026-10-02")
        v = self.ok("GET", "/api/eu/checkin", consulta={"periodo": "2026-W40"})
        p = next(p for p in v["perguntas"] if p["id"] == "promessa")
        # O prazo (sexta, 02/10) ainda não chegou na quarta: a pergunta não fica no passado (revisão de 07/10).
        self.assertEqual((p["texto"], p["porque"]), ("Já deu para cumprir “Levar ela ao cinema”?",
                                                     "O prazo é daqui a 2 dias (02/10)."))
        g = eu._ganchos_da_semana(self.banco, date(2026, 9, 28), date(2026, 10, 4), date(2026, 10, 5), None)
        self.assertEqual(next(x.porque for x in g if x.id == "promessa"), "O prazo era 02/10.")

        self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W40", "respostas": {"promessa": "Sim! Fomos no domingo."}})
        guardado = json.loads(self.linhas("checkins")[0]["respostas"])
        self.assertNotIn("cinema", json.dumps(guardado["perguntas"][0]["pergunta"]))   # guardada sem conteúdo do registro
        self.banco.executar("DELETE FROM promessas WHERE id = ?", (pid,))
        p = next(p for p in self.ok("GET", "/api/eu/checkin", consulta={"periodo": "2026-W40"})["perguntas"] if p["id"] == "promessa")
        self.assertEqual((p["texto"], p["resposta"]), ("Conseguiu cumprir a promessa que vencia nesta semana?",
                                                       "Sim! Fomos no domingo."))

    def test_pergunta_feita_desentendimento_e_no_maximo_tres(self):
        self.estado("Ela tá ansiosa.", dia(-8), importancia=3)
        self.pergunta_feita(1, dia(-7), pergunta="Qual é a lembrança mais feliz que você tem da sua infância?",
                            trilha="profundas")
        self.promessa("Ligar pra mãe dela", prazo="2026-09-25")
        self.ok("POST", "/api/eu/reparos", {"aconteceu": "Discutimos por causa do horário", "data": "2026-09-24"})
        perguntas = self.ok("GET", "/api/eu/checkin", consulta={"periodo": "2026-W39"})["perguntas"]
        self.assertEqual([p["id"] for p in perguntas], ["semana_dificil", "reparo", "coisa_boa"])
        self.assertEqual(perguntas[1]["texto"], "Depois do desentendimento de 24/09, como vocês ficaram?")
        self.estado("x", dia(-30))       # sem efeito: fora da semana
        sem_reparo = eu.perguntas_da_semana(self.banco, "2026-W38", REF)
        self.assertEqual([p.id for p in sem_reparo], ["coisa_boa", "faria_diferente"])
        com_pergunta = [p for p in eu._ganchos_da_semana(self.banco, dia(-9), dia(-3), REF, set()) if p.id == "pergunta_feita"]
        self.assertEqual(com_pergunta[0].texto,
                         "Como foi a conversa sobre “Qual é a lembrança mais feliz que você tem da sua infância?”?")

    def test_salvar_completar_e_os_erros(self):
        r = self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W39", "presenca": 4,
                                                "respostas": {"coisa_boa": "Fiz o jantar dela na terça."}})
        self.assertEqual((r["feito"]["presenca"], r["feito"]["emoji"]), (4, "😊"))
        self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W39", "respostas": {"faria_diferente": "Menos celular."}})
        v = self.ok("GET", "/api/eu/checkin", consulta={"periodo": "2026-W39"})
        self.assertEqual({p["id"]: p["resposta"] for p in v["perguntas"]},
                         {"coisa_boa": "Fiz o jantar dela na terça.", "faria_diferente": "Menos celular."})
        self.assertEqual(v["feito"]["presenca"], 4)                              # completar não apaga
        self.assertEqual(len(self.linhas("checkins")), 1)
        casos = [({"periodo": "2026-W39", "respostas": {"inventada": "x"}}, eu.PERGUNTA_DESCONHECIDA),
                 ({"periodo": "2026-W38", "respostas": {}}, eu.CHECKIN_VAZIO),
                 ({"periodo": "2026-W38", "presenca": 7}, eu.PRESENCA_INVALIDA),
                 ({"periodo": "2026-W38", "presenca": True}, eu.PRESENCA_INVALIDA),
                 ({"periodo": "2026-W45", "presenca": 3}, eu.PERIODO_FUTURO),
                 ({"periodo": "2026-W10", "presenca": 3}, eu.PERIODO_ANTIGO),
                 ({"periodo": "2026-09", "tipo": "semanal", "presenca": 3}, eu.PERIODO_INVALIDO),
                 ({"periodo": "2026-W38", "presenca": 3, "nota_dela": 2}, eu.CAMPOS_INVALIDOS)]
        for corpo, erro in casos:
            with self.subTest(corpo=corpo):
                r = self.pedir("POST", "/api/eu/checkin", corpo)
                self.assertEqual((r.status, r.dados["erro"]), (400, erro))

    def test_reflexao_mensal_com_intencao(self):
        self.ok("POST", "/api/eu/intencao", {"mes": "2026-09", "texto": "notar o que a deixa feliz", "sugestao": "alegrias"})
        v = self.ok("GET", "/api/eu/mes", consulta={"mes": "2026-09"})
        ids = [p["id"] for p in v["reflexao"]["perguntas"]]
        self.assertEqual(ids, ["mes_momento", "mes_cuidado", "mes_intencao", "mes_melhorar", "mes_voce"])
        self.assertEqual(v["reflexao"]["perguntas"][2]["texto"], "Como foi a sua intenção “Notar o que a deixa feliz”?")
        self.ok("POST", "/api/eu/checkin", {"tipo": "mensal", "periodo": "2026-09",
                                            "respostas": {"mes_intencao": "Prestei mais atenção, foi bom."}})
        intencao = self.ok("GET", "/api/eu/intencao", consulta={"mes": "2026-09"})["atual"]
        self.assertEqual((intencao["revisao"], intencao["revisada_em"]), ("Prestei mais atenção, foi bom.", DATA))

    def test_cartao_do_hoje_de_sexta_a_terca(self):
        for n in range(3):
            self.estado(f"Ela tá cansada {n}.", dia(-n - 1))
        self.assertEqual([c for c in self.hoje(REF) if c["tipo"] == "eu_checkin"], [])        # quarta: não
        [c] = [c for c in self.hoje(dia(2)) if c["tipo"] == "eu_checkin"]                     # sexta
        self.assertEqual((c["nivel"], c["chave"], c["dados"]["periodo"]), ("lembrete", "eu:checkin:2026-W40:", "2026-W40"))
        self.dia = dia(2)
        self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W40", "presenca": 3})
        self.assertEqual([c for c in self.hoje(dia(2)) if c["tipo"] == "eu_checkin"], [])     # feito: some
        self.ok("PATCH", "/api/config", {"eu_lembretes": False})
        self.assertEqual([c for c in self.cartoes(dia(9)) if c["tipo"] == "eu_checkin"], [])  # desligado em Ajustes

    def test_apagar_checkin(self):
        cid = self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W39", "presenca": 2})["id"]
        self.ok("DELETE", f"/api/eu/checkins/{cid}")
        self.assertEqual(self.linhas("checkins"), [])
        self.assertEqual(self.pedir("DELETE", f"/api/eu/checkins/{cid}").status, 404)


class TestEspelho(BaseNucleo):
    def test_promessas_feitas_e_cumpridas(self):
        self.promessa("Levar ela ao cinema", criado=dia(-3))
        self.promessa("Consertar o chuveiro", status="cumprida", criado=dia(-40), concluida=dia(-1))
        e = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})
        [linha] = [l for l in e["linhas"] if l["chave"] == "promessas"]
        self.assertEqual(linha["texto"], "Promessas: 1 promessa nova; 1 cumprida 💙.")

    def test_acompanhamento_das_pendencias(self):
        pend = self.rid("Ela tá esperando o resultado da entrevista de emprego.", data="2026-09-12", confirmar=())
        volta = self.rid("Perguntei como foi a entrevista e ela passou!", data="2026-09-15", confirmar=())
        e = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})
        [linha] = [l for l in e["linhas"] if l["chave"] == "acompanhamento"]
        self.assertIn("em 15/09 você anotou que voltou ao assunto", linha["detalhes"][0]["texto"])
        self.assertEqual(linha["detalhes"][0]["retorno_registro_id"], volta)
        self.assertEqual(set(linha["registro_ids"]), {pend, volta})

    def test_acompanhamento_sem_retorno_e_gentil(self):
        self.rid("Ela tá esperando o resultado do exame.", data="2026-09-20", confirmar=())
        e = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})
        [linha] = [l for l in e["linhas"] if l["chave"] == "acompanhamento"]
        self.assertIn("ainda não há registro de que vocês voltaram ao assunto", linha["detalhes"][0]["texto"])

    def test_equilibrio_preocupacoes_e_alegrias(self):
        self.estado("Ela tá estressada.", dia(-3))
        self.estado("Ela tá ansiosa.", dia(-2))
        e = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})
        [linha] = [l for l in e["linhas"] if l["chave"] == "equilibrio"]
        self.assertEqual(linha["texto"], "Você registrou 2 preocupações e 0 alegrias dela. Talvez valha notar também o que a deixa feliz.")
        self.estado("Ela ficou feliz.", dia(-1), polaridade=1)
        self.estado("Acho que ela tá triste.", dia(-1), certeza=Certeza.INFERENCIA)      # palpite não é fato: não conta
        [linha] = [l for l in self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})["linhas"] if l["chave"] == "equilibrio"]
        self.assertEqual(linha["texto"], "Você registrou 2 preocupações e 1 alegria dela.")

    def test_antecedencia_nas_datas(self):
        self.rid("A apresentação dela é dia 28.", data="2026-09-10", confirmar=())
        e = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})
        [linha] = [l for l in e["linhas"] if l["chave"] == "antecedencia"]
        # "período" é palavra do 🌙: o espelho diz "esta data" (revisão de 07/10).
        self.assertEqual(linha["texto"], "Esta data estava anotada com antecedência.")

        self.assertIn("(28/09) estava anotado desde 10/09 (18 dias antes)", linha["detalhes"][0]["texto"])

    def test_atitudes_que_combinam_com_o_que_ela_disse(self):
        ap = self.rid("Ela me disse que o que ajuda é eu ficar perto dela quando tá triste.", data="2026-09-20", confirmar=())
        at = self.rid("Fiquei com ela no sofá a noite toda.", data="2026-09-25")
        e = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})
        [linha] = [l for l in e["linhas"] if l["chave"] == "atitudes"]
        self.assertEqual(linha["texto"], "Você anotou 1 atitude de cuidado; 1 combina com o que ela disse que ajuda 💙.")
        self.assertEqual(linha["detalhes"][0]["combina"][0]["registro_id"], ap)
        self.assertEqual(set(linha["registro_ids"]), {ap, at})

    def test_descompasso_vira_pergunta_citando_o_que_ela_disse(self):
        self.rid("Ela disse que prefere que eu não tente resolver, só que eu escute.", data="2026-09-10", confirmar=())
        self.rid("Dei conselho de novo sobre o chefe dela.", data="2026-09-15")
        self.rid("Tentei resolver o problema da faculdade dela.", data="2026-09-22")
        [d] = self.ok("GET", "/api/eu/atitudes")["descompassos"]
        self.assertTrue(d["texto"].endswith("Como foi?"))
        self.assertIn("“Ela disse que prefere que eu não tente resolver, só que eu escute.”", d["texto"])
        self.assertIn("em 2 registros, você anotou que deu conselho ou tentou resolver", d["texto"])
        espelho = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})
        self.assertTrue(any(l["chave"] == "descompasso" and l["pergunta"] for l in espelho["linhas"]))

    def test_tendencia_em_texto_e_periodo_vazio(self):
        self.ok("POST", "/api/eu/atitudes", {"texto": "Fiz um chá pra ela", "data": "2026-08-10"})
        self.ok("POST", "/api/eu/atitudes", {"texto": "Busquei ela no trabalho", "data": "2026-09-10"})
        self.ok("POST", "/api/eu/atitudes", {"texto": "Escutei ela com calma", "data": "2026-09-12"})
        e = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})
        self.assertIn("Atitudes de cuidado anotadas: em julho, 0; em agosto, 1; em setembro, 2.", e["tendencia"])
        vazio = self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-W30"})
        self.assertEqual((vazio["linhas"], vazio["vazio"]), ([], eu.AVISO_ESPELHO_VAZIO))
        self.assertEqual(self.pedir("GET", "/api/eu/espelho", consulta={"periodo": "2026-12"}).status, 400)

    def test_toda_citacao_aponta_para_registro_que_existe(self):
        self.estado("Ela tá estressada.", dia(-3))
        self.rid("Fiquei com ela no sofá.", data="2026-09-25")
        self.rid("A gente brigou por causa da louça.", data="2026-09-26")
        existentes = {r["id"] for r in self.ok("GET", "/api/registros")}
        for linha in self.ok("GET", "/api/eu/espelho", consulta={"periodo": "2026-09"})["linhas"]:
            with self.subTest(linha=linha["chave"]):
                self.assertLessEqual(set(linha["registro_ids"]), existentes)


class TestLadoALado(BaseNucleo):
    def test_o_que_eu_achei_ao_lado_do_que_ela_disse(self):
        rid = self.rid("Perguntei pra ela o que fez ela se sentir cuidada. Ela disse que foi quando eu levei sopa.",
                       data="2026-09-20", confirmar=())
        self.pergunta_feita(201, dia(-10), rid, trilha="nos_dois",
                            pergunta="O que te fez se sentir cuidada por mim nas últimas semanas?",
                            resposta="Ela disse que foi quando eu levei sopa.", certeza="confirmado")
        self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W39", "respostas": {"coisa_boa": "Levei sopa quando ela tava gripada."}})
        lado = self.ok("GET", "/api/eu/mes", consulta={"mes": "2026-09"})["lado_a_lado"]
        [par] = lado["pares"]
        self.assertEqual((par["tema"], par["eu"][0]["resposta"], par["ela"][0]["resposta"], par["ela"][0]["certeza"]),
                         ("cuidado", "Levei sopa quando ela tava gripada.", "Ela disse que foi quando eu levei sopa.", "confirmado"))
        self.assertIsNone(lado["sugestao_pergunta"])

    def test_resposta_que_nao_esta_no_registro_nao_aparece(self):
        rid = self.rid("Fiz a pergunta sobre nós dois.", data="2026-09-20", confirmar=())
        self.pergunta_feita(200, dia(-10), rid, trilha="nos_dois", pergunta="Tem algo que eu possa fazer melhor por você?",
                            resposta="Ela disse que quer mais flores.", certeza="confirmado")
        [ela] = self.ok("GET", "/api/eu/mes", consulta={"mes": "2026-09"})["lado_a_lado"]["ela"]
        self.assertIsNone(ela["resposta"])                                           # nunca inventar
        self.assertEqual(ela["registro_id"], rid)

    def test_sem_respostas_dela_sugere_uma_pergunta_com_cuidado(self):
        lado = self.ok("GET", "/api/eu/mes", consulta={"mes": "2026-09"})["lado_a_lado"]
        self.assertEqual((lado["pares"], lado["ela"]), ([], []))
        self.assertTrue(lado["sugestao_pergunta"]["texto"].endswith("?"))
        self.assertIn("Talvez", lado["sugestao_pergunta"]["porque"])


class TestContratoDasOutrasTabelas(unittest.TestCase):
    """Só as colunas do contrato (sem `trilha`, `pergunta`, `resposta`) e sem a tabela: nada quebra."""

    def setUp(self):
        eu.esquecer_diario()

    def test_so_as_colunas_do_contrato(self):
        with recursos.usando(eu, falso_perguntas(com_extras=False)):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            rid = b.criar_registro("Perguntei a ela.", REF, [], [], [])
            b.inserir("perguntas_feitas", {"pergunta_id": 214, "status": "feita", "feita_em": dia(-2), "registro_id": rid})
            b.inserir("perguntas_feitas", {"pergunta_id": 3, "status": "feita", "feita_em": dia(-2), "registro_id": rid})
            [ela] = eu.lado_a_lado(b, "2026-09")["ela"]                  # 214 está na faixa "Nós dois" (200-249)
            self.assertEqual((ela["registro_id"], ela["resposta"]), (rid, None))
            linha = [l for l in eu.espelho(b, "2026-09", REF)["linhas"] if l["chave"] == "perguntas"][0]
            self.assertEqual(linha["texto"], "Você fez 1 pergunta profunda e 1 pergunta sobre vocês dois.")

    def test_sem_as_tabelas_dos_outros(self):
        with recursos.usando(eu):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            self.assertEqual(eu.lado_a_lado(b, "2026-09")["ela"], [])
            self.assertEqual([p.id for p in eu.perguntas_da_semana(b, "2026-W39", REF)], ["coisa_boa", "faria_diferente"])
            self.assertEqual(eu.espelho(b, "2026-09", REF)["linhas"], [])
            self.assertEqual(eu.blocos_de_carta(b, "natal", REF), [])

    def test_com_o_recurso_perguntas_de_verdade(self):
        try:
            from entrenos.recursos import perguntas as real
            if not getattr(real, "ESQUEMA", None):
                self.skipTest("o recurso perguntas ainda não tem tabela")
            with recursos.usando(eu, real):
                b = Banco.novo()
                self.addCleanup(b.fechar)
                rid = b.criar_registro("Ela respondeu que foi a sopa.", REF, [], [], [])
                cols = eu._colunas(b, "perguntas_feitas")
                p = real.pergunta(201)
                valores = {"pergunta_id": 201, "status": "feita", "feita_em": dia(-1).isoformat(), "registro_id": rid}
                extras_ = {"trilha": real.trilha(p), "categoria": p.categoria, "pergunta": p.texto, "origem": "registro",
                           "criado_em": "2026-09-29T10:00:00", "resposta": "Ela respondeu que foi a sopa.",
                           "certeza": "confirmado", "recusou": 0}
                valores.update({k: v for k, v in extras_.items() if k in cols})
                b.inserir("perguntas_feitas", valores)
        except unittest.SkipTest:
            raise
        except Exception as erro:  # noqa: BLE001 - o recurso de verdade está sendo escrito ao mesmo tempo
            self.skipTest(f"o recurso perguntas não abriu agora ({type(erro).__name__})")
        with recursos.usando(eu, real):
            [ela] = eu.lado_a_lado(b, "2026-09")["ela"]
            self.assertEqual((ela["resposta"], ela["certeza"], ela["tema"]), ("Ela respondeu que foi a sopa.", "confirmado", "cuidado"))

    def test_com_o_recurso_ciclo_de_verdade(self):
        """O ciclo nunca explica nada aqui: com o recurso de verdade, o registro dele não vira "semana
        difícil" nem bloco de carta (pula se o recurso não abrir agora)."""
        texto = "Ela ficou menstruada ontem e tá muito triste, coitada."
        try:
            from entrenos.recursos import ciclo as real
            if not callable(getattr(real, "fora_da_ia", None)) or not callable(getattr(real, "gravar_extras", None)):
                self.skipTest("o recurso ciclo ainda não está pronto")
            with recursos.usando(eu, real):
                proposta = motor.MotorRegras().analisar(texto, REF, Conhecimento(nome_dela="Julia"))
                extras_ciclo = proposta.extras.get("ciclo", [])
                if not extras_ciclo:
                    self.skipTest("o recurso ciclo não reconheceu a frase agora")
                b = Banco.novo()
                self.addCleanup(b.fechar)
                with b.transacao():
                    rid = b.criar_registro(texto, REF, proposta.itens, [], [], privado=True)
                    real.gravar_extras(b, rid, json.loads(json.dumps(extras_ciclo, default=str)), REF)
                fora_ciclo = real.fora_da_ia(b)
        except unittest.SkipTest:
            raise
        except Exception as erro:  # noqa: BLE001 - o recurso de verdade está sendo escrito ao mesmo tempo
            self.skipTest(f"o recurso ciclo não abriu agora ({type(erro).__name__})")
        if rid not in fora_ciclo:
            self.skipTest("o recurso ciclo ainda não deixa este registro fora")
        with recursos.usando(eu, real):
            self.assertIn(rid, eu._fora_outros(b))
            self.assertEqual(eu._estados_dela(b, dia(-3), REF, eu._fora_outros(b)), [])
            self.assertNotIn("semana_dificil", [p.id for p in eu.perguntas_da_semana(b, "2026-W40", REF)])
            self.assertFalse(any(x.get("registro_id") == rid for x in eu.blocos_de_carta(b, "sem_motivo", REF)))


class TestIntencao(BaseNucleo):
    def test_sugestoes_vem_dos_dados_com_o_porque(self):
        a = self.estado("Ela tá estressada.", dia(-3))
        self.estado("Ela tá ansiosa.", dia(-2))
        self.estado("Ela tá triste.", dia(-1))
        sugestoes = self.ok("GET", "/api/eu/intencao")["sugestoes"]
        alegrias = next(s for s in sugestoes if s["id"] == "alegrias")
        self.assertEqual((alegrias["texto"], alegrias["porque"]),
                         ("Notar o que a deixa feliz", "Nos últimos 30 dias você registrou 3 preocupações e 0 alegrias dela."))
        self.assertIn(a, alegrias["registro_ids"])
        momento = next(s for s in sugestoes if s["id"] == "momento")
        self.assertEqual(momento["porque"], "Ontem você registrou: “Ela tá triste.”")
        self.assertTrue(all(s["de_dados"] for s in sugestoes))

    def test_sem_dados_ideias_gerais_marcadas_como_tais(self):
        sugestoes = self.ok("GET", "/api/eu/intencao")["sugestoes"]
        self.assertEqual([s["de_dados"] for s in sugestoes], [False, False, False])
        self.assertTrue(all("não vem dos seus registros" in s["porque"] for s in sugestoes))

    def test_o_que_ela_disse_que_ajuda_vira_sugestao_ate_combinar(self):
        self.rid("Ela disse que o que ajuda é eu ficar perto quando ela tá triste.", data="2026-09-20", confirmar=())
        ids = [s["id"] for s in self.ok("GET", "/api/eu/intencao")["sugestoes"]]
        self.assertIn("apoio", ids)
        self.rid("Fiquei com ela no sofá a noite toda.", data="2026-09-25")
        self.assertNotIn("apoio", [s["id"] for s in self.ok("GET", "/api/eu/intencao")["sugestoes"]])

    def test_escolher_lembrar_no_meio_do_mes_e_revisar(self):
        self.dia = date(2026, 10, 2)
        r = self.ok("POST", "/api/eu/intencao", {"texto": "escutar mais do que falar"})
        self.assertEqual((r["atual"]["mes"], r["atual"]["texto"], r["atual"]["origem"]), ("2026-10", "Escutar mais do que falar", "escrita"))
        [c] = [c for c in self.cartoes(date(2026, 10, 15)) if c["tipo"] == "eu_intencao"]
        self.assertEqual(c["texto"], "Sua intenção deste mês: “Escutar mais do que falar”. Como está indo?")
        self.assertEqual([c for c in self.cartoes(date(2026, 10, 20)) if c["tipo"] == "eu_intencao"], [])
        self.ok("PATCH", f"/api/eu/intencao/{r['id']}", {"revisao": "Consegui na maioria dos dias."})
        self.assertEqual([c for c in self.cartoes(date(2026, 10, 15)) if c["tipo"] == "eu_intencao"], [])
        self.assertEqual(self.pedir("POST", "/api/eu/intencao", {"mes": "2026-12", "texto": "x"}).status, 400)


class TestReparos(BaseNucleo):
    def test_criar_a_mao_so_a_sua_parte(self):
        r = self.ok("POST", "/api/eu/reparos", {"aconteceu": "Discutimos sobre a viagem", "eu_senti": "Frustrado",
                                                "ela_pode_ter_sentido": "Pressionada", "tema": "viagem", "intensidade": 2,
                                                "data": "2026-09-27"})
        self.assertEqual((r["ela_certeza"], r["conversamos"], r["acompanhar"]), ("inferencia", None, True))
        r2 = self.ok("POST", "/api/eu/reparos", {"aconteceu": "Brigamos", "ela_pode_ter_sentido": "Magoada", "ela_disse": True})
        self.assertEqual(r2["ela_certeza"], "confirmado")
        for corpo in ({"aconteceu": "x", "o_que_ela_errou": "tudo"}, {"aconteceu": "x", "culpa_dela": True}):
            with self.subTest(corpo=corpo):
                r = self.pedir("POST", "/api/eu/reparos", corpo)
                self.assertEqual((r.status, r.dados["erro"]), (400, eu.SO_A_SUA_PARTE))
        for corpo in ({"aconteceu": ""}, {"aconteceu": "x", "intensidade": 4}, {"aconteceu": "x", "data": "2026-12-01"},
                      {"aconteceu": "x", "conversamos": "sim"}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.pedir("POST", "/api/eu/reparos", corpo).status, 400)

    def test_vocês_conversaram_de_2_a_7_dias_depois(self):
        r = self.ok("POST", "/api/eu/reparos", {"aconteceu": "Discutimos sobre a viagem", "data": "2026-09-29"})
        self.assertEqual([c for c in self.cartoes(REF) if c["tipo"] == "eu_reparo"], [])            # 1 dia: cedo
        [c] = [c for c in self.cartoes(dia(1)) if c["tipo"] == "eu_reparo"]
        self.assertEqual(c["texto"], "Vocês conversaram sobre aquilo de terça (29/09)?")
        self.assertEqual(c["chave"], f"reparo:p{r['id']}:conversaram:")
        self.assertEqual([c for c in self.cartoes(dia(8)) if c["tipo"] == "eu_reparo"], [])          # passou: deixa quieto
        self.ok("PATCH", f"/api/eu/reparos/{r['id']}", {"conversamos": True})
        self.assertEqual([c for c in self.cartoes(dia(1)) if c["tipo"] == "eu_reparo"], [])
        self.assertEqual(self.linhas("reparos")[0]["acompanhado_em"], DATA)

    def test_tema_que_voltou_tres_vezes(self):
        for d, tema in (("2026-08-10", "a mãe dela"), ("2026-09-01", "mãe dela de novo"), ("2026-09-20", "a mae dela")):
            self.ok("POST", "/api/eu/reparos", {"aconteceu": "Discutimos", "tema": tema, "data": d})
        self.ok("POST", "/api/eu/reparos", {"aconteceu": "Discutimos", "tema": "a louça", "data": "2026-09-21"})
        [t] = self.ok("GET", "/api/eu/reparos")["temas"]
        self.assertEqual((t["vezes"], t["tema"]), (3, "a mae dela"))
        self.assertIn("talvez valha uma conversa calma", t["texto"])
        self.assertTrue([c for c in self.cartoes(REF) if c["tipo"] == "eu_tema"])

    def test_frequentes_e_intensos_sugere_ajuda_com_cuidado(self):
        for d in ("2026-09-10", "2026-09-18", "2026-09-27"):
            self.ok("POST", "/api/eu/reparos", {"aconteceu": "Brigamos feio", "intensidade": 3, "data": d})
        [c] = [c for c in self.cartoes(REF) if c["tipo"] == "eu_cuidado_relacao"]
        self.assertIn("Talvez valha conversar com ela num momento calmo", c["texto"])
        self.assertIn("profissional", c["texto"])
        for palavra in ("tóxic", "abusiv", "transtorno", "diagn", "culpa"):
            self.assertNotIn(palavra, (c["texto"] + c["porque"]).lower())
        self.assertIsNone(eu.intensos(self.banco, dia(30)))                                        # 30 dias depois: não

    def test_o_que_aconteceu_vem_do_registro(self):
        self.rid("A gente discutiu por causa do celular.")
        [r] = self.linhas("reparos")
        resposta = self.pedir("PATCH", f"/api/eu/reparos/{r['id']}", {"aconteceu": "Ela começou a briga"})
        self.assertEqual((resposta.status, resposta.dados["erro"]), (400, eu.ACONTECEU_DO_REGISTRO))
        self.ok("PATCH", f"/api/eu/reparos/{r['id']}", {"faria_diferente": "Largar o celular quando ela fala"})

    def test_apagar_some_do_banco_das_copias_e_dos_avisos(self):
        r = self.ok("POST", "/api/eu/reparos", {"aconteceu": "Discutimos sobre a viagem", "data": "2026-09-27"})
        self.ok("POST", "/api/alertas/dispensar", {"chave": f"eu:reparo:p{r['id']}:conversaram:"})
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.ok("DELETE", f"/api/eu/reparos/{r['id']}")
        self.assertEqual(self.linhas("reparos"), [])
        self.assertFalse(any(k.startswith("eu:") for k in self.banco.alertas_dispensados()))
        repetir[0](copia)
        self.assertEqual(copia.consultar("SELECT * FROM reparos"), [])
        self.assertFalse(any(k.startswith("eu:") for k in copia.alertas_dispensados()))
        historico = json.dumps(self.ok("GET", "/api/historico"), ensure_ascii=False)
        self.assertIn(f"reparos #{r['id']} apagado", historico)
        self.assertNotIn("viagem", historico)                                           # histórico sem conteúdo


class TestAprendizados(BaseNucleo):
    def test_criar_editar_e_gatilhos_legiveis(self):
        a = self.ok("POST", "/api/eu/aprendizados", {"texto": "quando o assunto é a mãe dela, melhor só ouvir"})
        self.assertEqual(a["texto"], "Quando o assunto é a mãe dela, melhor só ouvir")
        self.assertIn("“mae”", a["gatilhos"])
        b = self.ok("PATCH", f"/api/eu/aprendizados/{a['id']}", {"texto": "Quando ela chega cansada, deixar ela descansar"})
        self.assertEqual(b["gatilhos"], ["ela cansada"] + [g for g in b["gatilhos"] if g != "ela cansada"])
        self.assertEqual(self.pedir("POST", "/api/eu/aprendizados", {"texto": "quando ela chega cansada, deixar ela descansar"}).status, 400)
        self.assertEqual(self.pedir("PATCH", f"/api/eu/aprendizados/{a['id']}", {"texto": "x", "gatilhos": []}).status, 400)

    def test_volta_no_hoje_quando_um_registro_novo_casa(self):
        ap = self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela chega cansada, o melhor é deixar ela descansar"})
        self.assertEqual([c for c in self.cartoes(REF) if c["tipo"] == "eu_aprendizado"], [])
        rid = self.rid("Hoje ela chegou muito cansada do trabalho.", confirmar=())
        [c] = [c for c in self.cartoes(REF) if c["tipo"] == "eu_aprendizado"]
        self.assertEqual(c["texto"], "Um aprendizado seu combina com hoje: “Quando ela chega cansada, o melhor é deixar ela descansar”")
        self.assertIn("Hoje você registrou: “Hoje ela chegou muito cansada do trabalho.” (ela está cansada", c["porque"])
        self.assertIn("Você anotou esse aprendizado em 30/09.", c["porque"])
        self.assertEqual((c["registro_ids"], c["chave"]), ([rid], f"r{rid}:ap{ap['id']}:"))
        self.assertEqual([c for c in self.cartoes(dia(3)) if c["tipo"] == "eu_aprendizado"], [])   # passou
        [visto] = self.ok("GET", "/api/eu/aprendizados")
        self.assertEqual(visto["voltou"][0]["registro_id"], rid)

    def test_registro_de_antes_do_aprendizado_e_o_proprio_registro_nao_contam(self):
        self.rid("Ela chegou cansada ontem.", data="2026-09-29", confirmar=())
        self.rid("Aprendi que quando ela chega cansada, o melhor é deixar ela descansar.")
        self.assertEqual([c for c in self.cartoes(REF) if c["tipo"] == "eu_aprendizado"], [])

    def test_apagar_aprendizado_e_registro_esquecem_os_avisos(self):
        ap = self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela chega cansada, o melhor é deixar ela descansar"})
        rid = self.rid("Hoje ela chegou muito cansada do trabalho.", confirmar=())
        self.ok("POST", "/api/alertas/dispensar", {"chave": f"eu:r{rid}:ap{ap['id']}:"})
        self.ok("DELETE", f"/api/eu/aprendizados/{ap['id']}")
        self.assertFalse(any(k.startswith("eu:") for k in self.banco.alertas_dispensados()))
        ap2 = self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela chega cansada, ficar perto"})
        self.ok("POST", "/api/alertas/dispensar", {"chave": f"eu:r{rid}:ap{ap2['id']}:"})
        self.ok("DELETE", f"/api/registros/{rid}")
        self.assertFalse(any(k.startswith("eu:") for k in self.banco.alertas_dispensados()))


class TestCuidarDeVoce(BaseNucleo):
    def checkins(self, *valores, ultima: str = "2026-W39"):
        _, inicio, _ = eu.limites(ultima)
        for n, v in enumerate(reversed(valores)):
            self.banco.inserir("checkins", {"tipo": "semanal", "periodo": eu.semana(inicio - timedelta(days=7 * n)),
                                            "respostas": {"perguntas": []}, "presenca": v, "criado_em": "x"})

    def test_tres_semanas_pesadas(self):
        self.checkins(4, 2, 2, 1)
        c = eu.cuidar_de_voce(self.banco, REF)
        self.assertEqual(c["texto"], eu.CUIDAR_TEXTO)
        self.assertEqual([s["emoji"] for s in c["semanas"]], ["😕", "😕", "😶‍🌫️"])
        [cartao] = [x for x in self.cartoes(REF) if x["tipo"] == "eu_cuidar"]
        self.assertEqual(cartao["nivel"], "sugestao")
        for palavra in ("depress", "ansiedade", "transtorno", "diagn"):
            self.assertNotIn(palavra, (cartao["texto"] + cartao["porque"]).lower())

    def test_caindo_tres_semanas_seguidas(self):
        self.checkins(5, 4, 3, 2)
        self.assertIsNotNone(eu.cuidar_de_voce(self.banco, REF))

    def test_sem_motivo_nao_diz_nada(self):
        self.checkins(5, 2, 4, 3)
        self.assertIsNone(eu.cuidar_de_voce(self.banco, REF))

    def test_semanas_com_buraco_ou_antigas_nao_contam(self):
        self.checkins(2, 2, ultima="2026-W39")
        self.banco.inserir("checkins", {"tipo": "semanal", "periodo": "2026-W35", "respostas": {}, "presenca": 1, "criado_em": "x"})
        self.assertIsNone(eu.cuidar_de_voce(self.banco, REF))
        self.assertIsNone(eu.cuidar_de_voce(self.banco, dia(40)))


class TestCartas(BaseNucleo):
    def preparar(self):
        frase = self.rid("Ela disse que ama o mar.", data="2026-09-01", confirmar=())
        self.rid("Ela adora tulipas.", data="2026-09-02", confirmar=())
        self.rid("Ela comentou que adora tulipas amarelas.", data="2026-09-03", confirmar=())
        self.rid("Ela gosta de jazz.", data="2026-09-04", confirmar=())                 # uma vez só: não é gosto forte
        ciclo = self.estado("Ela ficou feliz com o chá na cólica.", dia(-5), polaridade=1)
        self.f_ciclo.ids = {ciclo}
        self.banco.inserir("marcos", {"titulo": "Primeiro encontro", "data": "2024-03-12", "criado_em": "x"})
        self.ok("POST", "/api/eu/admiracoes", {"texto": "o jeito que ela cuida da avó"})
        return frase

    def test_blocos_so_do_que_foi_registrado(self):
        frase = self.preparar()
        blocos = self.ok("GET", "/api/eu/cartas/blocos", consulta={"ocasiao": "aniversario_dela"})["blocos"]
        tipos = {b["tipo"] for b in blocos}
        self.assertLessEqual({"frase", "gosta", "admiracao", "marco"}, tipos)
        self.assertEqual(blocos[0]["tipo"], "admiracao")                             # aniversário: o que você admira primeiro
        self.assertIn(frase, [b["registro_id"] for b in blocos if b["tipo"] == "frase"])
        # "comentou que adora tulipas amarelas" é fala dela (🟢) e junta com "tulipas"; jazz (uma vez, 🟡) não.
        self.assertEqual([b["texto"] for b in blocos if b["tipo"] == "gosta"], ["tulipas amarelas", "mar"])
        self.assertFalse(any("cólica" in json.dumps(b, ensure_ascii=False) for b in blocos))   # nada do ciclo
        self.assertEqual(self.pedir("GET", "/api/eu/cartas/blocos", consulta={"ocasiao": "inventada"}).status, 400)

    def test_esqueleto_so_com_o_escolhido_e_relido_do_diario(self):
        self.preparar()
        blocos = self.ok("GET", "/api/eu/cartas/blocos", consulta={"ocasiao": "namoro"})["blocos"]
        marco = next(b for b in blocos if b["tipo"] == "marco")
        gosta = next(b for b in blocos if b["tipo"] == "gosta")
        e = self.ok("POST", "/api/eu/cartas/esqueleto", {"ocasiao": "namoro", "blocos": [marco["id"], gosta["id"]]})
        self.assertEqual(e["texto"].splitlines()[0], "Feliz aniversário de namoro, Julia!")
        self.assertIn("Lembro de quando… (Primeiro encontro, 12/03/2024)", e["texto"])
        self.assertIn("Sei o quanto você gosta de tulipas amarelas.", e["texto"])
        self.assertNotIn(" mar", e["texto"])                                         # o que não foi escolhido não entra
        r = self.pedir("POST", "/api/eu/cartas/esqueleto", {"ocasiao": "namoro", "blocos": ["frase:99999"]})
        self.assertEqual((r.status, r.dados["erro"]), (400, eu.BLOCO_INVALIDO))

    def test_guardar_editar_apagar_e_apagar_o_registro_tira_a_referencia(self):
        frase = self.preparar()
        bloco = next(b for b in self.ok("GET", "/api/eu/cartas/blocos", consulta={"ocasiao": "sem_motivo"})["blocos"]
                     if b["registro_id"] == frase)
        c = self.ok("POST", "/api/eu/cartas", {"ocasiao": "sem_motivo", "texto": "Julia,\n\nVocê me disse que ama o mar…",
                                               "blocos": [bloco["id"]]})
        self.assertEqual(c["blocos"], [{"id": bloco["id"], "registro_id": frase}])
        self.ok("PATCH", f"/api/eu/cartas/{c['id']}", {"titulo": "Para ler na praia"})
        self.ok("DELETE", f"/api/registros/{frase}")
        self.assertEqual(json.loads(self.linhas("cartas")[0]["blocos"]), [])
        self.ok("DELETE", f"/api/eu/cartas/{c['id']}")
        self.assertEqual(self.linhas("cartas"), [])
        self.assertEqual(self.pedir("POST", "/api/eu/cartas", {"ocasiao": "natal", "texto": "  "}).status, 400)

    def test_cartao_no_hoje_perto_do_aniversario_dela(self):
        self.preparar()
        [c] = [c for c in self.cartoes(REF) if c["tipo"] == "eu_carta"]               # aniversário dela em 10/10
        self.assertEqual(c["texto"], "O aniversário dela está chegando (em 10 dias). Talvez uma carta?")
        self.assertEqual([c for c in self.cartoes(dia(-30)) if c["tipo"] == "eu_carta"], [])


class TestVisaoGeralEHoje(BaseNucleo):
    def test_visao_geral(self):
        self.estado("Ela tá triste com a avó.", dia(-1))
        self.rid("Fiquei com ela no sofá a noite toda.", confirmar=("atitude",))
        self.ok("POST", "/api/eu/admiracoes", {"texto": "a força dela"})
        v = self.ok("GET", "/api/eu")
        self.assertEqual((v["semana"]["periodo"], v["semana"]["feito"], v["mes"]["mes"]), ("2026-W39", False, "2026-09"))
        self.assertEqual(v["momento_dela"]["texto"], "Ontem você registrou: “Ela tá triste com a avó.”")
        self.assertEqual(v["atitudes"][0]["texto"], "Fiquei com ela no sofá a noite toda")
        self.assertEqual(v["admiracoes"][0]["texto"], "A força dela")
        self.assertIsNone(v["cuidar"])

    def test_no_hoje_no_maximo_uma_sugestao_daqui(self):
        for d in ("2026-09-10", "2026-09-18", "2026-09-27"):
            self.ok("POST", "/api/eu/reparos", {"aconteceu": "Brigamos feio", "intensidade": 3, "data": d, "tema": "a mãe dela"})
        self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela chega cansada, o melhor é deixar ela descansar"})
        self.rid("Hoje ela chegou muito cansada do trabalho.", confirmar=())
        sugestoes = [c for c in self.hoje(REF) if c["nivel"] == "sugestao"]
        self.assertEqual(len(sugestoes), 1)
        self.assertEqual(sugestoes[0]["tipo"], "eu_cuidado_relacao")                 # a de maior relevância

    def test_nada_de_nota_nem_placar(self):
        self.rid("Fiquei com ela no sofá a noite toda.")
        self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W39", "presenca": 2})
        tudo = json.dumps([self.ok("GET", "/api/eu"), self.ok("GET", "/api/eu/mes"), self.cartoes(REF)], ensure_ascii=False)
        for proibido in (r"\bnotas?\b", r"\bplacar", r"\bpontua", r"\branking", r"\bscore", "%", r"\bmédia\b"):
            self.assertIsNone(re.search(proibido, tudo.lower()), proibido)


class TestPrivacidade(BaseNucleo):
    class CadeiaFalsa:
        """No lugar da IA: anota o que receberia (nada sai do computador)."""

        def __init__(self):
            self.recebido: list = []

        def responder(self, pergunta, trechos):
            self.recebido.append(json.dumps(trechos, ensure_ascii=False, default=str))
            return Resposta(pergunta=pergunta, texto="Achei.", encontrados=list(trechos)), "principal", []

        def sugerir(self, contexto, referencia):
            self.recebido.append(json.dumps(contexto, ensure_ascii=False, default=str))
            return [], "principal", []

        def analisar(self, texto, referencia, conhecimento, base):
            self.recebido.append(texto)
            return base, "principal", []

    def test_nada_da_area_eu_vai_para_a_ia(self):
        self.banco.salvar_config(ia_principal=IA)
        cadeia = self.CadeiaFalsa()
        self.enterContext(mock.patch.object(motor, "obter_cadeia_ia", lambda ajustes: cadeia))
        self.rid("Ela adora tulipas amarelas.", confirmar=())
        privado = self.rid("Levei sopa de abóbora pra ela quando ela tava gripada.")
        self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela fica brava, esperar a girafa passar"})
        self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W39", "respostas": {"coisa_boa": "Cantei serenata de kazoo"}})
        self.ok("POST", "/api/eu/cartas", {"ocasiao": "natal", "texto": "Carta do pinguim azul"})
        self.assertIn(privado, nucleo.fora_da_ia(self.banco))
        # Mesmo se você desmarcar a caixinha 🔒 depois, o que é da área Eu continua fora da IA.
        self.ok("PATCH", f"/api/registros/{privado}", {"privado": False})
        self.assertTrue(self.ok("GET", f"/api/registros/{privado}")["fora_da_ia"])
        self.assertEqual(self.pedir("POST", "/api/ia/sugestao", {}).status, 200)
        self.assertEqual(self.pedir("POST", "/api/ia/busca", {"q": "tulipas sopa abóbora girafa kazoo pinguim"}).status, 200)
        self.assertTrue(cadeia.recebido)
        enviado = " ".join(cadeia.recebido)
        for segredo in ("abóbora", "girafa", "kazoo", "pinguim"):
            self.assertNotIn(segredo, enviado)
        r = self.pedir("POST", "/api/ia/analisar", {"texto": "Levei sopa pra ela.", "data_registro": DATA})
        self.assertEqual(r.status, 403)
        self.assertNotIn("Levei sopa pra ela.", cadeia.recebido)

    def test_log_sem_conteudo_quando_algo_falha(self):
        self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela fica brava, esperar a girafa passar"})
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        with mock.patch.object(eu, "visao_geral", side_effect=RuntimeError("girafa passar")), \
                mock.patch.object(eu, "_cartoes_aprendizado", side_effect=RuntimeError("girafa passar")):
            self.assertEqual(self.pedir("GET", "/api/eu").status, 500)
            self.assertEqual(self.pedir("GET", "/api/hoje").status, 200)               # o Hoje abre sem os cartões daqui
        texto_log = "\n".join(coletor.mensagens)
        self.assertIn("RuntimeError", texto_log)
        self.assertNotIn("girafa", texto_log)

    def test_historico_sem_conteudo(self):
        self.ok("POST", "/api/eu/admiracoes", {"texto": "a risada de foca dela"})
        self.ok("POST", "/api/eu/reparos", {"aconteceu": "Discutimos sobre o abacaxi"})
        historico = json.dumps(self.ok("GET", "/api/historico"), ensure_ascii=False)
        self.assertIn("admiracoes #", historico)
        for segredo in ("foca", "abacaxi"):
            self.assertNotIn(segredo, historico)

    def test_rotas_pedem_sessao(self):
        for metodo, caminho in (("GET", "/api/eu"), ("GET", "/api/eu/aprendizados"), ("GET", "/api/eu/cartas"),
                                ("POST", "/api/eu/admiracoes")):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.pedir(metodo, caminho, {} if metodo == "POST" else None, token=None).status, 401)


class TestExportarImportarApagar(BaseNucleo):
    def test_exportar_e_juntar_noutro_diario_sem_duplicar(self):
        rid = self.rid("Fiquei com ela no hospital. A gente brigou por causa da visita. Admiro a força dela.")
        self.ok("POST", "/api/eu/aprendizados", {"texto": "Quando ela fica brava, esperar um pouco"})
        self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W39", "presenca": 4, "respostas": {"coisa_boa": "Cuidei dela."}})
        self.ok("POST", "/api/eu/intencao", {"texto": "escutar mais"})
        self.ok("POST", "/api/eu/cartas", {"ocasiao": "natal", "texto": "Feliz Natal!"})
        exportado = self.ok("GET", "/api/exportar")
        dados = exportado["recursos"]["eu"]
        self.assertEqual({t: len(dados[t]) for t in eu.TABELAS},
                         {"checkins": 1, "atitudes": 1, "aprendizados": 1, "reparos": 1, "admiracoes": 1, "cartas": 1,
                          "intencoes": 1})
        self.assertEqual(dados["atitudes"][0]["registro_id"], rid)
        texto = json.dumps(exportado, ensure_ascii=False)
        with recursos.usando(eu, falso_perguntas(), falso_promessas(), falso_marcos(), falso_ciclo()):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            destino.criar_registro("Um registro de antes.", REF, [], [], [])
            previa = destino.importar_json(texto, previa=True)
            self.assertEqual(previa["recursos"]["eu"]["novos"], 7)
            self.assertEqual(destino.consultar("SELECT * FROM atitudes"), [])
            r = destino.importar_json(texto)
            self.assertEqual(r["recursos"]["eu"], {"novos": 7, "texto": "7 itens novos da área Eu."})
            novo = next(x["id"] for x in destino.registros() if "hospital" in x["texto"])
            self.assertEqual(destino.consultar("SELECT registro_id FROM atitudes")[0]["registro_id"], novo)
            self.assertIn(novo, eu.fora_da_ia(destino))
            self.assertEqual(destino.importar_json(texto)["recursos"]["eu"]["novos"], 0)

    def test_importar_nunca_inventa(self):
        rid = self.rid("Fiquei com ela no hospital.")
        exportado = self.ok("GET", "/api/exportar")
        dados = exportado["recursos"]["eu"]
        dados["atitudes"].append({**dados["atitudes"][0], "texto": "Levei ela pra Paris", "trecho": "Levei ela pra Paris."})
        dados["atitudes"].append({**dados["atitudes"][0], "texto": "Sem registro", "registro_id": 777})
        dados["reparos"].append({"aconteceu": "", "data": DATA})
        dados["checkins"].append({"tipo": "semanal", "periodo": "semana passada"})
        with recursos.usando(eu):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            r = destino.importar_json(json.dumps(exportado, ensure_ascii=False))["recursos"]["eu"]
            self.assertEqual(r["novos"], 1)
            self.assertIn("4 ficaram de fora", r["texto"])
            outro = Banco.novo()
            self.addCleanup(outro.fechar)
            ruim = {**exportado, "recursos": {"eu": {"atitudes": "não é lista"}}}
            avisos = outro.importar_json(json.dumps(ruim, ensure_ascii=False))["avisos"]
            self.assertIn(recursos.AVISO_IMPORTAR.format(rotulo="Área Eu"), avisos)
        self.assertTrue(rid)

    def test_apagar_o_registro_apaga_o_que_veio_dele_e_as_referencias(self):
        self.estado("Ela tá estressada.", dia(-8))
        rid = self.estado("Ela tá exausta.", dia(-7))
        self.ok("POST", "/api/eu/checkin", {"periodo": "2026-W39", "respostas": {"semana_dificil": "Fiquei perto."}})
        atit = self.rid("Fiquei com ela no hospital. A gente brigou por causa da visita.")
        self.ok("DELETE", f"/api/registros/{atit}")
        self.assertEqual((self.linhas("atitudes"), self.linhas("reparos")), ([], []))
        self.ok("DELETE", f"/api/registros/{rid}")
        guardado = json.loads(self.linhas("checkins")[0]["respostas"])
        self.assertNotIn(rid, guardado["perguntas"][0]["refs"]["registro_ids"])
        exportado = json.dumps(self.ok("GET", "/api/exportar"), ensure_ascii=False)
        self.assertNotIn("hospital", exportado)                                    # apagar é apagar: nem no export
        self.assertNotIn(atit, eu.fora_da_ia(self.banco))


class TestTela(unittest.TestCase):
    def test_tela_sem_html_montado_nem_estilo_inline(self):
        js = (RAIZ / "web" / "telas" / "eu.js").read_text(encoding="utf-8")
        for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".style.",
                         "style=", "localStorage", "sessionStorage", "new Function"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, js)
        for registro in ("registrarMenu('eu'", "registrarTela('eu'", "registrarSecaoAjustes('eu'"):
            with self.subTest(registro=registro):
                self.assertIn(registro, js)
        self.assertIn("window.EntreNos", js)

    def test_tipos_dos_cartoes_e_dos_extras_batem_com_a_tela(self):
        js = (RAIZ / "web" / "telas" / "eu.js").read_text(encoding="utf-8")
        for tipo in eu.TIPOS_EXTRAS:
            self.assertIn(f"registrarRevisaoExtra('{tipo}'", js)
        fonte = (RAIZ / "entrenos" / "recursos" / "eu.py").read_text(encoding="utf-8")
        tipos = set(re.findall(r'tipo="(eu_[a-z_]+)"', fonte))
        self.assertGreaterEqual(len(tipos), 8)
        for tipo in tipos:
            self.assertIn(f"registrarCartaoHoje('{tipo}'", js)

    def test_telas_da_secao(self):
        js = (RAIZ / "web" / "telas" / "eu.js").read_text(encoding="utf-8")
        for sub in ("checkin", "mes", "aprendizados", "reparos", "cartas"):
            self.assertIn(f"'{sub}'", js)


if __name__ == "__main__":
    unittest.main()
