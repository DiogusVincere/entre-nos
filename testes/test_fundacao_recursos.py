"""A fundação dos recursos (DESIGN.md, seção 20.0).

Um recurso FALSO de teste usa todos os ganchos (esquema com duas versões, extrair, gravar_extras,
marca_privado, rotas, cartoes_hoje, exportar, importar, ao_apagar_registro e fora_da_ia) e é
trocado no lugar da lista fixa por `recursos.usando(...)`. Também: o registro tolerante da lista fixa
(os nove esqueletos), a cadeia de migrações 1 -> 4 com bancos montados pelo código de cada versão,
os ajudantes genéricos do Banco e o que eles recusam, a IA que nunca recebe extras nem o que fica
fora dela, o anti-excesso central do Hoje e a página (index.html, web/telas/, servidor).

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026, scrypt pequeno.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from entrenos import alertas, cofre, motor, nucleo, ponte, recursos, servidor
from entrenos import banco as banco_mod
from entrenos import texto as texto_mod
from entrenos.banco import Banco, NaoEncontrado, OperacaoProibida
from entrenos.modelos import Alerta, Certeza, Item, Resposta, TipoItem, para_json
from testes.test_servidor import PIN, REF, Relogio, ServidorDeTeste

RAIZ = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
WEB = RAIZ / "web"
TEXTO = "Prometi levar ela ao cinema na sexta. Segredo: ela anda com muita cólica. Ela adora tulipas."
PROMESSA = "Prometi levar ela ao cinema na sexta."
SEGREDO = "Segredo: ela anda com muita cólica."
DATA = "2026-09-30"
IA = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": "modelo-de-teste", "tipo": "ollama",
      "chave": None}
_PADRAO = object()

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-fundacao-env-")
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


# ---------------------------------------------------------------- o recurso falso

def recurso_falso(nome: str = "falso", versao: int = 2, **trocas) -> types.ModuleType:
    """Um recurso que usa todos os ganchos. `trocas` substitui qualquer atributo (um gancho que
    levanta, um esquema ruim...). `chamadas` anota o que o núcleo chamou."""
    m = types.ModuleType(f"testes.recurso_{nome}")
    tabela = f"{nome}_coisas"
    m.NOME = nome
    m.ROTULO = "Recurso de teste"
    m.TABELA = tabela
    m.ESQUEMA = [
        f"CREATE TABLE {tabela} (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, resumo TEXT NOT NULL,"
        f" trecho TEXT NOT NULL, registro_id INTEGER, criado_em TEXT);"
        f" CREATE INDEX {tabela}_registro ON {tabela}(registro_id)",
        f"ALTER TABLE {tabela} ADD COLUMN feito INTEGER NOT NULL DEFAULT 0",
    ][:versao]
    m.ESQUEMA_VERSAO = versao
    m.TIPOS_EXTRAS = ("promessa", "segredo")
    m.chamadas = []

    def extrair(texto, referencia, pessoas, datas, itens, conhecimento):
        m.chamadas.append(("extrair", len(itens)))
        if itens:
            itens[0].resumo = "MEXI"           # é uma cópia: a proposta das regras não muda
        saida = []
        for _, _, frase in texto_mod.frases(texto):
            if frase.startswith("Prometi"):
                saida.append({"tipo": "promessa", "resumo": "Levar ao cinema", "trecho": frase, "prazo": referencia,
                              "_interno": "some"})
            if frase.startswith("Segredo"):
                saida.append({"tipo": "segredo", "resumo": "", "trecho": frase})
        saida.append({"tipo": "promessa", "resumo": "inventado", "trecho": "isso não está no texto"})
        saida.append({"tipo": "desconhecido", "resumo": "x", "trecho": texto[:7]})
        saida.append("nem é um dict")
        return saida

    def gravar_extras(banco, registro_id, extras, referencia):
        m.chamadas.append(("gravar", registro_id, [e["tipo"] for e in extras], referencia))
        for e in extras:
            banco.inserir(tabela, {"tipo": e["tipo"], "resumo": e["resumo"], "trecho": e["trecho"],
                                   "registro_id": registro_id, "criado_em": referencia})

    def listar(nucleo_, pedido, banco):
        return banco.consultar(f"SELECT id, tipo, resumo, registro_id FROM {tabela} ORDER BY id")

    def apagar(nucleo_, pedido, banco):
        banco.apagar(tabela, pedido.id)
        nucleo_.limpar_copias(lambda copia: copia.apagar(tabela, pedido.id))
        return {}

    def explodir(nucleo_, pedido, banco):
        raise RuntimeError("Mariana adora tulipas")

    def recusar(nucleo_, pedido, banco):
        raise ValueError("Isso não dá para guardar.")

    def livre(nucleo_, pedido, banco):
        with nucleo_.trava:
            return {"registros": len(banco.registros())}

    def cartoes_hoje(banco, referencia):
        return [
            recursos.cartao("i1", "importante", "Importante 1", relevancia=90),
            recursos.cartao("i2", "importante", "Importante 2", relevancia=95, dados={"quando": referencia}),
            *[recursos.cartao(f"l{n}", "lembrete", f"Lembrete {n}", relevancia=50 + n, tipo="lembrete_falso")
              for n in range(4)],
            recursos.cartao("s1", "sugestao", "Talvez a primeira", relevancia=60),
            recursos.cartao("s2", "sugestao", "Talvez a segunda", relevancia=70, registro_ids=[1]),
            {"chave": "", "nivel": "lembrete", "texto": "sem chave"},
            {"chave": "x", "nivel": "urgente", "texto": "nível que não existe"},
            Alerta(chave="alerta", nivel="lembrete", texto="Do tipo Alerta", porque="", relevancia=10),
        ]

    def exportar(banco):
        return {"coisas": banco.consultar(f"SELECT tipo, resumo, trecho, registro_id FROM {tabela} ORDER BY id")}

    def importar(banco, dados, mapa):
        novos = 0
        for c in dados.get("coisas", []):
            rid = mapa.get(c["registro_id"])
            if rid is None or banco.consultar(f"SELECT 1 FROM {tabela} WHERE registro_id = ? AND trecho = ?",
                                              (rid, c["trecho"])):
                continue
            banco.inserir(tabela, {"tipo": c["tipo"], "resumo": c["resumo"], "trecho": c["trecho"], "registro_id": rid})
            novos += 1
        return {"novos": novos, "texto": f"{novos} coisas de teste"}

    def ao_apagar_registro(banco, registro_id):
        m.chamadas.append(("apagar", registro_id))
        banco.esquecer_alertas(f"{nome}:r{registro_id}:")

    def fora_da_ia(banco):
        return {l["registro_id"] for l in banco.consultar(
            f"SELECT registro_id FROM {tabela} WHERE tipo = 'segredo' AND registro_id IS NOT NULL")}

    m.extrair = extrair
    m.gravar_extras = gravar_extras
    m.marca_privado = lambda extras: any(e.get("tipo") == "segredo" for e in extras)
    m.rotas = lambda: [recursos.rota("GET", "coisas", listar), recursos.rota("DELETE", "coisas/{id}", apagar),
                       recursos.rota("POST", "explodir", explodir), recursos.rota("POST", "recusar", recusar),
                       recursos.rota("GET", "livre", livre, acesso=recursos.LIVRE)]
    m.cartoes_hoje = cartoes_hoje
    m.exportar = exportar
    m.importar = importar
    m.ao_apagar_registro = ao_apagar_registro
    m.fora_da_ia = fora_da_ia
    for chave, valor in trocas.items():
        setattr(m, chave, valor)
    return m


def explodir(*_a, **_k):
    raise RuntimeError("Mariana adora tulipas e o PIN é pizza de sexta")


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


def coletar_log(teste: unittest.TestCase) -> Coletor:
    coletor = Coletor()
    logging.getLogger("entrenos").addHandler(coletor)
    teste.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
    return coletor


def tabela_existe(banco: Banco, nome: str) -> bool:
    return banco._con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (nome,)).fetchone() is not None


def versao_do_recurso(banco: Banco, nome: str) -> int | None:
    linha = banco._con.execute("SELECT versao FROM recursos_esquema WHERE nome = ?", (nome,)).fetchone()
    return linha[0] if linha else None


# ---------------------------------------------------------------- o registro (lista fixa)

class TestRegistro(unittest.TestCase):
    def test_lista_fixa_com_os_nove_recursos(self):
        # Os nove nasceram esqueletos (só o NOME) e foram escritos em paralelo; desde a integração
        # (07/10/2026) todos cumprem o contrato inteiro de 20.0.1.
        self.assertEqual(recursos.NOMES, ("perguntas", "presentes", "promessas", "rapida", "lugares", "marcos",
                                          "semana", "ciclo", "eu"))
        self.assertEqual(recursos.nomes_carregados(), list(recursos.NOMES))
        self.assertEqual(recursos.falhas(), {})
        arquivos = sorted(p.stem for p in (RAIZ / "entrenos" / "recursos").glob("*.py") if p.stem != "__init__")
        self.assertEqual(arquivos, sorted(recursos.NOMES))
        for modulo in recursos.carregados():
            with self.subTest(recurso=modulo.NOME):
                self.assertIsInstance(getattr(modulo, "ROTULO", None), str)
                passos = recursos.esquema(modulo)
                self.assertTrue(passos)                                          # cada um tem tabelas
                self.assertEqual(getattr(modulo, "ESQUEMA_VERSAO", len(passos)), len(passos))
                tipos = getattr(modulo, "TIPOS_EXTRAS", None)
                if callable(getattr(modulo, "extrair", None)):
                    self.assertTrue(tipos, "quem extrai declara TIPOS_EXTRAS (20.0.1)")
                for gancho in recursos.GANCHOS:
                    valor = getattr(modulo, gancho, None)
                    self.assertTrue(valor is None or callable(valor), gancho)
                if callable(getattr(modulo, "rotas", None)):
                    for r in modulo.rotas():
                        self.assertIsInstance(r, recursos.RotaRecurso)
        # Quem deixa registros fora da IA (o ciclo e a área Eu, seção 20) e quem é discreto (o ciclo).
        self.assertEqual({n for n, _ in recursos.com_gancho("fora_da_ia")}, {"perguntas", "ciclo", "eu"})
        self.assertEqual({n for n, _ in recursos.com_gancho("discretos")}, {"ciclo"})

    def test_diario_novo_tem_as_tabelas_dos_recursos_e_sem_eles_nada_muda(self):
        b = Banco.novo()
        self.addCleanup(b.fechar)
        versoes = dict(b._con.execute("SELECT nome, versao FROM recursos_esquema").fetchall())
        self.assertEqual(versoes, {m.NOME: len(recursos.esquema(m)) for m in recursos.carregados()})
        rotas = nucleo.rotas_dos_recursos()
        self.assertTrue(rotas)
        for r in rotas:
            with self.subTest(rota=r.padrao.pattern):
                self.assertTrue(any(f"/api/{n}" in r.padrao.pattern.replace("\\", "") for n in recursos.NOMES))
        self.assertEqual(set(b.exportar()["recursos"]), {n for n, _ in recursos.com_gancho("exportar")})
        # Sem nenhum recurso (a lista trocada por uma vazia), o diário sai como antes da seção 20.
        with recursos.usando():
            vazio = Banco.novo()
            self.addCleanup(vazio.fechar)
            self.assertEqual(vazio._con.execute("SELECT count(*) FROM recursos_esquema").fetchone()[0], 0)
            self.assertEqual(nucleo.rotas_dos_recursos(), ())
            self.assertEqual(vazio.exportar()["recursos"], {})

    def test_recurso_que_nao_abre_nao_derruba_o_app(self):
        log = coletar_log(self)
        real = recursos.importlib.import_module

        def importar(nome, *a, **k):
            if nome == "entrenos.recursos.presentes":
                raise SyntaxError("texto do usuário Mariana")
            return real(nome, *a, **k)

        self.addCleanup(recursos.recarregar)
        with mock.patch.object(recursos.importlib, "import_module", importar):
            recursos.recarregar()
        self.assertEqual(recursos.falhas(), {"presentes": "SyntaxError"})
        self.assertNotIn("presentes", recursos.nomes_carregados())
        self.assertEqual(len(recursos.nomes_carregados()), 8)
        self.assertTrue(any("presentes" in m and "SyntaxError" in m for m in log.mensagens))
        self.assertFalse(any("Mariana" in m for m in log.mensagens))
        # O diário abre e funciona; o que fica fora da IA é que não dá para saber (nada vai).
        b = Banco.novo()
        self.addCleanup(b.fechar)
        b.criar_registro("Ela adora café.", REF, [], [], [])
        with self.assertRaises(recursos.RecursoIndisponivel):
            recursos.fora_da_ia(b)

    def test_contrato_quebrado_fica_de_fora(self):
        ruins = {
            "esquema_sem_versao": recurso_falso("a", ESQUEMA_VERSAO=5),
            "gancho_que_nao_e_funcao": recurso_falso("b", extrair="não é função"),
            "tipos_invalidos": recurso_falso("c", TIPOS_EXTRAS="promessa"),
            "nome_invalido": recurso_falso("d", NOME="Com Espaço"),
        }
        log = coletar_log(self)
        with recursos.usando(recurso_falso(), *ruins.values()):
            self.assertEqual(recursos.nomes_carregados(), ["falso"])
            self.assertEqual(set(recursos.falhas()), {"a", "b", "c", "Com Espaço"})
        self.assertEqual(recursos.nomes_carregados(), list(recursos.NOMES))      # a lista fixa volta
        self.assertTrue(log.mensagens)


# ---------------------------------------------------------------- esquema: cadeia 1 -> 4 e versões dos recursos

def banco_na_versao(versao: int) -> sqlite3.Connection:
    """Um banco como a versão `versao` gravava, montado pelas migrações daquela época."""
    con = sqlite3.connect(":memory:", isolation_level=None)
    for v in range(versao):
        passo = banco_mod._MIGRACOES[v]
        if callable(passo):
            con.execute("BEGIN")
            passo(con)
            con.execute(f"PRAGMA user_version = {v + 1}")
            con.execute("COMMIT")
        else:
            con.executescript(f"BEGIN;\n{passo}\nPRAGMA user_version = {v + 1};\nCOMMIT;")
    return con


class TestEsquema(unittest.TestCase):
    def test_esquema_central_e_5(self):
        # v4 = recursos_esquema (seção 20.0); v5 = as tabelas da sincronização (seção 22).
        self.assertEqual(banco_mod.ESQUEMA_VERSAO, 5)
        self.assertEqual(len(banco_mod._MIGRACOES), 5)
        b = Banco.novo()
        self.addCleanup(b.fechar)
        self.assertEqual(b._con.execute("PRAGMA user_version").fetchone()[0], 5)
        self.assertEqual({l[1] for l in b._con.execute("PRAGMA table_info(recursos_esquema)")}, {"nome", "versao"})
        tabelas = {l[0] for l in b._con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual({"sync_ids", "sync_linhas", "sync_lapides", "sync_aparelhos", "sync_conflitos"}, tabelas)
        # São do núcleo: nenhum recurso grava nelas.
        self.assertTrue(all(banco_mod._tabela_central(t) for t in tabelas if t.startswith("sync_")))

    def test_cadeia_de_1_a_5_com_bancos_das_versoes_antigas(self):
        falso = recurso_falso()
        for versao in (1, 2, 3):
            with self.subTest(versao=versao):
                con = banco_na_versao(versao)
                self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], versao)
                colunas = "criado_em, data_registro, texto" + (", privado" if versao >= 3 else "")
                valores = "'2026-09-01T10:00:00', '2026-09-01', 'Ela adora tulipas.'" + (", 1" if versao >= 3 else "")
                con.execute(f"INSERT INTO registros ({colunas}) VALUES ({valores})")
                con.execute("INSERT INTO busca_fts (rowid, conteudo, tipo, ref_id) VALUES (2, 'Ela adora tulipas.', "
                            "'registro', 1)")
                con.execute("INSERT INTO itens (registro_id, tipo, resumo, trecho, certeza, criado_em, atualizado_em)"
                            " VALUES (1, 'preferencia', 'Gosta de tulipas', 'Ela adora tulipas.', 'observado',"
                            " '2026-09-01T10:00:00', '2026-09-01T10:00:00')")
                con.execute("INSERT INTO pessoas (nome, relacao, criado_em) VALUES ('Sandra', 'mãe', '2026-09-01T10:00:00')")
                if versao < 3:
                    con.execute("INSERT INTO config (chave, valor) VALUES ('motor', '\"ollama\"'), "
                                "('motor_modelo', '\"qwen3\"')")
                dados = con.serialize()
                con.close()
                with recursos.usando(falso):
                    b = Banco.de_bytes(dados)
                    self.addCleanup(b.fechar)
                    self.assertTrue(b.sujo)                                   # migrou: falta salvar
                    self.assertEqual(b._con.execute("PRAGMA user_version").fetchone()[0], 5)
                    self.assertEqual(versao_do_recurso(b, "falso"), 2)       # o recurso entrou junto
                    self.assertIn("feito", {l[1] for l in b._con.execute("PRAGMA table_info(falso_coisas)")})
                    registro = b.registro(1)
                    self.assertEqual(registro["texto"], "Ela adora tulipas.")
                    self.assertEqual(registro["privado"], versao >= 3)
                    self.assertEqual([i.resumo for i in registro["itens"]], ["Gosta de tulipas"])
                    self.assertEqual(b.pessoas()[0]["nome"], "Sandra")
                    self.assertTrue(b.buscar_texto("tulipas"))
                    if versao < 3:
                        self.assertEqual(b.config()["ia_principal"]["modelo"], "qwen3")
                    b.conferir_integridade()
                    # E segue gravando, e volta igual do arquivo.
                    rid = b.criar_registro("Ela adora café.", REF, [], [], [])
                    b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "r", "trecho": "t", "registro_id": rid})
                    copia = Banco.de_bytes(b.para_bytes())
                    self.addCleanup(copia.fechar)
                    self.assertFalse(copia.sujo)
                    self.assertEqual(len(copia.consultar("SELECT * FROM falso_coisas")), 1)
                    copia.conferir_integridade()

    def test_recurso_migra_da_versao_dele(self):
        with recursos.usando(recurso_falso(versao=1)):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            self.assertEqual(versao_do_recurso(b, "falso"), 1)
            self.assertNotIn("feito", {l[1] for l in b._con.execute("PRAGMA table_info(falso_coisas)")})
            b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "r", "trecho": "t"})
            dados = b.para_bytes()
        with recursos.usando(recurso_falso(versao=2)):
            novo = Banco.de_bytes(dados)
            self.addCleanup(novo.fechar)
            self.assertTrue(novo.sujo)
            self.assertEqual(versao_do_recurso(novo, "falso"), 2)
            self.assertEqual(novo.consultar("SELECT resumo, feito FROM falso_coisas"), [{"resumo": "r", "feito": 0}])
            dados_v2 = novo.para_bytes()
        # Dados de uma versão mais nova do recurso: como no esquema central, não abre.
        with recursos.usando(recurso_falso(versao=1)):
            with self.assertRaises(ValueError) as erro:
                Banco.de_bytes(dados_v2)
            self.assertEqual(str(erro.exception), banco_mod.DADOS_DE_VERSAO_NOVA)

    def test_recurso_nao_mexe_no_que_e_do_nucleo(self):
        ruins = {
            "indice_central": "CREATE TABLE ruim_t (id INTEGER PRIMARY KEY); CREATE INDEX ruim_i ON registros(texto)",
            "gatilho": "CREATE TABLE ruim_t (id INTEGER PRIMARY KEY); CREATE TRIGGER ruim_g AFTER INSERT ON registros"
                       " BEGIN UPDATE registros SET privado = 0; END",
            "gatilho_proprio": "CREATE TABLE ruim_t (id INTEGER PRIMARY KEY);"
                               " CREATE TRIGGER ruim_g AFTER INSERT ON ruim_t BEGIN SELECT 1; END",
            "visao": "CREATE VIEW ruim_v AS SELECT * FROM registros",
            "coluna_central": "ALTER TABLE itens ADD COLUMN ruim TEXT",
            "apagar_central": "DROP TABLE historico",
            "pragma": "PRAGMA writable_schema = 1",
            "transacao": "CREATE TABLE ruim_t (id INTEGER PRIMARY KEY); COMMIT",
            "dados_centrais": "CREATE TABLE ruim_t (id INTEGER PRIMARY KEY); DELETE FROM registros",
            "sequencia": "UPDATE sqlite_sequence SET seq = 0 WHERE name = 'registros'",
            "virtual": "CREATE VIRTUAL TABLE ruim_fts USING fts5(a)",
            "temporaria": "CREATE TEMP TABLE ruim_temp (a INTEGER)",
            "anexar": "ATTACH DATABASE ':memory:' AS outro",
            "funcao": lambda con: con.execute("DELETE FROM registros"),
        }
        base = Banco.novo()
        base.criar_registro("Ela adora café.", REF, [], [], [])
        dados = base.para_bytes()
        esperados = {tuple(l) for l in base._con.execute("SELECT type, name, sql FROM sqlite_master")}
        base.fechar()
        log = coletar_log(self)
        for nome, passo in ruins.items():
            with self.subTest(caso=nome), recursos.usando(recurso_falso("ruim", ESQUEMA=[passo], ESQUEMA_VERSAO=1)):
                b = Banco.de_bytes(dados)
                try:
                    self.assertIsNone(versao_do_recurso(b, "ruim"))
                    self.assertEqual({tuple(l) for l in b._con.execute("SELECT type, name, sql FROM sqlite_master")},
                                     esperados)
                    self.assertEqual(len(b.registros()), 1)
                    self.assertEqual(b.maiores_ids()["registros"], 1)
                    self.assertNotIn("outro", {l[1] for l in b._con.execute("PRAGMA database_list")})
                finally:
                    b.fechar()
        self.assertTrue(all("ruim" in m for m in log.mensagens if "esquema" in m))
        self.assertFalse(any("registros" in m for m in log.mensagens))     # nada do SQL vai para o log

    def test_integridade_aceita_as_tabelas_dos_recursos(self):
        with recursos.usando(recurso_falso()):
            b = Banco.novo()
            b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "r", "trecho": "t"})
            dados = b.para_bytes()
            b.fechar()
            copia = Banco.de_bytes(dados)
            copia.conferir_integridade()
            copia.fechar()
        # Sem o recurso (e sem nenhum que falhou), a tabela a mais é estranha.
        with recursos.usando():
            copia = Banco.de_bytes(dados)
            self.addCleanup(copia.fechar)
            with self.assertRaises(ValueError):
                copia.conferir_integridade()
        # Com um recurso que não abriu aqui, as tabelas dele são aceitas; gatilho nunca.
        with recursos.usando(recurso_falso("quebrado", extrair="não é função")):
            self.assertTrue(recursos.falhas())
            copia = Banco.de_bytes(dados)
            copia.conferir_integridade()
            copia.fechar()
            con = sqlite3.connect(":memory:")
            con.deserialize(dados)
            con.execute("CREATE TRIGGER escondido AFTER INSERT ON registros BEGIN UPDATE registros SET privado = 0; END")
            con.commit()
            com_gatilho = con.serialize()
            con.close()
            suspeito = Banco.de_bytes(com_gatilho)
            self.addCleanup(suspeito.fechar)
            with self.assertRaises(ValueError):
                suspeito.conferir_integridade()

    def test_ids_das_tabelas_dos_recursos_nao_se_repetem(self):
        with recursos.usando(recurso_falso()):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            for _ in range(3):
                b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "r", "trecho": "t"})
            self.assertEqual(b.maiores_ids()["falso_coisas"], 3)
            outro = Banco.novo()
            self.addCleanup(outro.fechar)
            outro.garantir_ids_acima(b.maiores_ids())
            self.assertEqual(outro.inserir("falso_coisas", {"tipo": "promessa", "resumo": "r", "trecho": "t"}), 4)


# ---------------------------------------------------------------- ajudantes genéricos do Banco

class TestAjudantes(unittest.TestCase):
    def setUp(self):
        self.enterContext(recursos.usando(recurso_falso()))
        self.salvos: list[bytes] = []
        self.banco = Banco.novo(ao_salvar=self.salvos.append)
        self.addCleanup(self.banco.fechar)
        self.rid = self.banco.criar_registro("Ela adora café.", REF, [], [], [])
        self.salvos.clear()

    def test_inserir_atualizar_apagar_com_historico_sem_conteudo(self):
        b = self.banco
        cid = b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "Segredo da Mariana", "trecho": "t",
                                          "registro_id": self.rid, "criado_em": REF})
        self.assertEqual(len(self.salvos), 1)                      # cada escrita salva
        linha = b.consultar("SELECT * FROM falso_coisas WHERE id = ?", (cid,))[0]
        self.assertEqual((linha["criado_em"], linha["feito"]), (DATA, 0))
        b.atualizar("falso_coisas", cid, {"feito": True, "resumo": {"lista": [1, date(2026, 10, 1)]}})
        linha = b.consultar("SELECT feito, resumo FROM falso_coisas WHERE id = ?", (cid,))[0]
        self.assertEqual(linha["feito"], 1)
        self.assertEqual(json.loads(linha["resumo"]), {"lista": [1, "2026-10-01"]})
        b.apagar("falso_coisas", cid)
        self.assertEqual(b.consultar("SELECT * FROM falso_coisas"), [])
        with self.assertRaises(NaoEncontrado):
            b.apagar("falso_coisas", cid)
        with self.assertRaises(NaoEncontrado):
            b.atualizar("falso_coisas", cid, {"feito": 1})
        historico = [h["resumo"] for h in b.historico()]
        self.assertEqual(historico[:3], [f"falso_coisas #{cid} apagado", f"falso_coisas #{cid} atualizado: feito, resumo",
                                         f"falso_coisas #{cid} criado"])
        self.assertFalse(any("Mariana" in h for h in historico))

    def test_consultar_le_tudo_mas_nao_escreve(self):
        b = self.banco
        self.assertEqual(b.consultar("SELECT texto FROM registros"), [{"texto": "Ela adora café."}])
        self.assertTrue(b.consultar("SELECT ref_id FROM busca_fts WHERE busca_fts MATCH 'cafe'"))
        self.assertEqual(b.consultar("SELECT value FROM json_each('[1, 2]')"), [{"value": 1}, {"value": 2}])
        for sql in ("DELETE FROM registros", "WITH x AS (SELECT 1) DELETE FROM falso_coisas",
                    "INSERT INTO falso_coisas (tipo, resumo, trecho) VALUES ('a', 'b', 'c')", "PRAGMA user_version = 9",
                    "CREATE TABLE z (a)"):
            with self.subTest(sql=sql), self.assertRaises(OperacaoProibida):
                b.consultar(sql)
        self.assertEqual(len(b.registros()), 1)

    def test_executar_so_nas_tabelas_do_recurso(self):
        b = self.banco
        b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "r", "trecho": "t", "registro_id": self.rid})
        self.assertEqual(b.executar("UPDATE falso_coisas SET feito = 1 WHERE registro_id = ?", (self.rid,)), 1)
        proibidos = ["DELETE FROM registros", "UPDATE itens SET resumo = 'x'", "INSERT INTO config VALUES ('a', '1')",
                     "DELETE FROM historico", "DELETE FROM alertas_dispensados", "UPDATE sqlite_sequence SET seq = 0",
                     "INSERT INTO busca_fts (conteudo) VALUES ('x')", "DELETE FROM recursos_esquema", "COMMIT",
                     "BEGIN", "SAVEPOINT x", "PRAGMA user_version = 9", "CREATE TABLE z (a)", "DROP TABLE falso_coisas",
                     "CREATE TRIGGER t AFTER INSERT ON falso_coisas BEGIN SELECT 1; END", "ATTACH ':memory:' AS x",
                     "VACUUM"]
        for sql in proibidos:
            with self.subTest(sql=sql), self.assertRaises((OperacaoProibida, sqlite3.Error)):
                b.executar(sql)
        self.assertFalse(b._con.in_transaction)
        self.assertEqual(len(b.registros()), 1)
        for tabela in ("registros", "busca_fts", "sqlite_master", "falso coisas", "nao_existe", 3):
            with self.subTest(tabela=tabela), self.assertRaises(OperacaoProibida):
                b.inserir(tabela, {"texto": "x"})
        with self.assertRaises(OperacaoProibida):
            b.inserir("falso_coisas", {"id": 99, "tipo": "a", "resumo": "b", "trecho": "c"})
        with self.assertRaises(OperacaoProibida):
            b.inserir("falso_coisas", {"tipo; DROP": "a"})

    def test_transacao_e_tudo_ou_nada_e_salva_uma_vez(self):
        b = self.banco
        with b.transacao():
            b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "1", "trecho": "t"})
            b.executar("UPDATE falso_coisas SET feito = 1")
            b.criar_registro("Ela adora chá.", REF, [], [], [])
        self.assertEqual(len(self.salvos), 1)
        with self.assertRaises(RuntimeError):
            with b.transacao():
                b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "2", "trecho": "t"})
                raise RuntimeError("desfaz tudo")
        self.assertEqual(len(self.salvos), 1)
        self.assertEqual([l["resumo"] for l in b.consultar("SELECT resumo FROM falso_coisas")], ["1"])
        # Dentro de outra, um erro desfaz só a parte de dentro (ponto de salvamento).
        with b.transacao():
            b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "3", "trecho": "t"})
            try:
                with b.transacao():
                    b.inserir("falso_coisas", {"tipo": "promessa", "resumo": "4", "trecho": "t"})
                    raise RuntimeError("só esta parte")
            except RuntimeError:
                pass
        self.assertEqual([l["resumo"] for l in b.consultar("SELECT resumo FROM falso_coisas ORDER BY id")], ["1", "3"])
        self.assertEqual(len(self.salvos), 2)

    def test_esquecer_alertas_so_do_proprio_recurso(self):
        b = self.banco
        for chave in ("falso:r1:2026-10-01", "falso:r2:x", "evento:1:2026-10-01:d7", "falsone:r1"):
            b.dispensar_alerta(chave)
        self.assertEqual(b.esquecer_alertas("falso:r1:"), 1)
        self.assertEqual(b.alertas_dispensados(), {"falso:r2:x", "evento:1:2026-10-01:d7", "falsone:r1"})
        for prefixo in ("evento:", "", "falso", "outro:", "%"):
            with self.subTest(prefixo=prefixo), self.assertRaises(OperacaoProibida):
                b.esquecer_alertas(prefixo)
        self.assertEqual(b.esquecer_alertas("falso:%"), 0)              # % é letra, não curinga


# ---------------------------------------------------------------- extras na análise (motor)

class TestExtrasNaAnalise(unittest.TestCase):
    def test_extras_conferidos_e_o_aviso_do_cadeado(self):
        falso = recurso_falso()
        with recursos.usando(falso):
            proposta = motor.MotorRegras().analisar(TEXTO, REF)
        self.assertEqual(list(proposta.extras), ["falso"])
        extras = proposta.extras["falso"]
        self.assertEqual([(e["tipo"], e["trecho"]) for e in extras], [("promessa", PROMESSA), ("segredo", SEGREDO)])
        self.assertEqual(extras[0]["prazo"], DATA)                 # data em ISO (JSON)
        self.assertNotIn("_interno", extras[0])                    # "_" é da fundação
        self.assertNotIn("_privado", extras[0])
        self.assertEqual(extras[1]["resumo"], SEGREDO)             # sem resumo: o próprio trecho
        self.assertTrue(extras[1]["_privado"])
        self.assertIn(recursos.AVISO_PRIVADO, proposta.avisos)
        # O recurso recebeu cópias: mexer nelas não muda o que as regras entenderam.
        self.assertTrue(proposta.itens)
        self.assertNotEqual(proposta.itens[0].resumo, "MEXI")
        self.assertEqual(falso.chamadas[0], ("extrair", len(proposta.itens)))
        dados = para_json(proposta)
        self.assertEqual(json.loads(json.dumps(dados))["extras"]["falso"][0]["tipo"], "promessa")

    def test_sem_recursos_extras_vazio(self):
        with recursos.usando():
            proposta = motor.MotorRegras().analisar(TEXTO, REF)
        self.assertEqual(proposta.extras, {})
        self.assertEqual(para_json(proposta)["extras"], {})

    def test_recurso_que_falha_vira_aviso_sem_conteudo(self):
        log = coletar_log(self)
        quebrado = recurso_falso("quebrado", ROTULO="Parte quebrada", extrair=explodir)
        lista_ruim = recurso_falso("lista_ruim", ROTULO=None, extrair=lambda *a: "não é lista")
        with recursos.usando(quebrado, recurso_falso(), lista_ruim):
            proposta = motor.MotorRegras().analisar(TEXTO, REF)
        self.assertEqual(list(proposta.extras), ["falso"])            # o resto continua
        self.assertIn(recursos.AVISO_EXTRAIR.format(rotulo="Parte quebrada"), proposta.avisos)
        self.assertIn(recursos.AVISO_EXTRAIR.format(rotulo="lista_ruim"), proposta.avisos)
        self.assertTrue(proposta.itens)
        texto_log = "\n".join(log.mensagens)
        self.assertIn("quebrado", texto_log)
        self.assertIn("RuntimeError", texto_log)
        self.assertNotIn("Mariana", texto_log)
        self.assertNotIn("pizza", texto_log)
        self.assertNotIn("cinema", texto_log)

    def test_marca_privado_que_falha_conta_como_cadeado(self):
        with recursos.usando(recurso_falso(marca_privado=explodir)):
            proposta = motor.MotorRegras().analisar(TEXTO, REF)
        self.assertTrue(all(e.get("_privado") for e in proposta.extras["falso"]))


# ---------------------------------------------------------------- o núcleo (sem HTTP)

class BaseNucleo(unittest.TestCase):
    recursos_extra: tuple = ()

    def setUp(self):
        self.falso = recurso_falso()
        self.enterContext(recursos.usando(self.falso, *self.recursos_extra))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-fundacao-")
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

    def analisar(self, texto: str = TEXTO) -> dict:
        r = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": DATA})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def corpo(self, proposta: dict, confirmar=("promessa",), **mais) -> dict:
        extras = {nome: [e for e in lista if e["tipo"] in confirmar] for nome, lista in proposta["extras"].items()}
        return {"texto": proposta["texto"], "data_registro": DATA, "itens": proposta["itens"],
                "pessoas": proposta["pessoas"], "relacoes": proposta["relacoes"],
                "extras": {n: l for n, l in extras.items() if l}, **mais}

    def registrar(self, texto: str = TEXTO, confirmar=("promessa",), **mais) -> nucleo.RespostaApi:
        return self.pedir("POST", "/api/registros", self.corpo(self.analisar(texto), confirmar, **mais))

    def coisas(self) -> list[dict]:
        return self.banco.consultar("SELECT tipo, registro_id FROM falso_coisas ORDER BY id")


class TestGravarComExtras(BaseNucleo):
    def test_analisar_traz_os_extras(self):
        p = self.analisar()
        self.assertEqual([e["tipo"] for e in p["extras"]["falso"]], ["promessa", "segredo"])
        self.assertTrue(p["extras"]["falso"][1]["_privado"])

    def test_extra_confirmado_e_gravado_na_mesma_transacao(self):
        with mock.patch.object(self.app.cofre, "salvar", wraps=self.app.cofre.salvar) as salvar:
            r = self.registrar(confirmar=("promessa",))
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados, {"id": r.dados["id"]})
        self.assertEqual(salvar.call_count, 1)                     # registro + extras: um arquivo gravado
        rid = r.dados["id"]
        self.assertEqual(self.coisas(), [{"tipo": "promessa", "registro_id": rid}])
        self.assertEqual(self.falso.chamadas[-1], ("gravar", rid, ["promessa"], REF))
        self.assertFalse(self.pedir("GET", f"/api/registros/{rid}").dados["privado"])

    def test_extra_que_marca_privado_deixa_o_registro_fora_da_ia(self):
        r = self.registrar(confirmar=("promessa", "segredo"), privado=False)
        self.assertEqual(r.status, 200, r.dados)
        self.assertTrue(r.dados["privado"])
        self.assertEqual(r.dados["avisos"], [recursos.AVISO_PRIVADO_GRAVADO])
        rid = r.dados["id"]
        self.assertTrue(self.pedir("GET", f"/api/registros/{rid}").dados["privado"])
        # Desmarcar o 🔒 depois não tira o registro de fora da IA: o recurso continua dizendo.
        self.assertEqual(self.pedir("PATCH", f"/api/registros/{rid}", {"privado": False}).status, 200)
        self.assertIn(rid, nucleo.fora_da_ia(self.banco))

    def test_sem_confirmar_nada_fica_so_o_registro(self):
        r = self.registrar(confirmar=())
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(self.coisas(), [])
        self.assertFalse(self.pedir("GET", f"/api/registros/{r.dados['id']}").dados["privado"])

    def test_extras_que_nao_servem_400_e_nada_gravado(self):
        p = self.analisar()
        promessa = p["extras"]["falso"][0]
        casos = {
            "recurso desconhecido": ({"outro": [promessa]}, recursos.EXTRA_DESCONHECIDO),
            "tipo desconhecido": ({"falso": [{**promessa, "tipo": "presente"}]}, recursos.EXTRA_DESCONHECIDO),
            "sem tipo": ({"falso": [{k: v for k, v in promessa.items() if k != "tipo"}]}, recursos.EXTRA_DESCONHECIDO),
            "trecho inventado": ({"falso": [{**promessa, "trecho": "Prometi uma viagem a Paris."}]},
                                 recursos.EXTRA_SEM_TRECHO),
            "trecho vazio": ({"falso": [{**promessa, "trecho": " "}]}, recursos.EXTRA_SEM_TRECHO),
            "lista no lugar do objeto": ([promessa], recursos.EXTRAS_FORMATO),
            "texto no lugar da lista": ({"falso": "promessa"}, recursos.EXTRAS_FORMATO),
            "demais": ({"falso": [promessa] * (recursos.MAX_EXTRAS + 1)}, recursos.EXTRAS_DEMAIS),
        }
        for nome, (extras, mensagem) in casos.items():
            with self.subTest(caso=nome):
                r = self.pedir("POST", "/api/registros", {**self.corpo(p), "extras": extras})
                self.assertEqual((r.status, r.dados["erro"]), (400, mensagem))
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])
        self.assertEqual(self.coisas(), [])

    def test_gravar_extras_com_defeito_salva_o_registro_e_avisa(self):
        def gravar_e_explodir(banco, registro_id, extras, referencia):
            banco.inserir(self.falso.TABELA, {"tipo": "promessa", "resumo": "meia", "trecho": "t",
                                              "registro_id": registro_id})
            raise RuntimeError("Mariana")

        self.falso.gravar_extras = gravar_e_explodir
        r = self.registrar()
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["avisos"], [recursos.AVISO_GRAVAR.format(rotulo="Recurso de teste")])
        self.assertEqual(len(self.pedir("GET", "/api/registros").dados), 1)
        self.assertEqual(self.coisas(), [])                  # a parte do recurso foi desfeita inteira

    def test_gravar_extras_que_recusa_o_dado_400_e_nada_gravado(self):
        def recusar(banco, registro_id, extras, referencia):
            banco.inserir(self.falso.TABELA, {"tipo": "promessa", "resumo": "meia", "trecho": "t"})
            raise ValueError("O prazo da promessa não é uma data.")

        self.falso.gravar_extras = recusar
        r = self.registrar()
        self.assertEqual((r.status, r.dados["erro"]), (400, "O prazo da promessa não é uma data."))
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])
        self.assertEqual(self.coisas(), [])


class TestRotasDosRecursos(BaseNucleo):
    def test_rotas_com_prefixo_e_token(self):
        rid = self.registrar().dados["id"]
        r = self.pedir("GET", "/api/falso/coisas")
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual([(c["tipo"], c["registro_id"]) for c in r.dados], [("promessa", rid)])
        for token in (None, "token-inventado"):
            self.assertEqual(self.pedir("GET", "/api/falso/coisas", token=token).status, 401)
        self.assertEqual(self.pedir("GET", "/api/falso/nada").status, 404)
        r = self.pedir("POST", "/api/falso/coisas", {})
        self.assertEqual((r.status, r.cabecalhos.get("Allow")), (405, "GET"))
        self.assertEqual(self.pedir("GET", "/api/falso/livre").dados, {"registros": 1})
        self.assertTrue(all(rota.padrao.pattern.startswith(re.escape("/api/falso")) for rota in nucleo.rotas_dos_recursos()))
        self.assertTrue(all(rota.acesso in (nucleo.SESSAO, nucleo.LIVRE) for rota in nucleo.rotas_dos_recursos()))
        # O núcleo vem antes: nenhuma rota de recurso esconde uma do núcleo.
        self.assertEqual(nucleo.todas_as_rotas()[:len(nucleo.ROTAS)], nucleo.ROTAS)

    def test_erros_das_rotas(self):
        log = coletar_log(self)
        r = self.pedir("POST", "/api/falso/recusar", {})
        self.assertEqual((r.status, r.dados["erro"]), (400, "Isso não dá para guardar."))
        r = self.pedir("POST", "/api/falso/explodir", {})
        self.assertEqual((r.status, r.dados["erro"]), (500, nucleo.FALHA_INTERNA))
        self.assertTrue(any("RuntimeError" in m for m in log.mensagens))
        self.assertFalse(any("Mariana" in m for m in log.mensagens))

    def test_apagar_pela_rota_limpa_as_copias(self):
        self.registrar()
        cid = self.banco.consultar("SELECT id FROM falso_coisas")[0]["id"]
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/falso/coisas/{cid}").status, 200)
        self.assertEqual(self.coisas(), [])
        self.assertEqual(len(repetir), 1)
        repetir[0](copia)                                  # o que o cofre faria em cada cópia
        self.assertEqual(copia.consultar("SELECT * FROM falso_coisas"), [])
        self.assertEqual(self.pedir("DELETE", f"/api/falso/coisas/{cid}").status, 404)

    def test_rotas_fora_do_contrato_ficam_de_fora(self):
        def f(*_):
            return {}
        ruins = [
            recurso_falso("r1", rotas=lambda: [recursos.rota("GET", "../registros", f)]),
            recurso_falso("r2", rotas=lambda: [recursos.rota("GET", "x", f, acesso="publica")]),
            recurso_falso("r3", rotas=lambda: [recursos.rota("TRACE", "x", f)]),
            recurso_falso("r4", rotas=lambda: [("GET", "x", f)]),
            recurso_falso("r5", rotas=explodir),
            recurso_falso("r6", rotas=lambda: [recursos.rota("GET", "ok", f), recursos.rota("GET", "x?y", f)]),
        ]
        log = coletar_log(self)
        with recursos.usando(*ruins):
            self.assertEqual(nucleo.rotas_dos_recursos(), ())
            self.assertEqual(self.pedir("GET", "/api/r6/ok").status, 404)
        self.assertEqual(len([m for m in log.mensagens if "(rotas)" in m]), 6)


class TestHoje(BaseNucleo):
    recursos_extra = (recurso_falso("outro", ESQUEMA=None, ESQUEMA_VERSAO=None, cartoes_hoje=lambda banco, ref: [
        recursos.cartao("s", "sugestao", "Talvez a do outro", relevancia=65),
        recursos.cartao("s9", "sugestao", "Talvez outra do outro", relevancia=99)]),)

    def test_anti_excesso_central(self):
        dados = self.pedir("GET", "/api/hoje").dados
        alertas_ = dados["alertas"]
        por_nivel = {n: [a for a in alertas_ if a["nivel"] == n] for n in ("importante", "lembrete", "sugestao")}
        self.assertEqual([a["chave"] for a in por_nivel["importante"]], ["falso:i2", "falso:i1"])    # todos
        self.assertEqual([a["chave"] for a in por_nivel["lembrete"]], ["falso:l3", "falso:l2", "falso:l1"])
        sugestoes = por_nivel["sugestao"]
        self.assertEqual([a["chave"] for a in sugestoes], ["outro:s9", "falso:s2"])     # 1 por fornecedor, 2 no total
        primeiro = por_nivel["importante"][0]
        self.assertEqual((primeiro["recurso"], primeiro["tipo"], primeiro["dados"]), ("falso", "falso", {"quando": DATA}))
        self.assertEqual(por_nivel["lembrete"][0]["tipo"], "lembrete_falso")
        self.assertEqual(sugestoes[1]["registro_ids"], [1])

    def test_dispensado_nao_volta_e_cartao_ruim_nao_aparece(self):
        log = coletar_log(self)
        self.assertEqual(self.pedir("POST", "/api/alertas/dispensar", {"chave": "falso:i2"}).status, 200)
        chaves = [a["chave"] for a in self.pedir("GET", "/api/hoje").dados["alertas"]]
        self.assertNotIn("falso:i2", chaves)
        self.assertIn("falso:i1", chaves)
        self.assertNotIn("falso:x", chaves)
        self.assertEqual(len([m for m in log.mensagens if "cartoes_hoje (cartão)" in m]), 2)

    def test_recurso_que_falha_nao_derruba_o_hoje(self):
        self.falso.cartoes_hoje = explodir
        r = self.pedir("GET", "/api/hoje")
        self.assertEqual(r.status, 200)
        self.assertEqual([a["chave"] for a in r.dados["alertas"]], ["outro:s9"])

    def test_sugestao_do_hoje_continua_com_a_dos_recursos(self):
        # Um estado negativo recente gera a sugestão do próprio Hoje (fornecedor "núcleo").
        sem = alertas.hoje(self.banco, REF)
        self.banco.criar_registro("Ela está muito cansada.", REF, [
            Item(tipo=TipoItem.ESTADO, resumo="Muito cansada", trecho="Ela está muito cansada.",
                 certeza=Certeza.OBSERVADO, categorias=["Preocupações"], data_evento=REF, polaridade=-1)], [], [])
        com = alertas.hoje(self.banco, REF)
        do_nucleo = [a for a in com["alertas"] if a.nivel == "sugestao" and not isinstance(a, alertas.CartaoRecurso)]
        sugestoes = [a for a in com["alertas"] if a.nivel == "sugestao"]
        self.assertLessEqual(len(sugestoes), alertas.MAX_SUGESTOES_TOTAL)
        fornecedores = [getattr(a, "recurso", "") or "núcleo" for a in sugestoes]
        self.assertEqual(len(fornecedores), len(set(fornecedores)))
        if do_nucleo:
            self.assertGreaterEqual(do_nucleo[0].relevancia, min(a.relevancia for a in sugestoes))
        self.assertTrue(all(isinstance(a, Alerta) for a in sem["alertas"]))


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


class TestForaDaIA(BaseNucleo):
    def setUp(self):
        super().setUp()
        self.banco.salvar_config(ia_principal=IA)
        self.cadeia = CadeiaFalsa()
        self.enterContext(mock.patch.object(motor, "obter_cadeia_ia", lambda ajustes: self.cadeia))
        # Um registro com o segredo (o recurso o deixa fora da IA) e um comum.
        self.segredo = self.registrar("Segredo: ela anda com muita cólica e adora tulipas.", confirmar=("segredo",)).dados["id"]
        self.pedir("PATCH", f"/api/registros/{self.segredo}", {"privado": False})        # nem assim vai
        self.comum = self.registrar("Ela adora tulipas amarelas.", confirmar=()).dados["id"]

    def test_fora_da_ia_soma_os_privados_e_os_dos_recursos(self):
        self.assertEqual(nucleo.fora_da_ia(self.banco), {self.segredo})
        # O detalhe do registro diz que um recurso o deixa fora da IA (a tela trava a caixinha).
        detalhe = self.pedir("GET", f"/api/registros/{self.segredo}").dados
        self.assertEqual((detalhe["privado"], detalhe["fora_da_ia"]), (False, True))
        self.assertIs(self.pedir("GET", f"/api/registros/{self.comum}").dados["fora_da_ia"], False)
        self.assertEqual(self.pedir("PATCH", f"/api/registros/{self.comum}", {"privado": True}).status, 200)
        self.assertEqual(nucleo.fora_da_ia(self.banco), {self.segredo, self.comum})

    def test_busca_com_ia_nao_manda_o_que_fica_fora(self):
        busca = self.pedir("GET", "/api/busca", consulta={"q": "tulipas"}).dados
        privados = {e["registro_id"]: e["privado"] for e in busca["encontrados"]}
        self.assertEqual(privados, {self.segredo: True, self.comum: False})
        self.assertTrue(busca["para_ia"])
        r = self.pedir("POST", "/api/ia/busca", {"q": "tulipas"})
        self.assertEqual(r.status, 200, r.dados)
        enviados = self.cadeia.respostas[-1]
        self.assertEqual({t["registro_id"] for t in enviados}, {self.comum})
        self.assertNotIn("cólica", json.dumps(enviados, ensure_ascii=False, default=str))
        self.assertIn(nucleo.PRIVADOS_DE_FORA, r.dados["avisos"])

    def test_sugestao_com_ia_nao_manda_o_que_fica_fora(self):
        self.assertEqual(self.pedir("POST", "/api/ia/sugestao", {}).status, 200)
        contexto = self.cadeia.contextos[-1]
        self.assertTrue(contexto)
        self.assertEqual({c["registro_id"] for c in contexto}, {self.comum})
        self.assertNotIn(self.segredo, {c["registro_id"] for c in nucleo.contexto_sugestao(self.banco, REF)})

    def test_analisar_com_ia_nunca_recebe_extras(self):
        r = self.pedir("POST", "/api/ia/analisar", {"texto": "Prometi levar ela ao cinema na sexta.",
                                                    "data_registro": DATA})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(self.cadeia.analises[-1].extras, {})                 # a IA não viu
        self.assertEqual([e["tipo"] for e in r.dados["proposta"]["extras"]["falso"]], ["promessa"])   # a tela vê
        # Texto com o que o recurso deixa fora da IA: nem sai daqui (mesmo sem a caixinha 🔒).
        antes = len(self.cadeia.analises)
        r = self.pedir("POST", "/api/ia/analisar", {"texto": TEXTO, "data_registro": DATA, "privado": False})
        self.assertEqual((r.status, r.dados["erro"]), (403, nucleo.TEXTO_FICA_FORA_DA_IA))
        self.assertEqual(len(self.cadeia.analises), antes)

    def test_sem_saber_o_que_fica_fora_nada_vai(self):
        self.falso.fora_da_ia = explodir
        for rota, corpo in (("/api/ia/busca", {"q": "tulipas"}), ("/api/ia/sugestao", {})):
            with self.subTest(rota=rota):
                r = self.pedir("POST", rota, corpo)
                self.assertEqual((r.status, r.dados["erro"]), (503, nucleo.FORA_DA_IA_INDISPONIVEL))
        self.assertFalse(self.pedir("GET", "/api/busca", consulta={"q": "tulipas"}).dados["para_ia"])
        self.assertEqual(self.cadeia.respostas, [])
        self.assertEqual(self.cadeia.contextos, [])

    def test_recurso_que_nao_abriu_tambem_bloqueia_a_ia(self):
        with recursos.usando(recurso_falso("quebrado", extrair="não é função"), com_os_fixos=True):
            self.assertTrue(recursos.falhas())
            for rota, corpo in (("/api/ia/busca", {"q": "tulipas"}), ("/api/ia/sugestao", {}),
                                ("/api/ia/analisar", {"texto": "Ela adora café.", "data_registro": DATA})):
                with self.subTest(rota=rota):
                    self.assertEqual(self.pedir("POST", rota, corpo).status, 503)
        self.assertEqual((self.cadeia.respostas, self.cadeia.contextos, self.cadeia.analises), ([], [], []))


class TestExportarImportarApagar(BaseNucleo):
    def test_exportar_e_juntar_noutro_diario(self):
        rid = self.registrar(confirmar=("promessa", "segredo")).dados["id"]
        exportado = self.pedir("GET", "/api/exportar").dados
        self.assertEqual(exportado["recursos"]["falso"]["coisas"][0]["registro_id"], rid)
        self.assertNotIn("avisos", exportado)
        texto = json.dumps(exportado, ensure_ascii=False)

        destino = Banco.novo()
        self.addCleanup(destino.fechar)
        destino.criar_registro("Um registro de antes.", REF, [], [], [])       # os ids mudam
        previa = destino.importar_json(texto, previa=True)
        self.assertEqual(previa["recursos"], {"falso": {"novos": 2, "texto": "2 coisas de teste"}})
        self.assertEqual(destino.consultar("SELECT * FROM falso_coisas"), [])          # a prévia não grava
        r = destino.importar_json(texto)
        self.assertEqual(r["recursos"]["falso"]["novos"], 2)
        novo = [x["id"] for x in destino.registros() if x["texto"] == TEXTO][0]
        self.assertNotEqual(novo, rid)
        self.assertEqual({l["registro_id"] for l in destino.consultar("SELECT registro_id FROM falso_coisas")}, {novo})
        self.assertTrue(destino.registro(novo)["privado"])
        # De novo: nada duplica (o registro é repetido e o recurso confere).
        r = destino.importar_json(texto)
        self.assertEqual((r["registros_novos"], r["recursos"]["falso"]["novos"]), (0, 0))
        self.assertEqual(len(destino.consultar("SELECT * FROM falso_coisas")), 2)

    def test_importar_tolerante(self):
        self.registrar()
        exportado = self.pedir("GET", "/api/exportar").dados
        exportado["recursos"]["nao_existe_aqui"] = {"x": 1}
        destino = Banco.novo()
        self.addCleanup(destino.fechar)
        r = destino.importar_json(json.dumps(exportado, ensure_ascii=False))
        self.assertIn(recursos.AVISO_IMPORTAR_DESCONHECIDOS, r["avisos"])
        self.assertEqual(r["registros_novos"], 1)
        self.falso.importar = lambda banco, dados, mapa: (banco.inserir("falso_coisas", {"tipo": "a", "resumo": "b",
                                                                                         "trecho": "c"}), explodir())
        outro = Banco.novo()
        self.addCleanup(outro.fechar)
        r = outro.importar_json(json.dumps(exportado, ensure_ascii=False))
        self.assertIn(recursos.AVISO_IMPORTAR.format(rotulo="Recurso de teste"), r["avisos"])
        self.assertEqual(r["registros_novos"], 1)
        self.assertEqual(outro.consultar("SELECT * FROM falso_coisas"), [])          # a parte dele foi desfeita
        with self.assertRaises(ValueError):
            outro.importar_json(json.dumps({**exportado, "recursos": ["não é objeto"]}))

    def test_exportar_com_recurso_que_falha_avisa(self):
        self.falso.exportar = explodir
        dados = self.pedir("GET", "/api/exportar").dados
        self.assertEqual(dados["recursos"], {})
        self.assertEqual(dados["avisos"], [recursos.AVISO_EXPORTAR.format(rotulo="Recurso de teste")])

    def test_apagar_registro_apaga_o_que_depende_dele(self):
        rid = self.registrar(confirmar=("promessa", "segredo")).dados["id"]
        outro = self.registrar("Prometi ligar para ela amanhã.").dados["id"]
        self.banco.dispensar_alerta(f"falso:r{rid}:2026-10-02")
        self.banco.dispensar_alerta(f"falso:r{outro}:2026-10-02")
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertIn(("apagar", rid), self.falso.chamadas)
        self.assertEqual(self.coisas(), [{"tipo": "promessa", "registro_id": outro}])
        self.assertEqual(self.banco.alertas_dispensados(), {f"falso:r{outro}:2026-10-02"})
        exportado = json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False)
        self.assertNotIn(PROMESSA, exportado)                 # apagar é apagar: nem no export
        self.assertNotIn("cólica", exportado)
        self.assertEqual(nucleo.fora_da_ia(self.banco), set())

    def test_apagar_sem_o_gancho_ou_com_ele_quebrado_apaga_assim_mesmo(self):
        for caso, gancho in (("sem gancho", None), ("gancho quebrado", explodir)):
            with self.subTest(caso=caso):
                self.falso.ao_apagar_registro = gancho
                rid = self.registrar().dados["id"]
                self.assertEqual(len(self.coisas()), 1)
                self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
                self.assertEqual(self.coisas(), [])
                self.assertIsNone(self.banco.registro(rid))

    def test_apagar_alcanca_a_tabela_de_recurso_que_nao_abriu(self):
        rid = self.registrar().dados["id"]
        dados = self.banco.para_bytes()
        with recursos.usando(recurso_falso("quebrado", extrair="não é função")):     # o "falso" sumiu
            copia = Banco.de_bytes(dados)
            self.addCleanup(copia.fechar)
            copia.apagar_registro(rid)
            self.assertEqual(copia._con.execute("SELECT count(*) FROM falso_coisas").fetchone()[0], 0)


class TestPonte(unittest.TestCase):
    def desligar(self) -> None:
        if ponte._nucleo is not None:
            ponte._nucleo.bloquear()
        ponte._nucleo = None
        if ponte._log_manipulador is not None:
            logging.getLogger("entrenos").removeHandler(ponte._log_manipulador)
            ponte._log_manipulador.close()
            ponte._log_manipulador = None

    def test_extras_pela_ponte(self):
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-fundacao-ponte-")
        self.addCleanup(shutil.rmtree, pasta, True)
        self.enterContext(recursos.usando(recurso_falso()))
        ponte.iniciar_no_navegador(pasta, hoje=lambda: REF, relogio=Relogio())
        self.addCleanup(self.desligar)

        def pedir(metodo, caminho, corpo=None, token=None):
            return json.loads(ponte.tratar_json(metodo, caminho, None if corpo is None else json.dumps(corpo), token))

        token = pedir("POST", "/api/configurar", {"pin": PIN})["dados"]["token"]
        p = pedir("POST", "/api/analisar", {"texto": TEXTO, "data_registro": DATA}, token)["dados"]
        self.assertEqual([e["tipo"] for e in p["extras"]["falso"]], ["promessa", "segredo"])
        r = pedir("POST", "/api/registros", {"texto": TEXTO, "data_registro": DATA, "itens": p["itens"],
                                             "extras": {"falso": p["extras"]["falso"][:1]}}, token)
        self.assertEqual((r["status"], r["gravou"]), (200, True))
        self.assertEqual(len(pedir("GET", "/api/falso/coisas", token=token)["dados"]), 1)
        self.assertTrue(json.loads(ponte.autoteste())["ok"])


# ---------------------------------------------------------------- a página

class TestPagina(unittest.TestCase):
    def test_index_carrega_as_telas_dos_recursos_depois_do_app(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
        self.assertTrue(all(corpo.strip() == "" for _, corpo in scripts))         # nada inline (CSP)
        fontes = [re.search(r'src="([^"]+)"', attrs).group(1) for attrs, _ in scripts]
        # Seção 22: a sincronização (Ajustes → ☁️) entra logo depois do app.js, antes das telas.
        self.assertEqual(fontes, ["app.js", "sincronizar.js", *(f"telas/{n}.js" for n in recursos.NOMES)])
        self.assertTrue(all(re.search(r"\bdefer\b", attrs) for attrs, _ in scripts))
        estilos = re.findall(r'<link rel="stylesheet" href="([^"]+)">', html)
        self.assertEqual(estilos, ["estilo.css", *(f"telas/{n}.css" for n in recursos.NOMES)])
        self.assertNotIn("<style", html)
        self.assertNotIn("style=", html)

    def test_barra_de_baixo_do_celular(self):
        # Hoje, Ela, + Registrar (no centro), Buscar e Mais; o resto (e os recursos) vai para "Mais".
        html = (WEB / "index.html").read_text(encoding="utf-8")
        inferior = re.search(r'<nav class="nav-inferior".*?</nav>', html, re.S).group(0)
        barra, mais = inferior.split('id="menu-mais"')
        self.assertEqual(re.findall(r'data-rota="([a-z]+)"', barra), ["hoje", "ela", "registrar", "buscar"])
        self.assertIn('id="botao-mais"', barra)
        self.assertRegex(barra, r'class="nav-registrar"><span class="nav-icone" aria-hidden="true">\+</span>')
        self.assertEqual(re.findall(r'data-rota="([a-z]+)"', mais), ["datas", "registros", "ajustes"])

    def test_telas_dos_recursos_seguem_o_contrato_da_pagina(self):
        # Nasceram vazias pela fundação; desde a integração cada recurso tem a sua tela. O que vale
        # para todas: só window.EntreNos, nada de HTML montado nem estilo inline (CSP), e o CSS próprio
        # pode existir (index.html carrega) ou ficar só com o comentário.
        for nome in recursos.NOMES:
            with self.subTest(arquivo=f"{nome}.js"):
                js = (WEB / "telas" / f"{nome}.js").read_text(encoding="utf-8")
                self.assertTrue(js.lstrip().startswith("/*"))
                self.assertIn("window.EntreNos", js)
                for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                                 "new Function", ".style.", "setAttribute('style'", 'setAttribute("style"'):
                    self.assertNotIn(proibido, js)
                self.assertNotRegex(js, r"\bstyle\s*:")
                self.assertNotIn("fetch(", js)                 # só pelo api() do app.js
            with self.subTest(arquivo=f"{nome}.css"):
                css = (WEB / "telas" / f"{nome}.css").read_text(encoding="utf-8")
                self.assertTrue(css.lstrip().startswith("/*"))
                self.assertNotIn("@import", css)
                self.assertNotRegex(css, r"url\(\s*['\"]?(?:https?:)?//")   # nada de fora

    def test_app_js_expoe_o_contrato(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        bloco = re.search(r"window\.EntreNos = Object\.freeze\(\{(.*?)\n  \}\);", app, re.S)
        self.assertIsNotNone(bloco)
        for nome in ("api", "h", "selo", "navegar", "registrarTela", "registrarCartaoHoje", "registrarAbaEla",
                     "registrarSecaoAjustes", "registrarRevisaoExtra", "registrarMenu", "util"):
            with self.subTest(nome=nome):
                self.assertRegex(bloco.group(1), rf"\n    {nome}[,:]")
        self.assertNotIn("innerHTML", app)
        self.assertIn("quandoPronto(carregarEstado)", app)          # começa depois das telas dos recursos

    def test_servidor_serve_as_telas_com_tipo_certo_e_csp(self):
        s = ServidorDeTeste()
        self.addCleanup(s.fechar)
        for nome in recursos.NOMES:
            for caminho, tipo in ((f"/telas/{nome}.js", "text/javascript; charset=utf-8"),
                                  (f"/telas/{nome}.css", "text/css; charset=utf-8")):
                with self.subTest(caminho=caminho):
                    r = s.pedir("GET", caminho, token=None)
                    self.assertEqual(r.status, 200)
                    self.assertEqual(r.cabecalhos["Content-Type"], tipo)
                    self.assertEqual(r.cabecalhos["Content-Security-Policy"], servidor.CSP)
                    self.assertEqual(r.cabecalhos["X-Content-Type-Options"], "nosniff")
        for caminho in ("/telas/", "/telas/nao-existe.js", "/telas/../app.js", "/telas/%2e%2e/index.html"):
            with self.subTest(caminho=caminho):
                self.assertIn(s.pedir("GET", caminho, token=None).status, (404,))
        self.assertIn("script-src 'self'", servidor.CSP)
        self.assertNotIn("unsafe-inline", servidor.CSP)


if __name__ == "__main__":
    unittest.main()
