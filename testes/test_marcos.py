"""Marcos, linha do tempo e "Neste dia" (DESIGN.md, seção 20.6).

Só este recurso entra na lista (`recursos.usando(marcos)`): os outros estão sendo escritos ao mesmo
tempo. Cobre a extração por regras (frases de conversa, gírias, sem acento, datas relativas que o
Python calcula, negação, plano, dúvida, pergunta, ironia, ex, marcos de outras pessoas), a gravação
conferida (nada inventado: trecho literal, expressão e pessoa escritas no trecho), as rotas (listar,
criar, editar, apagar e as cópias, parecidos, linha do tempo, neste dia), os marcos de tempo juntos
(Ajustes ou o marco de namoro; sem duplicar os avisos do núcleo), os cartões do Hoje ("Neste dia",
há 6 meses, aniversário de um marco, o cuidado com uma perda dela, o momento dela, 29/02),
exportar/importar/apagar registro e os limites de privacidade (nada vai para a IA; o que os
recursos deixam fora da IA nunca volta no Hoje; log e histórico sem texto).

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
from entrenos.recursos import marcos
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
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-marcos-env-")
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


def extras(texto: str, referencia: date = REF, nome_dela: str | None = "Julia") -> list[dict]:
    """O que o recurso propõe para o texto, como a revisão recebe (pelo motor e pela fundação)."""
    with recursos.usando(marcos):
        proposta = motor.MotorRegras().analisar(texto, referencia, Conhecimento(pessoas=[], nome_dela=nome_dela))
    return proposta.extras.get("marcos", [])


def d(texto: str) -> date:
    return date.fromisoformat(texto)


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


# ======================================================================== extração

class TestExtrair(unittest.TestCase):
    def um(self, texto: str, **kw) -> dict:
        achados = extras(texto, **kw)
        self.assertEqual(len(achados), 1, achados)
        e = achados[0]
        self.assertEqual(e["tipo"], "marco")
        self.assertIn(e["trecho"], texto)                           # nunca inventar: trecho literal
        if e.get("data_expressao"):
            self.assertIn(plano(e["data_expressao"]), plano(e["trecho"]))
        return e

    def nenhum(self, texto: str, **kw) -> None:
        self.assertEqual(extras(texto, **kw), [], texto)

    def test_nosso_primeiro_encontro_foi_ontem(self):
        e = self.um("Ontem foi nosso primeiro encontro, fomos no Outback e ela amou.")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data"], e["precisao"], e["data_expressao"], e["data_origem"]),
                         ("primeiro_encontro", "Primeiro encontro", "2026-09-29", "dia", "Ontem", "texto"))
        self.assertEqual((e["resumo"], e["forca"], e["motivo"]),
                         ("Primeiro encontro (29/09/2026)", "forte", "Você escreveu “nosso primeiro encontro”."))
        self.assertIsNone(e["nota_data"])

    def test_pedi_ela_em_namoro_dia_20_vale_a_ultima_vez_que_passou(self):
        # As regras de datas leem "dia 20" (dito em 30/09) como 20/10; o pedido já aconteceu: 20/09.
        e = self.um("Pedi ela em namoro dia 20 e ela aceitou!")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data"], e["data_origem"], e["data_expressao"]),
                         ("namoro", "Pedido de namoro", "2026-09-20", "ajustada", "dia 20"))
        self.assertIn("a última vez que “dia 20” passou", e["nota_data"])

    def test_dissemos_eu_te_amo_no_sabado_e_o_sabado_que_passou(self):
        e = self.um("Dissemos eu te amo pela primeira vez no sábado.")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data"], e["data_origem"]),
                         ("eu_te_amo", "Primeiro “eu te amo”", "2026-09-26", "ajustada"))

    def test_primeira_viagem_com_destino_e_mes(self):
        e = self.um("Nossa primeira viagem juntos foi pra Paraty em julho.")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["descricao"], e["data"], e["precisao"]),
                         ("viagem", "Primeira viagem juntos", "Paraty", "2026-07-01", "mes"))
        self.assertEqual(e["resumo"], "Primeira viagem juntos (julho de 2026)")

    def test_fizemos_a_primeira_viagem_em_mes_barra_ano(self):
        e = self.um("Fizemos nossa primeira viagem juntos pra Gramado em 07/2025.")
        self.assertEqual((e["descricao"], e["data"], e["precisao"]), ("Gramado", "2025-07-01", "mes"))

    def test_conheci_a_mae_dela_domingo_passado(self):
        e = self.um("Conheci a mãe dela no domingo passado, foi tranquilo.")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["pessoa"], e["data"]),
                         ("familia", "Você conheceu a mãe dela", "mãe dela", "2026-09-27"))

    def test_conheci_o_pai_dela_com_o_nome_que_o_texto_disse(self):
        e = self.um("Conheci o pai dela, seu Roberto, ontem.")
        self.assertEqual((e["titulo"], e["data"]), ("Você conheceu o pai dela (Roberto)", "2026-09-29"))

    def test_conheci_os_pais_dela_em_12_03(self):
        # Desde a integração, regras/datas.py lê "conheci" como passado: a data já vem certa do texto
        # (antes vinha 12/03/2027 e o recurso ajustava).
        e = self.um("conheci os pais dela em 12/03")
        self.assertEqual((e["titulo"], e["data"], e["data_origem"]), ("Você conheceu os pais dela", "2026-03-12", "texto"))

    def test_familia_da_julia_pelo_nome_dela(self):
        e = self.um("conheci a familia da Julia semana passada")
        self.assertEqual((e["marco_tipo"], e["data"], e["precisao"]), ("familia", "2026-09-21", "semana"))

    def test_ela_conheceu_minha_mae_vira_sua_mae(self):
        e = self.um("Ela conheceu minha mãe no almoço de domingo.")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data"]), ("familia", "Ela conheceu sua mãe", "2026-09-27"))

    def test_apresentado_e_casa_dos_pais(self):
        e = self.um("fui apresentado aos pais dela no domingo")
        self.assertEqual((e["titulo"], e["data"]), ("Você conheceu os pais dela", "2026-09-27"))
        e = self.um("Primeira vez na casa dos pais dela, a mãe dela fez lasanha.")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data_origem"]),
                         ("familia", "Primeira vez na casa dos pais dela", "registro"))

    def test_hoje_faz_1_ano_do_primeiro_encontro(self):
        e = self.um("Hoje faz 1 ano do nosso primeiro encontro.")
        self.assertEqual((e["titulo"], e["data"], e["precisao"], e["data_expressao"]),
                         ("Primeiro encontro", "2025-09-30", "dia", "Hoje faz 1 ano"))

    def test_o_marco_fez_1_ano_hoje(self):
        e = self.um("Nosso primeiro encontro fez 1 ano hoje.")
        self.assertEqual((e["data"], e["precisao"]), ("2025-09-30", "dia"))

    def test_faz_n_anos_hoje_amanha_e_como_se_fosse_ontem(self):
        # Revisão de 07/10: "faz 2 anos hoje que" virava hoje; "amanhã faz 1 ano" virava só o ano; e
        # "como se fosse ontem" virava a data de ontem.
        e = self.um("Faz 2 anos hoje que pedi ela em namoro.")
        self.assertEqual((e["marco_tipo"], e["data"], e["precisao"]), ("namoro", "2024-09-30", "dia"))
        e = self.um("Amanhã faz 1 ano que a gente se conheceu.")
        self.assertEqual((e["data"], e["precisao"]), ("2025-10-01", "dia"))
        e = self.um("Lembro do nosso primeiro encontro como se fosse ontem.")
        self.assertEqual((e["data"], e["data_origem"]), (DATA, "registro"))

    def test_faz_um_ano_que_a_gente_se_conheceu_por_extenso(self):

        e = self.um("Hoje faz um ano que a gente se conheceu ❤️")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data"]), ("primeiro_encontro", "Vocês se conheceram", "2025-09-30"))

    def test_fazem_2_anos_vale_o_ano_e_nao_um_dia_que_ninguem_disse(self):
        e = self.um("fazem 2 anos que a gente se conheceu")
        self.assertEqual((e["data"], e["precisao"], e["resumo"]), ("2024-01-01", "ano", "Vocês se conheceram (2024)"))
        e = self.um("nos conhecemos há 2 anos na faculdade")
        self.assertEqual((e["data"], e["precisao"]), ("2024-01-01", "ano"))

    def test_faz_6_meses_que_comecamos_a_namorar_vale_o_mes(self):
        e = self.um("Faz 6 meses que começamos a namorar.")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data"], e["precisao"]),
                         ("namoro", "Começo do namoro", "2026-03-01", "mes"))

    def test_primeiro_natal_juntos_que_ja_passou(self):
        e = self.um("nosso primeiro natal juntos foi incrivel")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data"]), ("outro", "Primeiro natal juntos", "2025-12-25"))

    def test_dia_dos_namorados_que_passou(self):
        e = self.um("Pedi ela em namoro no dia dos namorados.")
        self.assertEqual(e["data"], "2026-06-12")

    def test_sem_data_usa_o_dia_do_registro_e_avisa(self):
        e = self.um("mano, nosso primeiro role foi no bar do zé kkk")
        self.assertEqual((e["marco_tipo"], e["data"], e["data_origem"], e["data_expressao"]),
                         ("primeiro_encontro", DATA, "registro", None))
        self.assertIn("usei o dia do registro", e["nota_data"])

    def test_girias_e_sem_acento(self):
        casos = {
            "pedi ela pra namorar ontem e ela disse sim": ("namoro", "2026-09-29"),
            "ontem a gente se conheceu numa festa na casa da Bia": ("primeiro_encontro", "2026-09-29"),
            "conheci a familia dela sabado passado, mt nervoso": ("familia", "2026-09-26"),
            "dei nosso primeiro beijo na roda gigante anteontem": ("outro", "2026-09-28"),
        }
        for texto, (tipo, data) in casos.items():
            with self.subTest(texto=texto):
                e = self.um(texto)
                self.assertEqual((e["marco_tipo"], e["data"]), (tipo, data))

    def test_nome_dela_e_apelido_contam_como_ela(self):
        self.assertEqual(self.um("pedi a Julia em namoro dia 12/03/2025")["data"], "2025-03-12")
        self.assertEqual(self.um("Conheci a Julia no Tinder em 2023.")["titulo"], "Vocês se conheceram")

    def test_outra_pessoa_nao_e_ela(self):
        self.nenhum("Pedi a Carla em namoro em 2010.")
        self.nenhum("Conheci a Mariana na academia ontem.")
        self.nenhum("conheci Paris em 2019, foi lindo")
        self.nenhum("conheci Paris em 2019, foi lindo", nome_dela=None)
        self.nenhum("conheci gente nova no curso")
        self.assertEqual(self.um("Conheci Julia numa festa em 2022.")["data"], "2022-01-01")

    def test_tempo_juntos_nao_vira_marco_a_guardar(self):
        self.nenhum("nosso primeiro mês de namoro foi ótimo")
        self.nenhum("Hoje é nosso primeiro aniversário de namoro!")

    def test_sem_o_nome_dela_configurado_o_marco_fica_para_conferir(self):
        e = self.um("Pedi a Carla em namoro em 2010.", nome_dela=None)
        self.assertEqual(e["forca"], "fraca")
        self.assertIn("Confira se é ela.", e["motivo"])

    def test_negacao(self):
        for texto in ("ainda nao conheci a familia dela", "Não foi nosso primeiro encontro, foi o segundo.",
                      "nunca dissemos eu te amo", "não pedi ela em namoro ainda", "n conheci os pais dela"):
            with self.subTest(texto=texto):
                self.nenhum(texto)

    def test_plano_e_futuro_nao_sao_marco(self):
        for texto in ("vou pedir ela em namoro sabado", "Amanhã é nosso primeiro encontro!",
                      "Nosso primeiro natal juntos vai ser na casa dela.", "Quero que nossa primeira viagem seja pra Paris.",
                      "semana que vem vou conhecer os pais dela", "sábado que vem é nosso primeiro encontro",
                      "vamos fazer nossa primeira viagem em dezembro", "se nosso primeiro encontro for bom, vou pedir de novo"):
            with self.subTest(texto=texto):
                self.nenhum(texto)

    def test_pergunta_duvida_e_ironia(self):
        for texto in ("quando foi nosso primeiro encontro?", "Será que ela lembra do nosso primeiro beijo",
                      "Não sei se nosso primeiro encontro foi em março ou abril.",
                      "nosso primeiro encontro foi perfeito sqn", "nao lembro quando foi nosso primeiro beijo"):
            with self.subTest(texto=texto):
                self.nenhum(texto)

    def test_ex_fica_de_fora(self):
        for texto in ("minha ex e eu nos conhecemos em 2015", "Pedi minha ex-namorada em namoro em 2018.",
                      "o primeiro encontro com a ex foi um desastre"):
            with self.subTest(texto=texto):
                self.nenhum(texto)

    def test_marco_de_outras_pessoas_fica_de_fora(self):
        for texto in ("o primeiro encontro da Mariana com o Pedro foi ontem", "A primeira viagem dela de avião foi aos 10 anos.",
                      "A irmã dela começou a namorar o Lucas.", "o primeiro beijo dela foi aos 15",
                      "A Bia e o Rafa se conheceram na faculdade."):
            with self.subTest(texto=texto):
                self.nenhum(texto)

    def test_acho_que_vira_proposta_fraca(self):
        e = self.um("acho que nosso primeiro beijo foi em março")
        self.assertEqual((e["forca"], e["titulo"], e["data"], e["precisao"]), ("fraca", "Primeiro beijo", "2026-03-01", "mes"))
        self.assertIn("confira", e["motivo"])

    def test_eu_te_amo_so_com_primeira_vez(self):
        self.nenhum("Ela disse que me ama!")
        self.nenhum("ela falou eu te amo antes de dormir")
        e = self.um("Ontem ela falou eu te amo pela primeira vez e eu chorei kkk")
        self.assertEqual((e["marco_tipo"], e["data"]), ("eu_te_amo", "2026-09-29"))
        self.assertEqual(self.um("Ela me disse eu te amo pela primeira vez hoje 🥹")["data"], DATA)

    def test_primeira_briga_nao_e_marco(self):
        self.nenhum("nossa primeira briga foi horrível")
        self.nenhum("foi a primeira vez que a gente brigou feio")

    def test_noivado_e_pedido_no_mesmo_texto_viram_um(self):
        achados = extras("Ficamos noivos!! Pedi ela em casamento na praia.")
        self.assertEqual([e["titulo"] for e in achados], ["Noivado"])

    def test_morar_juntos_e_adocao(self):
        e = self.um("Fomos morar juntos em março de 2025.")
        self.assertEqual((e["titulo"], e["data"], e["precisao"]), ("Morar juntos", "2025-03-01", "mes"))
        e = self.um("Adotamos um gatinho ontem!")
        self.assertEqual((e["titulo"], e["data"]), ("Adoção: um gatinho", "2026-09-29"))
        self.nenhum("adotamos uma estratégia nova no trabalho")

    def test_nosso_primeiro_generico_e_primeira_vez_que(self):
        e = self.um("Nosso primeiro show juntos foi o do Djavan em 2025.")
        self.assertEqual((e["marco_tipo"], e["titulo"], e["data"]), ("outro", "Primeiro show juntos", "2025-01-01"))
        e = self.um("Foi a primeira vez que a gente foi à praia juntos, ela amou.")
        self.assertEqual(e["titulo"], "Primeira vez que a gente foi à praia juntos")

    def test_quando_pedi_ela_em_namoro_e_passado(self):
        self.assertEqual(self.um("Ela se emocionou quando pedi ela em namoro.")["titulo"], "Pedido de namoro")
        self.assertEqual(self.um("Ela ficou super feliz quando conheci a família dela ontem.")["data"], "2026-09-29")

    def test_data_futura_de_outra_oracao_nao_e_a_do_marco(self):
        e = self.um("nosso primeiro encontro foi ótimo e vamos repetir sábado")
        self.assertEqual((e["data"], e["data_origem"]), (DATA, "registro"))

    def test_plano_em_outra_oracao_nao_apaga_o_marco(self):
        e = self.um("Vamos comemorar porque ontem pedi ela em namoro.")
        self.assertEqual(e["data"], "2026-09-29")
        e = self.um("Ela vai lembrar pra sempre do dia em que pedi ela em namoro.")
        self.assertEqual(e["titulo"], "Pedido de namoro")

    def test_ex_de_outra_pessoa_nao_e_a_ex(self):
        e = self.um("Conheci a mãe dela e a ex-chefe dela ontem.")
        self.assertEqual(e["titulo"], "Você conheceu a mãe dela")

    def test_conheci_ela_melhor_nao_e_o_comeco(self):
        self.nenhum("Nesta viagem conheci ela melhor.")

    def test_dois_marcos_na_mesma_frase(self):
        achados = extras("No nosso primeiro encontro demos o primeiro beijo, em 10/03/2024.")
        self.assertEqual([(e["titulo"], e["data"]) for e in achados],
                         [("Primeiro encontro", "2024-03-10"), ("Primeiro beijo", "2024-03-10")])

    def test_trecho_literal_e_datas_sempre_pelo_python(self):
        textos = ["Ontem foi nosso primeiro encontro. Ela adora tulipas.", "Pedi ela em namoro dia 20 e ela aceitou!",
                  "Hoje faz 1 ano do nosso primeiro encontro.", "Nossa primeira viagem juntos foi pra Paraty em julho."]
        for texto in textos:
            for e in extras(texto):
                with self.subTest(texto=texto):
                    self.assertIn(e["trecho"], texto)
                    date.fromisoformat(e["data"])
                    self.assertLessEqual(d(e["data"]), REF)                 # marco é o que já aconteceu


# ======================================================================== ajudantes

class TestAjudantes(unittest.TestCase):
    def test_quando_texto_com_a_precisao_dita(self):
        casos = [((d("2026-09-29"), "dia"), "29/09/2026"), ((d("2026-07-01"), "mes"), "julho de 2026"),
                 ((d("2024-01-01"), "ano"), "2024"), ((d("2026-09-21"), "semana"), "semana de 21/09/2026")]
        for (data, precisao), esperado in casos:
            with self.subTest(precisao=precisao):
                self.assertEqual(marcos.quando_texto(data, precisao), esperado)

    def test_marcos_de_tempo_em_dias_e_meses(self):
        lista = marcos.marcos_de_tempo(d("2024-01-31"), d("2027-12-31"))
        self.assertEqual([x["titulo"] for x in lista],
                         ["100 dias juntos", "6 meses juntos", "1 ano juntos", "500 dias juntos", "2 anos juntos",
                          "1.000 dias juntos", "3 anos juntos"])
        datas = {x["titulo"]: x["data"] for x in lista}
        self.assertEqual(datas["100 dias juntos"], d("2024-05-10"))
        self.assertEqual(datas["6 meses juntos"], d("2024-07-31"))
        self.assertEqual(datas["1 ano juntos"], d("2025-01-31"))
        self.assertEqual(datas["1.000 dias juntos"], d("2026-10-27"))

    def test_mes_curto_cai_no_ultimo_dia_como_o_nucleo(self):
        [seis] = [x for x in marcos.marcos_de_tempo(d("2025-08-31"), d("2026-03-31")) if x["chave"] == "m6"]
        self.assertEqual(seis["data"], d("2026-02-28"))

    def test_avaliar_pontos_explicaveis(self):
        itens = [{"tipo": "nota", "trecho": "Fomos ao show do Djavan e ela chorou de emoção.",
                  "categorias": ["Memórias compartilhadas", "Música", "Preocupações"], "certeza": "observado"},
                 {"tipo": "nota", "trecho": "Ela disse que foi o melhor dia do ano.", "categorias": [],
                  "certeza": "confirmado"}]
        texto = "Fomos ao show do Djavan e ela chorou de emoção. Ela disse que foi o melhor dia do ano."
        av = marcos.avaliar(texto, itens)
        self.assertEqual((av.tom, av.pontos, av.frase),
                         ("feliz", 32, "Fomos ao show do Djavan e ela chorou de emoção."))
        self.assertEqual(av.motivos, ["uma memória de vocês", "um momento feliz dela", "palavras dela 🟢"])

    def test_avaliar_tom_dificil_e_luto(self):
        triste = [{"tipo": "estado", "polaridade": -1, "trecho": "Ela está muito triste.", "categorias": ["Preocupações"]}]
        self.assertEqual(marcos.avaliar("Ela está muito triste.", triste).tom, "dificil")
        self.assertEqual(marcos.avaliar("A gente brigou feio ontem.", []).tom, "dificil")
        av = marcos.avaliar("A avó dela faleceu hoje. Ela está muito triste.", triste)
        self.assertEqual((av.tom, av.frase), ("luto", "A avó dela faleceu hoje."))
        self.assertEqual(marcos.avaliar("O pai dela morreu de rir com a piada.", []).tom, "neutro")
        self.assertEqual(marcos.avaliar("O cachorro do meu amigo morreu.", []).tom, "neutro")    # não é dela

    def test_alegria_negada_nao_conta(self):
        itens = [{"tipo": "nota", "trecho": "O jantar não foi especial.", "categorias": []}]
        self.assertEqual(marcos.avaliar("O jantar não foi especial.", itens).pontos, 0)


# ======================================================================== núcleo (gravação, rotas, Hoje)

class BaseNucleo(unittest.TestCase):
    def setUp(self):
        self.enterContext(recursos.usando(marcos))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-marcos-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.dia = [REF]
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: self.dia[0], relogio=Relogio(), atraso_pin=0)
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

    def registrar(self, texto: str, data: str = DATA, confirmar: bool = True, mudar=None, **mais) -> nucleo.RespostaApi:
        p = self.analisar(texto, data)
        escolhidos = [dict(e) for e in p["extras"].get("marcos", [])] if confirmar else []
        for e in escolhidos:
            e.update(mudar or {})
        corpo = {"texto": p["texto"], "data_registro": data, "itens": p["itens"], "pessoas": p["pessoas"],
                 "relacoes": p["relacoes"], "extras": {"marcos": escolhidos} if escolhidos else {}, **mais}
        return self.pedir("POST", "/api/registros", corpo)

    def rid(self, texto: str, data: str = DATA, **kw) -> int:
        r = self.registrar(texto, data, **kw)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def linhas(self) -> list[dict]:
        return self.banco.consultar("SELECT * FROM marcos ORDER BY id")

    def novo(self, titulo="Primeira viagem juntos", data="2024-09-30", tipo="viagem", **mais) -> int:
        r = self.pedir("POST", "/api/marcos", {"titulo": titulo, "data": data, "tipo": tipo, **mais})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def hoje(self, data: date | str = REF) -> list[dict]:
        r = self.pedir("GET", "/api/hoje", consulta={"data": str(data)})
        self.assertEqual(r.status, 200, r.dados)
        return [a for a in r.dados["alertas"] if a.get("recurso") == "marcos"]

    def cartoes(self, data: date | str = REF) -> list[dict]:
        """Todos os cartões que o recurso manda (antes do anti-excesso central)."""
        with recursos.usando(marcos):
            return marcos.cartoes_hoje(self.banco, d(str(data)))

    def estado(self, texto: str, quando: date, polaridade: int = -1) -> int:
        return self.banco.criar_registro(texto, quando, [
            Item(tipo=TipoItem.ESTADO, resumo="Estado", trecho=texto, certeza=Certeza.OBSERVADO,
                 categorias=["Preocupações"] if polaridade < 0 else ["Coisas que deixam feliz"],
                 data_evento=quando, polaridade=polaridade)], [], [])


class TestGravar(BaseNucleo):
    def test_confirmar_grava_marco_com_trecho_data_e_registro(self):
        r = self.registrar("Ontem foi nosso primeiro encontro, fomos no Outback.")
        self.assertEqual(r.status, 200, r.dados)
        [m] = self.linhas()
        self.assertEqual((m["titulo"], m["data"], m["precisao"], m["tipo"], m["registro_id"], m["data_expressao"],
                          m["data_origem"]),
                         ("Primeiro encontro", "2026-09-29", "dia", "primeiro_encontro", r.dados["id"], "Ontem", "texto"))
        self.assertEqual(m["trecho"], "Ontem foi nosso primeiro encontro, fomos no Outback.")
        self.assertFalse(self.pedir("GET", f"/api/registros/{r.dados['id']}").dados["privado"])   # não vira 🔒

    def test_sem_confirmar_nada_e_gravado(self):
        self.assertEqual(self.registrar("Ontem foi nosso primeiro encontro.", confirmar=False).status, 200)
        self.assertEqual(self.linhas(), [])

    def test_ajustes_feitos_na_revisao(self):
        self.registrar("Pedi ela em namoro dia 20 e ela aceitou!",
                       mudar={"titulo": "O pedido na roda gigante", "data": "2026-09-19", "precisao": "dia",
                              "data_expressao": None, "data_origem": "manual", "marco_tipo": "namoro",
                              "descricao": "Parque da cidade"})
        [m] = self.linhas()
        self.assertEqual((m["titulo"], m["data"], m["data_origem"], m["descricao"], m["data_expressao"]),
                         ("O pedido na roda gigante", "2026-09-19", "manual", "Parque da cidade", None))

    def test_data_depois_do_registro_ou_invalida_e_400_e_nada_gravado(self):
        for mudar, erro in (({"data": "2026-12-01"}, marcos.DATA_FUTURA), ({"data": "amanhã"}, marcos.DATA_INVALIDA),
                            ({"data": "1850-01-01"}, marcos.DATA_ANTIGA), ({"titulo": "x" * 121}, marcos.TITULO_INVALIDO)):
            with self.subTest(mudar=mudar):
                r = self.registrar("Ontem foi nosso primeiro encontro.", mudar=mudar)
                self.assertEqual((r.status, r.dados["erro"]), (400, erro))
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])
        self.assertEqual(self.linhas(), [])

    def test_nunca_inventar_expressao_e_pessoa_que_nao_estao_no_trecho(self):
        self.registrar("Conheci a mãe dela ontem.", mudar={"pessoa": "Carla, a melhor amiga", "data_expressao": "no Natal"})
        [m] = self.linhas()
        self.assertEqual((m["pessoa"], m["data_expressao"]), (None, None))

    def test_tipo_desconhecido_vira_outro(self):
        self.registrar("Ontem foi nosso primeiro encontro.", mudar={"marco_tipo": "aventura"})
        self.assertEqual(self.linhas()[0]["tipo"], "outro")

    def test_mesmo_marco_contado_de_novo_nao_duplica(self):
        self.registrar("Ontem foi nosso primeiro encontro.")
        self.registrar("Nosso primeiro encontro foi ontem, no Outback.")
        self.assertEqual(len(self.linhas()), 1)
        self.registrar("Hoje faz 1 ano do nosso primeiro encontro.")          # outra data: outro marco
        self.assertEqual(len(self.linhas()), 2)

    def test_marco_nao_deixa_o_registro_fora_da_ia(self):
        r = self.registrar("Ontem foi nosso primeiro encontro.")
        self.assertNotIn("privado", r.dados)
        self.assertNotIn(recursos.AVISO_PRIVADO, self.analisar("Ontem foi nosso primeiro encontro.")["avisos"])
        self.assertEqual(recursos.fora_da_ia(self.banco), set())


class TestRotas(BaseNucleo):
    def test_criar_listar_editar_e_tempo(self):
        mid = self.novo("Primeira viagem juntos", "2025-07-12", "viagem", descricao="Paraty")
        dados = self.pedir("GET", "/api/marcos").dados
        [m] = dados["marcos"]
        self.assertEqual((m["id"], m["titulo"], m["quando"], m["icone"], m["descricao"], m["data_origem"], m["registro_id"],
                          m["ha"]), (mid, "Primeira viagem juntos", "12/07/2025", "✈️", "Paraty", "manual", None, "há 1 ano"))
        self.assertEqual(m["aniversario"], {"data": "2027-07-12", "anos": 2, "dias": 285})
        r = self.pedir("PATCH", f"/api/marcos/{mid}", {"titulo": "Paraty!", "data": "2025-07-11", "tipo": "outro"})
        self.assertEqual((r.status, r.dados["marco"]["titulo"], r.dados["marco"]["data"], r.dados["marco"]["tipo"]),
                         (200, "Paraty!", "2025-07-11", "outro"))
        self.assertIsNone(dados["tempo"]["inicio"])

    def test_pedidos_que_nao_servem(self):
        mid = self.novo()
        casos = [
            ("POST", "/api/marcos", {"titulo": "", "data": "2025-01-01"}, 400),
            ("POST", "/api/marcos", {"titulo": "x" * 121, "data": "2025-01-01"}, 400),
            ("POST", "/api/marcos", {"titulo": "ok", "data": "2027-01-01"}, 400),
            ("POST", "/api/marcos", {"titulo": "ok"}, 400),
            ("POST", "/api/marcos", {"titulo": "ok", "data": "2025-01-01", "tipo": "festa"}, 400),
            ("POST", "/api/marcos", {"titulo": "ok", "data": "2025-01-01", "registro_id": 1}, 400),
            ("POST", "/api/marcos", {"titulo": "ok", "data": "2025-01-01", "precisao": "hora"}, 400),
            ("PATCH", f"/api/marcos/{mid}", {}, 400),
            ("PATCH", f"/api/marcos/{mid}", {"trecho": "inventado"}, 400),
            ("PATCH", "/api/marcos/999", {"titulo": "x"}, 404),
            ("DELETE", "/api/marcos/999", None, 404),
            ("POST", "/api/marcos/parecidos", {}, 400),
        ]
        for metodo, caminho, corpo, status in casos:
            with self.subTest(caminho=caminho, corpo=corpo):
                self.assertEqual(self.pedir(metodo, caminho, corpo).status, status)
        self.assertEqual(self.pedir("GET", "/api/marcos/neste-dia", consulta={"data": "ontem"}).status, 400)
        self.assertEqual(self.pedir("GET", "/api/marcos", token=None).status, 401)
        self.assertEqual(len(self.linhas()), 1)

    def test_lotacao(self):
        with mock.patch.object(marcos, "MAX_MARCOS", 1):
            self.novo()
            r = self.pedir("POST", "/api/marcos", {"titulo": "Outro", "data": "2025-01-01"})
            self.assertEqual((r.status, r.dados["erro"]), (400, marcos.DEMAIS))

    def test_parecidos_avisa_antes_de_guardar_outro_igual(self):
        mid = self.novo("Primeiro encontro", "2025-10-06", "primeiro_encontro")
        dados = self.pedir("POST", "/api/marcos/parecidos",
                           {"tipo": "primeiro_encontro", "titulo": "Primeiro encontro", "data": "2025-10-06"}).dados
        self.assertEqual([(x["id"], x["igual"]) for x in dados], [(mid, True)])
        dados = self.pedir("POST", "/api/marcos/parecidos", {"tipo": "primeiro_encontro", "titulo": "Vocês se conheceram"}).dados
        self.assertEqual([(x["id"], x["igual"]) for x in dados], [(mid, False)])        # o mesmo tipo: avisa
        self.assertEqual(self.pedir("POST", "/api/marcos/parecidos", {"tipo": "outro", "titulo": "Adoção: um gato"}).dados, [])

    def test_apagar_some_do_banco_das_copias_e_dos_avisos(self):
        mid = self.novo("Primeira viagem juntos", "2024-09-30", "viagem")
        [c] = self.hoje()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/marcos/{mid}").status, 200)
        self.assertEqual(self.linhas(), [])
        self.assertFalse(any(k.startswith("marcos:") for k in self.banco.alertas_dispensados()))
        self.assertEqual(len(repetir), 1)
        repetir[0](copia)                                         # o que o cofre faria em cada cópia
        self.assertEqual(copia.consultar("SELECT * FROM marcos"), [])
        self.assertFalse(any(k.startswith("marcos:") for k in copia.alertas_dispensados()))
        historico = json.dumps(self.pedir("GET", "/api/historico").dados, ensure_ascii=False)
        self.assertIn(f"marcos #{mid} apagado", historico)
        self.assertNotIn("viagem", historico)                     # histórico sem conteúdo

    def test_editar_a_data_esquece_os_avisos_dispensados(self):
        mid = self.novo("Primeira viagem juntos", "2024-09-30", "viagem")
        [c] = self.hoje()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        self.assertEqual(self.hoje(), [])
        self.pedir("PATCH", f"/api/marcos/{mid}", {"data": "2023-09-30"})
        self.assertEqual(len(self.hoje()), 1)


class TestEsquema(BaseNucleo):
    def test_tabela_tem_as_colunas_do_contrato(self):
        colunas = {l[1] for l in self.banco._con.execute("PRAGMA table_info(marcos)")}
        self.assertTrue({"id", "titulo", "data", "tipo", "descricao", "registro_id", "criado_em"} <= colunas)
        versao = self.banco._con.execute("SELECT versao FROM recursos_esquema WHERE nome = 'marcos'").fetchone()[0]
        self.assertEqual(versao, marcos.ESQUEMA_VERSAO)

    def test_tipo_fora_da_lista_e_recusado_pelo_proprio_banco(self):
        with self.assertRaises(Exception):
            self.banco.inserir("marcos", {"titulo": "x", "data": "2025-01-01", "tipo": "festa", "criado_em": "2025-01-01"})


class TestTempoJuntos(BaseNucleo):
    def test_inicio_no_futuro_nao_conta_nada(self):
        self.banco.salvar_config(inicio_namoro="2026-12-01")
        t = self.pedir("GET", "/api/marcos").dados["tempo"]
        self.assertEqual((t["base"], t["dias"], t["proximos"], t["tempo_juntos"]), ("ajustes", None, [], None))
        self.assertEqual(self.hoje(), [])

    def test_tempo_juntos_pelos_ajustes(self):
        self.banco.salvar_config(inicio_namoro="2025-03-30")
        t = self.pedir("GET", "/api/marcos").dados["tempo"]
        self.assertEqual((t["inicio"], t["base"], t["tempo_juntos"], t["dias"]), ("2025-03-30", "ajustes", "1 ano e 6 meses", 549))
        self.assertEqual([p["titulo"] for p in t["proximos"]], ["2 anos juntos", "1.000 dias juntos", "3 anos juntos"])
        self.assertEqual(t["ultimo"]["titulo"], "500 dias juntos")
        self.assertIn("informado em Ajustes", t["texto"])

    def test_sem_ajustes_vale_o_marco_de_namoro_e_a_tela_pode_guardar(self):
        rid = self.rid("Pedi ela em namoro hoje! Ela aceitou.", "2026-03-30")
        t = self.pedir("GET", "/api/marcos").dados["tempo"]
        self.assertEqual((t["inicio"], t["base"], t["marco"]["registro_id"], t["tempo_juntos"]),
                         ("2026-03-30", "marco", rid, "6 meses"))
        self.assertIn("Dá para guardar essa data em Ajustes", t["texto"])

    def test_ajustes_diferente_do_marco_avisa(self):
        self.rid("Pedi ela em namoro hoje! Ela aceitou.", "2026-03-30")
        self.banco.salvar_config(inicio_namoro="2026-03-28")
        t = self.pedir("GET", "/api/marcos").dados["tempo"]
        self.assertEqual((t["base"], t["diferente"]["data"], t["diferente"]["titulo"]),
                         ("ajustes", "2026-03-30", "Pedido de namoro"))


class TestHoje(BaseNucleo):
    def test_100_dias_lembrete_7_dias_antes_e_no_dia(self):
        self.banco.salvar_config(inicio_namoro="2026-06-22")
        [c] = self.hoje()
        self.assertEqual((c["nivel"], c["tipo"], c["texto"], c["relevancia"], c["chave"]),
                         ("lembrete", "marco_tempo", "Hoje vocês completam 100 dias juntos 💙", 85,
                          "marcos:tempo:d100:2026-09-30:d0"))
        self.assertIn("Vocês estão juntos desde 22/06/2026 (informado em Ajustes).", c["porque"])
        [c] = self.hoje("2026-09-23")
        self.assertEqual((c["texto"], c["relevancia"]), ("Daqui a 7 dias vocês completam 100 dias juntos 💙", 78))
        self.assertEqual(self.hoje("2026-09-24"), [])

    def test_no_dia_lembra_os_momentos_guardados(self):
        self.banco.salvar_config(inicio_namoro="2026-06-22")
        rid = self.rid("Ontem foi nosso primeiro encontro.", "2026-06-15")
        [c] = self.hoje()
        self.assertEqual([m["titulo"] for m in c["dados"]["momentos"]], ["Primeiro encontro"])
        self.assertIn("Alguns momentos de vocês que você guardou: Primeiro encontro (14/06/2026).", c["porque"])
        self.assertIn(rid, c["registro_ids"])

    def test_6_meses_pelos_ajustes_nao_duplica_o_aviso_do_nucleo(self):
        self.banco.salvar_config(inicio_namoro="2026-03-30")
        todos = self.pedir("GET", "/api/hoje", consulta={"data": DATA}).dados["alertas"]
        self.assertTrue(any(a["texto"] == "Hoje vocês completam 6 meses juntos." for a in todos))   # o do núcleo
        self.assertEqual(self.hoje(), [])                             # sem marcos guardados: nada genérico
        self.novo("Primeiro encontro", "2026-03-01", "primeiro_encontro")
        [c] = self.hoje()
        self.assertEqual((c["nivel"], c["tipo"]), ("sugestao", "marco_relembrar"))
        self.assertEqual(c["texto"], "Hoje vocês completam 6 meses juntos. Talvez seja bonito relembrar com ela alguns "
                                     "momentos de vocês 💙")
        self.assertIn("Você guardou: Primeiro encontro (01/03/2026).", c["porque"])

    def test_sem_ajustes_os_6_meses_saem_do_marco_de_namoro(self):
        rid = self.rid("Pedi ela em namoro hoje! Ela aceitou.", "2026-03-30")
        cartoes = self.hoje()
        [c] = [x for x in cartoes if x["tipo"] == "marco_tempo"]
        self.assertEqual((c["nivel"], c["texto"]), ("lembrete", "Hoje vocês completam 6 meses juntos 💙"))
        self.assertIn("“Pedi ela em namoro hoje!”", c["porque"])
        self.assertIn(rid, c["registro_ids"])

    def test_aniversario_do_primeiro_encontro_7_dias_antes_e_no_dia(self):
        rid = self.rid("Ontem foi nosso primeiro encontro, fomos no Outback.", "2025-10-07")
        [c] = self.hoje("2026-09-29")
        self.assertEqual((c["nivel"], c["tipo"], c["texto"], c["relevancia"]),
                         ("lembrete", "marco_aniversario", "Daqui a 7 dias faz 1 ano: “Primeiro encontro”.", 74))
        [c] = self.hoje("2026-10-06")
        self.assertEqual((c["texto"], c["registro_ids"]), ("Hoje faz 1 ano: “Primeiro encontro” 💙", [rid]))
        self.assertIn("Você registrou em 07/10/2025: “Ontem foi nosso primeiro encontro, fomos no Outback.”", c["porque"])
        self.assertIn("Talvez seja gostoso relembrar com ela.", c["porque"])

    def test_neste_dia_um_registro_feliz_de_um_ano_atras(self):
        rid = self.rid("Fomos ao show do Djavan e ela chorou de emoção. Ela disse que foi o melhor dia do ano.",
                       "2025-09-30", confirmar=False)
        [c] = self.hoje()
        self.assertEqual((c["nivel"], c["tipo"], c["registro_ids"], c["chave"]),
                         ("sugestao", "neste_dia", [rid], f"marcos:r{rid}:neste:{DATA}"))
        self.assertEqual(c["texto"], "Neste dia, há 1 ano, você registrou: “Fomos ao show do Djavan e ela chorou de emoção.” "
                                     "Talvez seja gostoso relembrar com ela.")
        self.assertIn("Volta porque é uma memória de vocês e um momento feliz dela e palavras dela 🟢.", c["porque"])

    def test_neste_dia_um_marco_de_dois_anos_atras(self):
        self.novo("Primeira viagem juntos", "2024-09-30", "viagem", descricao="Paraty")
        [c] = self.hoje()
        self.assertEqual((c["tipo"], c["relevancia"], c["texto"]),
                         ("neste_dia", 66, "Neste dia, há 2 anos: “Primeira viagem juntos” (Paraty) 💙 "
                                           "Talvez seja gostoso relembrar com ela."))
        self.assertIn("Você guardou este marco", c["porque"])

    def test_ha_6_meses_so_o_que_foi_forte_e_feliz(self):
        rid = self.rid("Passamos o dia na praia, foi maravilhoso.", "2026-03-30", confirmar=False)
        self.rid("Ela comeu pizza.", "2026-03-30", confirmar=False)
        [c] = self.hoje()
        self.assertEqual((c["texto"], c["chave"]), ("Há 6 meses, você registrou: “Passamos o dia na praia, foi maravilhoso.” "
                                                    "Talvez seja gostoso relembrar com ela.", f"marcos:r{rid}:seis:{DATA}"))

    def test_marco_de_6_meses_atras(self):
        self.rid("Nossa primeira viagem juntos foi pra Paraty, ela amou a praia.", "2026-03-30")
        [c] = self.hoje()
        self.assertEqual(c["texto"], "Há 6 meses: “Primeira viagem juntos” (Paraty) 💙 Talvez seja gostoso relembrar com ela.")

    def test_neste_dia_nao_traz_briga_nem_momento_dificil_nem_o_que_e_pouco(self):
        # Um dia com memória boa e briga no fim: a briga não volta sozinha, nem com o resto.
        self.rid("Fomos ao cinema e foi lindo, mas depois a gente brigou feio por ciúme.", "2025-09-30", confirmar=False)
        self.estado("Ela estava muito triste com o trabalho.", d("2024-09-30"))
        self.rid("Ela comeu pizza.", "2023-09-30", confirmar=False)
        self.assertEqual(self.hoje(), [])
        self.assertEqual(self.pedir("GET", "/api/marcos/neste-dia").dados["lembrancas"], [])

    def test_aniversario_de_uma_perda_dela_vira_cuidado_na_vespera_e_no_dia(self):
        rid = self.rid("A avó dela faleceu hoje. Ela está arrasada.", "2025-10-01", confirmar=False)
        apoio = self.banco.criar_registro("Quando está triste, ela prefere que eu só fique perto.", d("2026-05-01"), [
            Item(tipo=TipoItem.APOIO, resumo="Que eu fique perto", trecho="Quando está triste, ela prefere que eu só fique perto.",
                 certeza=Certeza.CONFIRMADO, alvo="que eu fique perto", polaridade=1)], [], [])
        [c] = self.hoje()
        self.assertEqual((c["tipo"], c["relevancia"], c["chave"]), ("neste_dia_luto", 64, f"marcos:r{rid}:luto:2026-10-01:d1"))
        self.assertEqual(c["texto"], "Amanhã faz 1 ano: “A avó dela faleceu hoje.” Talvez seja um dia mais sensível para ela, "
                                     "e estar por perto ajude.")
        self.assertIn("Ela já disse o que ajuda: “Quando está triste, ela prefere que eu só fique perto.”", c["porque"])
        self.assertEqual(c["registro_ids"], [rid, apoio])
        [c] = self.hoje("2026-10-01")
        self.assertEqual((c["relevancia"], c["texto"][:52]), (70, "Neste dia, há 1 ano, você registrou: “A avó dela fal"))

    def test_momento_dificil_dela_tira_o_convite(self):
        self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
        antes = self.hoje()[0]["relevancia"]
        triste = self.estado("Ela está muito cansada e estressada com o trabalho.", d("2026-09-29"))
        [c] = self.hoje()
        self.assertEqual(c["relevancia"], antes - 15)
        self.assertNotIn("Talvez seja gostoso", c["texto"])
        self.assertIn("Por isso fica só como lembrança, sem convite.", c["porque"])
        self.assertIn(triste, c["registro_ids"])
        self.estado("Ela está super feliz hoje.", REF, polaridade=1)          # o mais recente é bom: volta o convite
        self.assertIn("Talvez seja gostoso", self.hoje()[0]["texto"])

    def test_29_de_fevereiro_volta_em_28_de_fevereiro(self):
        self.rid("Fomos ao parque e ela amou o piquenique, foi um dia lindo.", "2024-02-29", confirmar=False)
        [c] = self.hoje("2025-02-28")
        self.assertIn("Neste dia, há 1 ano", c["texto"])
        self.assertEqual(self.hoje("2025-03-01"), [])

    def test_so_uma_sugestao_de_marcos_no_hoje(self):
        self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
        self.novo("Primeira viagem juntos", "2024-09-30", "viagem")
        self.rid("Passamos o dia na praia, foi maravilhoso.", "2026-03-30", confirmar=False)
        self.assertEqual(len([c for c in self.cartoes() if c["nivel"] == "sugestao"]), 3)
        self.assertEqual(len(self.hoje()), 1)
        self.assertEqual(self.hoje()[0]["relevancia"], 66)            # o marco de vocês primeiro

    def test_dispensar_nao_volta_no_dia_mas_volta_no_ano_seguinte(self):
        self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
        [c] = self.hoje()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        self.assertEqual(self.hoje(), [])
        [c] = self.hoje("2027-09-30")
        self.assertIn("há 2 anos", c["texto"])

    def test_neste_dia_na_tela(self):
        self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
        self.novo("Primeira viagem juntos", "2024-09-30", "viagem")
        dados = self.pedir("GET", "/api/marcos/neste-dia").dados
        self.assertEqual([l["fonte"] for l in dados["lembrancas"]], ["marco", "registro"])
        self.assertTrue(all(l["texto"] for l in dados["lembrancas"]))
        outro = self.pedir("GET", "/api/marcos/neste-dia", consulta={"data": "2026-10-01"}).dados
        self.assertEqual((outro["data"], outro["lembrancas"]), ("2026-10-01", []))


class TestLinhaDoTempo(BaseNucleo):
    def montar(self):
        self.banco.salvar_config(inicio_namoro="2025-03-30")
        self.rid("Ontem foi nosso primeiro encontro, fomos no Outback.", "2025-03-01")
        self.rid("Fomos ao show do Djavan e ela chorou de emoção. Ela disse que foi o melhor dia do ano.", "2025-09-30",
                 confirmar=False)
        self.rid("A gente brigou feio por causa de ciúme.", "2026-01-10", confirmar=False)
        self.rid("A Mariana vai fazer uma cirurgia em agosto e está preocupada.", "2026-06-01", confirmar=False)

    def test_por_ano_com_marcos_tempo_e_registros(self):
        self.montar()
        dados = self.pedir("GET", "/api/marcos/linha").dados
        self.assertEqual([a["ano"] for a in dados["anos"]], [2026, 2025])
        tipos = [(p["tipo"], p["titulo"]) for a in dados["anos"] for p in a["pontos"]]
        self.assertIn(("marco", "Primeiro encontro"), tipos)
        self.assertIn(("inicio", "Começo do namoro"), tipos)
        self.assertIn(("tempo", "1 ano juntos"), tipos)
        self.assertIn(("tempo", "500 dias juntos"), tipos)
        self.assertIn(("registro", "Fomos ao show do Djavan e ela chorou de emoção."), tipos)
        self.assertNotIn("brigou", json.dumps(dados, ensure_ascii=False))          # o essencial
        datas = [p["data"] for a in dados["anos"] for p in a["pontos"]]
        self.assertEqual(datas, sorted(datas, reverse=True))
        [show] = [p for a in dados["anos"] for p in a["pontos"] if p["tipo"] == "registro"]
        self.assertEqual(show["motivos"], ["uma memória de vocês", "um momento feliz dela", "palavras dela 🟢"])

    def test_tudo_mostra_os_momentos_dificeis_e_os_de_outras_pessoas(self):
        self.montar()
        dados = self.pedir("GET", "/api/marcos/linha", consulta={"tudo": "1"}).dados
        self.assertTrue(dados["tudo"])
        texto = json.dumps(dados, ensure_ascii=False)
        self.assertIn("brigou", texto)
        self.assertIn("cirurgia", texto)
        [briga] = [p for a in dados["anos"] for p in a["pontos"] if "brigou" in (p["trecho"] or "")]
        self.assertEqual(briga["tom"], "dificil")

    def test_pessoas_e_registro_em_cada_ponto(self):
        rid = self.rid("Conheci a mãe dela, a Sandra, no domingo passado.", "2026-09-30")
        dados = self.pedir("GET", "/api/marcos/linha").dados
        [p] = [p for a in dados["anos"] for p in a["pontos"] if p["tipo"] == "marco"]
        self.assertEqual((p["registro_id"], p["marco_tipo"]), (rid, "familia"))
        self.assertTrue(p["pessoas"])

    def test_vazio(self):
        dados = self.pedir("GET", "/api/marcos/linha").dados
        self.assertEqual((dados["anos"], dados["tempo"]["inicio"]), ([], None))


class TestExportarImportarApagar(BaseNucleo):
    def test_exportar_e_juntar_noutro_diario_sem_duplicar(self):
        rid = self.rid("Ontem foi nosso primeiro encontro, fomos no Outback.", "2025-10-07")
        self.novo("Primeira viagem juntos", "2025-07-12", "viagem", descricao="Paraty")
        exportado = self.pedir("GET", "/api/exportar").dados
        lista = exportado["recursos"]["marcos"]["marcos"]
        self.assertEqual([(m["titulo"], m["registro_id"], m["data"]) for m in lista],
                         [("Primeiro encontro", rid, "2025-10-06"), ("Primeira viagem juntos", None, "2025-07-12")])
        texto = json.dumps(exportado, ensure_ascii=False)
        with recursos.usando(marcos):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            previa = destino.importar_json(texto, previa=True)
            self.assertEqual(previa["recursos"]["marcos"]["novos"], 2)
            self.assertEqual(destino.consultar("SELECT * FROM marcos"), [])
            r = destino.importar_json(texto)
            self.assertEqual(r["recursos"]["marcos"], {"novos": 2, "texto": "2 marcos novos."})
            novo = [x["id"] for x in destino.registros() if "Outback" in x["texto"]][0]
            self.assertEqual(destino.consultar("SELECT titulo, registro_id, descricao, data_origem FROM marcos ORDER BY id"),
                             [{"titulo": "Primeiro encontro", "registro_id": novo, "descricao": None, "data_origem": "texto"},
                              {"titulo": "Primeira viagem juntos", "registro_id": None, "descricao": "Paraty",
                               "data_origem": "manual"}])
            self.assertEqual(destino.importar_json(texto)["recursos"]["marcos"]["novos"], 0)

    def test_importar_tolerante_e_nunca_inventa(self):
        rid = self.rid("Ontem foi nosso primeiro encontro.", "2025-10-07")
        exportado = self.pedir("GET", "/api/exportar").dados
        lista = exportado["recursos"]["marcos"]["marcos"]
        lista.append({"titulo": "Viagem a Paris", "data": "2025-01-01", "registro_id": rid, "trecho": "Fomos a Paris."})
        lista.append({"titulo": "", "data": "2025-01-01"})
        lista.append("nem é objeto")
        lista.append({"titulo": "Sem registro", "data": "2025-01-01", "registro_id": 777})
        lista.append({"titulo": "Data ruim", "data": "ontem"})
        lista.append({"titulo": "À mão", "data": "2024-05-01", "tipo": "inventado", "precisao": "hora"})
        with recursos.usando(marcos):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            resumo = destino.importar_json(json.dumps(exportado, ensure_ascii=False))["recursos"]["marcos"]
            self.assertEqual(resumo["novos"], 2)
            self.assertIn("5 ficaram de fora", resumo["texto"])
            self.assertEqual(destino.consultar("SELECT titulo, tipo, precisao FROM marcos ORDER BY id"),
                             [{"titulo": "Primeiro encontro", "tipo": "primeiro_encontro", "precisao": "dia"},
                              {"titulo": "À mão", "tipo": "outro", "precisao": "dia"}])
            outro = Banco.novo()
            self.addCleanup(outro.fechar)
            ruim = {**exportado, "recursos": {"marcos": {"marcos": "não é lista"}}}
            r = outro.importar_json(json.dumps(ruim, ensure_ascii=False))
            self.assertIn(recursos.AVISO_IMPORTAR.format(rotulo="Marcos"), r["avisos"])

    def test_apagar_o_registro_apaga_o_marco_e_as_chaves(self):
        rid = self.rid("Nossa primeira viagem juntos foi pra Paraty, ela amou a praia.", "2024-09-30")
        lembranca = self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
        for c in self.cartoes():
            self.pedir("POST", "/api/alertas/dispensar", {"chave": f"marcos:{c['chave']}"})
        self.assertTrue(any(k.startswith(f"marcos:r{lembranca}:") for k in self.banco.alertas_dispensados()))
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{lembranca}").status, 200)
        self.assertEqual(self.linhas(), [])
        self.assertFalse(any(k.startswith("marcos:") for k in self.banco.alertas_dispensados()))
        exportado = json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False)
        self.assertNotIn("Paraty", exportado)                    # apagar é apagar: nem no export
        self.assertEqual(self.pedir("GET", "/api/marcos/linha").dados["anos"], [])


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

    def test_marco_guardado_a_mao_nunca_vai_para_a_ia(self):
        self.banco.salvar_config(ia_principal=IA)
        cadeia = self.CadeiaFalsa()
        self.enterContext(mock.patch.object(motor, "obter_cadeia_ia", lambda ajustes: cadeia))
        self.rid("Ela adora tulipas amarelas.", confirmar=False)
        self.novo("Aurora boreal em Tromsø", "2025-01-10", "viagem", descricao="o céu verde")
        self.assertEqual(self.pedir("POST", "/api/ia/sugestao", {}).status, 200)
        self.assertEqual(self.pedir("POST", "/api/ia/busca", {"q": "aurora boreal tulipas"}).status, 200)
        self.assertTrue(cadeia.recebido)
        self.assertFalse(any("aurora" in r.lower() or "céu verde" in r for r in cadeia.recebido))

    def test_o_que_um_recurso_deixa_fora_da_ia_nunca_volta_no_hoje(self):
        rid = self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
        self.assertEqual(len(self.hoje()), 1)
        falso = types.ModuleType("testes.recurso_ciclo_falso")
        falso.NOME = "ciclofalso"
        falso.fora_da_ia = lambda banco: {rid}
        with recursos.usando(marcos, falso):
            self.assertEqual(self.hoje(), [])
            self.assertEqual(self.pedir("GET", "/api/marcos/neste-dia").dados["lembrancas"], [])
            self.assertEqual(self.pedir("GET", "/api/marcos/linha").dados["anos"], [])

    def test_recurso_que_nao_abriu_esconde_os_registros_privados(self):
        rid = self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False, privado=True)
        self.assertEqual([c["registro_ids"] for c in self.hoje()], [[rid]])     # 🔒 é só sobre a IA
        quebrado = types.ModuleType("testes.recurso_quebrado")
        quebrado.NOME = "Com Espaço"                                          # fora do contrato: não abre
        with recursos.usando(marcos, quebrado):
            self.assertTrue(recursos.falhas())
            self.assertEqual(self.hoje(), [])                                  # sem saber o que é do ciclo: nada

    def test_sem_lembrancas_o_hoje_nem_pergunta_o_que_fica_fora_da_ia(self):
        chamadas = []
        falso = types.ModuleType("testes.recurso_contador")
        falso.NOME = "contador"
        falso.fora_da_ia = lambda banco: chamadas.append(1) or set()
        self.rid("Ela comeu pizza.", "2026-09-01", confirmar=False)
        with recursos.usando(marcos, falso):
            self.hoje()
            self.assertEqual(chamadas, [])                     # nada a lembrar: o gancho dos outros nem roda
            self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
            chamadas.clear()
            self.assertEqual(len(self.hoje()), 1)
            self.assertEqual(len(chamadas), 1)                 # e roda uma vez só por Hoje

    def test_palavras_do_ciclo_nunca_aparecem_no_hoje(self):
        self.rid("Ela estava com cólica, mas fomos ao cinema e ela amou o filme.", "2025-09-30", confirmar=False)
        self.rid("Ela ficou menstruada e mesmo assim fomos à praia, foi lindo.", "2024-09-30", confirmar=False)
        self.assertEqual(self.hoje(), [])
        texto = json.dumps(self.pedir("GET", "/api/marcos/linha", consulta={"tudo": "1"}).dados, ensure_ascii=False)
        for palavra in ("cólica", "menstru"):
            self.assertNotIn(palavra, texto)

    def test_marco_de_registro_sensivel_fica_mas_o_trecho_nao_aparece(self):
        rid = self.rid("Ontem foi nosso primeiro encontro e ela estava com cólica, coitada.", "2025-10-07")
        self.assertEqual(len(self.linhas()), 1)
        [c] = self.hoje("2026-10-06")
        self.assertEqual(c["texto"], "Hoje faz 1 ano: “Primeiro encontro” 💙")
        self.assertNotIn("cólica", json.dumps(c, ensure_ascii=False))
        self.assertIn("Você guardou este marco num registro em 07/10/2025", c["porque"])
        # Sem palavra do ciclo, mas num registro que um recurso deixa fora da IA: também não cita.
        outro = self.rid("Nossa primeira viagem juntos foi pra Paraty.", "2024-09-30")
        falso = types.ModuleType("testes.recurso_eu_falso")
        falso.NOME = "eufalso"
        falso.fora_da_ia = lambda banco: {outro}
        with recursos.usando(marcos, falso):
            [c] = self.hoje()
            self.assertNotIn("primeira viagem juntos foi", c["porque"])
            linha = self.pedir("GET", "/api/marcos/linha").dados
            pontos = [p for a in linha["anos"] for p in a["pontos"] if p["tipo"] == "marco"]
            self.assertEqual(len(pontos), 2)
            self.assertTrue(all(p["trecho"] is None for p in pontos))
        self.assertIn(rid, [m["registro_id"] for m in self.linhas()])

    def test_estado_do_ciclo_nao_explica_o_humor_dela(self):
        self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
        self.estado("Ela está com muita cólica e irritada.", d("2026-09-29"))
        [c] = self.hoje()
        self.assertIn("Talvez seja gostoso relembrar com ela.", c["texto"])        # não conta como momento difícil
        self.assertNotIn("cólica", json.dumps(c, ensure_ascii=False))

    def test_textos_do_hoje_sao_possibilidade_e_citam_a_origem(self):
        self.banco.salvar_config(inicio_namoro="2026-06-22")
        self.rid("Fomos ao show do Djavan e ela chorou de emoção.", "2025-09-30", confirmar=False)
        self.rid("A avó dela faleceu hoje.", "2025-10-01", confirmar=False)
        for c in self.cartoes():
            with self.subTest(chave=c["chave"]):
                if c["nivel"] == "sugestao":
                    self.assertIn("Talvez", c["texto"])
                self.assertTrue(c["porque"])
                for proibido in ("você deve", "precisa fazer", "tem que", "menstrua", "ciclo", "TPM"):
                    self.assertNotIn(proibido, c["texto"])

    def test_log_sem_conteudo_quando_algo_falha(self):
        self.novo("Aurora boreal em Tromsø", "2024-09-30", "viagem")
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        with mock.patch.object(marcos, "lembrancas", side_effect=RuntimeError("Aurora boreal em Tromsø")):
            self.assertEqual(self.pedir("GET", "/api/hoje").status, 200)       # o Hoje abre sem os cartões daqui
            self.assertEqual(self.pedir("GET", "/api/marcos/neste-dia").status, 500)
        texto_log = "\n".join(coletor.mensagens)
        self.assertIn("marcos", texto_log)
        self.assertIn("RuntimeError", texto_log)
        self.assertNotIn("Aurora", texto_log)

    def test_trecho_de_cada_marco_esta_no_registro(self):
        for texto in ("Ontem foi nosso primeiro encontro. Ela adora tulipas.", "mano, pedi ela pra namorar ontem kkk",
                      "Conheci a mãe dela no domingo passado, foi tranquilo."):
            self.rid(texto)
        self.assertEqual(len(self.linhas()), 3)
        for m in self.linhas():
            registro = self.pedir("GET", f"/api/registros/{m['registro_id']}").dados
            self.assertIn(m["trecho"], registro["texto"])


class TestTela(unittest.TestCase):
    def test_tela_sem_html_montado_nem_estilo_inline(self):
        js = (RAIZ / "web" / "telas" / "marcos.js").read_text(encoding="utf-8")
        for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".style.",
                         "style=", "localStorage", "sessionStorage", "fetch("):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, js)
        for registro in ("registrarMenu('tempo'", "registrarTela('tempo'", "registrarRevisaoExtra('marco'"):
            with self.subTest(registro=registro):
                self.assertIn(registro, js)
        self.assertIn("window.EntreNos", js)

    def test_tipos_dos_cartoes_e_dos_extras_batem_com_a_tela(self):
        js = (RAIZ / "web" / "telas" / "marcos.js").read_text(encoding="utf-8")
        for tipo in marcos.TIPOS_EXTRAS:
            self.assertIn(f"registrarRevisaoExtra('{tipo}'", js)
        fonte = (RAIZ / "entrenos" / "recursos" / "marcos.py").read_text(encoding="utf-8")
        tipos = set(re.findall(r'tipo="([a-z_]+)"', fonte)) | set(re.findall(r'tipo = "(neste_dia[a-z_]*)"', fonte))
        self.assertTrue({"marco_tempo", "marco_relembrar", "marco_aniversario", "neste_dia", "neste_dia_luto"} <= tipos)
        for tipo in tipos:
            self.assertIn(f"registrarCartaoHoje('{tipo}'", js)
        for tipo in marcos.TIPOS:
            self.assertIn(f"'{tipo}'", js)


if __name__ == "__main__":
    unittest.main()
