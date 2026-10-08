"""Cofre: o banco serializado, criptografado com o PIN (DESIGN.md, seção 13).

Formato do arquivo (inteiros big-endian):

    b"ENTRENOS1" | n (4) | r (4) | p (4) | sal (16) | nonce (12) | AES-256-GCM(dados) + tag (16)

A chave sai do PIN por scrypt (n, r, p gravados no cabeçalho). O cabeçalho inteiro, do
b"ENTRENOS1" ao nonce, é o dado associado do GCM: qualquer byte alterado faz a decifragem
falhar. A chave fica só em memória enquanto o cofre está aberto.

O PIN da IA (DESIGN.md, seção 17.5) não cifra nada: fica guardado dentro do banco como um
verificador scrypt (`criar_verificador`), que só serve para conferir o PIN digitado.

Restaurar um backup (seção 18.2) é feito em dois tempos: `abrir_conteudo` decifra o arquivo que
veio em bytes sem tocar em nada do disco, e só depois de o servidor conferir que ali dentro há
um diário válido `Cofre.restaurar` guarda uma cópia do arquivo atual e troca de uma vez.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import logging
import os
import re
import secrets
import struct
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import config

MAGICO = b"ENTRENOS1"
SCRYPT_N = 2 ** 16
SCRYPT_R = 8
SCRYPT_P = 1
TAMANHO_SAL = 16
TAMANHO_NONCE = 12
TAMANHO_CHAVE = 32
TAMANHO_TAG = 16
_PARAMETROS = struct.Struct(">III")
TAMANHO_CABECALHO = len(MAGICO) + _PARAMETROS.size + TAMANHO_SAL + TAMANHO_NONCE

# Verificador do PIN da IA: scrypt mais leve que o do cofre (ele não protege o arquivo, só a
# área de ajustes da IA; o diário continua exigindo o PIN principal).
VERIFICADOR_N = 2 ** 15
VERIFICADOR_R = 8
VERIFICADOR_P = 1

# Backups automáticos: um por dia, "entrenos-AAAA-MM-DD.cofre" dentro de backups/.
_RE_BACKUP = re.compile(r"^entrenos-(\d{4}-\d{2}-\d{2})\.cofre$")
# Cópia do diário guardada antes de uma restauração (seção 18.2). Não entra no rodízio de
# BACKUPS_AUTOMATICOS (só o usuário a apaga) e fica cifrada com o PIN que o diário tinha; mas,
# como os backups automáticos, acompanha a troca de PIN e as remoções quando abre com a chave.
PREFIXO_ANTES_DA_RESTAURACAO = "entrenos-antes-da-restauracao-"
_RE_ANTES_DA_RESTAURACAO = re.compile(
    r"^" + re.escape(PREFIXO_ANTES_DA_RESTAURACAO) + r"(\d{4}-\d{2}-\d{2}-\d{6})(?:-(\d+))?\.cofre$")
# Cópias de um diário anterior (seção 18.6): o entrenos.cofre sumiu (antivírus, apagado) e outro
# diário começou aqui, ou a cópia é de outra chave. Ficam como estão: fora do rodízio, de apagar
# e da troca de PIN; só o usuário as apaga.
PREFIXO_DIARIO_ANTERIOR = "entrenos-anterior-"
# Arquivo do diário que estava danificado e foi trocado por um backup (seção 18.6).
PREFIXO_DANIFICADO = "entrenos-danificado-"
_RE_DATA_NO_NOME = re.compile(r"(\d{4}-\d{2}-\d{2})")
_RE_NUMEROS = re.compile(r"\d+")
# Um diário salvo é um banco SQLite serializado: tamanho múltiplo da página (512 bytes ou mais).
PAGINA_SQLITE_MINIMA = 512

_log = logging.getLogger(__name__)


class PinErrado(Exception):
    """O PIN não decifra o arquivo. O GCM não distingue PIN errado de arquivo adulterado."""


class CofreCorrompido(Exception):
    """O arquivo não tem o formato do Entre Nós (cabeçalho estranho ou arquivo cortado)."""


class CofreFechado(RuntimeError):
    """Tentativa de salvar sem a chave (cofre bloqueado ou ainda não aberto)."""


class PastaSumiu(FileNotFoundError):
    """A pasta de dados foi apagada com o diário aberto (o desinstalador com "apagar tudo", ou à
    mão). Salvar a recriaria com o que a pessoa mandou apagar: o cofre se recusa (seção 18.6)."""


class CofreMudou(RuntimeError):
    """O arquivo do diário mudou desde que este cofre o leu ou gravou: outra aba (no navegador,
    outra cópia do app no mesmo armazenamento) gravou nele. Salvar por cima apagaria o que a outra
    gravou: o cofre se recusa (seção 19.7)."""


@dataclass(frozen=True)
class _Cabecalho:
    n: int
    r: int
    p: int
    sal: bytes
    nonce: bytes

    def para_bytes(self) -> bytes:
        return MAGICO + _PARAMETROS.pack(self.n, self.r, self.p) + self.sal + self.nonce


# Quem copiar o arquivo testa PINs fora do app, sem atraso nenhum: os PINs fáceis de
# adivinhar são recusados. Só números precisa de pelo menos DIGITOS_MINIMOS.
DIGITOS_MINIMOS = 10
_PINS_COMUNS = frozenset("""
senha senha1 senha12 senha123 senha1234 password password1 qwerty qwerty1 qwerty123 qwertyuiop
asdfgh asdfghjkl zxcvbn abc123 abcd1234 a1b2c3 1q2w3e 1q2w3e4r iloveyou teamo teamo1 teamo123
amor123 amorzinho meuamor amormeu mudar123 123mudar admin admin123 entrenos entre-nos
princesa princesinha brasil flamengo palmeiras corinthians saopaulo santos gremio vasco
naosei segredo segredo1 trustno1 changeme welcome
""".split())
_SEQUENCIAS = "0123456789 abcdefghijklmnopqrstuvwxyz qwertyuiop asdfghjkl zxcvbnm".split()


def problema_do_pin(pin: object) -> str | None:
    """Por que o PIN não serve (mensagem para a tela), ou None se ele é aceitável."""
    if not isinstance(pin, str) or len(pin) < config.PIN_MINIMO:
        return f"O PIN precisa ter pelo menos {config.PIN_MINIMO} caracteres."
    compacto = re.sub(r"\s+", "", unicodedata.normalize("NFC", pin).lower())
    if not compacto:
        return "O PIN não pode ser só de espaços."
    if len(set(compacto)) <= 2:
        return "O PIN não pode ser só um ou dois caracteres repetidos (como 111111 ou 121212)."
    if any(compacto in s or compacto in s[::-1] for s in _SEQUENCIAS):
        return "O PIN não pode ser uma sequência (como 123456 ou abcdef)."
    if compacto in _PINS_COMUNS:
        return "Esse PIN é muito comum. Escolha outro, de preferência uma frase curta."
    if compacto.isdigit() and len(compacto) < DIGITOS_MINIMOS:
        return (f"Um PIN só de números precisa ter pelo menos {DIGITOS_MINIMOS} dígitos. "
                "Uma frase curta é mais segura e fácil de lembrar, como “girassol na janela”.")
    return None


def _validar_pin(pin: str) -> None:
    problema = problema_do_pin(pin)
    if problema:
        raise ValueError(problema)


def _parametros_validos(n: int, r: int, p: int) -> bool:
    """Só os parâmetros que o próprio app grava (r=8, p=1, n potência de 2 até 2**17).

    Vêm do arquivo, antes de qualquer PIN: um backup preparado com n=2**20 e p=16 custaria
    13 s e 256 MB por tentativa. Com este limite, no máximo 128 MB e menos de meio segundo.
    """
    potencia_de_dois = n > 1 and n & (n - 1) == 0
    return potencia_de_dois and 2 ** 10 <= n <= 2 ** 17 and r == 8 and p == 1


def _derivar(pin: str, sal: bytes, n: int, r: int, p: int) -> bytearray:
    # NFC: o mesmo PIN com acento digitado em teclados diferentes vira os mesmos bytes.
    pin_bytes = unicodedata.normalize("NFC", pin).encode("utf-8")
    return bytearray(Scrypt(salt=sal, length=TAMANHO_CHAVE, n=n, r=r, p=p).derive(pin_bytes))


def _cifrar(chave: bytearray, sal: bytes, parametros: tuple[int, int, int], dados: bytes) -> bytes:
    cabecalho = _Cabecalho(*parametros, sal=sal, nonce=secrets.token_bytes(TAMANHO_NONCE)).para_bytes()
    return cabecalho + AESGCM(chave).encrypt(cabecalho[-TAMANHO_NONCE:], bytes(dados), cabecalho)


def _cabecalho(conteudo: bytes) -> _Cabecalho:
    """Só o cabeçalho (CofreCorrompido se não é um cofre do Entre Nós). `conteudo` pode ser só
    o começo do arquivo, com pelo menos TAMANHO_CABECALHO bytes."""
    if len(conteudo) < TAMANHO_CABECALHO or bytes(conteudo[:len(MAGICO)]) != MAGICO:
        raise CofreCorrompido("O arquivo não é um cofre do Entre Nós ou está incompleto.")
    pos = len(MAGICO)
    n, r, p = _PARAMETROS.unpack_from(conteudo, pos)
    if not _parametros_validos(n, r, p):
        raise CofreCorrompido("O cabeçalho do cofre está danificado.")
    pos += _PARAMETROS.size
    sal = bytes(conteudo[pos:pos + TAMANHO_SAL])
    pos += TAMANHO_SAL
    nonce = bytes(conteudo[pos:pos + TAMANHO_NONCE])
    return _Cabecalho(n, r, p, sal, nonce)


def _separar(conteudo: bytes) -> tuple[_Cabecalho, memoryview]:
    """Cabeçalho e parte cifrada. A parte cifrada é uma vista do próprio `conteudo` (sem cópia:
    um backup de 47 MB não vira dois)."""
    if len(conteudo) < TAMANHO_CABECALHO + TAMANHO_TAG:
        raise CofreCorrompido("O arquivo não é um cofre do Entre Nós ou está incompleto.")
    return _cabecalho(conteudo), memoryview(conteudo)[TAMANHO_CABECALHO:]


def _decifrar(chave: bytearray, cabecalho: _Cabecalho, cifrado: bytes) -> bytes:
    try:
        return AESGCM(chave).decrypt(cabecalho.nonce, cifrado, cabecalho.para_bytes())
    except InvalidTag:
        raise PinErrado("PIN incorreto.") from None


def _b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii")


def _de_b64(texto: object) -> bytes | None:
    if not isinstance(texto, str):
        return None
    try:
        return base64.b64decode(texto.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeError, ValueError):
        return None


def criar_verificador(pin: str) -> dict:
    """Verificador scrypt do PIN da IA: {sal, n, r, p, hash}, sal e hash em base64.

    Guarda só o resultado do scrypt (com sal novo), nunca o PIN. Recusa PIN fácil de
    adivinhar, como o PIN principal (ValueError com a mensagem para a tela).
    """
    _validar_pin(pin)
    sal = secrets.token_bytes(TAMANHO_SAL)
    n, r, p = VERIFICADOR_N, VERIFICADOR_R, VERIFICADOR_P
    chave = _derivar(pin, sal, n, r, p)
    try:
        return {"sal": _b64(sal), "n": n, "r": r, "p": p, "hash": _b64(bytes(chave))}
    finally:
        _zerar(chave)


def verificador_valido(verificador: object) -> bool:
    """O dicionário tem o formato de `criar_verificador` (sem conferir PIN nenhum)?"""
    if not isinstance(verificador, dict) or set(verificador) != {"sal", "n", "r", "p", "hash"}:
        return False
    n, r, p = verificador.get("n"), verificador.get("r"), verificador.get("p")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (n, r, p)):
        return False
    sal, esperado = _de_b64(verificador.get("sal")), _de_b64(verificador.get("hash"))
    return (_parametros_validos(n, r, p) and sal is not None and len(sal) >= 8
            and esperado is not None and len(esperado) == TAMANHO_CHAVE)


def conferir_verificador(pin: object, verificador: object) -> bool:
    """O PIN bate com o verificador? Comparação em tempo constante; formato estranho = False."""
    if not isinstance(pin, str) or not pin or not verificador_valido(verificador):
        return False
    sal, esperado = _de_b64(verificador["sal"]), _de_b64(verificador["hash"])
    chave = _derivar(pin, sal, verificador["n"], verificador["r"], verificador["p"])
    try:
        return hmac.compare_digest(bytes(chave), esperado)
    finally:
        _zerar(chave)


class CofreRecebido:
    """Um cofre que veio em bytes (um backup para restaurar), já decifrado com o PIN dele.

    Nada foi gravado: quem troca o arquivo é `Cofre.restaurar`. A chave fica só em memória;
    `descartar` a apaga (chame sempre, restaurando ou desistindo).
    """

    def __init__(self, dados: bytes, chave: bytearray, sal: bytes, parametros: tuple[int, int, int]):
        self.dados = dados
        self._chave: bytearray | None = chave
        self._sal = sal
        self._parametros = parametros

    def _chave_viva(self) -> bytearray:
        if self._chave is None:
            raise CofreFechado("O backup já foi descartado.")
        return self._chave

    def descartar(self) -> None:
        if self._chave is not None:
            _zerar(self._chave)
        self._chave = None

    def __repr__(self) -> str:          # nunca mostra dados nem chave
        return f"CofreRecebido({len(self.dados)} bytes)"


def tamanho_decifrado(conteudo: bytes) -> int:
    """Quantos bytes o cofre `conteudo` tem depois de decifrado, conferindo só o cabeçalho.

    CofreCorrompido se não é um cofre do Entre Nós (cabeçalho estranho, parâmetros de scrypt
    absurdos, arquivo menor que o cabeçalho). Não precisa do PIN.
    """
    if not isinstance(conteudo, (bytes, bytearray)):
        raise CofreCorrompido("O arquivo não é um cofre do Entre Nós ou está incompleto.")
    _, cifrado = _separar(conteudo)
    return len(cifrado) - TAMANHO_TAG


def abrir_conteudo(conteudo: bytes, pin: str) -> CofreRecebido:
    """Decifra um cofre que veio em bytes (backup para restaurar) sem tocar em arquivo nenhum.

    CofreCorrompido: não é um cofre do Entre Nós (inclusive parâmetros de scrypt absurdos, que
    estourariam a memória). PinErrado: o PIN não abre (o GCM não distingue PIN errado de
    arquivo alterado ou cortado no meio).
    """
    if not isinstance(conteudo, (bytes, bytearray)):
        raise CofreCorrompido("O arquivo não é um cofre do Entre Nós ou está incompleto.")
    cabecalho, cifrado = _separar(conteudo)
    if not isinstance(pin, str) or not pin:
        raise PinErrado("PIN incorreto.")
    parametros = (cabecalho.n, cabecalho.r, cabecalho.p)
    chave = _derivar(pin, cabecalho.sal, *parametros)
    try:
        dados = _decifrar(chave, cabecalho, cifrado)
    except BaseException:
        _zerar(chave)
        raise
    return CofreRecebido(dados, chave, cabecalho.sal, parametros)


# Espera entre as tentativas de `_substituir` (segundos -> nada). Quem roda no computador liga
# `time.sleep` (o servidor.py faz isso ao ser importado): ali o antivírus às vezes segura o arquivo
# por um instante. O cofre não dorme por conta própria porque também roda no navegador (Pyodide,
# seção 19), onde não há como esperar nem antivírus segurando arquivo; lá tenta de novo direto.
esperar_entre_tentativas: Callable[[float], None] | None = None


def _substituir(origem: Path, alvo: Path) -> None:
    """os.replace com algumas tentativas: no Windows o antivírus às vezes segura o arquivo."""
    for tentativa in range(5):
        try:
            os.replace(origem, alvo)
            return
        except PermissionError:
            if tentativa == 4:
                raise
            esperar = esperar_entre_tentativas
            if esperar is not None:
                esperar(0.05 * 2 ** tentativa)


def _gravar_atomico(alvo: Path, conteudo: bytes) -> None:
    """Grava num temporário da mesma pasta e troca de uma vez: nunca fica arquivo pela metade."""
    alvo.parent.mkdir(parents=True, exist_ok=True)
    temporario = alvo.with_name(f"{alvo.name}.tmp-{secrets.token_hex(4)}")
    try:
        with open(temporario, "xb") as f:
            f.write(conteudo)
            f.flush()
            os.fsync(f.fileno())
        _substituir(temporario, alvo)
    except BaseException:
        temporario.unlink(missing_ok=True)
        raise


class Cofre:
    """Arquivo criptografado com os dados do app. Seguro para usar de várias threads."""

    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)
        self.arquivo = self.pasta / config.ARQUIVO_COFRE
        self.pasta_backups = self.pasta / config.PASTA_BACKUPS
        self.hoje = date.today          # dá nome aos backups; os testes trocam por data fixa
        self.agora = datetime.now       # hora no nome da cópia de antes da restauração
        # Quantas cópias automáticas (uma por dia) ficam em backups/. No navegador, menos: tudo o que
        # está na pasta de dados é carregado na memória ao abrir (IDBFS, seção 19.7).
        self.backups_automaticos = config.BACKUPS_AUTOMATICOS
        # Cópias que não entram no rodízio (de antes da restauração, de um diário anterior e
        # danificados): None = nunca apagadas (computador, onde a pessoa vê a pasta); um número = só
        # as N mais novas de cada tipo (navegador, onde não há como apagá-las à mão).
        self.copias_extras_maximas: int | None = None
        self._trava = threading.RLock()
        self._chave: bytearray | None = None
        self._sal: bytes | None = None
        self._parametros: tuple[int, int, int] | None = None
        # (inode, tamanho, data) do arquivo do diário quando este cofre o leu ou gravou pela última
        # vez: se mudou, outra aba gravou nele (CofreMudou). None = nada a comparar.
        self._marca: tuple[int, int, int] | None = None

    def existe(self) -> bool:
        return self.arquivo.is_file()

    @property
    def aberto(self) -> bool:
        return self._chave is not None

    def criar(self, pin: str, dados: bytes) -> None:
        _validar_pin(pin)
        with self._trava:
            if self.existe():
                raise FileExistsError("Já existe um cofre nesta pasta.")
            sal = secrets.token_bytes(TAMANHO_SAL)
            parametros = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
            chave = _derivar(pin, sal, *parametros)
            _gravar_atomico(self.arquivo, _cifrar(chave, sal, parametros, dados))
            self._guardar_chave(chave, sal, parametros)
            self._marca = self._marca_do_arquivo()

    def abrir(self, pin: str) -> bytes:
        if not isinstance(pin, str):
            raise PinErrado("PIN incorreto.")
        with self._trava:
            marca = self._marca_do_arquivo()
            cabecalho, cifrado = _separar(self._ler())
            chave = _derivar(pin, cabecalho.sal, cabecalho.n, cabecalho.r, cabecalho.p)
            dados = _decifrar(chave, cabecalho, cifrado)
            self._guardar_chave(chave, cabecalho.sal, (cabecalho.n, cabecalho.r, cabecalho.p))
            self._marca = marca
            return dados

    def salvar(self, dados: bytes) -> None:
        """Grava `dados` cifrado com a chave guardada. PastaSumiu se a pasta de dados foi apagada
        com o diário aberto: recriá-la traria de volta o que a pessoa mandou apagar."""
        with self._trava:
            if self._chave is None:
                raise CofreFechado("O cofre está bloqueado; desbloqueie antes de salvar.")
            if not self.pasta.is_dir():
                raise PastaSumiu("A pasta de dados do Entre Nós não existe mais.")
            self._conferir_marca()
            novo = _cifrar(self._chave, self._sal, self._parametros, dados)
            if self.existe():
                self._guardar_backup()
            _gravar_atomico(self.arquivo, novo)
            self._marca = self._marca_do_arquivo()

    def trocar_pin(self, atual: str, novo: str) -> None:
        """Re-cifra o arquivo e os backups automáticos com um sal e uma chave novos.

        Os backups também mudam: se continuassem abrindo com o PIN antigo, trocar o PIN
        (porque alguém o viu, por exemplo) não protegeria as cópias.
        """
        _validar_pin(novo)
        if not isinstance(atual, str):
            raise PinErrado("PIN incorreto.")
        with self._trava:
            if self.aberto:
                # Recifrar o que outra aba gravou e seguir salvando daqui apagaria aquilo.
                self._conferir_marca()
            cabecalho, cifrado = _separar(self._ler())
            chave_antiga = _derivar(atual, cabecalho.sal, cabecalho.n, cabecalho.r, cabecalho.p)
            dados = _decifrar(chave_antiga, cabecalho, cifrado)
            sal = secrets.token_bytes(TAMANHO_SAL)
            parametros = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
            chave = _derivar(novo, sal, *parametros)
            _gravar_atomico(self.arquivo, _cifrar(chave, sal, parametros, dados))
            self._marca = self._marca_do_arquivo() if self.aberto else None
            chaves_antigas = {(cabecalho.sal, cabecalho.n, cabecalho.r, cabecalho.p): chave_antiga}
            self._recifrar_backups(atual, chaves_antigas, chave, sal, parametros)
            if self.aberto:
                self._guardar_chave(chave, sal, parametros)
            else:
                _zerar(chave)
            for k in chaves_antigas.values():
                _zerar(k)

    def restaurar(self, recebido: CofreRecebido, dados: bytes | None = None) -> Path | None:
        """Troca o diário pelo backup `recebido` (de `abrir_conteudo`). O PIN passa a ser o dele.

        Quem chama já conferiu que os dados decifrados são um diário válido: aqui nada é
        conferido de novo. Antes de trocar, o arquivo atual (se existe) é copiado para
        backups/entrenos-antes-da-restauracao-AAAA-MM-DD-HHMMSS.cofre, cifrado como estava; se
        a cópia falhar, nada muda. Depois o arquivo é trocado de uma vez (temporário +
        os.replace), cifrado com a chave do backup (nonce novo), e o cofre fica aberto com ela.

        `dados`: o que gravar no lugar (o banco já migrado, por exemplo); None = os dados do backup.
        Devolve o caminho da cópia do diário anterior, ou None se não havia diário.
        """
        chave = recebido._chave_viva()
        with self._trava:
            copia = self._copiar_antes_da_restauracao() if self.existe() else None
            conteudo = recebido.dados if dados is None else bytes(dados)
            _gravar_atomico(self.arquivo, _cifrar(chave, recebido._sal, recebido._parametros, conteudo))
            # Cópia da chave: descartar o `recebido` não pode apagar a chave do cofre aberto.
            self._guardar_chave(bytearray(chave), recebido._sal, recebido._parametros)
            self._marca = self._marca_do_arquivo()
            self._podar_copias_extras()
            return copia

    def confere_pin(self, pin: object) -> bool:
        """O PIN principal bate com a chave do cofre aberto? (não mexe no arquivo)

        Serve para recusar um PIN da IA igual ao principal e para confirmar o "esqueci o PIN
        da IA". Com o cofre fechado não há com o que comparar: CofreFechado.
        """
        if not isinstance(pin, str) or not pin:
            return False
        with self._trava:
            if self._chave is None:
                raise CofreFechado("O cofre está bloqueado; desbloqueie antes.")
            sal, parametros = self._sal, self._parametros
        # O scrypt roda fora da trava: salvar não precisa esperar por ele.
        candidata = _derivar(pin, sal, *parametros)
        try:
            with self._trava:
                if self._chave is None:
                    raise CofreFechado("O cofre está bloqueado; desbloqueie antes.")
                if (self._sal, self._parametros) != (sal, parametros):
                    return self.confere_pin(pin)        # o PIN foi trocado no meio: confere de novo
                return hmac.compare_digest(candidata, self._chave)
        finally:
            _zerar(candidata)

    def fechar(self) -> None:
        with self._trava:
            if self._chave is not None:
                _zerar(self._chave)
            self._chave = None
            self._sal = None
            self._parametros = None
            self._marca = None

    def conteudo(self) -> bytes:
        """Os bytes do arquivo do diário como está no disco (cifrado), para baixar como backup.
        Com o cofre aberto, CofreMudou se outra aba gravou nele (a cópia não seria a desta)."""
        with self._trava:
            if self.aberto:
                self._conferir_marca()
            return self._ler()

    def cifrar_dados(self, dados: bytes) -> bytes:
        """Um .cofre com `dados` (outro banco serializado), cifrado com a chave e o sal do diário
        aberto (abre com o mesmo PIN). Não grava nada: é para as cópias que saem do aparelho sem
        algum segredo (a chave da sincronização, seção 22.2). CofreMudou se outra aba gravou no
        arquivo (a cópia não seria a desta), como `conteudo`."""
        with self._trava:
            if self._chave is None:
                raise CofreFechado("O cofre está bloqueado; desbloqueie antes.")
            self._conferir_marca()
            return _cifrar(self._chave, self._sal, self._parametros, bytes(dados))

    def copiar_backup(self, destino: Path, conteudo: bytes | None = None) -> Path:
        """Copia o arquivo cifrado (ou `conteudo`, um .cofre já pronto) para `destino` como
        entrenos-backup-AAAA-MM-DD.cofre.

        Nunca sobrescreve: se já houver cópia do dia, acrescenta -2, -3...
        """
        destino = Path(destino)
        if not destino.is_dir():
            raise NotADirectoryError("A pasta de destino não existe.")
        if conteudo is None:
            with self._trava:
                conteudo = self._ler()
        base = f"entrenos-backup-{self.hoje():%Y-%m-%d}"
        for numero in range(1, 1000):
            alvo = destino / (base + (f"-{numero}" if numero > 1 else "") + ".cofre")
            try:
                with open(alvo, "xb") as f:
                    f.write(conteudo)
                    f.flush()
                    os.fsync(f.fileno())
                return alvo
            except FileExistsError:
                continue
        raise FileExistsError("Há cópias demais com a data de hoje nesta pasta.")

    def backups(self) -> list[Path]:
        """Backups automáticos, do mais novo para o mais antigo."""
        if not self.pasta_backups.is_dir():
            return []
        achados = [(m.group(1), p) for p in self.pasta_backups.iterdir()
                   if p.is_file() and (m := _RE_BACKUP.match(p.name))]
        return [p for _, p in sorted(achados, reverse=True)]

    def copias_de_restauracao(self) -> list[Path]:
        """Cópias guardadas antes de uma restauração, da mais nova para a mais antiga."""
        if not self.pasta_backups.is_dir():
            return []
        achados = [((m.group(1), int(m.group(2) or 1)), p) for p in self.pasta_backups.iterdir()
                   if p.is_file() and (m := _RE_ANTES_DA_RESTAURACAO.match(p.name))]
        return [p for _, p in sorted(achados, reverse=True)]

    def _copias_cifradas(self) -> list[Path]:
        """O que acompanha remoções e troca de PIN: backups automáticos e cópias de antes da
        restauração (só as que abrem com a chave; as de outro PIN ficam como estão)."""
        return self.backups() + self.copias_de_restauracao()

    def copias_guardadas(self) -> list[Path]:
        """Todas as cópias de um diário em backups/ (automáticas, de antes da restauração, de um
        diário anterior e arquivo danificado guardado), da data mais nova para a mais antiga."""
        if not self.pasta_backups.is_dir():
            return []
        prefixos = ("entrenos-",)
        achados = []
        try:
            for p in self.pasta_backups.iterdir():
                if p.is_file() and p.name.startswith(prefixos) and p.name.endswith(".cofre"):
                    data = _RE_DATA_NO_NOME.search(p.name)
                    achados.append(((data.group(1) if data else "", p.name), p))
        except OSError:
            return []
        return [p for _, p in sorted(achados, reverse=True)]

    def resumo_das_copias(self) -> dict | None:
        """{quantas, mais_recente (AAAA-MM-DD do nome), pasta} das cópias guardadas, ou None.

        Para a tela de primeiro uso avisar que este computador já teve um diário (o arquivo pode
        ter ido para a quarentena do antivírus ou sido apagado) e onde estão as cópias."""
        copias = self.copias_guardadas()
        if not copias:
            return None
        data = _RE_DATA_NO_NOME.search(copias[0].name)
        return {"quantas": len(copias), "mais_recente": data.group(1) if data else None,
                "pasta": str(self.pasta_backups)}

    def separar_copias_anteriores(self) -> int:
        """Primeiro uso com cópias em backups/ (o entrenos.cofre sumiu): as cópias automáticas e
        as de antes da restauração ganham o nome entrenos-anterior-… e saem do rodízio, de
        apagar e da troca de PIN do diário que vai começar. Sem isso, em cinco dias de uso o
        rodízio apagaria as cópias mais recentes do diário que sumiu. Devolve quantas mudaram."""
        with self._trava:
            if self.existe():
                return 0
            mudadas = 0
            for caminho in self._copias_cifradas():
                try:
                    self._por_de_lado(caminho)
                    mudadas += 1
                except OSError as erro:
                    _log.warning("Não consegui separar uma cópia anterior: %s", type(erro).__name__)
            if mudadas:
                self._podar_copias_extras()
            return mudadas

    def danificado(self) -> bool:
        """O arquivo do diário existe mas não tem como abrir com PIN nenhum: cabeçalho estranho
        ou tamanho que não é o de um diário (cortado no meio). Lê só o cabeçalho e o tamanho.

        Arquivo que não dá para ler agora (o antivírus segurando) não conta como danificado.
        Um byte trocado no meio não aparece aqui: o GCM não distingue isso de PIN errado."""
        try:
            tamanho = self.arquivo.stat().st_size
            with open(self.arquivo, "rb") as f:
                comeco = f.read(TAMANHO_CABECALHO)
        except OSError:
            return False
        try:
            _cabecalho(comeco)
        except CofreCorrompido:
            return True
        decifrado = tamanho - TAMANHO_CABECALHO - TAMANHO_TAG
        return decifrado <= 0 or decifrado % PAGINA_SQLITE_MINIMA != 0

    def guardar_danificado(self) -> Path:
        """Move o arquivo do diário (danificado) para backups/entrenos-danificado-AAAA-MM-DD-HHMMSS.cofre.
        Nada é apagado: a pessoa (ou um técnico) ainda pode tentar recuperar. Falha = OSError."""
        with self._trava:
            base = f"{PREFIXO_DANIFICADO}{self.hoje():%Y-%m-%d}-{self.agora():%H%M%S}"
            self.pasta_backups.mkdir(parents=True, exist_ok=True)
            for numero in range(1, 1000):
                alvo = self.pasta_backups / (base + (f"-{numero}" if numero > 1 else "") + ".cofre")
                if alvo.exists():
                    continue
                os.rename(self.arquivo, alvo)
                self._podar_copias_extras()
                return alvo
            raise FileExistsError("Há arquivos danificados demais guardados com esta data e hora.")

    def reescrever_backups(self, transformar: Callable[[bytes], bytes | None]) -> int:
        """Passa cada backup automático (e cada cópia de antes de uma restauração) por
        `transformar` e grava de novo, cifrado.

        Serve para que o que foi apagado suma também das cópias automáticas. `transformar`
        recebe os dados decifrados e devolve os novos (None = não mexer). Só alcança as cópias
        que abrem com a chave atual. Devolve quantas cópias mudaram.
        """
        with self._trava:
            if self._chave is None:
                raise CofreFechado("O cofre está bloqueado; desbloqueie antes de salvar.")
            chave, sal, parametros = self._chave, self._sal, self._parametros
            mudadas = 0
            for caminho in self._copias_cifradas():
                try:
                    cabecalho, cifrado = _separar(caminho.read_bytes())
                    if (cabecalho.sal, (cabecalho.n, cabecalho.r, cabecalho.p)) != (sal, parametros):
                        continue        # cópia de outro PIN: não dá para abrir
                    novo = transformar(_decifrar(chave, cabecalho, cifrado))
                    if novo is None:
                        continue
                    _gravar_atomico(caminho, _cifrar(chave, sal, parametros, novo))
                    mudadas += 1
                except (CofreCorrompido, PinErrado, OSError):
                    continue
            return mudadas

    # ------------------------------------------------------------------ internos

    def _ler(self) -> bytes:
        if not self.existe():
            raise FileNotFoundError("Ainda não existe cofre nesta pasta.")
        return self.arquivo.read_bytes()

    def _marca_do_arquivo(self) -> tuple[int, int, int] | None:
        try:
            info = os.stat(self.arquivo)
        except OSError:
            return None
        return info.st_ino, info.st_size, info.st_mtime_ns

    def _conferir_marca(self) -> None:
        """CofreMudou se o arquivo do diário não é mais o que este cofre leu ou gravou por último.
        Arquivo que sumiu (com a pasta existindo) não conta: é regravado da memória (seção 18.6)."""
        if self._marca is None:
            return
        atual = self._marca_do_arquivo()
        if atual is not None and atual != self._marca:
            raise CofreMudou("O arquivo do diário foi gravado por outra aba ou janela.")

    def _podar_copias_extras(self) -> None:
        """No navegador (`copias_extras_maximas`): das cópias fora do rodízio, só as N mais novas de
        cada tipo ficam. No computador (None) nada sai: a pessoa vê a pasta e decide."""
        maximo = self.copias_extras_maximas
        if maximo is None or not self.pasta_backups.is_dir():
            return
        grupos: dict[str, list[tuple[tuple[tuple[int, ...], str], Path]]] = {}
        prefixos = (PREFIXO_ANTES_DA_RESTAURACAO, PREFIXO_DIARIO_ANTERIOR, PREFIXO_DANIFICADO)
        try:
            for p in self.pasta_backups.iterdir():
                prefixo = next((x for x in prefixos if p.name.startswith(x)), None)
                if prefixo is None or not p.name.endswith(".cofre") or not p.is_file():
                    continue
                # Data, hora e o -2, -3 de quem caiu no mesmo segundo, como números: o mais novo é o maior.
                numeros = tuple(int(n) for n in _RE_NUMEROS.findall(p.name[len(prefixo):]))
                grupos.setdefault(prefixo, []).append(((numeros, p.name), p))
        except OSError:
            return
        for copias in grupos.values():
            for _, caminho in sorted(copias, reverse=True)[max(0, maximo):]:
                try:
                    caminho.unlink()
                except OSError as erro:
                    _log.warning("Não consegui apagar uma cópia antiga: %s", type(erro).__name__)

    def _guardar_chave(self, chave: bytearray, sal: bytes, parametros: tuple[int, int, int]) -> None:
        if self._chave is not None and self._chave is not chave:
            _zerar(self._chave)
        self._chave, self._sal, self._parametros = chave, sal, parametros

    def _copiar_antes_da_restauracao(self) -> Path:
        """Cópia do arquivo atual em backups/ antes de uma restauração. Nunca sobrescreve:
        duas no mesmo segundo ganham -2, -3... Falha aqui (OSError) impede a restauração."""
        conteudo = self.arquivo.read_bytes()
        base = f"{PREFIXO_ANTES_DA_RESTAURACAO}{self.hoje():%Y-%m-%d}-{self.agora():%H%M%S}"
        for numero in range(1, 1000):
            alvo = self.pasta_backups / (base + (f"-{numero}" if numero > 1 else "") + ".cofre")
            if alvo.exists():
                continue
            _gravar_atomico(alvo, conteudo)
            return alvo
        raise FileExistsError("Há cópias demais de antes da restauração com esta data e hora.")

    def _guardar_backup(self) -> None:
        """Guarda a versão que está no disco, uma vez por dia. Falha aqui não impede salvar.

        O rodízio (BACKUPS_AUTOMATICOS) só apaga cópias da chave de agora. Cópia automática de
        outra chave (de um diário anterior, ou de antes de restaurar um backup de outro PIN) nunca
        é apagada: ganha o nome entrenos-anterior-… e sai do rodízio (seção 18.6)."""
        try:
            chave_atual = (self._sal, self._parametros)
            for copia in self.backups():
                if self._chave_do_arquivo(copia) != chave_atual:
                    try:
                        self._por_de_lado(copia)
                    except OSError as erro:      # fica onde está, e o rodízio também não a apaga
                        _log.warning("Não consegui separar uma cópia anterior: %s", type(erro).__name__)
            alvo = self.pasta_backups / f"entrenos-{self.hoje():%Y-%m-%d}.cofre"
            if not alvo.exists():
                _gravar_atomico(alvo, self.arquivo.read_bytes())
            for antigo in self.backups()[max(1, self.backups_automaticos):]:
                if self._chave_do_arquivo(antigo) == chave_atual:
                    antigo.unlink(missing_ok=True)
        except OSError as erro:
            _log.warning("Backup automático falhou: %s", type(erro).__name__)
        self._podar_copias_extras()

    @staticmethod
    def _chave_do_arquivo(caminho: Path) -> tuple[bytes, tuple[int, int, int]] | None:
        """(sal, (n, r, p)) do cabeçalho de uma cópia, ou None se não dá para ler ou não é cofre."""
        try:
            with open(caminho, "rb") as f:
                cabecalho = _cabecalho(f.read(TAMANHO_CABECALHO))
        except (OSError, CofreCorrompido):
            return None
        return cabecalho.sal, (cabecalho.n, cabecalho.r, cabecalho.p)

    def _por_de_lado(self, caminho: Path) -> Path:
        """entrenos-X.cofre -> entrenos-anterior-X.cofre na mesma pasta (nunca sobrescreve)."""
        resto = caminho.name[len("entrenos-"):-len(".cofre")]
        for numero in range(1, 1000):
            alvo = caminho.with_name(f"{PREFIXO_DIARIO_ANTERIOR}{resto}{f'-{numero}' if numero > 1 else ''}.cofre")
            if alvo.exists():
                continue
            os.rename(caminho, alvo)
            return alvo
        raise FileExistsError("Há cópias anteriores demais com este nome.")

    def _recifrar_backups(self, pin_antigo: str, chaves: dict, chave: bytearray, sal: bytes,
                          parametros: tuple[int, int, int]) -> None:
        for caminho in self._copias_cifradas():
            try:
                cabecalho, cifrado = _separar(caminho.read_bytes())
                id_chave = (cabecalho.sal, cabecalho.n, cabecalho.r, cabecalho.p)
                if id_chave not in chaves:
                    chaves[id_chave] = _derivar(pin_antigo, cabecalho.sal, cabecalho.n,
                                                cabecalho.r, cabecalho.p)
                dados = _decifrar(chaves[id_chave], cabecalho, cifrado)
                _gravar_atomico(caminho, _cifrar(chave, sal, parametros, dados))
            except (CofreCorrompido, PinErrado, OSError):
                # Cópia de outro PIN ou danificada: fica como está.
                continue


def _zerar(chave: bytearray) -> None:
    for i in range(len(chave)):
        chave[i] = 0


__all__ = ["Cofre", "CofreCorrompido", "CofreFechado", "CofreMudou", "CofreRecebido", "PREFIXO_ANTES_DA_RESTAURACAO",
           "PREFIXO_DANIFICADO", "PREFIXO_DIARIO_ANTERIOR", "PastaSumiu", "PinErrado", "abrir_conteudo",
           "conferir_verificador", "criar_verificador", "problema_do_pin", "tamanho_decifrado",
           "verificador_valido"]
