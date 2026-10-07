"""Testes de entrenos.banco (DESIGN.md, seção 14).

Banco em memória; o teste de ida e volta com o Cofre usa pasta temporária (ENTRENOS_DADOS).
Data de referência fixa: quarta-feira, 30/09/2026. Nunca date.today().
"""
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, timedelta

from entrenos import config
from entrenos.banco import CONFIG_PADRAO, ESQUEMA_VERSAO, Banco, NaoEncontrado
from entrenos.cofre import Cofre
from entrenos.modelos import (Certeza, Item, PessoaEncontrada, Precisao, Relacao, TipoItem,
                              para_json)

REF = date(2026, 9, 30)  # quarta-feira

FRASE_MAE = "A mãe dela, Sandra, vai fazer uma cirurgia em novembro."
FRASE_APRESENTACAO = "Ela está preocupada com a apresentação do dia 20."
FRASE_TULIPAS = "Ela adora tulipas."
TEXTO = f"{FRASE_MAE} {FRASE_APRESENTACAO} {FRASE_TULIPAS}"


def novo_item(tipo=TipoItem.NOTA, resumo="Nota", trecho="", certeza=Certeza.OBSERVADO, **campos) -> Item:
    return Item(tipo=tipo, resumo=resumo, trecho=trecho, certeza=certeza, **campos)


def itens_padrao() -> list[Item]:
    return [
        novo_item(TipoItem.EVENTO, "Mãe: cirurgia em novembro/2026", FRASE_MAE,
                  categorias=["Família", "Saúde", "Eventos futuros"], importancia=3,
                  data_evento=date(2026, 11, 1), data_expressao="em novembro",
                  precisao=Precisao.MES, pessoa="mãe"),
        novo_item(TipoItem.ESTADO, "Preocupada com a apresentação", FRASE_APRESENTACAO,
                  categorias=["Preocupações", "Trabalho"], data_evento=REF),
        novo_item(TipoItem.PREFERENCIA, "Gosta de tulipas", FRASE_TULIPAS, Certeza.OBSERVADO,
                  categorias=["Preferências"], alvo="tulipas", polaridade=1),
    ]


def pessoas_padrao() -> list[PessoaEncontrada]:
    return [PessoaEncontrada(trecho="A mãe dela, Sandra", inicio=0, fim=18, nome="Sandra", relacao="mãe")]


class BaseBanco(unittest.TestCase):
    def setUp(self):
        self.salvos: list[bytes] = []
        self.banco = Banco.novo(ao_salvar=self.salvos.append)
        self.addCleanup(self.banco.fechar)

    def criar_padrao(self, data: date = REF, relacoes=None) -> int:
        if relacoes is None:
            relacoes = [Relacao(tipo="contexto", motivo="Ela já comentou que apresentações a deixam nervosa.",
                                item_novo=1, sugestao="Talvez perguntar como ela está.")]
        return self.banco.criar_registro(TEXTO, data, itens_padrao(), pessoas_padrao(), relacoes,
                                         contexto="no jantar", humor="pensativa")

    def criar_simples(self, texto: str, data: date = REF, itens=None, pessoas=None) -> int:
        itens = itens if itens is not None else [novo_item(trecho=texto, resumo=texto[:40])]
        return self.banco.criar_registro(texto, data, itens, pessoas or [], [])

    def tabelas(self, banco: Banco | None = None) -> set[str]:
        banco = banco or self.banco
        return {l[0] for l in banco._con.execute("SELECT name FROM sqlite_master WHERE type IN ('table')")}

    def contar(self, tabela: str, banco: Banco | None = None) -> int:
        return (banco or self.banco)._con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]


class TestEsquema(BaseBanco):
    def test_novo_tem_todas_as_tabelas(self):
        esperadas = {"config", "registros", "itens", "pessoas", "registro_pessoas", "relacoes",
                     "alertas_dispensados", "historico", "busca_fts"}
        self.assertTrue(esperadas <= self.tabelas())
        versao = self.banco._con.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(versao, ESQUEMA_VERSAO)

    def test_fts_usa_unicode61_sem_acento(self):
        sql = self.banco._con.execute("SELECT sql FROM sqlite_master WHERE name = 'busca_fts'").fetchone()[0]
        self.assertIn("unicode61 remove_diacritics 2", sql)

    def test_novo_nao_chama_ao_salvar(self):
        self.assertEqual(self.salvos, [])

    def test_para_bytes_e_um_sqlite(self):
        self.assertTrue(self.banco.para_bytes().startswith(b"SQLite format 3\x00"))

    def test_migra_banco_da_versao_1(self):
        from entrenos.banco import _ESQUEMA_V1
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(_ESQUEMA_V1 + "\nPRAGMA user_version = 1;")
        con.execute("INSERT INTO pessoas (nome, relacao, criado_em) VALUES ('Sandra', 'mãe', '2026-09-01T10:00:00')")
        con.commit()
        banco = Banco.de_bytes(con.serialize())
        self.addCleanup(banco.fechar)
        self.assertEqual(banco._con.execute("PRAGMA user_version").fetchone()[0], ESQUEMA_VERSAO)
        colunas = {l[1] for l in banco._con.execute("PRAGMA table_info(pessoas)")}
        self.assertTrue({"nome_registro_id", "relacao_registro_id"} <= colunas)
        self.assertEqual(banco.pessoas()[0]["nome"], "Sandra")


class TestMigracaoV3(unittest.TestCase):
    """Seção 17.4: esquema 3 (registros.privado) e o motor antigo virando ia_principal."""

    @staticmethod
    def banco_antigo(versao: int, ajustes: dict) -> bytes:
        """Bytes de um banco como a versão `versao` (1 ou 2) gravava, com um registro e `ajustes`."""
        from entrenos.banco import _ESQUEMA_V1, _MIGRACAO_V2
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(_ESQUEMA_V1 + (_MIGRACAO_V2 if versao >= 2 else "")
                              + f"\nPRAGMA user_version = {versao};")
            con.execute("INSERT INTO registros (criado_em, data_registro, texto) VALUES (?, ?, ?)",
                        ("2026-09-01T10:00:00", "2026-09-01", FRASE_TULIPAS))
            for chave, valor in ajustes.items():
                con.execute("INSERT INTO config (chave, valor) VALUES (?, ?)", (chave, json.dumps(valor)))
            con.commit()
            return con.serialize()
        finally:
            con.close()

    def abrir(self, dados: bytes) -> Banco:
        banco = Banco.de_bytes(dados)
        self.addCleanup(banco.fechar)
        return banco

    @staticmethod
    def guardadas(banco: Banco) -> set[str]:
        return {l[0] for l in banco._con.execute("SELECT chave FROM config")}

    def assert_na_v3(self, banco: Banco) -> None:
        self.assertEqual(ESQUEMA_VERSAO, 3)
        self.assertEqual(banco._con.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertIn("privado", {l[1] for l in banco._con.execute("PRAGMA table_info(registros)")})
        self.assertFalse(any(k.startswith("motor") for k in self.guardadas(banco)))

    def test_novo_ja_nasce_na_v3(self):
        banco = Banco.novo()
        self.addCleanup(banco.fechar)
        self.assert_na_v3(banco)
        self.assertIsNone(banco.config()["ia_principal"])

    def test_v2_com_ollama_antigo(self):
        banco = self.abrir(self.banco_antigo(2, {"motor": "ollama", "motor_modelo": "qwen3:4b-instruct",
                                                 "motor_tempo_limite": 90, "nome_dela": "Ana"}))
        self.assert_na_v3(banco)
        c = banco.config()
        self.assertEqual(c["ia_principal"], {"provedor": "ollama", "url": "http://127.0.0.1:11434",
                                             "modelo": "qwen3:4b-instruct", "tipo": "ollama", "chave": None})
        self.assertIsNone(c["ia_reserva"])
        self.assertEqual(c["ia_tempo_limite"], 90)
        self.assertEqual(c["nome_dela"], "Ana")                  # o resto dos ajustes fica
        self.assertFalse(any(k.startswith("motor") for k in c))
        registro = banco.registro(1)
        self.assertEqual((registro["texto"], registro["privado"]), (FRASE_TULIPAS, False))

    def test_v1_com_openai_antigo_passa_pela_v2(self):
        banco = self.abrir(self.banco_antigo(1, {"motor": "openai", "motor_url": "https://api.groq.com/openai/",
                                                 "motor_modelo": "llama-3.3-70b", "motor_chave": "gsk_segredo123"}))
        self.assert_na_v3(banco)
        self.assertTrue({"nome_registro_id", "relacao_registro_id"}
                        <= {l[1] for l in banco._con.execute("PRAGMA table_info(pessoas)")})
        # O antigo chamava {base}/v1/chat/completions; o novo chama {url}/chat/completions.
        self.assertEqual(banco.config()["ia_principal"],
                         {"provedor": "groq", "url": "https://api.groq.com/openai/v1", "modelo": "llama-3.3-70b",
                          "tipo": "openai", "chave": "gsk_segredo123"})
        self.assertEqual(banco.config()["ia_tempo_limite"], 120)

    def test_url_antiga_que_ja_tinha_v1(self):
        banco = self.abrir(self.banco_antigo(2, {"motor": "openai", "motor_url": "http://127.0.0.1:1234/v1",
                                                 "motor_modelo": "gemma"}))
        ia = banco.config()["ia_principal"]
        self.assertEqual((ia["provedor"], ia["url"], ia["chave"]), ("lmstudio", "http://127.0.0.1:1234/v1", None))

    def test_servico_desconhecido_vira_outro(self):
        for url, provedor in (("https://minha-ia.exemplo.com.br/api", "outro"),
                              ("http://127.0.0.1:11434", "outro")):     # Ollama pelo formato comum
            with self.subTest(url=url):
                banco = self.abrir(self.banco_antigo(2, {"motor": "openai", "motor_url": url, "motor_modelo": "m"}))
                ia = banco.config()["ia_principal"]
                self.assertEqual((ia["provedor"], ia["url"], ia["tipo"]), (provedor, url + "/v1", "openai"))

    def test_motor_sem_ia_de_verdade_so_some(self):
        casos = [{"motor": "regras", "motor_url": "http://127.0.0.1:11434", "motor_modelo": "m"},
                 {"motor": "openai", "motor_modelo": "m", "motor_chave": "sk-solta-no-banco"},     # sem URL
                 {"motor": "ollama"},                                                             # sem modelo
                 {"motor": "chatgpt", "motor_modelo": "m"},
                 {"motor": "openai", "motor_url": "https://eu:senha@ia.exemplo.com", "motor_modelo": "m"},
                 {"motor_chave": "sk-solta-no-banco", "motor_tempo_limite": 30}]
        for ajustes in casos:
            with self.subTest(ajustes=ajustes):
                banco = self.abrir(self.banco_antigo(2, ajustes))
                self.assert_na_v3(banco)
                self.assertIsNone(banco.config()["ia_principal"])
                self.assertEqual(banco.config()["ia_tempo_limite"], 120)
                bruto = banco.para_bytes()
                self.assertNotIn(b"sk-solta-no-banco", bruto)          # apagado de verdade
                self.assertNotIn(b"senha@", bruto)

    def test_ia_nova_nao_e_sobrescrita(self):
        ia = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": "novo", "tipo": "ollama", "chave": None}
        banco = self.abrir(self.banco_antigo(2, {"motor": "ollama", "motor_modelo": "antigo", "ia_principal": ia}))
        self.assertEqual(banco.config()["ia_principal"]["modelo"], "novo")

    def test_migrado_continua_gravando_e_recarrega(self):
        salvos: list[bytes] = []
        banco = Banco.de_bytes(self.banco_antigo(1, {"motor": "ollama", "motor_modelo": "m"}), ao_salvar=salvos.append)
        self.addCleanup(banco.fechar)
        rid = banco.criar_registro("Registro novo e privado.", REF, [], [], [], privado=True)
        copia = self.abrir(salvos[-1])
        self.assert_na_v3(copia)
        self.assertEqual(copia.registros_privados(), {rid})
        self.assertEqual(copia.config()["ia_principal"]["modelo"], "m")

    def test_motor_antigo_recusado_depois(self):
        banco = Banco.novo()
        self.addCleanup(banco.fechar)
        for campos in ({"motor": "ollama"}, {"motor_url": "http://x"}, {"motor_chave": "k"}, {"motor": None},
                       {"motor_tempo_limite": 30}):
            with self.subTest(campos=campos), self.assertRaises(ValueError):
                banco.salvar_config(**campos)


class TestConfigIA(BaseBanco):
    IA = {"provedor": "openai", "url": "https://api.openai.com/v1/", "modelo": "gpt-4o-mini", "tipo": "openai",
          "chave": "  sk-proj-SEGREDO0123456789abcd  "}

    def test_ia_valida_e_normalizada(self):
        self.banco.salvar_config(ia_principal=self.IA, ia_reserva=None)
        c = self.banco.config()
        self.assertEqual(c["ia_principal"], {"provedor": "openai", "url": "https://api.openai.com/v1",
                                             "modelo": "gpt-4o-mini", "tipo": "openai",
                                             "chave": "sk-proj-SEGREDO0123456789abcd"})
        self.assertIsNone(c["ia_reserva"])

    def test_ia_invalida(self):
        casos = [{"url": "ftp://api.openai.com/v1"}, {"url": "api.openai.com"}, {"url": "https://eu:senha@x.com/v1"},
                 {"tipo": "claude"}, {"modelo": "  "}, {"modelo": None}, {"provedor": "Groq!"},
                 {"chave": "sk-proj-SEGREDO com espaço"}, {"chave": 1234}, {"extra": 1}]
        for mudanca in casos:
            with self.subTest(mudanca=mudanca):
                with self.assertRaises(ValueError) as erro:
                    self.banco.salvar_config(ia_principal=self.IA | mudanca)
                self.assertNotIn("SEGREDO", str(erro.exception))
        with self.assertRaises(ValueError):
            self.banco.salvar_config(ia_reserva="ollama")
        self.assertIsNone(self.banco.config()["ia_principal"])
        self.assertEqual(self.salvos, [])

    def test_tempo_limite(self):
        self.banco.salvar_config(ia_tempo_limite=45)
        self.assertEqual(self.banco.config()["ia_tempo_limite"], 45)
        for valor in (4, 601, True, "60", -1):
            with self.subTest(valor=valor), self.assertRaises((ValueError, TypeError)):
                self.banco.salvar_config(ia_tempo_limite=valor)

    def test_pin_da_ia_so_como_verificador(self):
        from unittest import mock
        from entrenos import cofre
        with mock.patch.object(cofre, "VERIFICADOR_N", 2 ** 10):
            verificador = cofre.criar_verificador("girassol na janela")
        self.banco.salvar_config(ia_pin=verificador)
        self.assertEqual(self.banco.config()["ia_pin"], verificador)
        for ruim in ("girassol na janela", {"hash": "x"}, verificador | {"n": 3}, verificador | {"pin": "x"}):
            with self.subTest(ruim=ruim), self.assertRaises(ValueError):
                self.banco.salvar_config(ia_pin=ruim)

    def test_exportar_e_historico_sem_segredos(self):
        from unittest import mock
        from entrenos import cofre
        with mock.patch.object(cofre, "VERIFICADOR_N", 2 ** 10):
            verificador = cofre.criar_verificador("girassol na janela")
        self.banco.salvar_config(ia_principal=self.IA, ia_reserva=self.IA | {"chave": None}, ia_pin=verificador)
        self.criar_padrao()
        dados = self.banco.exportar()
        texto = json.dumps(dados, ensure_ascii=False)
        self.assertNotIn("SEGREDO", texto)
        self.assertNotIn(verificador["hash"], texto)
        self.assertNotIn("ia_pin", dados["config"])
        self.assertEqual(dados["config"]["ia_principal"]["tem_chave"], True)
        self.assertEqual(dados["config"]["ia_reserva"]["tem_chave"], False)
        self.assertNotIn("chave", dados["config"]["ia_principal"])
        self.assertNotIn("SEGREDO", json.dumps(para_json(self.banco.historico()), ensure_ascii=False))
        self.assertEqual(self.banco.config()["ia_principal"]["chave"], "sk-proj-SEGREDO0123456789abcd")   # uso interno


class TestPrivado(BaseBanco):
    def test_criar_marcar_e_listar(self):
        publico = self.criar_simples("Ela adora tulipas.")
        privado = self.banco.criar_registro("Segredo dela.", REF, [], [], [], privado=True)
        self.assertEqual(self.banco.registro(privado)["privado"], True)
        self.assertEqual(self.banco.registro(publico)["privado"], False)
        self.assertEqual({r["id"]: r["privado"] for r in self.banco.registros()}, {publico: False, privado: True})
        self.assertEqual(self.banco.registros_privados(), {privado})
        self.banco.marcar_privado(publico, True)
        self.banco.marcar_privado(privado, False)
        self.assertEqual(self.banco.registros_privados(), {publico})
        historico = json.dumps(para_json(self.banco.historico()), ensure_ascii=False)
        self.assertIn(f"registro #{publico}: não enviar para a IA", historico)
        self.assertNotIn("tulipas", historico)
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        self.assertEqual(copia.registros_privados(), {publico})
        self.assertEqual(copia.exportar()["registros"][0]["privado"], True)

    def test_erros(self):
        rid = self.criar_simples("Ela adora tulipas.")
        with self.assertRaises(NaoEncontrado):
            self.banco.marcar_privado(999, True)
        for valor in ("sim", 1, None):
            with self.subTest(valor=valor):
                with self.assertRaises(ValueError):
                    self.banco.marcar_privado(rid, valor)
                with self.assertRaises(ValueError):
                    self.banco.criar_registro("Outro.", REF, [], [], [], privado=valor)
        self.assertEqual(self.banco.registros_privados(), set())


class TestConfig(BaseBanco):
    def test_config_padrao(self):
        c = self.banco.config()
        self.assertEqual(c, CONFIG_PADRAO)
        self.assertEqual(c["antecedencias"], config.ANTECEDENCIAS_PADRAO)
        self.assertEqual(c["minutos_bloqueio"], config.MINUTOS_BLOQUEIO_PADRAO)
        # Seção 17.4: sem "motor"; a IA fica em ia_principal/ia_reserva, desligada por padrão.
        self.assertNotIn("motor", c)
        self.assertEqual((c["ia_principal"], c["ia_reserva"], c["ia_pin"], c["ia_tempo_limite"]),
                         (None, None, None, 120))
        c["antecedencias"].append(99)       # a cópia devolvida não altera o padrão
        self.assertNotIn(99, self.banco.config()["antecedencias"])

    def test_salvar_config_com_datas(self):
        self.banco.salvar_config(nome_dela="Júlia", inicio_namoro=date(2026, 3, 7),
                                 aniversario_dela="1999-05-12", antecedencias=[7, 30, 1, 7])
        c = self.banco.config()
        self.assertEqual(c["nome_dela"], "Júlia")
        self.assertEqual(c["inicio_namoro"], date(2026, 3, 7))
        self.assertEqual(c["aniversario_dela"], date(1999, 5, 12))
        self.assertEqual(c["antecedencias"], [30, 7, 1])
        self.assertEqual(len(self.salvos), 1)

    def test_salvar_config_invalida(self):
        casos = [{"motor": "chatgpt"}, {"minutos_bloqueio": 0}, {"inicio_namoro": "31/02/2026"},
                 {"antecedencias": "30"}, {"antecedencias": [-1]}, {"Chave Estranha": 1}]
        for valores in casos:
            with self.subTest(valores=valores), self.assertRaises((ValueError, TypeError)):
                self.banco.salvar_config(**valores)
        self.assertEqual(self.banco.config(), CONFIG_PADRAO)
        self.assertEqual(self.salvos, [])

    def test_none_volta_ao_padrao(self):
        # Seção 17.4: o antigo motor/motor_url deu lugar a ia_tempo_limite/ia_principal.
        ia = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": "qwen3", "tipo": "ollama",
              "chave": None}
        self.banco.salvar_config(minutos_bloqueio=3, ia_tempo_limite=30, ia_principal=ia)
        self.banco.salvar_config(minutos_bloqueio=None, ia_tempo_limite=None)
        c = self.banco.config()
        self.assertEqual(c["minutos_bloqueio"], config.MINUTOS_BLOQUEIO_PADRAO)
        self.assertEqual(c["ia_tempo_limite"], 120)
        self.assertEqual(c["ia_principal"], ia)

    def test_chave_nova_aceita_se_couber_em_json(self):
        self.banco.salvar_config(tema="escuro")
        self.assertEqual(self.banco.config()["tema"], "escuro")


class TestCriarRegistro(BaseBanco):
    def test_grava_registro_e_itens(self):
        rid = self.criar_padrao()
        r = self.banco.registro(rid)
        self.assertEqual(r["texto"], TEXTO)
        self.assertEqual(r["data_registro"], REF)
        self.assertEqual(r["contexto"], "no jantar")
        self.assertEqual(r["humor"], "pensativa")
        self.assertEqual(len(r["itens"]), 3)
        evento = r["itens"][0]
        self.assertIsNotNone(evento.id)
        self.assertEqual(evento.registro_id, rid)
        self.assertEqual(evento.tipo, TipoItem.EVENTO)
        self.assertEqual(evento.data_evento, date(2026, 11, 1))
        self.assertEqual(evento.precisao, Precisao.MES)
        self.assertEqual(evento.categorias, ["Família", "Saúde", "Eventos futuros"])
        self.assertEqual(evento.importancia, 3)
        self.assertEqual(evento.trecho, FRASE_MAE)
        self.assertIsNotNone(evento.criado_em)
        tulipas = r["itens"][2]
        self.assertEqual((tulipas.alvo, tulipas.polaridade), ("tulipas", 1))

    def test_chama_ao_salvar_com_os_bytes(self):
        self.criar_padrao()
        self.assertEqual(len(self.salvos), 1)
        copia = Banco.de_bytes(self.salvos[0])
        self.assertEqual(copia.registro(1)["texto"], TEXTO)

    def test_registro_vazio_recusado(self):
        for texto in ("", "   \n"):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                self.banco.criar_registro(texto, REF, [], [], [])
        self.assertEqual(self.contar("registros"), 0)

    def test_item_invalido_nao_grava_nada(self):
        ruim = novo_item(trecho=FRASE_TULIPAS, status="talvez")
        with self.assertRaises(ValueError):
            self.banco.criar_registro(TEXTO, REF, [*itens_padrao(), ruim], pessoas_padrao(), [])
        self.assertEqual(self.contar("registros"), 0)
        self.assertEqual(self.contar("pessoas"), 0)
        self.assertEqual(self.salvos, [])

    def test_aceita_dicionarios_vindos_do_navegador(self):
        itens = [para_json(i) for i in itens_padrao()]
        pessoas = [para_json(p) for p in pessoas_padrao()]
        relacoes = [{"tipo": "contexto", "motivo": "m", "item_novo": 0}]
        rid = self.banco.criar_registro(TEXTO, "2026-09-30", itens, pessoas, relacoes)
        r = self.banco.registro(rid)
        self.assertEqual(len(r["itens"]), 3)
        self.assertEqual(r["relacoes"][0].item_novo, r["itens"][0].id)

    def test_ids_nao_sao_reaproveitados(self):
        self.criar_simples("Primeiro registro.")
        segundo = self.criar_simples("Segundo registro.")
        item_segundo = self.banco.registro(segundo)["itens"][0].id
        self.banco.apagar_registro(segundo)
        terceiro = self.criar_simples("Terceiro registro.")
        self.assertEqual(terceiro, segundo + 1)
        self.assertGreater(self.banco.registro(terceiro)["itens"][0].id, item_segundo)


class TestPessoasDoRegistro(BaseBanco):
    def test_cria_pessoa_nova_e_liga_ao_registro(self):
        rid = self.criar_padrao()
        pessoas = self.banco.pessoas()
        self.assertEqual(len(pessoas), 1)
        sandra = pessoas[0]
        self.assertEqual((sandra["nome"], sandra["relacao"]), ("Sandra", "mãe"))
        self.assertEqual([p["id"] for p in self.banco.registro(rid)["pessoas"]], [sandra["id"]])

    def test_item_da_mae_resolve_pela_pessoa_do_proprio_registro(self):
        rid = self.criar_padrao()
        evento, estado, _ = self.banco.registro(rid)["itens"]
        self.assertEqual(evento.pessoa, "mãe")
        self.assertEqual(evento.pessoa_id, self.banco.pessoas()[0]["id"])
        self.assertIsNone(estado.pessoa_id)

    def test_mae_sem_acento_casa_com_relacao_mae(self):
        mae = self.banco.criar_pessoa("Sandra", "mãe")
        self.banco.criar_pessoa("Carla", "tia")
        item = novo_item(TipoItem.EVENTO, "Exame", "A mãe dela fez exames.", pessoa="mae")
        rid = self.criar_simples("A mãe dela fez exames.", itens=[item])
        self.assertEqual(self.banco.registro(rid)["itens"][0].pessoa_id, mae)

    def test_item_casa_por_nome_e_apelido(self):
        mari = self.banco.criar_pessoa("Mariana", "melhor amiga", apelidos=["Mari"])
        itens = [novo_item(resumo="a", trecho="A Mari ligou.", pessoa="Mari"),
                 novo_item(resumo="b", trecho="A Mariana veio.", pessoa="mariana"),
                 novo_item(resumo="c", trecho="A melhor amiga dela.", pessoa="a melhor amiga dela")]
        rid = self.criar_simples("A Mari ligou. A Mariana veio. A melhor amiga dela.", itens=itens)
        self.assertEqual([i.pessoa_id for i in self.banco.registro(rid)["itens"]], [mari, mari, mari])

    def test_relacao_repetida_nao_casa_sozinha(self):
        self.banco.criar_pessoa("Ana", "amiga")
        self.banco.criar_pessoa("Bia", "amiga")
        item = novo_item(trecho="Uma amiga dela ligou.", pessoa="amiga")
        rid = self.criar_simples("Uma amiga dela ligou.", itens=[item])
        dado = self.banco.registro(rid)["itens"][0]
        self.assertIsNone(dado.pessoa_id)
        self.assertEqual(dado.pessoa, "amiga")

    def test_pessoa_desconhecida_fica_so_com_o_texto(self):
        item = novo_item(trecho="O Joaquim ligou.", pessoa="Joaquim")
        rid = self.criar_simples("O Joaquim ligou.", itens=[item])
        dado = self.banco.registro(rid)["itens"][0]
        self.assertEqual((dado.pessoa, dado.pessoa_id), ("Joaquim", None))
        self.assertEqual(self.banco.pessoas(), [])

    def test_nome_dela_e_ela(self):
        self.banco.salvar_config(nome_dela="Júlia")
        item = novo_item(TipoItem.PREFERENCIA, "Gosta de sushi", "A Júlia gosta de sushi.", pessoa="Julia")
        pessoa = PessoaEncontrada(trecho="Júlia", inicio=2, fim=7, nome="Júlia")
        rid = self.criar_simples("A Júlia gosta de sushi.", itens=[item], pessoas=[pessoa])
        dado = self.banco.registro(rid)["itens"][0]
        self.assertEqual((dado.pessoa, dado.pessoa_id), (None, None))
        self.assertEqual(self.banco.pessoas(), [])

    def test_pessoa_sem_nome_nem_relacao_nao_e_criada(self):
        pessoa = PessoaEncontrada(trecho="alguém", inicio=0, fim=6)
        self.criar_simples("alguém ligou.", pessoas=[pessoa])
        self.assertEqual(self.banco.pessoas(), [])

    def test_pessoa_ja_cadastrada_ganha_o_nome_que_faltava(self):
        mae = self.banco.criar_pessoa(None, "mãe")
        pessoa = PessoaEncontrada(trecho="a mãe dela, Sandra", inicio=0, fim=18, nome="Sandra",
                                  relacao="mãe", pessoa_id=mae, nova=False)
        self.criar_simples("a mãe dela, Sandra, ligou.", pessoas=[pessoa])
        self.assertEqual(self.banco.pessoa(mae)["nome"], "Sandra")
        self.assertEqual(len(self.banco.pessoas()), 1)

    def test_pessoa_nova_repetida_nao_duplica(self):
        mari = self.banco.criar_pessoa("Mariana", "amiga")
        repetidas = [PessoaEncontrada(trecho="Mariana", inicio=0, fim=7, nome="Mariana"),
                     PessoaEncontrada(trecho="Mariana", inicio=20, fim=27, nome="Mariana")]
        rid = self.criar_simples("Mariana ligou e depois Mariana voltou.", pessoas=repetidas)
        self.assertEqual(len(self.banco.pessoas()), 1)
        self.assertEqual([p["id"] for p in self.banco.registro(rid)["pessoas"]], [mari])

    def test_mesma_pessoa_nova_duas_vezes_no_mesmo_registro(self):
        repetidas = [PessoaEncontrada(trecho="a tia dela", inicio=0, fim=10, relacao="tia"),
                     PessoaEncontrada(trecho="a tia", inicio=20, fim=25, relacao="tia")]
        self.criar_simples("a tia dela ligou e a tia voltou.", pessoas=repetidas)
        self.assertEqual(len(self.banco.pessoas()), 1)

    def test_pessoa_id_inexistente_cai_para_o_texto(self):
        pessoa = PessoaEncontrada(trecho="Otávio", inicio=0, fim=6, nome="Otávio", pessoa_id=999, nova=False)
        self.criar_simples("Otávio ligou.", pessoas=[pessoa])
        self.assertEqual([p["nome"] for p in self.banco.pessoas()], ["Otávio"])


class TestRelacoes(BaseBanco):
    def test_item_novo_indice_vira_id(self):
        rid = self.criar_padrao()
        r = self.banco.registro(rid)
        [relacao] = r["relacoes"]
        self.assertEqual(relacao.item_novo, r["itens"][1].id)
        self.assertEqual(relacao.status, "pendente")
        self.assertEqual(relacao.sugestao, "Talvez perguntar como ela está.")

    def test_indice_invalido_vira_none(self):
        rid = self.criar_padrao(relacoes=[Relacao(tipo="contexto", motivo="m", item_novo=7)])
        self.assertIsNone(self.banco.registro(rid)["relacoes"][0].item_novo)

    def test_relacao_com_item_antigo(self):
        antigo = self.criar_simples("Ela não gosta de sushi.")
        item_antigo = self.banco.registro(antigo)["itens"][0].id
        rel = Relacao(tipo="contradicao", motivo="Em 12/03 você registrou...", item_novo=2,
                      item_antigo_id=item_antigo)
        rid = self.criar_padrao(relacoes=[rel])
        [gravada] = self.banco.relacoes(registro_id=rid)
        self.assertEqual(gravada.item_antigo_id, item_antigo)
        self.assertEqual(gravada.registro_antigo_id, antigo)

    def test_relacao_com_item_antigo_apagado_nao_e_gravada(self):
        rel = Relacao(tipo="contradicao", motivo="cita algo apagado", item_novo=0, item_antigo_id=555)
        rid = self.criar_padrao(relacoes=[rel])
        self.assertEqual(self.banco.relacoes(status=None, registro_id=rid), [])

    def test_filtro_por_status_e_resolver(self):
        rid = self.criar_padrao()
        [relacao] = self.banco.relacoes()
        self.banco.resolver_relacao(relacao.id, "confirmada")
        self.assertEqual(self.banco.relacoes(), [])
        self.assertEqual([r.status for r in self.banco.relacoes(status="confirmada")], ["confirmada"])
        self.assertEqual(len(self.banco.relacoes(status=None, registro_id=rid)), 1)
        self.assertEqual(self.banco.relacoes(status=None, registro_id=rid + 1), [])

    def test_resolver_com_status_invalido_ou_id_inexistente(self):
        self.criar_padrao()
        [relacao] = self.banco.relacoes()
        with self.assertRaises(ValueError):
            self.banco.resolver_relacao(relacao.id, "aceita")
        with self.assertRaises(KeyError):
            self.banco.resolver_relacao(999, "descartada")


class TestConsultas(BaseBanco):
    def test_registro_inexistente(self):
        self.assertIsNone(self.banco.registro(42))
        self.assertIsNone(self.banco.item(42))
        self.assertIsNone(self.banco.pessoa(42))

    def test_registros_ordem_e_paginacao(self):
        ids = {}
        for dias in (5, 1, 3, 1, 0):
            ids.setdefault(dias, []).append(self.criar_simples(f"Registro de {dias} dias atrás.",
                                                               REF - timedelta(days=dias)))
        lista = self.banco.registros()
        esperado = [ids[0][0], ids[1][1], ids[1][0], ids[3][0], ids[5][0]]
        self.assertEqual([r["id"] for r in lista], esperado)
        pagina1 = self.banco.registros(limite=2)
        pagina2 = self.banco.registros(limite=2, antes_de=pagina1[-1]["id"])
        pagina3 = self.banco.registros(limite=2, antes_de=pagina2[-1]["id"])
        self.assertEqual([r["id"] for r in pagina1 + pagina2 + pagina3], esperado)

    def test_registros_resumo(self):
        rid = self.criar_padrao()
        [r] = self.banco.registros()
        self.assertEqual(r["id"], rid)
        self.assertEqual(r["qtd_itens"], 3)
        self.assertEqual(r["data_registro"], REF)
        self.assertIn("Família", r["categorias"])
        self.assertEqual([p["nome"] for p in r["pessoas"]], ["Sandra"])

    def test_itens_filtros(self):
        self.criar_padrao(REF - timedelta(days=20))
        self.criar_simples("Ela está cansada.", REF, itens=[
            novo_item(TipoItem.ESTADO, "Cansada", "Ela está cansada.", categorias=["Preocupações"])])
        self.assertEqual(len(self.banco.itens()), 4)
        self.assertEqual(len(self.banco.itens(tipo=TipoItem.ESTADO)), 2)
        self.assertEqual(len(self.banco.itens(tipo="estado")), 2)
        self.assertEqual([i.resumo for i in self.banco.itens(categoria="Saúde")],
                         ["Mãe: cirurgia em novembro/2026"])
        self.assertEqual([i.resumo for i in self.banco.itens(desde=REF - timedelta(days=7))], ["Cansada"])
        recentes = self.banco.itens(tipo=TipoItem.ESTADO)
        self.assertEqual(recentes[0].resumo, "Cansada")      # mais recente primeiro

    def test_itens_por_status(self):
        rid = self.criar_padrao()
        tulipas = self.banco.registro(rid)["itens"][2]
        self.banco.atualizar_item(tulipas.id, status="mudou")
        self.assertEqual(len(self.banco.itens()), 2)
        self.assertEqual([i.id for i in self.banco.itens(status="mudou")], [tulipas.id])
        self.assertEqual(len(self.banco.itens(status=None)), 3)

    def test_itens_pessoa_id_qualquer_dela_e_de_alguem(self):
        rid = self.criar_padrao()
        item_joaquim = novo_item(trecho="O Joaquim ligou.", pessoa="Joaquim")
        self.criar_simples("O Joaquim ligou.", itens=[item_joaquim])
        sandra = self.banco.pessoas()[0]["id"]
        self.assertEqual(len(self.banco.itens()), 4)
        self.assertEqual(len(self.banco.itens(pessoa_id=...)), 4)
        dela = self.banco.itens(pessoa_id=None)
        self.assertEqual(sorted(i.resumo for i in dela), ["Gosta de tulipas", "Preocupada com a apresentação"])
        self.assertEqual([i.registro_id for i in self.banco.itens(pessoa_id=sandra)], [rid])


class TestAtualizarEApagarItem(BaseBanco):
    def test_atualizar_item(self):
        rid = self.criar_padrao()
        tulipas = self.banco.registro(rid)["itens"][2]
        self.banco.atualizar_item(tulipas.id, certeza="confirmado", categorias=["Preferências", "Presentes"],
                                  importancia=3, lembrar_em=date(2026, 10, 3))
        novo = self.banco.item(tulipas.id)
        self.assertEqual(novo.certeza, Certeza.CONFIRMADO)
        self.assertEqual(novo.categorias, ["Preferências", "Presentes"])
        self.assertEqual(novo.importancia, 3)
        self.assertEqual(novo.lembrar_em, date(2026, 10, 3))
        self.assertEqual(novo.resumo, "Gosta de tulipas")

    def test_atualizar_resumo_atualiza_a_busca(self):
        rid = self.criar_padrao()
        tulipas = self.banco.registro(rid)["itens"][2]
        self.banco.atualizar_item(tulipas.id, resumo="Gosta de girassóis")
        achados = self.banco.buscar_texto("girassois")
        self.assertIn({"tipo": "item", "ref_id": tulipas.id}, [{k: a[k] for k in ("tipo", "ref_id")} for a in achados])

    def test_atualizar_pessoa_do_item(self):
        rid = self.criar_padrao()
        estado = self.banco.registro(rid)["itens"][1]
        sandra = self.banco.pessoas()[0]["id"]
        self.banco.atualizar_item(estado.id, pessoa="Sandra")
        self.assertEqual(self.banco.item(estado.id).pessoa_id, sandra)
        self.banco.atualizar_item(estado.id, pessoa_id=None)
        self.assertEqual((self.banco.item(estado.id).pessoa, self.banco.item(estado.id).pessoa_id), (None, None))
        self.banco.atualizar_item(estado.id, pessoa_id=sandra)
        self.assertEqual(self.banco.item(estado.id).pessoa, "Sandra")

    def test_atualizar_item_invalido(self):
        rid = self.criar_padrao()
        item = self.banco.registro(rid)["itens"][0]
        casos = [{"registro_id": 3}, {"criado_em": "2026-01-01"}, {"status": "talvez"}, {"certeza": "certeza absoluta"},
                 {"tipo": "fofoca"}, {"trecho": "frase que não está no texto"}, {"pessoa_id": 999},
                 {"data_evento": "amanhã"}]
        for campos in casos:
            with self.subTest(campos=campos), self.assertRaises(ValueError):
                self.banco.atualizar_item(item.id, **campos)
        self.assertEqual(self.banco.item(item.id), item)

    def test_trecho_so_se_for_parte_do_texto(self):
        rid = self.criar_padrao()
        item = self.banco.registro(rid)["itens"][0]
        self.banco.atualizar_item(item.id, trecho="vai fazer uma cirurgia em novembro")
        self.assertEqual(self.banco.item(item.id).trecho, "vai fazer uma cirurgia em novembro")

    def test_atualizar_ou_apagar_item_inexistente(self):
        with self.assertRaises(NaoEncontrado):
            self.banco.atualizar_item(999, status="resolvido")
        with self.assertRaises(KeyError):
            self.banco.apagar_item(999)

    def test_apagar_item_remove_relacoes_e_busca(self):
        rid = self.criar_padrao()
        estado = self.banco.registro(rid)["itens"][1]
        self.banco.apagar_item(estado.id)
        self.assertIsNone(self.banco.item(estado.id))
        self.assertEqual(self.banco.relacoes(status=None), [])
        self.assertNotIn(estado.id, [a["ref_id"] for a in self.banco.buscar_texto("preocupada")
                                     if a["tipo"] == "item"])
        self.assertIsNotNone(self.banco.registro(rid))


class TestApagarRegistro(BaseBanco):
    def test_remove_itens_relacoes_pessoas_e_busca(self):
        rid = self.criar_padrao()
        antigo_item = self.banco.registro(rid)["itens"][0].id
        outro = self.criar_simples("Ela comentou da cirurgia.", itens=[novo_item(trecho="Ela comentou da cirurgia.")])
        self.banco.criar_registro("Mais um.", REF, [], [], [Relacao(tipo="contexto", motivo="cita a cirurgia",
                                                                    item_antigo_id=antigo_item)])
        self.banco.apagar_registro(rid)
        self.assertIsNone(self.banco.registro(rid))
        self.assertEqual(self.contar("itens"), 1)
        self.assertEqual(self.banco.relacoes(status=None), [])
        self.assertEqual(self.banco._con.execute(
            "SELECT count(*) FROM registro_pessoas WHERE registro_id = ?", (rid,)).fetchone()[0], 0)
        self.assertEqual(self.banco.buscar_texto("tulipas"), [])
        self.assertEqual({a["ref_id"] for a in self.banco.buscar_texto("cirurgia") if a["tipo"] == "registro"},
                         {outro})

    def test_texto_apagado_some_dos_bytes(self):
        segredo = "Ela confidenciou em Zanzibar que a Leocádia guarda um segredo inconfessável."
        itens = [novo_item(TipoItem.FATO, "Segredo inconfessável", segredo, alvo="Zanzibar", pessoa="Leocádia")]
        pessoas = [PessoaEncontrada(trecho="a Leocádia", inicio=33, fim=43, nome="Leocádia")]
        rid = self.banco.criar_registro(segredo, REF, itens, pessoas, [])
        self.criar_simples("Ela adora tulipas amarelas.")
        self.assertIn(b"Zanzibar", self.banco.para_bytes())
        self.banco.apagar_registro(rid)
        for dados in (self.salvos[-1], self.banco.para_bytes()):   # o que foi para o cofre e o atual
            for palavra in ("Zanzibar", "zanzibar", "Leocádia", "leocadia", "inconfessável", "inconfessavel",
                            "confidenciou"):
                with self.subTest(palavra=palavra):
                    self.assertNotIn(palavra.encode("utf-8"), dados)
            self.assertIn(b"tulipas", dados)
        self.assertEqual(self.banco.pessoas(), [])

    def test_vacuum_nao_deixa_paginas_livres(self):
        grande = "Texto comprido. " * 5000
        rid = self.criar_simples(grande)
        tamanho_com = len(self.banco.para_bytes())
        self.banco.apagar_registro(rid)
        dados = self.banco.para_bytes()
        self.assertLess(len(dados), tamanho_com)
        copia = Banco.de_bytes(dados)
        self.assertEqual(copia._con.execute("PRAGMA freelist_count").fetchone()[0], 0)

    def test_historico_sem_conteudo(self):
        rid = self.criar_padrao()
        self.banco.apagar_registro(rid)
        historico = self.banco.historico()
        self.assertEqual(historico[0]["resumo"], f"registro #{rid} apagado")
        self.assertEqual(historico[0]["acao"], "apagar")
        tudo = json.dumps(para_json(historico), ensure_ascii=False).lower()
        for palavra in ("sandra", "mãe", "cirurgia", "tulipas", "apresentação", "jantar", "pensativa"):
            self.assertNotIn(palavra, tudo)

    def test_pessoa_com_outras_mencoes_fica(self):
        rid = self.criar_padrao()
        sandra = self.banco.pessoas()[0]["id"]
        pessoa = PessoaEncontrada(trecho="Sandra", inicio=0, fim=6, nome="Sandra", pessoa_id=sandra, nova=False)
        self.criar_simples("Sandra ligou.", pessoas=[pessoa])
        self.banco.apagar_registro(rid)
        self.assertEqual([p["id"] for p in self.banco.pessoas()], [sandra])

    def test_pessoa_editada_ou_criada_a_mao_fica(self):
        rid = self.criar_padrao()
        sandra = self.banco.pessoas()[0]["id"]
        self.banco.atualizar_pessoa(sandra, observacoes="Mora em Campinas")
        manual = self.banco.criar_pessoa("Otávio", "pai")
        item = novo_item(trecho="O pai dela ligou.", pessoa="pai")
        outro = self.criar_simples("O pai dela ligou.", itens=[item])
        self.banco.apagar_registro(rid)
        self.banco.apagar_registro(outro)
        self.assertEqual({p["id"] for p in self.banco.pessoas()}, {sandra, manual})

    def test_apagar_inexistente(self):
        with self.assertRaises(NaoEncontrado):
            self.banco.apagar_registro(123)

    def test_nome_aprendido_do_registro_sai_junto(self):
        # A "mãe" foi cadastrada à mão; o nome veio do texto apagado e não pode ficar.
        mae = self.banco.criar_pessoa(None, "mãe")
        texto = "A mãe dela, Sandraleia, ligou."
        pessoa = PessoaEncontrada(trecho="A mãe dela, Sandraleia", inicio=0, fim=22, nome="Sandraleia", relacao="mãe")
        rid = self.banco.criar_registro(texto, REF, [novo_item(trecho=texto, pessoa="mãe")], [pessoa], [])
        self.assertEqual(self.banco.pessoa(mae)["nome"], "Sandraleia")
        self.banco.apagar_registro(rid)
        self.assertEqual((self.banco.pessoa(mae)["nome"], self.banco.pessoa(mae)["relacao"]), (None, "mãe"))
        self.assertNotIn(b"Sandraleia", self.banco.para_bytes())

    def test_nome_citado_em_outro_registro_fica(self):
        texto = "A mãe dela, Sandraleia, ligou."
        pessoa = PessoaEncontrada(trecho="A mãe dela, Sandraleia", inicio=0, fim=22, nome="Sandraleia", relacao="mãe")
        rid = self.banco.criar_registro(texto, REF, [], [pessoa], [])
        pid = self.banco.pessoas()[0]["id"]
        outro = "A Sandraleia mandou um bolo."
        self.banco.criar_registro(outro, REF, [], [PessoaEncontrada(trecho="Sandraleia", inicio=2, fim=12,
                                                                     nome="Sandraleia", pessoa_id=pid, nova=False)], [])
        self.banco.apagar_registro(rid)
        self.assertEqual(self.banco.pessoa(pid)["nome"], "Sandraleia")

    def test_nome_editado_pelo_usuario_fica(self):
        mae = self.banco.criar_pessoa(None, "mãe")
        texto = "A mãe dela, Sandraleia, ligou."
        pessoa = PessoaEncontrada(trecho="A mãe dela, Sandraleia", inicio=0, fim=22, nome="Sandraleia", relacao="mãe")
        rid = self.banco.criar_registro(texto, REF, [], [pessoa], [])
        self.banco.atualizar_pessoa(mae, nome="Sandraleia")
        self.banco.apagar_registro(rid)
        self.assertEqual(self.banco.pessoa(mae)["nome"], "Sandraleia")

    def test_alertas_dispensados_do_item_apagado_saem(self):
        rid = self.criar_padrao()
        iid = self.banco.registro(rid)["itens"][0].id
        outro = self.criar_simples("Ela comentou da viagem.")
        outro_iid = self.banco.registro(outro)["itens"][0].id
        for chave in (f"evento:{iid}:2026-11-01:d30", f"data:{iid}:2026-11-01:d7", f"pendencia:{iid}:2026-10-03",
                      f"depois:{iid}:2026-10-02", f"estado:{iid}", f"evento:{outro_iid}:2026-10-20:d7",
                      f"evento:{iid}0:2026-10-20:d7", "namoro:2026-10-07:d7"):
            self.banco.dispensar_alerta(chave)
        self.banco.apagar_registro(rid)
        self.assertEqual(self.banco.alertas_dispensados(),
                         {f"evento:{outro_iid}:2026-10-20:d7", f"evento:{iid}0:2026-10-20:d7", "namoro:2026-10-07:d7"})
        self.banco.apagar_item(outro_iid)
        self.assertNotIn(f"evento:{outro_iid}:2026-10-20:d7", self.banco.alertas_dispensados())

    def test_alerta_do_aniversario_da_pessoa_apagada_sai(self):
        pid = self.banco.criar_pessoa("Mariana", "melhor amiga")
        self.banco.dispensar_alerta(f"pessoa:{pid}:2026-10-03:d3")
        self.banco.apagar_pessoa(pid)
        self.assertEqual(self.banco.alertas_dispensados(), set())


class TestRevisaoPessoasEItens(BaseBanco):
    def test_pessoa_nova_escolhida_nao_junta_com_homonimo(self):
        amiga = self.banco.criar_pessoa("Mariana", "melhor amiga")
        texto = "A Mariana do trabalho ligou."
        pessoa = {"trecho": "Mariana", "inicio": 2, "fim": 9, "nome": "Mariana", "relacao": "colega",
                  "pessoa_id": None, "nova": True, "nova_escolhida": True}
        item = novo_item(trecho=texto, pessoa="Mariana")
        rid = self.banco.criar_registro(texto, REF, [item], [pessoa], [])
        colegas = [p for p in self.banco.pessoas() if p["id"] != amiga]
        self.assertEqual([(p["nome"], p["relacao"]) for p in colegas], [("Mariana", "colega")])
        self.assertEqual(self.banco.registro(rid)["itens"][0].pessoa_id, colegas[0]["id"])
        # Sem a escolha, continua juntando com quem já existe.
        rid2 = self.banco.criar_registro("A Mariana ligou.", REF, [], [
            PessoaEncontrada(trecho="Mariana", inicio=2, fim=9, nome="Mariana")], [])
        self.assertIn(amiga, [p["id"] for p in self.banco.registro(rid2)["pessoas"]])

    def test_avo_e_avo_sao_duas_pessoas(self):
        avo = self.banco.criar_pessoa(None, "avô")
        texto = "A avó dela faz aniversário dia 20/11."
        item = novo_item(TipoItem.DATA_IMPORTANTE, "Aniversário da avó — 20/11", texto, pessoa="avó",
                         data_evento=date(2026, 11, 20), recorrente=True)
        rid = self.banco.criar_registro(texto, REF, [item], [PessoaEncontrada(trecho="A avó dela", inicio=0, fim=10,
                                                                             relacao="avó")], [])
        pid = self.banco.registro(rid)["itens"][0].pessoa_id
        self.assertNotEqual(pid, avo)
        self.assertEqual(self.banco.pessoa(pid)["relacao"], "avó")

    def test_acrescentar_item(self):
        rid = self.criar_simples("Ela pediu sushi no jantar e amou.")
        iid = self.banco.acrescentar_item(rid, novo_item(TipoItem.PREFERENCIA, "Gosta de sushi",
                                                         "Ela pediu sushi no jantar e amou.", alvo="sushi", polaridade=1))
        self.assertEqual(self.banco.item(iid).registro_id, rid)
        self.assertIn(("item", iid), [(a["tipo"], a["ref_id"]) for a in self.banco.buscar_texto("sushi")])
        with self.assertRaises(ValueError):
            self.banco.acrescentar_item(rid, novo_item(trecho="Trecho inventado."))
        with self.assertRaises(NaoEncontrado):
            self.banco.acrescentar_item(999, novo_item(trecho="x"))


class TestSemTemporariosEmDisco(BaseBanco):
    def test_temp_store_em_memoria(self):
        self.assertEqual(self.banco._con.execute("PRAGMA temp_store").fetchone()[0], 2)
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        self.assertEqual(copia._con.execute("PRAGMA temp_store").fetchone()[0], 2)

    def test_vacuum_de_banco_grande_nao_cria_arquivo_temporario(self):
        # Acima do cache de páginas (~2 MB), o VACUUM montava a cópia em %TEMP%\etilqs_*, em claro.
        pasta = tempfile.mkdtemp(prefix="entrenos-tmp-")
        self.addCleanup(shutil.rmtree, pasta, ignore_errors=True)
        antes = {k: os.environ.get(k) for k in ("TMP", "TEMP")}
        for k in antes:
            os.environ[k] = pasta
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in antes.items()])
        texto = "SegredoNoDisco-Quokka " + "x" * 8000
        for _ in range(450):
            self.banco.criar_registro(texto, REF, [], [], [])
        vistos: set[str] = set()
        parar = threading.Event()

        def vigiar() -> None:
            while not parar.is_set():
                vistos.update(os.listdir(pasta))

        vigia = threading.Thread(target=vigiar)
        vigia.start()
        try:
            dados = self.banco.para_bytes()
        finally:
            parar.set()
            vigia.join()
        self.assertGreater(len(dados), 3 * 1024 * 1024)
        self.assertEqual(vistos, set())


class TestBusca(BaseBanco):
    def test_sem_acento_acha_com_acento(self):
        rid = self.criar_padrao()
        achados = self.banco.buscar_texto("apresentacao")
        self.assertTrue(achados)
        self.assertIn(("registro", rid), [(a["tipo"], a["ref_id"]) for a in achados])

    def test_com_acento_maiuscula_e_prefixo(self):
        self.criar_padrao()
        for consulta in ("Apresentação", "APRESENT", "cirurg", "Tulipa"):
            with self.subTest(consulta=consulta):
                self.assertTrue(self.banco.buscar_texto(consulta))

    def test_termos_soltos_com_or(self):
        self.criar_padrao()
        self.criar_simples("Ela quer conhecer Lisboa.")
        tipos = {a["ref_id"] for a in self.banco.buscar_texto("tulipas lisboa") if a["tipo"] == "registro"}
        self.assertEqual(len(tipos), 2)

    def test_sintaxe_fts_nunca_lanca(self):
        self.criar_padrao()
        consultas = ['"', '""', "(", ")", "(tulipas", "tulipas)", "*", "tul*", "AND", "OR", "NOT",
                     "NEAR(a b)", "tulipas AND", "NOT tulipas", '"tulipas', "col:valor", "conteudo:tulipas",
                     "^tulipas", "-", "+", "'", "a'b", "×÷", "", "   ", "\x00", "tulipas\"*)(",
                     "{conteudo}: x", "a" * 5000, None, 123]
        for consulta in consultas:
            with self.subTest(consulta=consulta):
                self.assertIsInstance(self.banco.buscar_texto(consulta), list)

    def test_palavras_fts_viram_termos_comuns(self):
        rid = self.criar_simples("Ela ouviu rock and roll e not bad na festa.")
        self.assertIn(rid, [a["ref_id"] for a in self.banco.buscar_texto("AND")])
        self.assertIn(rid, [a["ref_id"] for a in self.banco.buscar_texto("NOT")])
        self.assertTrue(self.banco.buscar_texto('"rock" AND (roll)'))

    def test_itens_e_registros_com_score_ordenado(self):
        rid = self.criar_padrao()
        achados = self.banco.buscar_texto("tulipas")
        self.assertEqual({a["tipo"] for a in achados}, {"item", "registro"})
        tulipas = self.banco.registro(rid)["itens"][2].id
        self.assertIn(tulipas, [a["ref_id"] for a in achados if a["tipo"] == "item"])
        notas = [a["score"] for a in achados]
        self.assertEqual(notas, sorted(notas, reverse=True))
        self.assertTrue(all(isinstance(n, float) for n in notas))

    def test_limite(self):
        for i in range(8):
            self.criar_simples(f"Tulipas número {i}.")
        self.assertEqual(len(self.banco.buscar_texto("tulipas", limite=3)), 3)

    def test_so_stopwords_nao_acha_nada(self):
        self.criar_padrao()
        self.assertEqual(self.banco.buscar_texto("o que ela"), [])


class TestPessoas(BaseBanco):
    def test_crud(self):
        pid = self.banco.criar_pessoa("Mariana", "melhor amiga", apelidos=["Mari", "Mari", " "],
                                      observacoes="Conheceram na faculdade")
        self.assertEqual(self.banco.pessoa(pid), {
            "id": pid, "nome": "Mariana", "relacao": "melhor amiga", "apelidos": ["Mari"],
            "observacoes": "Conheceram na faculdade", "aniversario": None})
        self.banco.atualizar_pessoa(pid, aniversario=date(1998, 3, 3), apelidos=["Mari", "Nana"])
        p = self.banco.pessoa(pid)
        self.assertEqual(p["aniversario"], date(1998, 3, 3))
        self.assertEqual(p["apelidos"], ["Mari", "Nana"])
        self.assertEqual([x["id"] for x in self.banco.pessoas()], [pid])

    def test_pessoa_precisa_de_nome_ou_relacao(self):
        with self.assertRaises(ValueError):
            self.banco.criar_pessoa(None, "  ")
        pid = self.banco.criar_pessoa("Ana", None)
        with self.assertRaises(ValueError):
            self.banco.atualizar_pessoa(pid, nome="")
        with self.assertRaises(ValueError):
            self.banco.atualizar_pessoa(pid, idade=30)
        with self.assertRaises(NaoEncontrado):
            self.banco.atualizar_pessoa(999, nome="X")

    def test_apagar_pessoa_mantem_itens_com_o_nome(self):
        rid = self.criar_padrao()
        sandra = self.banco.pessoas()[0]["id"]
        self.banco.apagar_pessoa(sandra)
        self.assertIsNone(self.banco.pessoa(sandra))
        evento = self.banco.registro(rid)["itens"][0]
        self.assertEqual((evento.pessoa, evento.pessoa_id), ("Sandra", None))
        self.assertEqual(self.banco.registro(rid)["pessoas"], [])
        self.assertNotIn(evento.id, [i.id for i in self.banco.itens(pessoa_id=None)])
        with self.assertRaises(NaoEncontrado):
            self.banco.apagar_pessoa(sandra)

    def test_mencoes(self):
        primeiro = self.criar_padrao(REF - timedelta(days=10))
        sandra = self.banco.pessoas()[0]["id"]
        pessoa = PessoaEncontrada(trecho="Sandra", inicio=12, fim=18, nome="Sandra", pessoa_id=sandra, nova=False)
        segundo = self.criar_simples("Hoje cedo a Sandra mandou mensagem. Depois choveu.", REF,
                                     itens=[], pessoas=[pessoa])
        mencoes = self.banco.mencoes(sandra)
        self.assertEqual([m["registro_id"] for m in mencoes], [segundo, primeiro])
        self.assertEqual(mencoes[0]["trecho"], "Hoje cedo a Sandra mandou mensagem.")
        self.assertEqual(mencoes[0]["data_registro"], REF)
        self.assertEqual(mencoes[1]["trecho"], FRASE_MAE)
        self.assertEqual(len(self.banco.mencoes(sandra, limite=1)), 1)
        self.assertEqual(self.banco.mencoes(999), [])


class TestAlertasEHistorico(BaseBanco):
    def test_alertas_dispensados(self):
        self.assertEqual(self.banco.alertas_dispensados(), set())
        self.banco.dispensar_alerta("evento:12:d3")
        self.banco.dispensar_alerta("evento:12:d3")
        self.banco.dispensar_alerta("mesversario:2026-10-07")
        self.assertEqual(self.banco.alertas_dispensados(), {"evento:12:d3", "mesversario:2026-10-07"})
        with self.assertRaises(ValueError):
            self.banco.dispensar_alerta("  ")

    def test_historico_registra_acoes_sem_conteudo(self):
        rid = self.criar_padrao()
        pid = self.banco.criar_pessoa("Carolina", "prima")
        self.banco.atualizar_pessoa(pid, observacoes="Mora em Recife")
        item = self.banco.registro(rid)["itens"][0]
        self.banco.atualizar_item(item.id, resumo="Resumo com Recife")
        self.banco.salvar_config(nome_dela="Júlia")
        acoes = [(h["acao"], h["tabela"]) for h in self.banco.historico()]
        self.assertIn(("criar", "registros"), acoes)
        self.assertIn(("atualizar", "itens"), acoes)
        self.assertIn(("configurar", "config"), acoes)
        tudo = json.dumps(para_json(self.banco.historico()), ensure_ascii=False).lower()
        for palavra in ("carolina", "prima", "recife", "júlia", "sandra", "cirurgia"):
            self.assertNotIn(palavra, tudo)
        self.assertEqual(len(self.banco.historico(limite=2)), 2)


class TestBytesEExportacao(BaseBanco):
    def povoar(self) -> None:
        self.banco.salvar_config(nome_dela="Júlia", inicio_namoro=date(2026, 3, 7))
        rid = self.criar_padrao()
        self.criar_simples("Ela quer conhecer Lisboa.", REF - timedelta(days=2),
                           itens=[novo_item(TipoItem.DESEJO, "Quer conhecer Lisboa", "Ela quer conhecer Lisboa.",
                                            categorias=["Sonhos", "Viagens"], alvo="Lisboa")])
        self.banco.criar_pessoa("Mariana", "melhor amiga", apelidos=["Mari"])
        self.banco.dispensar_alerta("evento:1:d3")
        self.banco.resolver_relacao(self.banco.relacoes()[0].id, "descartada")
        self.banco.atualizar_item(self.banco.registro(rid)["itens"][2].id, status="resolvido")

    @staticmethod
    def sem_data_de_exportacao(dados: dict) -> dict:
        return {k: v for k, v in dados.items() if k != "exportado_em"}

    def test_de_bytes_preserva_tudo(self):
        self.povoar()
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.assertEqual(self.sem_data_de_exportacao(copia.exportar()),
                         self.sem_data_de_exportacao(self.banco.exportar()))
        self.assertEqual(copia.buscar_texto("lisboa"), self.banco.buscar_texto("lisboa"))
        self.assertEqual(copia.alertas_dispensados(), {"evento:1:d3"})
        self.assertEqual(copia.itens(status=None), self.banco.itens(status=None))

    def test_de_bytes_continua_gravando(self):
        self.povoar()
        salvos = []
        copia = Banco.de_bytes(self.banco.para_bytes(), ao_salvar=salvos.append)
        rid = copia.criar_registro("Registro depois de carregar.", REF, [], [], [])
        self.assertEqual(len(salvos), 1)
        self.assertEqual(copia.registro(rid)["texto"], "Registro depois de carregar.")
        self.assertEqual(copia.buscar_texto("carregar")[0]["ref_id"], rid)

    def test_de_bytes_invalido(self):
        for dados in (b"", b"lixo que nao e banco", b"SQLite format 3\x00" + b"\x00" * 50):
            with self.subTest(dados=dados[:20]), self.assertRaises(ValueError):
                Banco.de_bytes(dados)

    def test_de_bytes_de_outro_sqlite(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("CREATE TABLE outra (x)")
        with self.assertRaises(ValueError):
            Banco.de_bytes(con.serialize())

    def test_de_bytes_de_versao_mais_nova(self):
        self.banco._con.execute(f"PRAGMA user_version = {ESQUEMA_VERSAO + 1}")
        with self.assertRaises(ValueError):
            Banco.de_bytes(self.banco.para_bytes())

    def test_exportar_completo(self):
        self.povoar()
        dados = self.banco.exportar()
        texto = json.dumps(dados, ensure_ascii=False)       # já vem pronto para JSON
        self.assertIn("tulipas", texto)
        self.assertEqual(dados["app"], "entre-nos")
        self.assertEqual(dados["versao"], config.VERSAO)
        self.assertEqual(dados["config"]["nome_dela"], "Júlia")
        self.assertEqual(dados["config"]["inicio_namoro"], "2026-03-07")
        self.assertEqual(len(dados["registros"]), 2)
        self.assertEqual(len(dados["pessoas"]), 2)
        principal = next(r for r in dados["registros"] if r["texto"] == TEXTO)
        self.assertEqual(len(principal["itens"]), 3)
        self.assertEqual(principal["itens"][0]["data_evento"], "2026-11-01")
        self.assertEqual(principal["pessoas"][0]["nome"], "Sandra")
        self.assertEqual(principal["relacoes"][0]["status"], "descartada")
        self.assertEqual(dados["alertas_dispensados"][0]["chave"], "evento:1:d3")
        self.assertTrue(dados["historico"])
        self.assertEqual([r["data_registro"] for r in dados["registros"]], ["2026-09-28", "2026-09-30"])

    def test_ida_e_volta_pelo_cofre(self):
        tmp = tempfile.mkdtemp(prefix="entrenos-teste-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        antigo = os.environ.get("ENTRENOS_DADOS")
        os.environ["ENTRENOS_DADOS"] = tmp
        self.addCleanup(lambda: os.environ.pop("ENTRENOS_DADOS") if antigo is None
                        else os.environ.__setitem__("ENTRENOS_DADOS", antigo))
        self.povoar()
        cofre = Cofre(config.pasta_dados())
        cofre.hoje = lambda: REF
        cofre.criar("tulipas amarelas", self.banco.para_bytes())
        self.banco.ao_salvar = cofre.salvar
        self.banco.criar_registro("Ela contou do show de sábado.", REF, [], [], [])
        conteudo = (config.pasta_dados() / config.ARQUIVO_COFRE).read_bytes()
        for palavra in ("tulipas", "Sandra", "Lisboa", "show de sábado"):
            self.assertNotIn(palavra.encode("utf-8"), conteudo)
        dados = Cofre(config.pasta_dados()).abrir("tulipas amarelas")
        recarregado = Banco.de_bytes(dados)
        self.assertEqual(self.sem_data_de_exportacao(recarregado.exportar()),
                         self.sem_data_de_exportacao(self.banco.exportar()))
        self.assertEqual(recarregado.buscar_texto("show")[0]["tipo"], "registro")


class TestConcorrencia(BaseBanco):
    def test_escritas_de_varias_threads(self):
        erros = []

        def trabalhar(n: int) -> None:
            try:
                for i in range(5):
                    rid = self.banco.criar_registro(f"Registro {n}-{i} sobre tulipas.", REF,
                                                    [novo_item(trecho=f"Registro {n}-{i} sobre tulipas.")], [], [])
                    self.banco.buscar_texto("tulipas")
                    self.banco.registro(rid)
            except Exception as erro:  # noqa: BLE001 - o teste só quer saber se houve erro
                erros.append(erro)

        threads = [threading.Thread(target=trabalhar, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(erros, [])
        self.assertEqual(self.contar("registros"), 40)
        self.assertEqual(self.contar("itens"), 40)
        self.assertEqual(len(self.salvos), 40)
        self.assertEqual(Banco.de_bytes(self.salvos[-1]).registros(limite=100).__len__(), 40)


if __name__ == "__main__":
    unittest.main()
