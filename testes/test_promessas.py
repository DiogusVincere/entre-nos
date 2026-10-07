"""Promessas (DESIGN.md, seção 20.3).

Só este recurso entra na lista (`recursos.usando(promessas)`): os outros estão sendo escritos ao
mesmo tempo. Cobre a extração por regras (frases de conversa, gírias, sem acento, datas relativas,
negação, dúvida, pergunta, ironia, o que NÃO é promessa, promessa cumprida), a gravação conferida
(nada inventado: trecho literal, pessoa escrita no trecho), as rotas (lista, criar, editar, cumprir,
deixar de lado, reabrir, apagar e as cópias), os cartões do Hoje (prazo chegando, conflito com algo
dela no mesmo dia, sugestão gentil, momento dela, "parece que você cumpriu"), exportar/importar/apagar
registro e os limites de privacidade (nada vai para a IA, log e histórico sem texto).

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026 (quarta), scrypt pequeno.
"""
from __future__ import annotations

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

from entrenos import cofre, motor, nucleo, recursos
from entrenos.banco import Banco
from entrenos.modelos import Certeza, Item, Resposta, TipoItem
from entrenos.motor import Conhecimento
from entrenos.recursos import promessas
from entrenos.regras.pessoas import plano
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
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-promessas-env-")
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
    """O que o recurso propõe para o texto, como a revisão recebe (passando pelo motor e pela fundação)."""
    with recursos.usando(promessas):
        proposta = motor.MotorRegras().analisar(texto, referencia, Conhecimento(pessoas=[], nome_dela=nome_dela))
    return proposta.extras.get("promessas", [])


def so_promessas(texto: str, **kw) -> list[dict]:
    return [e for e in extras(texto, **kw) if e["tipo"] == "promessa"]


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


def dia(n: int) -> date:
    return REF + timedelta(days=n)


# ======================================================================== extração

class TestExtrair(unittest.TestCase):
    def uma(self, texto: str, **kw) -> dict:
        achadas = so_promessas(texto, **kw)
        self.assertEqual(len(achadas), 1, achadas)
        e = achadas[0]
        self.assertIn(e["trecho"], texto)                          # nunca inventar: trecho literal
        return e

    def nenhuma(self, texto: str, **kw) -> None:
        self.assertEqual(so_promessas(texto, **kw), [], texto)

    def test_prometi_levar_ela_ao_cinema_na_sexta(self):
        e = self.uma("Prometi levar ela ao cinema na sexta. Ela adora pipoca.")
        self.assertEqual((e["texto"], e["resumo"]), ("Levar ela ao cinema", "Levar ela ao cinema"))
        self.assertEqual((e["prazo"], e["precisao"], e["prazo_expressao"]), ("2026-10-02", "dia", "sexta"))
        self.assertEqual((e["trecho"], e["forma"], e["forca"], e["feita_em"]),
                         ("Prometi levar ela ao cinema na sexta.", "prometi", "forte", DATA))
        self.assertIsNone(e["pessoa"])
        self.assertEqual(e["motivo"], "Você escreveu “Prometi”.")

    def test_ontem_prometi_sem_acento_pega_o_dia_da_promessa_e_o_prazo(self):
        e = self.uma("ontem prometi pra ela que ia fazer o jantar sabado")
        self.assertEqual((e["texto"], e["feita_em"], e["prazo"]), ("Fazer o jantar", "2026-09-29", "2026-10-03"))

    def test_fiquei_de_ligar_amanha(self):
        e = self.uma("fiquei de ligar pra ela amanha cedo")
        self.assertEqual((e["texto"], e["prazo"], e["forma"]), ("Ligar pra ela cedo", "2026-10-01", "fiquei_de"))

    def test_combinei_com_ela_domingo_que_vem_e_ambiguo(self):
        e = self.uma("Combinei com ela de ir na casa da mãe dela no domingo que vem.")
        self.assertEqual((e["texto"], e["prazo"], e["forma"]), ("Ir na casa da mãe dela", "2026-10-04", "combinei"))
        self.assertEqual(e["prazo_alternativas"], ["2026-10-11"])         # a revisão deixa escolher

    def test_vou_levar_ela_pra_praia_em_dezembro_vale_o_mes(self):
        e = self.uma("vou levar ela pra praia em dezembro")
        self.assertEqual((e["texto"], e["prazo"], e["precisao"], e["forma"], e["forca"]),
                         ("Levar ela pra praia", "2026-12-01", "mes", "vou", "media"))

    def test_tenho_q_levar_ela_no_medico_semana_q_vem(self):
        e = self.uma("tenho q levar ela no medico semana q vem")
        self.assertEqual((e["texto"], e["prazo"], e["precisao"]), ("Levar ela no medico", "2026-10-05", "semana"))

    def test_girias_e_abreviacoes(self):
        casos = {
            "prometi pra ela q ia levar ela no role sabado": ("Levar ela no role", "2026-10-03"),
            "mano, fiquei de buscar ela no trampo amanha": ("Buscar ela no trampo", "2026-10-01"),
            "tenho que ajudar ela com a mudança no fds": ("Ajudar ela com a mudança", "2026-10-03"),
        }
        for texto, (esperado, prazo) in casos.items():
            with self.subTest(texto=texto):
                e = self.uma(texto)
                self.assertEqual((e["texto"], e["prazo"]), (esperado, prazo))

    def test_prometi_pra_mae_dela_guarda_para_quem(self):
        e = self.uma("Prometi pra mãe dela que ia consertar o portão.")
        self.assertEqual((e["texto"], e["pessoa"]), ("Consertar o portão", "mãe dela"))
        self.assertIn(plano(e["pessoa"]), plano(e["trecho"]))       # a pessoa está escrita no trecho

    def test_minha_sogra_e_gente_dela(self):
        e = self.uma("Prometi pra minha sogra que ia consertar o portão")
        self.assertEqual(e["pessoa"], "minha sogra")

    def test_minha_namorada_e_ela(self):
        e = self.uma("Prometi pra minha namorada que ia aprender a dançar forró")
        self.assertEqual((e["texto"], e["pessoa"]), ("Aprender a dançar forró", None))

    def test_promessa_para_gente_sua_ou_para_si_fica_de_fora(self):
        for texto in ("Prometi pro meu chefe que entregaria o relatório amanhã.",
                      "Prometi pra mim mesmo que vou ser mais paciente com ela.",
                      "fiquei de mandar o relatório pro meu chefe amanhã",
                      "Prometi pra minha mãe que ia visitar ela no domingo."):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_promessa_dela_ou_de_outro_nao_e_sua(self):
        for texto in ("Ela prometeu que vai me ligar.", "Ela disse que ia buscar a irmã no aeroporto.",
                      "A mãe dela prometeu um bolo pra ela.", "ela ficou de me mandar as fotos"):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_negacao(self):
        for texto in ("Eu não prometi nada.", "nunca prometi levar ela pra Paris", "n prometi nd",
                      "não fiquei de buscar ela", "não vou levar ela no show", "Jamais prometi isso pra ela."):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_duvida_pergunta_e_ironia(self):
        for texto in ("Não sei se prometi levar ela no show.", "Será que eu prometi isso?",
                      "eu prometi levar ela no show?", "Prometi levar ela no show sqn",
                      "nao lembro se fiquei de buscar ela", "Se eu prometi, vou cumprir."):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_acho_que_vira_promessa_fraca(self):
        e = self.uma("Acho que prometi levar ela pra jantar sexta.")
        self.assertEqual((e["forca"], e["texto"], e["prazo"]), ("fraca", "Levar ela pra jantar", "2026-10-02"))
        self.assertIn("confira", e["motivo"])

    def test_ela_lembrou_que_eu_prometi_e_promessa(self):
        self.assertEqual(self.uma("Ela lembrou que eu prometi levar ela pra ver o mar.")["texto"],
                         "Levar ela pra ver o mar")
        self.assertEqual(self.uma("nao esquece que prometi levar ela no show do Djavan")["texto"],
                         "Levar ela no show do Djavan")

    def test_fiquei_de_sem_compromisso_nao_conta(self):
        for texto in ("fiquei de cama o dia todo", "fiquei de olho nela na festa", "fiquei de boa o dia todo",
                      "fiquei de fora da conversa"):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_vou_sem_ela_nao_e_compromisso_com_ela(self):
        for texto in ("vou levar o carro na oficina amanha", "tenho que fazer o relatório até sexta",
                      "preciso buscar meu irmão na escola"):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_combinado_com_amigo_seu_e_prometi_que_vou(self):
        # Revisão de 07/10: "combinei com o Pedro" virava promessa; "prometi... que vou" ficava "vou" (média).
        self.assertEqual(so_promessas("Combinei com o Pedro de jogar bola sábado."), [])
        [p] = so_promessas("Combinei com o Pedro de levar ela na festa.")
        self.assertEqual(p["texto"], "Levar ela na festa")
        [p] = so_promessas("Prometi pra ela que vou levar ela no cinema sexta.")
        self.assertEqual((p["texto"], p["forma"], p["forca"], p["prazo"]),
                         ("Levar ela no cinema", "prometi", "forte", "2026-10-02"))

    def test_vago_fica_de_fora(self):

        for texto in ("fiquei de ver isso", "Prometi uma coisa pra ela.", "prometi pra ela"):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_nome_dela_vale_como_ela(self):
        e = self.uma("vou levar a Ju no show sabado", nome_dela="Julia")
        self.assertEqual((e["texto"], e["prazo"]), ("Levar a Ju no show", "2026-10-03"))
        self.nenhuma("vou levar a Ju no show sabado")                 # sem o nome dela: não sei quem é

    def test_duas_promessas_cada_uma_com_seu_prazo(self):
        achadas = so_promessas("Prometi levar ela ao cinema e fiquei de ligar pra mãe dela amanhã.")
        self.assertEqual([(e["texto"], e["prazo"]) for e in achadas],
                         [("Levar ela ao cinema", None), ("Ligar pra mãe dela", "2026-10-01")])

    def test_gatilhos_encadeados_viram_uma_so(self):
        e = self.uma("combinamos que vou buscar ela as 8")
        self.assertEqual((e["texto"], e["forma"]), ("Buscar ela as 8", "combinei"))

    def test_auxiliar_sai_e_verbo_principal_vira_infinitivo(self):
        casos = {
            "eu prometi que nunca mais ia esquecer o aniversário dela": "Nunca mais esquecer o aniversário dela",
            "ela me fez prometer que eu ia no medico essa semana": "Ir no medico",
            "Prometi pra Mariana que levaria a Julia na festa": "Levar a Julia na festa",
            "Prometemos que a gente vai viajar pra Bahia ano que vem": "Viajar pra Bahia",
            "Prometi uma coisa: nunca mais atrasar nos nossos encontros.": "Nunca mais atrasar nos nossos encontros",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(self.uma(texto)["texto"], esperado)

    def test_quando_no_comeco_vai_para_o_fim(self):
        e = self.uma("Prometi que no aniversário dela eu ia fazer um jantar surpresa com as amigas dela")
        self.assertEqual(e["texto"], "Fazer um jantar surpresa com as amigas dela (no aniversário dela)")

    def test_texto_longo_e_cortado_com_reticencias(self):
        e = self.uma("Prometi fazer um jantar surpresa com as amigas dela com bolo de cenoura velas música ao vivo "
                     "e tudo mais que ela gosta")
        self.assertTrue(e["texto"].endswith("…"))
        self.assertLessEqual(len(e["texto"].split()), 15)

    def test_falei_pra_ela_que_ia_e_jurei(self):
        e = self.uma("Falei pra ela que ia buscar ela no trabalho amanhã.")
        self.assertEqual((e["texto"], e["prazo"], e["forma"]), ("Buscar ela no trabalho", "2026-10-01", "disse_que"))
        self.assertEqual(self.uma("Jurei que ia parar de fumar.")["texto"], "Parar de fumar")

    def test_resumo_so_usa_palavras_do_trecho(self):
        frases_ = ["Prometi levar ela ao cinema na sexta.", "fiquei de ligar pra ela amanha cedo",
                   "Combinei com ela de ir na casa da mãe dela no domingo que vem.", "vou levar ela pra praia em dezembro",
                   "Prometi pra mãe dela que ia consertar o portão.", "ela me fez prometer que eu ia no medico essa semana",
                   "Prometi pra Mariana que levaria a Julia na festa", "tenho que ajudar ela com a mudança no fds"]
        for texto in frases_:
            with self.subTest(texto=texto):
                for e in so_promessas(texto):
                    palavras_trecho = set(re.findall(r"[a-z]+", plano(e["trecho"])))
                    # As únicas palavras que podem mudar: o infinitivo de "ia"/"levaria" (ir, levar).
                    novas = set(re.findall(r"[a-z]+", plano(e["texto"]))) - palavras_trecho
                    self.assertLessEqual(novas, {"ir", "levar"}, e)

    def test_promessa_cumprida_dita_com_todas_as_letras(self):
        for texto in ("Levei ela no cinema ontem, como prometi.", "Cumpri a promessa de levar ela ao cinema!",
                      "Promessa cumprida: levei ela pra ver o mar!", "Levei ela no cinema, como a gente tinha combinado.",
                      "fiz o que eu tinha prometido"):
            with self.subTest(texto=texto):
                achados = extras(texto)
                self.assertEqual([e["tipo"] for e in achados], ["promessa_cumprida"], achados)
                self.assertIn(achados[0]["trecho"], texto)
                self.assertNotIn("promessa_id", achados[0])             # nada é ligado sozinho

    def test_o_show_que_prometi_fala_de_uma_promessa_antiga(self):
        for texto in ("Levei ela no show que prometi levar ela ano passado.",
                      "Hoje teve o jantar que eu tinha prometido fazer pra ela, como prometi cozinhar o risoto."):
            with self.subTest(texto=texto):
                self.nenhuma(texto)

    def test_cumprimento_negado_ou_em_duvida_nao_conta(self):
        for texto in ("Ainda não cumpri a promessa.", "Não fiz o que prometi.", "Será que cumpri o que prometi?",
                      "não sei se cumpri a promessa", "Levei ela no jantar que prometi."):
            with self.subTest(texto=texto):
                self.assertEqual(extras(texto), [], texto)

    def test_extrair_direto_aceita_objetos_e_nao_mexe_neles(self):
        datas = motor.MotorRegras().analisar("Prometi levar ela ao cinema na sexta.", REF).datas
        copia = [(d.expressao, d.data) for d in datas]
        saida = promessas.extrair("Prometi levar ela ao cinema na sexta.", REF, [], datas, [], None)
        self.assertEqual(saida[0]["prazo"], date(2026, 10, 2))
        self.assertEqual([(d.expressao, d.data) for d in datas], copia)


class TestAjudantes(unittest.TestCase):
    def test_formas_passado(self):
        casos = {"levar": {"levei", "levamos", "fomos"}, "buscar": {"busquei", "buscamos"},
                 "ligar": {"liguei", "ligamos"}, "começar": {"comecei", "comecamos"}, "fazer": {"fiz", "fizemos"},
                 "assistir": {"assisti", "assistimos", "vimos"}, "comer": {"comi", "comemos"}, "ir": {"fui", "fomos"},
                 "levá-la": {"levei", "levamos", "fomos"}, "nada": set()}
        for verbo, esperado in casos.items():
            with self.subTest(verbo=verbo):
                self.assertEqual(promessas.formas_passado(verbo), esperado)

    def test_quando_prazo_com_a_precisao_dita(self):
        casos = [((REF, "dia"), "hoje"), ((dia(1), "dia"), "amanhã"), ((dia(-1), "dia"), "ontem"),
                 ((dia(2), "dia"), "sexta (02/10)"), ((dia(9), "dia"), "sexta, 09/10"),
                 ((date(2026, 9, 28), "semana"), "esta semana"), ((date(2026, 10, 5), "semana"), "na semana que vem"),
                 ((date(2026, 12, 1), "mes"), "em dezembro"), ((date(2027, 1, 1), "mes"), "em janeiro de 2027"),
                 ((date(2026, 9, 1), "mes"), "este mês"), ((date(2027, 1, 1), "ano"), "em 2027")]
        for (prazo, precisao), esperado in casos:
            with self.subTest(prazo=prazo, precisao=precisao):
                self.assertEqual(promessas.quando_prazo(prazo, precisao, REF), esperado)

    def test_pontuar_explica_os_pontos(self):
        pontos, motivos = promessas.pontuar("Levar ela ao cinema", "Levei ela no cinema ontem, como prometi.")
        self.assertEqual(pontos, 70)
        self.assertEqual(motivos, ["a mesma ação (“levei”)", "o mesmo assunto (“cinema”)"])
        self.assertEqual(promessas.pontuar("Levar ela ao cinema", "Ela adora tulipas."), (0, []))

    def test_acao_para_o_meio_da_frase(self):
        self.assertEqual(promessas.acao("Levar ela ao cinema"), "levar ela ao cinema")
        self.assertEqual(promessas.acao("Mariana vai amar o presente"), "Mariana vai amar o presente")
        self.assertEqual(promessas.acao("Lavo a louça", "prometi que eu lavo a louça"), "lavo a louça")


# ======================================================================== núcleo (gravação, rotas, Hoje)

class BaseNucleo(unittest.TestCase):
    def setUp(self):
        self.enterContext(recursos.usando(promessas))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-promessas-")
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

    def registrar(self, texto: str, data: str = DATA, confirmar=("promessa", "promessa_cumprida"), mudar=None,
                  com_itens: bool = True, **mais) -> nucleo.RespostaApi:
        p = self.analisar(texto, data)
        escolhidos = [dict(e) for e in p["extras"].get("promessas", []) if e["tipo"] in confirmar]
        for e in escolhidos:
            e.update(mudar or {})
        corpo = {"texto": p["texto"], "data_registro": data, "itens": p["itens"] if com_itens else [],
                 "pessoas": p["pessoas"], "relacoes": p["relacoes"],
                 "extras": {"promessas": escolhidos} if escolhidos else {}, **mais}
        return self.pedir("POST", "/api/registros", corpo)

    def linhas(self) -> list[dict]:
        return self.banco.consultar("SELECT * FROM promessas ORDER BY id")

    def nova(self, texto: str = "Levar ela ao cinema", prazo: str | None = None) -> int:
        r = self.pedir("POST", "/api/promessas", {"texto": texto, "prazo": prazo})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def hoje(self, data: date = REF) -> list[dict]:
        r = self.pedir("GET", "/api/hoje", consulta={"data": data.isoformat()})
        self.assertEqual(r.status, 200, r.dados)
        return [a for a in r.dados["alertas"] if a.get("recurso") == "promessas"]


class TestGravar(BaseNucleo):
    def test_confirmar_grava_com_prazo_trecho_e_registro(self):
        r = self.registrar("Prometi levar ela ao cinema na sexta. Ela adora pipoca.")
        self.assertEqual(r.status, 200, r.dados)
        [p] = self.linhas()
        self.assertEqual((p["texto"], p["prazo"], p["precisao"], p["prazo_expressao"], p["status"], p["registro_id"],
                          p["feita_em"], p["forma"]),
                         ("Levar ela ao cinema", "2026-10-02", "dia", "sexta", "aberta", r.dados["id"], DATA, "prometi"))
        self.assertEqual(p["trecho"], "Prometi levar ela ao cinema na sexta.")
        self.assertFalse(self.pedir("GET", f"/api/registros/{r.dados['id']}").dados["privado"])   # não vira 🔒

    def test_sem_confirmar_nada_e_gravado(self):
        self.assertEqual(self.registrar("Prometi levar ela ao cinema na sexta.", confirmar=()).status, 200)
        self.assertEqual(self.linhas(), [])

    def test_texto_e_prazo_ajustados_na_revisao(self):
        self.registrar("Prometi levar ela ao cinema na sexta.",
                       mudar={"texto": "levar ela pra ver o filme novo", "prazo": "2026-10-09", "precisao": "dia",
                              "prazo_expressao": None})
        [p] = self.linhas()
        self.assertEqual((p["texto"], p["prazo"], p["prazo_expressao"]), ("Levar ela pra ver o filme novo", "2026-10-09", None))

    def test_prazo_invalido_400_e_nada_gravado(self):
        r = self.registrar("Prometi levar ela ao cinema na sexta.", mudar={"prazo": "sexta-feira"})
        self.assertEqual((r.status, r.dados["erro"]), (400, promessas.PRAZO_INVALIDO))
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])
        self.assertEqual(self.linhas(), [])

    def test_texto_apagado_na_revisao_vira_o_trecho_e_grande_demais_e_400(self):
        # Sem texto, a fundação põe o trecho no resumo: a promessa fica com as palavras escritas.
        self.assertEqual(self.registrar("Prometi levar ela ao cinema na sexta.",
                                        mudar={"texto": " ", "resumo": " "}).status, 200)
        self.assertEqual(self.linhas()[0]["texto"], "Prometi levar ela ao cinema na sexta.")
        r = self.registrar("Fiquei de ligar pra ela amanhã.", mudar={"texto": "x" * 301})
        self.assertEqual((r.status, r.dados["erro"]), (400, promessas.TEXTO_INVALIDO))
        self.assertEqual(len(self.linhas()), 1)

    def test_nunca_inventar_pessoa_e_expressao_que_nao_estao_no_trecho(self):
        self.registrar("Prometi levar ela ao cinema na sexta.",
                       mudar={"pessoa": "Carla, a melhor amiga", "prazo_expressao": "no Natal"})
        [p] = self.linhas()
        self.assertEqual((p["pessoa"], p["prazo_expressao"]), (None, None))

    def test_mesma_promessa_contada_de_novo_nao_duplica(self):
        self.registrar("Prometi levar ela ao cinema.")
        self.registrar("De novo: prometi levar ela ao cinema.", data="2026-10-01")
        self.assertEqual(len(self.linhas()), 1)
        self.registrar("Fiquei de levar ela ao cinema na sexta.")       # com prazo novo: é outra combinação
        self.assertEqual(len(self.linhas()), 2)

    def test_cumprida_escolhida_na_revisao(self):
        rid = self.registrar("Prometi levar ela ao cinema na sexta.").dados["id"]
        [p] = self.linhas()
        candidatas = self.pedir("POST", "/api/promessas/candidatas", {"texto": "Levei ela no cinema, como prometi."}).dados
        self.assertEqual([(c["id"], c["pontos"]) for c in candidatas], [(p["id"], 70)])
        self.assertEqual(candidatas[0]["motivos"], ["a mesma ação (“levei”)", "o mesmo assunto (“cinema”)"])
        r = self.registrar("Levei ela no cinema, como prometi.", data="2026-10-03", mudar={"promessa_id": p["id"]})
        self.assertEqual(r.status, 200, r.dados)
        [p] = self.linhas()
        self.assertEqual((p["status"], p["concluida_em"], p["concluida_registro_id"]), ("cumprida", "2026-10-03", r.dados["id"]))
        self.assertNotEqual(r.dados["id"], rid)

    def test_cumprida_sem_escolha_nao_muda_nada_e_id_estranho_e_400(self):
        self.registrar("Prometi levar ela ao cinema na sexta.")
        self.assertEqual(self.registrar("Levei ela no cinema, como prometi.").status, 200)
        self.assertEqual(self.linhas()[0]["status"], "aberta")
        r = self.registrar("Cumpri a promessa!", mudar={"promessa_id": "1"})
        self.assertEqual((r.status, r.dados["erro"]), (400, promessas.CUMPRIDA_INVALIDA))
        self.assertEqual(self.registrar("Cumpri a promessa!", mudar={"promessa_id": 999}).status, 200)   # sumiu: ignora


class TestRotas(BaseNucleo):
    def test_criar_a_mao_e_listar(self):
        pid = self.nova("levar ela pra ver o mar", "2026-10-12")
        dados = self.pedir("GET", "/api/promessas").dados
        [p] = dados["abertas"]
        self.assertEqual((p["id"], p["texto"], p["trecho"], p["forma"], p["registro_id"], p["prazo"], p["prazo_texto"],
                          p["situacao"], p["feita_quando"]),
                         (pid, "Levar ela pra ver o mar", "Levar ela pra ver o mar", "manual", None, "2026-10-12",
                          "segunda, 12/10", "no_prazo", "hoje"))
        self.assertEqual((dados["cumpridas"], dados["deixadas"], dados["momento"]), ([], [], None))

    def test_ordem_atrasada_hoje_chegando_e_sem_prazo(self):
        sem = self.nova("Aprender a fazer pão")
        longe = self.nova("Levar ela pra Ouro Preto", "2026-11-20")
        hoje = self.nova("Ligar pra ela", DATA)
        atrasada = self.nova("Arrumar o chuveiro", "2026-09-25")
        perto = self.nova("Buscar ela no trabalho", "2026-10-02")
        ordem = [p["id"] for p in self.pedir("GET", "/api/promessas").dados["abertas"]]
        self.assertEqual(ordem, [atrasada, hoje, perto, longe, sem])

    def test_cumprir_deixar_de_lado_e_reabrir(self):
        pid = self.nova()
        r = self.pedir("PATCH", f"/api/promessas/{pid}", {"status": "cumprida"})
        self.assertEqual((r.status, r.dados["promessa"]["status"], r.dados["promessa"]["concluida_em"]),
                         (200, "cumprida", DATA))
        self.assertEqual(len(self.pedir("GET", "/api/promessas").dados["cumpridas"]), 1)
        self.pedir("PATCH", f"/api/promessas/{pid}", {"status": "desisti"})
        self.assertEqual(len(self.pedir("GET", "/api/promessas").dados["deixadas"]), 1)
        r = self.pedir("PATCH", f"/api/promessas/{pid}", {"status": "aberta"})
        self.assertEqual((r.dados["promessa"]["status"], r.dados["promessa"]["concluida_em"]), ("aberta", None))

    def test_cumprida_com_o_registro_que_mostrou_usa_a_data_dele(self):
        pid = self.nova()
        rid = self.registrar("Levei ela no cinema e ela amou.", data="2026-10-04").dados["id"]
        r = self.pedir("PATCH", f"/api/promessas/{pid}", {"status": "cumprida", "registro_id": rid})
        p = r.dados["promessa"]
        self.assertEqual((p["concluida_em"], p["concluida_registro_id"]), ("2026-10-04", rid))
        r = self.pedir("PATCH", f"/api/promessas/{pid}", {"status": "cumprida", "registro_id": 999})
        self.assertEqual((r.status, r.dados["erro"]), (400, promessas.REGISTRO_INVALIDO))

    def test_editar_texto_e_prazo(self):
        pid = self.nova("levar ela ao cinema", "2026-10-02")
        r = self.pedir("PATCH", f"/api/promessas/{pid}", {"texto": "levar ela ao cinema e jantar", "prazo": None})
        p = r.dados["promessa"]
        self.assertEqual((p["texto"], p["prazo"], p["precisao"], p["trecho"]),
                         ("Levar ela ao cinema e jantar", None, None, "Levar ela ao cinema e jantar"))

    def test_pedidos_que_nao_servem(self):
        pid = self.nova()
        casos = [
            ("POST", "/api/promessas", {"texto": ""}, 400),
            ("POST", "/api/promessas", {"texto": "x" * 301}, 400),
            ("POST", "/api/promessas", {"texto": "ok", "prazo": "amanhã"}, 400),
            ("POST", "/api/promessas", {"texto": "ok", "registro_id": 1}, 400),
            ("PATCH", f"/api/promessas/{pid}", {"status": "talvez"}, 400),
            ("PATCH", f"/api/promessas/{pid}", {"id": 3}, 400),
            ("PATCH", f"/api/promessas/{pid}", {}, 400),
            ("PATCH", f"/api/promessas/{pid}", {"registro_id": 1}, 400),
            ("PATCH", "/api/promessas/999", {"status": "cumprida"}, 404),
            ("DELETE", "/api/promessas/999", None, 404),
            ("POST", "/api/promessas/parecidas", {"texto": "", "outra": 1}, 400),
        ]
        for metodo, caminho, corpo, status in casos:
            with self.subTest(caminho=caminho, corpo=corpo):
                self.assertEqual(self.pedir(metodo, caminho, corpo).status, status)
        self.assertEqual(self.pedir("GET", "/api/promessas", token=None).status, 401)
        self.assertEqual(len(self.linhas()), 1)

    def test_lotacao(self):
        with mock.patch.object(promessas, "MAX_PROMESSAS", 1):
            self.nova()
            r = self.pedir("POST", "/api/promessas", {"texto": "Outra"})
            self.assertEqual((r.status, r.dados["erro"]), (400, promessas.DEMAIS))

    def test_parecidas_avisa_antes_de_criar_outra_igual(self):
        pid = self.nova("Levar ela ao cinema")
        self.nova("Ligar pra mãe dela")
        dados = self.pedir("POST", "/api/promessas/parecidas", {"texto": "levar ela ao cinema"}).dados
        self.assertEqual([(d["id"], d["igual"]) for d in dados], [(pid, True)])
        self.assertEqual(self.pedir("POST", "/api/promessas/parecidas", {"texto": "aprender violão"}).dados, [])

    def test_apagar_some_do_banco_das_copias_e_dos_avisos(self):
        pid = self.nova("Levar ela ao cinema", DATA)
        self.assertTrue(self.hoje())
        self.pedir("POST", "/api/alertas/dispensar", {"chave": f"promessas:p{pid}:prazo:{DATA}:hoje"})
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/promessas/{pid}").status, 200)
        self.assertEqual(self.linhas(), [])
        self.assertFalse(any(c.startswith("promessas:") for c in self.banco.alertas_dispensados()))
        self.assertEqual(len(repetir), 1)
        repetir[0](copia)                                         # o que o cofre faria em cada cópia
        self.assertEqual(copia.consultar("SELECT * FROM promessas"), [])
        self.assertFalse(any(c.startswith("promessas:") for c in copia.alertas_dispensados()))
        historico = json.dumps(self.pedir("GET", "/api/historico").dados, ensure_ascii=False)
        self.assertIn(f"promessas #{pid} apagado", historico)
        self.assertNotIn("cinema", historico)                   # histórico sem conteúdo


class TestHoje(BaseNucleo):
    def test_prazo_hoje_amanha_e_perto_viram_lembretes(self):
        hoje = self.nova("levar ela ao cinema", DATA)
        amanha = self.nova("ligar pra ela", "2026-10-01")
        perto = self.nova("buscar ela no trabalho", "2026-10-03")
        self.nova("levar ela pra Ouro Preto", "2026-10-20")
        cartoes = {c["dados"]["id"]: c for c in self.hoje()}
        self.assertEqual(set(cartoes), {hoje, amanha, perto})
        self.assertEqual(cartoes[hoje]["texto"], "Hoje: levar ela ao cinema.")
        self.assertEqual(cartoes[amanha]["texto"], "Amanhã: ligar pra ela.")
        self.assertEqual(cartoes[perto]["texto"], "Sábado (03/10): buscar ela no trabalho.")
        self.assertEqual([cartoes[i]["relevancia"] for i in (hoje, amanha, perto)], [86, 80, 70])
        self.assertTrue(all(c["nivel"] == "lembrete" and c["tipo"] == "promessa_prazo" for c in cartoes.values()))
        self.assertEqual(cartoes[hoje]["chave"], f"promessas:p{hoje}:prazo:{DATA}:hoje")
        self.assertIn("Você anotou esta promessa em 30/09", cartoes[hoje]["porque"])

    def test_dispensar_nao_volta_mas_a_proxima_fase_volta(self):
        pid = self.nova("levar ela ao cinema", "2026-10-01")
        [c] = self.hoje()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        self.assertEqual(self.hoje(), [])
        self.assertEqual(len(self.hoje(dia(1))), 1)                  # no dia: outra fase, outra chave
        self.pedir("PATCH", f"/api/promessas/{pid}", {"prazo": "2026-10-01"})   # mexeu: os avisos voltam
        self.assertEqual(len(self.hoje()), 1)

    def test_evento_do_proprio_registro_nao_duplica_o_aviso_do_nucleo(self):
        self.registrar("Prometi levar ela ao cinema na sexta.")       # as regras também criam o EVENTO
        self.assertEqual(self.hoje(dia(1)), [])
        nucleo_ = self.pedir("GET", "/api/hoje", consulta={"data": "2026-10-01"}).dados["alertas"]
        self.assertTrue(any("cinema" in a["texto"] for a in nucleo_))   # quem lembra é o Hoje de sempre
        self.registrar("Fiquei de levar ela pra ver o mar sábado.", com_itens=False)   # sem o EVENTO: lembra aqui
        self.assertEqual([c["dados"]["texto"] for c in self.hoje(dia(1))], ["Levar ela pra ver o mar"])

    def test_algo_dela_no_mesmo_dia_entra_no_porque(self):
        self.registrar("A prova de estatística dela é sexta e ela está nervosa.", confirmar=())
        pid = self.nova("levar ela ao cinema", "2026-10-02")
        [c] = self.hoje()
        self.assertIn("No mesmo dia ela tem: ", c["porque"])
        self.assertIn("Talvez valha combinar com ela se o dia ainda funciona.", c["porque"])
        vista = self.pedir("GET", "/api/promessas").dados["abertas"][0]
        self.assertEqual(vista["id"], pid)
        self.assertEqual(len(vista["conflitos"]), 1)
        self.assertIn(vista["conflitos"][0]["registro_id"], c["registro_ids"])

    def test_prazo_que_passou_vira_sugestao_gentil_e_depois_some(self):
        self.nova("arrumar o chuveiro", "2026-09-28")
        [c] = self.hoje()
        self.assertEqual((c["nivel"], c["tipo"], c["relevancia"]), ("sugestao", "promessa_gentil", 56))
        self.assertEqual(c["texto"], "Talvez ainda dê tempo para “arrumar o chuveiro”: o combinado era segunda, 28/09.")
        self.assertEqual(self.hoje(dia(40)), [])                      # muito tempo depois: deixa quieto

    def test_aberta_ha_mais_de_14_dias_sem_prazo(self):
        self.nova("levar ela pra ver o mar")
        self.assertEqual(self.hoje(dia(14)), [])
        [c] = self.hoje(dia(20))
        self.assertEqual(c["texto"], "Há 20 dias você prometeu “levar ela pra ver o mar”. Talvez seja um bom momento; "
                                     "e se você mudou de ideia, tudo bem deixar de lado.")
        self.assertEqual((c["nivel"], c["relevancia"]), ("sugestao", 44))

    def test_so_uma_sugestao_de_promessas_no_hoje(self):
        self.nova("levar ela pra ver o mar")
        self.nova("aprender a fazer pão")
        self.nova("arrumar o chuveiro", "2026-10-15")
        alertas = self.pedir("GET", "/api/hoje", consulta={"data": dia(20).isoformat()}).dados["alertas"]
        self.assertEqual(len([a for a in alertas if a.get("recurso") == "promessas" and a["nivel"] == "sugestao"]), 1)

    def test_momento_dificil_dela_muda_o_tom(self):
        self.nova("levar ela pra ver o mar")
        sem = self.hoje(dia(20))[0]["relevancia"]
        quando = dia(19)
        self.banco.criar_registro("Ela está muito cansada com o trabalho.", quando, [
            Item(tipo=TipoItem.ESTADO, resumo="Muito cansada", trecho="Ela está muito cansada com o trabalho.",
                 certeza=Certeza.OBSERVADO, categorias=["Preocupações"], data_evento=quando, polaridade=-1)], [], [])
        self.assertEqual(self.hoje(dia(20))[0]["relevancia"], sem - 12)    # não é hora de lembrar disso
        momento = promessas.painel(self.banco, dia(20))["momento"]
        self.assertEqual((momento["trecho"], momento["data"]), ("Ela está muito cansada com o trabalho.", quando))
        self.assertIsNone(promessas.painel(self.banco, dia(30))["momento"])     # passou: ela pode estar bem

    def test_momento_dificil_com_o_que_ajuda_vira_carinho(self):
        self.nova("fazer uma massagem nela")
        quando = dia(19)
        self.banco.criar_registro("Ela está estressada. O que ajuda é massagem.", quando, [
            Item(tipo=TipoItem.ESTADO, resumo="Estressada", trecho="Ela está estressada.", certeza=Certeza.OBSERVADO,
                 categorias=["Preocupações"], data_evento=quando, polaridade=-1),
            Item(tipo=TipoItem.APOIO, resumo="Massagem ajuda", trecho="O que ajuda é massagem.",
                 certeza=Certeza.OBSERVADO, alvo="massagem", polaridade=1)], [], [])
        [c] = self.hoje(dia(20))
        self.assertEqual(c["relevancia"], 44 + 12)
        self.assertIn("E ela já disse o que ajuda: “O que ajuda é massagem.”", c["porque"])
        self.assertIn("Talvez cumprir isso agora seja um carinho.", c["porque"])

    def test_parece_que_voce_cumpriu(self):
        self.registrar("Prometi levar ela ao cinema.")
        depois = self.registrar("Ontem levei ela no cinema e ela amou a pipoca.", data="2026-10-03", confirmar=()).dados["id"]
        [c] = self.hoje(dia(3))
        self.assertEqual((c["nivel"], c["tipo"], c["texto"]),
                         ("sugestao", "promessa_cumprida_talvez", "Parece que você cumpriu “levar ela ao cinema” 💙"))
        self.assertIn("Em 03/10 você registrou: “Ontem levei ela no cinema e ela amou a pipoca.”", c["porque"])
        self.assertEqual(c["registro_ids"][0], depois)
        self.assertEqual(c["chave"], f"promessas:p{c['dados']['id']}:cumpriu:r{depois}:")
        # "Sim, cumpri" pela tela: a data é a do registro que mostrou.
        r = self.pedir("PATCH", f"/api/promessas/{c['dados']['id']}", {"status": "cumprida", "registro_id": depois})
        self.assertEqual(r.dados["promessa"]["concluida_em"], "2026-10-03")
        self.assertEqual(self.hoje(dia(3)), [])

    def test_hoje_procura_so_nos_ultimos_90_dias_e_a_tela_em_todos(self):
        self.registrar("Prometi levar ela ao cinema.")
        self.registrar("Levei ela no cinema e ela amou.", data="2026-10-03", confirmar=())
        self.assertEqual([c["tipo"] for c in self.hoje(dia(3))], ["promessa_cumprida_talvez"])
        longe = dia(200)
        self.assertNotIn("promessa_cumprida_talvez", [c["tipo"] for c in self.hoje(longe)])
        [p] = promessas.painel(self.banco, longe)["abertas"]
        self.assertEqual(p["indicio"]["trecho"], "Levei ela no cinema e ela amou.")

    def test_preposicao_nao_e_o_mesmo_assunto(self):
        # Revisão de 07/10: "para" casava com "paraty" pelas 4 primeiras letras, e o Hoje dizia que você
        # tinha cumprido "levar ela pra Paraty" por causa de um café levado para ela.
        self.registrar("Prometi levar ela pra Paraty em dezembro.", data="2026-07-06")
        self.registrar("Levei café para ela no trabalho.", data="2026-09-20", confirmar=())
        self.assertNotIn("promessa_cumprida_talvez", [c["tipo"] for c in self.hoje(REF)])
        self.assertTrue(promessas._mesmo_termo("cinema", "cinemas"))
        self.assertTrue(promessas._mesmo_termo("bolo", "bolos"))
        self.assertFalse(promessas._mesmo_termo("paraty", "para"))
        self.assertFalse(promessas._mesmo_termo("pela", "pelada"))

    def test_indicio_de_registro_do_ciclo_nao_cita_a_frase(self):
        from entrenos.recursos import ciclo
        with recursos.usando(promessas, ciclo):
            self.registrar("Prometi comprar chocolate pra ela.", data="2026-09-01")
            self.registrar("Ela menstruou hoje e eu comprei chocolate pra ela. Ela amou!", data="2026-09-20",
                           confirmar=())
            [c] = [c for c in self.hoje(date(2026, 9, 24)) if c["tipo"] == "promessa_cumprida_talvez"]
            self.assertNotIn("menstru", json.dumps(c, ensure_ascii=False).lower())
            self.assertTrue(c["dados"]["indicio"]["discreto"])
            self.assertEqual(c["dados"]["indicio"]["trecho"], "")
            [p] = self.pedir("GET", "/api/promessas", consulta={}).dados["abertas"]
            self.assertNotIn("menstru", json.dumps(p, ensure_ascii=False).lower())

    def test_nao_e_cumprimento_quando_negado_outro_assunto_ou_antes(self):

        for texto, data in (("Ainda não levei ela no cinema.", "2026-10-03"), ("Levei ela no shopping.", "2026-10-03"),
                            ("Levei ela no cinema semana retrasada.", "2026-09-20"),
                            ("Vou levar ela no cinema de novo.", "2026-10-03")):
            with self.subTest(texto=texto):
                self.registrar(texto, data=data, confirmar=())
        self.registrar("Prometi levar ela ao cinema.")
        self.assertEqual([c["tipo"] for c in self.hoje(dia(3))], [])
        self.assertIsNone(self.pedir("GET", "/api/promessas", consulta={}).dados["abertas"][0]["indicio"])


class TestExportarImportarApagar(BaseNucleo):
    def test_exportar_e_juntar_noutro_diario_sem_duplicar(self):
        rid = self.registrar("Prometi pra mãe dela que ia consertar o portão.").dados["id"]
        self.nova("Aprender a fazer pão", "2026-10-20")
        exportado = self.pedir("GET", "/api/exportar").dados
        lista = exportado["recursos"]["promessas"]["promessas"]
        self.assertEqual([(p["texto"], p["registro_id"], p["pessoa"]) for p in lista],
                         [("Consertar o portão", rid, "mãe dela"), ("Aprender a fazer pão", None, None)])
        texto = json.dumps(exportado, ensure_ascii=False)
        with recursos.usando(promessas):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            destino.criar_registro("Um registro de antes.", REF, [], [], [])
            previa = destino.importar_json(texto, previa=True)
            self.assertEqual(previa["recursos"]["promessas"]["novos"], 2)
            self.assertEqual(destino.consultar("SELECT * FROM promessas"), [])
            r = destino.importar_json(texto)
            self.assertEqual(r["recursos"]["promessas"], {"novos": 2, "texto": "2 promessas novas."})
            novo = [x["id"] for x in destino.registros() if "portão" in x["texto"]][0]
            linhas = destino.consultar("SELECT texto, registro_id, pessoa, prazo FROM promessas ORDER BY id")
            self.assertEqual(linhas, [{"texto": "Consertar o portão", "registro_id": novo, "pessoa": "mãe dela", "prazo": None},
                                      {"texto": "Aprender a fazer pão", "registro_id": None, "pessoa": None,
                                       "prazo": "2026-10-20"}])
            r = destino.importar_json(texto)
            self.assertEqual(r["recursos"]["promessas"]["novos"], 0)
            self.assertEqual(len(destino.consultar("SELECT * FROM promessas")), 2)

    def test_importar_tolerante_e_nunca_inventa(self):
        rid = self.registrar("Prometi levar ela ao cinema na sexta.").dados["id"]
        exportado = self.pedir("GET", "/api/exportar").dados
        lista = exportado["recursos"]["promessas"]["promessas"]
        lista.append({"texto": "Viajar pra Paris", "registro_id": rid, "trecho": "Prometi viajar pra Paris."})  # inventada
        lista.append({"texto": "", "registro_id": None})
        lista.append("nem é objeto")
        lista.append({"texto": "Sem registro", "registro_id": 777})
        lista.append({"texto": "À mão", "registro_id": None, "status": "inventado", "prazo": "nunca"})
        with recursos.usando(promessas):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            r = destino.importar_json(json.dumps(exportado, ensure_ascii=False))
            resumo = r["recursos"]["promessas"]
            self.assertEqual(resumo["novos"], 2)
            self.assertIn("4 ficaram de fora", resumo["texto"])
            linhas = destino.consultar("SELECT texto, status, prazo FROM promessas ORDER BY id")
            self.assertEqual(linhas, [{"texto": "Levar ela ao cinema", "status": "aberta", "prazo": "2026-10-02"},
                                      {"texto": "À mão", "status": "aberta", "prazo": None}])
            outro = Banco.novo()
            self.addCleanup(outro.fechar)
            ruim = {**exportado, "recursos": {"promessas": {"promessas": "não é lista"}}}
            r = outro.importar_json(json.dumps(ruim, ensure_ascii=False))
            self.assertIn(recursos.AVISO_IMPORTAR.format(rotulo="Promessas"), r["avisos"])

    def test_apagar_o_registro_apaga_a_promessa_e_as_chaves(self):
        rid = self.registrar("Fiquei de levar ela pra ver o mar sábado.", com_itens=False).dados["id"]
        outro = self.registrar("Prometi aprender a fazer pão.").dados["id"]
        [c] = self.hoje()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertEqual([p["registro_id"] for p in self.linhas()], [outro])
        self.assertFalse(any(k.startswith("promessas:") for k in self.banco.alertas_dispensados()))
        exportado = json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False)
        self.assertNotIn("ver o mar", exportado)                 # apagar é apagar: nem no export

    def test_apagar_o_registro_que_mostrou_o_cumprimento_tira_a_ligacao_e_a_data(self):
        self.registrar("Prometi levar ela ao cinema.")
        prova = self.registrar("Levei ela no cinema ontem.", data="2026-10-03", confirmar=()).dados["id"]
        [p] = self.linhas()
        self.pedir("PATCH", f"/api/promessas/{p['id']}", {"status": "cumprida", "registro_id": prova})
        self.pedir("POST", "/api/alertas/dispensar", {"chave": f"promessas:p{p['id']}:cumpriu:r{prova}:"})
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{prova}").status, 200)
        [p] = self.linhas()
        self.assertEqual((p["status"], p["concluida_em"], p["concluida_registro_id"]), ("cumprida", None, None))
        self.assertFalse(any(k.startswith("promessas:") for k in self.banco.alertas_dispensados()))


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

    def test_promessa_anotada_a_mao_nunca_vai_para_a_ia(self):
        self.banco.salvar_config(ia_principal=IA)
        cadeia = self.CadeiaFalsa()
        self.enterContext(mock.patch.object(motor, "obter_cadeia_ia", lambda ajustes: cadeia))
        self.registrar("Ela adora tulipas amarelas.", confirmar=())
        self.nova("Levar ela pra ver a aurora boreal")
        self.assertEqual(self.pedir("POST", "/api/ia/sugestao", {}).status, 200)
        self.assertEqual(self.pedir("POST", "/api/ia/busca", {"q": "aurora boreal tulipas"}).status, 200)
        self.assertTrue(cadeia.recebido)
        self.assertFalse(any("aurora" in r for r in cadeia.recebido))
        self.assertEqual(recursos.fora_da_ia(self.banco), set())        # e não tira registro nenhum da IA

    def test_promessa_nao_deixa_o_registro_fora_da_ia(self):
        r = self.registrar("Prometi levar ela ao cinema na sexta.")
        self.assertNotIn("privado", r.dados)
        self.assertNotIn(recursos.AVISO_PRIVADO, self.analisar("Prometi levar ela ao cinema na sexta.")["avisos"])

    def test_log_sem_conteudo_quando_algo_falha(self):
        self.nova("Levar ela pra ver a aurora boreal", DATA)
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        with mock.patch.object(promessas, "_vista", side_effect=RuntimeError("Levar ela pra ver a aurora boreal")):
            r = self.pedir("GET", "/api/hoje")
            self.assertEqual(r.status, 200)                          # o Hoje abre sem os cartões daqui
            self.assertEqual(self.pedir("GET", "/api/promessas").status, 500)
        texto_log = "\n".join(coletor.mensagens)
        self.assertIn("promessas", texto_log)
        self.assertIn("RuntimeError", texto_log)
        self.assertNotIn("aurora", texto_log)

    def test_trecho_de_cada_promessa_esta_no_registro(self):
        textos = ["Prometi levar ela ao cinema na sexta. Ela adora pipoca.",
                  "mano, fiquei de buscar ela no trampo amanha",
                  "Combinei com ela de ir na casa da mãe dela no domingo que vem."]
        for texto in textos:
            self.registrar(texto)
        for p in self.linhas():
            registro = self.pedir("GET", f"/api/registros/{p['registro_id']}").dados
            self.assertIn(p["trecho"], registro["texto"])


class TestTela(unittest.TestCase):
    def test_tela_sem_html_montado_nem_estilo_inline(self):
        js = (RAIZ / "web" / "telas" / "promessas.js").read_text(encoding="utf-8")
        for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".style.",
                         "style=", "localStorage", "sessionStorage"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, js)
        for registro in ("registrarMenu('promessas'", "registrarTela('promessas'", "registrarRevisaoExtra('promessa'",
                         "registrarRevisaoExtra('promessa_cumprida'", "registrarCartaoHoje('promessa_prazo'",
                         "registrarCartaoHoje('promessa_gentil'", "registrarCartaoHoje('promessa_cumprida_talvez'"):
            with self.subTest(registro=registro):
                self.assertIn(registro, js)
        self.assertIn("window.EntreNos", js)

    def test_tipos_dos_cartoes_e_dos_extras_batem_com_a_tela(self):
        js = (RAIZ / "web" / "telas" / "promessas.js").read_text(encoding="utf-8")
        for tipo in promessas.TIPOS_EXTRAS:
            self.assertIn(f"registrarRevisaoExtra('{tipo}'", js)
        fonte = (RAIZ / "entrenos" / "recursos" / "promessas.py").read_text(encoding="utf-8")
        for tipo in set(re.findall(r'tipo="(promessa[a-z_]*)"', fonte)):
            self.assertIn(f"registrarCartaoHoje('{tipo}'", js)


if __name__ == "__main__":
    unittest.main()
