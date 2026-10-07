"""Anotação rápida (DESIGN.md, seção 20.4).

Só este recurso entra na lista (`recursos.usando(rapida)`): os outros estão sendo escritos ao mesmo
tempo. Cobre guardar sem revisar (com a data de referência do momento da anotação), listar com as
datas calculadas por essa referência, editar, apagar (banco, cópias, export e avisos), organizar
(a revisão normal com a referência guardada; o servidor confere que o registro é mesmo a anotação
antes de tirá-la da lista; "parece que já foi organizada"), o cartão do Hoje (contagem, urgência de
hoje/amanhã, chave que muda com anotação nova), exportar/importar e a privacidade (a anotação não é
registro: nunca vai para a IA nem para a busca; fica cifrada no arquivo; log e histórico sem texto).

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
from entrenos.modelos import Resposta
from entrenos.recursos import rapida
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
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-rapida-env-")
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


def dia(n: int) -> date:
    return REF + timedelta(days=n)


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


class BaseNucleo(unittest.TestCase):
    def setUp(self):
        self.enterContext(recursos.usando(rapida))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-rapida-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: REF, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(self.app.bloquear)
        self.token = None
        r = self.pedir("POST", "/api/configurar", {"pin": PIN})
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

    def anotar(self, texto: str = "ela tem prova amanha e ta nervosa", **mais) -> int:
        r = self.pedir("POST", "/api/rapida/rascunhos", {"texto": texto, **mais})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def lista(self) -> dict:
        r = self.pedir("GET", "/api/rapida/rascunhos")
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def linhas(self) -> list[dict]:
        return self.banco.consultar("SELECT * FROM rascunhos ORDER BY id")

    def organizar(self, rascunho: dict, texto: str | None = None) -> int:
        """Como a tela faz: analisa com a data da anotação, salva a revisão e avisa o servidor."""
        texto = texto if texto is not None else rascunho["texto"]
        p = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": rascunho["referencia"]}).dados
        r = self.pedir("POST", "/api/registros", {"texto": p["texto"], "data_registro": rascunho["referencia"],
                                                  "itens": p["itens"], "pessoas": p["pessoas"], "relacoes": p["relacoes"],
                                                  "privado": rascunho["privado"]})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def cartoes(self, data: date = REF) -> list[dict]:
        r = self.pedir("GET", "/api/hoje", consulta={"data": data.isoformat()})
        self.assertEqual(r.status, 200, r.dados)
        return [a for a in r.dados["alertas"] if a.get("recurso") == "rapida"]


# ======================================================================== guardar, listar, editar

class TestGuardar(BaseNucleo):
    def test_guardar_sem_revisar_com_a_data_de_hoje(self):
        rid = self.anotar("Ela comentou que a prova é amanhã e tá nervosa")
        [r] = self.linhas()
        self.assertEqual((r["id"], r["texto"], r["referencia"], r["privado"]),
                         (rid, "Ela comentou que a prova é amanhã e tá nervosa", DATA, 0))
        self.assertTrue(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", r["criado_em"]))
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])          # não virou registro

    def test_referencia_do_formulario_e_os_detalhes(self):
        self.anotar("Ontem ela chorou no carro", referencia="2026-09-27", privado=True, contexto="no carro",
                    humor="triste")
        [r] = self.linhas()
        self.assertEqual((r["referencia"], r["privado"], r["contexto"], r["humor"]), ("2026-09-27", 1, "no carro", "triste"))

    def test_data_no_futuro_nao_vale_mas_um_dia_de_folga_sim(self):
        self.assertEqual(self.pedir("POST", "/api/rapida/rascunhos", {"texto": "x", "referencia": "2026-10-01"}).status, 200)
        for referencia in ("2026-10-02", "amanhã", "31/12/2026", "1800-01-01", 20261001):
            with self.subTest(referencia=referencia):
                r = self.pedir("POST", "/api/rapida/rascunhos", {"texto": "x", "referencia": referencia})
                self.assertEqual((r.status, r.dados["erro"]), (400, rapida.DATA_INVALIDA))

    def test_pedidos_que_nao_servem(self):
        casos = [({"texto": ""}, rapida.TEXTO_VAZIO), ({"texto": "   "}, rapida.TEXTO_VAZIO), ({}, rapida.TEXTO_VAZIO),
                 ({"texto": 3}, rapida.TEXTO_VAZIO), ({"texto": "x" * (rapida.MAX_TEXTO + 1)}, rapida.TEXTO_GRANDE),
                 ({"texto": "ok", "privado": "sim"}, None), ({"texto": "ok", "contexto": "x" * 201}, None),
                 ({"texto": "ok", "registro_id": 1}, None)]
        for corpo, mensagem in casos:
            with self.subTest(corpo=str(corpo)[:40]):
                r = self.pedir("POST", "/api/rapida/rascunhos", corpo)
                self.assertEqual(r.status, 400)
                if mensagem:
                    self.assertEqual(r.dados["erro"], mensagem)
        self.assertEqual(self.linhas(), [])
        self.assertEqual(self.pedir("GET", "/api/rapida/rascunhos", token=None).status, 401)

    def test_texto_guarda_as_quebras_de_linha_e_tira_so_as_pontas(self):
        self.anotar("  ela tem prova amanha\n\ne ta nervosa  ")
        self.assertEqual(self.linhas()[0]["texto"], "ela tem prova amanha\n\ne ta nervosa")

    def test_referencia_vazia_vira_hoje_e_detalhes_vazios_viram_nada(self):
        self.anotar("um", referencia="", contexto="  ", humor=None)
        self.anotar("dois", referencia=None, privado=False)
        self.assertEqual([(r["referencia"], r["contexto"], r["humor"], r["privado"]) for r in self.linhas()],
                         [(DATA, None, None, 0), (DATA, None, None, 0)])

    def test_quando_e_dias(self):
        self.anotar("de hoje")
        self.anotar("de três dias atrás", referencia="2026-09-27")
        por_texto = {r["texto"]: r for r in self.lista()["rascunhos"]}
        self.assertEqual((por_texto["de hoje"]["dias"], por_texto["de hoje"]["quando"]), (0, "hoje"))
        self.assertEqual((por_texto["de três dias atrás"]["dias"], por_texto["de três dias atrás"]["quando"]),
                         (3, "há 3 dias"))

    def test_datas_com_a_precisao_e_o_passado(self):
        self.anotar("ela viaja em novembro e foi ao médico ontem")
        datas = {d["expressao"]: d for d in self.lista()["rascunhos"][0]["datas"]}
        self.assertEqual((datas["em novembro"]["data"], datas["em novembro"]["precisao"]), ("2026-11-01", "mes"))
        self.assertEqual((datas["ontem"]["data"], datas["ontem"]["passado"]), ("2026-09-29", True))

    def test_inicio_curto_para_o_cartao(self):
        self.anotar("palavra " * 40)
        inicio = self.lista()["rascunhos"][0]["inicio"]
        self.assertLessEqual(len(inicio), 90)
        self.assertTrue(inicio.endswith("…"))

    def test_lista_vazia(self):
        self.assertEqual(self.lista(), {"rascunhos": [], "total": 0, "para_organizar": 0})

    def test_editar_com_data_no_futuro_e_400(self):
        rid = self.anotar()
        r = self.pedir("PATCH", f"/api/rapida/rascunhos/{rid}", {"referencia": "2026-12-25"})
        self.assertEqual((r.status, r.dados["erro"]), (400, rapida.DATA_INVALIDA))
        self.assertEqual(self.linhas()[0]["referencia"], DATA)

    def test_apagar_anotacao_nao_mexe_nos_registros(self):
        registro = self.organizar({"texto": "Ela adora tulipas.", "referencia": DATA, "privado": False})
        rid = self.anotar("ela adora tulipas")
        self.assertEqual(self.pedir("DELETE", f"/api/rapida/rascunhos/{rid}").status, 200)
        self.assertIsNotNone(self.pedir("GET", f"/api/registros/{registro}").dados)
        self.assertEqual(len(self.pedir("GET", "/api/registros").dados), 1)

    def test_lotacao(self):
        with mock.patch.object(rapida, "MAX_RASCUNHOS", 2):
            self.anotar("um")
            self.anotar("dois")
            r = self.pedir("POST", "/api/rapida/rascunhos", {"texto": "três"})
            self.assertEqual((r.status, r.dados["erro"]), (400, rapida.DEMAIS))

    def test_lista_as_mais_antigas_primeiro_com_datas_pela_referencia(self):
        nova = self.anotar("ela vai no dentista sexta")
        antiga = self.anotar("ela tem prova amanha e ta nervosa", referencia="2026-09-29")
        dados = self.lista()
        self.assertEqual([r["id"] for r in dados["rascunhos"]], [antiga, nova])
        self.assertEqual((dados["total"], dados["para_organizar"]), (2, 2))
        r = dados["rascunhos"][0]
        # "amanhã" foi escrito em 29/09: é 30/09 (hoje), não 01/10.
        self.assertEqual(r["datas"], [{"expressao": "amanha", "data": "2026-09-30", "precisao": "dia", "passado": False,
                                       "dias": 0}])
        self.assertEqual((r["urgente"], r["dias"], r["quando"], r["organizada"]), ("hoje", 1, "ontem", None))
        self.assertEqual(dados["rascunhos"][1]["datas"][0]["data"], "2026-10-02")

    def test_editar_texto_data_e_cadeado(self):
        rid = self.anotar("ela tem prova amanha")
        r = self.pedir("PATCH", f"/api/rapida/rascunhos/{rid}", {"texto": "ela tem prova amanhã cedo",
                                                                  "referencia": "2026-09-29", "privado": True})
        self.assertEqual(r.status, 200, r.dados)
        v = r.dados["rascunho"]
        self.assertEqual((v["texto"], v["referencia"], v["privado"], v["datas"][0]["data"]),
                         ("ela tem prova amanhã cedo", "2026-09-29", True, "2026-09-30"))
        for corpo in ({}, {"texto": ""}, {"contexto": "x"}, {"id": 2}, {"privado": "sim"}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.pedir("PATCH", f"/api/rapida/rascunhos/{rid}", corpo).status, 400)
        self.assertEqual(self.pedir("PATCH", "/api/rapida/rascunhos/999", {"texto": "x"}).status, 404)

    def test_apagar_some_do_banco_das_copias_do_export_e_dos_avisos(self):
        rid = self.anotar("Segredo: ela quer um gatinho chamado Biscoito")
        [c] = self.cartoes()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/rapida/rascunhos/{rid}").status, 200)
        self.assertEqual(self.linhas(), [])
        self.assertFalse(any(k.startswith("rapida:") for k in self.banco.alertas_dispensados()))
        repetir[0](copia)
        self.assertEqual(copia.consultar("SELECT * FROM rascunhos"), [])
        self.assertFalse(any(k.startswith("rapida:") for k in copia.alertas_dispensados()))
        self.assertNotIn("Biscoito", json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False))
        self.assertEqual(self.pedir("DELETE", f"/api/rapida/rascunhos/{rid}").status, 404)


# ======================================================================== organizar

class TestOrganizar(BaseNucleo):
    def test_organizar_usa_a_data_da_anotacao(self):
        rid = self.anotar("ela tem prova amanha e ta nervosa", referencia="2026-09-27")
        r = self.lista()["rascunhos"][0]
        p = self.pedir("POST", "/api/analisar", {"texto": r["texto"], "data_registro": r["referencia"]}).dados
        datas = [d["data"] for d in p["datas"]]
        self.assertEqual(datas, ["2026-09-28"])                       # anotado em 27/09: "amanhã" = 28/09
        registro = self.organizar(r)
        self.assertEqual(self.pedir("GET", f"/api/registros/{registro}").dados["data_registro"], "2026-09-27")
        self.assertEqual(rid, r["id"])

    def test_organizado_confere_e_tira_da_lista(self):
        self.anotar("Ela comentou que a prova é amanhã e tá nervosa")
        r = self.lista()["rascunhos"][0]
        registro = self.organizar(r)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            resposta = self.pedir("POST", f"/api/rapida/rascunhos/{r['id']}/organizado", {"registro_id": registro})
        self.assertEqual(resposta.dados, {"organizado": True, "registro_id": registro})
        self.assertEqual(self.linhas(), [])
        self.assertEqual(len(repetir), 1)                              # some também das cópias automáticas
        self.assertEqual(self.cartoes(), [])

    def test_organizado_com_o_texto_um_pouco_mexido(self):
        self.anotar("ela tem prova de estatistica amanha e ta nervosa")
        r = self.lista()["rascunhos"][0]
        registro = self.organizar(r, "Ela tem prova de estatística amanhã e tá nervosa.")
        resposta = self.pedir("POST", f"/api/rapida/rascunhos/{r['id']}/organizado", {"registro_id": registro})
        self.assertTrue(resposta.dados["organizado"])

    def test_registro_diferente_nao_apaga_a_anotacao(self):
        self.anotar("ela tem prova amanha e ta nervosa")
        r = self.lista()["rascunhos"][0]
        outro = self.organizar(r, "Ela adora tulipas amarelas.")
        resposta = self.pedir("POST", f"/api/rapida/rascunhos/{r['id']}/organizado", {"registro_id": outro})
        self.assertEqual(resposta.dados, {"organizado": False, "motivo": rapida.NAO_E_ESTA})
        self.assertEqual(len(self.linhas()), 1)

    def test_registro_de_antes_da_anotacao_nao_apaga(self):
        antigo = self.organizar({"texto": "ela tem prova amanha", "referencia": DATA, "privado": False})
        # O registro é de bem antes (no mesmo segundo os dois horários seriam iguais).
        self.banco._con.execute("UPDATE registros SET criado_em = '2000-01-01T00:00:00' WHERE id = ?", (antigo,))
        self.anotar("ela tem prova amanha")
        rid = self.linhas()[0]["id"]
        resposta = self.pedir("POST", f"/api/rapida/rascunhos/{rid}/organizado", {"registro_id": antigo})
        self.assertEqual(resposta.dados, {"organizado": False, "motivo": rapida.ANTES_DA_ANOTACAO})
        self.assertEqual(len(self.linhas()), 1)

    def test_organizado_com_pedido_estranho(self):
        rid = self.anotar()
        for corpo in ({"registro_id": 999}, {"registro_id": "1"}, {"registro_id": True}, {}, {"registro_id": 1, "x": 1}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.pedir("POST", f"/api/rapida/rascunhos/{rid}/organizado", corpo).status, 400)
        self.assertEqual(self.pedir("POST", "/api/rapida/rascunhos/999/organizado", {"registro_id": 1}).status, 404)
        self.assertEqual(len(self.linhas()), 1)

    def test_organizar_com_cadeado_guarda_o_registro_fora_da_ia(self):
        self.anotar("ela anda muito triste com a família", privado=True)
        r = self.lista()["rascunhos"][0]
        self.assertTrue(r["privado"])
        registro = self.organizar(r)
        self.assertTrue(self.pedir("GET", f"/api/registros/{registro}").dados["privado"])

    def test_organizado_duas_vezes(self):
        self.anotar("ela tem prova amanha")
        r = self.lista()["rascunhos"][0]
        registro = self.organizar(r)
        caminho = f"/api/rapida/rascunhos/{r['id']}/organizado"
        self.assertTrue(self.pedir("POST", caminho, {"registro_id": registro}).dados["organizado"])
        self.assertEqual(self.pedir("POST", caminho, {"registro_id": registro}).status, 404)

    def test_parece_organizada_so_com_a_mesma_data(self):
        self.anotar("ela tem prova amanha", referencia="2026-09-29")
        self.organizar({"texto": "ela tem prova amanha", "referencia": DATA, "privado": False})   # outra data
        self.assertIsNone(self.lista()["rascunhos"][0]["organizada"])

    def test_lista_percebe_a_anotacao_ja_organizada_e_o_hoje_nao_conta(self):
        self.anotar("Ela comentou que a prova é amanhã e tá nervosa")
        self.anotar("ela vai no dentista sexta")
        r = self.lista()["rascunhos"][0]
        registro = self.organizar(r)                                   # a tela não avisou (saiu no meio)
        dados = self.lista()
        self.assertEqual(dados["rascunhos"][0]["organizada"], {"registro_id": registro, "data_registro": DATA})
        self.assertEqual((dados["total"], dados["para_organizar"]), (2, 1))
        [c] = self.cartoes()
        self.assertTrue(c["texto"].startswith("📝 1 anotação para organizar"))


# ======================================================================== Hoje

class TestHoje(BaseNucleo):
    def test_sem_anotacao_sem_cartao(self):
        self.assertEqual(self.cartoes(), [])

    def test_um_cartao_com_a_contagem_e_a_mais_antiga(self):
        self.anotar("ela quer trocar de emprego", referencia="2026-09-26")
        self.anotar("a mãe dela vai viajar")
        self.anotar("ela começou a ler um livro novo")
        [c] = self.cartoes()
        self.assertEqual((c["nivel"], c["tipo"], c["texto"]), ("lembrete", "rapida", "📝 3 anotações para organizar"))
        self.assertTrue(c["porque"].startswith("A mais antiga é de 26/09 (há 4 dias)."))
        self.assertEqual(c["relevancia"], 40 + 12 + 3)
        self.assertEqual([r["inicio"] for r in c["dados"]["rascunhos"]],
                         ["ela quer trocar de emprego", "a mãe dela vai viajar", "ela começou a ler um livro novo"])
        self.assertEqual(c["dados"]["total"], 3)

    def test_anotacao_que_fala_de_hoje_ou_amanha_sobe(self):
        self.anotar("ela vai no dentista daqui a uma semana")
        self.anotar("ela tem prova amanha", referencia="2026-09-29")       # escrita ontem: a prova é hoje
        [c] = self.cartoes()
        self.assertEqual((c["texto"], c["relevancia"]), ("📝 2 anotações para organizar (uma fala de hoje)", 82))
        self.assertIn("Na anotação de 29/09, “amanha” é hoje.", c["porque"])
        self.assertEqual(c["dados"]["rascunhos"][0]["urgente"], "hoje")    # a urgente vem primeiro
        self.assertEqual(self.cartoes(dia(1))[0]["relevancia"], 40 + 6 + 2)   # passou: volta ao normal

    def test_amanha(self):
        self.anotar("ela tem entrevista amanha")
        [c] = self.cartoes()
        self.assertEqual((c["texto"], c["relevancia"]), ("📝 1 anotação para organizar (ela fala de amanhã)", 76))

    def test_anotada_hoje(self):
        self.anotar("ela começou a pintar")
        [c] = self.cartoes()
        self.assertTrue(c["porque"].startswith("Você anotou hoje."))
        self.assertEqual(c["chave"], f"rapida:n{self.linhas()[0]['id']}:pendentes")

    def test_no_maximo_tres_no_cartao(self):
        for n in range(5):
            self.anotar(f"anotação número {n}", referencia=dia(-n).isoformat())
        [c] = self.cartoes()
        self.assertEqual((c["dados"]["total"], len(c["dados"]["rascunhos"])), (5, 3))
        self.assertEqual([r["inicio"] for r in c["dados"]["rascunhos"]],
                         ["anotação número 4", "anotação número 3", "anotação número 2"])

    def test_anotacao_com_palavras_do_ciclo_aparece_reservada(self):
        # Revisão de 07/10: o começo da anotação ia direto para o Hoje, com as palavras do ciclo.
        from entrenos.recursos import ciclo
        with recursos.usando(rapida, ciclo):
            self.anotar("Ela menstruou hoje, cólica forte. Comprar chocolate.")
            self.anotar("ela começou a pintar")
            [c] = self.cartoes()
            self.assertNotIn("menstru", json.dumps(c, ensure_ascii=False).lower())
            self.assertEqual([(r["inicio"], r["discreto"]) for r in c["dados"]["rascunhos"]],
                             [(rapida.INICIO_RESERVADO, True), ("ela começou a pintar", False)])
            # Na tela da anotação rápida, o texto continua inteiro.
            self.assertIn("menstruou", self.lista()["rascunhos"][0]["texto"])

    def test_todas_ja_organizadas_sem_cartao(self):

        self.anotar("ela tem prova amanha")
        self.organizar(self.lista()["rascunhos"][0])
        self.assertEqual(self.cartoes(), [])

    def test_dispensar_some_ate_chegar_anotacao_nova(self):
        self.anotar("um")
        [c] = self.cartoes()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        self.assertEqual(self.cartoes(), [])
        self.anotar("dois")
        self.assertEqual(len(self.cartoes()), 1)


# ======================================================================== exportar e importar

class TestExportarImportar(BaseNucleo):
    def test_exportar_e_juntar_sem_duplicar(self):
        self.anotar("ela tem prova amanha", referencia="2026-09-29", privado=True)
        self.anotar("a mãe dela vai viajar", contexto="no almoço")
        exportado = self.pedir("GET", "/api/exportar").dados
        lista = exportado["recursos"]["rapida"]["rascunhos"]
        self.assertEqual([(r["texto"], r["referencia"], r["privado"]) for r in lista],
                         [("ela tem prova amanha", "2026-09-29", 1), ("a mãe dela vai viajar", DATA, 0)])
        texto = json.dumps(exportado, ensure_ascii=False)
        with recursos.usando(rapida):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            previa = destino.importar_json(texto, previa=True)
            self.assertEqual(previa["recursos"]["rapida"]["novos"], 2)
            self.assertEqual(destino.consultar("SELECT * FROM rascunhos"), [])
            r = destino.importar_json(texto)
            self.assertEqual(r["recursos"]["rapida"], {"novos": 2, "texto": "2 anotações para organizar."})
            self.assertEqual(destino.consultar("SELECT texto, referencia, privado, contexto FROM rascunhos ORDER BY id"),
                             [{"texto": "ela tem prova amanha", "referencia": "2026-09-29", "privado": 1, "contexto": None},
                              {"texto": "a mãe dela vai viajar", "referencia": DATA, "privado": 0, "contexto": "no almoço"}])
            self.assertEqual(destino.importar_json(texto)["recursos"]["rapida"]["novos"], 0)

    def test_exportar_sem_anotacoes(self):
        self.assertEqual(self.pedir("GET", "/api/exportar").dados["recursos"]["rapida"], {"rascunhos": []})

    def test_importar_respeita_o_limite(self):
        for n in range(3):
            self.anotar(f"anotação {n}")
        texto = json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False)
        with recursos.usando(rapida), mock.patch.object(rapida, "MAX_RASCUNHOS", 2):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            r = destino.importar_json(texto)
            self.assertEqual(r["recursos"]["rapida"]["novos"], 2)
            self.assertIn("1 ficaram de fora", r["recursos"]["rapida"]["texto"])

    def test_importar_tolerante(self):
        self.anotar("ok")
        exportado = self.pedir("GET", "/api/exportar").dados
        exportado["recursos"]["rapida"]["rascunhos"] += [{"texto": "", "referencia": DATA}, "não é objeto",
                                                         {"texto": "sem data"}, {"texto": "data ruim", "referencia": "ontem"},
                                                         {"texto": "certo", "referencia": "2026-09-01", "criado_em": "x"}]
        with recursos.usando(rapida):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            r = destino.importar_json(json.dumps(exportado, ensure_ascii=False))
            self.assertEqual(r["recursos"]["rapida"]["novos"], 2)
            self.assertIn("4 ficaram de fora", r["recursos"]["rapida"]["texto"])
            outro = Banco.novo()
            self.addCleanup(outro.fechar)
            r = outro.importar_json(json.dumps({**exportado, "recursos": {"rapida": ["não é objeto"]}}))
            self.assertIn(recursos.AVISO_IMPORTAR.format(rotulo="Anotação rápida"), r["avisos"])
            self.assertEqual(outro.consultar("SELECT * FROM rascunhos"), [])


# ======================================================================== privacidade

class TestPrivacidade(BaseNucleo):
    class CadeiaFalsa:
        def __init__(self):
            self.recebido: list[str] = []

        def responder(self, pergunta, trechos):
            self.recebido.append(json.dumps(trechos, ensure_ascii=False, default=str))
            return Resposta(pergunta=pergunta, texto="Achei.", encontrados=list(trechos)), "principal", []

        def sugerir(self, contexto, referencia):
            self.recebido.append(json.dumps(contexto, ensure_ascii=False, default=str))
            return [], "principal", []

    def test_anotacao_nunca_vai_para_a_ia_nem_para_a_busca(self):
        self.banco.salvar_config(ia_principal=IA)
        cadeia = self.CadeiaFalsa()
        self.enterContext(mock.patch.object(motor, "obter_cadeia_ia", lambda ajustes: cadeia))
        self.organizar({"texto": "Ela adora tulipas amarelas.", "referencia": DATA, "privado": False})
        self.anotar("ela quer um gatinho chamado Biscoito e adora tulipas")
        self.assertEqual(self.pedir("POST", "/api/ia/sugestao", {}).status, 200)
        self.assertEqual(self.pedir("POST", "/api/ia/busca", {"q": "tulipas Biscoito gatinho"}).status, 200)
        self.assertTrue(cadeia.recebido)
        self.assertFalse(any("Biscoito" in r for r in cadeia.recebido))
        busca = self.pedir("GET", "/api/busca", consulta={"q": "Biscoito"}).dados
        self.assertEqual(busca["encontrados"], [])                       # ainda não é registro
        self.assertEqual(recursos.fora_da_ia(self.banco), set())

    def test_fica_cifrada_no_arquivo(self):
        self.anotar("ela quer um gatinho chamado Biscoito")
        arquivos = [p for p in Path(self.pasta).rglob("*") if p.is_file()]
        self.assertTrue(arquivos)
        for p in arquivos:
            with self.subTest(arquivo=p.name):
                self.assertNotIn("Biscoito".encode("utf-8"), p.read_bytes())

    def test_historico_e_log_sem_conteudo(self):
        rid = self.anotar("ela quer um gatinho chamado Biscoito")
        self.pedir("PATCH", f"/api/rapida/rascunhos/{rid}", {"texto": "ela quer dois gatinhos, Biscoito e Paçoca"})
        historico = json.dumps(self.pedir("GET", "/api/historico").dados, ensure_ascii=False)
        self.assertIn(f"rascunhos #{rid} criado", historico)
        self.assertIn(f"rascunhos #{rid} atualizado: texto", historico)
        self.assertNotIn("Biscoito", historico)
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        with mock.patch.object(rapida, "_vista", side_effect=RuntimeError("ela quer um gatinho chamado Biscoito")):
            self.assertEqual(self.pedir("GET", "/api/hoje").status, 200)
            self.assertEqual(self.pedir("GET", "/api/rapida/rascunhos").status, 500)
        texto_log = "\n".join(coletor.mensagens)
        self.assertIn("rapida", texto_log)
        self.assertNotIn("Biscoito", texto_log)

    def test_nao_tem_registro_id_nem_some_ao_apagar_um_registro(self):
        colunas = {c["name"] for c in self.banco.consultar("SELECT name FROM pragma_table_info('rascunhos')")}
        self.assertEqual(colunas, {"id", "texto", "criado_em", "referencia", "privado", "contexto", "humor"})
        self.anotar("ela tem prova amanha")
        outro = self.organizar({"texto": "Ela adora tulipas.", "referencia": DATA, "privado": False})
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{outro}").status, 200)
        self.assertEqual(len(self.linhas()), 1)


class TestEsquemaENavegador(unittest.TestCase):
    def test_esquema_e_integridade_ida_e_volta(self):
        with recursos.usando(rapida):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            versao = b._con.execute("SELECT versao FROM recursos_esquema WHERE nome = 'rapida'").fetchone()[0]
            self.assertEqual(versao, rapida.ESQUEMA_VERSAO)
            b.inserir("rascunhos", {"texto": "ela tem prova amanha", "referencia": REF, "criado_em": "2026-09-30T10:00:00"})
            copia = Banco.de_bytes(b.para_bytes())
            self.addCleanup(copia.fechar)
            copia.conferir_integridade()
            self.assertEqual(copia.consultar("SELECT texto, referencia FROM rascunhos"),
                             [{"texto": "ela tem prova amanha", "referencia": DATA}])

    def test_datas_do_texto(self):
        self.assertEqual(rapida.datas_do_texto("ela tem prova amanha", dia(-1), REF),
                         [{"expressao": "amanha", "data": REF, "precisao": "dia", "passado": False, "dias": 0}])
        self.assertEqual(rapida.datas_do_texto("ela adora tulipas", REF, REF), [])

    def test_pelo_caminho_do_navegador(self):
        from entrenos import ponte
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-rapida-ponte-")
        self.addCleanup(shutil.rmtree, pasta, True)
        self.enterContext(recursos.usando(rapida))
        ponte.iniciar_no_navegador(pasta, hoje=lambda: REF, relogio=Relogio())

        def desligar():
            if ponte._nucleo is not None:
                ponte._nucleo.bloquear()
            ponte._nucleo = None
            if ponte._log_manipulador is not None:
                logging.getLogger("entrenos").removeHandler(ponte._log_manipulador)
                ponte._log_manipulador.close()
                ponte._log_manipulador = None

        self.addCleanup(desligar)

        def pedir(metodo, caminho, corpo=None, token=None):
            return json.loads(ponte.tratar_json(metodo, caminho, None if corpo is None else json.dumps(corpo), token))

        token = pedir("POST", "/api/configurar", {"pin": PIN})["dados"]["token"]
        r = pedir("POST", "/api/rapida/rascunhos", {"texto": "ela tem prova amanha", "referencia": "2026-09-29"}, token)
        self.assertEqual((r["status"], r["gravou"]), (200, True))
        lista = pedir("GET", "/api/rapida/rascunhos", token=token)["dados"]
        self.assertEqual(lista["rascunhos"][0]["urgente"], "hoje")
        hoje = pedir("GET", "/api/hoje", token=token)["dados"]
        self.assertEqual([a["tipo"] for a in hoje["alertas"] if a.get("recurso") == "rapida"], ["rapida"])


class TestTela(unittest.TestCase):
    def test_tela_sem_html_montado_nem_estilo_inline(self):
        js = (RAIZ / "web" / "telas" / "rapida.js").read_text(encoding="utf-8")
        for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".style.",
                         "style=", "localStorage", "sessionStorage"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, js)
        for registro in ("registrarMenu('anotar'", "registrarTela('anotar'", "registrarCartaoHoje('rapida'"):
            self.assertIn(registro, js)
        self.assertIn("/organizado", js)
        # A revisão normal, com a data da anotação, pelo contrato do app (seção 20.10): nada de mexer
        # nos campos da tela do núcleo pelos ids.
        self.assertIn("E.abrirRegistrar(", js)
        self.assertIn("data: r.referencia", js)
        self.assertIn("aoSalvar:", js)
        self.assertIn("registrarAcaoRegistrar('rapida-guardar'", js)
        self.assertNotIn("#reg-", js)
        self.assertNotIn("MutationObserver", js)


if __name__ == "__main__":
    unittest.main()
