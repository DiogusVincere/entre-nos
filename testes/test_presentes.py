"""Ideias de presentes (DESIGN.md, seção 20.2).

Só este recurso entra na lista (`recursos.usando(presentes)`): os outros estão sendo escritos ao
mesmo tempo. Cobre a extração por regras (frases de conversa, sem acento, datas relativas,
negação, ironia, pergunta, o que NÃO é presente), a gravação conferida (a certeza nunca sobe), a
tela (desejos, ideias 🔵 com o porquê, recebidos, não recomendados, momento dela), as ações
(dei, esconder, reação, apagar e as cópias), o cartão de ocasião do Hoje, exportar/importar/apagar,
os limites de privacidade (nada vai para a IA, o log não guarda texto) e o "nunca inventar".

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026 (quarta), scrypt pequeno.
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
from entrenos.recursos import presentes
from entrenos.texto import normalizar
from testes.test_servidor import PIN, REF, Relogio

DATA = REF.isoformat()
TIPOS = ("presente", "presente_desejo")
IA = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": "modelo-de-teste", "tipo": "ollama",
      "chave": None}
_PADRAO = object()

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-presentes-env-")
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
    with recursos.usando(presentes):
        proposta = motor.MotorRegras().analisar(texto, referencia, Conhecimento(pessoas=[], nome_dela=nome_dela))
    return proposta.extras.get("presentes", [])


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


# ======================================================================== extração: presentes

class TestExtrairPresentes(unittest.TestCase):
    def um(self, texto: str, **kw) -> dict:
        achados = [e for e in extras(texto, **kw) if e["tipo"] == "presente"]
        self.assertEqual(len(achados), 1, achados)
        e = achados[0]
        self.assertIn(e["trecho"], texto)                          # nunca inventar: trecho literal
        return e

    def test_dei_um_colar_e_ela_amou(self):
        e = self.um("Dei um colar pra ela e ela amou.")
        self.assertEqual((e["descricao"], e["de_quem"], e["reacao"], e["planejado"]), ("colar", "eu", "gostou", False))
        self.assertEqual(e["trecho"], "Dei um colar pra ela e ela amou.")
        self.assertEqual(e["certeza"], "observado")
        self.assertEqual(e["resumo"], "Você deu a ela: colar — gostou")

    def test_reacao_na_frase_seguinte_entra_no_trecho(self):
        e = self.um("Dei um colar pra ela. Ela amou!")
        self.assertEqual(e["reacao"], "gostou")
        self.assertEqual(e["trecho"], "Dei um colar pra ela. Ela amou!")

    def test_ganhou_da_mae_dela_e_nao_curtiu(self):
        e = self.um("Ela ganhou um perfume da mãe dela e não curtiu muito.")
        self.assertEqual((e["descricao"], e["de_quem"], e["quem"], e["reacao"]), ("perfume", "outro", "a mãe dela", "nao_gostou"))
        self.assertEqual(e["resumo"], "Ela ganhou da mãe dela: perfume — não gostou")

    def test_frases_da_revisao_de_07_10(self):
        e = self.um("Presenteei ela com um livro.")
        self.assertEqual((e["descricao"], e["de_quem"]), ("livro", "eu"))
        e = self.um("Ela ganhou um livro de presente da Mariana.")
        self.assertEqual((e["descricao"], e["quem"]), ("livro", "a Mariana"))
        e = self.um("Ela ganhou flores minhas, mas achou exagerado.")
        self.assertEqual((e["descricao"], e["de_quem"], e["reacao"]), ("flores", "eu", "nao_gostou"))
        e = self.um("Dei um ingresso pro show do Coldplay pra ela.")
        self.assertEqual(e["descricao"], "ingresso pro show do Coldplay")
        # Marca depois do objeto continua no objeto ("perfume da Natura" não é quem deu).
        self.assertEqual(self.um("Ela ganhou um perfume da Natura e adorou.")["quem"], None)

    def test_ganhou_de_presente_da_mae_em_outra_ordem(self):
        e = self.um("Ela ganhou de presente da mãe dela uma bolsa e adorou")
        self.assertEqual((e["descricao"], e["quem"], e["reacao"]), ("bolsa", "a mãe dela", "gostou"))

    def test_amou_o_livro_que_eu_dei_de_aniversario(self):
        e = self.um("Ela amou o livro que eu dei de aniversário.")
        self.assertEqual((e["descricao"], e["de_quem"], e["reacao"], e["ocasiao"]), ("livro", "eu", "gostou", "aniversario_dela"))

    def test_nao_curtiu_o_vinho_que_eu_dei(self):
        e = self.um("Ela não curtiu o vinho que eu dei.")
        self.assertEqual((e["descricao"], e["reacao"]), ("vinho", "nao_gostou"))

    def test_amiga_deu_um_caderno(self):
        e = self.um("A Mariana deu um caderno pra ela.")
        self.assertEqual((e["descricao"], e["de_quem"], e["quem"], e["reacao"]), ("caderno", "outro", "a Mariana", "nao_sei"))

    def test_irma_deu_ontem(self):
        e = self.um("Ontem a irmã dela deu um quadro pra ela.")
        self.assertEqual((e["descricao"], e["quem"], e["data"]), ("quadro", "a irmã dela", "2026-09-29"))
        self.assertEqual(e["resumo"], "Ela ganhou da irmã dela: quadro")

    def test_ganhou_do_chefe_guarda_o_artigo(self):
        e = self.um("Ela ganhou um vinho do chefe e não bebeu ainda.")
        self.assertEqual((e["quem"], e["reacao"]), ("o chefe", "nao_sei"))
        self.assertEqual(presentes.de_quem_texto(e["quem"]), "do chefe")

    def test_comprei_pra_dar_no_aniversario_e_separado(self):
        e = self.um("Comprei um anel pra dar no aniversário dela.")
        self.assertEqual((e["descricao"], e["planejado"], e["ocasiao"], e["reacao"]), ("anel", True, "aniversario_dela", "nao_sei"))

    def test_vou_dar_no_natal_pega_o_natal_que_vem(self):
        e = self.um("Vou dar um perfume pra ela no natal.")
        self.assertEqual((e["planejado"], e["ocasiao"], e["data"]), (True, "natal", "2026-12-25"))

    def test_ganhou_no_natal_e_o_natal_passado(self):
        e = self.um("Ela ganhou um ursinho de pelúcia no natal e ficou toda boba.")
        self.assertEqual((e["descricao"], e["data"], e["reacao"]), ("ursinho de pelúcia", "2025-12-25", "gostou"))

    def test_dei_ontem_a_data_vem_do_python(self):
        e = self.um("Dei flores pra ela ontem, ela ficou super feliz.")
        self.assertEqual((e["descricao"], e["data"], e["data_expressao"], e["reacao"]), ("flores", "2026-09-29", "ontem", "gostou"))

    def test_sem_acento_com_apelido_dela(self):
        e = self.um("dei um buque de rosas pra ju sabado passado e ela chorou de emocao", nome_dela="Julia")
        self.assertEqual((e["descricao"], e["data"], e["reacao"]), ("buque de rosas", "2026-09-26", "gostou"))

    def test_dei_de_presente_pra_ela_em_qualquer_ordem(self):
        self.assertEqual(self.um("Dei de presente pra ela um caderno de receitas.")["descricao"], "caderno de receitas")

    def test_meu_presente_foi_e_vai_ser(self):
        e = self.um("Meu presente pra ela foi um kit de skincare e ela amou.")
        self.assertEqual((e["descricao"], e["reacao"], e["planejado"]), ("kit de skincare", "gostou", False))
        f = self.um("O meu presente de aniversário pra ela vai ser uma viagem pra praia.")
        self.assertEqual((f["descricao"], f["planejado"], f["ocasiao"]), ("viagem", True, "aniversario_dela"))

    def test_a_gente_deu_um_bichinho(self):
        e = self.um("A gente deu um cachorrinho pra ela no natal e ela pulou de alegria.")
        self.assertEqual((e["descricao"], e["de_quem"], e["data"], e["reacao"]), ("cachorrinho", "eu", "2025-12-25", "gostou"))

    def test_reacao_neutra_e_negativa(self):
        self.assertEqual(self.um("Ela achou ok o perfume que eu dei.")["reacao"], "neutra")
        self.assertEqual(self.um("Ela ganhou da tia dela um vestido mas achou meio brega")["reacao"], "nao_gostou")
        e = self.um("Dei um perfume pra ela no dia dos namorados mas ela achou exagerado.")
        self.assertEqual((e["reacao"], e["ocasiao"], e["data"]), ("nao_gostou", "dia_dos_namorados", "2026-06-12"))

    def test_ocasiao_de_namoro(self):
        e = self.um("Fiz um álbum de fotos pra ela de 1 ano de namoro e ela se emocionou.")
        self.assertEqual((e["descricao"], e["ocasiao"], e["reacao"]), ("álbum de fotos", "namoro", "gostou"))

    def test_ganhou_de_mim(self):
        e = self.um("Ganhou de mim um livro da Clarice e adorou.")
        self.assertEqual((e["descricao"], e["de_quem"], e["quem"]), ("livro da Clarice", "eu", None))

    def test_lista_de_presentes_numa_frase(self):
        achados = [e for e in extras("Dei um colar e um brinco pra ela de aniversário e ela amou.") if e["tipo"] == "presente"]
        self.assertEqual([(e["descricao"], e["reacao"], e["ocasiao"]) for e in achados],
                         [("colar", "gostou", "aniversario_dela"), ("brinco", "gostou", "aniversario_dela")])

    def test_desejo_realizado_na_mesma_frase_nao_fica_aberto(self):
        achados = extras("Dei um kindle pra ela, ela tava doida por um kindle.")
        self.assertEqual([(e["tipo"], e["descricao"]) for e in achados], [("presente", "kindle")])
        self.assertEqual([e["descricao"] for e in extras("Ela tava doida por um kindle.")], ["kindle"])

    def test_certeza_acompanha_a_frase(self):
        self.assertEqual(self.um("Acho que ela gostou do colar que eu dei.")["certeza"], "inferencia")
        self.assertEqual(self.um("Ela disse que amou o colar que eu dei.")["certeza"], "confirmado")


# ======================================================================== extração: desejos

class TestExtrairDesejos(unittest.TestCase):
    def desejo(self, texto: str) -> dict:
        achados = [e for e in extras(texto) if e["tipo"] == "presente_desejo"]
        self.assertEqual(len(achados), 1, achados)
        self.assertIn(achados[0]["trecho"], texto)
        return achados[0]

    def test_doida_por_um_kindle(self):
        e = self.desejo("ela ta doida por um kindle")
        self.assertEqual((e["descricao"], e["certeza"], e["resumo"]), ("kindle", "observado", "Ela gostaria de ganhar: kindle"))

    def test_vive_falando_e_interpretacao(self):
        e = self.desejo("Ela vive falando daquela bolsa da Zara.")
        self.assertEqual((e["descricao"], e["certeza"]), ("bolsa da Zara", "inferencia"))

    def test_precisa_de_uma_mochila_dito_por_ela(self):
        e = self.desejo("Ela comentou que precisa de uma mochila nova.")
        self.assertEqual((e["descricao"], e["certeza"]), ("mochila nova", "confirmado"))

    def test_adoraria_ganhar(self):
        self.assertEqual(self.desejo("Ela disse que adoraria ganhar uma câmera instantânea.")["descricao"], "câmera instantânea")

    def test_pediu_de_presente(self):
        self.assertEqual(self.desejo("ela pediu um fone de presente")["descricao"], "fone")

    def test_lista_de_desejos_que_tem(self):
        [d] = [e for e in extras("Na lista de desejos dela tem uma câmera instantânea.") if e["tipo"] == "presente_desejo"]
        self.assertEqual(d["descricao"], "câmera instantânea")

    def test_babando_na_vitrine_e_de_olho(self):

        e = self.desejo("Ela ficou babando num tênis da Nike na vitrine.")
        self.assertEqual((e["descricao"], e["certeza"]), ("tênis da Nike", "inferencia"))
        self.assertEqual(self.desejo("Ela tá de olho numa air fryer.")["descricao"], "air fryer")
        self.assertEqual(self.desejo("Ela colocou uma luminária na lista de desejos.")["descricao"], "luminária")

    def test_o_que_o_nucleo_ja_pega_nao_duplica(self):
        # "queria um fone novo" o núcleo guarda como DESEJO (Presentes); a tela lê o item dele.
        with recursos.usando(presentes):
            p = motor.MotorRegras().analisar("Ela queria um fone novo.", REF)
        self.assertEqual(p.extras, {})
        self.assertTrue(any(i.tipo.value == "desejo" and "Presentes" in i.categorias for i in p.itens))


# ======================================================================== extração: o que NÃO é

class TestNaoInventa(unittest.TestCase):
    def nada(self, *textos: str, nome_dela: str | None = None) -> None:
        for texto in textos:
            with self.subTest(texto=texto):
                self.assertEqual(extras(texto, nome_dela=nome_dela), [])

    def test_dar_que_nao_e_presente(self):
        self.nada("Dei uma volta com ela no parque.", "Dei risada da piada dela.", "Dei carona pra ela até o trabalho.",
                  "Dei um tempo pra ela pensar.", "Dei um beijo nela.", "dei um bolo nela ontem, vacilei",
                  "Dei uma força pra ela no TCC.", "Comprei um remédio pra ela.")

    def test_gesto_do_dia_a_dia_nao_e_presente(self):
        # Correção final de 08/10 (20.2): levar café para ela é um gesto (atitude), não um presente. Sem
        # verbo de dar/presentear/ganhar e sem marca de presente, só um objeto claramente de presente vale.
        self.nada("Levei café para ela no trabalho.", "Levei café para ela de manhã.", "Fiz um bolo pra ela.",
                  "Comprei pão pra ela.", "Levei um lanche pra ela no trabalho.", "Trouxe água pra ela.",
                  "Preparei um chá pra ela quando ela chegou cansada.")

    def test_gesto_com_objeto_ou_marca_de_presente_vale(self):
        self.assertEqual(self.um_presente("Levei flores para ela.")["descricao"], "flores")
        self.assertEqual(self.um_presente("Levei um café de presente pra ela.")["descricao"], "café")
        self.assertEqual(self.um_presente("Trouxe um chocolate pra ela.")["descricao"], "chocolate")
        self.assertEqual(self.um_presente("Dei café pra ela.")["descricao"], "café")          # verbo de dar

    def um_presente(self, texto: str) -> dict:
        achados = [e for e in extras(texto) if e["tipo"] == "presente"]
        self.assertEqual(len(achados), 1, (texto, achados))
        return achados[0]

    def test_ganhar_que_nao_e_presente(self):
        self.nada("Ela ganhou o jogo de tabuleiro ontem.", "Ela ganhou uma promoção no trabalho!",
                  "Ela ganhou peso nas férias.", "Ela ganhou um sobrinho!", "Ela ganhou um celular num sorteio.")

    def test_presente_para_outra_pessoa_ou_de_outra_direcao(self):
        self.nada("Dei um presente pra mãe dela.", "Dei um livro pra Mariana.", "Ela deu um presente pra irmã dela.",
                  "Ela me deu um relógio lindo.", "Comprei flores pra minha mãe.")

    def test_negacao_hipotese_pergunta_e_ironia(self):
        self.nada("Não dei nada pra ela no aniversário.", "Nunca dei flores pra ela.",
                  "Se eu der um perfume pra ela, será que ela gosta?", "Será que ela queria um fone novo?",
                  "Dei um colar pra ela e ela amou, sqn")

    def test_fala_negada_e_duvida(self):
        self.nada("Ela não disse que queria um fone.", "Não sei se ela tá doida por um kindle.")

    def test_precisar_de_ajuda_nao_e_desejo_de_presente(self):
        self.nada("Ela precisa de um tempo pra pensar.", "Ela precisa de uma ajuda com a mudança.",
                  "Ela tá precisando de um abraço.")

    def test_desejo_de_outra_pessoa_nao_e_dela(self):
        self.nada("A mãe dela precisa de um andador novo.", "Eu tô doido por um videogame novo.")

    def test_preferencia_solta_nao_vira_presente(self):
        self.nada("Ela adora tulipas.", "A mãe dela, Sandra, vai fazer uma cirurgia em novembro. Ela disse que adora tulipas.")

    def test_contrato_dos_extras_num_texto_longo(self):
        texto = ("Ontem foi um dia corrido. Dei um colar pra ela e ela amou. A Mariana deu um caderno pra ela. "
                 "Ela tá de olho numa air fryer. Ela não gosta de perfume doce. Vou dar um livro pra ela no natal.")
        achados = extras(texto)
        self.assertEqual(sorted(e["descricao"] for e in achados), ["air fryer", "caderno", "colar", "livro"])
        for e in achados:
            with self.subTest(extra=e["descricao"]):
                self.assertIn(e["tipo"], presentes.TIPOS_EXTRAS)
                self.assertIn(e["trecho"], texto)
                self.assertTrue(e["resumo"])
                self.assertNotIn("_privado", e)                 # presente não deixa o registro fora da IA
                json.dumps(e, ensure_ascii=False)

    def test_extrair_direto_aceita_entradas_vazias(self):
        self.assertEqual(presentes.extrair("", REF, [], [], [], None), [])
        r = presentes.extrair("Dei um colar pra ela.", REF, [], [], [], None)
        self.assertEqual([e["descricao"] for e in r], ["colar"])


# ======================================================================== base do núcleo

class BaseNucleo(unittest.TestCase):
    config: dict = {}

    def setUp(self):
        self.enterContext(recursos.usando(presentes))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-presentes-")
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

    def corpo(self, proposta: dict, data: str = DATA, confirmar=TIPOS, mexer=None, **mais) -> dict:
        lista = [copy.deepcopy(e) for e in proposta["extras"].get("presentes", []) if e["tipo"] in confirmar]
        for e in lista:
            if mexer:
                e.update(mexer)
        return {"texto": proposta["texto"], "data_registro": data, "itens": proposta["itens"],
                "pessoas": proposta["pessoas"], "relacoes": proposta["relacoes"],
                "extras": {"presentes": lista} if lista else {}, **mais}

    def registrar(self, texto: str, data: str = DATA, confirmar=TIPOS, mexer=None) -> int:
        r = self.pedir("POST", "/api/registros", self.corpo(self.analisar(texto, data), data, confirmar, mexer))
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def painel(self, data: str | None = None) -> dict:
        r = self.pedir("GET", "/api/presentes", consulta={"data": data} if data else None)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(json.loads(r.corpo.decode("utf-8")), r.dados)
        return r.dados

    def cartoes(self, data: str | None = None) -> list[dict]:
        r = self.pedir("GET", "/api/hoje", consulta={"data": data} if data else None)
        self.assertEqual(r.status, 200, r.dados)
        return [a for a in r.dados["alertas"] if a.get("recurso") == "presentes"]

    def linhas(self, tabela: str = "presentes") -> list[dict]:
        return self.banco.consultar(f"SELECT * FROM {tabela} ORDER BY id")


# ======================================================================== gravar

class TestGravar(BaseNucleo):
    def test_confirmados_sao_gravados_com_o_registro(self):
        rid = self.registrar("Dei um colar pra ela e ela amou. Ela tá de olho numa air fryer.")
        [p] = self.linhas()
        self.assertEqual((p["descricao"], p["de_quem"], p["reacao"], p["registro_id"], p["planejado"]),
                         ("colar", "eu", "gostou", rid, 0))
        self.assertEqual(p["trecho"], "Dei um colar pra ela e ela amou.")
        [d] = self.linhas("presentes_desejos")
        self.assertEqual((d["descricao"], d["registro_id"], d["pista"]), ("air fryer", rid, "de_olho"))
        self.assertFalse(self.pedir("GET", f"/api/registros/{rid}").dados["privado"])     # presente não é 🔒

    def test_o_que_nao_foi_confirmado_nao_e_gravado(self):
        self.registrar("Dei um colar pra ela e ela amou.", confirmar=())
        self.assertEqual(self.linhas(), [])

    def test_certeza_nunca_sobe(self):
        self.registrar("Dei um colar pra ela e ela amou.", mexer={"certeza": "confirmado"})
        self.assertEqual(self.linhas()[0]["certeza"], "observado")

    def test_correcao_do_usuario_vale(self):
        self.registrar("Dei um colar pra ela e ela amou.", mexer={"descricao": "colar de prata", "ocasiao": "namoro"})
        p = self.linhas()[0]
        self.assertEqual((p["descricao"], p["ocasiao"]), ("colar de prata", "namoro"))

    def test_separado_nao_tem_reacao(self):
        self.registrar("Comprei um anel pra dar no aniversário dela.", mexer={"reacao": "gostou"})
        p = self.linhas()[0]
        self.assertEqual((p["planejado"], p["reacao"]), (1, "nao_sei"))

    def test_campos_que_nao_servem_400_e_nada_gravado(self):
        p = self.analisar("Dei um colar pra ela e ela amou.")
        casos = {"reação": {"reacao": "amou demais"}, "ocasião": {"ocasiao": "carnaval"}, "descrição": {"descricao": " "},
                 "data": {"data": "30/09/2026"}, "de quem": {"de_quem": "ela"}, "planejado": {"planejado": "sim"},
                 "descrição longa": {"descricao": "x" * 121}}
        for nome, mexer in casos.items():
            with self.subTest(caso=nome):
                r = self.pedir("POST", "/api/registros", self.corpo(p, mexer=mexer))
                self.assertEqual(r.status, 400, r.dados)
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])
        self.assertEqual(self.linhas(), [])

    def test_trecho_inventado_400(self):
        p = self.analisar("Dei um colar pra ela e ela amou.")
        r = self.pedir("POST", "/api/registros", self.corpo(p, mexer={"trecho": "Dei um carro pra ela."}))
        self.assertEqual((r.status, r.dados["erro"]), (400, recursos.EXTRA_SEM_TRECHO))
        self.assertEqual(self.linhas(), [])


# ======================================================================== a tela

class TestPainel(BaseNucleo):
    def test_vazio_nao_inventa_nada(self):
        p = self.painel()
        self.assertTrue(p["vazio"])
        for chave in ("desejos", "ideias", "separados", "gostou", "nao_gostou", "nao_recomendados", "ocultos"):
            self.assertEqual(p[chave], [], chave)
        self.assertIsNone(p["momento"])

    def test_desejo_do_nucleo_e_do_recurso_com_ha_quanto_tempo(self):
        self.registrar("Ela queria um fone novo.", "2026-02-10")
        self.registrar("ela ta doida por um kindle", "2026-09-20")
        d = {x["descricao"]: x for x in self.painel()["desejos"]}
        self.assertEqual(set(d), {"fone novo", "kindle"})
        self.assertEqual((d["fone novo"]["origem"], d["fone novo"]["quando"]), ("item", "há 8 meses"))
        self.assertEqual((d["kindle"]["origem"], d["kindle"]["quando"]), ("desejo", "há 10 dias"))
        self.assertEqual(d["kindle"]["trecho"], "ela ta doida por um kindle")

    def test_o_mesmo_desejo_em_dois_registros_vira_um(self):
        self.registrar("Ela queria um fone novo.", "2026-03-01")
        self.registrar("Ela tá de olho num fone novo.", "2026-09-01")
        [d] = self.painel()["desejos"]
        self.assertEqual((d["vezes"], len(d["origens"])), (2, 2))
        self.assertEqual(d["data_registro"], "2026-09-01")                 # o mais recente representa

    def test_desejo_de_outra_pessoa_nao_entra(self):
        self.registrar("A Mariana queria um fone novo.")
        self.assertEqual(self.painel()["desejos"], [])

    def test_ideia_so_de_preferencia_forte(self):
        self.registrar("Ela adora tulipas.", "2026-09-01")                     # uma vez, observado: ainda não
        self.assertEqual(self.painel()["ideias"], [])
        self.registrar("Ela disse que adora tulipas.", "2026-09-20")
        [i] = self.painel()["ideias"]
        self.assertEqual((i["texto"], i["frase"], i["certeza"]), ("tulipas", "Talvez algo com tulipas", "inferencia"))
        self.assertIn("ela mesma disse que gosta 🟢", i["motivos"])
        self.assertIn("apareceu em 2 registros", i["motivos"])
        self.assertEqual({o["certeza"] for o in i["origens"]}, {"confirmado", "observado"})

    def test_duas_observacoes_tambem_contam(self):
        self.registrar("Ela adora chocolate amargo.", "2026-08-01")
        self.registrar("Ela pediu chocolate amargo de novo, ela ama chocolate amargo.", "2026-09-10")
        self.assertEqual([i["texto"] for i in self.painel()["ideias"]], ["chocolate amargo"])

    def test_ideia_que_contradiz_o_que_ela_disse_sai(self):
        # Revisão de 07/10: "não gosta de flores de presente" deixava "Talvez algo com girassóis"; e o
        # motivo "já apareceu ligado a presentes" saía do dicionário de palavras, sem registro nenhum.
        self.registrar("Ela disse que ama girassóis.", "2026-09-01")
        self.registrar("Ela ama girassóis, sorriu quando viu.", "2026-09-05")
        self.registrar("Ela disse que ama chocolate amargo.", "2026-09-06")
        self.registrar("Ela ama vinho tinto.", "2026-09-06")
        self.registrar("Ela adora vinho tinto.", "2026-09-07")
        ideias = {i["texto"]: i for i in self.painel()["ideias"]}
        self.assertEqual(set(ideias), {"girassóis", "chocolate amargo", "vinho tinto"})
        self.assertNotIn("já apareceu ligado a presentes", ideias["girassóis"]["motivos"])
        self.registrar("Ela disse que não gosta de flores de presente.", "2026-09-10")
        self.registrar("Ela disse que parou de beber vinho, não gosta mais.", "2026-09-11")
        self.assertEqual([i["texto"] for i in self.painel()["ideias"]], ["chocolate amargo"])

    def test_motivo_de_presente_so_com_registro_de_presente(self):
        self.registrar("Ela disse que ama girassóis.", "2026-09-01")
        self.registrar("Ela ganhou girassóis no aniversário e amou, ela ama girassóis.", "2026-09-05")
        [i] = [i for i in self.painel()["ideias"] if i["texto"] == "girassóis"]
        self.assertIn("já apareceu ligado a presentes", i["motivos"])

    def test_inferencia_nao_vira_ideia(self):
        self.registrar("Acho que ela gosta de tulipas.", "2026-09-01")
        self.registrar("Talvez ela goste de tulipas.", "2026-09-10")
        self.assertEqual(self.painel()["ideias"], [])

    def test_hobby_registrado_uma_vez_vira_ideia_com_verbo(self):
        self.registrar("Ela adora fazer crochê.", "2026-09-10")
        [i] = self.painel()["ideias"]
        self.assertEqual(i["frase"], "Talvez algo para fazer crochê")
        self.assertIn("é um hobby dela", i["motivos"])

    def test_o_que_nao_e_presenteavel_nao_vira_ideia(self):
        self.registrar("Ela disse que gosta de acordar cedo.", "2026-09-10")
        self.assertEqual(self.painel()["ideias"], [])

    def test_nome_proprio_que_ela_ama(self):
        self.registrar("Ela disse que ama Harry Potter.", "2026-09-10")
        [i] = self.painel()["ideias"]
        self.assertEqual(i["texto"], "Harry Potter")
        self.assertIn("é um nome (obra, artista ou marca) de que ela gosta", i["motivos"])

    def test_contradicao_e_mencao_antiga_ficam_de_fora(self):
        self.registrar("Ela disse que ama girassóis.", "2026-09-01")
        self.registrar("Ela não gosta de girassóis.", "2026-09-20")
        self.registrar("Ela disse que ama vinho tinto.", "2025-06-01")            # mais de 12 meses
        self.assertEqual(self.painel()["ideias"], [])

    def test_pontuacao_explicavel_ordena(self):
        self.registrar("Ela disse que adora tulipas.", "2026-09-25")
        self.registrar("Ela ficou feliz quando ganhou tulipas.", "2026-09-26")
        self.registrar("Ela disse que ama café.", "2026-04-01")
        ideias = self.painel()["ideias"]
        self.assertEqual([i["texto"] for i in ideias], ["tulipas", "café"])
        self.assertGreater(ideias[0]["pontos"], ideias[1]["pontos"])
        self.assertTrue(all(i["motivos"] for i in ideias))

    def test_restricao_vira_aviso_na_ideia_de_comida(self):
        self.registrar("Ela é intolerante a lactose. Ela disse que ama chocolate amargo.")
        [i] = self.painel()["ideias"]
        self.assertEqual(i["avisos"], ["Atenção ao que você registrou em 30/09: “Ela é intolerante a lactose.”"])

    def test_nao_recomendados_com_os_motivos(self):
        self.registrar("Ela é alérgica a camarão. Ela não gosta de perfume doce. Ela não quer mais ganhar flores.", "2026-09-01")
        self.registrar("Ela ganhou um vinho da tia dela e não curtiu muito.", "2026-09-10")
        self.registrar("Dei um colar pra ela e ela amou.", "2026-09-20")
        self.registrar("A Mariana deu um caderno pra ela.", "2026-09-25")
        nr = {(n["tipo"], n["texto"]) for n in self.painel()["nao_recomendados"]}
        self.assertEqual(nr, {("restricao", "Alérgica a camarão"), ("nao_gosta", "perfume doce"), ("nao_quer", "flores"),
                              ("nao_gostou", "vinho"), ("ja_dado", "colar"), ("ja_ganhou", "caderno")})
        for n in self.painel()["nao_recomendados"]:
            self.assertTrue(n["trecho"] and n["registro_id"])                  # cada um cita a origem

    def test_presente_dado_ha_pouco_sai_das_ideias(self):
        self.registrar("Ela disse que adora tulipas.", "2026-08-01")
        self.registrar("Dei tulipas pra ela e ela amou.", "2026-09-20")
        self.assertEqual(self.painel()["ideias"], [])
        self.assertIn(("ja_dado", "tulipas"), {(n["tipo"], n["texto"]) for n in self.painel()["nao_recomendados"]})

    def test_listas_de_recebidos_separados_e_reacoes(self):
        self.registrar("Dei um colar pra ela e ela amou.", "2026-09-20")
        self.registrar("Ela ganhou um perfume da mãe dela e não curtiu muito.", "2026-09-21")
        self.registrar("Comprei um anel pra dar no aniversário dela.", "2026-09-22")
        p = self.painel()
        self.assertEqual([x["descricao"] for x in p["recebidos"]["de_voce"]], ["colar"])
        self.assertEqual([(x["descricao"], x["quem_texto"]) for x in p["recebidos"]["de_outros"]], [("perfume", "da mãe dela")])
        self.assertEqual([x["descricao"] for x in p["separados"]], ["anel"])
        self.assertEqual([x["descricao"] for x in p["gostou"]], ["colar"])
        self.assertEqual([x["descricao"] for x in p["nao_gostou"]], ["perfume"])

    def test_talvez_ja_dado(self):
        self.registrar("Ela queria um fone novo.", "2026-02-10")
        self.registrar("Dei um fone pra ela e ela amou.", "2026-09-20")
        [d] = self.painel()["desejos"]
        self.assertEqual((d["talvez_dado"]["descricao"], d["talvez_dado"]["de_quem"]), ("fone", "eu"))

    def test_momento_dela_com_o_que_ajuda(self):
        self.registrar("O que ajuda é um chá quentinho.", "2026-09-01")
        self.registrar("Ela está muito cansada.", "2026-09-28")
        m = self.painel()["momento"]
        self.assertEqual(m["estado"]["trecho"], "Ela está muito cansada.")
        self.assertEqual([a["trecho"] for a in m["apoios"]], ["O que ajuda é um chá quentinho."])

    def test_momento_sem_o_que_ajuda_ou_ja_passado_nao_aparece(self):
        self.registrar("Ela está muito cansada.", "2026-09-28")
        self.assertIsNone(self.painel()["momento"])                         # sem apoio: nada genérico
        self.registrar("O que ajuda é um chá quentinho.", "2026-09-01")
        self.registrar("Ela está feliz hoje.", "2026-09-29")
        self.assertIsNone(self.painel()["momento"])                         # o mais recente é bom
        self.assertIsNotNone(self.painel("2026-09-28")["momento"])

    def test_nunca_inventar_tudo_cita_trechos_que_existem(self):
        for texto, data in (("Ela queria um fone novo.", "2026-02-10"), ("ela ta doida por um kindle", "2026-09-20"),
                            ("Ela disse que adora tulipas.", "2026-08-10"), ("Ela adora tulipas.", "2026-09-01"),
                            ("Dei um colar pra ela e ela amou.", "2026-09-25"), ("Ela é alérgica a camarão.", "2026-09-26")):
            self.registrar(texto, data)
        textos = {r["id"]: r["texto"] for r in self.pedir("GET", "/api/registros").dados}
        p = self.painel()
        citadas = [o for d in p["desejos"] for o in d["origens"]] + [o for i in p["ideias"] for o in i["origens"]]
        citadas += p["nao_recomendados"] + p["gostou"]
        self.assertTrue(citadas)
        for c in citadas:
            self.assertIn(c["trecho"], textos[c["registro_id"]])
        for i in p["ideias"]:
            self.assertTrue(any(normalizar(i["texto"]) in normalizar(o["trecho"]) for o in i["origens"]))
        for d in p["desejos"]:
            self.assertIn(normalizar(d["descricao"]), normalizar(d["trecho"]))


# ======================================================================== ações

class TestAcoes(BaseNucleo):
    def test_dei_um_desejo_do_nucleo(self):
        self.registrar("Ela queria um fone novo.", "2026-02-10")
        [d] = self.painel()["desejos"]
        r = self.pedir("POST", f"/api/presentes/itens/{d['id']}/dei", {"ocasiao": "aniversario_dela"})
        self.assertEqual(r.status, 200, r.dados)
        p = self.painel()
        self.assertEqual(p["desejos"], [])
        [g] = p["recebidos"]["de_voce"]
        self.assertEqual((g["descricao"], g["data"], g["ocasiao"], g["ligado_a_desejo"]), ("fone novo", DATA, "aniversario_dela", True))
        self.assertEqual(g["trecho"], "Ela queria um fone novo.")

    def test_dei_um_grupo_e_apagar_o_presente_traz_de_volta(self):
        self.registrar("Ela queria um fone novo.", "2026-03-01")
        self.registrar("Ela tá de olho num fone novo.", "2026-09-01")
        [d] = self.painel()["desejos"]
        pid = self.pedir("POST", f"/api/presentes/desejos/{d['id']}/dei", {}).dados["presente_id"]
        self.assertEqual(self.painel()["desejos"], [])                       # os dois registros do grupo
        self.assertEqual(self.pedir("DELETE", f"/api/presentes/{pid}").status, 200)
        self.assertEqual(len(self.painel()["desejos"]), 1)
        self.assertEqual(self.linhas("presentes_ocultos"), [])

    def test_e_esse_liga_um_presente_que_ja_existia(self):
        self.registrar("Ela queria um fone novo.", "2026-02-10")
        self.registrar("Dei um fone pra ela e ela amou.", "2026-09-20")
        [d] = self.painel()["desejos"]
        pid = d["talvez_dado"]["id"]
        self.assertEqual(self.pedir("POST", f"/api/presentes/itens/{d['id']}/dei", {"presente_id": pid}).status, 200)
        self.assertEqual(self.painel()["desejos"], [])
        self.assertEqual(len(self.linhas()), 1)                              # não criou outro presente
        r = self.pedir("POST", f"/api/presentes/itens/{d['id']}/dei", {"presente_id": pid})
        self.assertEqual(r.status, 400)                                       # já está ligado

    def test_dei_com_data_no_futuro_ou_campo_estranho_400(self):
        self.registrar("ela ta doida por um kindle")
        [d] = self.painel()["desejos"]
        for corpo in ({"data": "2026-10-05"}, {"data": "ontem"}, {"ocasiao": "carnaval"}, {"quem": "eu"}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.pedir("POST", f"/api/presentes/desejos/{d['id']}/dei", corpo).status, 400)
        self.assertEqual(self.pedir("POST", "/api/presentes/desejos/999/dei", {}).status, 404)
        self.assertEqual(self.linhas(), [])

    def test_esconder_e_mostrar_de_novo(self):
        self.registrar("ela ta doida por um kindle")
        [d] = self.painel()["desejos"]
        self.assertEqual(self.pedir("POST", f"/api/presentes/desejos/{d['id']}/ocultar", {}).status, 200)
        p = self.painel()
        self.assertEqual(p["desejos"], [])
        [o] = p["ocultos"]
        self.assertEqual((o["tipo"], o["texto"]), ("desejo", "kindle"))
        for id_ in o["ids"]:
            self.assertEqual(self.pedir("DELETE", f"/api/presentes/ocultos/{id_}").status, 200)
        self.assertEqual([x["descricao"] for x in self.painel()["desejos"]], ["kindle"])

    def test_esconder_uma_ideia(self):
        self.registrar("Ela disse que adora tulipas.")
        [i] = self.painel()["ideias"]
        self.assertEqual(self.pedir("POST", f"/api/presentes/ideias/{i['item_id']}/ocultar", {}).status, 200)
        p = self.painel()
        self.assertEqual(p["ideias"], [])
        self.assertEqual([(o["tipo"], o["texto"]) for o in p["ocultos"]], [("ideia", "tulipas")])
        self.assertEqual(self.pedir("POST", "/api/presentes/ideias/999/ocultar", {}).status, 404)

    def test_mudar_a_reacao_e_dar_o_separado(self):
        self.registrar("Dei um colar pra ela.", "2026-09-20")
        self.registrar("Comprei um anel pra dar no aniversário dela.", "2026-09-22")
        colar, anel = self.linhas()
        self.assertEqual(self.pedir("PATCH", f"/api/presentes/{colar['id']}", {"reacao": "gostou"}).status, 200)
        r = self.pedir("PATCH", f"/api/presentes/{anel['id']}", {"reacao": "gostou"})
        self.assertEqual(r.status, 400)                                     # ainda não foi dado
        self.assertEqual(self.pedir("PATCH", f"/api/presentes/{anel['id']}", {"planejado": False}).status, 200)
        colar, anel = self.linhas()
        self.assertEqual(colar["reacao"], "gostou")
        self.assertEqual((anel["planejado"], anel["data"]), (0, DATA))        # deu hoje
        for corpo in ({"registro_id": 1}, {"reacao": "talvez"}, {}, {"planejado": True}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.pedir("PATCH", f"/api/presentes/{colar['id']}", corpo).status, 400)
        self.assertEqual(self.pedir("PATCH", "/api/presentes/999", {"reacao": "gostou"}).status, 404)

    def test_apagar_repete_nas_copias(self):
        self.registrar("Dei um colar pra ela e ela amou.")
        pid = self.linhas()[0]["id"]
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/presentes/{pid}").status, 200)
        self.assertEqual(self.linhas(), [])
        self.assertEqual(len(repetir), 1)
        repetir[0](copia)                                  # o que o cofre faria em cada cópia
        self.assertEqual(copia.consultar("SELECT * FROM presentes"), [])
        self.assertEqual(self.pedir("DELETE", f"/api/presentes/{pid}").status, 404)

    def test_rotas_pedem_o_pin(self):
        for metodo, caminho in (("GET", "/api/presentes"), ("PATCH", "/api/presentes/1"), ("DELETE", "/api/presentes/1"),
                                ("POST", "/api/presentes/itens/1/dei")):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.pedir(metodo, caminho, {} if metodo != "GET" else None, token=None).status, 401)


# ======================================================================== Hoje

class TestHoje(BaseNucleo):
    config = {"aniversario_dela": "1998-10-30"}

    def test_aniversario_em_30_dias_com_o_que_ela_mencionou(self):
        self.registrar("Ela queria um fone novo.", "2026-02-10")
        self.registrar("ela ta doida por um kindle", "2026-09-20")
        self.registrar("Ela comentou que precisa de uma mochila nova.", "2026-09-25")
        [c] = self.cartoes()
        self.assertEqual(c["texto"], "Ideias para o aniversário dela (em 30 dias): 3 coisas que ela mencionou.")
        self.assertEqual((c["nivel"], c["chave"], c["tipo"], c["data"]),
                         ("sugestao", "presentes:ocasiao:dela:2026-10-30", "presentes_ocasiao", "2026-10-30"))
        self.assertEqual([d["descricao"] for d in c["dados"]["desejos"]], ["mochila nova", "kindle", "fone novo"])
        self.assertIn("“ela ta doida por um kindle”", c["porque"])
        self.assertEqual(sorted(c["registro_ids"]), [1, 2, 3])

    def test_sem_nada_registrado_nenhum_cartao(self):
        self.registrar("Ela adora tulipas.")                                # fraca: não é ideia
        self.assertEqual(self.cartoes(), [])

    def test_fora_dos_45_dias_nenhum_cartao(self):
        self.registrar("ela ta doida por um kindle")
        self.assertEqual(self.cartoes("2026-09-14"), [])                    # 46 dias antes
        self.assertEqual(len(self.cartoes("2026-09-15")), 1)                # 45 dias antes

    def test_ideias_e_o_que_ja_foi_separado(self):
        self.registrar("Ela disse que adora tulipas.", "2026-09-01")
        sem = self.cartoes()[0]
        self.registrar("Comprei um anel pra dar no aniversário dela.", "2026-09-29")
        [c] = self.cartoes()
        self.assertEqual(c["texto"], "Ideias para o aniversário dela (em 30 dias): 1 ideia a partir do que ela gosta. "
                                     "Você já separou: anel.")
        self.assertLess(c["relevancia"], sem["relevancia"])
        self.assertEqual(c["dados"]["separados"][0]["descricao"], "anel")

    def test_dispensado_nao_volta(self):
        self.registrar("ela ta doida por um kindle")
        chave = self.cartoes()[0]["chave"]
        self.assertEqual(self.pedir("POST", "/api/alertas/dispensar", {"chave": chave}).status, 200)
        self.assertEqual(self.cartoes(), [])

    def test_natal(self):
        self.banco.salvar_config(aniversario_dela=None)
        self.registrar("ela ta doida por um kindle")
        [c] = self.cartoes("2026-11-20")
        self.assertEqual(c["texto"], "Ideias para o Natal (em 35 dias): 1 coisa que ela mencionou.")
        self.assertEqual(c["chave"], "presentes:ocasiao:natal:2026-12-25")


class TestHojeNamoroERegistro(BaseNucleo):
    config = {"inicio_namoro": "2025-10-20"}

    def test_um_ano_de_namoro(self):
        self.registrar("ela ta doida por um kindle")
        [c] = self.cartoes()
        self.assertEqual(c["texto"], "Ideias para o aniversário de namoro (em 20 dias): 1 coisa que ela mencionou.")

    def test_um_cartao_por_ocasiao_e_o_hoje_escolhe(self):
        rid = self.registrar("O aniversário dela é dia 30/10.")
        self.registrar("ela ta doida por um kindle")
        item_id = self.banco.registro(rid)["itens"][0].id
        brutos = {c["chave"]: c for c in presentes.cartoes_hoje(self.banco, REF)}
        self.assertEqual(set(brutos), {"ocasiao:namoro:2026-10-20", f"ocasiao:data:{item_id}:2026-10-30"})
        self.assertEqual(brutos[f"ocasiao:data:{item_id}:2026-10-30"]["texto"],
                         "Ideias para o aniversário dela (em 30 dias): 1 coisa que ela mencionou.")
        # O anti-excesso central deixa uma sugestão por recurso: a mais relevante (a mais perto).
        self.assertEqual([c["chave"] for c in self.cartoes()], ["presentes:ocasiao:namoro:2026-10-20"])

    def test_aniversario_tirado_de_um_registro_e_apagar_esquece_a_chave(self):
        rid = self.registrar("O aniversário dela é dia 30/10.")
        self.registrar("ela ta doida por um kindle")
        item_id = self.banco.registro(rid)["itens"][0].id
        chave = f"presentes:ocasiao:data:{item_id}:2026-10-30"
        self.assertIn(chave.removeprefix("presentes:"), [c["chave"] for c in presentes.cartoes_hoje(self.banco, REF)])
        self.pedir("POST", "/api/alertas/dispensar", {"chave": chave})
        self.assertIn(chave, self.banco.alertas_dispensados())
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertNotIn(chave, self.banco.alertas_dispensados())          # a chave revelaria a data


# ======================================================================== exportar, importar, apagar

class TestLevarEApagar(BaseNucleo):
    def montar(self) -> None:
        self.registrar("Ela queria um fone novo.", "2026-02-10")
        self.registrar("ela ta doida por um kindle", "2026-09-20")
        self.registrar("Dei um colar pra ela e ela amou.", "2026-09-25")
        self.registrar("Ela disse que adora tulipas.", "2026-09-26")
        d = {x["descricao"]: x for x in self.painel()["desejos"]}
        self.pedir("POST", f"/api/presentes/itens/{d['fone novo']['id']}/dei", {})
        self.pedir("POST", f"/api/presentes/desejos/{d['kindle']['id']}/ocultar", {})
        [i] = self.painel()["ideias"]
        self.pedir("POST", f"/api/presentes/ideias/{i['item_id']}/ocultar", {})

    def test_exportar_e_juntar_noutro_diario_sem_duplicar(self):
        self.montar()
        antes = self.painel()
        exportado = self.pedir("GET", "/api/exportar").dados
        dados = exportado["recursos"]["presentes"]
        self.assertEqual(len(dados["presentes"]), 2)
        self.assertEqual(dados["presentes"][1]["desejo_item"]["trecho"], "Ela queria um fone novo.")
        texto = json.dumps(exportado, ensure_ascii=False)
        destino = Banco.novo()
        self.addCleanup(destino.fechar)
        destino.criar_registro("Um registro de antes.", REF, [], [], [])          # os ids mudam
        r = destino.importar_json(texto)
        self.assertEqual(r["recursos"]["presentes"]["novos"], 3)
        self.assertEqual(r["recursos"]["presentes"]["ocultos"], 2)
        depois = presentes.painel(destino, REF)
        self.assertEqual(depois["desejos"], [])                               # dado e escondido continuam assim
        self.assertEqual(depois["ideias"], [])
        self.assertEqual([x["descricao"] for x in depois["recebidos"]["de_voce"]],
                         [x["descricao"] for x in antes["recebidos"]["de_voce"]])
        self.assertTrue(all(x["ligado_a_desejo"] for x in depois["recebidos"]["de_voce"] if x["descricao"] == "fone novo"))
        r = destino.importar_json(texto)
        self.assertEqual((r["registros_novos"], r["recursos"]["presentes"]["novos"]), (0, 0))
        self.assertEqual(len(destino.consultar("SELECT * FROM presentes")), 2)

    def test_importar_ignora_o_que_nao_serve(self):
        self.registrar("Dei um colar pra ela e ela amou.")
        exportado = self.pedir("GET", "/api/exportar").dados
        dados = exportado["recursos"]["presentes"]
        dados["presentes"].append({"descricao": "carro", "trecho": "Dei um carro pra ela.", "registro_id": 1})
        dados["presentes"].append({"descricao": "x", "trecho": "y", "registro_id": 999})
        dados["presentes"].append("nem é objeto")
        dados["desejos"].append({"descricao": "", "trecho": "Dei", "registro_id": 1})
        destino = Banco.novo()
        self.addCleanup(destino.fechar)
        r = destino.importar_json(json.dumps(exportado, ensure_ascii=False))
        self.assertEqual((r["recursos"]["presentes"]["novos"], r["recursos"]["presentes"]["ignorados"]), (1, 4))
        self.assertEqual([l["descricao"] for l in destino.consultar("SELECT descricao FROM presentes")], ["colar"])

    def test_apagar_o_registro_apaga_tudo_que_veio_dele(self):
        rid = self.registrar("Dei um colar pra ela e ela amou. Ela tá de olho numa air fryer.")
        outro = self.registrar("Ela queria um fone novo.", "2026-02-10")
        [d] = [x for x in self.painel()["desejos"] if x["origem"] == "item"]
        self.pedir("POST", f"/api/presentes/itens/{d['id']}/dei", {})
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertEqual(self.linhas("presentes_desejos"), [])
        self.assertEqual([l["descricao"] for l in self.linhas()], ["fone novo"])
        exportado = json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False)
        self.assertNotIn("colar", exportado)                                  # apagar é apagar: nem no export
        self.assertNotIn("air fryer", exportado)
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{outro}").status, 200)
        self.assertEqual(self.linhas(), [])
        self.assertEqual(self.linhas("presentes_ocultos"), [])

    def test_apagar_o_desejo_solta_o_presente_ligado_a_ele(self):
        rid = self.registrar("Ela queria um fone novo.", "2026-02-10")
        self.registrar("Dei um fone pra ela e ela amou.", "2026-09-20")
        [d] = self.painel()["desejos"]
        self.pedir("POST", f"/api/presentes/itens/{d['id']}/dei", {"presente_id": d["talvez_dado"]["id"]})
        self.assertIsNotNone(self.linhas()[0]["desejo_item_id"])
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        [p] = self.linhas()
        self.assertIsNone(p["desejo_item_id"])                                # nenhum id pendurado
        self.assertEqual(p["descricao"], "fone")


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
        r = self.pedir("POST", "/api/ia/analisar", {"texto": "Comprei um anel pra dar no aniversário dela.",
                                                    "data_registro": DATA})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(cadeia.analises[-1].extras, {})                         # a IA não viu
        self.assertEqual([e["descricao"] for e in r.dados["proposta"]["extras"]["presentes"]], ["anel"])

    def test_registro_com_presente_continua_podendo_ir_para_a_ia_se_o_usuario_quiser(self):
        rid = self.registrar("Dei um colar pra ela e ela amou.")
        self.assertEqual(nucleo.fora_da_ia(self.banco), set())
        self.assertFalse(hasattr(presentes, "fora_da_ia"))
        self.assertFalse(hasattr(presentes, "marca_privado"))
        self.assertFalse(self.pedir("GET", f"/api/registros/{rid}").dados["privado"])

    def test_erro_ao_gravar_nao_guarda_texto_no_log(self):
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        real = presentes._linha_presente

        def explodir(e, rid, criado):
            real(e, rid, criado)
            raise RuntimeError("Mariana deu um colar de ouro")

        with mock.patch.object(presentes, "_linha_presente", explodir):
            r = self.pedir("POST", "/api/registros", self.corpo(self.analisar("Dei um colar pra ela e ela amou.")))
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["avisos"], [recursos.AVISO_GRAVAR.format(rotulo="Ideias de presentes")])
        self.assertEqual(self.linhas(), [])                                   # a parte do recurso foi desfeita
        texto_log = "\n".join(coletor.mensagens)
        self.assertIn("RuntimeError", texto_log)
        self.assertNotIn("Mariana", texto_log)
        self.assertNotIn("colar", texto_log)

    def test_cartao_e_tela_nao_diagnosticam_nem_dao_ordem(self):
        self.banco.salvar_config(aniversario_dela="1998-10-30")
        self.registrar("O que ajuda é um chá quentinho.", "2026-09-01")
        self.registrar("Ela está muito cansada.", "2026-09-28")
        self.registrar("ela ta doida por um kindle", "2026-09-20")
        self.registrar("Ela disse que adora tulipas.", "2026-09-21")
        tudo = json.dumps([self.painel(), self.cartoes()], ensure_ascii=False).lower()
        for proibido in ("você deve", "tem que", "precisa comprar", "compre", "diagnóstico", "depressão", "tpm"):
            self.assertNotIn(proibido, tudo)
        for i in self.painel()["ideias"]:
            self.assertTrue(i["frase"].startswith("Talvez"))


# ======================================================================== contrato com a fundação

class TestContrato(unittest.TestCase):
    def test_o_modulo_cumpre_o_contrato(self):
        with recursos.usando(presentes):
            self.assertEqual(recursos.nomes_carregados(), ["presentes"])
            self.assertEqual(recursos.falhas(), {})
            self.assertEqual(recursos.tipos_extras(presentes), frozenset(TIPOS))
            self.assertEqual(recursos.rotulo("presentes"), "Ideias de presentes")
            b = Banco.novo()
            self.addCleanup(b.fechar)
            versao = b._con.execute("SELECT versao FROM recursos_esquema WHERE nome = 'presentes'").fetchone()[0]
            self.assertEqual(versao, presentes.ESQUEMA_VERSAO)
            colunas = {l[1] for l in b._con.execute("PRAGMA table_info(presentes)")}
            self.assertLessEqual({"id", "descricao", "data", "ocasiao", "de_quem", "reacao", "registro_id", "criado_em"},
                                 colunas)                                     # as colunas da seção 20.2
            b.conferir_integridade()
            self.assertEqual(b.exportar()["recursos"]["presentes"], {"versao": 1, "presentes": [], "desejos": [], "ocultos": []})

    def test_painel_sai_em_json(self):
        with recursos.usando(presentes):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            json.dumps(para_json(presentes.painel(b, REF)), ensure_ascii=False)
            self.assertEqual(presentes.cartoes_hoje(b, REF), [])

    def test_de_quem_texto(self):
        casos = {"a mãe dela": "da mãe dela", "o chefe": "do chefe", "mãe dela": "da mãe dela", "pai dela": "do pai dela",
                 "minha mãe": "da minha mãe", "meu pai": "do meu pai", "Mariana": "de Mariana", "os pais dela": "dos pais dela"}
        for quem, esperado in casos.items():
            with self.subTest(quem=quem):
                self.assertEqual(presentes.de_quem_texto(quem), esperado)

    def test_chave_junta_variacoes(self):
        self.assertTrue(presentes.mesma_coisa("um fone novo", "Fones novos"))
        self.assertTrue(presentes.mesma_coisa("fone", "fone novo"))
        self.assertFalse(presentes.mesma_coisa("fone", "kindle"))
        self.assertFalse(presentes.mesma_coisa("", "kindle"))


if __name__ == "__main__":
    unittest.main()
