"""Testes da seção 18.2: restaurar um backup (.cofre) e importar o JSON exportado.

Restaurar: no primeiro uso (sem token) e com o app aberto (token + PIN atual). O arquivo é
conferido inteiro antes de qualquer troca (não é cofre, cofre cortado, PIN errado, banco
danificado dentro de um cofre válido, esquema antigo que migra); depois vêm a cópia do diário
anterior, o PIN do backup, a sessão nova e a área da IA fechada.

Importar: prévia sem gravar; juntar sem duplicar (registros repetidos, pessoa casada e pessoa
nova, relações remapeadas e quebradas descartadas, privado preservado, ajustes, PIN da IA e
chaves do arquivo ignorados); JSON malformado, de outro app, com tipos errados ou grande demais
(400 sem gravar nada); importar duas vezes; e a ida e volta exportar -> importar num diário vazio.

Pasta de dados temporária (ENTRENOS_DADOS) e data fixa 30/09/2026. O scrypt usa n pequeno só
para os testes andarem rápido; o valor real é testado em test_cofre.py.
"""
from __future__ import annotations

import base64
import copy
import http.client
import json
import os
import re
import shutil
import sqlite3
import struct
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from entrenos import banco as banco_mod
from entrenos import cofre, config, nucleo, servidor
from entrenos.banco import Banco
from entrenos.modelos import Certeza, Item, PessoaEncontrada, Precisao, Relacao, TipoItem
from testes.test_servidor import PIN, REF, Relogio, ServidorDeTeste

PIN_BACKUP = "girassol na janela azul"
PIN_IA = "cafe com canela e mel"
PIN_IA_BACKUP = "chuva fina na varanda"
CHAVE = "sk-proj-SEGREDO0123456789wxyz"

FRASE_MAE = "A mãe dela, Sandra, vai fazer uma cirurgia em novembro."
FRASE_TULIPAS = "Ela adora tulipas."
TEXTO_MAE = f"{FRASE_MAE} {FRASE_TULIPAS}"
TEXTO_MARI = "A Mari me contou que vai viajar para Recife no feriado."
TEXTO_TRISTE = "Ela ficou triste com a notícia do trabalho."
TEXTO_ANTIGO = "Ela gosta de café sem açúcar."
RE_COPIA = re.compile(r"^entrenos-antes-da-restauracao-2026-09-30-\d{6}(-\d+)?\.cofre$")

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-restaurar-env-")
    os.environ["ENTRENOS_DADOS"] = _pasta_env      # garantia: nada cai em %LOCALAPPDATA%
    for nome, valor in (("SCRYPT_N", 2 ** 12), ("VERIFICADOR_N", 2 ** 10)):
        remendo = mock.patch.object(cofre, nome, valor)
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


# ---------------------------------------------------------------- dados de exemplo

def b64(conteudo: bytes) -> str:
    return base64.b64encode(conteudo).decode("ascii")


def cifrar(dados: bytes, pin: str = PIN_BACKUP) -> bytes:
    """Um arquivo .cofre com `dados` dentro, gravado pelo próprio Cofre numa pasta temporária."""
    pasta = tempfile.mkdtemp(prefix="entrenos-teste-cofre-")
    try:
        c = cofre.Cofre(Path(pasta))
        c.criar(pin, dados)
        return c.arquivo.read_bytes()
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def decifrar(conteudo: bytes, pin: str) -> bytes:
    recebido = cofre.abrir_conteudo(conteudo, pin)
    try:
        return recebido.dados
    finally:
        recebido.descartar()


def conteudo_do_diario(dados: bytes) -> dict:
    """Tudo o que o diário guarda (a exportação, sem a hora dela): compara o conteúdo de duas
    cópias sem depender dos bytes (o VACUUM muda o contador de alterações do SQLite)."""
    b = Banco.de_bytes(dados)
    try:
        tudo = b.exportar()
    finally:
        b.fechar()
    tudo.pop("exportado_em", None)
    return tudo


def versao_do_banco(dados: bytes) -> int:
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(dados)
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def banco_antigo(versao: int, ajustes: dict | None = None) -> bytes:
    """Bytes de um banco como as versões 1 e 2 do esquema gravavam, com um registro."""
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(banco_mod._ESQUEMA_V1 + (banco_mod._MIGRACAO_V2 if versao >= 2 else "")
                          + f"\nPRAGMA user_version = {versao};")
        con.execute("INSERT INTO registros (criado_em, data_registro, texto) VALUES (?, ?, ?)",
                    ("2026-08-01T10:00:00", "2026-08-01", TEXTO_ANTIGO))
        for chave, valor in (ajustes or {}).items():
            con.execute("INSERT INTO config (chave, valor) VALUES (?, ?)", (chave, json.dumps(valor)))
        con.commit()
        return con.serialize()
    finally:
        con.close()


def itens_mae() -> list[Item]:
    return [
        Item(TipoItem.EVENTO, "Mãe: cirurgia em novembro/2026", FRASE_MAE, Certeza.OBSERVADO,
             categorias=["Família", "Saúde", "Eventos futuros"], importancia=3, data_evento=date(2026, 11, 1),
             data_expressao="em novembro", precisao=Precisao.MES, pessoa="mãe"),
        Item(TipoItem.PREFERENCIA, "Gosta de tulipas", FRASE_TULIPAS, Certeza.OBSERVADO,
             categorias=["Preferências"], alvo="tulipas", polaridade=1),
    ]


def pessoas_mae() -> list[PessoaEncontrada]:
    return [PessoaEncontrada(trecho="A mãe dela, Sandra", inicio=0, fim=18, nome="Sandra", relacao="mãe")]


def diario_de_origem() -> Banco:
    """O diário de "outro computador": ajustes com segredos, uma pessoa escrita à mão (com apelido,
    observação e aniversário), três registros (o último 🔒, com relação para um item do primeiro)
    e um aviso dispensado."""
    b = Banco.novo()
    b.salvar_config(nome_dela="Julia", ia_pin=cofre.criar_verificador(PIN_IA),
                    ia_principal={"provedor": "openai", "url": "https://api.openai.com/v1", "modelo": "gpt-4o-mini",
                                  "tipo": "openai", "chave": CHAVE})
    mariana = b.criar_pessoa("Mariana", "melhor amiga", apelidos=["Mari"], observacoes="Se conheceram na faculdade.")
    b.atualizar_pessoa(mariana, aniversario=date(1998, 3, 12))
    r1 = b.criar_registro(TEXTO_MAE, date(2026, 9, 20), itens_mae(), pessoas_mae(), [], contexto="no jantar")
    b.criar_registro(TEXTO_MARI, date(2026, 9, 25),
                     [Item(TipoItem.FATO, "Mariana vai viajar para Recife", TEXTO_MARI, Certeza.CONFIRMADO,
                           categorias=["Amigos", "Viagens"], pessoa="Mariana", pessoa_id=mariana)],
                     [PessoaEncontrada(trecho="A Mari", inicio=0, fim=6, nome="Mariana", pessoa_id=mariana, nova=False)],
                     [], humor="animada")
    tulipas = next(i for i in b.registro(r1)["itens"] if i.alvo == "tulipas")
    b.criar_registro(TEXTO_TRISTE, date(2026, 9, 28),
                     [Item(TipoItem.ESTADO, "Triste com a notícia do trabalho", TEXTO_TRISTE, Certeza.OBSERVADO,
                           categorias=["Preocupações", "Trabalho"], data_evento=date(2026, 9, 28))],
                     [], [Relacao(tipo="contexto", motivo="Em 20/09 você registrou que ela adora tulipas.", item_novo=0,
                                  item_antigo_id=tulipas.id, sugestao="Talvez levar tulipas num dia comum.")],
                     privado=True)
    b.dispensar_alerta("evento:1:d3")
    return b


def exportacao_de_origem() -> dict:
    """O JSON exportado do diário de origem, com os segredos que um arquivo mexido poderia trazer."""
    b = diario_de_origem()
    try:
        dados = json.loads(json.dumps(b.exportar(), ensure_ascii=False))
    finally:
        b.fechar()
    dados["config"]["ia_pin"] = cofre.criar_verificador(PIN_IA)
    dados["config"]["ia_principal"]["chave"] = CHAVE
    dados["config"]["ia_reserva"] = {"provedor": "groq", "url": "https://api.groq.com/openai/v1",
                                     "modelo": "llama", "tipo": "openai", "chave": "gsk_OUTRA_CHAVE_123456"}
    return dados


def registro_do_json(dados: dict, texto: str) -> dict:
    return next(r for r in dados["registros"] if r["texto"] == texto)


# ---------------------------------------------------------------- base

class Base(unittest.TestCase):
    def servidor(self, **opcoes) -> ServidorDeTeste:
        s = ServidorDeTeste(**opcoes)
        self.addCleanup(s.fechar)
        return s

    def pasta_temporaria(self) -> Path:
        pasta = Path(tempfile.mkdtemp(prefix="entrenos-teste-destino-"))
        self.addCleanup(shutil.rmtree, pasta, True)
        return pasta

    def backup_de(self, s: ServidorDeTeste) -> bytes:
        """A cópia que Ajustes → Backup grava (POST /api/backup), lida de volta."""
        r = s.pedir("POST", "/api/backup", {"pasta": str(self.pasta_temporaria())})
        self.assertEqual(r.status, 200, r.dados)
        return Path(r.dados["arquivo"]).read_bytes()

    def origem(self, pin: str = PIN_BACKUP, **ajustes) -> ServidorDeTeste:
        """Diário de outro computador (PIN do backup): dois registros e a mãe dela."""
        s = self.servidor()
        s.configurar(pin, nome_dela="Julia")
        b = s.app._banco
        b.criar_registro(TEXTO_MAE, date(2026, 9, 20), itens_mae(), pessoas_mae(), [])
        b.criar_registro(TEXTO_MARI, date(2026, 9, 25), [], [], [])
        if ajustes:
            b.salvar_config(**ajustes)
        return s

    @staticmethod
    def restaurar(s: ServidorDeTeste, conteudo: bytes | str, pin: str | None = PIN_BACKUP, *, token=None, **extras):
        corpo = {"arquivo_b64": conteudo if isinstance(conteudo, str) else b64(conteudo), **extras}
        if pin is not None:
            corpo["pin"] = pin
        return s.pedir("POST", "/api/restaurar", corpo, token=token)

    @staticmethod
    def arquivos(s: ServidorDeTeste) -> dict[str, bytes]:
        """Tudo o que há na pasta de dados (para provar que nada mudou), menos o pin_espera.json:
        ele guarda só o bloqueio do PIN errado, para fechar e abrir não zerá-lo (seção 19.7)."""
        pasta = Path(s.pasta)
        return {str(p.relative_to(pasta)): p.read_bytes() for p in sorted(pasta.rglob("*"))
                if p.is_file() and p.name != nucleo.ARQUIVO_ESPERA_PIN}

    @staticmethod
    def textos(s: ServidorDeTeste) -> list[str]:
        r = s.pedir("GET", "/api/registros")
        assert r.status == 200, (r.status, r.dados)
        return sorted(x["texto"] for x in r.dados)


# ---------------------------------------------------------------- cofre

class TestCofreRecebido(Base):
    """cofre.abrir_conteudo e Cofre.restaurar, sem servidor."""

    def setUp(self):
        self.pasta = self.pasta_temporaria()

    def novo_cofre(self) -> cofre.Cofre:
        c = cofre.Cofre(self.pasta)
        c.hoje = lambda: REF
        c.agora = lambda: datetime(2026, 9, 30, 21, 5, 9)
        return c

    def test_abrir_conteudo_nao_toca_no_disco(self):
        conteudo = cifrar(b"dados do backup" * 40)
        recebido = cofre.abrir_conteudo(conteudo, PIN_BACKUP)
        self.addCleanup(recebido.descartar)
        self.assertEqual(recebido.dados, b"dados do backup" * 40)
        self.assertEqual(cofre.tamanho_decifrado(conteudo), len(b"dados do backup" * 40))
        self.assertNotIn(b"dados", repr(recebido).encode())
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_abrir_conteudo_recusa(self):
        conteudo = cifrar(b"x" * 512)
        with self.assertRaises(cofre.PinErrado):
            cofre.abrir_conteudo(conteudo, "outro pin qualquer")
        for estranho in (b"", b"nao e cofre", conteudo[:30], b"ENTRENOS2" + conteudo[9:]):
            with self.subTest(estranho=estranho[:12]), self.assertRaises(cofre.CofreCorrompido):
                cofre.abrir_conteudo(estranho, PIN_BACKUP)
        absurdo = bytearray(conteudo)
        absurdo[9:13] = struct.pack(">I", 2 ** 30)        # scrypt que estouraria a memória
        with self.assertRaises(cofre.CofreCorrompido):
            cofre.abrir_conteudo(bytes(absurdo), PIN_BACKUP)
        with self.assertRaises(cofre.CofreCorrompido):
            cofre.tamanho_decifrado(bytes(absurdo))

    def test_restaurar_guarda_copia_e_troca_o_pin(self):
        atual = self.novo_cofre()
        atual.criar(PIN, b"diario de agora")
        antes = atual.arquivo.read_bytes()
        recebido = cofre.abrir_conteudo(cifrar(b"diario do backup"), PIN_BACKUP)
        copia = atual.restaurar(recebido)
        recebido.descartar()
        self.assertEqual(copia.parent, self.pasta / config.PASTA_BACKUPS)
        self.assertEqual(copia.name, "entrenos-antes-da-restauracao-2026-09-30-210509.cofre")
        self.assertEqual(copia.read_bytes(), antes)                       # cifrada com o PIN antigo
        self.assertEqual(atual.backups(), [])                             # não entra no rodízio
        self.assertTrue(atual.aberto)
        self.assertEqual(self.novo_cofre().abrir(PIN_BACKUP), b"diario do backup")
        with self.assertRaises(cofre.PinErrado):
            self.novo_cofre().abrir(PIN)
        atual.salvar(b"escrito depois")                                   # a chave guardada é a do backup
        self.assertEqual(self.novo_cofre().abrir(PIN_BACKUP), b"escrito depois")
        self.assertEqual(atual.copias_de_restauracao(), [copia])
        # Limpar as cópias só alcança o que abre com a chave de agora: a do diário antigo fica.
        self.assertEqual(atual.reescrever_backups(lambda dados: b"mexido"), 1)
        self.assertEqual(copia.read_bytes(), antes)
        # Outra restauração no mesmo segundo não sobrescreve a cópia.
        recebido = cofre.abrir_conteudo(cifrar(b"outro backup"), PIN_BACKUP)
        segunda = atual.restaurar(recebido, dados=b"ja migrado")
        recebido.descartar()
        self.assertEqual(segunda.name, "entrenos-antes-da-restauracao-2026-09-30-210509-2.cofre")
        self.assertEqual(copia.read_bytes(), antes)
        self.assertEqual(self.novo_cofre().abrir(PIN_BACKUP), b"ja migrado")

    def test_copia_do_mesmo_pin_acompanha_troca_de_pin_e_remocoes(self):
        # Restaurando um backup do próprio diário (mesmo PIN), a cópia de antes abre com a chave
        # de agora: trocar o PIN precisa protegê-la, e o que for apagado precisa sumir dela.
        atual = self.novo_cofre()
        atual.criar(PIN, b"diario de agora")
        recebido = cofre.abrir_conteudo(atual.arquivo.read_bytes(), PIN)
        copia = atual.restaurar(recebido, dados=b"diario restaurado")
        recebido.descartar()
        self.assertEqual(atual.reescrever_backups(lambda dados: dados.replace(b"agora", b"limpo")), 1)
        self.assertEqual(decifrar(copia.read_bytes(), PIN), b"diario de limpo")
        atual.trocar_pin(PIN, PIN_BACKUP)
        with self.assertRaises(cofre.PinErrado):
            decifrar(copia.read_bytes(), PIN)
        self.assertEqual(decifrar(copia.read_bytes(), PIN_BACKUP), b"diario de limpo")
        self.assertEqual(atual.backups(), [])                             # e continua fora do rodízio
        for dia in range(1, config.BACKUPS_AUTOMATICOS + 3):
            atual.hoje = lambda dia=dia: date(2026, 10, dia)
            atual.salvar(b"mais um dia")
        self.assertEqual(len(atual.backups()), config.BACKUPS_AUTOMATICOS)
        self.assertTrue(copia.exists())

    def test_restaurar_sem_diario_nao_faz_copia(self):
        atual = self.novo_cofre()
        recebido = cofre.abrir_conteudo(cifrar(b"diario do backup"), PIN_BACKUP)
        self.assertIsNone(atual.restaurar(recebido))
        recebido.descartar()
        self.assertEqual(self.novo_cofre().abrir(PIN_BACKUP), b"diario do backup")
        self.assertFalse((self.pasta / config.PASTA_BACKUPS).exists())

    def test_sem_copia_nada_muda(self):
        atual = self.novo_cofre()
        atual.criar(PIN, b"diario de agora")
        antes = atual.arquivo.read_bytes()
        (self.pasta / config.PASTA_BACKUPS).write_bytes(b"um arquivo no lugar da pasta")
        recebido = cofre.abrir_conteudo(cifrar(b"diario do backup"), PIN_BACKUP)
        with self.assertRaises(OSError):
            atual.restaurar(recebido)
        self.assertEqual(atual.arquivo.read_bytes(), antes)
        atual.salvar(b"continua com o PIN antigo")
        self.assertEqual(self.novo_cofre().abrir(PIN), b"continua com o PIN antigo")
        recebido.descartar()
        with self.assertRaises(cofre.CofreFechado):                       # descartado não restaura
            atual.restaurar(recebido)


# ---------------------------------------------------------------- banco: resumo, integridade, ids

class TestBancoParaRestaurar(unittest.TestCase):
    def test_resumo(self):
        b = Banco.novo()
        self.addCleanup(b.fechar)
        self.assertEqual(b.resumo(), {"registros": 0, "pessoas": 0, "primeiro_registro": None, "ultimo_registro": None})
        b.criar_registro(TEXTO_MAE, date(2026, 9, 20), itens_mae(), pessoas_mae(), [])
        b.criar_registro(TEXTO_ANTIGO, date(2025, 1, 5), [], [], [])
        b.criar_pessoa("Mariana", "amiga")
        self.assertEqual(b.resumo(), {"registros": 2, "pessoas": 2, "primeiro_registro": date(2025, 1, 5),
                                      "ultimo_registro": date(2026, 9, 20)})

    def test_conferir_integridade(self):
        b = Banco.novo()
        self.addCleanup(b.fechar)
        b.criar_registro(TEXTO_MAE, date(2026, 9, 20), itens_mae(), pessoas_mae(), [])
        b.conferir_integridade()                         # inteiro: não levanta nada
        estragos = {
            "sem a coluna privado": "ALTER TABLE registros DROP COLUMN privado",
            "sem a tabela de pessoas": "DROP TABLE pessoas",
            "ajuste que não é JSON": "INSERT INTO config (chave, valor) VALUES ('nome_dela', 'isso nao e json')",
            "data que não é data": "UPDATE registros SET data_registro = 'ontem'",
        }
        for nome, sql in estragos.items():
            with self.subTest(nome):
                copia = Banco.de_bytes(b.para_bytes())
                self.addCleanup(copia.fechar)
                copia._con.execute(sql)
                with self.assertRaises(ValueError) as erro:
                    copia.conferir_integridade()
                self.assertEqual(str(erro.exception), banco_mod.BANCO_DANIFICADO)

    def test_garantir_ids_acima(self):
        b = Banco.novo()
        self.addCleanup(b.fechar)
        b.criar_registro(TEXTO_ANTIGO, REF, [], [], [])
        self.assertEqual(b.maiores_ids()["registros"], 1)
        b.garantir_ids_acima({"registros": 7, "itens": 12, "pessoas": 0, "outra_tabela": 99, "relacoes": True})
        self.assertEqual(b.criar_registro(TEXTO_MAE, REF, itens_mae(), pessoas_mae(), []), 8)
        self.assertGreater(b.registro(8)["itens"][0].id, 12)
        b.garantir_ids_acima({"registros": 3})           # nunca volta para trás
        self.assertEqual(b.criar_registro(TEXTO_MARI, REF, [], [], []), 9)


# ---------------------------------------------------------------- restaurar no primeiro uso

class TestRestaurarPrimeiroUso(Base):
    def setUp(self):
        self.backup = self.backup_de(self.origem())

    def test_restaura_e_abre_com_o_pin_do_backup(self):
        s = self.servidor()
        self.assertFalse(s.pedir("GET", "/api/estado", token=None).dados["configurado"])
        r = self.restaurar(s, self.backup)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["resumo"], {"registros": 2, "pessoas": 1, "primeiro_registro": "2026-09-20",
                                             "ultimo_registro": "2026-09-25"})
        self.assertIsNone(r.dados["copia"])              # não havia diário para guardar
        s.token = r.dados["token"]
        self.assertGreaterEqual(len(s.token), 40)
        estado = s.pedir("GET", "/api/estado", token=None).dados
        self.assertEqual((estado["configurado"], estado["desbloqueado"]), (True, True))
        self.assertEqual(self.textos(s), sorted([TEXTO_MAE, TEXTO_MARI]))
        self.assertEqual(s.pedir("GET", "/api/config").dados["nome_dela"], "Julia")
        self.assertEqual(decifrar((Path(s.pasta) / config.ARQUIVO_COFRE).read_bytes(), PIN_BACKUP)[:16],
                         b"SQLite format 3\x00")
        # Pública só enquanto não existe diário: agora é como qualquer rota protegida.
        self.assertEqual(self.restaurar(s, self.backup).status, 401)
        self.assertEqual(s.pedir("POST", "/api/configurar", {"pin": "outro pin qualquer"}, token=None).status, 409)
        s.pedir("POST", "/api/bloquear")
        self.assertEqual(self.restaurar(s, self.backup).status, 423)
        self.assertEqual(s.desbloquear(PIN).status, 401)
        self.assertEqual(s.desbloquear(PIN_BACKUP).status, 200)
        self.assertEqual(self.textos(s), sorted([TEXTO_MAE, TEXTO_MARI]))

    def test_aceita_data_url_do_navegador(self):
        s = self.servidor()
        r = self.restaurar(s, "data:application/octet-stream;base64," + b64(self.backup))
        self.assertEqual(r.status, 200, r.dados)

    def test_pin_errado_401_com_bloqueio_sem_criar_nada(self):
        relogio = Relogio()
        s = self.servidor(atraso_pin=0.3, relogio=relogio)
        r = self.restaurar(s, self.backup, "pin que nao e o do backup")
        self.assertEqual(r.status, 401)
        self.assertEqual((r.dados["erro"], r.dados["espere_segundos"]), (servidor.PIN_DO_BACKUP, 1))
        self.assertEqual(s.app._falhas_pin, 1)           # o mesmo contador do desbloqueio
        self.assertAlmostEqual(s.app.espera_do_pin(), 0.3)
        self.assertEqual(self.arquivos(s), {})
        # Só o bloqueio do PIN foi para o disco: contadores e horários, nada do backup nem do PIN.
        espera = json.loads((Path(s.pasta) / nucleo.ARQUIVO_ESPERA_PIN).read_text("utf-8"))
        self.assertEqual(set(espera), {"principal", "visto"})
        self.assertEqual(set(espera["principal"]), {"falhas", "livre_em"})
        self.assertFalse(s.app.cofre.aberto)
        # Dentro do bloqueio, nem o PIN certo do backup é testado (seção 19).
        r = self.restaurar(s, self.backup)
        self.assertEqual((r.status, r.dados["espere_segundos"]), (429, 1))
        self.assertEqual(self.arquivos(s), {})
        relogio.avancar(0.31)
        self.assertEqual(self.restaurar(s, self.backup, "outro pin errado ainda").status, 401)
        self.assertAlmostEqual(s.app.espera_do_pin(), 0.6)         # dobra, como no desbloqueio
        relogio.avancar(0.61)
        self.assertEqual(self.restaurar(s, self.backup).status, 200)
        self.assertEqual(s.app._falhas_pin, 0)
        self.assertFalse((Path(s.pasta) / nucleo.ARQUIVO_ESPERA_PIN).exists())     # acertou: sai

    def test_arquivo_que_nao_e_cofre_400_sem_gastar_tentativa(self):
        s = self.servidor(atraso_pin=0.5)
        cortado_no_meio = self.backup[:len(self.backup) // 2]
        absurdo = bytearray(self.backup)
        absurdo[9:13] = struct.pack(">I", 2 ** 30)
        casos = {
            "json": json.dumps({"app": "entre-nos"}).encode(),
            "sqlite sem criptografia": Banco.novo().para_bytes(),
            "bytes aleatórios": os.urandom(5000),
            "só o começo do cabeçalho": b"ENTRENOS1" + b"\0" * 10,
            "cortado no meio": cortado_no_meio,
            "sem os últimos bytes": self.backup[:-100],
            "sem a etiqueta do GCM": self.backup[:-16],
            "cortado no cabeçalho": self.backup[:30],
            "scrypt absurdo": bytes(absurdo),
        }
        for nome, conteudo in casos.items():
            with self.subTest(nome):
                inicio = time.monotonic()
                r = self.restaurar(s, conteudo)
                self.assertLess(time.monotonic() - inicio, 0.45)
                self.assertEqual(r.status, 400, r.dados)
                self.assertEqual(r.dados["erro"], servidor.NAO_E_BACKUP)
        for nome, corpo in {"base64 inválido": {"arquivo_b64": "@@@ não é base64", "pin": PIN_BACKUP},
                            "sem arquivo": {"pin": PIN_BACKUP}, "arquivo vazio": {"arquivo_b64": "", "pin": PIN_BACKUP},
                            "arquivo em número": {"arquivo_b64": 12, "pin": PIN_BACKUP},
                            "sem PIN": {"arquivo_b64": b64(self.backup)},
                            "PIN em número": {"arquivo_b64": b64(self.backup), "pin": 1234567890}}.items():
            with self.subTest(nome):
                r = s.pedir("POST", "/api/restaurar", corpo, token=None)
                self.assertEqual(r.status, 400, r.dados)
        self.assertEqual(s.app._falhas_pin, 0)
        self.assertEqual(self.arquivos(s), {})

    def test_banco_danificado_dentro_de_cofre_valido_400(self):
        s = self.servidor()
        grande = Banco.novo()
        for i in range(30):
            grande.criar_registro(f"Registro {i}: " + "um texto comprido para ocupar páginas. " * 6, REF, [], [], [])
        estragado = bytearray(grande.para_bytes())
        estragado[3 * 4096:3 * 4096 + 64] = b"\xff" * 64
        sem_tabelas = sqlite3.connect(":memory:")
        sem_tabelas.executescript("CREATE TABLE outra (a); PRAGMA user_version = 3;")
        casos = {
            "zeros": b"\0" * 4096,
            "página estragada": bytes(estragado),
            "outro banco com o número de esquema certo": sem_tabelas.serialize(),
        }
        sem_tabelas.close()
        for nome, dados in casos.items():
            with self.subTest(nome):
                r = self.restaurar(s, cifrar(dados))
                self.assertEqual(r.status, 400, r.dados)
                self.assertEqual(r.dados["erro"], servidor.BACKUP_DANIFICADO)
        futuro = sqlite3.connect(":memory:")
        futuro.executescript(banco_mod._ESQUEMA_V1 + "PRAGMA user_version = 99;")
        r = self.restaurar(s, cifrar(futuro.serialize()))
        futuro.close()
        self.assertEqual(r.status, 400)
        self.assertIn("versão mais nova", r.dados["erro"])
        self.assertEqual(self.arquivos(s), {})
        self.assertFalse(s.app.cofre.aberto)
        self.assertFalse(s.pedir("GET", "/api/estado", token=None).dados["configurado"])

    def test_esquema_antigo_migra(self):
        for versao in (1, 2):
            with self.subTest(versao=versao):
                s = self.servidor()
                antigos = {"nome_dela": "Julia", "motor": "ollama", "motor_modelo": "qwen3:4b-instruct"}
                r = self.restaurar(s, cifrar(banco_antigo(versao, antigos)))
                self.assertEqual(r.status, 200, r.dados)
                self.assertEqual(r.dados["resumo"]["registros"], 1)
                s.token = r.dados["token"]
                registros = s.pedir("GET", "/api/registros").dados
                self.assertEqual([(x["texto"], x["privado"]) for x in registros], [(TEXTO_ANTIGO, False)])
                self.assertEqual(s.pedir("GET", "/api/config").dados["nome_dela"], "Julia")
                self.assertEqual(s.app._banco.config()["ia_principal"]["provedor"], "ollama")
                # O arquivo gravado já é do esquema atual (não precisa migrar de novo).
                gravado = decifrar((Path(s.pasta) / config.ARQUIVO_COFRE).read_bytes(), PIN_BACKUP)
                self.assertEqual(versao_do_banco(gravado), banco_mod.ESQUEMA_VERSAO)


# ---------------------------------------------------------------- restaurar com o app aberto

class TestRestaurarComAppAberto(Base):
    def setUp(self):
        self.backup = self.backup_de(self.origem(
            ia_pin=cofre.criar_verificador(PIN_IA_BACKUP),
            ia_principal={"provedor": "outro", "url": "http://127.0.0.1:9/v1", "modelo": "modelo-do-backup",
                          "tipo": "openai", "chave": None}))
        self.relogio = Relogio()             # o bloqueio do PIN errado anda só quando o teste manda
        self.s = self.servidor(atraso_pin=0.2, relogio=self.relogio)
        self.s.configurar(PIN, nome_dela="Ana")
        self.s.app._banco.criar_registro(TEXTO_ANTIGO, REF, [], [], [])
        r = self.s.pedir("POST", "/api/ia/pin", {"novo": PIN_IA})
        self.assertEqual(r.status, 200, r.dados)

    def ia_estado(self) -> dict:
        r = self.s.pedir("GET", "/api/ia/estado")
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def test_pin_atual_errado_401_sem_trocar_nada(self):
        s = self.s
        antes, token = self.arquivos(s), s.token
        r = self.restaurar(s, self.backup, token=token, pin_atual="nao e o pin de agora")
        self.assertEqual(r.status, 401)
        self.assertEqual((r.dados["erro"], r.dados["espere_segundos"]), ("O PIN atual não confere.", 1))
        self.assertEqual(s.app._falhas_pin, 1)
        # Dentro do bloqueio, nenhum dos dois PINs é testado.
        self.assertEqual(self.restaurar(s, self.backup, token=token, pin_atual=PIN).status, 429)
        self.assertEqual(s.app._falhas_pin, 1)
        self.relogio.avancar(0.21)
        # PIN do backup errado (com o PIN atual certo): também 401, e nada muda.
        r = self.restaurar(s, self.backup, "pin que nao e o do backup", token=token, pin_atual=PIN)
        self.assertEqual(r.status, 401)
        self.assertEqual(r.dados["erro"], servidor.PIN_DO_BACKUP)
        self.assertEqual(self.restaurar(s, self.backup, token=token).status, 400)       # sem pin_atual
        self.assertEqual(self.restaurar(s, self.backup, token=None, pin_atual=PIN).status, 401)
        self.assertEqual(self.restaurar(s, self.backup, token="token-inventado", pin_atual=PIN).status, 401)
        self.assertEqual(self.arquivos(s), antes)
        self.assertEqual(s.token, token)
        self.assertEqual(self.textos(s), [TEXTO_ANTIGO])                  # a sessão continua
        self.assertTrue(self.ia_estado()["liberada"])

    def test_pin_certo_troca_guarda_copia_e_abre_sessao_nova(self):
        s = self.s
        arquivo = Path(s.pasta) / config.ARQUIVO_COFRE
        antes, token_antigo = arquivo.read_bytes(), s.token
        r = self.restaurar(s, self.backup, token=token_antigo, pin_atual=PIN)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["resumo"], {"registros": 2, "pessoas": 1, "primeiro_registro": "2026-09-20",
                                             "ultimo_registro": "2026-09-25"})
        # A cópia do diário de antes: em backups/, com o que estava lá (PIN antigo). Ela é gravada
        # da memória (o diário vivo é salvo antes), então os bytes cifrados mudam, o conteúdo não.
        copia = Path(r.dados["copia"])
        self.assertEqual(copia.parent, Path(s.pasta) / config.PASTA_BACKUPS)
        self.assertRegex(copia.name, RE_COPIA)
        self.assertEqual(conteudo_do_diario(decifrar(copia.read_bytes(), PIN)),
                         conteudo_do_diario(decifrar(antes, PIN)))
        self.assertFalse(r.dados["danificado"])
        self.assertIn(TEXTO_ANTIGO.encode(), decifrar(copia.read_bytes(), PIN))
        self.assertNotIn(copia, s.app.cofre.backups())
        # Sessão nova: o token antigo morre.
        self.assertNotEqual(r.dados["token"], token_antigo)
        self.assertEqual(s.pedir("GET", "/api/registros", token=token_antigo).status, 401)
        s.token = r.dados["token"]
        self.assertEqual(self.textos(s), sorted([TEXTO_MAE, TEXTO_MARI]))
        self.assertEqual(s.pedir("GET", "/api/config").dados["nome_dela"], "Julia")
        # O arquivo agora abre com o PIN do backup, e só com ele.
        self.assertEqual(versao_do_banco(decifrar(arquivo.read_bytes(), PIN_BACKUP)), banco_mod.ESQUEMA_VERSAO)
        s.pedir("POST", "/api/bloquear")
        self.assertEqual(s.desbloquear(PIN).status, 401)
        self.relogio.avancar(0.21)                       # passa o bloqueio do PIN errado
        self.assertEqual(s.desbloquear(PIN_BACKUP).status, 200)
        self.assertEqual(self.textos(s), sorted([TEXTO_MAE, TEXTO_MARI]))

    def test_area_e_configuracao_da_ia_passam_a_ser_as_do_backup(self):
        s = self.s
        self.assertTrue(self.ia_estado()["liberada"])
        r = self.restaurar(s, self.backup, token=s.token, pin_atual=PIN)
        self.assertEqual(r.status, 200, r.dados)
        s.token = r.dados["token"]
        estado = self.ia_estado()
        self.assertEqual((estado["pin_definido"], estado["liberada"], estado["configurada"]), (True, False, True))
        self.assertEqual(s.pedir("GET", "/api/ia/config").status, 403)       # área fechada
        self.assertEqual(s.app._banco.config()["ia_principal"]["modelo"], "modelo-do-backup")
        self.assertEqual(s.pedir("POST", "/api/ia/entrar", {"pin_ia": PIN_IA}).status, 401)     # o PIN da IA de antes
        self.relogio.avancar(0.21)                       # passa o bloqueio do PIN da IA errado
        self.assertEqual(s.pedir("POST", "/api/ia/entrar", {"pin_ia": PIN_IA_BACKUP}).status, 200)

    def test_restaurar_copia_antiga_do_mesmo_diario_nao_repete_ids(self):
        s = self.s
        copia_antiga = self.backup_de(s)                 # só o registro #1
        for texto in (TEXTO_MARI, TEXTO_TRISTE):
            self.assertEqual(s.pedir("POST", "/api/registros", {"texto": texto, "data_registro": "2026-09-30"}).status,
                             200)
        maiores = s.app._banco.maiores_ids()
        self.assertEqual(maiores["registros"], 3)
        r = self.restaurar(s, copia_antiga, PIN, token=s.token, pin_atual=PIN)
        self.assertEqual(r.status, 200, r.dados)
        s.token = r.dados["token"]
        self.assertEqual(self.textos(s), [TEXTO_ANTIGO])
        novo = s.pedir("POST", "/api/registros", {"texto": "Ela quer ver o mar.", "data_registro": "2026-09-30"})
        self.assertEqual(novo.status, 200, novo.dados)
        self.assertGreater(novo.dados["id"], 3)          # #2 e #3 seguem nos backups automáticos

    def test_bloqueado_423(self):
        s = self.s
        token = s.token
        s.pedir("POST", "/api/bloquear")
        antes = self.arquivos(s)
        self.assertEqual(self.restaurar(s, self.backup, token=token, pin_atual=PIN).status, 423)
        self.assertEqual(self.restaurar(s, self.backup, token=None).status, 423)
        self.assertEqual(self.arquivos(s), antes)


# ---------------------------------------------------------------- importar JSON

class BaseImportar(Base):
    def setUp(self):
        self.s = self.servidor()
        self.s.configurar(PIN, nome_dela="Ana")
        # O diário daqui já tem o primeiro registro da origem (e com ele a mãe dela, Sandra).
        self.s.app._banco.criar_registro(TEXTO_MAE, date(2026, 9, 20), itens_mae(), pessoas_mae(), [],
                                         contexto="no jantar")

    def importar(self, dados, previa: bool = False, **extras):
        texto = dados if isinstance(dados, str) else json.dumps(dados, ensure_ascii=False)
        corpo = {"json": texto, **extras}
        if previa:
            return self.s.pedir("POST", "/api/importar/previa", corpo)
        corpo.setdefault("confirmar", True)
        return self.s.pedir("POST", "/api/importar", corpo)

    def foto(self) -> tuple:
        """Tudo o que poderia mudar: arquivos da pasta de dados, contagens, ajustes e histórico."""
        b = self.s.app._banco
        contagens = {t: b._con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("registros", "itens", "pessoas", "relacoes", "registro_pessoas", "busca_fts")}
        return self.arquivos(self.s), contagens, b.config(), b.historico(1000)

    def pessoa(self, nome: str) -> dict:
        return next(p for p in self.s.pedir("GET", "/api/pessoas").dados if p["nome"] == nome)

    def registro(self, texto: str) -> dict:
        rid = next(x["id"] for x in self.s.pedir("GET", "/api/registros").dados if x["texto"] == texto)
        return self.s.pedir("GET", f"/api/registros/{rid}").dados


class TestImportar(BaseImportar):
    def test_previa_nao_grava_nada(self):
        antes = self.foto()
        r = self.importar(exportacao_de_origem(), previa=True)
        self.assertEqual(r.status, 200, r.dados)
        resumo = {k: v for k, v in r.dados.items() if k not in ("avisos", "detalhes_pessoas")}
        self.assertEqual(resumo, {"registros_novos": 2, "registros_repetidos": 1, "registros_repetidos_no_arquivo": 0,
                                  "registros_marcados_privados": 0, "pessoas_novas": 1, "pessoas_existentes": 1,
                                  "pessoas_completadas": 0, "itens": 2})
        # A prévia diz quem entra e com quem cada pessoa do arquivo foi casada.
        self.assertEqual(r.dados["detalhes_pessoas"],
                         {"novas": [{"rotulo": "Mariana (melhor amiga)", "parecida_com": None}],
                          "existentes": [{"arquivo": "Sandra (mãe)", "daqui": "Sandra (mãe)"}], "completadas": []})
        self.assertNotIn("importados", r.dados)
        self.assertTrue(any("não são importados" in a for a in r.dados["avisos"]))
        self.assertEqual(self.foto(), antes)
        # A prévia não gasta ids: a importação de verdade dá os mesmos números.
        self.assertEqual(self.s.app._banco.maiores_ids()["registros"], 1)

    def test_juntar(self):
        dados = exportacao_de_origem()
        sandra = self.pessoa("Sandra")
        tulipas_daqui = next(i for i in self.registro(TEXTO_MAE)["itens"] if i["alvo"] == "tulipas")
        r = self.importar(dados)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual({k: v for k, v in r.dados.items() if k not in ("avisos", "detalhes_pessoas")},
                         {"registros_novos": 2, "registros_repetidos": 1, "registros_repetidos_no_arquivo": 0,
                          "registros_marcados_privados": 0, "pessoas_novas": 1, "pessoas_existentes": 1,
                          "pessoas_completadas": 0, "itens": 2, "importados": 2})
        self.assertEqual(self.textos(self.s), sorted([TEXTO_MAE, TEXTO_MARI, TEXTO_TRISTE]))
        # Pessoa casada (Sandra, a mãe) não duplica; pessoa nova (Mariana) vem com tudo o que tinha.
        pessoas = self.s.pedir("GET", "/api/pessoas").dados
        self.assertEqual(sorted(p["nome"] for p in pessoas), ["Mariana", "Sandra"])
        self.assertEqual(self.pessoa("Sandra")["id"], sandra["id"])
        mariana = self.pessoa("Mariana")
        self.assertEqual((mariana["relacao"], mariana["apelidos"], mariana["observacoes"], mariana["aniversario"]),
                         ("melhor amiga", ["Mari"], "Se conheceram na faculdade.", "1998-03-12"))
        # Itens e ligações com os ids daqui.
        viagem = self.registro(TEXTO_MARI)
        self.assertEqual(viagem["humor"], "animada")
        self.assertEqual([p["id"] for p in viagem["pessoas"]], [mariana["id"]])
        self.assertEqual([(i["pessoa_id"], i["certeza"]) for i in viagem["itens"]], [(mariana["id"], "confirmado")])
        triste = self.registro(TEXTO_TRISTE)
        self.assertTrue(triste["privado"])                               # 🔒 continua 🔒
        self.assertFalse(viagem["privado"])
        [relacao] = triste["relacoes"]
        self.assertEqual((relacao["item_novo"], relacao["item_antigo_id"], relacao["registro_antigo_id"]),
                         (triste["itens"][0]["id"], tulipas_daqui["id"], self.registro(TEXTO_MAE)["id"]))
        self.assertEqual(relacao["sugestao"], "Talvez levar tulipas num dia comum.")
        # Nada do que é deste diário vem do arquivo: ajustes, PIN da IA, chaves, avisos dispensados.
        b = self.s.app._banco
        ajustes = b.config()
        self.assertEqual(ajustes["nome_dela"], "Ana")
        self.assertIsNone(ajustes["ia_pin"])
        self.assertIsNone(ajustes["ia_principal"])
        self.assertIsNone(ajustes["ia_reserva"])
        self.assertEqual(b.alertas_dispensados(), set())
        self.assertFalse(self.s.pedir("GET", "/api/ia/estado").dados["pin_definido"])
        # Histórico: só "importação: N registros", sem conteúdo.
        historico = self.s.pedir("GET", "/api/historico").dados
        self.assertEqual(historico[0]["resumo"], "importação: 2 registros")
        tudo = json.dumps(historico, ensure_ascii=False)
        for segredo in (CHAVE, "gsk_OUTRA", "Recife", "Mariana", "Julia", "tulipas"):
            self.assertNotIn(segredo, tudo)
        # Tudo foi para o arquivo cifrado e continua lá depois de bloquear.
        self.s.pedir("POST", "/api/bloquear")
        self.assertEqual(self.s.desbloquear(PIN).status, 200)
        self.assertEqual(self.textos(self.s), sorted([TEXTO_MAE, TEXTO_MARI, TEXTO_TRISTE]))
        self.assertTrue(self.s.pedir("GET", "/api/busca?q=Recife").dados["encontrados"])

    def test_relacoes_quebradas_e_itens_sem_trecho_ficam_de_fora(self):
        dados = exportacao_de_origem()
        triste = registro_do_json(dados, TEXTO_TRISTE)
        mae = registro_do_json(dados, TEXTO_MAE)
        triste["relacoes"] += [
            {"tipo": "contexto", "motivo": "Registro que não veio.", "item_novo": triste["itens"][0]["id"],
             "registro_antigo_id": 999},
            {"tipo": "contexto", "motivo": "Item que não veio.", "item_novo": triste["itens"][0]["id"],
             "item_antigo_id": 998},
            {"tipo": "mesma_pessoa", "motivo": "O item novo é de outro registro.", "item_novo": mae["itens"][0]["id"]},
        ]
        triste["itens"].append({**triste["itens"][0], "id": 500, "trecho": "Uma frase que não está no texto."})
        r = self.importar(dados)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["itens"], 2)
        self.assertIn("3 ligações entre registros apontavam para algo que não veio no arquivo e ficaram de fora.",
                      r.dados["avisos"])
        self.assertIn("1 item não cita um trecho do texto do registro e ficou de fora.", r.dados["avisos"])
        registro = self.registro(TEXTO_TRISTE)
        self.assertEqual(len(registro["relacoes"]), 1)
        self.assertEqual([i["trecho"] for i in registro["itens"]], [TEXTO_TRISTE])

    def test_importar_duas_vezes_nao_duplica(self):
        dados = exportacao_de_origem()
        self.assertEqual(self.importar(dados).status, 200)
        foto = self.foto()
        r = self.importar(dados)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual({k: v for k, v in r.dados.items() if k not in ("avisos", "detalhes_pessoas")},
                         {"registros_novos": 0, "registros_repetidos": 3, "registros_repetidos_no_arquivo": 0,
                          "registros_marcados_privados": 0, "pessoas_novas": 0, "pessoas_existentes": 2,
                          "pessoas_completadas": 0, "itens": 0, "importados": 0})
        self.assertIn("Todos os registros deste arquivo já estão no diário: nada novo para juntar.", r.dados["avisos"])
        _, contagens, ajustes, historico = self.foto()
        self.assertEqual((contagens, ajustes, historico), foto[1:])
        # Repetido é pela data e pelo texto normalizado (maiúsculas, acentos e espaços não contam).
        mexido = copy.deepcopy(dados)
        viagem = registro_do_json(mexido, TEXTO_MARI)
        viagem["texto"], viagem["itens"] = "a  MARI me contou que vai viajar para recife no feriado.", []
        self.assertEqual(self.importar(mexido, previa=True).dados["registros_novos"], 0)
        outra_data = copy.deepcopy(dados)
        registro_do_json(outra_data, TEXTO_MARI)["data_registro"] = "2026-09-26"
        self.assertEqual(self.importar(outra_data, previa=True).dados["registros_novos"], 1)

    def test_sem_confirmar_nao_importa(self):
        antes = self.foto()
        for confirmar in (None, False, "sim", 1):
            with self.subTest(confirmar=confirmar):
                r = self.importar(exportacao_de_origem(), confirmar=confirmar)
                self.assertEqual(r.status, 400, r.dados)
        self.assertEqual(self.foto(), antes)

    def test_arquivo_com_problema_400_sem_gravar_nada(self):
        antes = self.foto()
        base = exportacao_de_origem()

        def com(mudar) -> str:
            dados = copy.deepcopy(base)
            mudar(dados)
            return json.dumps(dados, ensure_ascii=False)

        casos = {
            "não é JSON": "{isso não é json",
            "JSON com NaN": '{"app": "entre-nos", "versao": "0.1.0", "registros": [], "x": NaN}',
            "texto inválido em UTF-8": '{"app": "entre-nos", "versao": "0.1.0", "registros": [], "x": "\\ud800"}',
            "lista em vez de objeto": "[1, 2, 3]",
            "JSON de outro app": json.dumps({"app": "outro-diario", "versao": "1.0", "registros": []}),
            "sem app": json.dumps({"versao": "0.1.0", "registros": []}),
            "esquema do futuro": com(lambda d: d.update(esquema=99)),
            "sem versão": com(lambda d: d.pop("versao")),
            "registros não é lista": com(lambda d: d.update(registros={"1": "x"})),
            "texto em número": com(lambda d: d["registros"][1].update(texto=123)),
            "texto vazio": com(lambda d: d["registros"][1].update(texto="   ")),
            "data em outro formato": com(lambda d: d["registros"][1].update(data_registro="25/09/2026")),
            "privado em texto": com(lambda d: d["registros"][2].update(privado="sim")),
            "importância em texto": com(lambda d: d["registros"][1]["itens"][0].update(importancia="3")),
            "importância verdadeiro": com(lambda d: d["registros"][1]["itens"][0].update(importancia=True)),
            "tipo de item inventado": com(lambda d: d["registros"][1]["itens"][0].update(tipo="fofoca")),
            "certeza inventada": com(lambda d: d["registros"][1]["itens"][0].update(certeza="absoluta")),
            "categorias em texto": com(lambda d: d["registros"][1]["itens"][0].update(categorias="Amigos")),
            "item que não é objeto": com(lambda d: d["registros"][1]["itens"].append("item")),
            "relação sem tipo": com(lambda d: d["registros"][2]["relacoes"][0].pop("tipo")),
            "apelidos em texto": com(lambda d: d["pessoas"][0].update(apelidos="Mari")),
            "aniversário inválido": com(lambda d: d["pessoas"][0].update(aniversario="1998-02-30")),
            "pessoa sem id": com(lambda d: d["pessoas"][0].pop("id")),
            "registros com o mesmo id": com(lambda d: d["registros"][1].update(id=d["registros"][0]["id"])),
            "pessoas com o mesmo id": com(lambda d: d["pessoas"][1].update(id=d["pessoas"][0]["id"])),
            # Erro só no último registro: nada dos anteriores entra.
            "erro no último registro": com(lambda d: d["registros"][-1].update(humor=["triste"])),
        }
        for nome, texto in casos.items():
            for previa in (True, False):
                with self.subTest(nome, previa=previa):
                    r = self.importar(texto, previa=previa)
                    self.assertEqual(r.status, 400, r.dados)
                    self.assertIn("erro", r.dados)
        for nome, corpo in {"json em objeto": {"json": base, "confirmar": True},
                            "sem json": {"confirmar": True}, "json vazio": {"json": "  ", "confirmar": True}}.items():
            with self.subTest(nome):
                self.assertEqual(self.s.pedir("POST", "/api/importar", corpo).status, 400)
        self.assertEqual(self.foto(), antes)

    def test_mensagens_de_erro_dizem_onde_sem_mostrar_conteudo(self):
        dados = exportacao_de_origem()
        registro_do_json(dados, TEXTO_MARI)["itens"][0]["importancia"] = "muito alta"
        r = self.importar(dados)
        self.assertEqual(r.status, 400)
        self.assertEqual(r.dados["erro"], "O arquivo tem um dado com formato errado em “registros[1].itens[0].importancia”.")
        self.assertNotIn("muito alta", r.dados["erro"])
        r = self.importar(json.dumps({"app": "outro"}))
        self.assertEqual(r.dados["erro"], banco_mod.NAO_E_EXPORTACAO)

    def test_falha_no_meio_desfaz_tudo(self):
        antes = self.foto()
        with mock.patch.object(banco_mod.Banco, "_inserir_relacao_importada", side_effect=RuntimeError("no meio")):
            r = self.importar(exportacao_de_origem())
        self.assertEqual(r.status, 500)
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.importar(exportacao_de_origem()).status, 200)       # e depois funciona

    def test_grande_demais(self):
        antes = self.foto()
        dados = exportacao_de_origem()
        with mock.patch.object(banco_mod, "MAX_IMPORTAR_REGISTROS", 2):
            r = self.importar(dados)
        self.assertEqual((r.status, r.dados["erro"]), (400, banco_mod.GRANDE_DEMAIS))
        with mock.patch.object(banco_mod, "MAX_IMPORTAR_ITENS", 3):
            r = self.importar(dados, previa=True)
        self.assertEqual((r.status, r.dados["erro"]), (400, banco_mod.GRANDE_DEMAIS))
        with mock.patch.object(banco_mod, "MAX_JSON_IMPORTAR", 1000):
            r = self.importar(dados, previa=True)
        self.assertEqual((r.status, r.dados["erro"]), (400, banco_mod.GRANDE_DEMAIS))
        self.assertEqual(self.foto(), antes)

    def test_corpo_de_ate_64_mb_so_nestas_rotas(self):
        # 2 MB passa na importação (e não passaria nas outras rotas, que param em 1 MB).
        dados = exportacao_de_origem()
        registro_do_json(dados, TEXTO_MARI)["contexto"] = "c" * (2 * 1024 * 1024)
        texto = json.dumps(dados, ensure_ascii=False)
        self.assertGreater(len(texto), servidor.LIMITE_CORPO)
        r = self.importar(texto, previa=True)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(self.s.pedir("POST", "/api/analisar", {"texto": texto}).status, 413)
        grandes = {(rota.metodo, rota.padrao.pattern.replace("\\", "")) for rota in servidor.ROTAS
                   if rota.limite_corpo > servidor.LIMITE_CORPO}
        # Seção 22: o arquivo de sincronização de outro aparelho também vem no corpo.
        self.assertEqual(grandes, {("POST", "/api/restaurar"), ("POST", "/api/importar/previa"),
                                   ("POST", "/api/importar"), ("POST", "/api/sync/receber")})
        self.assertEqual(servidor.LIMITE_CORPO_ARQUIVO, 64 * 1024 * 1024)

    def anunciar(self, rota: str, tamanho: int, token: str | None) -> int:
        """Só o cabeçalho com o tamanho: a resposta chega sem o corpo ser mandado."""
        conexao = http.client.HTTPConnection(servidor.ENDERECO, self.s.porta, timeout=10)
        try:
            conexao.putrequest("POST", rota)
            conexao.putheader("Content-Type", "application/json")
            conexao.putheader("Content-Length", str(tamanho))
            if token:
                conexao.putheader("X-EntreNos-Token", token)
            conexao.endheaders()
            return conexao.getresponse().status
        finally:
            conexao.close()

    def test_mais_de_64_mb_413_e_sem_token_nem_le_o_corpo(self):
        acima = servidor.LIMITE_CORPO_ARQUIVO + 1
        for rota in ("/api/importar", "/api/importar/previa", "/api/restaurar"):
            with self.subTest(rota=rota):
                self.assertEqual(self.anunciar(rota, acima, self.s.token), 413)
                self.assertEqual(self.anunciar(rota, 50 * 1024 * 1024, None), 401)
        # (Acima de 8 MB o servidor nem espera o corpo chegar para responder; abaixo, lê e descarta.)
        self.assertEqual(self.anunciar("/api/analisar", 10 * 1024 * 1024, self.s.token), 413)
        # No primeiro uso, restaurar não pede token, mas o limite vale.
        vazio = self.servidor()
        conexao = http.client.HTTPConnection(servidor.ENDERECO, vazio.porta, timeout=10)
        try:
            conexao.putrequest("POST", "/api/restaurar")
            conexao.putheader("Content-Type", "application/json")
            conexao.putheader("Content-Length", str(acima))
            conexao.endheaders()
            self.assertEqual(conexao.getresponse().status, 413)
        finally:
            conexao.close()

    def test_bloqueado_423(self):
        s = self.s
        token = s.token
        s.pedir("POST", "/api/bloquear")
        for previa in (True, False):
            r = s.pedir("POST", "/api/importar/previa" if previa else "/api/importar",
                        {"json": json.dumps(exportacao_de_origem()), "confirmar": True}, token=token)
            self.assertEqual(r.status, 423)


class TestCasarPessoas(unittest.TestCase):
    """Como as pessoas do arquivo casam com as do diário (Banco.importar_json direto)."""

    def banco(self) -> Banco:
        b = Banco.novo()
        self.addCleanup(b.fechar)
        return b

    @staticmethod
    def exportar(b: Banco) -> str:
        return json.dumps(b.exportar(), ensure_ascii=False)

    def test_relacao_sem_nome_ganha_o_nome_do_arquivo(self):
        origem = self.banco()
        origem.criar_registro(TEXTO_MAE, date(2026, 9, 20), itens_mae(), pessoas_mae(), [])
        daqui = self.banco()
        mae = daqui.criar_pessoa(None, "mãe")
        resumo = daqui.importar_json(self.exportar(origem))
        self.assertEqual((resumo["pessoas_novas"], resumo["pessoas_existentes"]), (0, 1))
        self.assertEqual([(p["id"], p["nome"], p["relacao"]) for p in daqui.pessoas()], [(mae, "Sandra", "mãe")])
        [item] = [i for i in daqui.itens() if i.pessoa_id is not None]
        self.assertEqual(item.pessoa_id, mae)

    def test_por_apelido(self):
        origem = self.banco()
        origem.criar_pessoa("Mari", None)
        daqui = self.banco()
        mariana = daqui.criar_pessoa("Mariana", "melhor amiga", apelidos=["Mari"])
        resumo = daqui.importar_json(self.exportar(origem))
        self.assertEqual((resumo["pessoas_novas"], resumo["pessoas_existentes"]), (0, 1))
        self.assertEqual([p["id"] for p in daqui.pessoas()], [mariana])

    def test_homonimos_continuam_dois_e_nao_duplicam_de_novo(self):
        origem = self.banco()
        origem.criar_pessoa("Mariana", "amiga")
        origem.criar_pessoa("Mariana", "prima")
        daqui = self.banco()
        amiga = daqui.criar_pessoa("Mariana", "amiga")
        resumo = daqui.importar_json(self.exportar(origem))
        self.assertEqual((resumo["pessoas_novas"], resumo["pessoas_existentes"]), (1, 1))
        self.assertEqual(sorted((p["nome"], p["relacao"]) for p in daqui.pessoas()),
                         [("Mariana", "amiga"), ("Mariana", "prima")])
        self.assertEqual(daqui.pessoas()[0]["id"], amiga)
        resumo = daqui.importar_json(self.exportar(origem))
        self.assertEqual((resumo["pessoas_novas"], resumo["pessoas_existentes"]), (0, 2))
        self.assertEqual(len(daqui.pessoas()), 2)

    def test_completa_sem_sobrescrever(self):
        origem = self.banco()
        pid = origem.criar_pessoa("Sandra", "mãe", apelidos=["Sandrinha"], observacoes="Adora novelas.")
        origem.atualizar_pessoa(pid, aniversario=date(1970, 5, 3))
        daqui = self.banco()
        minha = daqui.criar_pessoa("Sandra", None, observacoes="Mora em Campinas.")
        daqui.importar_json(self.exportar(origem))
        [p] = daqui.pessoas()
        self.assertEqual((p["id"], p["relacao"], p["apelidos"], p["observacoes"], p["aniversario"]),
                         (minha, "mãe", ["Sandrinha"], "Mora em Campinas.", date(1970, 5, 3)))

    def test_pessoa_que_so_veio_do_texto_sai_junto_com_o_registro(self):
        # "Apagar é apagar": a Sandra criada pela importação só existe pelo registro que a cita.
        origem = self.banco()
        origem.criar_registro(TEXTO_MAE, date(2026, 9, 20), itens_mae(), pessoas_mae(), [])
        daqui = self.banco()
        daqui.importar_json(self.exportar(origem))
        [rid] = [r["id"] for r in daqui.registros()]
        self.assertEqual(len(daqui.pessoas()), 1)
        daqui.apagar_registro(rid)
        self.assertEqual(daqui.pessoas(), [])

    def test_banco_aceita_texto_bytes_e_dict_e_salva_uma_vez(self):
        origem = self.banco()
        origem.criar_registro(TEXTO_MAE, date(2026, 9, 20), itens_mae(), pessoas_mae(), [])
        texto = self.exportar(origem)
        salvos: list[bytes] = []
        for dados in (texto, texto.encode("utf-8"), json.loads(texto)):
            with self.subTest(tipo=type(dados).__name__):
                daqui = self.banco()
                daqui.ao_salvar = salvos.append
                antes = len(salvos)
                self.assertEqual(daqui.importar_json(dados, previa=True)["registros_novos"], 1)
                self.assertEqual(len(salvos), antes)                     # prévia não salva
                self.assertEqual(daqui.importar_json(dados)["importados"], 1)
                self.assertEqual(len(salvos), antes + 1)
        with self.assertRaises(ValueError):
            self.banco().importar_json(texto, previa="sim")


# ---------------------------------------------------------------- ida e volta

class TestIdaEVolta(Base):
    """Exportar (GET /api/exportar) e importar num diário vazio reproduz registros, itens e pessoas."""

    @staticmethod
    def normalizar(exportado: dict) -> dict:
        """O conteúdo sem os números (ids) do banco: pessoa, item e registro viram o que eles são."""
        pessoas = {p["id"]: (p["nome"], p["relacao"]) for p in exportado["pessoas"]}
        regs = {r["id"]: (r["data_registro"], r["texto"]) for r in exportado["registros"]}
        itens = {i["id"]: (r["texto"], i["trecho"], i["resumo"]) for r in exportado["registros"] for i in r["itens"]}
        registros = []
        for r in exportado["registros"]:
            registros.append({
                **{k: r[k] for k in ("criado_em", "data_registro", "texto", "contexto", "humor", "privado")},
                "pessoas": sorted((p["nome"], p["relacao"]) for p in r["pessoas"]),
                "itens": [{**{k: v for k, v in i.items() if k not in ("id", "registro_id", "pessoa_id")},
                           "de": pessoas.get(i["pessoa_id"])} for i in r["itens"]],
                "relacoes": [{"tipo": x["tipo"], "motivo": x["motivo"], "sugestao": x["sugestao"], "status": x["status"],
                              "novo": itens.get(x["item_novo"]), "antigo": itens.get(x["item_antigo_id"]),
                              "registro_antigo": regs.get(x["registro_antigo_id"])} for x in r["relacoes"]],
            })
        return {"registros": registros,
                "pessoas": sorted((p["nome"] or "", p["relacao"] or "", tuple(p["apelidos"]), p["observacoes"],
                                   p["aniversario"] or "") for p in exportado["pessoas"])}

    def test_exportar_e_importar_num_diario_vazio(self):
        origem = self.servidor()
        origem.configurar(PIN_BACKUP, nome_dela="Julia")
        r = origem.pedir("POST", "/api/pessoas", {"nome": "Mariana", "relacao": "melhor amiga", "apelidos": ["Mari"],
                                                   "observacoes": "Se conheceram na faculdade.",
                                                   "aniversario": "1998-03-12"})
        self.assertEqual(r.status, 200, r.dados)
        primeiro, _ = origem.registrar(TEXTO_MAE, "2026-09-20")
        origem.registrar(TEXTO_MARI, "2026-09-25")
        tulipas = next(i for i in origem.pedir("GET", f"/api/registros/{primeiro}").dados["itens"]
                       if "tulipa" in (i["alvo"] or ""))
        r = origem.pedir("POST", "/api/registros", {
            "texto": TEXTO_TRISTE, "data_registro": "2026-09-28", "privado": True, "contexto": "por mensagem",
            "itens": [{"tipo": "estado", "resumo": "Triste com a notícia do trabalho", "trecho": TEXTO_TRISTE,
                       "certeza": "observado", "categorias": ["Preocupações"], "data_evento": "2026-09-28"}],
            "relacoes": [{"tipo": "contexto", "motivo": "Em 20/09 você registrou que ela adora tulipas.",
                          "item_novo": 0, "item_antigo_id": tulipas["id"], "sugestao": "Talvez levar tulipas."}]})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(origem.pedir("PATCH", f"/api/itens/{tulipas['id']}", {"status": "resolvido"}).status, 200)
        exportado = origem.pedir("GET", "/api/exportar")
        self.assertEqual(exportado.status, 200)
        self.assertGreaterEqual(sum(len(x["itens"]) for x in exportado.dados["registros"]), 4)
        self.assertGreaterEqual(sum(len(x["relacoes"]) for x in exportado.dados["registros"]), 1)
        self.assertTrue(any(x["privado"] for x in exportado.dados["registros"]))
        self.assertTrue(any(p["observacoes"] for p in exportado.dados["pessoas"]))

        destino = self.servidor()
        destino.configurar(PIN, nome_dela="Julia")
        texto = exportado.bruto.decode("utf-8")
        previa = destino.pedir("POST", "/api/importar/previa", {"json": texto})
        self.assertEqual(previa.status, 200, previa.dados)
        r = destino.pedir("POST", "/api/importar", {"json": texto, "confirmar": True})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual({k: v for k, v in r.dados.items() if k not in ("avisos", "importados")},
                         {k: v for k, v in previa.dados.items() if k != "avisos"})
        self.assertEqual(r.dados["importados"], 3)
        self.assertEqual(r.dados["registros_repetidos"], 0)
        reimportado = destino.pedir("GET", "/api/exportar").dados
        self.assertEqual(self.normalizar(reimportado), self.normalizar(exportado.dados))
        # E a busca acha o que veio.
        busca = destino.pedir("GET", "/api/busca?q=" + "Recife").dados
        self.assertTrue(busca["encontrados"])


if __name__ == "__main__":
    unittest.main()
