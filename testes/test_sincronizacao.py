"""Testes da sincronização entre aparelhos (DESIGN.md, seção 22).

Três aparelhos simulados (núcleos de verdade, cada um na sua pasta temporária) e um "Google Drive" de
mentira (um dict nome -> bytes). O ajudante `Aparelho.sincronizar` faz o que a página faz
(web/sincronizar.js): iniciar com o entrenos-sync.json, receber só os arquivos dos outros que mudaram
(pela etiqueta, como o md5 do Drive), preparar o próprio e enviar. Um relógio de mentira, o mesmo para
todos, decide o que é "mais recente". O scrypt usa n=2**12 aqui (o real é testado em test_cofre.py).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import unittest
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from entrenos import banco as banco_mod
from entrenos import cofre, sincronizacao
from entrenos.banco import Banco
from entrenos.nucleo import Nucleo

REF = date(2026, 10, 5)
PIN = "girassol na janela"
SENHA = "biscoito de polvilho"
TEXTO_A = "Ela adora tulipas amarelas."
TEXTO_B = "A mãe dela, Sandra, vai fazer uma cirurgia em novembro."
TEXTO_C = "Ela ficou menstruada ontem e está com cólica."
INICIO = 1_791_000_000.0          # 2026-10-03, mais ou menos

_remendos: list = []
_pasta_env: str | None = None
_env_antigo: str | None = None


class Mundo:
    """O relógio (o mesmo para todos os aparelhos) e o Drive de mentira."""

    def __init__(self) -> None:
        self.t = INICIO
        self.drive: dict[str, bytes] = {}

    def andar(self, segundos: float) -> None:
        self.t += segundos

    def parede(self) -> float:
        """Como um relógio de verdade, nunca dá o mesmo instante duas vezes (anda 1 µs a cada leitura):
        dois aparelhos que sincronizam "juntos" não carimbam igual."""
        self.t += 1e-6
        return self.t

    def local(self) -> str:
        return datetime.fromtimestamp(self.t).isoformat(timespec="seconds")


MUNDO = Mundo()


def setUpModule() -> None:
    global _pasta_env, _env_antigo
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-sync-env-")
    os.environ["ENTRENOS_DADOS"] = _pasta_env       # garantia: nada cai em %LOCALAPPDATA%
    for alvo, nome, valor in ((cofre, "SCRYPT_N", 2 ** 12), (sincronizacao, "SCRYPT_N", 2 ** 12),
                              (banco_mod, "_agora", MUNDO.local)):
        remendo = mock.patch.object(alvo, nome, valor)
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


class Aparelho:
    def __init__(self, caso: unittest.TestCase, nome: str, plataforma: str = "computador"):
        self.caso = caso
        self.nome = nome
        self.pasta = tempfile.mkdtemp(prefix=f"entrenos-teste-sync-{nome}-")
        caso.addCleanup(shutil.rmtree, self.pasta, True)
        self.dia = [REF]
        # Sem bloqueio por inatividade: o relógio de mentira anda horas entre uma edição e outra.
        self.n = Nucleo(self.pasta, atraso_pin=0, hoje=lambda: self.dia[0], relogio=lambda: MUNDO.t,
                        relogio_parede=MUNDO.parede, plataforma=plataforma, minutos_bloqueio=10 ** 6)
        caso.addCleanup(self.n.encerrar)
        self.token: str | None = None

    def pedir(self, metodo: str, caminho: str, corpo: Any = None, *, esperado: int | None = 200) -> Any:
        caminho, _, q = caminho.partition("?")
        consulta = dict(x.split("=", 1) for x in q.split("&") if x) if q else None
        r = self.n.tratar(metodo, caminho, corpo=None if corpo is None else json.dumps(corpo).encode("utf-8"),
                          token=self.token, consulta=consulta)
        if esperado is not None:
            self.caso.assertEqual(r.status, esperado, (metodo, caminho, r.dados))
        return r.dados if esperado is not None else r

    def configurar(self, **extras: Any) -> None:
        self.token = self.pedir("POST", "/api/configurar", {"pin": PIN, **extras})["token"]

    def registrar(self, texto: str, data: str = "2026-10-03", privado: bool = False) -> int:
        p = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": data})
        r = self.pedir("POST", "/api/registros", {"texto": texto, "data_registro": data, "itens": p["itens"],
                                                  "pessoas": p["pessoas"], "relacoes": [], "privado": privado,
                                                  "extras": p.get("extras") or {}})
        return r["id"]

    def registros(self) -> list[dict]:
        return self.pedir("GET", "/api/registros?limite=1000")

    def textos(self) -> list[str]:
        return sorted(r["texto"] for r in self.registros())

    def registro(self, texto: str) -> dict:
        rid = next(r["id"] for r in self.registros() if r["texto"] == texto)
        return self.pedir("GET", f"/api/registros/{rid}")

    def ligar(self, senha: str = SENHA, esperado: int = 200) -> Any:
        r = self.pedir("POST", "/api/sync/senha", {"senha": senha, "sync_json": MUNDO.drive.get("entrenos-sync.json")
                                                   and MUNDO.drive["entrenos-sync.json"].decode("utf-8")},
                       esperado=esperado)
        if esperado == 200 and r.get("sync_json"):
            MUNDO.drive["entrenos-sync.json"] = r["sync_json"].encode("utf-8")
        return r

    def sincronizar(self) -> list[dict]:
        """O que a página faz (web/sincronizar.js), com o Drive de mentira."""
        ini = self.pedir("POST", "/api/sync/iniciar", {"sync_json": MUNDO.drive["entrenos-sync.json"].decode("utf-8")})
        resumos = []
        for nome, conteudo in sorted(MUNDO.drive.items()):
            aparelho = sincronizacao.aparelho_do_arquivo(nome)
            if aparelho is None or nome == ini["arquivo"]:
                continue
            etiqueta = hashlib.md5(conteudo).hexdigest()
            if ini["etiquetas"].get(aparelho) == etiqueta:
                continue                      # não mudou desde a última vez: nem baixa
            resumos.append(self.pedir("POST", "/api/sync/receber", {
                "nome": nome, "etiqueta": etiqueta, "conteudo_b64": base64.b64encode(conteudo).decode("ascii")}))
        meu = self.pedir("POST", "/api/sync/preparar", {})
        conteudo = base64.b64decode(meu["conteudo_b64"])
        MUNDO.drive[meu["nome"]] = conteudo
        self.pedir("POST", "/api/sync/enviado", {"etiqueta": hashlib.md5(conteudo).hexdigest()})
        return resumos

    def arquivo(self) -> bytes:
        return MUNDO.drive[self.pedir("GET", "/api/sync/estado")["arquivo"]]

    def chaves(self) -> sincronizacao.Chaves:
        return sincronizacao.chaves_guardadas(sincronizacao.ajustes(self.n._banco))


def rodadas(*aparelhos: Aparelho, vezes: int = 2) -> None:
    for _ in range(vezes):
        for a in aparelhos:
            a.sincronizar()
            MUNDO.andar(5)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        MUNDO.drive.clear()
        MUNDO.t = INICIO

    def tres(self) -> tuple[Aparelho, Aparelho, Aparelho]:
        a, b, c = Aparelho(self, "a"), Aparelho(self, "b"), Aparelho(self, "c")
        for x in (a, b, c):
            x.configurar()
            x.ligar()
        return a, b, c


# ---------------------------------------------------------------- mescla com três aparelhos

class TestTresAparelhos(Base):
    def test_registros_novos_de_cada_lado_chegam_em_todos_sem_duplicar(self):
        a, b, c = self.tres()
        a.registrar(TEXTO_A)
        b.registrar(TEXTO_B)
        c.registrar("Ela quer conhecer Paraty.")
        rodadas(a, b, c)
        esperado = sorted([TEXTO_A, TEXTO_B, "Ela quer conhecer Paraty."])
        for x in (a, b, c):
            self.assertEqual(x.textos(), esperado, x.nome)
        # Itens, pessoas e o lugar (recurso) vieram juntos, sem repetir.
        for x in (a, c):
            self.assertEqual([p["nome"] for p in x.pedir("GET", "/api/pessoas")], ["Sandra"])
            self.assertEqual(len(x.registro(TEXTO_B)["itens"]), len(b.registro(TEXTO_B)["itens"]))
        for x in (a, b):
            self.assertIn("Paraty", json.dumps(x.pedir("GET", "/api/lugares"), ensure_ascii=False))
        # Mais rodadas não mudam nada (nem duplicam).
        antes = {x.nome: (x.textos(), len(x.pedir("GET", "/api/pessoas"))) for x in (a, b, c)}
        resumos = [r for x in (a, b, c) for r in x.sincronizar()]
        self.assertTrue(all(r["registros_novos"] == 0 and r["apagados"] == 0 for r in resumos), resumos)
        self.assertEqual({x.nome: (x.textos(), len(x.pedir("GET", "/api/pessoas"))) for x in (a, b, c)}, antes)

    def test_arquivo_que_nao_mudou_nem_e_baixado(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        a.sincronizar()
        self.assertEqual(len(b.sincronizar()), 1)
        self.assertEqual(b.sincronizar(), [])          # o de "a" não mudou desde a última vez

    def test_proprio_arquivo_e_ignorado(self):
        a = Aparelho(self, "a")
        a.configurar()
        a.ligar()
        a.registrar(TEXTO_A)
        a.sincronizar()
        conteudo = a.arquivo()
        nome = a.pedir("GET", "/api/sync/estado")["arquivo"]
        r = a.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(conteudo).decode()})
        self.assertEqual(r, {"proprio": True, "aparelho": nome[9:25]})

    def test_apagar_num_aparelho_some_nos_outros_e_nao_volta(self):
        a, b, c = self.tres()
        a.registrar(TEXTO_A)
        a.registrar(TEXTO_B)
        rodadas(a, b, c)
        # Apagado no b (o registro veio do a).
        alvo = b.registro(TEXTO_A)["id"]
        b.pedir("DELETE", f"/api/registros/{alvo}")
        b.sincronizar()
        # O a sincroniza ANTES do c: o arquivo do c ainda tem o registro, e ele não pode voltar.
        a.sincronizar()
        self.assertEqual(a.textos(), [TEXTO_B])
        c.sincronizar()
        self.assertEqual(c.textos(), [TEXTO_B])
        rodadas(a, b, c, vezes=3)
        for x in (a, b, c):
            self.assertEqual(x.textos(), [TEXTO_B], x.nome)
        # A lápide é só uma impressão: nada do texto no arquivo (nem cifrado com a chave certa).
        for x in (a, b, c):
            payload = sincronizacao.abrir(x.chaves(), x.arquivo())
            bruto = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("tulipas", bruto)
            self.assertTrue(any(l[1] == "registro" for l in payload["lapides"]))
            for uid, tipo, quando in payload["lapides"]:
                self.assertRegex(uid, r"^[ripl][0-9a-f]{32}(-\d+)?$")
                self.assertIn(tipo, sincronizacao.TIPOS_LAPIDE)
        # E o histórico só conta.
        historico = json.dumps(a.pedir("GET", "/api/historico"), ensure_ascii=False)
        self.assertNotIn("tulipas", historico)

    def test_registro_escrito_de_novo_depois_de_apagado_vale(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b)
        b.pedir("DELETE", f"/api/registros/{b.registro(TEXTO_A)['id']}")
        rodadas(b, a)
        self.assertEqual(a.textos(), [])
        MUNDO.andar(60)
        a.registrar(TEXTO_A)              # o mesmo texto, escrito de novo: é outro registro
        rodadas(a, b)
        self.assertEqual(b.textos(), [TEXTO_A])

    def test_apagar_item_e_pessoa_propaga(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_B)
        rodadas(a, b)
        item = next(i for i in b.registro(TEXTO_B)["itens"])
        b.pedir("DELETE", f"/api/itens/{item['id']}")
        pessoa = b.pedir("GET", "/api/pessoas")[0]
        b.pedir("DELETE", f"/api/pessoas/{pessoa['id']}")
        antes = len(a.registro(TEXTO_B)["itens"])
        rodadas(b, a, vezes=2)
        itens_a = a.registro(TEXTO_B)["itens"]
        self.assertEqual(len(itens_a), antes - 1)
        self.assertEqual(sorted(i["resumo"] for i in itens_a), sorted(i["resumo"] for i in b.registro(TEXTO_B)["itens"]))
        self.assertEqual(a.pedir("GET", "/api/pessoas"), [])
        rodadas(a, b)
        self.assertEqual(a.pedir("GET", "/api/pessoas"), [])           # e não volta


class TestEdicoes(Base):
    def _item(self, x: Aparelho, texto: str = TEXTO_A) -> dict:
        return x.registro(texto)["itens"][0]

    def test_edicao_concorrente_vale_a_mais_recente(self):
        a, b, c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b, c)
        a.pedir("PATCH", f"/api/itens/{self._item(a)['id']}", {"resumo": "Gosta muito de tulipas"})
        MUNDO.andar(600)                                # dez minutos depois, no b
        b.pedir("PATCH", f"/api/itens/{self._item(b)['id']}", {"resumo": "Adora tulipas amarelas", "status": "resolvido"})
        # Quem sincroniza primeiro não importa: vale a do b, a mais recente.
        rodadas(a, b, c)
        for x in (a, b, c):
            item = self._item(x)
            self.assertEqual((item["resumo"], item["status"]), ("Adora tulipas amarelas", "resolvido"), x.nome)
        self.assertEqual(a.pedir("GET", "/api/sync/conflitos"), [])

    def test_mudou_dos_dois_lados_quase_junto_vira_pergunta(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b)
        a.pedir("PATCH", f"/api/itens/{self._item(a)['id']}", {"resumo": "Versão do a"})
        MUNDO.andar(30)                                 # 30 s: não dá para saber qual veio depois
        b.pedir("PATCH", f"/api/itens/{self._item(b)['id']}", {"resumo": "Versão do b"})
        a.sincronizar()
        b.sincronizar()
        perguntas = b.pedir("GET", "/api/sync/conflitos")
        self.assertEqual(len(perguntas), 1)
        p = perguntas[0]
        self.assertEqual(p["tipo"], "item")
        self.assertIn({"campo": "resumo", "aqui": "Versão do b", "la": "Versão do a"}, p["campos"])
        self.assertEqual(self._item(b)["resumo"], "Versão do b")          # até responder, fica a daqui
        self.assertEqual(b.pedir("GET", "/api/sync/estado")["conflitos"], 1)
        # A pessoa escolhe a do outro aparelho: vira a mais recente e vai para todos.
        MUNDO.andar(5)
        b.pedir("POST", f"/api/sync/conflitos/{p['id']}", {"fica": "outro"})
        self.assertEqual(self._item(b)["resumo"], "Versão do a")
        self.assertEqual(b.pedir("GET", "/api/sync/conflitos"), [])
        b.pedir("POST", f"/api/sync/conflitos/{p['id']}", {"fica": "este"}, esperado=404)
        rodadas(b, a)
        self.assertEqual(self._item(a)["resumo"], "Versão do a")
        self.assertEqual(a.pedir("GET", "/api/sync/conflitos"), [])

    def test_resposta_da_pergunta_este(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b)
        a.pedir("PATCH", f"/api/itens/{self._item(a)['id']}", {"status": "resolvido"})
        MUNDO.andar(5)
        b.pedir("PATCH", f"/api/itens/{self._item(b)['id']}", {"status": "descartado"})
        a.sincronizar()
        b.sincronizar()
        p = b.pedir("GET", "/api/sync/conflitos")[0]
        b.pedir("POST", f"/api/sync/conflitos/{p['id']}", {"fica": "este"})
        rodadas(b, a)
        self.assertEqual(self._item(a)["status"], "descartado")
        self.assertEqual(a.pedir("GET", "/api/sync/conflitos"), [])
        b.pedir("POST", "/api/sync/conflitos/999", {"fica": "talvez"}, esperado=400)

    def test_apagar_o_item_tira_a_pergunta(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b)
        a.pedir("PATCH", f"/api/itens/{self._item(a)['id']}", {"resumo": "um"})
        MUNDO.andar(5)
        b.pedir("PATCH", f"/api/itens/{self._item(b)['id']}", {"resumo": "dois"})
        a.sincronizar()
        b.sincronizar()
        self.assertEqual(len(b.pedir("GET", "/api/sync/conflitos")), 1)
        b.pedir("DELETE", f"/api/itens/{self._item(b)['id']}")
        self.assertEqual(b.pedir("GET", "/api/sync/conflitos"), [])
        with b.n._trava:
            self.assertEqual(b.n._banco._con.execute("SELECT count(*) FROM sync_conflitos").fetchone()[0], 0)

    def test_versao_de_la_guardada_de_outro_jeito_nao_vira_pergunta(self):
        """Um aparelho aplica a mudança do outro e o banco dele guarda algum campo de outro jeito
        (simulado aqui: a importância muda junto). A mesma versão chegando de novo, de qualquer lado,
        não vira pergunta toda vez."""
        a, b, _c = self.tres()
        b.registrar(TEXTO_A)
        rodadas(b, a)
        MUNDO.andar(600)
        item = b.registro(TEXTO_A)["itens"][0]
        b.pedir("PATCH", f"/api/itens/{item['id']}", {"resumo": "Adora tulipas amarelas"})
        original = sincronizacao.aplicar_item

        def aplicar_e_normalizar(banco, local_id, campos, pessoa_local):
            ok = original(banco, local_id, campos, pessoa_local)
            banco.atualizar_item(local_id, importancia=3 if campos.get("importancia") != 3 else 1)
            return ok
        b.sincronizar()
        with mock.patch.object(sincronizacao, "aplicar_item", aplicar_e_normalizar):
            a.sincronizar()
        self.assertEqual(a.registro(TEXTO_A)["itens"][0]["resumo"], "Adora tulipas amarelas")
        b.registrar(TEXTO_B)                                              # os arquivos mudam de novo
        a.registrar("Ela quer conhecer Paraty.")
        rodadas(b, a, vezes=3)
        self.assertEqual(a.pedir("GET", "/api/sync/conflitos"), [])
        self.assertEqual(b.pedir("GET", "/api/sync/conflitos"), [])

    def test_pessoa_renomeada_nao_duplica(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_B)
        rodadas(a, b)
        sandra = a.pedir("GET", "/api/pessoas")[0]
        MUNDO.andar(600)
        a.pedir("PATCH", f"/api/pessoas/{sandra['id']}", {"nome": "Sandra Maria", "observacoes": "faz bolo de fubá"})
        rodadas(a, b)
        pessoas = b.pedir("GET", "/api/pessoas")
        self.assertEqual([(p["nome"], p["observacoes"]) for p in pessoas], [("Sandra Maria", "faz bolo de fubá")])
        self.assertEqual(len(a.pedir("GET", "/api/pessoas")), 1)

    def test_mesma_pessoa_nos_dois_se_completa_sem_pergunta(self):
        """A mãe dela cadastrada nos dois aparelhos antes de sincronizar, cada um sabendo uma coisa: as
        duas se juntam numa pessoa só, com o que cada lado sabia, sem virar pergunta."""
        a, b, _c = self.tres()
        a.pedir("POST", "/api/pessoas", {"nome": "Sandra", "relacao": "mãe", "observacoes": "faz bolo de fubá"})
        pid = b.pedir("POST", "/api/pessoas", {"nome": "Sandra", "relacao": "mãe", "apelidos": ["Sandrinha"]})["id"]
        b.pedir("PATCH", f"/api/pessoas/{pid}", {"aniversario": "1965-03-12"})
        rodadas(a, b, vezes=3)
        for x in (a, b):
            pessoas = x.pedir("GET", "/api/pessoas")
            self.assertEqual(len(pessoas), 1, x.nome)
            p = pessoas[0]
            self.assertEqual((p["nome"], p["observacoes"], p["apelidos"], p["aniversario"]),
                             ("Sandra", "faz bolo de fubá", ["Sandrinha"], "1965-03-12"), x.nome)
            self.assertEqual(x.pedir("GET", "/api/sync/conflitos"), [])
        # Dois valores diferentes (e sem saber qual veio depois): aí sim, pergunta.
        sandra_a = a.pedir("GET", "/api/pessoas")[0]["id"]
        sandra_b = b.pedir("GET", "/api/pessoas")[0]["id"]
        MUNDO.andar(600)
        a.pedir("PATCH", f"/api/pessoas/{sandra_a}", {"observacoes": "adora bolo de fubá"})
        MUNDO.andar(5)
        b.pedir("PATCH", f"/api/pessoas/{sandra_b}", {"observacoes": "faz o melhor bolo de fubá"})
        a.sincronizar()
        b.sincronizar()
        perguntas = b.pedir("GET", "/api/sync/conflitos")
        self.assertEqual([(p["tipo"], [c["campo"] for c in p["campos"]]) for p in perguntas], [("pessoa", ["observacoes"])])

    def test_item_acrescentado_num_registro_que_os_dois_tem(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b)
        from entrenos.modelos import Certeza, Item, TipoItem
        rid = a.registro(TEXTO_A)["id"]
        with a.n._trava:
            a.n._banco.acrescentar_item(rid, Item(tipo=TipoItem.NOTA, resumo="Tulipas amarelas, não vermelhas",
                                                  trecho=TEXTO_A, certeza=Certeza.OBSERVADO))
        rodadas(a, b)
        resumos = [i["resumo"] for i in b.registro(TEXTO_A)["itens"]]
        self.assertIn("Tulipas amarelas, não vermelhas", resumos)
        self.assertEqual(len(resumos), len(a.registro(TEXTO_A)["itens"]))
        rodadas(a, b)
        self.assertEqual(len(b.registro(TEXTO_A)["itens"]), len(resumos))   # não duplica


class TestPrivacidade(Base):
    def test_cadeado_e_ciclo_e_eu_preservados(self):
        a, b, c = self.tres()
        a.registrar(TEXTO_A, privado=True)
        a.registrar(TEXTO_C)                                               # ciclo: 🔒 sozinho
        a.registrar("Levei café para ela de manhã. A gente brigou por causa da viagem e eu fiquei chateado.")
        rodadas(a, b, c)
        for x in (b, c):
            self.assertTrue(x.registro(TEXTO_A)["privado"], x.nome)
            ciclo = x.registro(TEXTO_C)
            self.assertTrue(ciclo["privado"])
            self.assertTrue(ciclo["fora_da_ia"])
            painel = x.pedir("GET", "/api/ciclo")
            self.assertTrue(painel["tem_dados"])
            eu = json.dumps(x.pedir("GET", "/api/eu"), ensure_ascii=False)
            self.assertIn("café", eu)
            self.assertEqual(x.pedir("GET", "/api/eu/reparos") and True, True)
        # O 🔒 tirado depois (mais recente) vale nos outros; marcado de novo, também.
        MUNDO.andar(600)
        a.pedir("PATCH", f"/api/registros/{a.registro(TEXTO_A)['id']}", {"privado": False})
        rodadas(a, b, c)
        self.assertFalse(c.registro(TEXTO_A)["privado"])
        MUNDO.andar(600)
        c.pedir("PATCH", f"/api/registros/{c.registro(TEXTO_A)['id']}", {"privado": True})
        rodadas(c, a, b)
        self.assertTrue(a.registro(TEXTO_A)["privado"])
        self.assertTrue(b.registro(TEXTO_A)["privado"])

    def test_cadeado_na_duvida_fica(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b)
        a.pedir("PATCH", f"/api/registros/{a.registro(TEXTO_A)['id']}", {"privado": True})
        MUNDO.andar(20)
        b.pedir("PATCH", f"/api/registros/{b.registro(TEXTO_A)['id']}", {"privado": False})
        rodadas(a, b)
        self.assertTrue(a.registro(TEXTO_A)["privado"])
        self.assertTrue(b.registro(TEXTO_A)["privado"])
        self.assertEqual(b.pedir("GET", "/api/sync/conflitos"), [])          # o 🔒 não pergunta

    def test_a_chave_nunca_sai(self):
        a, _b, _c = self.tres()
        a.registrar(TEXTO_A)
        a.sincronizar()
        chave = sincronizacao.ajustes(a.n._banco)["chave"]
        for rota in ("/api/config", "/api/exportar", "/api/historico", "/api/estado", "/api/sync/estado"):
            bruto = json.dumps(a.pedir("GET", rota), ensure_ascii=False)
            self.assertNotIn(chave, bruto, rota)
            self.assertNotIn(SENHA, bruto, rota)
        self.assertNotIn("sync", a.pedir("GET", "/api/config"))
        self.assertNotIn("sync", a.pedir("GET", "/api/exportar")["config"])
        r = a.pedir("PATCH", "/api/config", {"sync": {"chave": "x"}}, esperado=400)
        self.assertIn("Sincronização", r["erro"])
        # O que o Google vê: o entrenos-sync.json só com sal, parâmetros e verificador; o .sync, embaralhado.
        config_drive = json.loads(MUNDO.drive["entrenos-sync.json"])
        self.assertEqual(set(config_drive), {"app", "tipo", "formato", "kdf", "verificador"})
        self.assertNotIn(chave, json.dumps(config_drive))
        arquivo = a.arquivo()
        self.assertTrue(arquivo.startswith(sincronizacao.MAGICO))
        for proibido in (b"tulipas", PIN.encode(), SENHA.encode(), chave.encode(), b"Computador"):
            self.assertNotIn(proibido, arquivo)
        # Nada no log de erros.
        log = Path(a.pasta) / "erros.log"
        self.assertFalse(log.exists() and SENHA in log.read_text(encoding="utf-8"))

    def test_desligar_tira_a_chave_daqui_e_das_copias(self):
        a = Aparelho(self, "a")
        a.configurar()
        a.registrar(TEXTO_A)
        a.dia[0] = REF + timedelta(days=1)
        a.ligar()                      # grava no dia seguinte: o diário de antes vira cópia automática
        a.sincronizar()
        a.dia[0] = REF + timedelta(days=2)
        a.pedir("PATCH", "/api/sync/ajustes", {"nome": "Notebook"})      # outra cópia, já com a chave
        copias = sorted((Path(a.pasta) / "backups").glob("entrenos-*.cofre"))
        self.assertTrue(copias)
        com_chave = [c for c in copias if "sync" in self._config_da_copia(c) and
                     "chave" in self._config_da_copia(c)["sync"]]
        self.assertTrue(com_chave)
        est = a.pedir("POST", "/api/sync/desligar", {})
        self.assertFalse(est["configurada"])
        self.assertTrue(est["religavel"])
        self.assertEqual(est["nome"], "Notebook")
        aj = sincronizacao.ajustes(a.n._banco)
        self.assertNotIn("chave", aj)
        # A memória da sincronização fica (só impressões, sem a chave), para religar com a mesma senha
        # sem ressuscitar o que for apagado agora (22.2); as perguntas saem.
        self.assertEqual(set(aj["anterior"]), {"aparelho", "verificador", "sequencia"})
        self.assertNotIn(SENHA, json.dumps(aj, ensure_ascii=False))
        with a.n._trava:
            self.assertGreater(a.n._banco._con.execute("SELECT count(*) FROM sync_ids").fetchone()[0], 0)
            self.assertEqual(a.n._banco._con.execute("SELECT count(*) FROM sync_conflitos").fetchone()[0], 0)
        for copia in sorted((Path(a.pasta) / "backups").glob("entrenos-*.cofre")):
            self.assertNotIn("sync", self._config_da_copia(copia), copia.name)
        a.pedir("POST", "/api/sync/preparar", {}, esperado=409)

    @staticmethod
    def _config_da_copia(caminho: Path) -> dict:
        recebido = cofre.abrir_conteudo(caminho.read_bytes(), PIN)
        try:
            b = Banco.de_bytes(recebido.dados)
            try:
                return b.config()
            finally:
                b.fechar()
        finally:
            recebido.descartar()

    def test_apagado_pela_sincronizacao_sai_das_copias(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b)
        b.dia[0] = REF + timedelta(days=1)              # a próxima gravação do b guarda uma cópia
        a.pedir("DELETE", f"/api/registros/{a.registro(TEXTO_A)['id']}")
        a.sincronizar()
        b.sincronizar()
        self.assertEqual(b.textos(), [])
        for copia in sorted((Path(b.pasta) / "backups").glob("entrenos-*.cofre")):
            recebido = cofre.abrir_conteudo(copia.read_bytes(), PIN)
            banco = Banco.de_bytes(recebido.dados)
            self.addCleanup(banco.fechar)
            self.assertEqual([r["texto"] for r in banco.registros()], [], copia.name)


# ---------------------------------------------------------------- senha, arquivos estranhos e versões

class TestSenhaEArquivos(Base):
    def test_senha_errada_e_senha_fraca(self):
        a = Aparelho(self, "a")
        a.configurar()
        r = a.pedir("POST", "/api/sync/senha", {"senha": "123456"}, esperado=400)
        self.assertIn("senha de sincronização", r["erro"])
        a.ligar()
        b = Aparelho(self, "b")
        b.configurar()
        r = b.ligar("outra senha qualquer", esperado=401)
        self.assertEqual(r["erro"], sincronizacao.SENHA_ERRADA)
        self.assertFalse(b.pedir("GET", "/api/sync/estado")["configurada"])
        b.pedir("POST", "/api/sync/iniciar", {"sync_json": MUNDO.drive["entrenos-sync.json"].decode()}, esperado=409)
        b.ligar()
        self.assertTrue(b.pedir("GET", "/api/sync/estado")["configurada"])
        b.ligar(esperado=409)                               # já ligada
        # A senha pode ser igual ao PIN (decisão do usuário); trocar o PIN não muda a sincronização.
        c = Aparelho(self, "c")
        c.configurar()
        MUNDO.drive.pop("entrenos-sync.json")
        # ... mas a tela avisa antes (22.2): 409 {igual_ao_pin} e só cria com a confirmação.
        r = c.ligar(PIN, esperado=409)
        self.assertTrue(r["igual_ao_pin"])
        self.assertEqual(r["erro"], sincronizacao.SENHA_IGUAL_AO_PIN)
        self.assertNotIn("entrenos-sync.json", MUNDO.drive)
        r = c.pedir("POST", "/api/sync/senha", {"senha": PIN, "mesmo_que_o_pin": True})
        MUNDO.drive["entrenos-sync.json"] = r["sync_json"].encode("utf-8")
        c.pedir("POST", "/api/pin", {"atual": PIN, "novo": "outro pin bem comprido"})
        c.registrar(TEXTO_A)
        c.sincronizar()

    def test_senha_errada_bloqueia_como_o_pin(self):
        a = Aparelho(self, "a")
        a.configurar()
        a.ligar()
        b = Aparelho(self, "b")
        b.n.atraso_pin = 1.0
        b.configurar()
        r = b.ligar("senha errada demais", esperado=401)
        self.assertEqual(r["espere_segundos"], 1)
        r = b.ligar(esperado=429)                            # antes da hora: nem testa (mesmo certa)
        self.assertEqual(r["espere_segundos"], 1)
        MUNDO.andar(1.5)
        b.ligar()

    def test_senha_mudou_em_outro_lugar(self):
        a = Aparelho(self, "a")
        a.configurar()
        a.ligar()
        a.sincronizar()
        outra, _ = sincronizacao.nova_configuracao("outra senha de sincronização")
        MUNDO.drive["entrenos-sync.json"] = sincronizacao.texto_da_configuracao(outra).encode()
        r = a.pedir("POST", "/api/sync/iniciar", {"sync_json": MUNDO.drive["entrenos-sync.json"].decode()},
                    esperado=409)
        self.assertTrue(r["senha_mudou"])
        r = a.pedir("POST", "/api/sync/iniciar", {"sync_json": "{nem json"}, esperado=400)
        self.assertTrue(r["arquivo_invalido"])

    def test_arquivo_adulterado_nao_muda_nada(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        a.sincronizar()
        nome = a.pedir("GET", "/api/sync/estado")["arquivo"]
        bom = MUNDO.drive[nome]
        casos = {
            "byte trocado no meio": bom[:60] + bytes([bom[60] ^ 1]) + bom[61:],
            "cortado": bom[:-20],
            "cabeçalho mexido": b"ENTRENOS-SYNX" + bom[13:],
            "lixo": b"isto nao e um arquivo do entre nos",
        }
        antes = b.textos()
        for caso, conteudo in casos.items():
            with self.subTest(caso=caso):
                r = b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(conteudo).decode()},
                            esperado=400)
                self.assertTrue(r["arquivo_invalido"])
                self.assertEqual(b.textos(), antes)
        # De outra senha (outra "conta"): também não abre.
        estranho = sincronizacao.cifrar(sincronizacao.Chaves(os.urandom(32)), {"app": "entre-nos"})
        r = b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(estranho).decode()},
                    esperado=400)
        self.assertEqual(r["erro"], sincronizacao.ARQUIVO_ESTRANHO)
        # Com o nome de outro aparelho: recusado.
        r = b.pedir("POST", "/api/sync/receber", {"nome": "entrenos-0123456789abcdef.sync",
                                                  "conteudo_b64": base64.b64encode(bom).decode()}, esperado=400)
        self.assertEqual(r["erro"], sincronizacao.ARQUIVO_TROCADO)
        b.pedir("POST", "/api/sync/receber", {"nome": "../entrenos.cofre", "conteudo_b64": "AA=="}, esperado=400)
        b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": "não é base64"}, esperado=400)
        self.assertEqual(b.textos(), antes)
        b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(bom).decode()})
        self.assertEqual(b.textos(), [TEXTO_A])

    def test_versao_de_formato_mais_nova(self):
        a, b, _c = self.tres()
        a.sincronizar()
        nome = a.pedir("GET", "/api/sync/estado")["arquivo"]
        chaves = a.chaves()
        # Só o byte do formato trocado, sem a chave: é um arquivo que não abre, nunca "versão nova"
        # (22.2: a tag do GCM é conferida antes da versão)...
        novo = bytearray(MUNDO.drive[nome])
        novo[len(sincronizacao.MAGICO)] = sincronizacao.FORMATO + 1
        r = b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(bytes(novo)).decode()},
                    esperado=400)
        self.assertTrue(r["arquivo_invalido"])
        # ... cifrado de verdade com o formato maior no cabeçalho: versão nova ...
        payload = sincronizacao.abrir(chaves, MUNDO.drive[nome])
        r = b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(
            sincronizacao.cifrar(chaves, payload, formato=sincronizacao.FORMATO + 1)).decode()}, esperado=409)
        self.assertTrue(r["versao_nova"])
        self.assertEqual(r["erro"], sincronizacao.ARQUIVO_NOVO)
        # ... por dentro (cifrado certo, formato maior) ...
        payload["formato"] = sincronizacao.FORMATO + 1
        r = b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(
            sincronizacao.cifrar(chaves, payload)).decode()}, esperado=409)
        self.assertTrue(r["versao_nova"])
        # ... na exportação de dentro (esquema de um diário mais novo) ...
        payload["formato"] = sincronizacao.FORMATO
        payload["exportacao"]["esquema"] = banco_mod.ESQUEMA_VERSAO + 1
        r = b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(
            sincronizacao.cifrar(chaves, payload)).decode()}, esperado=409)
        self.assertTrue(r["versao_nova"])
        # ... e no entrenos-sync.json: num aparelho já ligado, um diferente do seu é "a senha mudou",
        # nunca "versão nova" (ele não é autenticado: qualquer um com o Drive poria um formato 2).
        config = json.loads(MUNDO.drive["entrenos-sync.json"])
        config["formato"] = sincronizacao.FORMATO + 1
        r = b.pedir("POST", "/api/sync/iniciar", {"sync_json": json.dumps(config)}, esperado=409)
        self.assertTrue(r["senha_mudou"])
        self.assertNotIn("versao_nova", r)
        # Ao digitar a senha (aparelho ainda não ligado), aí sim: versão nova.
        c = Aparelho(self, "c2")
        c.configurar()
        r = c.pedir("POST", "/api/sync/senha", {"senha": SENHA, "sync_json": json.dumps(config)}, esperado=409)
        self.assertTrue(r["versao_nova"])

    def test_arquivo_grande_demais_ao_abrir(self):
        a, b, _c = self.tres()
        a.sincronizar()
        nome = a.pedir("GET", "/api/sync/estado")["arquivo"]
        chaves = a.chaves()
        with mock.patch.object(sincronizacao, "MAX_DESCOMPRIMIDO", 1000):
            r = b.pedir("POST", "/api/sync/receber", {"nome": nome, "conteudo_b64": base64.b64encode(
                sincronizacao.cifrar(chaves, {"app": "entre-nos", "x": "a" * 5000})).decode()}, esperado=400)
        self.assertEqual(r["erro"], sincronizacao.ARQUIVO_GRANDE)

    def test_sem_senha_as_rotas_pedem_para_ligar(self):
        a = Aparelho(self, "a")
        a.configurar()
        for metodo, rota, corpo in (("POST", "/api/sync/preparar", {}), ("POST", "/api/sync/iniciar", {"sync_json": "{}"}),
                                    ("POST", "/api/sync/receber", {"nome": "entrenos-0123456789abcdef.sync",
                                                                   "conteudo_b64": "AA=="})):
            r = a.pedir(metodo, rota, corpo, esperado=409)
            self.assertTrue(r["sem_senha"], rota)
        est = a.pedir("GET", "/api/sync/estado")
        self.assertEqual((est["configurada"], est["aparelho"], est["sync_json"]), (False, None, None))

    def test_ajustes(self):
        a = Aparelho(self, "a", plataforma="navegador")
        a.configurar()
        est = a.pedir("GET", "/api/sync/estado")
        self.assertEqual((est["nome"], est["plataforma"], est["automatica"]), ("Celular ou navegador", "navegador", True))
        id_app = "123456789012-abcdefghijklmnop.apps.googleusercontent.com"
        est = a.pedir("PATCH", "/api/sync/ajustes", {"id_app": id_app, "nome": "Celular", "automatica": False,
                                                     "conta": "alguem@gmail.com"})
        self.assertEqual((est["id_app"], est["nome"], est["automatica"], est["conta"]),
                         (id_app, "Celular", False, "alguem@gmail.com"))
        for ruim in ({"id_app": "não é um id"}, {"nome": ""}, {"nome": "x" * 41}, {"automatica": "sim"},
                     {"chave": "x"}, {"conta": 3}):
            with self.subTest(ruim=ruim):
                a.pedir("PATCH", "/api/sync/ajustes", ruim, esperado=400)
        self.assertNotIn("sync", a.pedir("GET", "/api/config"))

    def test_restaurar_ganha_identidade_propria(self):
        a = Aparelho(self, "a")
        a.configurar()
        a.ligar()
        a.registrar(TEXTO_A)
        a.sincronizar()
        copia = a.pedir("POST", "/api/backup/baixar", {})
        b = Aparelho(self, "b", plataforma="navegador")
        r = b.pedir("POST", "/api/restaurar", {"arquivo_b64": copia["arquivo_b64"], "pin": PIN})
        b.token = r["token"]
        # A cópia baixada sai sem a chave (22.2): restaurada, pede a senha de sincronização de novo.
        est_b = b.pedir("GET", "/api/sync/estado")
        self.assertFalse(est_b["configurada"])
        self.assertTrue(est_b["religavel"])
        b.ligar()
        est_a, est_b = a.pedir("GET", "/api/sync/estado"), b.pedir("GET", "/api/sync/estado")
        self.assertTrue(est_b["configurada"])
        self.assertNotEqual(est_a["aparelho"], est_b["aparelho"])           # senão escreveriam o mesmo arquivo
        self.assertEqual(est_b["nome"], "Celular ou navegador")
        b.registrar(TEXTO_B)
        rodadas(b, a)
        self.assertEqual(a.textos(), sorted([TEXTO_A, TEXTO_B]))
        self.assertEqual(b.textos(), sorted([TEXTO_A, TEXTO_B]))

    def test_mesmo_diario_nos_dois_antes_de_ligar(self):
        """Os dois aparelhos já tinham o mesmo diário (backup restaurado) antes de ligar: nada duplica
        e o que um mudou sem o outro saber (antes de ligar) vira pergunta."""
        a = Aparelho(self, "a")
        a.configurar()
        a.registrar(TEXTO_A)
        a.registrar(TEXTO_B)
        copia = a.pedir("POST", "/api/backup/baixar", {})
        b = Aparelho(self, "b")
        b.token = b.pedir("POST", "/api/restaurar", {"arquivo_b64": copia["arquivo_b64"], "pin": PIN})["token"]
        item = b.registro(TEXTO_A)["itens"][0]
        b.pedir("PATCH", f"/api/itens/{item['id']}", {"resumo": "Mudado no b antes de ligar"})
        a.ligar()
        b.ligar()
        rodadas(a, b)
        for x in (a, b):
            self.assertEqual(x.textos(), sorted([TEXTO_A, TEXTO_B]))
            self.assertEqual(len(x.pedir("GET", "/api/pessoas")), 1)
            self.assertEqual(len(x.registro(TEXTO_A)["itens"]), len(a.registro(TEXTO_A)["itens"]))
        perguntas = a.pedir("GET", "/api/sync/conflitos") + b.pedir("GET", "/api/sync/conflitos")
        self.assertTrue(perguntas)
        self.assertTrue(all(p["tipo"] == "item" for p in perguntas))


# ---------------------------------------------------------------- linhas dos recursos

class TestRecursos(Base):
    def test_impressao_igual_depois_de_importar(self):
        """A impressão de cada linha (sem ids e datas de criação) é a mesma no aparelho de origem e
        no que importou: é o que faz a lápide de uma linha achar a cópia dela nos outros."""
        a, b, _c = self.tres()
        for texto in ("Prometi levar ela ao cinema na sexta.", "Dei um colar para ela e ela amou!",
                      "Fomos no restaurante Mangiare e ela adorou a massa.", "Hoje faz 1 ano do nosso primeiro encontro.",
                      TEXTO_C + " Quando está com cólica, bolsa de água quente ajuda.",
                      "Levei café para ela de manhã. A gente brigou por causa da viagem e eu fiquei chateado.",
                      "Ela quer conhecer Paraty.", "Preciso lembrar de perguntar como foi a prova dela na quinta."):
            a.registrar(texto)
        a.pedir("POST", "/api/rapida/rascunhos", {"texto": "ver o presente da mãe dela"})
        rodadas(a, b)
        chaves = a.chaves()
        exp_a, exp_b = a.pedir("GET", "/api/exportar")["recursos"], b.pedir("GET", "/api/exportar")["recursos"]
        uids_a = set(sincronizacao._linhas_dos_dados(chaves, exp_a))
        uids_b = set(sincronizacao._linhas_dos_dados(chaves, exp_b))
        self.assertGreater(len(uids_a), 10)
        self.assertEqual(uids_a, uids_b)

    def test_anotacao_organizada_num_aparelho_nao_volta(self):
        a, b, _c = self.tres()
        rascunho = a.pedir("POST", "/api/rapida/rascunhos", {"texto": "ver o presente da mãe dela"})
        rodadas(a, b)
        lista_b = b.pedir("GET", "/api/rapida/rascunhos")
        self.assertIn("ver o presente da mãe dela", json.dumps(lista_b, ensure_ascii=False))
        rid = next(x["id"] for x in lista_b["rascunhos"])
        b.pedir("DELETE", f"/api/rapida/rascunhos/{rid}")
        rodadas(b, a, vezes=3)
        for x in (a, b):
            self.assertNotIn("ver o presente", json.dumps(x.pedir("GET", "/api/rapida/rascunhos"), ensure_ascii=False))
        self.assertTrue(rascunho)

    def test_apagar_todos_os_dados_do_ciclo_vale_em_todos(self):
        a, b, c = self.tres()
        a.registrar(TEXTO_C + " Quando está com cólica, bolsa de água quente ajuda.")
        rodadas(a, b, c)
        self.assertTrue(c.pedir("GET", "/api/ciclo")["tem_dados"])
        b.pedir("POST", "/api/ciclo/apagar-tudo", {"confirmar": True})
        self.assertFalse(b.pedir("GET", "/api/ciclo")["tem_dados"])
        rodadas(b, c, a, vezes=3)
        for x in (a, b, c):
            painel = x.pedir("GET", "/api/ciclo")
            self.assertFalse(painel["tem_dados"], x.nome)

    def test_promessa_apagada_some_e_cumprida_vale(self):
        a, b, _c = self.tres()
        a.registrar("Prometi levar ela ao cinema na sexta.")
        a.pedir("POST", "/api/promessas", {"texto": "Fazer um jantar pra ela"})
        rodadas(a, b)
        jantar = next(p for p in b.pedir("GET", "/api/promessas")["abertas"] if "jantar" in p["texto"])
        b.pedir("DELETE", f"/api/promessas/{jantar['id']}")
        rodadas(b, a, vezes=2)
        self.assertNotIn("jantar", json.dumps(a.pedir("GET", "/api/promessas"), ensure_ascii=False))


    def test_mencao_nova_de_um_lugar_que_os_dois_tem(self):
        """O lugar muda (visitado de novo) e ganha uma menção: nos outros aparelhos ele não é apagado
        (as menções antigas apontam para ele) nem duplicado, e a menção nova chega."""
        a, b, _c = self.tres()
        a.registrar("Fomos no restaurante Mangiare e ela adorou a massa.")
        rodadas(a, b)
        MUNDO.andar(600)
        a.registrar("Fomos de novo no restaurante Mangiare e ela amou o tiramisù.", data="2026-10-04")
        rodadas(a, b)
        lugares = b.pedir("GET", "/api/lugares")
        bruto = json.dumps(lugares, ensure_ascii=False)
        self.assertIn("tiramisù", bruto)
        with b.n._trava:
            nomes = [l["nome"] for l in b.n._banco.consultar("SELECT nome FROM lugares")]
            mencoes = b.n._banco.consultar("SELECT lugar_id FROM lugares_mencoes")
        self.assertEqual(nomes.count("Mangiare"), 1)
        self.assertEqual(len(mencoes), 2)
        self.assertEqual(len({m["lugar_id"] for m in mencoes}), 1)

    def test_lugar_corrigido_num_aparelho_nao_solta_as_mencoes_no_outro(self):
        """Corrigir um lugar muda a impressão dele (a de antes vira lápide). No outro aparelho, as
        menções ainda apontam para ele: ele não é apagado (nada fica solto) nem duplicado."""
        a, b, _c = self.tres()
        a.registrar("Fomos no restaurante Mangiare e ela adorou a massa.")
        rodadas(a, b)
        lugar = a.n._banco.consultar("SELECT id FROM lugares")[0]["id"]
        MUNDO.andar(600)
        a.pedir("PATCH", f"/api/lugares/{lugar}", {"cidade": "Campinas"})
        rodadas(a, b)
        with b.n._trava:
            lugares = b.n._banco.consultar("SELECT id, nome FROM lugares")
            mencoes = b.n._banco.consultar("SELECT lugar_id FROM lugares_mencoes")
        self.assertEqual([l["nome"] for l in lugares], ["Mangiare"])
        self.assertTrue(mencoes)
        self.assertTrue(all(m["lugar_id"] == lugares[0]["id"] for m in mencoes), (lugares, mencoes))

    def test_anotacao_nova_do_ciclo_fica_ligada_ao_ciclo_que_ja_veio(self):
        """O ciclo já veio antes (e não mudou); a anotação nova aponta para ele: ele entra junto na
        importação (só para ligar), e a anotação nova fica no mesmo ciclo do outro aparelho."""
        a, b, _c = self.tres()
        a.registrar(TEXTO_C, data="2026-10-03")
        rodadas(a, b)
        a.registrar("Ela está com dor de cabeça e enxaqueca hoje por causa da menstruação.", data="2026-10-04")
        rodadas(a, b)
        with b.n._trava:
            ciclos = b.n._banco.consultar("SELECT id FROM ciclos")
            eventos = b.n._banco.consultar("SELECT tipo, ciclo_id FROM ciclo_eventos")
        with a.n._trava:
            eventos_a = a.n._banco.consultar("SELECT tipo, ciclo_id FROM ciclo_eventos")
        self.assertEqual(len(ciclos), 1)
        self.assertEqual(len(eventos), len(eventos_a))
        self.assertGreater(len(eventos), 2)
        self.assertEqual({e["ciclo_id"] for e in eventos}, {ciclos[0]["id"]})

    def test_linha_que_ja_veio_e_foi_apagada_aqui_nao_volta(self):
        """Mesmo sem a lápide achar a cópia (se a importação mudasse algum campo), o que já veio de um
        aparelho e foi apagado aqui não volta dele: só entra o que é novo daquele aparelho."""
        a, b, _c = self.tres()
        a.pedir("POST", "/api/rapida/rascunhos", {"texto": "comprar flores no sábado"})
        rodadas(a, b)
        rid = b.pedir("GET", "/api/rapida/rascunhos")["rascunhos"][0]["id"]
        # Apagado "por fora" no b (sem passar pela foto da sincronização): nenhuma lápide sai daqui.
        with b.n._trava:
            b.n._banco.executar("DELETE FROM rascunhos WHERE id = ?", (rid,))
            b.n._banco._con.execute("DELETE FROM sync_linhas WHERE aparelho = ''")
        a.pedir("POST", "/api/rapida/rascunhos", {"texto": "ligar para a mãe dela"})
        rodadas(a, b)
        textos = [r["texto"] for r in b.pedir("GET", "/api/rapida/rascunhos")["rascunhos"]]
        self.assertEqual(textos, ["ligar para a mãe dela"])


class TestDadosDela(Base):
    def test_dados_dela_completam_o_que_falta(self):
        a = Aparelho(self, "a")
        a.configurar(nome_dela="Bia", aniversario_dela="2000-03-12")
        a.ligar()
        b = Aparelho(self, "b")
        b.configurar(nome_dela="Beatriz")
        b.ligar()
        rodadas(a, b)
        config_b = b.pedir("GET", "/api/config")
        self.assertEqual((config_b["nome_dela"], config_b["aniversario_dela"]), ("Beatriz", "2000-03-12"))


class TestFormato(unittest.TestCase):
    def test_cifra_e_compressao(self):
        chaves = sincronizacao.Chaves(os.urandom(32))
        payload = {"app": "entre-nos", "tipo": "sincronizacao", "formato": 1, "aparelho": "0123456789abcdef",
                   "exportacao": {}, "ids": {}, "lapides": [], "texto": "á" * 10000}
        arquivo = sincronizacao.cifrar(chaves, payload)
        # Comprimido (10 mil "á") e preenchido até o degrau de 64 kB (22.2).
        cabecalho = len(sincronizacao.MAGICO) + 1 + sincronizacao.TAMANHO_NONCE + sincronizacao.TAMANHO_TAG
        self.assertEqual(len(arquivo), cabecalho + 64 * 1024)
        self.assertEqual(sincronizacao.abrir(chaves, arquivo), payload)
        self.assertNotEqual(sincronizacao.cifrar(chaves, payload), arquivo)     # nonce novo a cada vez
        self.assertEqual(repr(chaves), "Chaves(...)")
        with self.assertRaises(sincronizacao.ArquivoSyncInvalido):
            sincronizacao.abrir(sincronizacao.Chaves(os.urandom(32)), arquivo)
        bomba = zlib.compress(b"x" * (2 * 1024 * 1024))
        self.assertLess(len(bomba), 10_000)

    def test_configuracao(self):
        config, chaves = sincronizacao.nova_configuracao(SENHA)
        texto = sincronizacao.texto_da_configuracao(config)
        lida = sincronizacao.ler_configuracao(texto)
        self.assertEqual(sincronizacao.chaves_da_senha(SENHA, lida).mestra, chaves.mestra)
        # O mesmo acento digitado em outro teclado (NFD) dá a mesma chave, como o PIN.
        import unicodedata
        com_acento, _ = sincronizacao.nova_configuracao("pão de queijo quentinho")
        self.assertTrue(sincronizacao.chaves_da_senha(unicodedata.normalize("NFD", "pão de queijo quentinho"), com_acento))
        with self.assertRaises(sincronizacao.SenhaSyncErrada):
            sincronizacao.chaves_da_senha(SENHA.upper(), lida)
        for ruim in ("", "[]", '{"app": "entre-nos"}', texto.replace('"scrypt"', '"pbkdf2"'),
                     texto.replace('"n":4096', '"n":1048576'), texto.replace('"r":8', '"r":1')):
            with self.subTest(ruim=ruim[:40]), self.assertRaises(sincronizacao.ArquivoSyncInvalido):
                sincronizacao.ler_configuracao(ruim)
        self.assertIsNone(sincronizacao.problema_da_senha("uma frase bem comprida"))
        self.assertIn("14 caracteres", sincronizacao.problema_da_senha("senha123"))

    def test_decidir(self):
        base = "2026-10-05T12:00:00+00:00"
        mais = (datetime.fromisoformat(base) + timedelta(seconds=sincronizacao.JANELA_CONFLITO + 1)).isoformat()
        perto = (datetime.fromisoformat(base) + timedelta(seconds=30)).isoformat()
        self.assertEqual(sincronizacao._decidir(base, mais), "la")
        self.assertEqual(sincronizacao._decidir(mais, base), "aqui")
        self.assertEqual(sincronizacao._decidir(base, perto), "conflito")
        self.assertEqual(sincronizacao._decidir(sincronizacao.EPOCA, sincronizacao.EPOCA), "conflito")
        self.assertEqual(sincronizacao._decidir("lixo", mais), "la")


# ---------------------------------------------------------------- correção final das seções 21 e 22 (08/10/2026)

class TestRespostasDasPerguntas(Base):
    """A resposta de uma pergunta dentro da janela das edições ganhava o mesmo carimbo (truncado ao
    segundo) em todos os aparelhos: "mesmo carimbo = mesma versão", e cada um ficava com a sua, em
    silêncio, para sempre."""

    def _item(self, x: Aparelho, texto: str = TEXTO_A) -> dict:
        return x.registro(texto)["itens"][0]

    def _pergunta_nos_dois(self, a: Aparelho, b: Aparelho) -> tuple[dict, dict]:
        a.registrar(TEXTO_A)
        rodadas(a, b)
        a.pedir("PATCH", f"/api/itens/{self._item(a)['id']}", {"resumo": "versão do A"})
        MUNDO.andar(20)
        b.pedir("PATCH", f"/api/itens/{self._item(b)['id']}", {"resumo": "versão do B"})
        a.sincronizar()
        b.sincronizar()
        a.sincronizar()
        pa, pb = a.pedir("GET", "/api/sync/conflitos"), b.pedir("GET", "/api/sync/conflitos")
        self.assertEqual((len(pa), len(pb)), (1, 1))
        return pa[0], pb[0]

    def _sem_divergencia_calada(self, *aparelhos: Aparelho) -> None:
        resumos = {x.nome: self._item(x)["resumo"] for x in aparelhos}
        perguntas = {x.nome: len(x.pedir("GET", "/api/sync/conflitos")) for x in aparelhos}
        if len(set(resumos.values())) > 1:
            self.assertTrue(all(perguntas.values()), f"divergiram sem pergunta: {resumos} {perguntas}")

    def test_os_dois_respondem_este_dentro_da_janela(self):
        a, b = Aparelho(self, "a"), Aparelho(self, "b")
        for x in (a, b):
            x.configurar()
            x.ligar()
        pa, pb = self._pergunta_nos_dois(a, b)
        a.pedir("POST", f"/api/sync/conflitos/{pa['id']}", {"fica": "este"})
        b.pedir("POST", f"/api/sync/conflitos/{pb['id']}", {"fica": "este"})
        with a.n._trava, b.n._trava:
            carimbos = {x.nome: x.n._banco._con.execute(
                "SELECT carimbo FROM sync_ids WHERE tipo = 'item'").fetchone()[0] for x in (a, b)}
        self.assertNotEqual(carimbos["a"], carimbos["b"], "as duas respostas ganharam o mesmo carimbo")
        rodadas(a, b, vezes=4)
        self._sem_divergencia_calada(a, b)

    def test_respostas_que_concordam_convergem_sem_pergunta(self):
        a, b = Aparelho(self, "a"), Aparelho(self, "b")
        for x in (a, b):
            x.configurar()
            x.ligar()
        pa, pb = self._pergunta_nos_dois(a, b)
        a.pedir("POST", f"/api/sync/conflitos/{pa['id']}", {"fica": "este"})      # fica a do A
        b.pedir("POST", f"/api/sync/conflitos/{pb['id']}", {"fica": "outro"})     # fica a do A
        rodadas(a, b, vezes=3)
        for x in (a, b):
            self.assertEqual(self._item(x)["resumo"], "versão do A", x.nome)
            self.assertEqual(x.pedir("GET", "/api/sync/conflitos"), [], x.nome)

    def test_tres_aparelhos_respondendo_juntos(self):
        a, b, c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b, c)
        a.pedir("PATCH", f"/api/itens/{self._item(a)['id']}", {"resumo": "versão do A"})
        MUNDO.andar(20)
        b.pedir("PATCH", f"/api/itens/{self._item(b)['id']}", {"resumo": "versão do B"})
        rodadas(a, b, c, vezes=1)
        for x in (a, b, c):
            for p in x.pedir("GET", "/api/sync/conflitos"):
                x.pedir("POST", f"/api/sync/conflitos/{p['id']}", {"fica": "este"})
        rodadas(a, b, c, vezes=4)
        self._sem_divergencia_calada(a, b, c)
        # E respondendo de novo (agora fora da janela e cada um uma vez), todos chegam à mesma versão.
        for _ in range(3):
            for x in (a, b, c):
                for p in x.pedir("GET", "/api/sync/conflitos"):
                    MUNDO.andar(200)
                    x.pedir("POST", f"/api/sync/conflitos/{p['id']}", {"fica": "este"})
                    rodadas(a, b, c, vezes=2)
        self.assertEqual(len({self._item(x)["resumo"] for x in (a, b, c)}), 1)
        self.assertEqual([x.pedir("GET", "/api/sync/conflitos") for x in (a, b, c)], [[], [], []])


class TestSenhaForte(unittest.TestCase):
    """A senha de sincronização tem regra própria, mais forte que a do PIN (22.2): quem lê a pasta do
    app no Drive testa senhas fora do app, sem limite."""

    def test_recusa_palavra_nome_e_frase_feita(self):
        fracas = {
            "flores": "14 caracteres", "camila": "14 caracteres", "girassol": "14 caracteres", "amor12": "14 caracteres",
            "camila123456789": "3 palavras", "girassolamarelo!": "3 palavras", "amor amor amor amor": "3 palavras",
            "flores amarelas": "3 palavras", "eu te amo muito": "muito comum", "te amo para sempre": "muito comum",
            "11111111111111": "repetidos",
        }
        for senha, motivo in fracas.items():
            with self.subTest(senha=senha):
                problema = sincronizacao.problema_da_senha(senha)
                self.assertIsNotNone(problema)
                self.assertIn(motivo, problema)
        for boa in ("biscoito de polvilho", "girassol na janela", "coruja tapioca farol sereno 47",
                    "Pão de queijo quentinho", "Tr0ub4dor&3-cavalo!xZ"):
            with self.subTest(boa=boa):
                self.assertIsNone(sincronizacao.problema_da_senha(boa))

    def test_a_rota_recusa_a_fraca_antes_do_scrypt(self):
        a = Aparelho(self, "a")
        a.configurar()
        with mock.patch.object(sincronizacao, "derivar", side_effect=AssertionError("scrypt")):
            r = a.pedir("POST", "/api/sync/senha", {"senha": "flores"}, esperado=400)
        self.assertIn("senha de sincronização", r["erro"])


class TestApagarEApagarNoDrive(Base):
    """'Apagar é apagar' também com a sincronização parada e no Drive (22.2)."""

    def test_apagado_com_a_sincronizacao_parada_nao_volta(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        b.registrar(TEXTO_B)
        rodadas(a, b)
        arquivo_a = a.pedir("GET", "/api/sync/estado")["arquivo"]
        for x in (a, b):
            x.pedir("POST", "/api/sync/desligar", {})
        a.pedir("DELETE", f"/api/registros/{a.registro(TEXTO_A)['id']}")
        a.ligar()
        b.ligar()
        # Religar com a mesma senha: a mesma identidade (o mesmo arquivo, reescrito).
        self.assertEqual(a.pedir("GET", "/api/sync/estado")["arquivo"], arquivo_a)
        rodadas(a, b, vezes=3)
        for x in (a, b):
            self.assertEqual(x.textos(), [TEXTO_B], x.nome)
        arquivos = [n for n in MUNDO.drive if sincronizacao.aparelho_do_arquivo(n)]
        self.assertEqual(len(arquivos), 2)                    # só o do a e o do b: nenhum arquivo velho a mais

    def test_religar_com_outra_senha_apaga_o_arquivo_antigo_deste_aparelho(self):
        a = Aparelho(self, "a")
        a.configurar()
        a.ligar()
        a.registrar(TEXTO_A)
        a.sincronizar()
        antigo = a.pedir("GET", "/api/sync/estado")["arquivo"]
        a.pedir("POST", "/api/sync/desligar", {})
        MUNDO.drive.pop("entrenos-sync.json")                 # "esqueci a senha": começa outra
        a.ligar("outra frase para sincronizar")
        est = a.pedir("GET", "/api/sync/estado")
        self.assertNotEqual(est["arquivo"], antigo)
        self.assertEqual(est["apagar"], [antigo])
        # A página apaga e avisa; o núcleo esquece.
        MUNDO.drive.pop(antigo)
        a.sincronizar()
        a.pedir("POST", "/api/sync/enviado", {"etiqueta": "x", "apagados": [antigo]})
        self.assertEqual(a.pedir("GET", "/api/sync/estado")["apagar"], [])

    def test_versao_antiga_de_um_aparelho_e_recusada(self):
        a, b, c = self.tres()
        a.registrar(TEXTO_A)
        rodadas(a, b, c)
        nome_a = a.pedir("GET", "/api/sync/estado")["arquivo"]
        velho = MUNDO.drive[nome_a]                            # a versão com TEXTO_A
        a.pedir("DELETE", f"/api/registros/{a.registro(TEXTO_A)['id']}")
        rodadas(a, b, c)
        self.assertEqual(b.textos(), [])
        # Alguém com acesso ao Drive põe a versão antiga do arquivo do "a" no lugar.
        MUNDO.drive[nome_a] = velho
        r = b.pedir("POST", "/api/sync/receber", {"nome": nome_a, "etiqueta": "velha",
                                                  "conteudo_b64": base64.b64encode(velho).decode()}, esperado=400)
        self.assertEqual(r["erro"], sincronizacao.ARQUIVO_ANTIGO)
        self.assertEqual(b.textos(), [])
        # Um aparelho que nunca juntou o "a" direto, mas já viu (pelo arquivo do "b") que ele estava
        # mais adiante, também recusa.
        d = Aparelho(self, "d")
        d.configurar()
        d.ligar()
        nome_b = b.pedir("GET", "/api/sync/estado")["arquivo"]
        d.pedir("POST", "/api/sync/receber", {"nome": nome_b, "conteudo_b64": base64.b64encode(MUNDO.drive[nome_b]).decode()})
        d.pedir("POST", "/api/sync/receber", {"nome": nome_a, "conteudo_b64": base64.b64encode(velho).decode()}, esperado=400)
        self.assertNotIn(TEXTO_A, d.textos())


class TestArquivoNoDrive(Base):
    def test_tamanho_em_degraus(self):
        a = Aparelho(self, "a")
        a.configurar()
        a.ligar()
        tamanhos = []
        for texto in ("", TEXTO_A, TEXTO_B):
            if texto:
                a.registrar(texto)
            a.sincronizar()
            tamanhos.append(len(a.arquivo()))
        self.assertEqual(len(set(tamanhos)), 1, tamanhos)                  # 0, 1 e 2 registros: o mesmo tamanho
        self.assertEqual(sincronizacao.tamanho_preenchido(1), 64 * 1024)
        self.assertEqual(sincronizacao.tamanho_preenchido(64 * 1024 + 1), 128 * 1024)
        self.assertEqual(sincronizacao.tamanho_preenchido(2 * 1024 * 1024 + 1), 2 * 1024 * 1024 + 256 * 1024)
        self.assertEqual(sincronizacao.tamanho_preenchido(9 * 1024 * 1024 + 1), 10 * 1024 * 1024)

    def test_pergunta_de_registro_do_ciclo_nao_mostra_o_texto(self):
        a, b, _c = self.tres()
        a.registrar(TEXTO_C)
        rodadas(a, b)
        item = lambda x: next(i for i in x.registro(TEXTO_C)["itens"] if i["status"] == "ativo")     # noqa: E731
        a.pedir("PATCH", f"/api/itens/{item(a)['id']}", {"status": "resolvido"})
        MUNDO.andar(30)
        b.pedir("PATCH", f"/api/itens/{item(b)['id']}", {"status": "descartado", "resumo": "Ela está com cólica forte"})
        a.sincronizar()
        b.sincronizar()
        perguntas = b.pedir("GET", "/api/sync/conflitos")
        self.assertTrue(perguntas)
        bruto = json.dumps(perguntas, ensure_ascii=False).lower()
        for palavra in ("menstru", "cólica", "colica"):
            self.assertNotIn(palavra, bruto)
        self.assertTrue(all(p["discreto"] and p["resumo"] is None for p in perguntas))
        campos = {c["campo"]: c for c in perguntas[0]["campos"]}
        self.assertTrue(campos["resumo"]["reservado"])
        self.assertEqual(campos["status"]["aqui"], "descartado")          # o que não é texto continua visível

    def test_rotas_que_a_sincronizacao_chama_sozinha_nao_contam_como_uso(self):
        a = Aparelho(self, "a")
        a.n.minutos_bloqueio = 10
        a.configurar()
        a.ligar()
        a.sincronizar()
        for _ in range(12):                                   # 12 min de novas tentativas, sem ninguém tocar
            MUNDO.andar(60)
            r = a.pedir("GET", "/api/sync/estado", esperado=None)
            if r.status != 200:
                break
        self.assertEqual(r.status, 423)                       # o bloqueio por inatividade aconteceu
        self.assertFalse(a.n.desbloqueado)

    def test_copia_que_sai_do_aparelho_nao_leva_a_chave(self):
        a = Aparelho(self, "a")
        a.configurar()
        a.ligar()
        a.registrar(TEXTO_A)
        copia = base64.b64decode(a.pedir("POST", "/api/backup/baixar", {})["arquivo_b64"])
        recebido = cofre.abrir_conteudo(copia, PIN)
        try:
            banco = Banco.de_bytes(recebido.dados)
            try:
                sync = banco.config()["sync"]
                self.assertEqual([r["texto"] for r in banco.registros()], [TEXTO_A])
            finally:
                banco.fechar()
        finally:
            recebido.descartar()
        self.assertNotIn("chave", sync)
        self.assertEqual(sync["anterior"]["aparelho"], None)
        # A chave continua no diário daqui (a sincronização segue ligada).
        self.assertTrue(a.pedir("GET", "/api/sync/estado")["configurada"])
        # A mesma cópia em bytes (a ponte do navegador): igual, sem base64.
        resposta, bruto = a.n.baixar_copia(a.token)
        self.assertEqual(resposta.status, 200)
        self.assertEqual(resposta.dados["tamanho"], len(bruto))
        self.assertTrue(bruto.startswith(b"ENTRENOS1"))

    def test_restaurar_com_a_sincronizacao_ligada_traz_de_volta_o_que_esta_nos_outros(self):
        """O que a tela de restaurar avisa (22.2): com a sincronização ligada, o que está nos outros
        aparelhos e no Drive volta depois de restaurar; o que foi apagado neles continua apagado."""
        a, b, _c = self.tres()
        a.registrar(TEXTO_A)
        copia = a.pedir("POST", "/api/backup/baixar", {})          # só com TEXTO_A
        a.registrar(TEXTO_B)
        rodadas(a, b)
        token = a.pedir("POST", "/api/restaurar", {"arquivo_b64": copia["arquivo_b64"], "pin": PIN, "pin_atual": PIN})["token"]
        a.token = token
        self.assertEqual(a.textos(), [TEXTO_A])
        a.ligar()                                                    # a cópia pede a senha de novo
        rodadas(a, b)
        self.assertEqual(a.textos(), sorted([TEXTO_A, TEXTO_B]))     # voltou do b


class TestMemoriaDoBanco(unittest.TestCase):
    """`Banco.lembrar` (22.2): o Hoje deixou de reler tudo; uma escrita invalida, e nada é guardado
    dentro de uma transação."""

    def test_escrita_invalida_e_transacao_nao_guarda(self):
        b = Banco.novo()
        self.addCleanup(b.fechar)
        contagem = []

        def calcular():
            contagem.append(1)
            return len(b.registros())
        self.assertEqual(b.lembrar("x", calcular), 0)
        self.assertEqual(b.lembrar("x", calcular), 0)
        self.assertEqual(len(contagem), 1)
        b.criar_registro("Ela adora tulipas.", REF, [], [], [])
        self.assertEqual(b.lembrar("x", calcular), 1)
        self.assertEqual(len(contagem), 2)
        with b.transacao():
            b.lembrar("y", calcular)
            b.lembrar("y", calcular)
        self.assertEqual(len(contagem), 4)

    def test_json_each_logo_na_conexao_nova(self):
        # No SQLite do Pyodide o primeiro json_each pelo Banco.consultar (com o autorizador dos recursos)
        # falhava; a conexão já nasce com ele montado (aqui só confere que funciona e que o autoteste
        # tem o passo; o Pyodide de verdade é o teste_pyodide.mjs).
        b = Banco.novo()
        self.addCleanup(b.fechar)
        dados = b.para_bytes()
        self.assertEqual([l["value"] for l in b.consultar("SELECT value FROM json_each('[1,2]')")], [1, 2])
        c = Banco.de_bytes(dados)
        self.addCleanup(c.fechar)
        self.assertEqual([l["value"] for l in c.consultar("SELECT value FROM json_each('[3]')")], [3])
        from entrenos import ponte
        self.assertEqual(json.loads(ponte.autoteste())["falhas"], [])


if __name__ == "__main__":
    unittest.main()
