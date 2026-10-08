"""Sincronização entre aparelhos pelo Google Drive (DESIGN.md, seção 22).

Quem fala com o Google é a página (`web/sincronizar.js`): ela lista, baixa e envia os arquivos da
pasta oculta do app no Drive (appDataFolder, escopo `drive.appdata`) e entrega ao núcleo, pelas rotas
`/api/sync/...`, os arquivos dos outros aparelhos. Este módulo faz o resto, igual no computador e no
celular (Pyodide): a senha de sincronização, a cifra dos arquivos e a mescla.

No Drive ficam:

- `entrenos-sync.json`, em claro: o sal e os parâmetros do scrypt da senha de sincronização e um
  verificador (HMAC da chave, nunca a chave). Sem nome, data nem aparelho.
- `entrenos-<aparelho>.sync`, um por aparelho; cada aparelho só escreve o seu (o Drive não tem
  gravação condicional confiável, então a mescla acontece na leitura). Por dentro: a exportação do
  diário (sem ajustes, histórico e avisos dispensados), a identidade (uid) e o carimbo de cada
  registro, item e pessoa, e as lápides; em JSON comprimido e cifrado com AES-256-GCM:
  `b"ENTRENOS-SYNC" | formato (1 byte) | nonce (12) | cifrado + tag`, com o cabeçalho como dado
  associado. O Google só vê bytes embaralhados.

A chave: scrypt(senha de sincronização, sal comum) = a chave mestra, guardada dentro do diário cifrado
(ajuste "sync"; nunca sai em /api/config, na exportação nem no histórico). Dela saem por HMAC a chave
da cifra, a das impressões (uid, lápides) e o verificador. A senha é própria: pode ser igual ao PIN,
mas trocar o PIN não muda nada aqui.

A mescla (fase 1), num arquivo de outro aparelho de cada vez, numa transação só:
1. o estado local é atualizado: cada registro, item e pessoa ganha (ou já tem) um uid; o que mudou
   desde a última vez ganha carimbo novo; o que sumiu daqui vira lápide (só a impressão);
2. as lápides do outro aparelho entram e o que elas apontam é apagado aqui (e nunca volta);
3. registros, pessoas e linhas dos recursos que este aparelho ainda não tem entram pela lógica de
   importar (juntar sem duplicar, 🔒 preservado, extras dos recursos, inclusive ciclo e área Eu);
4. o que existe dos dois lados e mudou: vale a versão de carimbo mais recente. Mudou nos dois lados
   quase ao mesmo tempo (JANELA_CONFLITO): vira uma pergunta na tela (o 🔒 de um registro não
   pergunta: na dúvida, fica 🔒).

Nada aqui registra conteúdo, senha, chave ou token: os erros sobem como exceções com mensagem para a
tela, e o log do núcleo guarda só o tipo.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import banco as _banco
from . import config as cfg
from . import recursos
from .cofre import _parametros_validos, problema_do_pin
from .modelos import para_json

FORMATO = 1                                # dos arquivos .sync e do entrenos-sync.json
MAGICO = b"ENTRENOS-SYNC"
TAMANHO_NONCE = 12
TAMANHO_TAG = 16
ARQUIVO_CONFIG = "entrenos-sync.json"
SCRYPT_N = 2 ** 16                         # como o PIN (cofre.SCRYPT_N); os testes baixam para andar rápido
SCRYPT_R = 8
SCRYPT_P = 1
TAMANHO_SAL = 16
JANELA_CONFLITO = 120                      # segundos: mudou dos dois lados mais perto que isto = pergunta
EPOCA = "1970-01-01T00:00:00+00:00"        # carimbo de quem foi visto pela primeira vez (sem data de mudança)
MAX_DESCOMPRIMIDO = 64 * 1024 * 1024       # um arquivo .sync aberto não passa disto (bomba de zip)
MAX_LAPIDES = 500_000
TIPOS_LAPIDE = ("registro", "item", "pessoa", "linha")
CAMPOS_ITEM = ("tipo", "resumo", "trecho", "certeza", "categorias", "importancia", "data_evento",
               "data_expressao", "precisao", "recorrente", "pessoa", "alvo", "polaridade", "lembrar_em", "status")
CAMPOS_PESSOA = ("nome", "relacao", "apelidos", "observacoes", "aniversario")
CAMPOS_DELA = ("nome_dela", "aniversario_dela", "inicio_namoro")
# Campos de uma linha de recurso que não entram na impressão: os ids são de cada aparelho e as datas de
# criação são de quando a linha entrou em cada um. O resto é o mesmo nos dois lados depois de importar.
_VOLATEIS = frozenset({"id", "criado_em", "atualizado_em"})
NOME_PADRAO = {"computador": "Computador", "navegador": "Celular ou navegador"}
MAX_NOME = 40
MAX_CONTA = 200
SENHA_MINIMO = 14                          # caracteres da senha de sincronização (22.2)
SENHA_PALAVRAS = 3                         # palavras diferentes (de 2 letras ou mais) numa frase
# Frases feitas (sem espaços, acentos nem números): as primeiras que alguém testaria.
_FRASES_COMUNS = frozenset({
    "euteamo", "euteamomuito", "teamomuito", "teamoparasempre", "euteamoparasempre", "amordaminhavida",
    "meuamor", "meuamoreteamo", "minhavida", "minhavidaeteamo", "parasempre", "juntosparasempre",
    "nossoamor", "nossoamorparasempre", "amoreterno", "amorverdadeiro", "senhadesincronizacao",
    "minhasenhadesincronizacao", "senhadoentrenos", "entrenosentrenos", "minhasenhasecreta", "senhasecreta",
    "deusefiel", "deuseamor", "deuseomeupastor", "tudoposso", "tudopossonaquelequemefortalece",
})
# O arquivo cifrado cresce em degraus (22.2): sem isso o tamanho do .sync seguia o diário quase
# byte a byte, e junto com a hora de cada envio o Google sabia quando e quanto a pessoa escreve.
DEGRAUS_PREENCHIMENTO = ((1024 * 1024, 64 * 1024), (8 * 1024 * 1024, 256 * 1024), (None, 1024 * 1024))

_RE_UID = re.compile(r"[ripl][0-9a-f]{32}(?:-[0-9]{1,6})?")
_RE_APARELHO = re.compile(r"[0-9a-f]{16}")
_RE_ARQUIVO = re.compile(r"entrenos-([0-9a-f]{16})\.sync")
# O ID do cliente OAuth do Google (não é segredo): "123456789012-abc...xyz.apps.googleusercontent.com".
_RE_ID_APP = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{2,150}\.apps\.googleusercontent\.com")
_RE_ETIQUETA = re.compile(r"[\x21-\x7e]{1,200}")

SEM_SENHA = ("A sincronização ainda não foi ligada neste aparelho: crie ou digite a senha de sincronização em "
             "Ajustes → ☁️ Sincronização.")
JA_CONFIGURADA = "A sincronização já está ligada neste aparelho."
SENHA_ERRADA = ("Essa não é a senha de sincronização dos seus outros aparelhos. Confira (maiúsculas e espaços "
                "contam) e tente de novo.")
CONFIG_INVALIDA = ("O arquivo de configuração da sincronização (entrenos-sync.json) no Google Drive não está no "
                   "formato esperado. Nada foi mudado.")
CONFIG_MUDOU = ("A senha de sincronização foi trocada em outro aparelho (ou o arquivo de configuração no Google "
                "Drive mudou). Para continuar, digite a senha de sincronização de novo.")
ARQUIVO_ESTRANHO = ("Um arquivo de sincronização no Google Drive não abre com a sua senha de sincronização: ele "
                    "pode ter sido mexido ou feito com outra senha. Ele ficou de fora, e nada aqui mudou.")
ARQUIVO_TROCADO = ("Um arquivo de sincronização no Google Drive está com o nome de outro aparelho. Ele ficou de "
                   "fora, e nada aqui mudou.")
ARQUIVO_NOVO = ("Um dos seus aparelhos usa uma versão mais nova do Entre Nós. Atualize este aparelho para "
                "sincronizar com ele; nada aqui mudou.")
ARQUIVO_GRANDE = "Um arquivo de sincronização é grande demais para este aparelho abrir. Nada aqui mudou."
ARQUIVO_ANTIGO = ("Um arquivo de sincronização no Google Drive é mais antigo do que um que este aparelho já juntou "
                  "(pode ter sido trocado por uma versão velha). Ele ficou de fora, e nada aqui mudou.")
SENHA_IGUAL_AO_PIN = ("Essa senha de sincronização é igual ao PIN deste diário. Quem descobrir uma (testando senhas "
                      "nos arquivos do Drive, por exemplo) fica sabendo a outra, e o PIN também abre as cópias do "
                      "diário. O melhor é usar outra frase (como a sugerida aqui).")
RECURSO_FORA = ("Uma parte do Entre Nós não abriu neste aparelho; os dados dela ficam para a próxima "
                "sincronização.")


class SenhaSyncErrada(Exception):
    """A senha não bate com o verificador do entrenos-sync.json."""


class ArquivoSyncInvalido(ValueError):
    """Arquivo (ou configuração) que não é da sincronização do Entre Nós, ou que não abre com a chave
    (mexido, cortado, de outra senha). A mensagem é para a tela."""


class ArquivoSyncNovo(ValueError):
    """Formato de uma versão mais nova do Entre Nós."""


# ---------------------------------------------------------------- chaves e senha

class Chaves:
    """A chave mestra e o que sai dela (HMAC com rótulos diferentes para cada uso)."""

    __slots__ = ("mestra", "cifra", "impressao", "verificador")

    def __init__(self, mestra: bytes):
        if not isinstance(mestra, (bytes, bytearray)) or len(mestra) != 32:
            raise ValueError("chave de sincronização inválida")
        self.mestra = bytes(mestra)
        self.cifra = hmac.new(self.mestra, b"entrenos-sync:cifra", hashlib.sha256).digest()
        self.impressao = hmac.new(self.mestra, b"entrenos-sync:impressao", hashlib.sha256).digest()
        self.verificador = _b64(hmac.new(self.mestra, b"entrenos-sync:verificador", hashlib.sha256).digest())

    def __repr__(self) -> str:          # nunca a chave numa mensagem ou log
        return "Chaves(...)"


def _b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii")


def _de_b64(texto: Any) -> bytes | None:
    if not isinstance(texto, str):
        return None
    try:
        return base64.b64decode(texto.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeError, ValueError):
        return None


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if not unicodedata.combining(c))


def problema_da_senha(senha: Any) -> str | None:
    """Por que a senha de sincronização não serve (mensagem para a tela), ou None.

    Regra própria, mais forte que a do PIN (22.2): o entrenos-sync.json (sal, parâmetros e
    verificador) fica no Drive, e quem lê a pasta do app (o Google, ou quem tomar a conta) testa
    senhas fora do app, sem limite e sem o bloqueio do PIN: com "flores" ou "camila123", o "o Google
    só vê arquivos embaralhados" deixa de valer. Pede pelo menos 14 caracteres e uma frase de pelo
    menos três palavras diferentes (ou, sem frase, 20 caracteres variados); recusa palavra ou nome
    único, mesmo com números, repetições, sequências, as senhas comuns do PIN e frases feitas."""
    if not isinstance(senha, str) or not senha.strip():
        return "Digite a senha de sincronização."
    texto = unicodedata.normalize("NFC", senha).strip()
    if len(texto) < SENHA_MINIMO:
        return (f"A senha de sincronização precisa ter pelo menos {SENHA_MINIMO} caracteres. Uma frase de três "
                "ou quatro palavras é o mais fácil de lembrar (como a sugerida aqui).")
    problema = problema_do_pin(texto)
    if problema is not None:
        return (problema.replace("Esse PIN é muito comum. Escolha outro", "Essa senha é muito comum. Escolha outra")
                .replace("O PIN ", "A senha de sincronização ").replace("Um PIN ", "Uma senha "))
    simples = _sem_acento(texto)
    compacto = re.sub(r"[^a-z0-9]", "", simples)
    if compacto in _FRASES_COMUNS or re.sub(r"\d", "", compacto) in _FRASES_COMUNS:
        return "Essa frase é muito comum. Escolha outra, com palavras que só você juntaria (como a sugerida aqui)."
    palavras = set(re.findall(r"[a-z]{2,}", simples))
    if len(palavras) >= SENHA_PALAVRAS:
        return None
    classes = sum((any(c.islower() for c in texto), any(c.isupper() for c in texto), any(c.isdigit() for c in texto),
                   any(not c.isalnum() and not c.isspace() for c in texto)))
    if len(texto) >= 20 and classes >= 3 and len(set(texto)) >= 12:
        return None
    return (f"Use uma frase com pelo menos {SENHA_PALAVRAS} palavras diferentes (como a sugerida aqui). Uma palavra "
            "ou um nome só, mesmo com números ou símbolos, é fácil de adivinhar por quem copiar os arquivos do Drive.")


def derivar(senha: str, sal: bytes, n: int, r: int, p: int) -> Chaves:
    """scrypt da senha (NFC, como o PIN) -> as chaves. Sem limite de tempo aqui: os parâmetros já
    foram conferidos (_parametros_validos: no máximo 2^17, r=8, p=1)."""
    if not _parametros_validos(n, r, p):
        raise ArquivoSyncInvalido(CONFIG_INVALIDA)
    bruta = unicodedata.normalize("NFC", senha).encode("utf-8")
    mestra = Scrypt(salt=sal, length=32, n=n, r=r, p=p).derive(bruta)
    return Chaves(mestra)


def nova_configuracao(senha: str) -> tuple[dict, Chaves]:
    """O entrenos-sync.json do primeiro aparelho (sal novo) e as chaves da senha."""
    problema = problema_da_senha(senha)
    if problema:
        raise ValueError(problema)
    sal = secrets.token_bytes(TAMANHO_SAL)
    chaves = derivar(senha, sal, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return _configuracao(sal, SCRYPT_N, SCRYPT_R, SCRYPT_P, chaves.verificador), chaves


def _configuracao(sal: bytes, n: int, r: int, p: int, verificador: str) -> dict:
    return {"app": "entre-nos", "tipo": "sincronizacao", "formato": FORMATO,
            "kdf": {"algoritmo": "scrypt", "sal": _b64(sal), "n": n, "r": r, "p": p},
            "verificador": verificador}


def texto_da_configuracao(configuracao: dict) -> str:
    return json.dumps(configuracao, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ler_configuracao(texto: Any) -> dict:
    """O entrenos-sync.json baixado do Drive, conferido campo a campo."""
    if not isinstance(texto, str) or len(texto) > 64 * 1024:
        raise ArquivoSyncInvalido(CONFIG_INVALIDA)
    try:
        dados = json.loads(texto.lstrip("﻿"))
    except (ValueError, RecursionError):
        raise ArquivoSyncInvalido(CONFIG_INVALIDA) from None
    if not isinstance(dados, dict) or dados.get("app") != "entre-nos" or dados.get("tipo") != "sincronizacao":
        raise ArquivoSyncInvalido(CONFIG_INVALIDA)
    formato = dados.get("formato")
    if isinstance(formato, int) and not isinstance(formato, bool) and formato > FORMATO:
        raise ArquivoSyncNovo(ARQUIVO_NOVO)
    if formato != FORMATO:
        raise ArquivoSyncInvalido(CONFIG_INVALIDA)
    kdf = dados.get("kdf")
    if not isinstance(kdf, dict) or kdf.get("algoritmo") != "scrypt":
        raise ArquivoSyncInvalido(CONFIG_INVALIDA)
    n, r, p = kdf.get("n"), kdf.get("r"), kdf.get("p")
    sal, verificador = _de_b64(kdf.get("sal")), _de_b64(dados.get("verificador"))
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (n, r, p)) or not _parametros_validos(n, r, p):
        raise ArquivoSyncInvalido(CONFIG_INVALIDA)
    if sal is None or not 8 <= len(sal) <= 64 or verificador is None or len(verificador) != 32:
        raise ArquivoSyncInvalido(CONFIG_INVALIDA)
    return _configuracao(sal, n, r, p, dados["verificador"])


def chaves_da_senha(senha: str, configuracao: dict) -> Chaves:
    """As chaves, se a senha bate com o verificador do entrenos-sync.json (SenhaSyncErrada senão)."""
    if not isinstance(senha, str) or not senha:
        raise SenhaSyncErrada()
    kdf = configuracao["kdf"]
    chaves = derivar(senha, _de_b64(kdf["sal"]), kdf["n"], kdf["r"], kdf["p"])
    if not hmac.compare_digest(chaves.verificador, configuracao["verificador"]):
        raise SenhaSyncErrada()
    return chaves


# ---------------------------------------------------------------- ajustes guardados no diário ("sync")

def ajustes(banco: Any) -> dict:
    valor = banco.config().get("sync")
    return dict(valor) if isinstance(valor, dict) else {}


def configurada(aj: dict) -> bool:
    return bool(_de_b64(aj.get("chave")) and isinstance(aj.get("aparelho"), str))


def chaves_guardadas(aj: dict) -> Chaves | None:
    mestra = _de_b64(aj.get("chave"))
    if mestra is None or len(mestra) != 32:
        return None
    return Chaves(mestra)


def configuracao_guardada(aj: dict) -> dict | None:
    """O entrenos-sync.json que corresponde à chave daqui (para a página recriar o arquivo, se ele
    sumir do Drive). Não tem segredo: sal, parâmetros e verificador."""
    sal = _de_b64(aj.get("sal"))
    if not configurada(aj) or sal is None:
        return None
    return _configuracao(sal, aj.get("n"), aj.get("r"), aj.get("p"), aj.get("verificador"))


def mesma_configuracao(aj: dict, configuracao: dict) -> bool:
    """O entrenos-sync.json do Drive é o desta chave? (Senha trocada em outro lugar = não.)"""
    guardada = configuracao_guardada(aj)
    return bool(guardada) and guardada["kdf"] == configuracao["kdf"] and hmac.compare_digest(
        str(guardada["verificador"]), str(configuracao["verificador"]))


def nome_do_arquivo(aparelho: str) -> str:
    return f"entrenos-{aparelho}.sync"


def aparelho_do_arquivo(nome: Any) -> str | None:
    achado = _RE_ARQUIVO.fullmatch(nome) if isinstance(nome, str) else None
    return achado.group(1) if achado else None


def _gravar_ajustes(banco: Any, aj: dict) -> None:
    banco.salvar_config(sync=aj or None)


def mudar_ajustes(banco: Any, corpo: dict, plataforma: str) -> dict:
    """PATCH /api/sync/ajustes {id_app?, nome?, automatica?, conta?}: o que não é segredo."""
    desconhecidos = sorted(set(corpo) - {"id_app", "nome", "automatica", "conta"})
    if desconhecidos:
        raise ValueError("Campos que não podem ser alterados aqui: " + ", ".join(desconhecidos))
    aj = ajustes(banco)
    if "id_app" in corpo:
        valor = corpo["id_app"]
        if valor in (None, ""):
            aj.pop("id_app", None)
        elif not isinstance(valor, str) or not _RE_ID_APP.fullmatch(valor.strip()):
            raise ValueError("Esse não parece um ID do app do Google: ele termina em .apps.googleusercontent.com. "
                             "Copie de novo do Google Cloud (Credenciais → ID do cliente).")
        else:
            aj["id_app"] = valor.strip()
    if "nome" in corpo:
        valor = corpo["nome"]
        if not isinstance(valor, str) or not valor.strip() or len(valor.strip()) > MAX_NOME \
                or any(ord(c) < 32 for c in valor):
            raise ValueError(f"Dê um nome curto a este aparelho (até {MAX_NOME} letras), como “Celular” ou “Notebook”.")
        aj["nome"] = unicodedata.normalize("NFC", valor.strip())
    if "automatica" in corpo:
        if not isinstance(corpo["automatica"], bool):
            raise ValueError("“automatica” precisa ser verdadeiro ou falso.")
        aj["automatica"] = corpo["automatica"]
    if "conta" in corpo:
        valor = corpo["conta"]
        if valor in (None, ""):
            aj.pop("conta", None)
        elif not isinstance(valor, str) or len(valor) > MAX_CONTA or any(ord(c) < 32 for c in valor):
            raise ValueError("Conta do Google inválida.")
        else:
            aj["conta"] = valor.strip()
    aj.setdefault("versao", 1)
    aj.setdefault("nome", NOME_PADRAO.get(plataforma, "Este aparelho"))
    _gravar_ajustes(banco, aj)
    return aj


def ligar(banco: Any, chaves: Chaves, configuracao: dict, plataforma: str, agora: str) -> dict:
    """Guarda a chave no diário e liga a sincronização deste aparelho. Devolve {aparelho, arquivo}.

    Religar com a MESMA senha (o mesmo entrenos-sync.json) depois de "Parar": volta a mesma identidade
    (o mesmo arquivo no Drive, que é reescrito) e a memória da sincronização, que ficou guardada; o
    que foi apagado com ela desligada vira lápide na primeira conferência e não volta dos outros
    aparelhos (22.2). Com outra senha (ou pela primeira vez): identidade nova e memória do zero; o
    arquivo antigo deste aparelho, de outra senha, entra na lista para a página apagar do Drive."""
    aj = ajustes(banco)
    kdf = configuracao["kdf"]
    anterior = aj.pop("anterior", None)
    anterior = anterior if isinstance(anterior, dict) else {}
    mesma = isinstance(anterior.get("verificador"), str) and hmac.compare_digest(
        anterior["verificador"], str(configuracao["verificador"]))
    aparelho = anterior.get("aparelho") if mesma and _RE_APARELHO.fullmatch(str(anterior.get("aparelho") or "")) \
        else secrets.token_hex(8)
    apagar = [n for n in aj.get("apagar") or [] if isinstance(n, str) and aparelho_do_arquivo(n)]
    if not mesma and _RE_APARELHO.fullmatch(str(anterior.get("aparelho") or "")):
        apagar.append(nome_do_arquivo(anterior["aparelho"]))       # o arquivo antigo, de outra senha
    aj.update({"versao": 1, "chave": _b64(chaves.mestra), "sal": kdf["sal"], "n": kdf["n"], "r": kdf["r"],
               "p": kdf["p"], "verificador": configuracao["verificador"], "aparelho": aparelho,
               "ligada_em": _segundos(agora), "versao_estado": aj.get("versao_estado") if mesma else cfg.VERSAO,
               "sequencia": int(anterior.get("sequencia") or 0) if mesma else 0})
    aj["apagar"] = [n for n in dict.fromkeys(apagar) if n != nome_do_arquivo(aparelho)]
    if not aj["apagar"]:
        aj.pop("apagar")
    if not mesma:
        aj.pop("sequencias", None)
    aj.setdefault("nome", NOME_PADRAO.get(plataforma, "Este aparelho"))
    aj.setdefault("automatica", True)
    for velho in ("ultima", "enviado"):
        aj.pop(velho, None)
    with banco.transacao():
        if mesma:
            with banco._escrita() as con:
                con.execute("DELETE FROM sync_conflitos")
        else:
            _limpar_estado(banco)
        _gravar_ajustes(banco, aj)
    return {"aparelho": aj["aparelho"], "arquivo": nome_do_arquivo(aj["aparelho"]), "religada": mesma}


def desligar(banco: Any) -> None:
    """Para de sincronizar neste aparelho: a chave sai do diário (ficam o ID do app, o nome e a
    preferência de sincronizar sozinho).

    A memória da sincronização (uids, lápides, o que veio de cada aparelho: só impressões, sem
    conteúdo) FICA, junto com a identidade deste aparelho e o verificador da senha (sem a chave): se
    ela for religada com a mesma senha, o que foi apagado enquanto estava desligada vira lápide (antes
    voltava dos outros aparelhos) e o arquivo deste aparelho no Drive é reescrito, em vez de ficar lá
    para sempre com o diário de antes (22.2). As perguntas pendentes saem."""
    aj = ajustes(banco)
    guardar = {k: aj[k] for k in ("versao", "id_app", "nome", "automatica", "apagar") if k in aj}
    if configurada(aj):
        guardar["anterior"] = {"aparelho": aj["aparelho"], "verificador": aj.get("verificador"),
                               "sequencia": int(aj.get("sequencia") or 0)}
        if isinstance(aj.get("sequencias"), dict):
            guardar["sequencias"] = aj["sequencias"]
    elif isinstance(aj.get("anterior"), dict):
        guardar["anterior"] = aj["anterior"]
    with banco.transacao():
        with banco._escrita() as con:
            con.execute("DELETE FROM sync_conflitos")
        _gravar_ajustes(banco, guardar)


def _limpar_estado(banco: Any) -> None:
    with banco._escrita() as con:
        for tabela in ("sync_ids", "sync_linhas", "sync_lapides", "sync_aparelhos", "sync_conflitos"):
            con.execute(f"DELETE FROM {tabela}")


def para_fora(aj: dict) -> dict | None:
    """O ajuste "sync" de uma cópia que SAI do aparelho (Baixar uma cópia, backup numa pasta): sem a
    chave (quem quebrar o PIN de uma cópia mandada para fora leria também os .sync), sem a identidade
    deste aparelho e sem a conta. Restaurada, a cópia pede a senha de sincronização de novo (22.2);
    o verificador fica, para a memória da sincronização valer se a senha for a mesma."""
    if not aj:
        return None
    fora = {k: aj[k] for k in ("versao", "id_app", "automatica") if k in aj}
    verificador = aj.get("verificador") if configurada(aj) else None
    if verificador is None and isinstance(aj.get("anterior"), dict):
        verificador = aj["anterior"].get("verificador")
    if isinstance(verificador, str):
        fora["anterior"] = {"aparelho": None, "verificador": verificador}
    return fora or None


def depois_de_restaurar(banco: Any, plataforma: str) -> bool:
    """Um diário restaurado traz a sincronização do aparelho de onde veio: este aparelho passa a ter
    identidade própria (senão os dois escreveriam o mesmo arquivo no Drive). True se mudou algo."""
    aj = ajustes(banco)
    anterior = aj.get("anterior")
    if isinstance(anterior, dict) and anterior.get("aparelho"):
        # Desligada no aparelho de onde a cópia veio: religar aqui não pode reusar aquele arquivo.
        aj["anterior"] = {"aparelho": None, "verificador": anterior.get("verificador")}
        aj.pop("apagar", None)
        aj["nome"] = NOME_PADRAO.get(plataforma, "Este aparelho")
        _gravar_ajustes(banco, aj)
        return True
    if not configurada(aj):
        return False
    aj["aparelho"] = secrets.token_hex(8)
    aj["nome"] = NOME_PADRAO.get(plataforma, "Este aparelho")
    aj["sequencia"] = 0
    for velho in ("conta", "ultima", "enviado", "apagar"):
        aj.pop(velho, None)
    _gravar_ajustes(banco, aj)
    return True


def marcar_enviado(banco: Any, etiqueta: Any, agora: str, apagados: Any = None) -> dict:
    """O Drive aceitou o arquivo deste aparelho (e apagou `apagados`, os arquivos antigos deste
    aparelho de outra senha que estavam na lista)."""
    aj = ajustes(banco)
    if not configurada(aj):
        raise ValueError(SEM_SENHA)
    if etiqueta is not None and (not isinstance(etiqueta, str) or not _RE_ETIQUETA.fullmatch(etiqueta)):
        raise ValueError("Etiqueta do arquivo enviado inválida.")
    if apagados is not None and (not isinstance(apagados, list) or not all(isinstance(n, str) for n in apagados)):
        raise ValueError("Lista de arquivos apagados inválida.")
    aj["ultima"] = _segundos(agora)
    if etiqueta is not None:
        aj["enviado"] = etiqueta
    if apagados:
        restantes = [n for n in aj.get("apagar") or [] if n not in apagados]
        if restantes:
            aj["apagar"] = restantes
        else:
            aj.pop("apagar", None)
    _gravar_ajustes(banco, aj)
    return aj


def estado(banco: Any, plataforma: str) -> dict:
    """O que a tela ☁️ Sincronização mostra (sem a chave, claro)."""
    aj = ajustes(banco)
    ligada = configurada(aj)
    con = banco._con
    with banco._trava:
        aparelhos = [dict(l) for l in con.execute(
            "SELECT aparelho, nome, gerado_em, etiqueta, mesclado_em FROM sync_aparelhos ORDER BY mesclado_em DESC")]
        conflitos = con.execute("SELECT count(*) FROM sync_conflitos").fetchone()[0]
    return {"configurada": ligada, "servico": "google_drive",
            "id_app": aj.get("id_app"), "nome": aj.get("nome") or NOME_PADRAO.get(plataforma, "Este aparelho"),
            "automatica": aj.get("automatica", True) is not False, "conta": aj.get("conta"),
            "aparelho": aj.get("aparelho") if ligada else None,
            "arquivo": nome_do_arquivo(aj["aparelho"]) if ligada else None,
            "arquivo_config": ARQUIVO_CONFIG, "ultima": aj.get("ultima") if ligada else None,
            "enviado": aj.get("enviado") if ligada else None,
            "sync_json": texto_da_configuracao(configuracao_guardada(aj)) if ligada else None,
            "aparelhos": aparelhos if ligada else [], "conflitos": conflitos if ligada else 0,
            # Arquivos antigos DESTE aparelho (de uma senha anterior) que a página apaga do Drive.
            "apagar": [n for n in aj.get("apagar") or [] if isinstance(n, str)] if ligada else [],
            "religavel": not ligada and isinstance(aj.get("anterior"), dict)}


# ---------------------------------------------------------------- impressões, assinaturas, carimbos

def _hmac_hex(chave: bytes, texto: str) -> str:
    return hmac.new(chave, texto.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()


def _canonico(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assinatura(chaves: Chaves, tipo: str, campos: Any) -> str:
    """Resumo (HMAC) dos campos sincronizados de algo: muda quando algum campo muda."""
    return _hmac_hex(chaves.impressao, f"assinatura\0{tipo}\0{_canonico(campos)}")[:32]


def _uid_registro(chaves: Chaves, data_registro: str, criado_em: str, texto: str) -> str:
    _, normalizado = _banco._chave_registro(data_registro, texto)
    return "r" + _hmac_hex(chaves.impressao, f"registro\0{data_registro}\0{criado_em}\0{normalizado}")[:32]


def _uid_item(chaves: Chaves, uid_registro: str, tipo: str, trecho: str, criado_em: str) -> str:
    return "i" + _hmac_hex(chaves.impressao, f"item\0{uid_registro}\0{tipo}\0{trecho}\0{criado_em}")[:32]


def _uid_pessoa(chaves: Chaves, nome: str | None, relacao: str | None) -> str:
    chave = f"pessoa\0{_banco._chave_pessoa(nome)}\0{_banco._chave_pessoa(relacao)}"
    return "p" + _hmac_hex(chaves.impressao, chave)[:32]


def _linha_estavel(linha: dict) -> dict:
    return {k: v for k, v in linha.items() if k not in _VOLATEIS and not k.endswith(("_id", "_ids"))}


def uid_linha(chaves: Chaves, nome: str, lista: str, linha: dict) -> str:
    """A impressão de uma linha de recurso: o mesmo conteúdo dá o mesmo uid em qualquer aparelho (os
    ids e as datas de criação, que mudam ao importar, ficam de fora)."""
    return "l" + _hmac_hex(chaves.impressao, f"linha\0{nome}\0{lista}\0{_canonico(_linha_estavel(linha))}")[:32]


def _uid_aleatorio(prefixo: str) -> str:
    return prefixo + secrets.token_hex(16)


def agora_utc(segundos: float) -> str:
    """O instante dos carimbos: UTC com microssegundos (dois aparelhos nunca carimbam igual por acaso;
    carimbo igual quer dizer "a mesma mudança", ver `_decidir_edicao`)."""
    return datetime.fromtimestamp(segundos, timezone.utc).isoformat(timespec="microseconds")


def _segundos(agora: str) -> str:
    """O instante para mostrar (sem as frações, que o Safari não lê): "2026-10-07T12:00:00+00:00"."""
    quando = _instante(agora)
    return _iso(quando) if quando is not None else agora


def _instante(texto: Any) -> datetime | None:
    if not isinstance(texto, str) or len(texto) > 40:
        return None
    try:
        quando = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return quando if quando.tzinfo is not None else None


def _de_local(texto: Any) -> datetime | None:
    """Hora local sem fuso (como o banco grava atualizado_em) -> UTC."""
    if not isinstance(texto, str) or len(texto) > 40:
        return None
    try:
        quando = datetime.fromisoformat(texto)
        return quando.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _iso(quando: datetime) -> str:
    return quando.astimezone(timezone.utc).isoformat(timespec="seconds")


def _depois_de(carimbo: str, candidato: datetime | None, agora: str) -> str:
    """O carimbo de uma mudança feita aqui: quando ela aconteceu (se o banco sabe, e nunca no futuro),
    e sempre depois do carimbo da versão anterior (ela veio depois, mesmo com relógios desacertados).
    Com microssegundos: o banco guarda a hora em segundos, e as frações vêm do relógio de agora, para
    duas mudanças em aparelhos diferentes nunca terem o mesmo carimbo por acaso."""
    limite = _instante(agora) or datetime.now(timezone.utc)
    if candidato is not None and candidato.microsecond == 0:
        candidato = candidato.replace(microsecond=limite.microsecond)
    quando = min(candidato, limite) if candidato is not None else limite
    antes = _instante(carimbo)
    if antes is not None and quando <= antes:
        quando = antes + timedelta(seconds=1)
    return quando.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _decidir(carimbo_aqui: str, carimbo_la: str) -> str:
    """"la" (vale a do outro aparelho), "aqui" ou "conflito" (mudou dos dois lados quase junto)."""
    aqui, la = _instante(carimbo_aqui), _instante(carimbo_la)
    aqui = aqui or _instante(EPOCA)
    la = la or _instante(EPOCA)
    if la > aqui + timedelta(seconds=JANELA_CONFLITO):
        return "la"
    if aqui > la + timedelta(seconds=JANELA_CONFLITO):
        return "aqui"
    return "conflito"


def _decidir_edicao(carimbo_aqui: str, carimbo_la: str) -> str:
    """Como `_decidir`, mas o mesmo carimbo (fora a época) é a mesma mudança: um lado aplicou a do
    outro e guardou algum campo normalizado de outro jeito. Não vira pergunta (cada um fica com a sua
    forma da mesma versão). Duas mudanças no mesmo segundo em aparelhos diferentes também cairiam
    aqui: raro demais para perguntar a cada sincronização por uma diferença que ninguém fez."""
    if carimbo_aqui == carimbo_la and carimbo_aqui != EPOCA:
        return "aqui"
    return _decidir(carimbo_aqui, carimbo_la)


def _maior(a: str, b: str) -> str:
    x, y = _instante(a), _instante(b)
    if x is None:
        return b
    if y is None:
        return a
    return a if x >= y else b


# ---------------------------------------------------------------- campos do que é sincronizado

def _campos_item(dados: dict) -> dict:
    return {k: dados.get(k) for k in CAMPOS_ITEM}


def _campos_pessoa(dados: dict) -> dict:
    campos = {k: dados.get(k) for k in CAMPOS_PESSOA}
    campos["observacoes"] = campos["observacoes"] or ""
    # A ordem dos apelidos não é mudança (os dois aparelhos chegam à mesma lista em ordens diferentes).
    campos["apelidos"] = sorted(campos["apelidos"] or [], key=lambda a: (_banco._chave_pessoa(a), a))
    return campos


def _itens_locais(banco: Any) -> list[tuple[int, int, dict, str | None, str | None]]:
    """[(id, registro_id, campos, criado_em, atualizado_em)] de todos os itens, em ordem de id."""
    saida = []
    for linha in banco._con.execute("SELECT * FROM itens ORDER BY id").fetchall():
        item = para_json(_banco._item_de_linha(linha))
        saida.append((linha["id"], linha["registro_id"], _campos_item(item), linha["criado_em"], linha["atualizado_em"]))
    return saida


def _pessoas_locais(banco: Any) -> list[tuple[int, dict]]:
    return [(l["id"], _campos_pessoa(para_json(_banco._pessoa_dict(l))))
            for l in banco._con.execute("SELECT * FROM pessoas ORDER BY id").fetchall()]


# ---------------------------------------------------------------- linhas dos recursos

def _linhas_dos_dados(chaves: Chaves, dados_recursos: Any) -> dict[str, tuple[str, str, dict]]:
    """{uid: (NOME, lista, linha)} de cada linha (dict) das listas exportadas pelos recursos."""
    saida: dict[str, tuple[str, str, dict]] = {}
    if not isinstance(dados_recursos, dict):
        return saida
    for nome, dados in dados_recursos.items():
        if not isinstance(nome, str) or not isinstance(dados, dict):
            continue
        for lista, linhas in dados.items():
            if not isinstance(lista, str) or not isinstance(linhas, list):
                continue
            for linha in linhas:
                if isinstance(linha, dict):
                    saida[uid_linha(chaves, nome, lista, linha)] = (nome, lista, linha)
    return saida


def _grupos(dados_recursos: Any) -> set[str]:
    """"NOME.lista" de cada lista exportada (mesmo vazia)."""
    if not isinstance(dados_recursos, dict):
        return set()
    return {f"{nome}.{lista}" for nome, dados in dados_recursos.items() if isinstance(dados, dict)
            for lista, linhas in dados.items() if isinstance(linhas, list)}


def _exportar_recursos(banco: Any) -> tuple[dict, set[str]]:
    """(dados de cada recurso, nomes dos que deviam exportar e não exportaram agora)."""
    dados, _avisos = recursos.exportar_dados(banco)
    esperados = {nome for nome, _ in recursos.com_gancho("exportar")} | set(recursos.falhas())
    return dados, {n for n in esperados if n not in dados}


def _referencias(linhas: Iterable[tuple[str, tuple[str, str, dict]]]) -> dict[tuple[str, str, Any], set[str]]:
    """Quem aponta para quem dentro de um recurso: um campo "X_id" de uma linha aponta para a linha de
    mesmo id na lista "Xs", "Xes" ou "X" do mesmo recurso (ciclo_id -> ciclos, lugar_id -> lugares).
    Campos que falam de registro (registro_id...) ficam de fora: registros são do núcleo."""
    por_recurso: dict[str, set[str]] = {}
    lista_linhas = list(linhas)
    for _uid, (nome, lista, _linha) in lista_linhas:
        por_recurso.setdefault(nome, set()).add(lista)
    refs: dict[tuple[str, str, Any], set[str]] = {}
    for uid, (nome, _lista, linha) in lista_linhas:
        for chave, valor in linha.items():
            if not chave.endswith("_id") or "registro" in chave or isinstance(valor, bool) or not isinstance(valor, int):
                continue
            base = chave[:-3]
            for alvo in (base + "s", base + "es", base):
                if alvo in por_recurso.get(nome, ()):
                    refs.setdefault((nome, alvo, valor), set()).add(uid)
    return refs


def _referenciada(refs: dict, uid: str, nome: str, lista: str, linha: dict) -> bool:
    quem = refs.get((nome, lista, linha.get("id")), set())
    return bool(quem - {uid})


def _mesmo_valor(exportado: Any, guardado: Any) -> bool:
    if isinstance(exportado, bool):
        exportado = int(exportado)
    if isinstance(exportado, (list, dict)) and isinstance(guardado, str):
        try:
            guardado = json.loads(guardado)
        except ValueError:
            return False
    if isinstance(exportado, float) or isinstance(guardado, float):
        try:
            return abs(float(exportado) - float(guardado)) < 1e-9
        except (TypeError, ValueError):
            return False
    return exportado == guardado


def _tabela_da_linha(banco: Any, nome: str, lista: str, linha: dict) -> str | None:
    """A tabela de um recurso de onde saiu esta linha exportada (a de mesmo id e mesmos valores).
    Nenhuma ou mais de uma: None (a linha não é apagada; na dúvida, fica)."""
    rid = linha.get("id")
    if isinstance(rid, bool) or not isinstance(rid, int):
        return None
    con = banco._con
    campos = set(linha) - {"id"}
    achadas: list[tuple[int, str]] = []
    for tabela in banco._tabelas_dos_recursos():
        colunas = {l[1] for l in con.execute(f'PRAGMA table_info("{tabela}")')}
        comuns = campos & colunas
        # A tabela precisa ter a maior parte do que a linha exportada tem (não só registro_id e data).
        if len(comuns) < 2 or len(comuns) * 10 < len(campos) * 6:
            continue
        guardada = con.execute(f'SELECT * FROM "{tabela}" WHERE id = ?', (rid,)).fetchone()
        if guardada is not None and all(_mesmo_valor(linha[c], guardada[c]) for c in comuns):
            achadas.append((len(comuns), tabela))
    if not achadas:
        return None
    melhor = max(n for n, _t in achadas)
    finalistas = [t for n, t in achadas if n == melhor]
    if len(finalistas) > 1:
        preferidas = [t for t in finalistas if t in (lista, f"{nome}_{lista}", nome)]
        finalistas = preferidas if len(preferidas) == 1 else finalistas
    return finalistas[0] if len(finalistas) == 1 else None


# ---------------------------------------------------------------- o estado local (antes e depois de mesclar)

def _ids_guardados(banco: Any) -> dict[tuple[str, int], dict]:
    return {(l["tipo"], l["local_id"]): dict(l) for l in banco._con.execute("SELECT * FROM sync_ids")}


def _lapidar(banco: Any, uid: str, tipo: str, quando: str) -> None:
    banco._con.execute("INSERT OR IGNORE INTO sync_lapides (uid, tipo, apagado_em) VALUES (?, ?, ?)", (uid, tipo, quando))


def atualizar_estado(banco: Any, chaves: Chaves, agora: str, *, absorver: bool = False) -> dict:
    """Confere o diário contra o que a sincronização lembrava (dentro de uma escrita).

    - registro, item ou pessoa sem uid ganha um (o mesmo em todos os aparelhos que já tinham o mesmo
      registro, pelo conteúdo; ou um novo, se aquele já foi apagado);
    - o que mudou ganha carimbo novo (ou, com `absorver`, só a assinatura nova: é o que a própria
      mescla acabou de mudar, que não pode passar por mudança feita aqui);
    - o que sumiu vira lápide (com `absorver`, só sai da lista: a mescla apagou por causa de uma lápide
      ou de um registro apagado, e isso já vale em todo lugar);
    - as linhas dos recursos: as que sumiram viram lápide, menos as de recurso que não exportou agora e
      quando a versão do app mudou (o formato pode ter mudado: nada é apagado por engano).
    Devolve {lapides: n}."""
    con = banco._con
    contagem = {"lapides": 0}
    aj = ajustes(banco)
    with banco._escrita():
        guardados = _ids_guardados(banco)
        usados = {l["uid"] for l in guardados.values()}
        lapides = {l[0] for l in con.execute("SELECT uid FROM sync_lapides")}
        vivos: set[tuple[str, int]] = set()

        def novo_uid(base: str, ordem: dict[str, int], prefixo: str) -> str:
            k = ordem.get(base, 0)
            ordem[base] = k + 1
            uid = base if k == 0 else f"{base}-{k}"
            if uid in usados or uid in lapides:
                uid = _uid_aleatorio(prefixo)
            usados.add(uid)
            return uid

        def conferir(tipo: str, local_id: int, ass: str, criar_uid: Callable[[], str],
                     quando: datetime | None = None) -> str:
            vivos.add((tipo, local_id))
            linha = guardados.get((tipo, local_id))
            if linha is None:
                uid = criar_uid()
                con.execute("INSERT INTO sync_ids (uid, tipo, local_id, assinatura, carimbo) VALUES (?, ?, ?, ?, ?)",
                            (uid, tipo, local_id, ass, EPOCA))
                guardados[(tipo, local_id)] = {"uid": uid, "tipo": tipo, "local_id": local_id, "assinatura": ass,
                                               "carimbo": EPOCA}
                return uid
            if linha["assinatura"] != ass:
                carimbo = linha["carimbo"] if absorver else _depois_de(linha["carimbo"], quando, agora)
                con.execute("UPDATE sync_ids SET assinatura = ?, carimbo = ? WHERE uid = ?", (ass, carimbo, linha["uid"]))
                linha.update(assinatura=ass, carimbo=carimbo)
            return linha["uid"]

        uid_de_registro: dict[int, str] = {}
        ordem: dict[str, int] = {}
        for r in con.execute("SELECT id, data_registro, criado_em, texto, privado FROM registros ORDER BY id").fetchall():
            ass = _assinatura(chaves, "registro", {"privado": bool(r["privado"])})
            uid_de_registro[r["id"]] = conferir(
                "registro", r["id"], ass,
                lambda r=r: novo_uid(_uid_registro(chaves, r["data_registro"], r["criado_em"], r["texto"]), ordem, "r"))
        ordem = {}
        for iid, rid, campos, criado_em, atualizado_em in _itens_locais(banco):
            ass = _assinatura(chaves, "item", campos)
            conferir("item", iid, ass,
                     lambda rid=rid, campos=campos, criado_em=criado_em: novo_uid(
                         _uid_item(chaves, uid_de_registro.get(rid, ""), campos["tipo"], campos["trecho"] or "",
                                   criado_em or ""), ordem, "i"),
                     _de_local(atualizado_em))
        ordem = {}
        for pid, campos in _pessoas_locais(banco):
            ass = _assinatura(chaves, "pessoa", campos)
            conferir("pessoa", pid, ass,
                     lambda campos=campos: novo_uid(_uid_pessoa(chaves, campos["nome"], campos["relacao"]), ordem, "p"))
        for (tipo, local_id), linha in list(guardados.items()):
            if (tipo, local_id) in vivos:
                continue
            if not absorver:
                _lapidar(banco, linha["uid"], tipo, agora)
                contagem["lapides"] += 1
            con.execute("DELETE FROM sync_ids WHERE uid = ?", (linha["uid"],))
            con.execute("DELETE FROM sync_conflitos WHERE uid = ?", (linha["uid"],))

        # Linhas dos recursos: a foto de agora contra a da última vez.
        dados, faltando = _exportar_recursos(banco)
        atuais = _linhas_dos_dados(chaves, dados)
        grupos_atuais = _grupos(dados)
        versao_mudou = aj.get("versao_estado") != cfg.VERSAO
        antes = [(l["uid"], l["grupo"]) for l in con.execute("SELECT uid, grupo FROM sync_linhas WHERE aparelho = ''")]
        for uid, grupo in antes:
            if uid in atuais:
                continue
            nome = grupo.split(".", 1)[0]
            if nome in faltando:
                continue                                   # o recurso não exportou: a linha fica na foto
            if not absorver and not versao_mudou and grupo in grupos_atuais:
                _lapidar(banco, uid, "linha", agora)
                contagem["lapides"] += 1
            con.execute("DELETE FROM sync_linhas WHERE aparelho = '' AND uid = ?", (uid,))
        conhecidas = {uid for uid, _ in antes}
        con.executemany("INSERT OR IGNORE INTO sync_linhas (aparelho, uid, grupo) VALUES ('', ?, ?)",
                        [(uid, f"{nome}.{lista}") for uid, (nome, lista, _l) in atuais.items() if uid not in conhecidas])
        if versao_mudou and configurada(aj):
            aj["versao_estado"] = cfg.VERSAO
            _gravar_ajustes(banco, aj)
    return contagem


# ---------------------------------------------------------------- o arquivo de cada aparelho

def tamanho_preenchido(n: int) -> int:
    """O tamanho do conteúdo (comprimido) por dentro da cifra, arredondado para cima em degraus:
    de 64 kB até 1 MB, de 256 kB até 8 MB, de 1 MB acima disso."""
    for ate, passo in DEGRAUS_PREENCHIMENTO:
        if ate is None or n <= ate:
            return max(passo, -(-n // passo) * passo)
    return n


def cifrar(chaves: Chaves, payload: dict, *, formato: int = FORMATO) -> bytes:
    """zlib(JSON) + zeros até o degrau (`tamanho_preenchido`), cifrado. Os zeros ficam depois do fim do
    fluxo zlib (o `_descomprimir` para no fim dele): o tamanho do arquivo no Drive só muda de degrau em
    degrau, sem acompanhar cada registro."""
    bruto = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    comprimido = zlib.compress(bruto, 6)
    comprimido += bytes(tamanho_preenchido(len(comprimido)) - len(comprimido))
    nonce = secrets.token_bytes(TAMANHO_NONCE)
    cabecalho = MAGICO + bytes([formato]) + nonce
    return cabecalho + AESGCM(chaves.cifra).encrypt(nonce, comprimido, cabecalho)


def _descomprimir(comprimido: bytes) -> bytes:
    descompressor = zlib.decompressobj()
    try:
        bruto = descompressor.decompress(comprimido, MAX_DESCOMPRIMIDO + 1)
    except zlib.error:
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO) from None
    if len(bruto) > MAX_DESCOMPRIMIDO or descompressor.unconsumed_tail:
        raise ArquivoSyncInvalido(ARQUIVO_GRANDE)
    if not descompressor.eof:
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO)
    return bruto


def abrir(chaves: Chaves, conteudo: bytes) -> dict:
    """O arquivo .sync de um aparelho, decifrado e conferido por cima (formato e campos principais)."""
    if not isinstance(conteudo, (bytes, bytearray, memoryview)):
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO)
    conteudo = bytes(conteudo)
    inicio = len(MAGICO) + 1 + TAMANHO_NONCE
    if len(conteudo) < inicio + TAMANHO_TAG or not conteudo.startswith(MAGICO):
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO)
    cabecalho = conteudo[:inicio]
    # Primeiro a tag (o cabeçalho, com o byte do formato, é dado associado): só depois de saber que o
    # arquivo é de um dos aparelhos se olha a versão. Antes, trocar só o byte do formato fazia
    # qualquer aparelho dizer "atualize este aparelho" sem ninguém saber a chave (22.2). Uma cifra
    # diferente no futuro usa outro MAGICO.
    try:
        comprimido = AESGCM(chaves.cifra).decrypt(cabecalho[-TAMANHO_NONCE:], conteudo[inicio:], cabecalho)
    except InvalidTag:
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO) from None
    formato = conteudo[len(MAGICO)]
    if formato > FORMATO:
        raise ArquivoSyncNovo(ARQUIVO_NOVO)
    if formato < 1:
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO)
    try:
        payload = json.loads(_descomprimir(comprimido).decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as erro:
        if isinstance(erro, ArquivoSyncInvalido):
            raise
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO) from None
    if not isinstance(payload, dict) or payload.get("app") != "entre-nos" or payload.get("tipo") != "sincronizacao":
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO)
    versao = payload.get("formato")
    if isinstance(versao, int) and not isinstance(versao, bool) and versao > FORMATO:
        raise ArquivoSyncNovo(ARQUIVO_NOVO)
    if versao != FORMATO or not isinstance(payload.get("aparelho"), str) \
            or not _RE_APARELHO.fullmatch(payload["aparelho"]) or not isinstance(payload.get("exportacao"), dict) \
            or not isinstance(payload.get("ids"), dict) or not isinstance(payload.get("lapides"), list):
        raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO)
    return payload


def _ids_para_o_arquivo(banco: Any) -> dict:
    saida: dict[str, dict[str, list[str]]] = {"registros": {}, "itens": {}, "pessoas": {}}
    nomes = {"registro": "registros", "item": "itens", "pessoa": "pessoas"}
    for l in banco._con.execute("SELECT uid, tipo, local_id, carimbo FROM sync_ids"):
        if l["tipo"] in nomes:
            saida[nomes[l["tipo"]]][str(l["local_id"])] = [l["uid"], l["carimbo"]]
    return saida


def preparar(banco: Any, chaves: Chaves, aj: dict, agora: str) -> tuple[str, bytes]:
    """O arquivo deste aparelho, com o diário de agora: (nome, bytes cifrados).

    Cada arquivo leva um número que só cresce (`sequencia`) e os números que este aparelho já viu dos
    outros (`vistos`): quem já juntou um arquivo mais novo de um aparelho recusa uma versão antiga
    dele posta no lugar (22.2)."""
    with banco.transacao():
        atualizar_estado(banco, chaves, agora)
        aj = ajustes(banco)
        aj["sequencia"] = int(aj.get("sequencia") or 0) + 1
        _gravar_ajustes(banco, aj)
    exportacao = banco.exportar()
    for fora in ("config", "historico", "alertas_dispensados", "avisos", "exportado_em"):
        exportacao.pop(fora, None)
    _dados, faltando = _exportar_recursos(banco)
    ajustes_banco = banco.config()
    with banco._trava:
        lapides = [[l["uid"], l["tipo"], l["apagado_em"]] for l in banco._con.execute(
            "SELECT uid, tipo, apagado_em FROM sync_lapides ORDER BY apagado_em, uid")]
        ids = _ids_para_o_arquivo(banco)
    payload = {"app": "entre-nos", "tipo": "sincronizacao", "formato": FORMATO, "versao_app": cfg.VERSAO,
               "aparelho": aj["aparelho"], "nome": aj.get("nome") or "", "gerado_em": _segundos(agora),
               "exportacao": exportacao, "recursos_falhos": sorted(faltando), "ids": ids, "lapides": lapides,
               "dados_dela": {k: para_json(ajustes_banco.get(k)) for k in CAMPOS_DELA if ajustes_banco.get(k)},
               "sequencia": aj["sequencia"], "vistos": _sequencias(aj)}
    return nome_do_arquivo(aj["aparelho"]), cifrar(chaves, payload)


def _sequencias(aj: dict) -> dict[str, int]:
    """{aparelho: o maior número de arquivo já visto daquele aparelho}."""
    bruto = aj.get("sequencias")
    if not isinstance(bruto, dict):
        return {}
    return {k: v for k, v in bruto.items() if isinstance(k, str) and _RE_APARELHO.fullmatch(k)
            and isinstance(v, int) and not isinstance(v, bool) and v >= 0}


def _numero(valor: Any) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor < 2 ** 53 else None


# ---------------------------------------------------------------- mesclar o arquivo de outro aparelho

def _uid_valido(valor: Any) -> str | None:
    return valor if isinstance(valor, str) and _RE_UID.fullmatch(valor) else None


def _par(mapa: Any, chave: Any) -> tuple[str | None, str]:
    """(uid, carimbo) de um id do arquivo em ids[...] (carimbo de época se faltar)."""
    if not isinstance(mapa, dict) or isinstance(chave, bool) or not isinstance(chave, int):
        return None, EPOCA
    valor = mapa.get(str(chave))
    if not isinstance(valor, list) or len(valor) != 2:
        return None, EPOCA
    uid = _uid_valido(valor[0])
    carimbo = valor[1] if _instante(valor[1]) is not None else EPOCA
    return uid, carimbo


def _lapides_do_arquivo(bruto: list) -> list[tuple[str, str, str]]:
    if len(bruto) > MAX_LAPIDES:
        raise ArquivoSyncInvalido(ARQUIVO_GRANDE)
    saida = []
    for l in bruto:
        if isinstance(l, list) and len(l) == 3 and _uid_valido(l[0]) and l[1] in TIPOS_LAPIDE \
                and _instante(l[2]) is not None and l[0][0] == l[1][0]:
            saida.append((l[0], l[1], l[2]))
    return saida


class _Mescla:
    """O trabalho de juntar UM arquivo de outro aparelho (dentro de uma transação)."""

    def __init__(self, banco: Any, chaves: Chaves, payload: dict, agora: str):
        self.banco = banco
        self.con = banco._con
        self.chaves = chaves
        self.p = payload
        self.agora = agora
        self.aparelho = payload["aparelho"]
        self.ids = payload["ids"]
        nome = payload.get("nome") if isinstance(payload.get("nome"), str) else ""
        nome = re.sub(r"[\x00-\x1f\x7f]", "", nome).strip()[:MAX_NOME]
        self.resumo = {"aparelho": self.aparelho, "nome": nome,
                       "registros_novos": 0, "pessoas_novas": 0, "itens_novos": 0, "linhas_novas": 0,
                       "atualizados": 0, "apagados": 0, "conflitos": 0, "avisos": []}
        # Ids daqui do que foi apagado por lápide (o núcleo repete nas cópias automáticas).
        self.apagados: dict[str, list] = {"registro": [], "item": [], "pessoa": [], "linha": []}
        self.recem: set[tuple[str, int]] = set()      # o que entrou agora (igual ao do outro lado)
        self.lapides: set[str] = set()
        self.uids_remotos: dict[str, tuple[str, str, dict]] = {}
        self.recursos_ok: set[str] = set()
        self.registros_antes: set[int] = set()
        self.pessoas_antes: set[int] = set()
        self.privado_antes: dict[int, bool] = {}
        self.mapas: dict[str, Any] = {}

    # ------------------------------------------------------------ 2. lápides

    def aplicar_lapides(self) -> None:
        validas = _lapides_do_arquivo(self.p.get("lapides") or [])
        self.con.executemany("INSERT OR IGNORE INTO sync_lapides (uid, tipo, apagado_em) VALUES (?, ?, ?)", validas)
        todas = {l[0] for l in self.con.execute("SELECT uid FROM sync_lapides")}
        self.lapides = todas
        alvos = [dict(l) for l in self.con.execute("SELECT uid, tipo, local_id FROM sync_ids") if l["uid"] in todas]
        ordem = {"registro": 0, "item": 1, "pessoa": 2}
        for alvo in sorted(alvos, key=lambda a: ordem.get(a["tipo"], 9)):
            tipo, local_id = alvo["tipo"], alvo["local_id"]
            existe = self.con.execute(f"SELECT 1 FROM {self._tabela(tipo)} WHERE id = ?", (local_id,)).fetchone()
            if existe is not None:
                if tipo == "registro":
                    self.banco.apagar_registro(local_id)
                elif tipo == "item":
                    self.banco.apagar_item(local_id)
                else:
                    self.banco.apagar_pessoa(local_id)
                self.apagados[tipo].append(local_id)
                self.resumo["apagados"] += 1
            self.con.execute("DELETE FROM sync_ids WHERE uid = ?", (alvo["uid"],))
        self._apagar_linhas(todas)

    @staticmethod
    def _tabela(tipo: str) -> str:
        return {"registro": "registros", "item": "itens", "pessoa": "pessoas"}[tipo]

    def _apagar_linhas(self, lapides: set[str]) -> None:
        dados, _faltando = _exportar_recursos(self.banco)
        locais = _linhas_dos_dados(self.chaves, dados)
        alvo = {u: locais[u] for u in locais if u in lapides}
        while alvo:
            refs = _referencias(locais.items())
            livres = [u for u, (nome, lista, linha) in alvo.items() if not _referenciada(refs, u, nome, lista, linha)]
            if not livres:
                break                     # só sobrou o que outra linha daqui ainda aponta: fica
            for uid in livres:
                nome, lista, linha = alvo.pop(uid)
                locais.pop(uid, None)
                tabela = _tabela_da_linha(self.banco, nome, lista, linha)
                if tabela is None:
                    continue
                try:
                    self.banco.apagar(tabela, linha["id"], historico=False)
                except (_banco.NaoEncontrado, _banco.OperacaoProibida):
                    continue
                self.apagados["linha"].append((tabela, linha["id"]))
                self.resumo["apagados"] += 1

    # ------------------------------------------------------------ 3. o que é novo: pela lógica de importar

    def filtrar(self) -> dict:
        exp = self.p["exportacao"]
        ids = self.ids
        lapides = self.lapides
        mortas = set()
        pessoas = []
        for bruta in exp.get("pessoas") or []:
            if not isinstance(bruta, dict):
                continue
            uid, _ = _par(ids.get("pessoas"), bruta.get("id"))
            if uid in lapides:
                mortas.add(bruta.get("id"))
                continue
            pessoas.append(bruta)
        registros = []
        for bruto in exp.get("registros") or []:
            if not isinstance(bruto, dict):
                continue
            uid, _ = _par(ids.get("registros"), bruto.get("id"))
            if uid in lapides:
                continue
            r = dict(bruto)
            itens = []
            for item in bruto.get("itens") or []:
                if not isinstance(item, dict):
                    continue
                u, _ = _par(ids.get("itens"), item.get("id"))
                if u in lapides:
                    continue
                if item.get("pessoa_id") in mortas:
                    item = {**item, "pessoa_id": None}
                itens.append(item)
            r["itens"] = itens
            r["pessoas"] = [x for x in bruto.get("pessoas") or [] if not (isinstance(x, dict) and x.get("id") in mortas)]
            registros.append(r)
        self.recursos_filtrados = self._filtrar_recursos(exp.get("recursos"))
        return {"app": "entre-nos", "versao": str(exp.get("versao") or "?"), "esquema": exp.get("esquema"),
                "pessoas": pessoas, "registros": registros, "recursos": self.recursos_filtrados}

    def _filtrar_recursos(self, dados: Any) -> dict:
        """As linhas dos recursos do outro aparelho que podem entrar: nada que tem lápide, e só o que
        ainda não veio dele antes (o que veio e foi apagado aqui não volta). Uma linha que já veio entra
        de novo só se uma linha nova aponta para ela (o lugar de uma menção nova)."""
        if not isinstance(dados, dict):
            return {}
        todas = _linhas_dos_dados(self.chaves, dados)
        vistos = {l[0] for l in self.con.execute("SELECT uid FROM sync_linhas WHERE aparelho = ?", (self.aparelho,))}
        manter = {u for u in todas if u not in self.lapides and u not in vistos}
        refs = _referencias(todas.items())
        mudou = True
        while mudou:
            mudou = False
            for uid, (nome, lista, linha) in todas.items():
                if uid in manter or uid in self.lapides:
                    continue
                if refs.get((nome, lista, linha.get("id")), set()) & manter:
                    manter.add(uid)
                    mudou = True
        self.uids_remotos = todas
        saida: dict[str, Any] = {}
        for nome, recurso in dados.items():
            if not isinstance(nome, str) or not isinstance(recurso, dict):
                continue
            novo = {}
            for lista, linhas in recurso.items():
                if not isinstance(linhas, list):
                    novo[lista] = linhas
                    continue
                novo[lista] = [l for l in linhas if isinstance(l, dict)
                               and uid_linha(self.chaves, nome, lista, l) in manter]
            saida[nome] = novo
        return saida

    def importar(self, exp: dict) -> dict:
        try:
            exportacao = _banco.validar_exportacao(exp)
        except ValueError as erro:
            if str(erro) == _banco.EXPORTACAO_NOVA:
                raise ArquivoSyncNovo(ARQUIVO_NOVO) from None
            raise ArquivoSyncInvalido(ARQUIVO_ESTRANHO) from None
        self.registros_antes = {l[0] for l in self.con.execute("SELECT id FROM registros")}
        self.privado_antes = {l[0]: bool(l[1]) for l in self.con.execute("SELECT id, privado FROM registros")}
        # Quem já tem identidade aqui não é casado de novo pelo nome (pode ter outro nome aqui).
        pessoas_por_uid = {l[0]: l[1] for l in self.con.execute("SELECT uid, local_id FROM sync_ids WHERE tipo = 'pessoa'")}
        conhecidas = {}
        for bruta in exp.get("pessoas") or []:
            uid, _ = _par(self.ids.get("pessoas"), bruta.get("id"))
            if uid in pessoas_por_uid:
                conhecidas[bruta["id"]] = pessoas_por_uid[uid]
        mapas: dict[str, Any] = {"pessoas_conhecidas": conhecidas}
        resultado = self.banco.juntar_exportacao(exportacao, mapas)
        self.mapas = mapas
        self.resumo["registros_novos"] = resultado.get("registros_novos", 0)
        self.resumo["pessoas_novas"] = resultado.get("pessoas_novas", 0)
        importados = resultado.get("recursos") or {}
        self.recursos_ok = set()
        for nome, dados in (exp.get("recursos") or {}).items():
            tem_linhas = isinstance(dados, dict) and any(isinstance(v, list) and v for v in dados.values())
            if not tem_linhas or nome in importados:
                self.recursos_ok.add(nome)
            else:
                self.resumo["avisos"].append(RECURSO_FORA)
        self.resumo["linhas_novas"] = sum(r.get("novos", 0) for r in importados.values()
                                          if isinstance(r, dict) and isinstance(r.get("novos"), int))
        return resultado

    # ------------------------------------------------------------ identidades do que entrou

    def ligar_identidades(self) -> None:
        guardados = _ids_guardados(self.banco)
        usados = {l["uid"] for l in guardados.values()}
        exp = self.p["exportacao"]

        def ligar(tipo: str, local_id: int, uid: str | None, carimbo: str, ass: str, novo: bool) -> None:
            atual = guardados.get((tipo, local_id))
            if uid is None:
                return
            if atual is None:
                if uid in usados:
                    return
                self.con.execute("INSERT INTO sync_ids (uid, tipo, local_id, assinatura, carimbo) VALUES (?, ?, ?, ?, ?)",
                                 (uid, tipo, local_id, ass, carimbo))
                usados.add(uid)
                guardados[(tipo, local_id)] = {"uid": uid, "assinatura": ass, "carimbo": carimbo}
                if novo:
                    self.recem.add((tipo, local_id))
            elif atual["uid"] != uid and uid < atual["uid"] and uid not in usados:
                # O mesmo registro (ou pessoa) com duas identidades: os dois aparelhos ficam com a menor.
                self.con.execute("UPDATE sync_ids SET uid = ? WHERE uid = ?", (uid, atual["uid"]))
                usados.discard(atual["uid"])
                usados.add(uid)
                atual["uid"] = uid

        registros_locais = {l[0]: bool(l[1]) for l in self.con.execute("SELECT id, privado FROM registros")}
        for file_id, local_id in (self.mapas.get("registros") or {}).items():
            uid, carimbo = _par(self.ids.get("registros"), file_id)
            if local_id in registros_locais:
                ass = _assinatura(self.chaves, "registro", {"privado": registros_locais[local_id]})
                ligar("registro", local_id, uid, carimbo, ass, local_id not in self.registros_antes)
        itens_por_id = {}
        for r in exp.get("registros") or []:
            if isinstance(r, dict):
                for it in r.get("itens") or []:
                    if isinstance(it, dict) and isinstance(it.get("id"), int):
                        itens_por_id[it["id"]] = r.get("id")
        for file_id, local_id in (self.mapas.get("itens") or {}).items():
            linha = self.con.execute("SELECT * FROM itens WHERE id = ?", (local_id,)).fetchone()
            if linha is None:
                continue
            novo = linha["registro_id"] not in self.registros_antes
            if not novo:
                continue                 # item de registro que já estava aqui: a mescla das edições cuida
            uid, carimbo = _par(self.ids.get("itens"), file_id)
            ass = _assinatura(self.chaves, "item", _campos_item(para_json(_banco._item_de_linha(linha))))
            ligar("item", local_id, uid, carimbo, ass, True)
        pessoas_antes = getattr(self, "pessoas_antes", set())
        for file_id, local_id in (self.mapas.get("pessoas") or {}).items():
            linha = self.con.execute("SELECT * FROM pessoas WHERE id = ?", (local_id,)).fetchone()
            if linha is None:
                continue
            uid, carimbo = _par(self.ids.get("pessoas"), file_id)
            ass = _assinatura(self.chaves, "pessoa", _campos_pessoa(para_json(_banco._pessoa_dict(linha))))
            ligar("pessoa", local_id, uid, carimbo, ass, local_id not in pessoas_antes)

    # ------------------------------------------------------------ 4. o que mudou dos dois lados

    def mesclar_edicoes(self) -> None:
        guardados = {l["uid"]: dict(l) for l in self.con.execute("SELECT * FROM sync_ids")}
        exp = self.p["exportacao"]
        pessoas_por_uid = {l["uid"]: l["local_id"] for l in guardados.values() if l["tipo"] == "pessoa"}
        uid_pessoa_remota = {}
        for bruta in exp.get("pessoas") or []:
            if isinstance(bruta, dict):
                uid, carimbo = _par(self.ids.get("pessoas"), bruta.get("id"))
                if uid:
                    uid_pessoa_remota[bruta.get("id")] = uid
                if uid and uid in guardados and ("pessoa", guardados[uid]["local_id"]) not in self.recem:
                    self._mesclar_pessoa(guardados[uid], _campos_pessoa(bruta), carimbo)
        for bruto in exp.get("registros") or []:
            if not isinstance(bruto, dict):
                continue
            uid_r, carimbo_r = _par(self.ids.get("registros"), bruto.get("id"))
            linha_r = guardados.get(uid_r) if uid_r else None
            local_rid = linha_r["local_id"] if linha_r else (self.mapas.get("registros") or {}).get(bruto.get("id"))
            if linha_r and local_rid in self.registros_antes:
                self._mesclar_privado(linha_r, bool(bruto.get("privado")), carimbo_r)
            if local_rid is None or local_rid not in self.registros_antes:
                continue                 # registro novo: entrou inteiro pela importação
            itens = [item for item in bruto.get("itens") or [] if isinstance(item, dict)]
            remotos = {u for u in (_par(self.ids.get("itens"), item.get("id"))[0] for item in itens) if u}
            for item in itens:
                uid_i, carimbo_i = _par(self.ids.get("itens"), item.get("id"))
                if uid_i is None or uid_i in self.lapides:
                    continue
                campos = _campos_item(item)
                pessoa_uid = uid_pessoa_remota.get(item.get("pessoa_id"))
                linha_i = guardados.get(uid_i)
                if linha_i is not None:
                    if ("item", linha_i["local_id"]) not in self.recem:
                        self._mesclar_item(linha_i, campos, carimbo_i, pessoas_por_uid.get(pessoa_uid))
                    continue
                self._item_novo(local_rid, uid_i, campos, carimbo_i, pessoas_por_uid.get(pessoa_uid), guardados,
                                remotos)

    def _conflito(self, linha: dict, remoto: dict, carimbo: str) -> None:
        texto = _canonico(remoto)
        existente = self.con.execute("SELECT id, remoto FROM sync_conflitos WHERE uid = ? AND aparelho = ?",
                                     (linha["uid"], self.aparelho)).fetchone()
        if existente is not None:
            if existente["remoto"] != texto:
                self.con.execute("UPDATE sync_conflitos SET remoto = ?, carimbo = ? WHERE id = ?",
                                 (texto, carimbo, existente["id"]))
            return
        self.con.execute("INSERT INTO sync_conflitos (uid, tipo, local_id, aparelho, remoto, carimbo, criado_em)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (linha["uid"], linha["tipo"], linha["local_id"], self.aparelho, texto, carimbo,
                          _segundos(self.agora)))
        self.resumo["conflitos"] += 1

    def _resolvido(self, uid: str) -> None:
        self.con.execute("DELETE FROM sync_conflitos WHERE uid = ? AND aparelho = ?", (uid, self.aparelho))

    def _guardar(self, linha: dict, tipo: str, campos: Any, carimbo: str) -> None:
        """Grava a assinatura do que ficou aqui e o carimbo (o de lá, quando veio de lá: o mesmo carimbo
        nos dois aparelhos quer dizer a mesma versão, `_decidir_edicao`)."""
        ass = _assinatura(self.chaves, tipo, campos)
        self.con.execute("UPDATE sync_ids SET assinatura = ?, carimbo = ? WHERE uid = ?", (ass, carimbo, linha["uid"]))
        linha.update(assinatura=ass, carimbo=carimbo)

    def _mesclar_item(self, linha: dict, remoto: dict, carimbo: str, pessoa_local: int | None) -> None:
        atual = self.banco.item(linha["local_id"])
        if atual is None:
            return
        aqui = _campos_item(para_json(atual))
        if _assinatura(self.chaves, "item", remoto) == _assinatura(self.chaves, "item", aqui):
            self._guardar(linha, "item", aqui, _maior(linha["carimbo"], carimbo))
            self._resolvido(linha["uid"])
            return
        decisao = _decidir_edicao(linha["carimbo"], carimbo)
        if decisao == "aqui":
            self._resolvido(linha["uid"])
            return
        if decisao == "conflito":
            self._conflito(linha, remoto, carimbo)
            return
        if aplicar_item(self.banco, linha["local_id"], remoto, pessoa_local):
            novo = _campos_item(para_json(self.banco.item(linha["local_id"])))
            self._guardar(linha, "item", novo, carimbo)
            self.resumo["atualizados"] += 1
            self._resolvido(linha["uid"])

    def _item_novo(self, local_rid: int, uid: str, campos: dict, carimbo: str, pessoa_local: int | None,
                   guardados: dict, remotos: set[str]) -> None:
        """Item que o outro aparelho tem num registro que os dois têm (acrescentado lá depois, ou o
        mesmo item visto pelos dois com identidades diferentes). `remotos`: os uids dos itens deste
        registro no arquivo (um item daqui que já tem par lá não serve de par para outro)."""
        if uid in guardados:
            return
        ass = _assinatura(self.chaves, "item", campos)
        por_local = {g["local_id"]: g for g in guardados.values() if g["tipo"] == "item"}
        for l in self.con.execute("SELECT * FROM itens WHERE registro_id = ? ORDER BY id", (local_rid,)).fetchall():
            existente = por_local.get(l["id"])
            if existente is not None and existente["uid"] in remotos:
                continue
            if _assinatura(self.chaves, "item", _campos_item(para_json(_banco._item_de_linha(l)))) != ass:
                continue
            # O mesmo item com outra identidade: os dois aparelhos ficam com a menor.
            if existente is None:
                self.con.execute("INSERT INTO sync_ids (uid, tipo, local_id, assinatura, carimbo) VALUES (?, 'item', ?, ?, ?)",
                                 (uid, l["id"], ass, carimbo))
                guardados[uid] = {"uid": uid, "tipo": "item", "local_id": l["id"], "assinatura": ass, "carimbo": carimbo}
            elif uid < existente["uid"]:
                self.con.execute("UPDATE sync_ids SET uid = ? WHERE uid = ?", (uid, existente["uid"]))
                guardados.pop(existente["uid"], None)
                existente["uid"] = uid
                guardados[uid] = existente
            return
        texto = self.con.execute("SELECT texto FROM registros WHERE id = ?", (local_rid,)).fetchone()
        if texto is None or not campos.get("trecho") or campos["trecho"] not in texto[0]:
            return
        try:
            novo_id = self.banco.acrescentar_item(local_rid, {**campos, "pessoa_id": pessoa_local})
        except (ValueError, TypeError, KeyError, _banco.NaoEncontrado):
            return
        linha = self.con.execute("SELECT * FROM itens WHERE id = ?", (novo_id,)).fetchone()
        ass_local = _assinatura(self.chaves, "item", _campos_item(para_json(_banco._item_de_linha(linha))))
        self.con.execute("INSERT INTO sync_ids (uid, tipo, local_id, assinatura, carimbo) VALUES (?, 'item', ?, ?, ?)",
                         (uid, novo_id, ass_local, carimbo))
        guardados[uid] = {"uid": uid, "tipo": "item", "local_id": novo_id, "assinatura": ass_local, "carimbo": carimbo}
        self.recem.add(("item", novo_id))
        self.resumo["itens_novos"] += 1

    def _mesclar_pessoa(self, linha: dict, remoto: dict, carimbo: str) -> None:
        atual = self.banco.pessoa(linha["local_id"])
        if atual is None:
            return
        aqui = _campos_pessoa(para_json(atual))
        if _assinatura(self.chaves, "pessoa", remoto) == _assinatura(self.chaves, "pessoa", aqui):
            self._guardar(linha, "pessoa", aqui, _maior(linha["carimbo"], carimbo))
            self._resolvido(linha["uid"])
            return
        decisao = _decidir_edicao(linha["carimbo"], carimbo)
        if decisao == "aqui":
            self._resolvido(linha["uid"])
            return
        if decisao == "conflito":
            # Sem saber qual veio depois: o que um lado tem e o outro deixou em branco se completa
            # (os dois aparelhos chegam à mesma pessoa). Só dois valores diferentes viram pergunta.
            combinado = _combinar_pessoa(aqui, remoto)
            if combinado is None:
                self._conflito(linha, remoto, carimbo)
                return
            if combinado != aqui and aplicar_pessoa(self.banco, linha["local_id"], combinado):
                self.resumo["atualizados"] += 1
            atual = self.banco.pessoa(linha["local_id"])
            self._guardar(linha, "pessoa", _campos_pessoa(para_json(atual)) if atual else combinado,
                          _maior(linha["carimbo"], carimbo))
            self._resolvido(linha["uid"])
            return
        if aplicar_pessoa(self.banco, linha["local_id"], remoto):
            self._guardar(linha, "pessoa", _campos_pessoa(para_json(self.banco.pessoa(linha["local_id"]))), carimbo)
            self.resumo["atualizados"] += 1
            self._resolvido(linha["uid"])

    def _mesclar_privado(self, linha: dict, privado_la: bool, carimbo: str) -> None:
        """O 🔒 de um registro que os dois têm: vale o mais recente; na dúvida, fica 🔒 (nunca pergunta)."""
        rid = linha["local_id"]
        antes = self.privado_antes.get(rid)
        if antes is None:
            return
        decisao = "aqui" if antes == privado_la else _decidir(linha["carimbo"], carimbo)
        final = privado_la if decisao == "la" else (antes or privado_la) if decisao == "conflito" else antes
        gravado = self.con.execute("SELECT privado FROM registros WHERE id = ?", (rid,)).fetchone()
        if gravado is None:
            return
        if bool(gravado[0]) != final:
            self.banco.marcar_privado(rid, final)        # desfaz o "só liga" da importação, se for o caso
        if final != antes:
            self.resumo["atualizados"] += 1
        self._guardar(linha, "registro", {"privado": final},
                      carimbo if decisao == "la" else _maior(linha["carimbo"], carimbo) if decisao == "conflito"
                      else linha["carimbo"])

    # ------------------------------------------------------------ dados dela, vistos e o aparelho

    def completar_dados_dela(self) -> None:
        dela = self.p.get("dados_dela")
        if not isinstance(dela, dict):
            return
        atuais = self.banco.config()
        faltam = {k: dela[k] for k in CAMPOS_DELA if dela.get(k) and not atuais.get(k)}
        for chave, valor in faltam.items():
            try:
                self.banco.salvar_config(**{chave: valor})
            except (ValueError, TypeError):
                continue

    def guardar_vistos(self) -> None:
        falhos = set(n for n in self.p.get("recursos_falhos") or [] if isinstance(n, str))
        grupos = {f"{nome}.{lista}" for nome, lista, _l in self.uids_remotos.values()}
        validos = {g for g in grupos if g.split(".", 1)[0] in self.recursos_ok and g.split(".", 1)[0] not in falhos}
        for grupo in validos:
            self.con.execute("DELETE FROM sync_linhas WHERE aparelho = ? AND grupo = ?", (self.aparelho, grupo))
        self.con.executemany("INSERT OR IGNORE INTO sync_linhas (aparelho, uid, grupo) VALUES (?, ?, ?)",
                             [(self.aparelho, uid, f"{nome}.{lista}") for uid, (nome, lista, _l) in self.uids_remotos.items()
                              if f"{nome}.{lista}" in validos])

    def registrar_aparelho(self, etiqueta: str | None) -> None:
        gerado = self.p.get("gerado_em") if _instante(self.p.get("gerado_em")) else None
        self.con.execute("INSERT INTO sync_aparelhos (aparelho, nome, gerado_em, etiqueta, mesclado_em) VALUES (?, ?, ?, ?, ?)"
                         " ON CONFLICT(aparelho) DO UPDATE SET nome = excluded.nome, gerado_em = excluded.gerado_em,"
                         " etiqueta = excluded.etiqueta, mesclado_em = excluded.mesclado_em",
                         (self.aparelho, self.resumo["nome"] or None, gerado, etiqueta, _segundos(self.agora)))


def aplicar_item(banco: Any, local_id: int, campos: dict, pessoa_local: int | None) -> bool:
    """Leva para o item daqui os campos (a versão do outro aparelho). False se não deu (o trecho não
    está no texto daqui, valor que este app não conhece): fica a versão daqui."""
    atual = banco.item(local_id)
    if atual is None:
        return False
    aqui = _campos_item(para_json(atual))
    mudar = {k: v for k, v in campos.items() if k in CAMPOS_ITEM and aqui.get(k) != v}
    if "pessoa" in mudar or (pessoa_local is not None and atual.pessoa_id != pessoa_local):
        if pessoa_local is not None:
            mudar["pessoa_id"] = pessoa_local
            mudar.setdefault("pessoa", campos.get("pessoa"))
    if not mudar:
        return True
    try:
        banco.atualizar_item(local_id, **mudar)
    except (ValueError, TypeError, KeyError, _banco.NaoEncontrado):
        return False
    return True


def _combinar_pessoa(aqui: dict, la: dict) -> dict | None:
    """A mesma pessoa nos dois aparelhos, sem saber qual mudou por último: o que um tem e o outro
    deixou em branco se junta (apelidos somam). Dois valores diferentes no mesmo campo: None (pergunta)."""
    junto: dict[str, Any] = {}
    for campo in CAMPOS_PESSOA:
        a, b = aqui.get(campo), la.get(campo)
        if campo == "apelidos":
            vistos = {_banco._chave_pessoa(x) for x in a or []}
            junto[campo] = list(a or []) + [x for x in (b or []) if _banco._chave_pessoa(x) not in vistos]
        elif a in (None, "") or a == b:
            junto[campo] = b if a in (None, "") else a
        elif b in (None, ""):
            junto[campo] = a
        else:
            return None
    return junto


def aplicar_pessoa(banco: Any, local_id: int, campos: dict) -> bool:
    atual = banco.pessoa(local_id)
    if atual is None:
        return False
    aqui = _campos_pessoa(para_json(atual))
    mudar = {k: v for k, v in campos.items() if k in CAMPOS_PESSOA and aqui.get(k) != v}
    if not mudar:
        return True
    try:
        banco.atualizar_pessoa(local_id, **mudar)
    except (ValueError, TypeError, KeyError, _banco.NaoEncontrado):
        return False
    return True


def mesclar(banco: Any, chaves: Chaves, conteudo: bytes, *, proprio: str, agora: str,
            nome_arquivo: str | None = None, etiqueta: str | None = None) -> dict:
    """Junta o arquivo .sync de outro aparelho a este diário, numa transação só (um arquivo gravado).

    Devolve o resumo para a tela ({aparelho, nome, registros_novos, ..., conflitos, avisos}) e, em
    "_apagados", os ids daqui do que as lápides apagaram (o núcleo repete nas cópias automáticas).
    Arquivo do próprio aparelho: {"proprio": true} e nada muda."""
    payload = abrir(chaves, conteudo)
    if nome_arquivo is not None and aparelho_do_arquivo(nome_arquivo) != payload["aparelho"]:
        raise ArquivoSyncInvalido(ARQUIVO_TROCADO)
    if payload["aparelho"] == proprio:
        return {"proprio": True, "aparelho": proprio}
    sequencia = _numero(payload.get("sequencia"))
    if sequencia is not None and sequencia < _sequencias(ajustes(banco)).get(payload["aparelho"], 0):
        raise ArquivoSyncInvalido(ARQUIVO_ANTIGO)
    trabalho = _Mescla(banco, chaves, payload, agora)
    with banco.transacao():
        lapides_antes = banco._con.execute("SELECT count(*) FROM sync_lapides").fetchone()[0]
        atualizar_estado(banco, chaves, agora)
        trabalho.pessoas_antes = {l[0] for l in banco._con.execute("SELECT id FROM pessoas")}
        trabalho.aplicar_lapides()
        trabalho.importar(trabalho.filtrar())
        trabalho.ligar_identidades()
        trabalho.mesclar_edicoes()
        trabalho.completar_dados_dela()
        trabalho.guardar_vistos()
        trabalho.registrar_aparelho(etiqueta)
        _guardar_sequencias(banco, payload, proprio, sequencia)
        atualizar_estado(banco, chaves, agora, absorver=True)
        r = trabalho.resumo
        if r["apagados"] or r["atualizados"] or r["itens_novos"]:
            partes = [f"{r['apagados']} {'apagado' if r['apagados'] == 1 else 'apagados'}",
                      f"{r['atualizados']} {'atualizado' if r['atualizados'] == 1 else 'atualizados'}"]
            if r["itens_novos"]:
                partes.append(f"{r['itens_novos']} {'item novo' if r['itens_novos'] == 1 else 'itens novos'}")
            banco._historico_add("sincronizar", "registros", None, "sincronização: " + ", ".join(partes))
        r["lapides_novas"] = banco._con.execute("SELECT count(*) FROM sync_lapides").fetchone()[0] - lapides_antes
    r["avisos"] = list(dict.fromkeys(r["avisos"]))
    return {**r, "_apagados": trabalho.apagados}


def _guardar_sequencias(banco: Any, payload: dict, proprio: str, sequencia: int | None) -> None:
    """O maior número visto de cada aparelho: o do arquivo que acabou de entrar e os que ele diz ter
    visto dos outros (assim um aparelho recusa a versão velha de um terceiro mesmo antes de juntar a
    nova dele)."""
    aj = ajustes(banco)
    vistas = _sequencias(aj)
    antes = dict(vistas)
    remetente = payload["aparelho"]
    if sequencia is not None:
        vistas[remetente] = max(vistas.get(remetente, 0), sequencia)
    outros = payload.get("vistos")
    if isinstance(outros, dict):
        for aparelho, n in list(outros.items())[:1000]:
            n = _numero(n)
            if n is None or not isinstance(aparelho, str) or not _RE_APARELHO.fullmatch(aparelho) \
                    or aparelho in (proprio, remetente):
                continue
            vistas[aparelho] = max(vistas.get(aparelho, 0), n)
    if vistas != antes:
        aj["sequencias"] = vistas
        _gravar_ajustes(banco, aj)


def apagar_nas_copias(copia: Any, apagados: dict) -> bool:
    """Numa cópia automática (backups/): o que as lápides apagaram aqui sai também dela. False = nada mudou."""
    mudou = False
    for tipo, apagar in (("registro", copia.apagar_registro), ("item", copia.apagar_item),
                         ("pessoa", copia.apagar_pessoa)):
        for local_id in apagados.get(tipo) or ():
            try:
                apagar(local_id)
                mudou = True
            except _banco.NaoEncontrado:
                continue
    for tabela, local_id in apagados.get("linha") or ():
        try:
            copia.apagar(tabela, local_id, historico=False)
            mudou = True
        except (_banco.NaoEncontrado, _banco.OperacaoProibida):
            continue
    return mudou


# ---------------------------------------------------------------- perguntas (conflitos)

def conflitos(banco: Any) -> list[dict]:
    """[{id, tipo, aparelho, nome_aparelho, criado_em, contexto, campos: [{campo, aqui, la}]}]: só os
    campos que estão diferentes. Conflito de algo que não existe mais sai (e não aparece)."""
    con = banco._con
    saida = []
    with banco._trava:
        nomes = {l[0]: l[1] for l in con.execute("SELECT aparelho, nome FROM sync_aparelhos")}
        linhas = con.execute("SELECT * FROM sync_conflitos ORDER BY id").fetchall()
    discretos = recursos.discretos(banco) if any(c["tipo"] == "item" for c in linhas) else set()
    for c in linhas:
        try:
            remoto = json.loads(c["remoto"])
        except ValueError:
            remoto = None
        if c["tipo"] == "item":
            atual = banco.item(c["local_id"])
            aqui = _campos_item(para_json(atual)) if atual is not None else None
            contexto = None
            if atual is not None:
                reg = banco.registro(atual.registro_id)
                contexto = {"registro_id": atual.registro_id,
                            "data_registro": para_json(reg["data_registro"]) if reg else None}
        else:
            atual = banco.pessoa(c["local_id"])
            aqui = _campos_pessoa(para_json(atual)) if atual is not None else None
            contexto = None
        if aqui is None or not isinstance(remoto, dict):
            with banco._escrita():
                con.execute("DELETE FROM sync_conflitos WHERE id = ?", (c["id"],))
            continue
        campos = [{"campo": k, "aqui": aqui.get(k), "la": remoto.get(k)} for k in aqui if aqui.get(k) != remoto.get(k)]
        if not campos:
            with banco._escrita():
                con.execute("DELETE FROM sync_conflitos WHERE id = ?", (c["id"],))
            continue
        # Item de um registro discreto (o ciclo, seções 20.8 e 20.10): a pergunta não mostra o texto
        # (resumo, trecho, sobre o quê, quem); a tela pede para abrir o registro.
        discreto = c["tipo"] == "item" and contexto is not None and contexto["registro_id"] in discretos
        if discreto:
            campos = [{**x, "aqui": None, "la": None, "reservado": True} if x["campo"] in _CAMPOS_RESERVADOS else x
                      for x in campos]
        saida.append({"id": c["id"], "tipo": c["tipo"], "aparelho": c["aparelho"],
                      "nome_aparelho": nomes.get(c["aparelho"]) or "outro aparelho", "criado_em": c["criado_em"],
                      "resumo": None if discreto else aqui.get("resumo") if c["tipo"] == "item"
                      else (aqui.get("nome") or aqui.get("relacao")),
                      "discreto": discreto, "contexto": contexto, "campos": campos})
    return saida


# Na pergunta de um item de registro discreto, estes campos não mostram o valor.
_CAMPOS_RESERVADOS = frozenset({"resumo", "trecho", "alvo", "pessoa", "data_expressao", "categorias"})


def _carimbo_da_resposta(base: str, agora: str) -> str:
    """O carimbo de uma resposta: depois das duas versões e fora da janela delas (nos outros aparelhos
    a escolha ganha, e não vira outra pergunta), e ÚNICO. Antes ele era a base + janela truncada ao
    segundo: dois aparelhos respondendo dentro da janela ganhavam o mesmo carimbo, que vale como "a
    mesma versão" (`_decidir_edicao`), e cada um ficava com a sua para sempre, sem pergunta (22.2).
    Agora: o instante de agora, se já passou da janela; senão, o fim da janela + 1 s com os
    microssegundos de agora (o relógio de cada aparelho). Respostas diferentes dadas quase juntas
    caem perto uma da outra e voltam como pergunta; respostas iguais têm a mesma assinatura e
    convergem."""
    instante_agora = _instante(agora) or datetime.now(timezone.utc)
    limiar = (_instante(base) or _instante(EPOCA)) + timedelta(seconds=JANELA_CONFLITO)
    if instante_agora > limiar:
        quando = instante_agora
    else:
        quando = limiar.replace(microsecond=0) + timedelta(seconds=1, microseconds=instante_agora.microsecond or 1)
    return quando.astimezone(timezone.utc).isoformat(timespec="microseconds")


def resolver_conflito(banco: Any, chaves: Chaves, conflito_id: int, fica: str, agora: str) -> None:
    """A pessoa escolheu: "este" (a versão deste aparelho) ou "outro". A escolha vale como a mudança
    mais recente: vai para os outros aparelhos na próxima sincronização."""
    if fica not in ("este", "outro"):
        raise ValueError("Escolha qual versão fica: “este” ou “outro”.")
    con = banco._con
    with banco.transacao():
        c = con.execute("SELECT * FROM sync_conflitos WHERE id = ?", (conflito_id,)).fetchone()
        if c is None:
            raise _banco.NaoEncontrado("Essa pergunta já foi respondida.")
        linha = con.execute("SELECT * FROM sync_ids WHERE uid = ?", (c["uid"],)).fetchone()
        if linha is None:
            con.execute("DELETE FROM sync_conflitos WHERE uid = ?", (c["uid"],))
            raise _banco.NaoEncontrado("Isso não existe mais neste aparelho.")
        linha = dict(linha)
        if fica == "outro":
            remoto = json.loads(c["remoto"])
            ok = (aplicar_item(banco, c["local_id"], remoto, None) if c["tipo"] == "item"
                  else aplicar_pessoa(banco, c["local_id"], remoto))
            if not ok:
                raise ValueError("Não deu para usar a versão do outro aparelho aqui. A deste aparelho continua.")
        # A escolha vale como a mudança mais recente (com a assinatura do que ficou, para a próxima
        # conferência não achar que mudou de novo).
        if c["tipo"] == "item":
            atual = banco.item(c["local_id"])
            campos = _campos_item(para_json(atual)) if atual is not None else {}
        else:
            atual = banco.pessoa(c["local_id"])
            campos = _campos_pessoa(para_json(atual)) if atual is not None else {}
        carimbo = _carimbo_da_resposta(_maior(linha["carimbo"], c["carimbo"]), agora)
        con.execute("UPDATE sync_ids SET assinatura = ?, carimbo = ? WHERE uid = ?",
                    (_assinatura(chaves, c["tipo"], campos), carimbo, c["uid"]))
        con.execute("DELETE FROM sync_conflitos WHERE uid = ?", (c["uid"],))


__all__ = [
    "ARQUIVO_CONFIG", "ArquivoSyncInvalido", "ArquivoSyncNovo", "CONFIG_MUDOU", "Chaves", "EPOCA", "FORMATO",
    "JANELA_CONFLITO", "JA_CONFIGURADA", "SEM_SENHA", "SENHA_ERRADA", "SenhaSyncErrada", "abrir", "agora_utc",
    "ajustes", "aparelho_do_arquivo", "apagar_nas_copias", "atualizar_estado", "chaves_da_senha", "chaves_guardadas",
    "cifrar", "configuracao_guardada", "configurada", "conflitos", "depois_de_restaurar", "desligar", "estado",
    "ler_configuracao", "ligar", "marcar_enviado", "mesclar", "mesma_configuracao", "mudar_ajustes",
    "nome_do_arquivo", "nova_configuracao", "para_fora", "preparar", "problema_da_senha", "resolver_conflito",
    "tamanho_preenchido", "texto_da_configuracao", "uid_linha", "ARQUIVO_ANTIGO", "SENHA_IGUAL_AO_PIN",
]
