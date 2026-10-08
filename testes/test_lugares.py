"""Lugares (DESIGN.md, seção 20.5).

Só este recurso entra na lista (`recursos.usando(lugares)`): os outros estão sendo escritos ao mesmo
tempo. Cobre a extração por regras (frases de conversa, sem acento, datas relativas, negação,
ironia, pergunta, fala de outra pessoa e o que NÃO é lugar), a gravação conferida (a certeza nunca
sobe, só fica a reação escrita), a aba (o que ela quer conhecer em ordem explicável, os lugares de
vocês, as pistas e o que evitar), as ações (corrigir, fomos, apagar, apagar uma menção e as cópias),
a sugestão da semana no Hoje (uma por semana, sensível ao momento dela, ocasiões e viagens),
exportar/importar/apagar registro, os limites de privacidade e o "nunca inventar".

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026 (quarta, semana 2026-W40), scrypt pequeno.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest import mock

from entrenos import cofre, motor, nucleo, recursos
from entrenos.banco import Banco
from entrenos.modelos import Resposta, para_json
from entrenos.motor import Conhecimento
from entrenos.recursos import lugares
from testes.test_servidor import PIN, REF, Relogio

DATA = REF.isoformat()
SEMANA = "2026-W40"
# O cartão do Hoje só aparece na quinta e na sexta (ou com uma data de vocês em até 3 dias, 20.5.1):
# os testes do Hoje abrem numa quinta da mesma semana do REF (quarta, 30/09).
QUINTA = date(2026, 10, 1)
IA = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": "modelo-de-teste", "tipo": "ollama",
      "chave": None}
_PADRAO = object()

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-lugares-env-")
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


def extras(texto: str, referencia: date = REF, nome_dela: str | None = None, pessoas: list | None = None) -> list[dict]:
    """O que o recurso propõe para o texto, como a revisão recebe (passando pelo motor e pela fundação)."""
    with recursos.usando(lugares):
        proposta = motor.MotorRegras().analisar(texto, referencia,
                                                Conhecimento(pessoas=pessoas or [], nome_dela=nome_dela))
    return proposta.extras.get("lugares", [])


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


class BaseExtrair(unittest.TestCase):
    def um(self, texto: str, **kw) -> dict:
        achados = extras(texto, **kw)
        self.assertEqual(len(achados), 1, achados)
        e = achados[0]
        self.assertIn(e["trecho"], texto)                          # nunca inventar: trecho literal
        self.assertIn(e["nome"], e["trecho"])                      # o nome é o que está escrito
        return e

    def campos(self, e: dict, *nomes: str) -> tuple:
        return tuple(e[n] for n in nomes)


# ======================================================================== extração: vocês foram, ela foi

class TestExtrairVisitas(BaseExtrair):
    def test_fomos_ontem_e_ela_amou(self):
        e = self.um("Fomos no Coco Bambu ontem e ela amou o camarão.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "status", "mencao", "quem", "opiniao", "reacao", "data"),
                         ("Coco Bambu", "restaurante", "fomos", "visita", "nos", 1, "amou o camarão", "2026-09-29"))
        self.assertEqual(e["resumo"], "Vocês foram: Coco Bambu — ela gostou")
        self.assertEqual(e["certeza"], "observado")
        self.assertEqual(e["trecho"], "Fomos no Coco Bambu ontem e ela amou o camarão.")

    def test_jantamos_no_restaurante_com_ressalva_sem_opiniao(self):
        e = self.um("Jantamos no restaurante Mangiare sábado passado, ela achou meio caro.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "opiniao", "ressalvas", "data", "data_expressao"),
                         ("Mangiare", "restaurante", None, ["caro"], "2026-09-26", "sábado passado"))

    def test_ressalva_mas_gostou_e_opiniao(self):
        # Revisão de 07/10: "achou caro mas gostou" ficava sem opinião (o "gostou" não tinha sujeito escrito).
        e = self.um("jantamos no Outback sexta passada, ela achou caro mas gostou")
        self.assertEqual(self.campos(e, "nome", "opiniao", "ressalvas"), ("Outback", 1, ["caro"]))

    def test_reacao_na_frase_seguinte_entra_no_trecho(self):
        e = self.um("Fomos no Madero ontem. Ela adorou!")

        self.assertEqual((e["opiniao"], e["reacao"]), (1, "adorou"))
        self.assertEqual(e["trecho"], "Fomos no Madero ontem. Ela adorou!")

    def test_levei_ela_e_ela_nao_gostou(self):
        e = self.um("Levei ela no Outback e ela não gostou muito.")
        self.assertEqual(self.campos(e, "quem", "opiniao", "reacao"), ("nos", -1, "não gostou muito"))
        self.assertEqual(e["resumo"], "Vocês foram: Outback — ela não gostou")

    def test_ela_foi_com_as_amigas_sem_voce(self):
        e = self.um("Ela foi no bar do Zé com as amigas e amou.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "mencao", "quem", "opiniao"),
                         ("bar do Zé", "bar", "visita", "ela", 1))
        self.assertTrue(e["resumo"].startswith("Ela foi: bar do Zé"))

    def test_ela_foi_comigo_e_de_voces(self):
        e = self.um("Ela foi comigo no Museu do Ipiranga.")
        self.assertEqual((e["quem"], e["tipo_lugar"]), ("nos", "cultura"))

    def test_praia_com_nome_e_a_gente_foi(self):
        e = self.um("A gente foi na praia de Maresias no feriado.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "quem"), ("praia de Maresias", "praia", "nos"))

    def test_minusculas_e_hj(self):
        e = self.um("fomos no shopping iguatemi hj")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "data", "data_expressao"),
                         ("shopping iguatemi", "compras", DATA, "hj"))

    def test_viagem_que_ja_foi(self):
        e = self.um("Nossa viagem para Paraty foi incrível.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "status", "quem", "opiniao"),
                         ("Paraty", "cidade", "fomos", "nos", None))     # "incrível" é seu, não dela

    def test_viajamos_e_ela_amou(self):
        e = self.um("Viajamos pra Ubatuba no feriado e ela amou a praia.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "opiniao", "reacao"), ("Ubatuba", "cidade", 1, "amou a praia"))

    def test_cidade_depois_do_lugar(self):
        e = self.um("Jantamos no Mangiare em Campinas, ela amou.")
        self.assertEqual(self.campos(e, "nome", "cidade", "opiniao"), ("Mangiare", "Campinas", 1))
        self.assertEqual(e["resumo"], "Vocês foram: Mangiare (Campinas) — ela gostou")

    def test_dois_lugares_na_mesma_frase(self):
        achados = extras("Fomos no Madero e depois na sorveteria Bacio di Latte, ela amou o sorvete.")
        self.assertEqual([(e["nome"], e["tipo_lugar"], e["opiniao"]) for e in achados],
                         [("Madero", "restaurante", None), ("Bacio di Latte", "doces", 1)])

    def test_comemos_algo_no_lugar(self):
        e = self.um("comemos pizza no Bráz e ela achou uma delícia")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "opiniao"), ("Bráz", "restaurante", 1))

    def test_reverso_com_opiniao_antes(self):
        e = self.um("Ela não gostou do restaurante japonês que fomos semana passada.")
        self.assertEqual(self.campos(e, "nome", "generico", "mencao", "opiniao", "data", "data_expressao"),
                         ("restaurante japonês", True, "visita", -1, None, "semana passada"))  # semana: sem dia inventado

    def test_ela_quer_voltar_depois_da_ida(self):
        e = self.um("Fomos no Mangiare e ela já quer voltar.")
        self.assertTrue(e["voltar"])
        self.assertEqual(e["mencao"], "visita")

    def test_ressalva_negada_nao_conta(self):
        e = self.um("Jantamos no Mangiare ontem e não era caro.")
        self.assertEqual(e["ressalvas"], [])

    def test_natal_a_cidade(self):
        e = self.um("Viajamos pra Natal no feriado.")
        self.assertEqual((e["nome"], e["tipo_lugar"]), ("Natal", "cidade"))

    def test_ela_me_levou_e_fui_com_ela(self):
        self.assertEqual(self.um("Ela me levou no Bar Brahma.")["quem"], "nos")
        self.assertEqual(self.um("Fui com ela no Mercadão.")["quem"], "nos")
        self.assertEqual(self.um("Ela foi no Mercadão com a gente.")["quem"], "nos")

    def test_cidade_da_viagem_na_mesma_frase(self):
        e = self.um("Na viagem pra Gramado, fomos no café Colonial Bavária e ela amou.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "cidade", "opiniao"), ("Bavária", "cafe", "Gramado", 1))

    def test_texto_longo_e_rapido(self):
        import time
        frase = ("Fomos no Coco Bambu ontem e ela amou o camarão, depois ela disse que quer conhecer o Beco do Batman "
                 "e a gente combinou de ir no Ibirapuera domingo com a mãe dela, mas a Mariana não quis ir. ")
        inicio = time.perf_counter()
        achados = lugares.extrair(frase * 40, REF, [], [], [], None)
        self.assertLess(time.perf_counter() - inicio, 5.0)
        self.assertEqual(sorted({e["nome"] for e in achados}), ["Beco do Batman", "Coco Bambu", "Ibirapuera"])


# ======================================================================== extração: vontades

class TestExtrairVontades(BaseExtrair):
    def test_ela_quer_conhecer(self):
        e = self.um("Ela quer conhecer o Beco do Batman.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "status", "mencao", "quem"),
                         ("Beco do Batman", "passeio", "queremos_ir", "vontade", "ela"))
        self.assertEqual(e["resumo"], "Ela quer conhecer: Beco do Batman")

    def test_sempre_quis_ir_pra_cidade(self):
        e = self.um("Ela sempre quis ir pra Gramado no inverno.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "quem"), ("Gramado", "cidade", "ela"))

    def test_fala_dela_confirma(self):
        e = self.um("Ela comentou que tem vontade de conhecer o Museu do Ipiranga.")
        self.assertEqual((e["nome"], e["certeza"]), ("Museu do Ipiranga", "confirmado"))

    def test_acho_que_e_inferencia(self):
        self.assertEqual(self.um("Acho que ela quer conhecer o Beco do Batman.")["certeza"], "inferencia")

    def test_cafe_com_nome_tira_o_tipo_do_nome(self):
        e = self.um("Ela quer ir no café Santo Grão.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar"), ("Santo Grão", "cafe"))

    def test_girias_sem_acento(self):
        e = self.um("ela ta doida pra conhecer a vinicola Guaspari")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "quem"), ("Guaspari", "bar", "ela"))
        f = self.um("ela quer conhecer gramado")
        self.assertEqual((f["nome"], f["tipo_lugar"]), ("gramado", "cidade"))

    def test_tipo_de_lugar_com_cozinha(self):
        e = self.um("Ela quer muito ir num restaurante mexicano.")
        self.assertEqual(self.campos(e, "nome", "generico", "tipo_lugar"), ("restaurante mexicano", True, "restaurante"))
        self.assertEqual(e["resumo"], "Ela quer ir: restaurante mexicano")

    def test_voces_combinaram_com_data_pelo_python(self):
        e = self.um("Vamos no Coco Bambu sábado.")
        self.assertEqual(self.campos(e, "quem", "status", "data", "data_expressao"),
                         ("nos", "queremos_ir", "2026-10-03", "sábado"))
        g = self.um("A gente combinou de ir no Ibirapuera domingo.")
        self.assertEqual(self.campos(g, "nome", "tipo_lugar", "quem", "data"), ("Ibirapuera", "natureza", "nos", "2026-10-04"))

    def test_sua_ideia(self):
        e = self.um("Quero levar ela no Jardim Botânico.")
        self.assertEqual(self.campos(e, "nome", "quem", "tipo_lugar"), ("Jardim Botânico", "eu", "natureza"))
        self.assertEqual(e["resumo"], "Você quer levar ela: Jardim Botânico")
        f = self.um("quero levar a ju no museu do ipiranga", nome_dela="Julia")
        self.assertEqual((f["quem"], f["nome"]), ("eu", "museu do ipiranga"))

    def test_ela_quer_voltar(self):
        e = self.um("ela quer voltar no Mangiare")
        self.assertEqual(self.campos(e, "voltar", "quem", "status"), (True, "ela", "queremos_ir"))
        self.assertEqual(e["resumo"], "Ela quer voltar: Mangiare")

    def test_lista_de_cidades(self):
        self.assertEqual([e["nome"] for e in extras("Ela sonha em conhecer Paris e Roma.")], ["Paris", "Roma"])

    def test_convite_dela(self):
        e = self.um("Ela me chamou pra ir no festival Coala.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "quem"), ("Coala", "show", "ela"))

    def test_apelido_dela_como_sujeito(self):
        e = self.um("A Ju quer conhecer o Beco do Batman.", nome_dela="Julia")
        self.assertEqual(e["quem"], "ela")


# ======================================================================== extração: opinião sobre um lugar

class TestExtrairOpinioes(BaseExtrair):
    def test_ela_adora_o_parque(self):
        e = self.um("Ela adora o Parque Ibirapuera.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "mencao", "status", "opiniao", "reacao"),
                         ("Parque Ibirapuera", "natureza", "opiniao", "fomos", 1, "adora"))
        self.assertEqual(e["resumo"], "Ela gosta de: Parque Ibirapuera")

    def test_odiou_com_ressalva_e_fala_dela(self):
        e = self.um("Ela disse que odiou o bar Pinguim, muito barulhento.")
        self.assertEqual(self.campos(e, "nome", "opiniao", "ressalvas", "certeza"),
                         ("Pinguim", -1, ["barulhento"], "confirmado"))

    def test_lugar_favorito_dela(self):
        e = self.um("O restaurante favorito dela é o Mangiare.")
        self.assertEqual(self.campos(e, "nome", "tipo_lugar", "opiniao", "reacao"),
                         ("Mangiare", "restaurante", 1, "restaurante favorito dela"))

    def test_nao_gosta_do_shopping(self):
        e = self.um("Ela não gosta do shopping Iguatemi.")
        self.assertEqual((e["nome"], e["opiniao"]), ("Iguatemi", -1))

    def test_opiniao_sobre_comida_nao_e_lugar(self):
        self.assertEqual(extras("Ela adora sushi."), [])
        self.assertEqual(extras("Ela ama praia."), [])                # tipo sem nome: o núcleo já guarda o gosto


# ======================================================================== não inventar

class TestNaoInventa(unittest.TestCase):
    def nada(self, *textos: str, nome_dela: str | None = None, pessoas: list | None = None) -> None:
        for texto in textos:
            with self.subTest(texto=texto):
                self.assertEqual(extras(texto, nome_dela=nome_dela, pessoas=pessoas), [])

    def test_ida_que_nao_e_lugar(self):
        self.nada("Fomos no mercado comprar pão.", "Fomos na casa da mãe dela.", "Fomos no médico ontem.",
                  "Fomos no aniversário da Mariana.", "Ela foi no trabalho.", "Ela quer ir embora.",
                  "ela queria ir na academia", "Fomos dormir cedo.", "Fomos de carro.")

    def test_tipo_sozinho_nao_e_um_lugar(self):
        self.nada("Fomos na praia ontem e ela amou.", "Fomos no cinema.", "Ela quer ir num bar.",
                  "Fomos no parque de manhã.", "Fomos num restaurante maravilhoso.")

    def test_negacao_condicao_pergunta_e_ironia(self):
        self.nada("Nunca fomos no Madero.", "Ela não quer ir no Outback.", "Se a gente for no Madero, ela vai amar.",
                  "Será que ela quer conhecer o Beco do Batman?", "Fomos no Coco Bambu e ela amou, sqn",
                  "A gente ainda não foi no Mangiare.")

    def test_duvida_e_fala_negada(self):
        self.nada("Não sei se ela quer conhecer Gramado.", "Ela não disse que quer conhecer Paris.")

    def test_lugar_de_outra_pessoa(self):
        self.nada("A mãe dela quer conhecer Gramado.", "A Mariana foi no bar do Zé.", "Eu fui no Madero com meus amigos.",
                  "A Mariana me contou que quer conhecer Paris.", "O irmão dela foi pra Paris.")

    def test_gente_nao_e_lugar(self):
        self.nada("Ela adora o Pedro.", "Fomos na Ju ontem.", nome_dela="Julia")
        self.nada("Fomos na Mariana ontem.", pessoas=[{"id": 1, "nome": "Mariana", "relacao": "amiga", "apelidos": []}])
        self.nada("Fomos no Natal na casa da avó dela.")             # "no Natal" é a data, não a cidade

    def test_contrato_dos_extras_num_texto_longo(self):
        texto = ("Ontem foi um dia corrido. Fomos no Coco Bambu e ela amou o camarão. Ela quer conhecer o Beco do "
                 "Batman. A mãe dela quer conhecer Gramado. Ela não gosta de lugar barulhento. Vamos no Madero sábado.")
        achados = extras(texto)
        self.assertEqual(sorted(e["nome"] for e in achados), ["Beco do Batman", "Coco Bambu", "Madero"])
        for e in achados:
            with self.subTest(extra=e["nome"]):
                self.assertEqual(e["tipo"], "lugar")
                self.assertIn(e["trecho"], texto)
                self.assertIn(e["nome"], e["trecho"])
                if e["reacao"]:
                    self.assertIn(e["reacao"], e["trecho"])
                for r in e["ressalvas"]:
                    self.assertIn(r, e["trecho"])
                self.assertTrue(e["resumo"])
                self.assertNotIn("_privado", e)                   # lugar não deixa o registro fora da IA
                json.dumps(e, ensure_ascii=False)

    def test_extrair_direto_aceita_entradas_vazias(self):
        self.assertEqual(lugares.extrair("", REF, [], [], [], None), [])
        self.assertEqual(lugares.extrair(None, REF, [], [], [], None), [])
        r = lugares.extrair("Ela quer conhecer o Beco do Batman.", REF, [], [], [], None)
        self.assertEqual([e["nome"] for e in r], ["Beco do Batman"])


class TestChave(unittest.TestCase):
    def test_mesma_chave_para_o_mesmo_lugar(self):
        self.assertEqual(lugares.chave_lugar("Restaurante Mangiare"), "mangiare")
        self.assertEqual(lugares.chave_lugar("o Mangiare"), "mangiare")
        self.assertEqual(lugares.chave_lugar("Parque Ibirapuera"), lugares.chave_lugar("ibirapuera"))
        self.assertEqual(lugares.chave_lugar("Museu do Ipiranga"), "museu do ipiranga")
        self.assertEqual(lugares.chave_lugar("restaurante mexicano"), "restaurante mexicano")

    def test_mesmo_lugar(self):
        self.assertTrue(lugares.mesmo_lugar("Coco Bambu", "coco bambu"))
        self.assertTrue(lugares.mesmo_lugar("Coco Bambu", "Coco Bambu Campinas"))
        self.assertFalse(lugares.mesmo_lugar("Madero", "Mangiare"))
        self.assertFalse(lugares.mesmo_lugar("restaurante mexicano", "restaurante mexicano Guacamole", generico=True))
        self.assertFalse(lugares.mesmo_lugar("", "Madero"))


# ======================================================================== base do núcleo

class BaseNucleo(unittest.TestCase):
    config: dict = {}

    def setUp(self):
        self.enterContext(recursos.usando(lugares))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-lugares-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.hoje = REF
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: self.hoje, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(self.app.bloquear)
        self.token = None
        r = self.pedir("POST", "/api/configurar", {"pin": PIN, **self.config})
        self.assertEqual(r.status, 200, r.dados)
        self.token = r.dados["token"]

    @property
    def banco(self) -> Banco:
        return self.app._banco

    def pedir(self, metodo, caminho, corpo=None, token=_PADRAO, **kw) -> nucleo.RespostaApi:
        bruto = None if corpo is None else json.dumps(corpo).encode("utf-8")
        return self.app.tratar(metodo, caminho, corpo=bruto, token=self.token if token is _PADRAO else token, **kw)

    def analisar(self, texto: str, data: str = DATA) -> dict:
        r = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": data})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def corpo(self, proposta: dict, data: str = DATA, confirmar: bool = True, mexer=None, **mais) -> dict:
        lista = [copy.deepcopy(e) for e in proposta["extras"].get("lugares", [])] if confirmar else []
        for e in lista:
            if mexer:
                e.update(mexer)
        return {"texto": proposta["texto"], "data_registro": data, "itens": proposta["itens"],
                "pessoas": [], "relacoes": proposta["relacoes"],
                "extras": {"lugares": lista} if lista else {}, **mais}

    def registrar(self, texto: str, data: str = DATA, confirmar: bool = True, mexer=None) -> int:
        r = self.pedir("POST", "/api/registros", self.corpo(self.analisar(texto, data), data, confirmar, mexer))
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def painel(self, data: str | None = None) -> dict:
        r = self.pedir("GET", "/api/lugares", consulta={"data": data} if data else None)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(json.loads(r.corpo.decode("utf-8")), r.dados)
        return r.dados

    def cartoes(self, data: str | None = None) -> list[dict]:
        r = self.pedir("GET", "/api/hoje", consulta={"data": data} if data else None)
        self.assertEqual(r.status, 200, r.dados)
        return [a for a in r.dados["alertas"] if a.get("recurso") == "lugares"]

    def linhas(self, tabela: str = "lugares") -> list[dict]:
        return self.banco.consultar(f"SELECT * FROM {tabela} ORDER BY id")

    def por_nome(self, lista: list[dict]) -> dict[str, dict]:
        return {x["nome"]: x for x in lista}


# ======================================================================== gravar

class TestGravar(BaseNucleo):
    def test_confirmado_vira_lugar_e_mencao(self):
        rid = self.registrar("Fomos no Coco Bambu ontem e ela amou o camarão.")
        [l] = self.linhas()
        self.assertEqual((l["nome"], l["tipo"], l["status"], l["opiniao"], l["juntos"], json.loads(l["registro_ids"])),
                         ("Coco Bambu", "restaurante", "fomos", 1, 1, [rid]))
        [m] = self.linhas("lugares_mencoes")
        self.assertEqual((m["registro_id"], m["mencao"], m["quem"], m["data"], m["reacao"]),
                         (rid, "visita", "nos", "2026-09-29", "amou o camarão"))
        self.assertFalse(self.pedir("GET", f"/api/registros/{rid}").dados["privado"])     # lugar não é 🔒

    def test_o_que_nao_foi_confirmado_nao_e_gravado(self):
        self.registrar("Fomos no Coco Bambu ontem e ela amou o camarão.", confirmar=False)
        self.assertEqual(self.linhas(), [])
        self.assertEqual(self.linhas("lugares_mencoes"), [])

    def test_certeza_nunca_sobe_e_reacao_inventada_cai(self):
        self.registrar("Fomos no Coco Bambu ontem.", mexer={"certeza": "confirmado", "reacao": "chorou de emoção",
                                                            "ressalvas": ["sujo", "ontem"]})
        [m] = self.linhas("lugares_mencoes")
        self.assertEqual(m["certeza"], "observado")
        self.assertIsNone(m["reacao"])                                # não está escrito: não fica
        self.assertEqual(json.loads(m["ressalvas"]), ["ontem"])       # só o que está no trecho

    def test_correcao_do_usuario_vale(self):
        self.registrar("Ela quer conhecer o Beco do Batman.",
                       mexer={"nome": "Beco do Batman (Vila Madalena)", "tipo_lugar": "cultura", "cidade": "São Paulo"})
        [l] = self.linhas()
        self.assertEqual((l["nome"], l["tipo"], l["cidade"], l["status"], l["quem_quer"]),
                         ("Beco do Batman (Vila Madalena)", "cultura", "São Paulo", "queremos_ir", "ela"))

    def test_trocar_a_situacao_na_revisao(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", mexer={"status": "fomos"})
        [m] = self.linhas("lugares_mencoes")
        self.assertEqual(m["mencao"], "visita")
        self.assertEqual(self.linhas()[0]["status"], "fomos")
        # "Vamos no Coco Bambu sábado" marcado como "já fomos": a data do plano não vira o dia da ida.
        self.registrar("Vamos no Coco Bambu sábado.", mexer={"status": "fomos"})
        m = self.linhas("lugares_mencoes")[-1]
        self.assertEqual((m["mencao"], m["data"], m["data_expressao"]), ("visita", None, None))

    def test_campos_que_nao_servem_400_e_nada_gravado(self):
        p = self.analisar("Fomos no Coco Bambu ontem e ela amou o camarão.")
        casos = {"nome vazio": {"nome": " "}, "nome longo": {"nome": "x" * 81}, "tipo": {"tipo_lugar": "boate"},
                 "status": {"status": "talvez"}, "opinião": {"opiniao": 2}, "opinião bool": {"opiniao": True},
                 "data": {"data": "29/09/2026"}, "ida no futuro": {"data": "2026-10-10"},
                 "quem": {"quem": "mae"}, "menção": {"mencao": "sonho"}, "voltar": {"voltar": "sim"}}
        for nome, mexer in casos.items():
            with self.subTest(caso=nome):
                r = self.pedir("POST", "/api/registros", self.corpo(p, mexer=mexer))
                self.assertEqual(r.status, 400, r.dados)
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])
        self.assertEqual(self.linhas(), [])

    def test_trecho_inventado_400(self):
        p = self.analisar("Ela quer conhecer o Beco do Batman.")
        r = self.pedir("POST", "/api/registros", self.corpo(p, mexer={"trecho": "Ela quer conhecer Paris."}))
        self.assertEqual((r.status, r.dados["erro"]), (400, recursos.EXTRA_SEM_TRECHO))
        self.assertEqual(self.linhas(), [])

    def test_o_mesmo_lugar_em_registros_diferentes_junta(self):
        a = self.registrar("Ela quer conhecer o restaurante Mangiare.", "2026-08-01")
        b = self.registrar("Jantamos no Mangiare ontem e ela adorou.", "2026-09-20")
        [l] = self.linhas()
        self.assertEqual((l["status"], l["opiniao"], l["quem_quer"], json.loads(l["registro_ids"])),
                         ("fomos", 1, "ela", [a, b]))
        self.assertEqual(len(self.linhas("lugares_mencoes")), 2)


# ======================================================================== a aba

class TestPainel(BaseNucleo):
    def test_vazio_nao_inventa_nada(self):
        p = self.painel()
        self.assertTrue(p["vazio"])
        for chave in ("quer_conhecer", "fomos", "evitar_lugares"):
            self.assertEqual(p[chave], [], chave)
        self.assertEqual(p["pistas"], {"curte": [], "evitar": []})
        self.assertIsNone(p["sugestao"])
        self.assertIsNone(p["momento"])
        self.assertEqual(p["sem_sugestao"], "sem_lugares")

    def test_quer_conhecer_em_ordem_explicavel(self):
        self.registrar("Quero levar ela no Jardim Botânico.", "2026-09-01")
        self.registrar("A gente quer conhecer o Mercadão.", "2026-09-01")
        self.registrar("Ela disse que quer conhecer o Beco do Batman.", "2026-09-01")
        self.registrar("ela quer conhecer o Beco do Batman", "2026-09-20")
        quer = self.painel()["quer_conhecer"]
        self.assertEqual([l["nome"] for l in quer], ["Beco do Batman", "Mercadão", "Jardim Botânico"])
        beco = quer[0]
        self.assertEqual((beco["quem_quer"], beco["vezes"], beco["certeza"]), ("ela", 2, "confirmado"))
        self.assertIn("ela mesma quer conhecer", beco["motivos"])
        self.assertIn("ela disse com as palavras dela 🟢", beco["motivos"])
        self.assertIn("apareceu em 2 registros", beco["motivos"])
        self.assertEqual(len(beco["mencoes"]), 2)
        self.assertTrue(all(m["trecho"] and m["registro_id"] for m in beco["mencoes"]))      # cada coisa cita a origem
        self.assertIn("é uma ideia sua", quer[2]["motivos"])

    def test_lugares_de_voces_com_opiniao_e_ressalvas(self):
        self.registrar("Jantamos no Mangiare em Campinas, ela amou.", "2026-08-10")
        self.registrar("Ela foi no bar do Zé com as amigas e achou meio barulhento.", "2026-09-10")
        fomos = self.por_nome(self.painel()["fomos"])
        self.assertEqual((fomos["Mangiare"]["opiniao"], fomos["Mangiare"]["juntos"], fomos["Mangiare"]["cidade"]),
                         (1, True, "Campinas"))
        self.assertEqual(fomos["Mangiare"]["ultima_visita"], "2026-08-10")
        self.assertEqual((fomos["bar do Zé"]["juntos"], fomos["bar do Zé"]["ressalvas"]), (False, ["barulhento"]))
        self.assertEqual([l["nome"] for l in self.painel()["fomos"]], ["bar do Zé", "Mangiare"])   # mais recente primeiro

    def test_combina_com_o_que_ela_gosta(self):
        self.registrar("Ela disse que ama comida japonesa.", "2026-09-01")
        self.registrar("Ela quer muito ir num restaurante japonês.", "2026-09-02")
        [l] = self.painel()["quer_conhecer"]
        self.assertTrue(any(m.startswith("combina com o que ela gosta: comida japonesa") for m in l["motivos"]), l["motivos"])

    def test_restricao_avisa_e_pesa(self):
        self.registrar("Ela é vegetariana.", "2026-09-01")
        self.registrar("Ela quer conhecer a churrascaria Fogo de Chão.", "2026-09-10")
        self.registrar("Ela quer conhecer o café Santo Grão.", "2026-09-10")
        quer = self.painel()["quer_conhecer"]
        self.assertEqual([l["nome"] for l in quer], ["Santo Grão", "Fogo de Chão"])
        churrascaria = quer[1]
        self.assertTrue(any("“Ela é vegetariana.”" in a and "pode não combinar" in a for a in churrascaria["avisos"]),
                        churrascaria["avisos"])

    def test_ela_nao_quer_vai_para_evitar(self):
        self.registrar("A gente quer ir no Outback.", "2026-09-01")
        self.registrar("Ela não quer ir no Outback.", "2026-09-10")
        p = self.painel()
        self.assertEqual(p["quer_conhecer"], [])
        [o] = p["evitar_lugares"]
        self.assertEqual((o["nome"], o["excluido"]), ("Outback", "ela disse que não quer"))
        self.assertIsNone(p["sugestao"])

    def test_pistas_do_que_ela_curte_e_evita(self):
        self.registrar("Ela odeia lugar barulhento.", "2026-09-01")
        self.registrar("Ela adora lugares tranquilos.", "2026-09-02")
        self.registrar("Ela tem alergia a camarão.", "2026-09-03")
        pistas = self.painel()["pistas"]
        self.assertEqual([x["alvo"] for x in pistas["curte"]], ["lugares tranquilos"])
        self.assertEqual(sorted(x["alvo"] for x in pistas["evitar"]), ["camarão", "lugar barulhento"])
        self.assertTrue(all(x["trecho"] and x["registro_id"] for x in pistas["curte"] + pistas["evitar"]))

    def test_atributo_do_lugar_pesa_com_o_que_ela_disse(self):
        self.registrar("Ela odeia lugar barulhento.", "2026-09-01")
        self.registrar("Ela quer conhecer a balada Lions.", "2026-09-02")
        self.registrar("Ela quer conhecer o Parque Lage.", "2026-09-02")
        quer = self.painel()["quer_conhecer"]
        self.assertEqual(quer[0]["nome"], "Parque Lage")
        balada = quer[1]
        self.assertTrue(any("lugar barulhento" in a for a in balada["avisos"]), balada["avisos"])

    def test_momento_dificil_aparece_e_segura_a_sugestao(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        self.registrar("Ela está muito cansada.", "2026-09-29")
        p = self.painel()
        self.assertEqual((p["momento"]["texto"], p["momento"]["bloqueia"]), ("Está cansada", True))
        self.assertIsNone(p["sugestao"])
        self.assertEqual(p["sem_sugestao"], "momento")

    def test_um_lugar_com_todas_as_mencoes(self):
        self.registrar("Ela quer conhecer o restaurante Mangiare.", "2026-08-01")
        self.registrar("Jantamos no Mangiare ontem e ela adorou.", "2026-09-20")
        [l] = self.linhas()
        r = self.pedir("GET", f"/api/lugares/{l['id']}")
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual([m["mencao"] for m in r.dados["mencoes"]], ["visita", "vontade"])
        self.assertEqual(self.pedir("GET", "/api/lugares/999").status, 404)


# ======================================================================== ações

class TestAcoes(BaseNucleo):
    def um_lugar(self, texto="Ela quer conhecer o Beco do Batman.", data="2026-09-01") -> int:
        self.registrar(texto, data)
        return self.linhas()[0]["id"]

    def test_corrigir_e_a_correcao_vale_mais_que_os_registros(self):
        lid = self.um_lugar("Jantamos no Mangiare e ela amou.")
        r = self.pedir("PATCH", f"/api/lugares/{lid}", {"opiniao": None, "nome": "Mangiare Trattoria", "tipo": "restaurante"})
        self.assertEqual(r.status, 200, r.dados)
        self.registrar("Jantamos no Mangiare Trattoria de novo e ela adorou.", "2026-09-20")
        [l] = self.linhas()
        self.assertEqual((l["nome"], l["opiniao"]), ("Mangiare Trattoria", None))       # o que você corrigiu fica
        self.assertEqual(sorted(json.loads(l["editado"])), ["nome", "opiniao", "tipo"])

    def test_corrigir_recusa_o_que_nao_serve(self):
        lid = self.um_lugar()
        for corpo in ({}, {"registro_ids": [1]}, {"tipo": "boate"}, {"status": "talvez"}, {"opiniao": 3},
                      {"nome": ""}, {"cidade": "x" * 61}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.pedir("PATCH", f"/api/lugares/{lid}", corpo).status, 400)
        self.registrar("Ela quer conhecer o Mercadão.", "2026-09-02")
        self.assertEqual(self.pedir("PATCH", f"/api/lugares/{lid}", {"nome": "o mercadão"}).dados["erro"],
                         lugares.ERRO_REPETIDO)
        self.assertEqual(self.pedir("PATCH", "/api/lugares/999", {"tipo": "bar"}).status, 404)

    def test_fomos(self):
        lid = self.um_lugar()
        self.assertEqual(self.pedir("POST", f"/api/lugares/{lid}/fomos", {"data": "2026-10-10"}).status, 400)   # futuro
        self.assertEqual(self.pedir("POST", f"/api/lugares/{lid}/fomos", {"quando": "ontem"}).status, 400)
        self.assertEqual(self.pedir("POST", f"/api/lugares/{lid}/fomos", {"data": "2026-09-27"}).status, 200)
        [l] = self.linhas()
        self.assertEqual((l["status"], l["juntos"], l["visitado_em"], l["opiniao"]), ("fomos", 1, "2026-09-27", None))
        [f] = self.painel()["fomos"]
        self.assertEqual((f["nome"], f["visitas"], f["ultima_visita"]), ("Beco do Batman", 1, "2026-09-27"))

    def test_voltar_para_a_lista_desfaz_o_fomos(self):
        lid = self.um_lugar()
        self.pedir("POST", f"/api/lugares/{lid}/fomos", {})
        self.assertEqual(self.pedir("PATCH", f"/api/lugares/{lid}", {"status": "queremos_ir"}).status, 200)
        [l] = self.linhas()
        self.assertEqual((l["status"], l["visitado_em"]), ("queremos_ir", None))

    def test_apagar_e_as_copias(self):
        lid = self.um_lugar()
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/lugares/{lid}").status, 200)
        self.assertEqual((self.linhas(), self.linhas("lugares_mencoes")), ([], []))
        self.assertEqual(len(repetir), 1)
        repetir[0](copia)                                  # o que o cofre faria em cada cópia
        self.assertEqual(copia.consultar("SELECT * FROM lugares"), [])
        self.assertEqual(copia.consultar("SELECT * FROM lugares_mencoes"), [])
        self.assertEqual(self.pedir("DELETE", f"/api/lugares/{lid}").status, 404)
        self.assertEqual(len(self.pedir("GET", "/api/registros").dados), 1)            # o registro continua

    def test_apagar_uma_mencao_refaz_o_lugar(self):
        self.registrar("Ela quer conhecer o restaurante Mangiare.", "2026-08-01")
        self.registrar("Jantamos no Mangiare ontem e ela adorou.", "2026-09-20")
        visita = next(m for m in self.linhas("lugares_mencoes") if m["mencao"] == "visita")
        self.assertEqual(self.pedir("DELETE", f"/api/lugares/mencoes/{visita['id']}").status, 200)
        [l] = self.linhas()
        self.assertEqual((l["status"], l["opiniao"], l["juntos"]), ("queremos_ir", None, 0))
        vontade = self.linhas("lugares_mencoes")[0]
        self.assertEqual(self.pedir("DELETE", f"/api/lugares/mencoes/{vontade['id']}").status, 200)
        self.assertEqual(self.linhas(), [])                # sem menção, o lugar sai
        self.assertEqual(self.pedir("DELETE", f"/api/lugares/mencoes/{vontade['id']}").status, 404)

    def test_rotas_pedem_o_pin(self):
        for metodo, caminho in (("GET", "/api/lugares"), ("GET", "/api/lugares/1"), ("PATCH", "/api/lugares/1"),
                                ("DELETE", "/api/lugares/1"), ("POST", "/api/lugares/1/fomos"),
                                ("DELETE", "/api/lugares/mencoes/1")):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.pedir(metodo, caminho, {} if metodo != "GET" else None, token=None).status, 401)


# ======================================================================== Hoje

class TestHoje(BaseNucleo):
    def setUp(self):
        super().setUp()
        self.hoje = QUINTA

    def test_sugestao_da_semana_cita_e_explica(self):
        rid = self.registrar("Ela disse que quer conhecer o Beco do Batman.", "2026-09-20")
        [c] = self.cartoes()
        self.assertEqual(c["texto"], "Talvez o Beco do Batman, que ela quer conhecer, seja uma boa para o próximo encontro.")
        lid = self.linhas()[0]["id"]
        self.assertEqual((c["nivel"], c["chave"], c["tipo"]), ("sugestao", f"lugares:sugestao:{lid}:{SEMANA}",
                                                               "lugares_sugestao"))
        self.assertIn("“Ela disse que quer conhecer o Beco do Batman.”", c["porque"])
        self.assertIn("ela mesma quer conhecer", c["dados"]["motivos"])
        self.assertEqual(c["registro_ids"], [rid])
        self.assertEqual(c["dados"]["origens"][0]["trecho"], "Ela disse que quer conhecer o Beco do Batman.")

    def test_sem_lugar_nenhum_cartao(self):
        self.registrar("Ela adora lugares tranquilos.")                 # gosto não é lugar: nada genérico
        self.registrar("Fomos no Madero e ela achou ok.", "2026-09-01")   # sem "gostou", nada para voltar
        self.assertEqual(self.cartoes(), [])

    def test_no_maximo_um_por_semana_e_agora_nao(self):
        self.registrar("Ela disse que quer conhecer o Beco do Batman.", "2026-09-20")
        self.registrar("Ela quer conhecer o Museu do Ipiranga.", "2026-09-10")
        cartoes = self.cartoes()
        self.assertEqual(len(cartoes), 1)
        self.assertIn("Beco do Batman", cartoes[0]["texto"])
        self.assertEqual(self.pedir("POST", "/api/alertas/dispensar", {"chave": cartoes[0]["chave"]}).status, 200)
        self.assertEqual(self.cartoes(), [])                            # nenhum outro na mesma semana
        self.assertEqual(self.cartoes("2026-10-04"), [])                # domingo ainda é a semana 40
        [c] = self.cartoes("2026-10-08")                                # semana nova (quinta): o Beco descansa
        self.assertIn("Museu do Ipiranga", c["texto"])
        self.assertTrue(c["chave"].endswith(":2026-W41"))
        s = self.painel("2026-11-02")["sugestao"]                      # 5 semanas depois ele volta, com um ponto a menos
        self.assertEqual(s["nome"], "Beco do Batman")
        self.assertIn("você deixou para depois 1 vez", s["motivos"])

    def test_momento_dificil_nao_sugere_sair(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        self.registrar("Ela está muito cansada.", "2026-09-28")
        self.assertEqual(self.cartoes(), [])
        self.assertEqual(len(self.cartoes("2026-10-08")), 1)            # passou (e é outra semana)

    def test_ja_sairam_esta_semana(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        self.registrar("Fomos no Madero ontem.")                        # 29/09, semana 40
        self.assertEqual(self.cartoes(), [])
        self.assertEqual(self.painel()["sem_sugestao"], "saiu")

    def test_ida_antiga_registrada_hoje_nao_conta_como_esta_semana(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        self.registrar("Fomos no Madero em 2019.")
        self.assertEqual(len(self.cartoes()), 1)

    def test_voltar_a_um_lugar_que_ela_amou(self):
        self.registrar("Jantamos no Mangiare e ela amou, mas achou meio caro.", "2026-06-01")
        [c] = self.cartoes()
        self.assertEqual(c["texto"], "Talvez voltar ao Mangiare, que ela gostou, seja uma boa para o próximo encontro.")
        self.assertEqual(c["dados"]["modo"], "rever")
        self.assertEqual(c["dados"]["avisos"], ["Ressalvas que você registrou dela: caro."])
        self.assertEqual(self.cartoes("2026-07-16"), [])                # só depois de 60 dias (uma quinta)

    def test_lugar_que_ela_nao_gostou_nunca_volta_como_sugestao(self):
        self.registrar("Jantamos no Outback e ela odiou.", "2026-05-01")
        self.registrar("Ela disse que odiou o bar Pinguim, muito barulhento.", "2026-05-02")
        self.assertEqual(self.cartoes(), [])
        self.assertEqual(self.cartoes("2027-01-07"), [])

    def test_ela_quer_voltar(self):
        self.registrar("Jantamos no Mangiare e ela já quer voltar.", "2026-09-01")
        [c] = self.cartoes()
        self.assertEqual(c["dados"]["modo"], "voltar")
        self.assertIn("ela mesma quer voltar", c["dados"]["motivos"])

    def test_plano_combinado_nao_vira_sugestao(self):
        self.registrar("Vamos no Coco Bambu sábado.")
        self.assertEqual(self.cartoes(), [])
        [l] = self.painel()["quer_conhecer"]
        self.assertEqual(l["planejado"]["data"], "2026-10-03")

    def test_viagem_registrada_com_lugar_da_cidade(self):
        self.registrar("Ela quer conhecer o café Bavária em Gramado.", "2026-09-01")
        self.registrar("Vamos viajar pra Gramado dia 20/10.", "2026-09-02")
        [c] = self.cartoes()
        self.assertEqual(c["texto"], "Talvez dê para conhecer o Bavária na viagem para Gramado (20/10).")
        self.assertEqual(c["dados"]["viagem"]["data"], "2026-10-20")
        self.assertEqual(len(c["registro_ids"]), 2)


class TestHojeOcasiao(BaseNucleo):
    config = {"inicio_namoro": "2025-10-20"}

    def setUp(self):
        super().setUp()
        self.hoje = QUINTA

    def test_aniversario_de_namoro_chegando(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        [c] = self.cartoes()
        self.assertEqual(c["texto"], "Talvez o Beco do Batman, que ela quer conhecer, seja uma boa para o "
                                     "aniversário de namoro (em 19 dias).")
        self.assertIn("informado em Ajustes", c["porque"])
        self.assertEqual(c["dados"]["ocasiao"]["data"], "2026-10-20")

    def test_dispensado_nao_volta(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        chave = self.cartoes()[0]["chave"]
        self.pedir("POST", "/api/alertas/dispensar", {"chave": chave})
        self.assertEqual(self.cartoes(), [])


class TestHojeDiaDaSemana(BaseNucleo):
    """O cartão de lugar no Hoje só na quinta e na sexta (planejar o fim de semana) ou com uma data
    de vocês (mesversário, aniversário dela ou de namoro) em até 3 dias (20.5.1). A aba Lugares mostra
    a sugestão em qualquer dia."""
    config = {"inicio_namoro": "2025-03-20"}            # mesversário todo dia 20

    def test_so_quinta_e_sexta(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        dias = {"2026-09-28": 0, "2026-09-29": 0, "2026-09-30": 0, "2026-10-01": 1, "2026-10-02": 1}
        for dia, esperado in dias.items():
            with self.subTest(dia=dia):
                self.assertEqual(len(self.cartoes(dia)), esperado)
        # Sábado e domingo também não (o fim de semana já chegou).
        self.assertEqual(self.cartoes("2026-10-03"), [])
        self.assertEqual(self.cartoes("2026-10-04"), [])
        # A aba continua com a sugestão numa quarta.
        self.assertEqual(self.painel("2026-09-30")["sugestao"]["nome"], "Beco do Batman")

    def test_data_especial_em_ate_tres_dias(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        # Segunda 19/10: o mesversário (20/10) é amanhã.
        [c] = self.cartoes("2026-10-19")
        self.assertIn("Beco do Batman", c["texto"])
        self.assertIn("mesversário", c["texto"])
        # Segunda 16/11: o mesversário de 20/11 está a 4 dias: nada.
        self.assertEqual(self.cartoes("2026-11-16"), [])
        # Terça 17/11: a 3 dias, aparece.
        self.assertEqual(len(self.cartoes("2026-11-17")), 1)


class TestHojeAniversarioDela(BaseNucleo):
    config = {"aniversario_dela": "1998-10-13"}

    def test_aniversario_dela_em_ate_tres_dias(self):
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        self.assertEqual(len(self.cartoes("2026-10-12")), 1)            # segunda, véspera do aniversário
        self.assertEqual(self.cartoes("2026-10-06"), [])                # terça, a 7 dias: não


# ======================================================================== exportar, importar, apagar

class TestLevarEApagar(BaseNucleo):
    def montar(self) -> None:
        self.registrar("Ela quer conhecer o restaurante Mangiare.", "2026-08-01")
        self.registrar("Jantamos no Mangiare ontem e ela adorou.", "2026-09-20")
        self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-21")
        beco = next(l for l in self.linhas() if l["nome"] == "Beco do Batman")
        self.pedir("PATCH", f"/api/lugares/{beco['id']}", {"tipo": "cultura"})

    def test_exportar_e_juntar_noutro_diario_sem_duplicar(self):
        self.montar()
        exportado = self.pedir("GET", "/api/exportar").dados
        dados = exportado["recursos"]["lugares"]
        self.assertEqual((len(dados["lugares"]), len(dados["mencoes"])), (2, 3))
        texto = json.dumps(exportado, ensure_ascii=False)
        destino = Banco.novo()
        self.addCleanup(destino.fechar)
        destino.criar_registro("Um registro de antes.", REF, [], [], [])          # os ids mudam
        r = destino.importar_json(texto)
        self.assertEqual((r["recursos"]["lugares"]["novos"], r["recursos"]["lugares"]["lugares_novos"]), (3, 2))
        depois = self.por_nome(lugares.painel(destino, REF)["quer_conhecer"] + lugares.painel(destino, REF)["fomos"])
        self.assertEqual((depois["Mangiare"]["status"], depois["Mangiare"]["opiniao"]), ("fomos", 1))
        self.assertEqual(depois["Beco do Batman"]["tipo"], "cultura")                # a correção veio junto
        r = destino.importar_json(texto)
        self.assertEqual((r["registros_novos"], r["recursos"]["lugares"]["novos"]), (0, 0))
        self.assertEqual(len(destino.consultar("SELECT * FROM lugares_mencoes")), 3)

    def test_importar_ignora_o_que_nao_serve(self):
        self.registrar("Ela quer conhecer o Beco do Batman.")
        exportado = self.pedir("GET", "/api/exportar").dados
        dados = exportado["recursos"]["lugares"]
        m = dados["mencoes"][0]
        dados["mencoes"] += [dict(m, trecho="Ela quer conhecer Paris."), dict(m, registro_id=999), "nem é objeto",
                             dict(m, mencao="sonho", trecho="Ela quer conhecer")]
        destino = Banco.novo()
        self.addCleanup(destino.fechar)
        r = destino.importar_json(json.dumps(exportado, ensure_ascii=False))
        self.assertEqual((r["recursos"]["lugares"]["novos"], r["recursos"]["lugares"]["ignorados"]), (1, 4))
        self.assertEqual([l["nome"] for l in destino.consultar("SELECT nome FROM lugares")], ["Beco do Batman"])

    def test_apagar_o_registro_apaga_o_que_veio_dele(self):
        a = self.registrar("Ela quer conhecer o restaurante Mangiare.", "2026-08-01")
        b = self.registrar("Jantamos no Mangiare ontem e ela adorou.", "2026-09-20")
        c = self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-21")
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{b}").status, 200)
        mangiare = self.por_nome(self.linhas())["Mangiare"]
        self.assertEqual((mangiare["status"], mangiare["opiniao"], json.loads(mangiare["registro_ids"])),
                         ("queremos_ir", None, [a]))
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{c}").status, 200)
        self.assertEqual([l["nome"] for l in self.linhas()], ["Mangiare"])
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{a}").status, 200)
        self.assertEqual((self.linhas(), self.linhas("lugares_mencoes")), ([], []))
        exportado = json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False)
        self.assertNotIn("Mangiare", exportado)                               # apagar é apagar: nem no export
        self.assertNotIn("Batman", exportado)

    def test_nome_que_so_estava_no_registro_apagado_sai(self):
        a = self.registrar("Ela quer conhecer o restaurante Mangiare.", "2026-08-01")
        self.registrar("Jantamos no Mangiare Trattoria ontem e ela amou.", "2026-09-20")
        [l] = self.linhas()
        self.assertEqual(l["nome"], "Mangiare")                               # o primeiro nome escrito
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{a}").status, 200)
        [l] = self.linhas()
        self.assertEqual((l["nome"], l["quem_quer"]), ("Mangiare Trattoria", None))

    def test_apagar_o_registro_esquece_a_chave_do_cartao(self):
        rid = self.registrar("Ela quer conhecer o Beco do Batman.", "2026-09-01")
        self.hoje = QUINTA                                              # o cartão só aparece na quinta e na sexta
        chave = self.cartoes()[0]["chave"]
        self.pedir("POST", "/api/alertas/dispensar", {"chave": chave})
        self.assertIn(chave, self.banco.alertas_dispensados())
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertNotIn(chave, self.banco.alertas_dispensados())

    def test_lugar_sem_registro_nao_aparece_nem_sai(self):
        self.registrar("Ela quer conhecer o Beco do Batman.")
        # Como se o recurso não tivesse aberto quando o registro foi apagado: a menção sai, o lugar fica.
        self.banco.executar("DELETE FROM lugares_mencoes")
        self.assertEqual(len(self.linhas()), 1)
        self.assertTrue(self.painel()["vazio"])
        self.assertEqual(self.cartoes(), [])
        self.assertEqual(self.pedir("GET", "/api/exportar").dados["recursos"]["lugares"]["lugares"], [])
        self.registrar("Ela quer conhecer o Mercadão.")                       # a próxima escrita limpa
        self.assertEqual([l["nome"] for l in self.linhas()], ["Mercadão"])


# ======================================================================== privacidade

class CadeiaFalsa:
    """No lugar da IA: anota o que receberia (nada sai do computador)."""

    def __init__(self):
        self.analises: list = []
        self.contextos: list = []

    def analisar(self, texto, referencia, conhecimento, base):
        self.analises.append(copy.deepcopy(base))
        return base, "principal", []

    def responder(self, pergunta, trechos):
        return Resposta(pergunta=pergunta, texto="Achei.", encontrados=list(trechos)), "principal", []

    def sugerir(self, contexto, referencia):
        self.contextos.append(copy.deepcopy(contexto))
        return [], "principal", []


class TestPrivacidade(BaseNucleo):
    def test_a_ia_nunca_recebe_o_que_o_recurso_achou(self):
        self.banco.salvar_config(ia_principal=IA)
        cadeia = CadeiaFalsa()
        self.enterContext(mock.patch.object(motor, "obter_cadeia_ia", lambda ajustes: cadeia))
        r = self.pedir("POST", "/api/ia/analisar", {"texto": "Ela quer conhecer o Beco do Batman.", "data_registro": DATA})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(cadeia.analises[-1].extras, {})                         # a IA não viu
        self.assertEqual([e["nome"] for e in r.dados["proposta"]["extras"]["lugares"]], ["Beco do Batman"])

    def test_lugar_nao_deixa_o_registro_fora_da_ia_sozinho(self):
        rid = self.registrar("Fomos no Coco Bambu ontem e ela amou o camarão.")
        self.assertEqual(nucleo.fora_da_ia(self.banco), set())
        self.assertFalse(hasattr(lugares, "fora_da_ia"))
        self.assertFalse(hasattr(lugares, "marca_privado"))
        self.assertFalse(self.pedir("GET", f"/api/registros/{rid}").dados["privado"])

    def test_erro_ao_gravar_nao_guarda_texto_no_log(self):
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        real = lugares._gravar_mencao

        def explodir(banco, m, generico, criado):
            real(banco, m, generico, criado)
            raise RuntimeError("Mariana jantou no Coco Bambu")

        with mock.patch.object(lugares, "_gravar_mencao", explodir):
            r = self.pedir("POST", "/api/registros", self.corpo(self.analisar("Fomos no Coco Bambu ontem.")))
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["avisos"], [recursos.AVISO_GRAVAR.format(rotulo="Lugares")])
        self.assertEqual(self.linhas(), [])                                   # a parte do recurso foi desfeita
        texto_log = "\n".join(coletor.mensagens)
        self.assertIn("RuntimeError", texto_log)
        self.assertNotIn("Mariana", texto_log)
        self.assertNotIn("Coco", texto_log)

    def test_texto_e_possibilidade_sem_ordem_nem_diagnostico(self):
        self.banco.salvar_config(inicio_namoro="2025-10-20")
        self.registrar("Ela é vegetariana.", "2026-09-01")
        self.registrar("Ela quer conhecer a churrascaria Fogo de Chão.", "2026-09-02")
        self.registrar("Jantamos no Mangiare e ela amou.", "2026-06-01")
        self.registrar("Ela está muito cansada.", "2026-09-25")
        tudo = json.dumps([self.painel(), self.cartoes()], ensure_ascii=False).lower()
        for proibido in ("você deve", "tem que", "precisa levar", "leve ela", "diagnóstico", "depressão", "tpm",
                         "nota", "placar", "ranking"):
            self.assertNotIn(proibido, tudo)
        for c in self.cartoes():
            self.assertTrue(c["texto"].startswith("Talvez"))


# ======================================================================== contrato com a fundação

class TestContrato(unittest.TestCase):
    def test_o_modulo_cumpre_o_contrato(self):
        with recursos.usando(lugares):
            self.assertEqual(recursos.nomes_carregados(), ["lugares"])
            self.assertEqual(recursos.falhas(), {})
            self.assertEqual(recursos.tipos_extras(lugares), frozenset({"lugar"}))
            self.assertEqual(recursos.rotulo("lugares"), "Lugares")
            b = Banco.novo()
            self.addCleanup(b.fechar)
            versao = b._con.execute("SELECT versao FROM recursos_esquema WHERE nome = 'lugares'").fetchone()[0]
            self.assertEqual(versao, lugares.ESQUEMA_VERSAO)
            colunas = {l[1] for l in b._con.execute("PRAGMA table_info(lugares)")}
            self.assertLessEqual({"id", "nome", "tipo", "status", "opiniao", "cidade", "registro_ids", "criado_em"},
                                 colunas)                                     # as colunas da seção 20.5
            b.conferir_integridade()
            self.assertEqual(b.exportar()["recursos"]["lugares"], {"versao": 1, "lugares": [], "mencoes": []})

    def test_painel_sai_em_json_e_sem_cartao(self):
        with recursos.usando(lugares):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            json.dumps(para_json(lugares.painel(b, REF)), ensure_ascii=False)
            self.assertEqual(lugares.cartoes_hoje(b, REF), [])

    def test_status_com_check_no_banco(self):
        with recursos.usando(lugares):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            with self.assertRaises(Exception):
                b.inserir("lugares", {"nome": "X", "status": "talvez", "criado_em": REF, "chave": "x"})


if __name__ == "__main__":
    unittest.main()
