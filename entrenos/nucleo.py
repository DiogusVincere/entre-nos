"""Núcleo do Entre Nós (DESIGN.md, seção 19): o app inteiro, sem saber por onde o pedido chegou.

`Nucleo.tratar(metodo, caminho, corpo=..., token=..., consulta=...)` é a entrada única: acha a
rota, confere o acesso (pública, sessão, livre, primeiro uso), o limite do corpo, lê o JSON, roda
a rota, traduz os erros e devolve uma `RespostaApi` (status, dados, cabeçalhos, corpo em JSON).
Quem leva o pedido até aqui é o transporte:

- no computador, `servidor.py` (HTTP em 127.0.0.1, arquivos da página, CSP, a thread vigia);
- no navegador (iPhone e Android), `ponte.py`, chamada pelo Pyodide dentro de um Web Worker.

Por isso o núcleo não pode depender do que o navegador não tem: nada de iniciar threads (só
travas), `time.sleep`, servidor HTTP, sockets, ctypes/Windows nem abrir o navegador. Os relógios
(`relogio`, segundos monotônicos; `relogio_parede`, time.time) e a data (`hoje`) são injetáveis,
e `plataforma` ("computador" ou "navegador", seção 19.7) liga ou desliga o que só existe num deles:
backup numa pasta, IA pela rede, o limite dos arquivos enviados e quantas cópias ficam guardadas.

Segurança que mora aqui (a do pedido HTTP fica no servidor.py):
- toda rota /api/* exceto ping, estado, configurar e desbloquear exige o token da sessão
  (restaurar também dispensa, mas só enquanto não existe diário);
- corpo até 1 MB (64 MB em restaurar e importar), sempre um objeto JSON;
- PIN errado: bloqueio temporário, sem esperar dentro do pedido. Depois de cada erro, nenhum PIN
  é conferido por 1 s, 2 s, 4 s... até 30 s; quem tenta antes recebe 429 com
  `{"erro", "espere_segundos"}` e o cabeçalho `Retry-After`, sem gastar scrypt nem tentativa. O
  bloqueio vai para `pin_espera.json` antes da resposta: fechar e abrir não o zera;
- sem uso por `minutos_bloqueio` (pelo relógio que tiver andado mais: o monotônico do navegador
  para enquanto o celular dorme), fecha o cofre e descarta banco e token, salvando só o que
  faltava. A inatividade é conferida no começo de cada pedido (preguiçoso) e por
  `verificar_inatividade()`, que o transporte pode chamar de tempos em tempos;
- outra aba gravou no diário depois que este núcleo o leu: 409, bloqueia sem gravar por cima.

Nada de conteúdo do usuário vai para log: `erros.log` guarda só o tipo do erro e onde ele
aconteceu no código. Os módulos motor, provedores, contexto, busca, alertas e perfil são
importados na hora do uso: se um deles faltar, só as rotas que dependem dele respondem 503.

IA sob demanda (seção 17): `/api/analisar` e `/api/busca` usam só as regras. A IA roda apenas
pelas rotas `/api/ia/analisar`, `/api/ia/busca` e `/api/ia/sugestao` (os botões ✨), e nada de
registro marcado "não enviar para a IA" segue para ela. A área de ajustes da IA pede um segundo
PIN (o PIN da IA), com bloqueio temporário e contador próprios; fica liberada por
`minutos_area_ia` e fecha no bloqueio e em `/api/ia/sair`. A chave de uma IA nunca volta para a
tela (só os 4 últimos caracteres) e nunca segue para outro endereço sem ser digitada de novo.

Restaurar backup e importar JSON (seção 18): `/api/restaurar` confere o arquivo inteiro antes de
trocar qualquer coisa, guarda uma cópia do diário atual e abre uma sessão nova com o PIN do
backup; `/api/importar/previa` e `/api/importar` juntam o JSON de `/api/exportar` sem duplicar.
Arquivos que somem (seção 18.6): diário danificado não gasta tentativa de PIN e pode ser trocado
por um backup; pasta de dados apagada com o diário aberto não é recriada.

Recursos (seção 20.0): as rotas de cada recurso entram em `/api/<NOME>/...` (sessão ou livre, nunca
públicas); `POST /api/registros` aceita os `extras` confirmados na revisão; e o que os recursos
mandam deixar fora da IA (`fora_da_ia`) se soma aos registros 🔒 em todos os botões ✨.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import hmac
import importlib
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import config, recursos
from .banco import (DADOS_DE_VERSAO_NOVA, Banco, NaoEncontrado, chave_da_sincronizacao, ler_exportacao,
                    validar_exportacao)
from .cofre import (Cofre, CofreCorrompido, CofreFechado, CofreMudou, CofreRecebido, PastaSumiu, PinErrado,
                    abrir_conteudo, conferir_verificador, criar_verificador, problema_do_pin, tamanho_decifrado)
from .modelos import Certeza, Item, Resposta, TipoItem, para_json

LIMITE_CORPO = 1024 * 1024
LIMITE_CORPO_ARQUIVO = 64 * 1024 * 1024   # só restaurar e importar (o arquivo vem no corpo)
MAX_PARAMETROS = 50                       # parâmetros na consulta (?q=...&dias=...)
# Onde o núcleo roda (seção 19.7). No navegador (Pyodide, num Web Worker do celular) a memória é
# curta e não volta (o wasm nunca encolhe), não há pasta para escolher, a IA ainda não alcança a
# rede e o armazenamento inteiro do app fica carregado na memória (IDBFS): menos cópias, arquivos
# menores e textos que não falam de Windows.
COMPUTADOR = "computador"
NAVEGADOR = "navegador"
PLATAFORMAS = (COMPUTADOR, NAVEGADOR)
LIMITE_CORPO_ARQUIVO_NAVEGADOR = 16 * 1024 * 1024
BACKUPS_AUTOMATICOS_NAVEGADOR = 2         # o backup de verdade, no celular, é a cópia baixada (e o Drive)
COPIAS_EXTRAS_NAVEGADOR = 2               # de antes da restauração, anteriores e danificados: as 2 mais novas de cada
# Bloqueio do PIN errado guardado fora da memória (seção 19.7): fechar e abrir o app (ou recarregar a
# página) não zera a espera. Sem segredo nenhum: só contadores e horários.
ARQUIVO_ESPERA_PIN = "pin_espera.json"
TOLERANCIA_RELOGIO = 5.0                  # segundos que o relógio do sistema pode voltar sozinho (NTP)
PAGINA_SQLITE_MINIMA = 512           # um banco SQLite serializado tem tamanho múltiplo disto
ATRASO_PIN = 1.0                     # segundos de bloqueio depois do 1º PIN errado; dobra a cada erro
ATRASO_PIN_MAXIMO = 30.0
MAX_PERGUNTA = 500
MINUTOS_AREA_IA = 10                 # a área de ajustes da IA fecha sozinha depois disso
COMPARACOES_SEM_ESPERA = 3           # "PIN da IA igual ao principal?" sem bloqueio (depois, como PIN errado)
QUAIS_IA = ("principal", "reserva")
DIAS_CONTEXTO_SUGESTAO = 30          # registros recentes que entram no pedido de sugestão
MAX_CONTEXTO_SUGESTAO = 40

BLOQUEADO = "O Entre Nós está bloqueado. Digite o PIN para continuar."
SESSAO_INVALIDA = "Sua sessão não vale mais. Digite o PIN de novo."
FALHA_INTERNA = "Algo deu errado aqui dentro. Tente de novo; se continuar, feche e abra o Entre Nós."
INDISPONIVEL = "Esta parte do Entre Nós ainda não está pronta. Tente de novo mais tarde."
DADO_INVALIDO = "Algum dado enviado não é válido."
FECHANDO = "O Entre Nós está sendo fechado."
ESPERE_O_PIN = "Espere {segundos} s antes de tentar o PIN de novo."
AREA_IA_FECHADA = "Esta área pede o PIN da IA."
SEM_IA = "Nenhuma IA configurada. Configure uma em Ajustes → IA."
IA_INCOMPLETA = "A IA configurada não pode ser usada assim."
REGISTRO_PRIVADO = "Este registro está marcado para não ir para a IA."
CHAVE_DE_NOVO = "Informe a chave de novo para o novo endereço."
AJUSTES_DA_IA = "Os ajustes da IA ficam em Ajustes → IA, protegidos pelo PIN da IA."
PRIVADOS_DE_FORA = "Registros marcados “não enviar para a IA” ficaram de fora do que foi para a IA."
# Seção 20.0: o que os recursos mandam deixar fora da IA.
FORA_DA_IA_INDISPONIVEL = ("Uma parte do Entre Nós não abriu direito, e não dá para saber com segurança o que "
                           "fica fora da IA. Por isso nada foi enviado para a IA.")
TEXTO_FICA_FORA_DA_IA = ("Este texto tem informações que nunca vão para a IA. As regras organizaram tudo aqui "
                         "mesmo.")
NAO_ENCONTREI = "Não encontrei essa informação."
JA_CONFIGURADO = "Este computador já tem um Entre Nós configurado. Use o seu PIN."
NAO_E_BACKUP = "Este arquivo não é um backup do Entre Nós (.cofre) ou está incompleto."
BACKUP_DANIFICADO = "O backup abriu, mas o diário dentro dele está danificado ou não é do Entre Nós."
PIN_DO_BACKUP = "Esse PIN não abre este backup."
# Seção 18.6: arquivo do diário sumido, danificado ou pasta de dados apagada com o app aberto.
DIARIO_DANIFICADO = ("O arquivo do diário (entrenos.cofre) está danificado: parece cortado no meio, e nenhum "
                     "PIN consegue abri-lo. Seu PIN não foi testado. Restaure um backup; o arquivo danificado "
                     "fica guardado na pasta backups.")
PASTA_SUMIU = ("A pasta de dados do Entre Nós foi apagada enquanto ele estava aberto (por exemplo, ao "
               "desinstalar escolhendo apagar o diário). Para não recriar o que foi apagado, o Entre Nós "
               "se bloqueou sem salvar.")
NAO_SALVOU_ANTES = ("Não consegui gravar o diário de agora antes da troca, então nada foi trocado. Tente de novo; "
                    "se continuar, feche e abra o Entre Nós.")
# Duas abas (ou janelas) com o mesmo diário: a outra gravou depois que esta abriu (seção 19.7).
DIARIO_MUDOU = ("O diário foi alterado em outro lugar (outra aba ou janela do Entre Nós) enquanto estava aberto "
                "aqui. Para não apagar o que foi gravado lá, o que você fez agora não foi salvo e o Entre Nós se "
                "bloqueou. Feche as outras abas ou janelas do Entre Nós e digite o PIN de novo.")
# No navegador (seção 19.7).
PASTA_SUMIU_NAVEGADOR = ("Os dados do Entre Nós foram apagados deste navegador enquanto ele estava aberto. Para não "
                         "recriar o que foi apagado, o Entre Nós se bloqueou sem salvar.")
IA_SO_NO_COMPUTADOR = ("A IA ainda não funciona no celular: use o Entre Nós do computador para os botões ✨. Os "
                       "ajustes da IA continuam guardados.")
BACKUP_SO_BAIXANDO = ("No celular não dá para escolher uma pasta: use “Baixar uma cópia” e guarde o arquivo onde "
                      "quiser (fora deste aparelho também).")
# Sincronização (seção 22).
AJUSTES_DA_SINCRONIZACAO = "Os ajustes da sincronização ficam em Ajustes → ☁️ Sincronização."
ARQUIVO_SYNC_VAZIO = "O arquivo de sincronização não chegou (veio vazio ou fora do formato esperado)."
MAX_ARQUIVO_SYNC_CELULAR = 11 * 1024 * 1024     # em base64 ainda cabe nos 16 MB do celular
ARQUIVO_SYNC_GRANDE = ("O arquivo de sincronização deste aparelho ficou grande ({mb} MB): um celular pode não "
                       "conseguir abri-lo. Os outros computadores abrem normalmente.")

_log = logging.getLogger("entrenos")
_log.addHandler(logging.NullHandler())
_log_nucleo = logging.getLogger("entrenos.nucleo")

# Mensagens de ValueError que vêm do Python (em inglês) e não servem para a tela.
_RE_MENSAGEM_INGLES = re.compile(
    r"^[a-z'\"(]|^Invalid\b|^Expecting\b| is not a valid |\bmust be\b|out of range|could not|codec can't")


# ---------------------------------------------------------------- erros e log

class ErroHttp(Exception):
    """Erro com status (no estilo HTTP) e mensagem em português para a tela.

    `cabecalhos` vão na resposta (ex.: Allow, Retry-After); `extras` vão no JSON junto com
    "erro" (ex.: {"pin_ia": true}, {"espere_segundos": 4}).
    """

    def __init__(self, status: int, mensagem: str, cabecalhos: dict[str, str] | None = None,
                 extras: dict[str, Any] | None = None):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem
        self.cabecalhos = cabecalhos or {}
        self.extras = extras or {}


def registrar_erro(erro: BaseException) -> None:
    """Grava no log só o tipo do erro e os pontos do código (arquivo:linha em função).

    A mensagem da exceção fica de fora: ela pode trazer texto, nome ou PIN do usuário.
    """
    try:
        linhas: list[str] = []
        atual: BaseException | None = erro
        vistos: set[int] = set()
        while atual is not None and id(atual) not in vistos:
            vistos.add(id(atual))
            linhas.append(type(atual).__qualname__ if atual is erro else f"causado por {type(atual).__qualname__}")
            for quadro in traceback.extract_tb(atual.__traceback__):
                linhas.append(f"  {os.path.basename(quadro.filename)}:{quadro.lineno} em {quadro.name}")
            atual = atual.__cause__ or atual.__context__
        _log_nucleo.error("\n".join(linhas))
    except Exception:  # noqa: BLE001 - o log nunca derruba o app
        pass


def configurar_log(pasta: Path | str) -> logging.Handler:
    """erros.log na pasta de dados (só criado quando houver erro). Devolve o manipulador, para
    quem abriu poder tirá-lo (`logging.getLogger("entrenos").removeHandler`) e fechá-lo."""
    import logging.handlers     # na hora: o núcleo não precisa dele para importar

    manipulador = logging.handlers.RotatingFileHandler(
        Path(pasta) / "erros.log", maxBytes=256 * 1024, backupCount=1, encoding="utf-8", delay=True)
    manipulador.setLevel(logging.WARNING)
    manipulador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _log.addHandler(manipulador)
    if _log.level == logging.NOTSET or _log.level > logging.WARNING:
        _log.setLevel(logging.WARNING)
    return manipulador


def _mensagem_de_dado(erro: BaseException) -> str:
    mensagem = str(erro) if isinstance(erro, ValueError) and not isinstance(erro, UnicodeError) else ""
    if not mensagem or _RE_MENSAGEM_INGLES.search(mensagem):
        return DADO_INVALIDO
    return mensagem


def traduzir_erro(erro: BaseException) -> ErroHttp:
    """Exceção -> status + mensagem em português."""
    if isinstance(erro, ErroHttp):
        return erro
    if isinstance(erro, NaoEncontrado):
        return ErroHttp(404, str(erro))
    if isinstance(erro, PinErrado):
        return ErroHttp(401, "PIN incorreto.")
    if isinstance(erro, CofreFechado):
        return ErroHttp(423, BLOQUEADO)
    if isinstance(erro, CofreMudou):
        return ErroHttp(409, DIARIO_MUDOU)
    if isinstance(erro, CofreCorrompido):
        registrar_erro(erro)
        return ErroHttp(500, f"{erro} Use uma cópia de segurança.")
    if isinstance(erro, ValueError):
        return ErroHttp(400, _mensagem_de_dado(erro))
    if isinstance(erro, (TypeError, KeyError)):
        registrar_erro(erro)     # pode ser dado estranho ou defeito: fica anotado, sem conteúdo
        return ErroHttp(400, DADO_INVALIDO)
    registrar_erro(erro)
    return ErroHttp(500, FALHA_INTERNA)


def _variacoes_do_pin(pin: str) -> list[str]:
    """O PIN e as variações óbvias dele (para o PIN da IA não ser "o principal em maiúsculas").

    Cada uma custa um scrypt; são no máximo 7. Tirar os espaços não dá para desfazer (onde
    eles estavam?): a tela pede que o PIN da IA não seja uma variação do principal.
    """
    base = _nfc(pin) if isinstance(pin, str) else ""
    limpo = re.sub(r"\s+", " ", base).strip()            # sem espaço nas pontas nem repetido
    formas = [base, base.strip(), limpo, limpo.lower(), limpo.upper(), limpo.capitalize(), limpo.title()]
    return [f for f in dict.fromkeys(formas) if f]


def atraso_do_pin(falhas: int, base: float = ATRASO_PIN, maximo: float = ATRASO_PIN_MAXIMO) -> float:
    """Bloqueio depois da n-ésima tentativa errada seguida: base, 2×base, 4×base... até `maximo`."""
    if falhas <= 0:
        return 0.0
    return min(maximo, base * 2 ** min(falhas - 1, 30))


def segundos_de_espera(segundos: float) -> int:
    """Segundos inteiros para a tela e para o Retry-After: arredonda para cima, no mínimo 1."""
    return max(1, math.ceil(round(segundos, 3)))


class _Espera:
    """Bloqueio temporário de um PIN (seção 19). Depois de um erro, nenhum PIN é conferido até
    `livre_em` (no relógio do núcleo); quem tenta antes recebe 429 com quanto falta, sem gastar
    scrypt nem tentativa. Não espera nada dentro do pedido: no navegador não há como dormir."""

    def __init__(self, relogio: Callable[[], float]):
        self._relogio = relogio
        self.livre_em: float | None = None

    def restante(self) -> float:
        if self.livre_em is None:
            return 0.0
        return max(0.0, self.livre_em - self._relogio())

    def conferir(self) -> None:
        """429 se ainda não é hora de conferir outro PIN. Chame com a trava do PIN."""
        restante = self.restante()
        if restante > 0:
            segundos = segundos_de_espera(restante)
            raise ErroHttp(429, ESPERE_O_PIN.format(segundos=segundos), {"Retry-After": str(segundos)},
                           extras={"espere_segundos": segundos})

    def adiar(self, segundos: float) -> None:
        """Nenhum PIN até daqui a `segundos` (nunca encurta um bloqueio que já estava valendo)."""
        if segundos > 0:
            fim = self._relogio() + segundos
            self.livre_em = fim if self.livre_em is None else max(self.livre_em, fim)

    def liberar(self) -> None:
        self.livre_em = None


def _numero_finito(valor: Any) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        return None
    return float(valor)


# ---------------------------------------------------------------- módulos que podem faltar

def _importar(nome: str):
    return importlib.import_module(f"{__package__}.{nome}")


def _funcao(modulo: str, nome: str) -> Callable:
    """Função de um módulo que pode ainda não existir. Faltando: 503 só nesta rota."""
    try:
        return getattr(_importar(modulo), nome)
    except Exception as erro:  # noqa: BLE001 - ImportError, SyntaxError, AttributeError...
        registrar_erro(erro)
        raise ErroHttp(503, INDISPONIVEL) from None


def _modulo(nome: str, *precisa: str) -> Any:
    """Módulo que pode ainda não existir (ou existir sem os nomes em `precisa`): 503 nesta rota."""
    try:
        modulo = _importar(nome)
        for atributo in precisa:
            getattr(modulo, atributo)
        return modulo
    except Exception as erro:  # noqa: BLE001
        registrar_erro(erro)
        raise ErroHttp(503, INDISPONIVEL) from None


# ---------------------------------------------------------------- IA: o que a tela pode saber

def _chave_da_ia(chave: str) -> bool:
    """Ajuste que só a área da IA mexe: ia_* e os antigos motor*."""
    return chave.startswith(("ia_", "motor"))


def _config_publica(config_atual: dict) -> dict:
    """Ajustes para a tela e para a exportação: nada da IA (nem chave, nem PIN, nem qual IA) e nada
    da sincronização (a chave dela abre os arquivos do Google Drive; seção 22)."""
    return {k: v for k, v in config_atual.items() if not _chave_da_ia(k) and not chave_da_sincronizacao(k)}


def _ias_configuradas(ajustes: dict) -> list[dict]:
    return [ajustes[f"ia_{q}"] for q in QUAIS_IA if isinstance(ajustes.get(f"ia_{q}"), dict)]


def _ia_sai_do_computador(ajustes: dict) -> bool:
    """Alguma IA configurada manda texto para fora deste computador? Sem saber, diz que sim."""
    ias = _ias_configuradas(ajustes)
    if not ias:
        return False
    try:
        envia = _importar("motor").ia_envia_para_fora
        return any(bool(envia(ia)) for ia in ias)
    except Exception as erro:  # noqa: BLE001 - na dúvida, o aviso aparece
        registrar_erro(erro)
        return True


def _problemas_ia(ajustes: dict) -> dict[str, str]:
    """{qual: por que a IA salva não dá para usar} (motor migrado sem chave, http:// para a internet...)."""
    if not _ias_configuradas(ajustes):
        return {}
    try:
        return dict(_importar("motor").problemas_da_config_ia(ajustes))
    except Exception as erro:  # noqa: BLE001 - sem o motor, os botões ✨ respondem 503
        registrar_erro(erro)
        return {}


def _info_ia(ajustes: dict, disponivel: bool = True) -> dict:
    """O que qualquer tela aberta com o PIN principal pode saber da IA (sem qual, URL ou modelo).

    `utilizavel`: alguma IA configurada dá para usar (senão os botões ✨ não aparecem).
    `espera_maxima`: segundos que um ✨ pode levar no pior caso (tempo limite × IAs utilizáveis).
    `disponivel` False: o transporte não alcança a IA (o celular, seção 19.7): nada é utilizável, e
    `motivo` (só nesse caso, com IA configurada) diz por quê.
    """
    configuradas = [q for q in QUAIS_IA if isinstance(ajustes.get(f"ia_{q}"), dict)]
    problemas = _problemas_ia(ajustes) if disponivel else {}
    utilizaveis = [q for q in configuradas if q not in problemas] if disponivel else []
    tempo = ajustes.get("ia_tempo_limite")
    tempo = tempo if isinstance(tempo, (int, float)) and not isinstance(tempo, bool) and tempo > 0 else 120
    info = {"configurada": bool(configuradas), "utilizavel": bool(utilizaveis),
            "sai_do_computador": _ia_sai_do_computador(ajustes),
            "espera_maxima": int(round(tempo * len(utilizaveis))) if utilizaveis else None}
    if not disponivel and configuradas:
        info["motivo"] = IA_SO_NO_COMPUTADOR
    return info


def _ia_publica(ia: Any, prov: Any) -> dict | None:
    """Uma IA configurada para a área do PIN da IA: a chave nunca, só os 4 últimos caracteres."""
    if not isinstance(ia, dict):
        return None
    servico = prov.provedor(ia.get("provedor"))
    chave = ia.get("chave") or ""
    url, modelo = ia.get("url") or "", ia.get("modelo") or ""
    publica = {"provedor": ia.get("provedor"), "nome_provedor": servico.nome if servico else ia.get("provedor"),
               "url": url, "modelo": modelo, "tipo": ia.get("tipo"), "tem_chave": bool(chave),
               "chave_final": chave[-4:] if len(chave) >= 8 else None,       # chave curta: nem o final
               "local": bool(prov.e_local(url, modelo)), "aviso": servico.aviso if servico else None}
    if ia.get("url_exata") is True:
        publica["url_exata"] = True          # "Outro" com o endereço como foi digitado (sem /v1)
    return publica


def _motor_neutro(ia_usada: str | None) -> str:
    """O `motor` das respostas ✨ sem serviço nem modelo ("ia:principal"): qual IA está
    escolhida é da área do PIN da IA (17.8), e o ✨ funciona só com o PIN principal."""
    return f"ia:{ia_usada}" if ia_usada in QUAIS_IA else "regras"


def _proxima_data(d: date, referencia: date, folga: int) -> date:
    """Próxima ocorrência de uma data que se repete todo ano (29/02 cai em 28/02)."""
    candidata = d
    for ano in (referencia.year - 1, referencia.year, referencia.year + 1):
        try:
            candidata = date(ano, d.month, d.day)
        except ValueError:
            candidata = date(ano, 2, 28)
        if candidata >= referencia - timedelta(days=folga):
            return candidata
    return candidata


def fora_da_ia(banco: Banco) -> set[int]:
    """Registros que nunca vão para a IA: os marcados 🔒 e os que os recursos mandam deixar de
    fora (o ciclo, a área Eu; seção 20.0). Se não dá para saber com segurança (um recurso não abriu
    ou falhou ao dizer), 503 e nada vai: na dúvida, nada sai daqui."""
    fora = set(banco.registros_privados())
    try:
        fora |= recursos.fora_da_ia(banco)
    except recursos.RecursoIndisponivel:
        raise ErroHttp(503, FORA_DA_IA_INDISPONIVEL) from None
    return fora


# O texto de um registro não muda: a resposta de `texto_fica_fora_da_ia` fica guardada (só sim/não,
# pela chave do resumo do texto) e é esquecida ao bloquear ou quando passa do tamanho.
_MEMO_TEXTO_FORA: dict[str, bool] = {}
_MAX_MEMO_TEXTO_FORA = 4096
_AVISO_EXTRAIR_INICIO = recursos.AVISO_EXTRAIR.split("{", 1)[0]


def texto_fica_fora_da_ia(texto: str, referencia: date, banco: Banco | None = None, *,
                          na_duvida: bool = True) -> bool:
    """O texto tem algo que um recurso deixa fora da IA, confirmado ou não na revisão (seção 20.10):
    um extra `_privado` dos tipos de `TIPOS_FORA_PELO_TEXTO` (`recursos.fica_fora_pelo_texto`).
    Desentendimento da área Eu, resposta de "Nós dois", o ciclo... ficam fora mesmo com "Não guardar"
    ou sem decidir; uma atitude sua não confirmada, não (o ✨ Analisar recusa todos, com 403).


    Com `na_duvida` (o padrão, para o que vai à IA): um recurso que não abriu ou não conseguiu ler o
    texto conta como sim. Sem ele (ao gravar), só o que foi achado de fato."""
    if not isinstance(texto, str) or not texto.strip():
        return False
    if recursos.falhas():
        return na_duvida
    chave = hashlib.sha256(texto.encode("utf-8", "surrogatepass")).hexdigest()
    guardado = _MEMO_TEXTO_FORA.get(chave)
    if guardado is not None:
        return guardado
    motor = _modulo("motor", "Conhecimento", "MotorRegras", "conhecimento_do_banco")
    # Só pessoas e o nome dela: sem a leitura do diário, os avisos que leem o diário (o aprendizado, o
    # planejamento do ciclo) ficam de fora, e eles nunca pedem 🔒.
    conhecimento = motor.Conhecimento()
    if banco is not None:
        completo = motor.conhecimento_do_banco(banco)
        conhecimento = motor.Conhecimento(pessoas=completo.pessoas, nome_dela=completo.nome_dela)
    proposta = motor.MotorRegras().analisar(texto, referencia, conhecimento)
    achou = recursos.fica_fora_pelo_texto(proposta.extras or {})
    falhou = any(isinstance(a, str) and a.startswith(_AVISO_EXTRAIR_INICIO) for a in proposta.avisos or [])
    if falhou and not achou:
        return na_duvida                     # um recurso não leu o texto: não guarda a resposta
    if len(_MEMO_TEXTO_FORA) >= _MAX_MEMO_TEXTO_FORA:
        _MEMO_TEXTO_FORA.clear()
    _MEMO_TEXTO_FORA[chave] = achou
    return achou


def esquecer_textos_fora_da_ia() -> None:
    _MEMO_TEXTO_FORA.clear()


def fora_da_ia_pelo_texto(banco: Banco, ids: Any, ja_fora: set[int] | None = None) -> set[int]:
    """Dos registros `ids`, os que ficam fora da IA pelo próprio texto (`texto_fica_fora_da_ia`), além
    dos que já estão em `ja_fora`. Para o que está para ir à IA (achados da busca, contexto da
    sugestão, o detalhe de um registro): ler o diário inteiro a cada pedido custaria demais."""
    ja_fora = ja_fora or set()
    procurar = sorted({i for i in ids if isinstance(i, int) and not isinstance(i, bool)} - ja_fora)
    if not procurar:
        return set()
    if recursos.falhas():
        raise ErroHttp(503, FORA_DA_IA_INDISPONIVEL)
    fora: set[int] = set()
    for inicio in range(0, len(procurar), 500):
        parte = procurar[inicio:inicio + 500]
        marcas = ", ".join("?" for _ in parte)
        for l in banco.consultar(f"SELECT id, texto, data_registro FROM registros WHERE id IN ({marcas})", parte):
            try:
                referencia = date.fromisoformat(str(l.get("data_registro"))[:10])
            except ValueError:
                referencia = date.today()
            if texto_fica_fora_da_ia(l.get("texto") or "", referencia, banco):
                fora.add(l["id"])
    return fora


def contexto_sugestao(banco: Banco, referencia: date) -> list[dict]:
    """O que a IA recebe para pensar numa sugestão (seção 17.3): itens ativos recentes, datas
    próximas e o que ajuda ou é restrição. Registros "não enviar para a IA" (e os que os
    recursos deixam fora da IA, `fora_da_ia`) ficam de fora.

    Prioridade: o que acontece agora (estado, pendência, evento próximo), depois o resto dos
    registros recentes, depois apoio e restrições. No máximo MAX_CONTEXTO_SUGESTAO itens.
    """
    privados = fora_da_ia(banco)
    datas_registro = {r["id"]: r["data_registro"] for r in banco.registros(limite=1000)}
    inicio = referencia - timedelta(days=DIAS_CONTEXTO_SUGESTAO)
    fim = referencia + timedelta(days=30)
    escolhidos: dict[int, tuple[int, Item]] = {}

    def incluir(item: Item, prioridade: int) -> None:
        if item.id is None or item.registro_id in privados:
            return
        if item.id not in escolhidos or prioridade < escolhidos[item.id][0]:
            escolhidos[item.id] = (prioridade, item)

    agora = (TipoItem.ESTADO, TipoItem.PENDENCIA, TipoItem.EVENTO)
    for item in banco.itens(desde=inicio):
        incluir(item, 0 if item.tipo in agora else 2 if item.tipo == TipoItem.NOTA else 1)
    for tipo in (TipoItem.EVENTO, TipoItem.DATA_IMPORTANTE, TipoItem.PENDENCIA):
        for item in banco.itens(tipo=tipo):
            quando = item.data_evento or item.lembrar_em
            if quando is not None and item.recorrente:
                quando = _proxima_data(quando, referencia, 3)
            if quando is not None and referencia - timedelta(days=3) <= quando <= fim:
                incluir(item, 0)
    for tipo in (TipoItem.APOIO, TipoItem.RESTRICAO):
        for item in banco.itens(tipo=tipo):
            incluir(item, 3)

    def data_de(item: Item) -> date | None:
        if item.registro_id not in datas_registro:
            registro = banco.registro(item.registro_id)
            datas_registro[item.registro_id] = registro["data_registro"] if registro else None
        return datas_registro[item.registro_id]

    ordem = sorted(escolhidos.values(),
                   key=lambda par: (par[0], -(data_de(par[1]) or date.min).toordinal(), -(par[1].id or 0)))
    # O texto do registro também decide (seção 20.10): um desentendimento salvo sem confirmar o extra
    # da área Eu, uma frase do ciclo com "Não guardar"... ficam de fora. Lido só para o que entraria.
    texto_fora: dict[Any, bool] = {}
    aceitos: list[tuple[int, Item]] = []
    for par in ordem:
        rid = par[1].registro_id
        if rid not in texto_fora:
            texto_fora[rid] = bool(fora_da_ia_pelo_texto(banco, [rid], privados))
        if not texto_fora[rid]:
            aceitos.append(par)
            if len(aceitos) >= MAX_CONTEXTO_SUGESTAO:
                break
    ordem = aceitos
    contexto = [para_json({
        "item_id": item.id, "registro_id": item.registro_id, "tipo": item.tipo, "resumo": item.resumo,
        "trecho": item.trecho, "certeza": item.certeza, "categorias": list(item.categorias),
        "pessoa": item.pessoa, "alvo": item.alvo, "polaridade": item.polaridade,
        "data_registro": data_de(item), "data_evento": item.data_evento, "precisao": item.precisao,
    }) for _, item in ordem[:MAX_CONTEXTO_SUGESTAO]]
    # Só o que está escrito no próprio registro: nome ou relação que veio do cadastro (às vezes
    # aprendido num registro 🔒) não vai para a IA.
    return so_o_escrito(banco, contexto)


def so_o_escrito(banco: Banco, entradas: list[dict]) -> list[dict]:
    """Cada achado (da busca ou do contexto da sugestão) limpo por `motor.so_o_escrito` com o
    texto do próprio registro. Achado de registro que não existe mais fica de fora."""
    limpar = _funcao("motor", "so_o_escrito")
    pessoas = banco.pessoas()
    textos: dict[Any, str | None] = {}
    saida: list[dict] = []
    for entrada in entradas:
        if not isinstance(entrada, dict):
            continue
        rid = entrada.get("registro_id")
        if rid not in textos:
            registro = banco.registro(rid) if isinstance(rid, int) and not isinstance(rid, bool) else None
            textos[rid] = registro["texto"] if registro else None
        if textos[rid] is not None:
            saida.append(limpar(entrada, textos[rid], pessoas))
    return saida


def _origem(url: str) -> tuple[str, str, int | None]:
    """(esquema, host, porta) de uma URL: é para onde a chave seria mandada."""
    partes = urllib.parse.urlsplit(url or "")
    try:
        porta = partes.port
    except ValueError:
        porta = None
    padrao = {"http": 80, "https": 443}.get(partes.scheme.lower())
    return partes.scheme.lower(), (partes.hostname or "").lower().rstrip("."), porta or padrao


def _rede_protegida(host: str | None) -> bool:
    """Este computador ou rede de casa (IP privado): ali a chave pode ir sem https."""
    host = (host or "").lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _conferir_envio_da_chave(url: str, chave: str | None) -> None:
    """Chave para a internet só por https (como o motor exige): recusa já ao salvar."""
    partes = urllib.parse.urlsplit(url)
    if chave and partes.scheme != "https" and not _rede_protegida(partes.hostname):
        raise ErroHttp(400, "Para enviar a chave pela internet, o endereço precisa começar com https://.")


def _erro_da_ia(erro: BaseException) -> ErroHttp:
    """Falha ao falar com a IA (listar modelos, testar) -> mensagem para a tela, sem chave nem URL."""
    if isinstance(erro, ErroHttp):
        return erro
    codigo = getattr(erro, "code", None) if isinstance(erro, urllib.error.HTTPError) else None
    if codigo in (401, 403):
        return ErroHttp(502, "A chave foi recusada pelo serviço de IA.")
    if codigo == 429:
        return ErroHttp(502, "O serviço de IA chegou ao limite de uso. Tente mais tarde.")
    if isinstance(erro, TimeoutError):          # socket.timeout é o mesmo TimeoutError
        return ErroHttp(504, "A IA demorou demais para responder.")
    mensagem = getattr(erro, "mensagem", None)
    if getattr(erro, "tipo", None) == "tempo":          # ErroIA do motor: tempo esgotado
        return ErroHttp(504, mensagem.strip() if isinstance(mensagem, str) and mensagem.strip()
                        else "A IA demorou demais para responder.")
    if isinstance(mensagem, str) and mensagem.strip():
        return ErroHttp(502, mensagem.strip())
    if isinstance(erro, ValueError) and not isinstance(erro, UnicodeError) and str(erro) \
            and not _RE_MENSAGEM_INGLES.search(str(erro)):
        return ErroHttp(502, str(erro))
    if isinstance(erro, (urllib.error.URLError, OSError)):
        return ErroHttp(502, "Não consegui falar com a IA nesse endereço.")
    registrar_erro(erro)
    return ErroHttp(502, "A IA não respondeu do jeito esperado.")


# ---------------------------------------------------------------- leitura do pedido

def _nfc(texto: str) -> str:
    """Acentos numa forma só (o navegador quase sempre já manda assim)."""
    return unicodedata.normalize("NFC", texto)


def _recusar_constante(nome: str):
    raise ValueError(f"Valor JSON não permitido: {nome}.")


def _json_do_corpo(bruto: bytes, grandes: tuple[str, ...] = ()) -> dict:
    """O corpo em JSON (objeto). `grandes`: campos de texto que podem ter dezenas de MB (o
    arquivo do backup, o JSON exportado) e que a própria rota confere; ficam de fora da
    conferência de UTF-8 abaixo, que faria mais duas cópias deles na memória."""
    if not bruto.strip():
        return {}
    try:
        dados = json.loads(bruto.decode("utf-8"), parse_constant=_recusar_constante)
        if not isinstance(dados, dict):
            raise ErroHttp(400, "O corpo do pedido precisa ser um objeto JSON.")
        # Escapes como "\ud800" geram texto que não vira UTF-8: recusa já na entrada.
        conferir = {k: v for k, v in dados.items() if k not in grandes or not isinstance(v, str)} \
            if grandes else dados
        json.dumps(conferir, ensure_ascii=False).encode("utf-8")
    except (UnicodeError, ValueError, RecursionError):
        raise ErroHttp(400, "O corpo do pedido não é um JSON válido.") from None
    return dados


def _data(valor: Any, campo: str) -> date | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor.strip()[:10])
        except ValueError:
            pass
    raise ErroHttp(400, f"Data inválida em “{campo}”: use o formato AAAA-MM-DD.")


def _texto(corpo: dict, campo: str, mensagem: str) -> str:
    valor = corpo.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise ErroHttp(400, mensagem)
    return _nfc(valor)


def _texto_opcional(corpo: dict, campo: str) -> str | None:
    valor = corpo.get(campo)
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ErroHttp(400, f"“{campo}” precisa ser um texto.")
    return _nfc(valor)


def _campos_editaveis(corpo: dict) -> dict:
    """Campos de um PATCH, com os textos em NFC. O id vem do endereço, nunca do corpo."""
    if "id" in corpo:
        raise ErroHttp(400, "Campos que não podem ser alterados: id")
    return {k: (_nfc(v) if isinstance(v, str) else v) for k, v in corpo.items()}


def _privado(corpo: dict) -> bool:
    """O campo "privado" ("não enviar para a IA"): true/false; ausente ou null = false."""
    valor = corpo.get("privado")
    if valor is None:
        return False
    if not isinstance(valor, bool):
        raise ErroHttp(400, "“privado” precisa ser verdadeiro ou falso.")
    return valor


def _qual_ia(valor: Any) -> str:
    if valor not in QUAIS_IA:
        raise ErroHttp(400, "Diga qual IA: “principal” ou “reserva”.")
    return valor


def _lista_de_objetos(corpo: dict, campo: str) -> list[dict]:
    valor = corpo.get(campo)
    if valor is None:
        return []
    if not isinstance(valor, list) or not all(isinstance(x, dict) for x in valor):
        raise ErroHttp(400, f"“{campo}” precisa ser uma lista de objetos.")
    return [dict(x) for x in valor]


def _arquivo_do_corpo(valor: Any) -> bytes:
    """O arquivo do backup, que vem em base64 (aceita também o "data:...;base64," do navegador)."""
    if not isinstance(valor, str) or not valor.strip():
        raise ErroHttp(400, "Escolha o arquivo do backup (.cofre).")
    texto = valor.strip()
    if texto.startswith("data:"):
        cabeca, _, texto = texto.partition(",")
        if not cabeca.endswith(";base64"):
            raise ErroHttp(400, "O arquivo não chegou no formato esperado (base64).")
    try:
        conteudo = base64.b64decode(re.sub(r"\s+", "", texto), validate=True)
    except (binascii.Error, ValueError):
        raise ErroHttp(400, "O arquivo não chegou no formato esperado (base64).") from None
    if not conteudo:
        raise ErroHttp(400, "Escolha o arquivo do backup (.cofre).")
    return conteudo


def _consulta(consulta: Mapping[str, Any] | None) -> dict[str, str]:
    """Os parâmetros do endereço ({nome: valor}). Valor em lista (como o `parse_qs` devolve)
    vale o último; valor que não é texto é recusado."""
    if not consulta:
        return {}
    if not isinstance(consulta, Mapping):
        raise ErroHttp(400, "Parâmetros inválidos no endereço.")
    if len(consulta) > MAX_PARAMETROS:
        raise ErroHttp(400, "Parâmetros demais no endereço.")
    saida: dict[str, str] = {}
    for nome, valor in consulta.items():
        if isinstance(valor, (list, tuple)):
            valor = valor[-1] if valor else ""
        if not isinstance(nome, str) or not isinstance(valor, str):
            raise ErroHttp(400, "Parâmetros inválidos no endereço.")
        saida[nome] = valor
    return saida


def mensagem_de_limite(limite: int) -> str:
    """A mensagem do 413, com o limite da rota (1 MB ou 64 MB)."""
    return f"O pedido é grande demais (limite de {limite // (1024 * 1024)} MB)."


@dataclass
class Pedido:
    metodo: str
    caminho: str
    consulta: dict[str, str] = field(default_factory=dict)
    bruto: bytes = b""
    token: str | None = None
    grupos: tuple[str, ...] = ()
    campos_grandes: tuple[str, ...] = ()         # ver _json_do_corpo
    encerrar: bool = False                       # /api/sair: o transporte desliga depois de responder
    _corpo: dict | None = field(default=None, repr=False)

    @property
    def corpo(self) -> dict:
        """O JSON do corpo, lido só depois de conferir o token."""
        if self._corpo is None:
            self._corpo = _json_do_corpo(self.bruto, self.campos_grandes)
            self.bruto = b""             # os bytes já viraram o dict: não ficam em dobro na memória
        return self._corpo

    @property
    def id(self) -> int:
        return int(self.grupos[0])

    def parametro(self, nome: str) -> str | None:
        return self.consulta.get(nome)

    def inteiro(self, nome: str, padrao: int | None, minimo: int, maximo: int) -> int | None:
        bruto = self.parametro(nome)
        if bruto is None or bruto.strip() == "":
            return padrao
        try:
            valor = int(bruto)
        except ValueError:
            raise ErroHttp(400, f"O parâmetro “{nome}” precisa ser um número inteiro.") from None
        if not minimo <= valor <= maximo:
            raise ErroHttp(400, f"O parâmetro “{nome}” precisa ficar entre {minimo} e {maximo}.")
        return valor


# ---------------------------------------------------------------- resposta

def _json_bytes(dados: Any) -> bytes:
    return json.dumps(dados, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass
class RespostaApi:
    """O que `Nucleo.tratar` devolve, igual para qualquer transporte.

    `dados` já está em JSON simples (dict, list, texto, número...); `corpo` é ele em JSON UTF-8,
    pronto para enviar. `cabecalhos`: os que importam para quem chamou (Allow no 405,
    Retry-After no 429). `encerrar`: o app foi encerrado por este pedido (/api/sair).
    """
    status: int
    dados: Any
    cabecalhos: dict[str, str] = field(default_factory=dict)
    corpo: bytes = b""
    encerrar: bool = False


def resposta_de_erro(erro: BaseException) -> RespostaApi:
    """Qualquer exceção vira a resposta JSON {"erro": "...", ...extras} com o status certo."""
    falha = traduzir_erro(erro)
    dados = {**falha.extras, "erro": falha.mensagem}
    return RespostaApi(falha.status, dados, dict(falha.cabecalhos), _json_bytes(dados))


# ---------------------------------------------------------------- rotas

PUBLICA = "publica"      # sem token
SESSAO = "sessao"        # token + banco aberto; roda com a trava do app (um pedido por vez)
LIVRE = "livre"          # token conferido antes; a função cuida das próprias travas
# Sem token só enquanto não existe diário (primeiro uso); existindo, token como LIVRE. A função
# confere sozinha (/api/restaurar, seção 18.2).
PRIMEIRO_USO = "primeiro_uso"


@dataclass(frozen=True)
class Rota:
    metodo: str
    padrao: re.Pattern[str]
    funcao: str
    acesso: str
    limite_corpo: int = LIMITE_CORPO
    campos_grandes: tuple[str, ...] = ()
    # Rotas dos recursos (seção 20.0): `manipulador(nucleo, pedido, banco)` no lugar de `funcao`.
    manipulador: Callable[[Any, Any, Any], Any] | None = None
    recurso: str | None = None
    # False: o pedido não conta como uso (não adia o bloqueio por inatividade). As rotas que a
    # sincronização chama sozinha, de minuto em minuto quando falta rede (seção 22.2).
    conta_uso: bool = True


def _rota(metodo: str, caminho: str, funcao: str, acesso: str = SESSAO, limite_corpo: int = LIMITE_CORPO,
          campos_grandes: tuple[str, ...] = (), conta_uso: bool = True) -> Rota:
    padrao = re.escape("/api/" + caminho).replace(re.escape("{id}"), r"([0-9]{1,12})")
    return Rota(metodo, re.compile(padrao), funcao, acesso, limite_corpo, campos_grandes, conta_uso=conta_uso)


ROTAS: tuple[Rota, ...] = (
    _rota("GET", "ping", "_ping", PUBLICA),
    _rota("GET", "estado", "_estado", PUBLICA),
    _rota("POST", "configurar", "_configurar", PUBLICA),
    _rota("POST", "desbloquear", "_desbloquear", PUBLICA),
    _rota("POST", "bloquear", "_bloquear"),
    _rota("POST", "analisar", "_analisar", LIVRE),
    _rota("POST", "registros", "_criar_registro"),
    _rota("GET", "registros", "_listar_registros"),
    _rota("GET", "registros/{id}", "_ver_registro"),
    _rota("PATCH", "registros/{id}", "_marcar_privado"),
    _rota("DELETE", "registros/{id}", "_apagar_registro"),
    _rota("PATCH", "itens/{id}", "_editar_item"),
    _rota("DELETE", "itens/{id}", "_apagar_item"),
    _rota("GET", "hoje", "_hoje"),
    _rota("POST", "alertas/dispensar", "_dispensar_alerta"),
    _rota("GET", "perfil", "_perfil"),
    _rota("GET", "pessoas", "_listar_pessoas"),
    _rota("POST", "pessoas", "_criar_pessoa"),
    _rota("PATCH", "pessoas/{id}", "_editar_pessoa"),
    _rota("DELETE", "pessoas/{id}", "_apagar_pessoa"),
    _rota("GET", "datas", "_datas"),
    _rota("GET", "busca", "_busca"),
    _rota("POST", "relacoes/{id}", "_resolver_relacao"),
    _rota("GET", "config", "_ver_config"),
    _rota("PATCH", "config", "_editar_config"),
    _rota("POST", "pin", "_trocar_pin", LIVRE),
    _rota("POST", "backup", "_backup"),
    _rota("POST", "backup/baixar", "_baixar_copia"),
    _rota("GET", "exportar", "_exportar"),
    _rota("GET", "historico", "_historico"),
    _rota("POST", "sair", "_sair", LIVRE),
    # IA sob demanda (seção 17.6). As que conferem PIN ou falam com a IA são LIVRE: o scrypt e a
    # resposta da IA não prendem o resto do app.
    _rota("GET", "ia/estado", "_ia_estado"),
    _rota("POST", "ia/pin", "_ia_criar_pin", LIVRE),
    _rota("POST", "ia/entrar", "_ia_entrar", LIVRE),
    _rota("POST", "ia/sair", "_ia_sair"),
    _rota("POST", "ia/pin/trocar", "_ia_trocar_pin", LIVRE),
    _rota("POST", "ia/redefinir", "_ia_redefinir", LIVRE),
    _rota("GET", "ia/config", "_ia_ver_config"),
    _rota("PATCH", "ia/config", "_ia_editar_config"),
    *(_rota("PUT", f"ia/config/{qual}", "_ia_salvar") for qual in QUAIS_IA),
    *(_rota("DELETE", f"ia/config/{qual}", "_ia_remover") for qual in QUAIS_IA),
    _rota("POST", "ia/modelos", "_ia_modelos", LIVRE),
    _rota("POST", "ia/testar", "_ia_testar", LIVRE),
    _rota("POST", "ia/analisar", "_ia_analisar", LIVRE),
    _rota("POST", "ia/busca", "_ia_busca", LIVRE),
    _rota("POST", "ia/sugestao", "_ia_sugestao", LIVRE),
    # Restaurar backup e importar JSON (seção 18.2): o arquivo vem no corpo, até 64 MB. LIVRE: o
    # scrypt do backup e a leitura do JSON rodam sem prender o resto do app.
    _rota("POST", "restaurar", "_restaurar", PRIMEIRO_USO, LIMITE_CORPO_ARQUIVO, ("arquivo_b64",)),
    _rota("POST", "importar/previa", "_importar_previa", LIVRE, LIMITE_CORPO_ARQUIVO, ("json",)),
    _rota("POST", "importar", "_importar", LIVRE, LIMITE_CORPO_ARQUIVO, ("json",)),
    # Sincronização (seção 22): a página fala com o Google Drive e entrega aqui os arquivos dos outros
    # aparelhos (receber, até o limite dos arquivos) e pede o deste (preparar). A senha é LIVRE: o
    # scrypt roda sem prender o resto do app.
    # As que o motor chama sozinho (estado, iniciar, receber, preparar, enviado, conflitos) não contam
    # como uso: as novas tentativas sem rede não podem impedir o bloqueio por inatividade (22.2).
    _rota("GET", "sync/estado", "_sync_estado", conta_uso=False),
    _rota("PATCH", "sync/ajustes", "_sync_ajustes"),
    _rota("POST", "sync/senha", "_sync_senha", LIVRE),
    _rota("POST", "sync/iniciar", "_sync_iniciar", conta_uso=False),
    _rota("POST", "sync/receber", "_sync_receber", SESSAO, LIMITE_CORPO_ARQUIVO, ("conteudo_b64",), conta_uso=False),
    _rota("POST", "sync/preparar", "_sync_preparar", conta_uso=False),
    _rota("POST", "sync/enviado", "_sync_enviado", conta_uso=False),
    _rota("GET", "sync/conflitos", "_sync_conflitos", conta_uso=False),
    _rota("POST", "sync/conflitos/{id}", "_sync_resolver"),
    _rota("POST", "sync/desligar", "_sync_desligar"),
)


_RE_CAMINHO_RECURSO = re.compile(r"(?:[a-z0-9_-]+|\{id\})(?:/(?:[a-z0-9_-]+|\{id\}))*")
_ROTAS_DOS_RECURSOS: tuple[int, tuple[Rota, ...]] | None = None


def _rota_de_recurso(nome: str, bruta: Any) -> Rota:
    """RotaRecurso -> Rota em /api/<NOME>/<caminho>. Só sessão ou livre: recurso nunca tem rota
    pública nem de primeiro uso (nada sai sem o PIN)."""
    if not isinstance(bruta, recursos.RotaRecurso):
        raise TypeError("use recursos.rota(...)")
    if bruta.metodo not in recursos.METODOS or bruta.acesso not in recursos.ACESSOS or not callable(bruta.funcao):
        raise ValueError("método, acesso ou função da rota")
    caminho = bruta.caminho.strip("/")
    if caminho and not _RE_CAMINHO_RECURSO.fullmatch(caminho):
        raise ValueError("caminho da rota")
    base = _rota(bruta.metodo, f"{nome}/{caminho}" if caminho else nome, "", bruta.acesso)
    return Rota(base.metodo, base.padrao, "", base.acesso, manipulador=bruta.funcao, recurso=nome)


def rotas_dos_recursos() -> tuple[Rota, ...]:
    """As rotas de todos os recursos (refeitas quando a lista de recursos muda). Recurso cujas
    rotas não cumprem o contrato fica sem nenhuma (e o erro vai para o log)."""
    global _ROTAS_DOS_RECURSOS
    geracao = recursos.geracao()
    if _ROTAS_DOS_RECURSOS is not None and _ROTAS_DOS_RECURSOS[0] == geracao:
        return _ROTAS_DOS_RECURSOS[1]
    todas: list[Rota] = []
    vistas: set[tuple[str, str]] = set()
    for nome, funcao in recursos.com_gancho("rotas"):
        try:
            do_recurso = [_rota_de_recurso(nome, bruta) for bruta in (funcao() or ())]
        except Exception as erro:  # noqa: BLE001 - o recurso fica sem rotas; o app segue
            recursos.registrar_erro(erro, nome, "rotas")
            continue
        for rota in do_recurso:
            if (rota.metodo, rota.padrao.pattern) not in vistas:
                vistas.add((rota.metodo, rota.padrao.pattern))
                todas.append(rota)
    _ROTAS_DOS_RECURSOS = (geracao, tuple(todas))
    return _ROTAS_DOS_RECURSOS[1]


def todas_as_rotas() -> tuple[Rota, ...]:
    """As rotas do núcleo (ROTAS) e, depois, as dos recursos (/api/<NOME>/...)."""
    return (*ROTAS, *rotas_dos_recursos())


def achar_rota(metodo: str, caminho: str) -> tuple[Rota, tuple[str, ...]]:
    """A rota de `metodo caminho` e os grupos do endereço ({id}). 404 sem rota; 405 (com Allow)
    quando o endereço existe com outro método."""
    permitidos: set[str] = set()
    for rota in todas_as_rotas():
        achado = rota.padrao.fullmatch(caminho)
        if achado:
            if rota.metodo == metodo:
                return rota, achado.groups()
            permitidos.add(rota.metodo)
    if permitidos:
        raise ErroHttp(405, "Este endereço não aceita esse tipo de pedido.",
                       {"Allow": ", ".join(sorted(permitidos))})
    raise ErroHttp(404, "Não encontrei esse endereço.")


# ---------------------------------------------------------------- o app

class Nucleo:
    """Estado do app: cofre, banco aberto, token, PIN e bloqueio. Não sabe nada de transporte.

    Só usa travas (`threading.RLock`), nunca inicia thread nem dorme: roda igual no servidor do
    computador (um pedido por thread) e no navegador (um pedido por vez, no Web Worker).

    Dois relógios (seção 19.7): `relogio` (segundos monotônicos: ninguém o adianta mudando a hora
    do aparelho) conta o bloqueio do PIN errado; `relogio_parede` (time.time) conta também o
    tempo em que o aparelho dormiu, que o monotônico do navegador (`performance.now()`) não conta.
    A inatividade e a área da IA valem pelo que tiver andado mais; o bloqueio do PIN guardado em
    `pin_espera.json` usa a parede só entre uma abertura e outra.

    `plataforma` ("computador" ou "navegador") muda o que só faz sentido num deles: backup numa
    pasta, IA, limite dos arquivos enviados, quantas cópias ficam em backups/ e alguns textos.
    """

    def __init__(self, pasta: Path | str, *, atraso_pin: float = ATRASO_PIN,
                 atraso_pin_maximo: float = ATRASO_PIN_MAXIMO, minutos_bloqueio: float | None = None,
                 hoje: Callable[[], date] | None = None, relogio: Callable[[], float] | None = None,
                 relogio_parede: Callable[[], float] | None = None,
                 minutos_area_ia: float = MINUTOS_AREA_IA, plataforma: str = COMPUTADOR):
        if plataforma not in PLATAFORMAS:
            raise ValueError(f"Plataforma desconhecida: {plataforma!r}.")
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.plataforma = plataforma
        no_computador = plataforma == COMPUTADOR
        self.hoje = hoje or date.today
        self.relogio = relogio or time.monotonic      # segundos; só diferenças importam
        self.relogio_parede = relogio_parede or time.time
        self.cofre = Cofre(self.pasta)
        self.cofre.hoje = self.hoje
        # O que muda com a plataforma (atributos: os testes podem trocar depois de criar).
        self.backup_em_pasta = no_computador          # POST /api/backup {pasta}
        self.ia_disponivel = no_computador            # os botões ✨ falam com a IA pela rede
        self.limite_corpo_arquivo = LIMITE_CORPO_ARQUIVO if no_computador else LIMITE_CORPO_ARQUIVO_NAVEGADOR
        if not no_computador:
            self.cofre.backups_automaticos = BACKUPS_AUTOMATICOS_NAVEGADOR
            self.cofre.copias_extras_maximas = COPIAS_EXTRAS_NAVEGADOR
        self.atraso_pin = float(atraso_pin)
        self.atraso_pin_maximo = float(atraso_pin_maximo)
        self.minutos_bloqueio = minutos_bloqueio      # None = o que estiver nos ajustes
        self._trava = threading.RLock()
        self._trava_pin = threading.RLock()
        self._banco: Banco | None = None
        self._token: str | None = None
        self._ultima_atividade = self.relogio()
        self._ultima_atividade_parede = self.relogio_parede()
        # PIN principal (desbloquear, restaurar, trocar o PIN, "esqueci o PIN da IA" e a
        # comparação "PIN da IA igual ao principal?"): um bloqueio temporário para todos.
        self._falhas_pin = 0
        self._espera_pin = _Espera(lambda: self.relogio())
        self._comparacoes_pin = 0        # "o PIN da IA é igual ao principal?" desde o último PIN certo
        # Área de ajustes da IA: aberta desde este instante ((relogio, relogio_parede)), ou fechada (None).
        self.minutos_area_ia = float(minutos_area_ia)
        self._area_ia_desde: tuple[float, float] | None = None
        # PIN da IA: trava, contador e bloqueio próprios (errar um não mexe no outro).
        self._trava_pin_ia = threading.RLock()
        self._falhas_pin_ia = 0
        self._espera_pin_ia = _Espera(lambda: self.relogio())
        # Senha de sincronização errada (seção 22): o mesmo bloqueio crescente, contador próprio (só em
        # memória: quem tem o entrenos-sync.json do Drive testa senhas fora do app de qualquer jeito).
        self._trava_senha_sync = threading.RLock()
        self._falhas_senha_sync = 0
        self._espera_senha_sync = _Espera(lambda: self.relogio())
        self._encerrando = False
        # O bloqueio do PIN que valia quando o app fechou (pin_espera.json).
        self._arquivo_espera = self.pasta / ARQUIVO_ESPERA_PIN
        self._trava_espera = threading.Lock()
        self._espera_no_disco = False
        self._ler_espera_gravada()

    # ------------------------------------------------------------ entrada única

    def tratar(self, metodo: str, caminho: str, *, corpo: bytes | None = None, token: str | None = None,
               consulta: Mapping[str, Any] | None = None) -> RespostaApi:
        """Um pedido da tela, venha de onde vier: `metodo` ("GET", "POST"...), `caminho`
        ("/api/registros/3", sem a consulta), `corpo` (bytes do JSON, ou None), `token` (o
        X-EntreNos-Token) e `consulta` ({"q": "..."}). Nunca levanta: erro vira RespostaApi.

        Passe o corpo direto na chamada (sem guardar outra referência a ele): o pedido o solta
        assim que o JSON é lido, e um arquivo de 64 MB não fica duas vezes na memória.
        """
        pedido: Pedido | None = None
        try:
            self.verificar_inatividade()
            rota, grupos = achar_rota(metodo, caminho)
            limite = self.limite_do_corpo(rota)
            if corpo is not None and len(corpo) > limite:
                raise ErroHttp(413, mensagem_de_limite(limite))
            pedido = Pedido(metodo, caminho, _consulta(consulta), bytes(corpo or b""), token, grupos,
                            campos_grandes=rota.campos_grandes)
            corpo = None                 # só o pedido guarda os bytes (e os solta depois de ler o JSON)
            dados = para_json(self.atender(rota, pedido))
            bruto = _json_bytes(dados)
        except Exception as erro:  # noqa: BLE001 - todo erro vira resposta JSON
            return resposta_de_erro(erro)
        return RespostaApi(200, dados, {}, bruto, encerrar=pedido.encerrar)

    def atender(self, rota: Rota, pedido: Pedido) -> Any:
        """Confere o acesso da rota e roda a função dela (o resultado ainda não está em JSON)."""
        if rota.manipulador is not None:
            return self._atender_recurso(rota, pedido)
        funcao = getattr(self, rota.funcao)
        if rota.acesso in (PUBLICA, PRIMEIRO_USO):
            return funcao(pedido)
        if rota.acesso == LIVRE:
            self._exigir_sessao(pedido.token)
            try:
                return funcao(pedido)
            finally:
                with self._trava:
                    if self._banco is not None:
                        self._marcar_atividade()
        with self._trava:
            banco = self._exigir_sessao(pedido.token, marcar=rota.conta_uso)
            try:
                return funcao(pedido, banco)
            finally:
                if self._banco is banco and rota.conta_uso:
                    self._marcar_atividade()

    def _atender_recurso(self, rota: Rota, pedido: Pedido) -> Any:
        """Rota de recurso (seção 20.0): `manipulador(nucleo, pedido, banco)`. Sessão (o padrão):
        com a trava do app. Livre: sem a trava (para trabalho demorado); quem mexe no banco pega
        `with nucleo.trava:` antes."""
        if rota.acesso == LIVRE:
            banco = self._exigir_sessao(pedido.token)
            try:
                return rota.manipulador(self, pedido, banco)
            finally:
                with self._trava:
                    if self._banco is not None:
                        self._marcar_atividade()
        if rota.acesso != SESSAO:
            raise ErroHttp(500, FALHA_INTERNA)          # nunca: _rota_de_recurso só deixa sessão e livre
        with self._trava:
            banco = self._exigir_sessao(pedido.token)
            try:
                return rota.manipulador(self, pedido, banco)
            finally:
                if self._banco is banco:
                    self._marcar_atividade()

    @property
    def trava(self) -> threading.RLock:
        """A trava do app (um pedido de sessão por vez). Para as rotas livres dos recursos."""
        return self._trava

    def limpar_copias(self, apagar: Callable[[Banco], bool | None]) -> None:
        """Repete `apagar` nas cópias automáticas de backups/ (apagar é apagar também nelas).
        Para as rotas dos recursos que apagam algo: `nucleo.limpar_copias(lambda b: b.apagar(t, id))`."""
        self._limpar_backups(apagar)

    def limite_do_corpo(self, rota: Rota) -> int:
        """Bytes que o corpo desta rota pode ter: 1 MB, ou o limite dos arquivos (restaurar e
        importar), que é menor no navegador."""
        return rota.limite_corpo if rota.limite_corpo <= LIMITE_CORPO else self.limite_corpo_arquivo

    def conferir_antes_do_corpo(self, rota: Rota, token: str | None) -> None:
        """Rotas de corpo grande (até 64 MB): o transporte confere o token antes de receber o
        corpo. Na rota de primeiro uso, sem diário (ou com ele danificado) não há token."""
        if rota.acesso == PUBLICA or (rota.acesso == PRIMEIRO_USO and (self.primeiro_uso()
                                                                       or self.diario_danificado())):
            return
        self._exigir_sessao(token)

    # ------------------------------------------------------------ ciclo de vida

    @property
    def desbloqueado(self) -> bool:
        with self._trava:           # espera um bloqueio em andamento terminar
            return self._banco is not None

    def verificar_inatividade(self) -> bool:
        """Bloqueia se passou do tempo sem uso. True quando bloqueou agora.

        Roda no começo de cada pedido; o transporte pode chamar também de tempos em tempos (no
        computador, a thread vigia do servidor.py), para bloquear mesmo sem pedido nenhum.
        """
        with self._trava:
            if self._banco is None:
                return False
            if self._sem_uso_ha() < self._limite_inatividade(self._banco):
                return False
            self.bloquear()
            return True

    def agenda_de_avisos(self, dias: int | None = None) -> dict[str, int] | None:
        """Os dias com lembretes de data e quantos ({"AAAA-MM-DD": n}), para a agenda de avisos que
        fica fora do cofre no navegador (seção 21.5): só datas e contagens. None com o diário
        bloqueado. Não conta como uso (não adia o bloqueio por inatividade) e não grava nada."""
        with self._trava:
            self.verificar_inatividade()
            banco = self._banco
            if banco is None:
                return None
            alertas = _importar("alertas")
            return alertas.agenda_de_avisos(banco, self.hoje(),
                                            alertas.DIAS_AGENDA_AVISOS if dias is None else dias)

    def _tempo_desde(self, monotonico: float, parede: float) -> float:
        """Segundos desde um instante marcado nos dois relógios: o que tiver andado mais.

        O monotônico não volta nem é adiantado por quem muda a hora; o de parede conta o tempo em
        que o celular dormiu com o app aberto (o `performance.now()` do navegador para). Hora
        atrasada à mão faz o de parede andar para trás: aí vale o monotônico."""
        return max(self.relogio() - monotonico, self.relogio_parede() - parede)

    def _sem_uso_ha(self) -> float:
        return self._tempo_desde(self._ultima_atividade, self._ultima_atividade_parede)

    def _limite_inatividade(self, banco: Banco) -> float:
        minutos = self.minutos_bloqueio
        if minutos is None:
            try:
                minutos = float(banco.config().get("minutos_bloqueio") or config.MINUTOS_BLOQUEIO_PADRAO)
            except Exception:  # noqa: BLE001 - ajuste estranho: usa o padrão
                minutos = config.MINUTOS_BLOQUEIO_PADRAO
        return float(minutos) * 60

    def bloquear(self) -> None:
        """Salva (se algo ficou sem salvar), esquece a chave e descarta o banco em memória e o token.

        Cada escrita já salva o diário; aqui só sobra o que não foi para o arquivo (um salvar que
        falhou, a migração de um diário antigo). Sem isso, cada bloqueio recifrava e regravava o
        arquivo inteiro à toa (no navegador, mais uma cópia para o armazenamento; seção 19.7)."""
        with self._trava:
            banco, self._banco, self._token = self._banco, None, None
            self._area_ia_desde = None           # a área da IA fecha junto
            esquecer_textos_fora_da_ia()

            if banco is not None:
                try:
                    if getattr(banco, "sujo", True):
                        self.cofre.salvar(banco.para_bytes())
                except (PastaSumiu, CofreMudou):
                    pass        # pasta apagada (18.6) ou diário gravado em outro lugar (19.7): não regrava
                except Exception as erro:  # noqa: BLE001 - bloquear vem antes de tudo
                    registrar_erro(erro)
                banco.ao_salvar = None
                banco.fechar()
            self.cofre.fechar()

    def encerrar(self) -> None:
        """Salva e bloqueia de vez (fim do programa): configurar, desbloquear e restaurar passam
        a responder 503."""
        self._encerrando = True
        self.bloquear()

    def _salvar(self, dados: bytes) -> None:
        """`ao_salvar` do banco aberto. Pasta de dados apagada com o diário aberto (o desinstalador
        com "apagar tudo"): bloqueia sem salvar, em vez de recriar o que foi apagado. O arquivo
        mudou desde que este núcleo o leu ou gravou (outra aba gravou nele, seção 19.7): também
        bloqueia sem salvar, em vez de apagar o que a outra gravou."""
        try:
            self.cofre.salvar(dados)
        except PastaSumiu:
            self._descartar_sem_salvar()
            raise ErroHttp(409, PASTA_SUMIU if self.plataforma == COMPUTADOR else PASTA_SUMIU_NAVEGADOR) from None
        except CofreMudou:
            self._descartar_sem_salvar()
            raise ErroHttp(409, DIARIO_MUDOU) from None

    def _descartar_sem_salvar(self) -> None:
        """Esquece o diário aberto, a chave e o token sem gravar nada."""
        with self._trava:
            banco, self._banco, self._token = self._banco, None, None
            self._area_ia_desde = None
            if banco is not None:
                banco.ao_salvar = None   # a conexão fecha sozinha quando o Banco é descartado
            self.cofre.fechar()

    def _marcar_atividade(self) -> None:
        self._ultima_atividade = self.relogio()
        self._ultima_atividade_parede = self.relogio_parede()

    def _exigir_sessao(self, token: str | None, *, marcar: bool = True) -> Banco:
        """O banco aberto, se o token confere. Bloqueado: 423; token errado ou ausente: 401.
        `marcar`: conta como uso (adia o bloqueio por inatividade)."""
        with self._trava:
            self.verificar_inatividade()
            if self._banco is None or self._token is None:
                raise ErroHttp(423, BLOQUEADO)
            if not token or not hmac.compare_digest(token.encode("utf-8", "replace"),
                                                    self._token.encode("utf-8")):
                raise ErroHttp(401, SESSAO_INVALIDA)
            if marcar:
                self._marcar_atividade()
            return self._banco

    def _abrir_sessao(self, banco: Banco) -> str:
        banco.ao_salvar = self._salvar
        self._banco = banco
        self._token = secrets.token_urlsafe(32)
        self._marcar_atividade()
        return self._token

    # ------------------------------------------------------------ PIN: bloqueio temporário (seção 19)

    def espera_do_pin(self) -> float:
        """Segundos até o próximo PIN principal poder ser conferido (0 = já pode)."""
        return self._espera_pin.restante()

    def espera_do_pin_ia(self) -> float:
        """Segundos até o próximo PIN da IA poder ser conferido (0 = já pode)."""
        return self._espera_pin_ia.restante()

    def _pin_errado(self, espera: _Espera, falhas: int, mensagem: str) -> ErroHttp:
        """Depois da `falhas`-ésima tentativa errada seguida: bloqueia por 1 s, 2 s, 4 s... e
        responde 401 já dizendo quanto falta (`espere_segundos`), para a tela contar."""
        atraso = atraso_do_pin(falhas, self.atraso_pin, self.atraso_pin_maximo)
        espera.adiar(atraso)
        return ErroHttp(401, mensagem, extras={"espere_segundos": segundos_de_espera(atraso)} if atraso > 0 else None)

    def _conferir_pin(self, acao: Callable[[], Any], mensagem: str = "PIN incorreto.") -> Any:
        """Roda `acao`, que confere o PIN principal. Errado: 401 e bloqueio temporário.

        Dentro do bloqueio, 429 sem rodar `acao` (nem o scrypt). A trava faz a conferência e o
        teste do PIN acontecerem juntos: tentativas em paralelo não testam mais de um PIN.
        """
        with self._trava_pin:
            self._espera_pin.conferir()
            try:
                resultado = acao()
            except PinErrado:
                self._falhas_pin += 1
                erro = self._pin_errado(self._espera_pin, self._falhas_pin, mensagem)
                self._gravar_espera()            # antes de responder: recarregar não zera a espera
                raise erro from None
            self._falhas_pin = 0
            self._comparacoes_pin = 0            # quem acertou o PIN principal não precisa esperar
            if self._espera_no_disco:
                self._gravar_espera()
            return resultado

    def _igual_ao_pin_principal(self, pin: str) -> bool:
        """O PIN (da IA) é igual ao principal, ou uma variação óbvia dele (maiúsculas, espaço
        no fim, espaços repetidos)? Cada resposta conta algo sobre o PIN principal: depois de
        COMPARACOES_SEM_ESPERA pedidos, cada comparação bloqueia como um PIN errado (o mesmo
        bloqueio do PIN principal). Assim quem achar o app aberto não descobre o PIN principal
        criando ou trocando o PIN da IA muitas vezes."""
        with self._trava_pin:
            self._espera_pin.conferir()
            self._comparacoes_pin += 1
            igual = any(self.cofre.confere_pin(v) for v in _variacoes_do_pin(pin))
            atraso = atraso_do_pin(self._comparacoes_pin - COMPARACOES_SEM_ESPERA + 1,
                                   self.atraso_pin, self.atraso_pin_maximo)
            if atraso > 0:
                self._espera_pin.adiar(atraso)
                self._gravar_espera()
            return igual

    def _senha_igual_ao_pin(self, senha: str) -> bool:
        """A senha de sincronização nova é o PIN principal (como foi digitada, ou sem espaços nas
        pontas e repetidos)? Um ou dois scrypt, só ao CRIAR a senha. É um oráculo do PIN como o
        "PIN da IA igual ao principal": conta nas mesmas comparações e, com o PIN principal em
        bloqueio, não compara (não avisa) em vez de responder 429."""
        base = _nfc(senha)
        formas = [f for f in dict.fromkeys((base, re.sub(r"\s+", " ", base).strip())) if f]
        with self._trava_pin:
            if self._espera_pin.restante() > 0:
                return False
            self._comparacoes_pin += 1
            try:
                igual = any(self.cofre.confere_pin(f) for f in formas)
            except CofreFechado:
                return False
            atraso = atraso_do_pin(self._comparacoes_pin - COMPARACOES_SEM_ESPERA + 1,
                                   self.atraso_pin, self.atraso_pin_maximo)
            if atraso > 0:
                self._espera_pin.adiar(atraso)
                self._gravar_espera()
            return igual

    def _conferir_pin_ia(self, verificador: dict, pin: Any, mensagem: str = "PIN da IA incorreto.") -> None:
        """Como `_conferir_pin`, para o PIN da IA, com trava, contador e bloqueio próprios."""
        with self._trava_pin_ia:
            self._espera_pin_ia.conferir()
            if not conferir_verificador(pin, verificador):
                self._falhas_pin_ia += 1
                erro = self._pin_errado(self._espera_pin_ia, self._falhas_pin_ia, mensagem)
                self._gravar_espera()
                raise erro
            self._falhas_pin_ia = 0
            if self._espera_no_disco:
                self._gravar_espera()

    def _zerar_pin_ia(self) -> None:
        """O PIN da IA mudou (criado, apagado, veio de um backup): erros do antigo não contam."""
        with self._trava_pin_ia:
            self._falhas_pin_ia = 0
            self._espera_pin_ia.liberar()
            if self._espera_no_disco:
                self._gravar_espera()

    def _zerar_pin_principal(self) -> None:
        """Diário novo (configurar): erros de PIN de antes não contam."""
        with self._trava_pin:
            self._falhas_pin = 0
            self._espera_pin.liberar()
            if self._espera_no_disco:
                self._gravar_espera()

    # ------------------------------------------------------------ PIN: a espera guardada (seção 19.7)

    def _ler_espera_gravada(self) -> None:
        """Ao abrir: retoma o bloqueio do PIN de `pin_espera.json` (feito por `_gravar_espera`).

        No celular, recarregar a página ou o sistema fechar o app criava outro núcleo, com o
        contador zerado: o próximo PIN era testado na hora. Agora:
        - o horário de liberação gravado (relógio de parede) continua valendo;
        - mesmo que já tenha passado, quem abre espera de novo `atraso_do_pin(falhas)`: adiantar a
          hora do aparelho não pula a espera, e cada reabertura custa um degrau inteiro;
        - relógio de parede antes do último registro (alguém atrasou a hora): o teto.
        Arquivo estranho ou ilegível é ignorado (ele não guarda segredo nenhum; quem consegue
        apagá-lo também consegue copiar o .cofre e testar PINs fora do app).
        """
        try:
            with open(self._arquivo_espera, encoding="utf-8") as f:
                dados = json.loads(f.read(64 * 1024))
        except FileNotFoundError:
            return
        except (OSError, ValueError, RecursionError):
            return
        self._espera_no_disco = True             # o que estiver lá sai na próxima gravação
        if not isinstance(dados, dict):
            return
        agora = self.relogio_parede()
        visto = _numero_finito(dados.get("visto"))
        hora_voltou = visto is not None and agora < visto - TOLERANCIA_RELOGIO
        for qual, espera in (("principal", self._espera_pin), ("ia", self._espera_pin_ia)):
            parte = dados.get(qual)
            if not isinstance(parte, dict):
                continue
            falhas = parte.get("falhas")
            falhas = falhas if isinstance(falhas, int) and not isinstance(falhas, bool) and 0 <= falhas <= 10 ** 6 else 0
            livre_em = _numero_finito(parte.get("livre_em"))
            restante = self.atraso_pin_maximo if hora_voltou else (livre_em - agora if livre_em is not None else 0.0)
            restante = max(restante, atraso_do_pin(falhas, self.atraso_pin, self.atraso_pin_maximo))
            if qual == "principal":
                self._falhas_pin = falhas
            else:
                self._falhas_pin_ia = falhas
            espera.adiar(min(restante, self.atraso_pin_maximo))

    def _gravar_espera(self) -> None:
        """Grava em `pin_espera.json` os contadores e até quando cada PIN fica bloqueado, ou apaga
        o arquivo quando não sobra nada. Nunca recria a pasta de dados apagada (seção 18.6), e
        falhar aqui não atrapalha o PIN (fica no erros.log, só o tipo)."""
        with self._trava_espera:
            try:
                agora = self.relogio_parede()
                partes: dict[str, Any] = {}
                for qual, falhas, espera in (("principal", self._falhas_pin, self._espera_pin),
                                             ("ia", self._falhas_pin_ia, self._espera_pin_ia)):
                    restante = espera.restante()
                    if falhas > 0 or restante > 0:
                        partes[qual] = {"falhas": falhas, "livre_em": round(agora + restante, 3)}
                if not partes:
                    if self._espera_no_disco:
                        self._arquivo_espera.unlink(missing_ok=True)
                        self._espera_no_disco = False
                    return
                if not self.pasta.is_dir():
                    return
                conteudo = json.dumps({**partes, "visto": round(agora, 3)})
                temporario = self._arquivo_espera.with_name(f"{ARQUIVO_ESPERA_PIN}.tmp-{secrets.token_hex(4)}")
                try:
                    with open(temporario, "x", encoding="utf-8") as f:
                        f.write(conteudo)
                    os.replace(temporario, self._arquivo_espera)
                except BaseException:
                    temporario.unlink(missing_ok=True)
                    raise
                self._espera_no_disco = True
            except Exception as erro:  # noqa: BLE001 - a espera em memória continua valendo
                registrar_erro(erro)

    def _confere_pin_principal(self, pin: str, mensagem: str) -> None:
        """PIN principal digitado de novo (com o cofre aberto), com o bloqueio do PIN principal."""
        def conferir() -> None:
            if not self.cofre.confere_pin(pin):
                raise PinErrado("PIN incorreto.")
        self._conferir_pin(conferir, mensagem)

    # ------------------------------------------------------------ área da IA

    def area_ia_aberta(self) -> bool:
        with self._trava:
            desde = self._area_ia_desde
            if desde is not None and (self._banco is None or self._tempo_desde(*desde) >= self.minutos_area_ia * 60):
                self._area_ia_desde = None
            return self._area_ia_desde is not None

    def segundos_da_area_ia(self) -> int | None:
        """Segundos até a área da IA se fechar sozinha; None se está fechada."""
        with self._trava:
            if not self.area_ia_aberta() or self._area_ia_desde is None:
                return None
            return max(0, int(self.minutos_area_ia * 60 - self._tempo_desde(*self._area_ia_desde) + 0.999))

    def _abrir_area_ia(self) -> None:
        with self._trava:
            if self._banco is not None:
                self._area_ia_desde = (self.relogio(), self.relogio_parede())

    def fechar_area_ia(self) -> None:
        with self._trava:
            self._area_ia_desde = None

    def _exigir_ia_disponivel(self) -> None:
        """Os botões ✨ e o "Buscar modelos"/"Testar" falam com a IA pela rede, o que no navegador
        (Pyodide) ainda não dá: 503 com o motivo, em vez de um "tempo esgotado" enganoso."""
        if not self.ia_disponivel:
            raise ErroHttp(503, IA_SO_NO_COMPUTADOR, extras={"ia_indisponivel": True})

    def _exigir_area_ia(self) -> None:
        if not self.area_ia_aberta():
            raise ErroHttp(403, AREA_IA_FECHADA, extras={"pin_ia": True})

    def _sessao_e_area_ia(self, token: str | None) -> dict:
        """Para as rotas LIVRE da área da IA: confere sessão e área, devolve os ajustes."""
        with self._trava:
            banco = self._exigir_sessao(token)
            self._exigir_area_ia()
            return banco.config()

    # ------------------------------------------------------------ diário que existe, falta ou está danificado

    def primeiro_uso(self) -> bool:
        """Ainda não existe diário aqui (nem aberto): hora de configurar ou restaurar."""
        with self._trava:
            return self._banco is None and not self.cofre.existe()

    def diario_danificado(self) -> bool:
        """O arquivo do diário existe, nada está aberto, e ele não tem como abrir com PIN nenhum
        (cortado, cabeçalho estranho): dá para restaurar um backup sem o PIN dele (seção 18.6)."""
        with self._trava:
            return self._banco is None and self.cofre.danificado()

    def _sem_o_arquivo_do_diario(self) -> str:
        """Mensagem do desbloqueio quando o entrenos.cofre não está na pasta (seção 18.6). No
        navegador não há antivírus nem pasta para mostrar: quem apaga é o próprio navegador."""
        if self.plataforma != COMPUTADOR:
            return ("O diário não está mais guardado neste navegador: ele pode ter apagado os dados do Entre Nós "
                    "(para liberar espaço, ou ao limpar os dados do site). Toque em “Ver as opções” para restaurar um "
                    "backup (uma cópia que você baixou) ou começar um diário novo.")
        mensagem = (f"O arquivo do diário (entrenos.cofre) não está na pasta {self.pasta}. Se você não o "
                    "apagou, o antivírus pode tê-lo colocado em quarentena: peça para restaurá-lo (não "
                    "desligue o antivírus).")
        copias = self.cofre.resumo_das_copias()
        if copias:
            mensagem += (f" Há {copias['quantas']} {'cópia' if copias['quantas'] == 1 else 'cópias'} em "
                         f"{copias['pasta']}: para usar uma, toque em “Ver as opções” e escolha “Já tenho um backup”.")
        else:
            mensagem += " Ou toque em “Ver as opções” para criar um diário ou restaurar um backup."
        return mensagem

    # ------------------------------------------------------------ rotas públicas

    def _ping(self, p: Pedido) -> dict:
        return {"app": "entre-nos", "versao": config.VERSAO}

    def _estado(self, p: Pedido) -> dict:
        with self._trava:
            self.verificar_inatividade()
            banco = self._banco
            # Bloqueado, os ajustes (e com eles a IA) estão dentro do cofre fechado.
            ia = (_info_ia(banco.config(), self.ia_disponivel) if banco is not None
                  else {"configurada": None, "utilizavel": None, "sai_do_computador": None, "espera_maxima": None})
            configurado = self.cofre.existe()
            danificado = banco is None and configurado and self.cofre.danificado()
            # Sem diário (ou com o arquivo danificado): quantas cópias de um diário que esteve aqui
            # existem em backups/ e onde, para a tela não deixar a pessoa começar outro às cegas.
            copias = self.cofre.resumo_das_copias() if not configurado or danificado else None
            if copias is not None and self.plataforma != COMPUTADOR:
                copias = {**copias, "pasta": None}       # a pasta do Emscripten não serve para a pessoa
            # plataforma, backup_em_pasta e limite_arquivo: para a página se ajustar ao celular (19.7).
            return {"configurado": configurado, "desbloqueado": banco is not None,
                    "versao": config.VERSAO, "ia": ia, "danificado": danificado, "copias_anteriores": copias,
                    "plataforma": self.plataforma, "backup_em_pasta": self.backup_em_pasta,
                    "limite_arquivo": self.limite_corpo_arquivo}

    def _configurar(self, p: Pedido) -> dict:
        pin = p.corpo.get("pin")
        problema = problema_do_pin(pin)
        if problema:
            raise ErroHttp(400, problema)
        valores = {}
        for chave in ("nome_dela", "inicio_namoro", "aniversario_dela"):
            valor = p.corpo.get(chave)
            if valor not in (None, ""):
                valores[chave] = _nfc(valor) if isinstance(valor, str) else valor
        for chave in ("inicio_namoro", "aniversario_dela"):
            _data(valores.get(chave), chave)
        with self._trava:
            if self._encerrando:
                raise ErroHttp(503, FECHANDO)
            if self.cofre.existe():
                raise ErroHttp(409, JA_CONFIGURADO)
            # Cópias de um diário que sumiu ficam de lado, fora do rodízio do diário novo (18.6).
            self.cofre.separar_copias_anteriores()
            banco = Banco.novo()
            try:
                if valores:
                    banco.salvar_config(**valores)
                self.cofre.criar(pin, banco.para_bytes())
                banco.marcar_salvo()
            except FileExistsError:
                banco.fechar()
                raise ErroHttp(409, JA_CONFIGURADO) from None
            except BaseException:
                banco.fechar()
                raise
            self._zerar_pin_principal()          # diário novo: erros de PIN de antes não contam
            return {"token": self._abrir_sessao(banco)}

    def _desbloquear(self, p: Pedido) -> dict:
        pin = p.corpo.get("pin")
        if self._encerrando:
            raise ErroHttp(503, FECHANDO)
        if not self.cofre.existe():
            raise ErroHttp(409, self._sem_o_arquivo_do_diario(), extras={"sem_diario": True})
        if self.cofre.danificado():
            # Nenhum PIN abriria: não gasta tentativa nem bloqueia (seção 18.6).
            raise ErroHttp(409, DIARIO_DANIFICADO, extras={"danificado": True})
        dados = self._conferir_pin(lambda: self.cofre.abrir(pin))
        with self._trava:
            self.verificar_inatividade()
            if self._banco is None:
                try:
                    banco = Banco.de_bytes(dados)
                except ValueError as erro:
                    self.cofre.fechar()
                    raise ErroHttp(500, f"{erro} Use uma cópia de segurança.") from None
                return {"token": self._abrir_sessao(banco)}
            # Já estava aberto (outra aba): vale o mesmo token.
            self._marcar_atividade()
            return {"token": self._token}

    # ------------------------------------------------------------ sessão

    def _bloquear(self, p: Pedido, banco: Banco) -> dict:
        self.bloquear()
        return {}

    def _analisar(self, p: Pedido) -> Any:
        """Sempre só as regras, com ou sem IA configurada (seção 17.1)."""
        texto = _texto(p.corpo, "texto", "Escreva alguma coisa antes de organizar.")
        referencia = _data(p.corpo.get("data_registro"), "data_registro") or self.hoje()
        conhecimento_do_banco = _funcao("motor", "conhecimento_do_banco")
        motor_regras = _funcao("motor", "MotorRegras")
        with self._trava:
            banco = self._exigir_sessao(p.token)
            conhecimento = conhecimento_do_banco(banco)
        proposta = motor_regras().analisar(texto, referencia, conhecimento)
        with self._trava:
            banco = self._exigir_sessao(p.token)
            proposta.relacoes = self._relacionar(proposta, banco, referencia)
        return proposta

    @staticmethod
    def _relacionar(proposta: Any, banco: Banco, referencia: date) -> list:
        try:
            relacionar = _funcao("contexto", "relacionar")
            return list(relacionar(proposta, banco, referencia))
        except Exception as erro:  # noqa: BLE001 - sem relações, a análise continua valendo
            if not isinstance(erro, ErroHttp):
                registrar_erro(erro)
            proposta.avisos.append("Não consegui procurar ligações com registros antigos desta vez.")
            return list(proposta.relacoes or [])

    def _criar_registro(self, p: Pedido, banco: Banco) -> dict:
        """Grava o registro revisado e, na mesma transação, os `extras` confirmados de cada recurso
        (seção 20.0). Extra de recurso que pede 🔒 (o ciclo) deixa o registro fora da IA, com aviso.

        Resposta: {"id"} e, quando for o caso, "privado": true (virou 🔒 por causa de um extra) e
        "avisos" (a parte de um recurso que não deu para guardar)."""
        c = p.corpo
        texto = _texto(c, "texto", "O registro está vazio.")
        data_registro = _data(c.get("data_registro"), "data_registro") or self.hoje()
        itens = _lista_de_objetos(c, "itens")
        for item in itens:
            trecho = item.get("trecho")
            if isinstance(trecho, str):
                item["trecho"] = trecho = _nfc(trecho)
            # Nunca inventar: todo item cita um pedaço literal do texto.
            if not isinstance(trecho, str) or not trecho.strip() or trecho not in texto:
                raise ErroHttp(400, "Cada item precisa citar um trecho que está no texto do registro.")
        extras = recursos.extras_confirmados(c.get("extras"), texto)
        forcado = recursos.recursos_que_marcam_privado(extras)
        # O texto também decide, como no 403 do ✨ Analisar: algo que um recurso deixa fora da IA
        # (desentendimento, resposta de "Nós dois", o ciclo) e ficou sem confirmar ou com "Não guardar"
        # deixa o registro 🔒 do mesmo jeito (seção 20.10). O aviso da revisão já dizia isso.
        if not forcado and texto_fica_fora_da_ia(texto, data_registro, banco, na_duvida=False):
            forcado = ["texto"]
        privado = _privado(c) or bool(forcado)
        with banco.transacao():
            rid = banco.criar_registro(texto, data_registro, itens, _lista_de_objetos(c, "pessoas"),
                                       _lista_de_objetos(c, "relacoes"), _texto_opcional(c, "contexto"),
                                       _texto_opcional(c, "humor"), privado=privado)
            avisos = recursos.gravar_extras(banco, rid, extras, data_registro)
        resposta: dict[str, Any] = {"id": rid}
        if forcado:
            resposta["privado"] = True
            avisos.insert(0, recursos.AVISO_PRIVADO_GRAVADO)
        if avisos:
            resposta["avisos"] = avisos
        return resposta

    def _marcar_privado(self, p: Pedido, banco: Banco) -> dict:
        """PATCH /api/registros/{id} {privado}: marca ou desmarca "não enviar para a IA"."""
        campos = _campos_editaveis(p.corpo)
        outros = sorted(set(campos) - {"privado"})
        if outros:
            raise ErroHttp(400, "Campos que não podem ser alterados: " + ", ".join(outros))
        if "privado" not in campos:
            raise ErroHttp(400, "Informe se o registro pode ir para a IA (“privado”).")
        banco.marcar_privado(p.id, _privado(campos))
        return {}

    def _listar_registros(self, p: Pedido, banco: Banco) -> list:
        return banco.registros(p.inteiro("limite", 50, 1, 1000), p.inteiro("antes_de", None, 1, 10 ** 12))

    def _ver_registro(self, p: Pedido, banco: Banco) -> dict:
        registro = banco.registro(p.id)
        if registro is None:
            raise ErroHttp(404, f"Registro #{p.id} não encontrado.")
        # Um recurso deixa este registro fora da IA (o ciclo, a área Eu; seção 20.0), com ou sem a
        # caixinha 🔒: a tela trava a caixinha. None = não deu para saber agora.
        try:
            registro["fora_da_ia"] = (registro["id"] in recursos.fora_da_ia(banco)
                                      or bool(fora_da_ia_pelo_texto(banco, [registro["id"]])))
        except (recursos.RecursoIndisponivel, ErroHttp):
            registro["fora_da_ia"] = None
        return registro

    def _limpar_backups(self, apagar: Callable[[Banco], bool | None]) -> None:
        """Repete nas cópias automáticas a remoção que acabou de ser feita.

        Sem isto, o registro apagado continuaria abrindo com o mesmo PIN numa cópia de
        backups/. Os ids nunca se repetem, então apagar pelo mesmo id é seguro. `apagar`
        devolvendo False (ou levantando NaoEncontrado) = nada mudou nessa cópia.
        """
        def transformar(dados: bytes) -> bytes | None:
            try:
                copia = Banco.de_bytes(dados)
            except ValueError:
                return None     # cópia danificada ou de outra versão: fica como está
            try:
                try:
                    if apagar(copia) is False:
                        return None
                except NaoEncontrado:
                    return None
                return copia.para_bytes()
            finally:
                copia.fechar()

        try:
            self.cofre.reescrever_backups(transformar)
        except Exception as erro:  # noqa: BLE001 - a remoção principal já valeu
            registrar_erro(erro)

    # A configuração da IA também sai das cópias: a chave e o verificador do PIN da IA abrem
    # com o PIN principal, então ficariam legíveis ali por até BACKUPS_AUTOMATICOS dias.

    @staticmethod
    def _backup_sem_chave_antiga(copia: Banco, qual: str, chave_atual: str | None) -> bool:
        """Numa cópia: a IA `qual` perde a chave que não é mais a salva."""
        ia = copia.config().get(f"ia_{qual}")
        if not isinstance(ia, dict) or not ia.get("chave") or ia.get("chave") == chave_atual:
            return False
        try:
            copia.salvar_config(**{f"ia_{qual}": {**ia, "chave": None}})
        except ValueError:
            copia.salvar_config(**{f"ia_{qual}": None})     # configuração velha estranha: sai inteira
        return True

    @staticmethod
    def _backup_sem(copia: Banco, **valores: Any) -> bool:
        """Numa cópia: os ajustes da IA passam a `valores` (só os que estão diferentes)."""
        ajustes = copia.config()
        mudar = {k: v for k, v in valores.items() if ajustes.get(k) != v}
        if not mudar:
            return False
        copia.salvar_config(**mudar)
        return True

    def _apagar_registro(self, p: Pedido, banco: Banco) -> dict:
        banco.apagar_registro(p.id)
        self._limpar_backups(lambda b: b.apagar_registro(p.id))
        return {}

    def _editar_item(self, p: Pedido, banco: Banco) -> dict:
        banco.atualizar_item(p.id, **_campos_editaveis(p.corpo))
        return {}

    def _apagar_item(self, p: Pedido, banco: Banco) -> dict:
        banco.apagar_item(p.id)
        self._limpar_backups(lambda b: b.apagar_item(p.id))
        return {}

    def _hoje(self, p: Pedido, banco: Banco) -> dict:
        referencia = _data(p.parametro("data"), "data") or self.hoje()
        return _funcao("alertas", "hoje")(banco, referencia)

    def _dispensar_alerta(self, p: Pedido, banco: Banco) -> dict:
        chave = p.corpo.get("chave")
        if not isinstance(chave, str) or not chave.strip():
            raise ErroHttp(400, "Informe qual aviso dispensar.")
        banco.dispensar_alerta(chave)
        return {}

    def _perfil(self, p: Pedido, banco: Banco) -> dict:
        return _funcao("perfil", "conhecendo_ela")(banco, self.hoje())

    def _listar_pessoas(self, p: Pedido, banco: Banco) -> list:
        return banco.pessoas()

    def _criar_pessoa(self, p: Pedido, banco: Banco) -> dict:
        c = p.corpo
        aniversario = _data(c.get("aniversario"), "aniversario")
        pid = banco.criar_pessoa(_texto_opcional(c, "nome"), _texto_opcional(c, "relacao"),
                                 c.get("apelidos") or [], _texto_opcional(c, "observacoes") or "")
        if aniversario is not None:
            banco.atualizar_pessoa(pid, aniversario=aniversario)
        return {"id": pid}

    def _editar_pessoa(self, p: Pedido, banco: Banco) -> dict:
        campos = _campos_editaveis(p.corpo)
        if "aniversario" in campos:
            campos["aniversario"] = _data(campos["aniversario"], "aniversario")
        banco.atualizar_pessoa(p.id, **campos)
        return {}

    def _apagar_pessoa(self, p: Pedido, banco: Banco) -> dict:
        banco.apagar_pessoa(p.id)
        self._limpar_backups(lambda b: b.apagar_pessoa(p.id))
        return {}

    def _datas(self, p: Pedido, banco: Banco) -> list:
        dias = p.inteiro("dias", 365, 1, 3660)
        return _funcao("alertas", "proximos_eventos")(banco, self.hoje(), dias)

    @staticmethod
    def _pergunta(valor: Any) -> str:
        pergunta = _nfc(valor.strip()) if isinstance(valor, str) else ""
        if not pergunta:
            raise ErroHttp(400, "Escreva uma pergunta.")
        if len(pergunta) > MAX_PERGUNTA:
            raise ErroHttp(400, f"A pergunta pode ter até {MAX_PERGUNTA} caracteres.")
        return pergunta

    def _busca(self, p: Pedido, banco: Banco) -> Any:
        """Sempre só as regras (a IA responde só pelo botão, em POST /api/ia/busca).

        Cada achado diz se é de registro 🔒 (`privado`), e `para_ia` diz se o ✨ Perguntar à IA
        teria algum trecho para mandar (senão a tela nem mostra o botão).
        """
        pergunta = self._pergunta(p.parametro("q") or "")
        buscar = _funcao("busca", "buscar")
        resposta = para_json(buscar(pergunta, banco, None, self.hoje()))
        # 🔒 = fica fora da IA: os marcados e os que os recursos deixam de fora (seção 20.0). Sem
        # saber com segurança o que fica de fora, o ✨ Perguntar à IA nem aparece.
        encontrados = [e for e in (resposta.get("encontrados") or []) if isinstance(e, dict)]
        try:
            privados, seguro = fora_da_ia(banco), True
            # O texto também decide (seção 20.10): o que um recurso deixaria fora sem confirmação.
            privados |= fora_da_ia_pelo_texto(banco, [e.get("registro_id") for e in encontrados], privados)
        except ErroHttp:
            privados, seguro = banco.registros_privados(), False
        for e in encontrados:
            e["privado"] = e.get("registro_id") in privados
        para_ia = seguro and any(not e["privado"] for e in encontrados)
        if seguro and encontrados and not para_ia:
            # Os mais relevantes são todos 🔒: a busca para a IA (sem eles) acharia outra coisa?
            para_ia = bool(buscar(pergunta, banco, None, self.hoje(), privados=privados).encontrados)
        resposta["para_ia"] = para_ia
        return resposta

    def _resolver_relacao(self, p: Pedido, banco: Banco) -> dict:
        acao = p.corpo.get("acao")
        situacoes = {"confirmar": "confirmada", "descartar": "descartada"}
        if acao in situacoes:
            banco.resolver_relacao(p.id, situacoes[acao])
            return {}
        if acao != "atualizar_preferencia":
            raise ErroHttp(400, "Ação desconhecida: use confirmar, descartar ou atualizar_preferencia.")
        relacao = next((r for r in banco.relacoes(status=None) if r.id == p.id), None)
        if relacao is None:
            raise ErroHttp(404, f"Relação #{p.id} não encontrada.")
        if relacao.item_antigo_id is None:
            raise ErroHttp(400, "Esta relação não aponta para uma informação antiga.")
        self._preferencia_nova(banco, relacao)
        # A antiga não some: fica marcada como "mudou".
        banco.atualizar_item(relacao.item_antigo_id, status="mudou")
        banco.resolver_relacao(p.id, "confirmada")
        return {}

    @staticmethod
    def _preferencia_nova(banco: Banco, relacao: Any) -> None:
        """Contradição vinda de uma ação ("Ela pediu sushi no jantar e amou", que é nota ou
        evento): atualizar precisa deixar a preferência nova no lugar da antiga, senão o gosto
        some do perfil. A certeza não sobe: fica observado (ou inferência, se era)."""
        antigo = banco.item(relacao.item_antigo_id)
        novo = banco.item(relacao.item_novo) if isinstance(relacao.item_novo, int) else None
        if (antigo is None or novo is None or antigo.tipo != TipoItem.PREFERENCIA or not antigo.alvo
                or antigo.polaridade not in (1, -1) or novo.tipo in (TipoItem.PREFERENCIA, TipoItem.RESTRICAO)):
            return
        polaridade = -antigo.polaridade
        campos = {
            "tipo": TipoItem.PREFERENCIA, "alvo": antigo.alvo, "polaridade": polaridade,
            "resumo": f"{'Gosta' if polaridade > 0 else 'Não gosta'} de {antigo.alvo}",
            "certeza": Certeza.INFERENCIA if novo.certeza == Certeza.INFERENCIA else Certeza.OBSERVADO,
            "categorias": list(dict.fromkeys([*antigo.categorias, *novo.categorias])),
        }
        if novo.tipo == TipoItem.NOTA:
            banco.atualizar_item(novo.id, **campos)          # a nota era a própria frase: vira a preferência
        else:
            banco.acrescentar_item(novo.registro_id, Item(trecho=novo.trecho, **campos))

    def _ver_config(self, p: Pedido, banco: Banco) -> dict:
        return _config_publica(banco.config())

    def _editar_config(self, p: Pedido, banco: Banco) -> dict:
        if any(_chave_da_ia(k) for k in p.corpo):
            raise ErroHttp(400, AJUSTES_DA_IA)
        if any(chave_da_sincronizacao(k) for k in p.corpo):
            raise ErroHttp(400, AJUSTES_DA_SINCRONIZACAO)
        campos = {k: (_nfc(v) if isinstance(v, str) else v) for k, v in p.corpo.items()}
        banco.salvar_config(**campos)
        return _config_publica(banco.config())

    def _trocar_pin(self, p: Pedido) -> dict:
        atual, novo = p.corpo.get("atual"), p.corpo.get("novo")
        if not isinstance(atual, str) or not atual:
            raise ErroHttp(400, "Digite o PIN atual.")
        problema = problema_do_pin(novo)
        if problema:
            raise ErroHttp(400, problema.replace("O PIN ", "O PIN novo ", 1))
        # Sem comparar o PIN novo com o PIN da IA: a resposta diria a quem sabe o PIN principal
        # se o palpite é o PIN da IA, sem o bloqueio do PIN da IA.
        self._conferir_pin(lambda: self.cofre.trocar_pin(atual, novo), "O PIN atual não confere.")
        return {}

    def _backup(self, p: Pedido, banco: Banco) -> dict:
        if not self.backup_em_pasta:
            # No navegador, qualquer caminho fora da pasta montada no IDBFS fica só na memória e some
            # ao recarregar: a resposta diria "copiado" sem cópia nenhuma (seção 19.7).
            raise ErroHttp(409, BACKUP_SO_BAIXANDO, extras={"baixar": True})
        pasta = p.corpo.get("pasta")
        if not isinstance(pasta, str) or not pasta.strip():
            raise ErroHttp(400, "Informe a pasta onde guardar a cópia.")
        destino = Path(os.path.expanduser(pasta.strip()))
        if not destino.is_absolute():
            raise ErroHttp(400, "Informe o caminho completo da pasta, como E:\\ ou D:\\Backups.")
        self._garantir_salvo(banco)             # a cópia sai com o que está na tela agora
        try:
            arquivo = self.cofre.copiar_backup(destino, self._conteudo_para_fora(banco))
        except CofreMudou:
            raise
        except NotADirectoryError:
            raise ErroHttp(400, "Essa pasta não existe.") from None
        except PermissionError:
            raise ErroHttp(400, "Sem permissão para gravar nessa pasta.") from None
        except FileExistsError as erro:
            raise ErroHttp(400, str(erro)) from None
        except OSError:
            raise ErroHttp(400, "Não consegui gravar a cópia nessa pasta.") from None
        return {"arquivo": str(arquivo)}

    def _baixar_copia(self, p: Pedido, banco: Banco) -> dict:
        """POST /api/backup/baixar: o .cofre de agora (cifrado, já salvo) para a página baixar ou
        compartilhar. É o backup do celular, onde não há pasta para escolher (seção 19.7); no
        computador também funciona. {nome, tamanho, arquivo_b64}."""
        nome, conteudo = self.copia_do_diario(banco)
        return {"nome": nome, "tamanho": len(conteudo), "arquivo_b64": base64.b64encode(conteudo).decode("ascii")}

    def baixar_copia(self, token: str | None) -> tuple[RespostaApi, bytes | None]:
        """POST /api/backup/baixar com o arquivo em bytes, fora do JSON (a ponte do navegador): no
        celular o base64 dentro do JSON fazia o .cofre inteiro virar texto 33% maior no Python, na
        string JSON, no JSON.parse do Worker e no postMessage (22.2; o wasm não devolve memória).
        Devolve (resposta {nome, tamanho}, bytes do .cofre) ou (erro, None). Nunca levanta."""
        try:
            self.verificar_inatividade()
            with self._trava:
                banco = self._exigir_sessao(token)
                try:
                    nome, conteudo = self.copia_do_diario(banco)
                finally:
                    if self._banco is banco:
                        self._marcar_atividade()
            dados = {"nome": nome, "tamanho": len(conteudo)}
            return RespostaApi(200, dados, {}, _json_bytes(dados)), conteudo
        except Exception as erro:  # noqa: BLE001 - todo erro vira resposta JSON
            return resposta_de_erro(erro), None

    def copia_do_diario(self, banco: Banco) -> tuple[str, bytes]:
        """(nome sugerido, bytes do .cofre) com o que está aberto agora, salvo antes. Chame com a
        trava do app e o banco da sessão. O arquivo é o mesmo que /api/restaurar aceita (e abre com o
        mesmo PIN); sai sem a chave da sincronização (`_conteudo_para_fora`)."""
        self._garantir_salvo(banco)
        return f"entrenos-backup-{self.hoje():%Y-%m-%d}.cofre", self._conteudo_para_fora(banco)

    def _conteudo_para_fora(self, banco: Banco) -> bytes | None:
        """O .cofre de uma cópia que SAI do aparelho (baixar, compartilhar, pendrive): sem a chave da
        sincronização, que antes ia junto, protegida só pelo PIN; quem quebrasse o PIN de uma cópia
        mandada por WhatsApp leria também os .sync do Drive (22.2). Restaurada, a cópia pede a senha
        de sincronização de novo. Sem sincronização ligada: o arquivo como está no disco."""
        sync = _importar("sincronizacao")
        aj = sync.ajustes(banco)
        if not aj.get("chave"):
            return self.cofre.conteudo()
        copia = Banco.de_bytes(banco.para_bytes())
        try:
            copia.salvar_config(sync=sync.para_fora(aj))
            return self.cofre.cifrar_dados(copia.para_bytes())
        finally:
            copia.fechar()

    def _garantir_salvo(self, banco: Banco) -> None:
        """O arquivo do diário com tudo o que está aberto: grava só se faltou salvar algo (ou se o
        arquivo sumiu da pasta)."""
        if getattr(banco, "sujo", True) or not self.cofre.existe():
            self._salvar(banco.para_bytes())
            banco.marcar_salvo()

    def _exportar(self, p: Pedido, banco: Banco) -> dict:
        dados = banco.exportar()          # já sem chave de IA e sem PIN da IA
        if isinstance(dados.get("config"), dict):
            dados["config"] = _config_publica(dados["config"])      # e sem qual IA (área do PIN da IA)
        return dados

    def _historico(self, p: Pedido, banco: Banco) -> list:
        return banco.historico(p.inteiro("limite", 100, 1, 1000))

    def _sair(self, p: Pedido) -> dict:
        """Salva e encerra. Quem desliga (o servidor, a página) é o transporte, depois de responder."""
        self.encerrar()
        p.encerrar = True
        return {}

    # ------------------------------------------------------------ restaurar e importar (seção 18.2)

    def restaurar_arquivo(self, arquivo: bytes, *, corpo: bytes | None = None,
                          token: str | None = None) -> RespostaApi:
        """/api/restaurar com o arquivo já em bytes, fora do JSON: a ponte do navegador recebe o
        Uint8Array do arquivo escolhido, sem base64 (que custava 4/3 do arquivo em texto e mais as
        cópias da leitura do JSON, seção 19.7). `corpo`: o JSON com {pin[, pin_atual,
        substituir_danificado]}. Mesmas regras, respostas e limite que a rota. Nunca levanta."""
        try:
            self.verificar_inatividade()
            if not isinstance(arquivo, (bytes, bytearray)):
                raise ErroHttp(400, "Escolha o arquivo do backup (.cofre).")
            if corpo is not None and len(corpo) > LIMITE_CORPO:
                raise ErroHttp(413, mensagem_de_limite(LIMITE_CORPO))
            if len(arquivo) > self.limite_corpo_arquivo:
                raise ErroHttp(413, mensagem_de_limite(self.limite_corpo_arquivo))
            pedido = Pedido("POST", "/api/restaurar", {}, bytes(corpo or b""), token)
            corpo = None
            dados = para_json(self._restaurar(pedido, arquivo=arquivo))
            bruto = _json_bytes(dados)
        except Exception as erro:  # noqa: BLE001 - todo erro vira resposta JSON
            return resposta_de_erro(erro)
        return RespostaApi(200, dados, {}, bruto)

    def _restaurar(self, p: Pedido, arquivo: bytes | None = None) -> dict:
        """POST /api/restaurar {arquivo_b64, pin[, pin_atual]}: troca o diário por um backup (.cofre).
        `arquivo`: o .cofre em bytes (`restaurar_arquivo`); aí o corpo não traz `arquivo_b64`.

        Primeiro uso (sem diário): pública, como /api/configurar. Com diário: pede o token e o
        PIN principal de agora (`pin_atual`, com o bloqueio do PIN errado). O arquivo é conferido
        inteiro antes de qualquer mudança (cabeçalho, PIN, banco com migração, integridade); só
        então o diário atual vai para backups/entrenos-antes-da-restauracao-...cofre e o backup
        entra no lugar, de uma vez. O PIN passa a ser o do backup, e os ajustes e a IA também;
        a sessão antiga morre e a área da IA fica fechada. Devolve {token, resumo, copia}.
        """
        if self._encerrando:
            raise ErroHttp(503, FECHANDO)
        # Três jeitos: primeiro uso (sem diário), diário danificado (existe, mas nenhum PIN abre:
        # pede `substituir_danificado: true`) e com o app aberto (token + PIN de agora).
        if self.primeiro_uso():
            modo = "primeiro_uso"
        elif self.diario_danificado():
            modo = "danificado"
        else:
            modo = "sessao"
            self._exigir_sessao(p.token)         # antes de ler o corpo
        c = p.corpo
        if modo == "danificado" and c.get("substituir_danificado") is not True:
            raise ErroHttp(409, DIARIO_DANIFICADO, extras={"danificado": True})
        if arquivo is None:
            conteudo = _arquivo_do_corpo(c.get("arquivo_b64"))
            c["arquivo_b64"] = None              # o texto em base64 (dezenas de MB) já virou bytes
        elif arquivo:
            conteudo = arquivo
        else:
            raise ErroHttp(400, "Escolha o arquivo do backup (.cofre).")
        del arquivo
        pin = c.get("pin")
        if not isinstance(pin, str) or not pin:
            raise ErroHttp(400, "Digite o PIN deste backup.")
        if modo == "sessao":
            pin_atual = c.get("pin_atual")
            if not isinstance(pin_atual, str) or not pin_atual:
                raise ErroHttp(400, "Digite o PIN atual do Entre Nós.")
            self._confere_pin_principal(pin_atual, "O PIN atual não confere.")
        recebido = self._abrir_backup(conteudo, pin)
        del conteudo
        try:
            novo = self._banco_do_backup(recebido)
            try:
                return self._trocar_pelo_backup(p, recebido, novo, modo)
            except BaseException:
                novo.ao_salvar = None
                novo.fechar()
                raise
        finally:
            recebido.descartar()

    def _abrir_backup(self, conteudo: bytes, pin: str) -> CofreRecebido:
        """Decifra o backup sem tocar no disco. Não é cofre (ou está cortado): 400 sem gastar
        tentativa de PIN; PIN errado: 401 com o bloqueio do PIN (o mesmo do desbloqueio)."""
        try:
            tamanho = tamanho_decifrado(conteudo)
        except CofreCorrompido:
            raise ErroHttp(400, NAO_E_BACKUP) from None
        # Dentro de um backup há um banco SQLite, cujo tamanho é múltiplo da página (512 bytes
        # ou mais): arquivo cortado no meio para aqui. O GCM sozinho não distinguiria de PIN errado.
        if tamanho <= 0 or tamanho % PAGINA_SQLITE_MINIMA:
            raise ErroHttp(400, NAO_E_BACKUP)

        def abrir() -> CofreRecebido:
            try:
                return abrir_conteudo(conteudo, pin)
            except CofreCorrompido:
                raise ErroHttp(400, NAO_E_BACKUP) from None
        return self._conferir_pin(abrir, PIN_DO_BACKUP)

    @staticmethod
    def _banco_do_backup(recebido: CofreRecebido) -> Banco:
        """O diário de dentro do backup, já migrado para o esquema atual e conferido. 400 se
        não é um diário do Entre Nós, se está danificado ou se é de uma versão mais nova."""
        try:
            novo = Banco.de_bytes(recebido.dados)
        except ValueError as erro:
            if str(erro) == DADOS_DE_VERSAO_NOVA:
                raise ErroHttp(400, "Este backup foi feito por uma versão mais nova do Entre Nós. "
                                    "Atualize o Entre Nós e tente de novo.") from None
            raise ErroHttp(400, BACKUP_DANIFICADO) from None
        except Exception as erro:  # noqa: BLE001 - banco estranho dentro de um cofre válido
            registrar_erro(erro)
            raise ErroHttp(400, BACKUP_DANIFICADO) from None
        try:
            novo.conferir_integridade()
        except BaseException as erro:
            novo.fechar()
            if isinstance(erro, ValueError):
                raise ErroHttp(400, BACKUP_DANIFICADO) from None
            raise
        return novo

    def _trocar_pelo_backup(self, p: Pedido, recebido: CofreRecebido, novo: Banco, modo: str) -> dict:
        """A troca em si, com a trava do app: nenhum pedido grava no diário antigo no meio.

        `modo`: "primeiro_uso", "danificado" ou "sessao" (ver `_restaurar`)."""
        with self._trava:
            if self._encerrando:
                raise ErroHttp(503, FECHANDO)
            antigo: Banco | None = None
            guardado: Path | None = None
            if modo == "primeiro_uso":
                if not self.primeiro_uso():
                    raise ErroHttp(409, JA_CONFIGURADO)      # configurou (ou restaurou) no meio
            elif modo == "danificado":
                if not self.diario_danificado():
                    raise ErroHttp(409, JA_CONFIGURADO)      # o arquivo mudou no meio
            else:
                antigo = self._exigir_sessao(p.token)       # bloqueou no meio: nada é trocado
                # Cópia antiga deste mesmo diário: os ids novos não podem repetir os que os backups
                # automáticos (mesmo PIN) já usaram, senão apagar um apagaria outro deles.
                novo.garantir_ids_acima(antigo.maiores_ids())
                # A cópia de antes da restauração sai do disco: o que só estava na memória (um
                # salvar que falhou, com o antivírus segurando o arquivo) vai para lá antes.
                try:
                    self._salvar(antigo.para_bytes())
                except OSError as erro:
                    registrar_erro(erro)
                    raise ErroHttp(500, NAO_SALVOU_ANTES) from None
            resumo = novo.resumo()
            try:
                if modo == "danificado":
                    guardado = self.cofre.guardar_danificado()
                if modo != "sessao":
                    self.cofre.separar_copias_anteriores()
                copia = self.cofre.restaurar(recebido, novo.para_bytes())
                novo.marcar_salvo()
            except OSError as erro:
                registrar_erro(erro)
                if guardado is not None and not self.cofre.existe():
                    try:
                        os.rename(guardado, self.cofre.arquivo)     # o danificado volta para o lugar
                    except OSError as outro:
                        registrar_erro(outro)
                raise ErroHttp(500, "Não consegui gravar o backup no lugar do diário. Nada foi trocado.") from None
            if guardado is not None:
                copia = guardado         # o arquivo danificado, guardado em backups/
            # Daqui em diante o arquivo já é o do backup e o cofre está aberto com a chave dele:
            # o token antigo morre, e a área da IA (e o PIN dela) passa a ser a do backup, fechada.
            self._banco = self._token = None
            self._area_ia_desde = None
            self._zerar_pin_ia()
            with self._trava_pin:
                self._comparacoes_pin = 0
            token = self._abrir_sessao(novo)
            # A sincronização do backup era do aparelho de onde ele veio: aqui ela ganha identidade
            # própria (senão dois aparelhos escreveriam o mesmo arquivo no Drive; seção 22).
            try:
                _importar("sincronizacao").depois_de_restaurar(novo, self.plataforma)
            except ErroHttp:
                raise
            except Exception as erro:  # noqa: BLE001 - a troca já valeu; sincronizar pede a senha de novo
                registrar_erro(erro)
            if antigo is not None:
                antigo.ao_salvar = None          # nada mais do diário antigo vai para o arquivo
                try:
                    antigo.fechar()
                except Exception as erro:  # noqa: BLE001 - a troca já valeu
                    registrar_erro(erro)
        return {"token": token, "resumo": resumo, "copia": self._caminho_para_tela(copia),
                "danificado": modo == "danificado"}

    def _caminho_para_tela(self, caminho: Path | None) -> str | None:
        """O caminho completo de um arquivo do app, para a pessoa achar (e copiar) no computador.
        No navegador a pasta é do Emscripten e não serve para nada fora dele: só o nome."""
        if caminho is None:
            return None
        return str(caminho) if self.plataforma == COMPUTADOR else Path(caminho).name

    @staticmethod
    def _exportacao_do_pedido(p: Pedido) -> Any:
        """O JSON de /api/exportar que veio como texto em {json}, lido e conferido campo a campo
        fora da trava do app (um arquivo grande não prende os outros pedidos nem o bloqueio)."""
        texto = p.corpo.get("json")
        if not isinstance(texto, str) or not texto.strip():
            raise ErroHttp(400, "Escolha o arquivo JSON exportado pelo Entre Nós.")
        p.corpo["json"] = None
        dados = ler_exportacao(texto)
        del texto
        return validar_exportacao(dados)

    def _importar_previa(self, p: Pedido) -> dict:
        """POST /api/importar/previa {json}: o resumo do que a importação faria. Nada é gravado."""
        exportacao = self._exportacao_do_pedido(p)
        with self._trava:
            return self._exigir_sessao(p.token).importar_json(exportacao, previa=True)

    def _importar(self, p: Pedido) -> dict:
        """POST /api/importar {json, confirmar: true}: junta ao diário, sem duplicar (seção 18.2)."""
        if p.corpo.get("confirmar") is not True:
            raise ErroHttp(400, "Confira o resumo e confirme a importação.")
        exportacao = self._exportacao_do_pedido(p)
        with self._trava:
            return self._exigir_sessao(p.token).importar_json(exportacao, previa=False)

    # ------------------------------------------------------------ sincronização (seção 22)
    #
    # A página (web/sincronizar.js) fala com o Google Drive; aqui só entram e saem arquivos já
    # cifrados com a chave de sincronização (que fica no diário e nunca sai dele). Fluxo de uma
    # sincronização: iniciar (confere o entrenos-sync.json do Drive) -> receber (cada arquivo de outro
    # aparelho que mudou) -> preparar (o arquivo deste) -> enviado (depois que o Drive aceitou).

    def _agora_utc(self) -> str:
        # Com microssegundos: é o instante dos carimbos da sincronização (sincronizacao.agora_utc).
        return datetime.fromtimestamp(self.relogio_parede(), timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _sync() -> Any:
        return _modulo("sincronizacao", "estado", "mesclar", "preparar", "ligar", "chaves_guardadas")

    @staticmethod
    def _erro_sync(sync: Any, erro: BaseException) -> ErroHttp:
        """Arquivo de outra versão: 409 {versao_nova}; arquivo que não abre ou estranho: 400
        {arquivo_invalido}; o resto como sempre."""
        if isinstance(erro, sync.ArquivoSyncNovo):
            return ErroHttp(409, str(erro), extras={"versao_nova": True})
        if isinstance(erro, sync.ArquivoSyncInvalido):
            return ErroHttp(400, str(erro), extras={"arquivo_invalido": True})
        return traduzir_erro(erro)

    def _sync_chaves(self, sync: Any, banco: Banco) -> tuple[dict, Any]:
        aj = sync.ajustes(banco)
        chaves = sync.chaves_guardadas(aj) if sync.configurada(aj) else None
        if chaves is None:
            raise ErroHttp(409, sync.SEM_SENHA, extras={"sem_senha": True})
        return aj, chaves

    def _sync_configuracao(self, sync: Any, texto: Any) -> dict:
        if not isinstance(texto, str) or not texto.strip():
            raise ErroHttp(400, sync.CONFIG_INVALIDA, extras={"arquivo_invalido": True})
        try:
            return sync.ler_configuracao(texto)
        except ValueError as erro:
            raise self._erro_sync(sync, erro) from None

    def _conferir_senha_sync(self, sync: Any, acao: Callable[[], Any]) -> Any:
        """Como o PIN: senha errada = 401 já dizendo quanto esperar; antes da hora, 429 sem scrypt."""
        with self._trava_senha_sync:
            self._espera_senha_sync.conferir()
            try:
                resultado = acao()
            except sync.SenhaSyncErrada:
                self._falhas_senha_sync += 1
                raise self._pin_errado(self._espera_senha_sync, self._falhas_senha_sync, sync.SENHA_ERRADA) from None
            self._falhas_senha_sync = 0
            self._espera_senha_sync.liberar()
            return resultado

    def _sync_estado(self, p: Pedido, banco: Banco) -> dict:
        """GET /api/sync/estado: ligada?, este aparelho, os outros, perguntas, ID do app e o conteúdo do
        entrenos-sync.json desta chave (sem segredo: para recriar o arquivo se ele sumir do Drive)."""
        sync = self._sync()
        return {**sync.estado(banco, self.plataforma), "plataforma": self.plataforma,
                # O maior arquivo de outro aparelho que este núcleo aceita em /sync/receber (ele vai em
                # base64 no corpo): a página nem baixa um maior (22.2).
                "limite_arquivo": max(0, self.limite_corpo_arquivo * 3 // 4 - 64 * 1024)}

    def _sync_ajustes(self, p: Pedido, banco: Banco) -> dict:
        """PATCH /api/sync/ajustes {id_app?, nome?, automatica?, conta?}."""
        sync = self._sync()
        corpo = {k: (_nfc(v) if isinstance(v, str) else v) for k, v in p.corpo.items()}
        sync.mudar_ajustes(banco, corpo, self.plataforma)
        return {**sync.estado(banco, self.plataforma), "plataforma": self.plataforma}

    def _sync_senha(self, p: Pedido) -> dict:
        """POST /api/sync/senha {senha, sync_json?, substituir?}: liga a sincronização neste aparelho.

        Sem `sync_json` (ainda não há entrenos-sync.json no Drive): cria a configuração com a senha
        (mesmas regras do PIN) e devolve o texto dele para a página enviar. Com `sync_json`: confere a
        senha contra o verificador (errada: 401 e bloqueio crescente). A chave fica no diário."""
        sync = self._sync()
        c = p.corpo
        senha = c.get("senha")
        if not isinstance(senha, str) or not senha:
            raise ErroHttp(400, "Digite a senha de sincronização.")
        senha = _nfc(senha)
        texto = c.get("sync_json")
        substituir = c.get("substituir") is True
        with self._trava:
            banco = self._exigir_sessao(p.token)
            if sync.configurada(sync.ajustes(banco)) and not substituir:
                raise ErroHttp(409, sync.JA_CONFIGURADA)
        criado = None
        if texto is None:
            problema = sync.problema_da_senha(senha)
            if problema:
                raise ErroHttp(400, problema)
            # Pode ser igual ao PIN (decisão do usuário), mas a tela avisa antes e pede confirmação:
            # quebrar uma (nos arquivos do Drive) entregaria a outra (22.2).
            if c.get("mesmo_que_o_pin") is not True and self._senha_igual_ao_pin(senha):
                raise ErroHttp(409, sync.SENHA_IGUAL_AO_PIN, extras={"igual_ao_pin": True})
            configuracao, chaves = sync.nova_configuracao(senha)      # scrypt fora da trava do app
            criado = sync.texto_da_configuracao(configuracao)
        else:
            configuracao = self._sync_configuracao(sync, texto)
            chaves = self._conferir_senha_sync(sync, lambda: sync.chaves_da_senha(senha, configuracao))
        with self._trava:
            banco = self._exigir_sessao(p.token)
            if sync.configurada(sync.ajustes(banco)) and not substituir:
                raise ErroHttp(409, sync.JA_CONFIGURADA)
            info = sync.ligar(banco, chaves, configuracao, self.plataforma, self._agora_utc())
        return {**info, "sync_json": criado, "criado": criado is not None}

    def _sync_iniciar(self, p: Pedido, banco: Banco) -> dict:
        """POST /api/sync/iniciar {sync_json}: o entrenos-sync.json do Drive ainda é o desta chave? (Senha
        trocada em outro aparelho: 409 {senha_mudou}.) Devolve o arquivo deste aparelho e a etiqueta
        do último arquivo de cada aparelho já juntado aqui (a página só baixa o que mudou)."""
        sync = self._sync()
        aj, _chaves = self._sync_chaves(sync, banco)
        try:
            configuracao = self._sync_configuracao(sync, p.corpo.get("sync_json"))
        except ErroHttp as erro:
            # Num aparelho já ligado, um entrenos-sync.json diferente do seu é "a senha mudou" (ou
            # um arquivo estranho), nunca "versão nova": o arquivo não é autenticado, e qualquer um
            # com acesso ao Drive poria um "formato": 2 nele (22.2).
            if erro.status == 409:
                raise ErroHttp(409, sync.CONFIG_MUDOU, extras={"senha_mudou": True}) from None
            raise
        if not sync.mesma_configuracao(aj, configuracao):
            raise ErroHttp(409, sync.CONFIG_MUDOU, extras={"senha_mudou": True})
        est = sync.estado(banco, self.plataforma)
        return {"aparelho": est["aparelho"], "arquivo": est["arquivo"],
                "etiquetas": {a["aparelho"]: a["etiqueta"] for a in est["aparelhos"] if a.get("etiqueta")}}

    def _sync_receber(self, p: Pedido, banco: Banco) -> dict:
        """POST /api/sync/receber {nome, etiqueta?, conteudo_b64}: junta o arquivo de outro aparelho.
        Arquivo que não abre com a chave (mexido, de outra senha): 400 e nada muda."""
        sync = self._sync()
        aj, chaves = self._sync_chaves(sync, banco)
        c = p.corpo
        nome, etiqueta = c.get("nome"), c.get("etiqueta")
        if sync.aparelho_do_arquivo(nome) is None:
            raise ErroHttp(400, "Nome de arquivo de sincronização inválido.")
        if etiqueta is not None and (not isinstance(etiqueta, str) or not 0 < len(etiqueta) <= 200):
            raise ErroHttp(400, "Etiqueta do arquivo inválida.")
        bruto = c.get("conteudo_b64")
        c["conteudo_b64"] = None
        try:
            conteudo = base64.b64decode(re.sub(r"\s+", "", bruto), validate=True) if isinstance(bruto, str) else b""
        except (binascii.Error, ValueError):
            conteudo = b""
        del bruto
        if not conteudo:
            raise ErroHttp(400, ARQUIVO_SYNC_VAZIO, extras={"arquivo_invalido": True})
        try:
            resumo = sync.mesclar(banco, chaves, conteudo, proprio=aj["aparelho"], agora=self._agora_utc(),
                                  nome_arquivo=nome, etiqueta=etiqueta)
        except ValueError as erro:
            raise self._erro_sync(sync, erro) from None
        apagados = resumo.pop("_apagados", None)
        if apagados and any(apagados.values()):
            # Apagar é apagar também nas cópias automáticas (como quando se apaga pela tela).
            self._limpar_backups(lambda copia: sync.apagar_nas_copias(copia, apagados))
        return resumo

    def _sync_preparar(self, p: Pedido, banco: Banco) -> dict:
        """POST /api/sync/preparar: o arquivo deste aparelho, cifrado, para a página enviar ao Drive."""
        sync = self._sync()
        aj, chaves = self._sync_chaves(sync, banco)
        nome, conteudo = sync.preparar(banco, chaves, aj, self._agora_utc())
        resposta: dict[str, Any] = {"nome": nome, "tamanho": len(conteudo),
                                    "conteudo_b64": base64.b64encode(conteudo).decode("ascii")}
        if len(conteudo) > MAX_ARQUIVO_SYNC_CELULAR:
            resposta["aviso"] = ARQUIVO_SYNC_GRANDE.format(mb=round(len(conteudo) / (1024 * 1024)))
        return resposta

    def _sync_enviado(self, p: Pedido, banco: Banco) -> dict:
        """POST /api/sync/enviado {etiqueta}: o Drive aceitou o arquivo deste aparelho."""
        sync = self._sync()
        aj = sync.marcar_enviado(banco, p.corpo.get("etiqueta"), self._agora_utc(), p.corpo.get("apagados"))
        return {"ultima": aj.get("ultima")}

    def _sync_conflitos(self, p: Pedido, banco: Banco) -> list:
        """GET /api/sync/conflitos: o que mudou nos dois aparelhos quase ao mesmo tempo (a tela pergunta)."""
        return self._sync().conflitos(banco)

    def _sync_resolver(self, p: Pedido, banco: Banco) -> dict:
        """POST /api/sync/conflitos/{id} {fica: "este"|"outro"}."""
        sync = self._sync()
        _aj, chaves = self._sync_chaves(sync, banco)
        sync.resolver_conflito(banco, chaves, p.id, p.corpo.get("fica"), self._agora_utc())
        return {}

    def _sync_desligar(self, p: Pedido, banco: Banco) -> dict:
        """POST /api/sync/desligar: para de sincronizar neste aparelho. A chave sai do diário e das
        cópias automáticas; os arquivos no Drive ficam (cada aparelho só mexe no seu)."""
        sync = self._sync()
        sync.desligar(banco)
        self._limpar_backups(lambda copia: isinstance(copia.config().get("sync"), dict)
                             and self._backup_sem(copia, sync=None))
        return {**sync.estado(banco, self.plataforma), "plataforma": self.plataforma}

    # ------------------------------------------------------------ IA: PIN e área

    def _ia_estado(self, p: Pedido, banco: Banco) -> dict:
        """Sem nome do serviço, URL nem modelo: isso fica na área do PIN da IA.

        `segundos_restantes`: quanto falta para a área se fechar (None fechada); a tela conta a
        partir daqui, não de quando a página abriu.
        """
        ajustes = banco.config()
        info = _info_ia(ajustes, self.ia_disponivel)
        estado = {"pin_definido": bool(ajustes.get("ia_pin")), "liberada": self.area_ia_aberta(),
                  "configurada": info["configurada"], "utilizavel": info["utilizavel"],
                  "reserva_configurada": isinstance(ajustes.get("ia_reserva"), dict),
                  "sai_do_computador": info["sai_do_computador"], "espera_maxima": info["espera_maxima"],
                  "segundos_restantes": self.segundos_da_area_ia()}
        if "motivo" in info:
            estado["motivo"] = info["motivo"]
        return estado

    @staticmethod
    def _pin_ia_novo(novo: Any) -> str:
        problema = problema_do_pin(novo)
        if problema:
            raise ErroHttp(400, problema.replace("O PIN ", "O PIN da IA ", 1))
        return novo

    def _pin_ia_diferente(self, novo: str) -> None:
        if self._igual_ao_pin_principal(novo):
            raise ErroHttp(400, "O PIN da IA precisa ser diferente do PIN principal.")

    def _ia_criar_pin(self, p: Pedido) -> dict:
        novo = self._pin_ia_novo(p.corpo.get("novo"))
        existe = "O PIN da IA já existe. Para trocar, abra a área da IA com ele."
        with self._trava:
            if self._exigir_sessao(p.token).config().get("ia_pin"):
                raise ErroHttp(409, existe)
        self._pin_ia_diferente(novo)
        verificador = criar_verificador(novo)        # scrypt fora da trava do app
        with self._trava:
            banco = self._exigir_sessao(p.token)
            if banco.config().get("ia_pin"):
                raise ErroHttp(409, existe)
            banco.salvar_config(ia_pin=verificador)
            self._zerar_pin_ia()
            self._abrir_area_ia()
        return {"liberada": True}

    def _ia_entrar(self, p: Pedido) -> dict:
        pin = p.corpo.get("pin_ia")
        if not isinstance(pin, str) or not pin:
            raise ErroHttp(400, "Digite o PIN da IA.")
        with self._trava:
            verificador = self._exigir_sessao(p.token).config().get("ia_pin")
        if not verificador:
            raise ErroHttp(409, "Ainda não existe PIN da IA. Crie um em Ajustes → IA.")
        self._conferir_pin_ia(verificador, pin)
        with self._trava:
            self._exigir_sessao(p.token)
            self._abrir_area_ia()
        return {"liberada": True}

    def _ia_sair(self, p: Pedido, banco: Banco) -> dict:
        self.fechar_area_ia()
        return {}

    def _ia_trocar_pin(self, p: Pedido) -> dict:
        atual, novo = p.corpo.get("atual"), p.corpo.get("novo")
        verificador = self._sessao_e_area_ia(p.token).get("ia_pin")
        if not isinstance(atual, str) or not atual:
            raise ErroHttp(400, "Digite o PIN da IA atual.")
        problema = problema_do_pin(novo)
        if problema:
            raise ErroHttp(400, problema.replace("O PIN ", "O PIN novo ", 1))
        if not verificador:
            raise ErroHttp(409, "Ainda não existe PIN da IA. Crie um em Ajustes → IA.")
        self._conferir_pin_ia(verificador, atual, "O PIN da IA atual não confere.")
        self._pin_ia_diferente(novo)
        novo_verificador = criar_verificador(novo)
        with self._trava:
            banco = self._exigir_sessao(p.token)
            self._exigir_area_ia()
            banco.salvar_config(ia_pin=novo_verificador)
            # O verificador do PIN antigo sai das cópias (quem tem o PIN principal testaria palpites nele).
            self._limpar_backups(lambda b: b.config().get("ia_pin") is not None
                                 and self._backup_sem(b, ia_pin=novo_verificador))
        return {}

    def _ia_redefinir(self, p: Pedido) -> dict:
        """"Esqueci o PIN da IA": com o PIN principal, apaga o PIN e a configuração da IA."""
        pin = p.corpo.get("pin")
        if not isinstance(pin, str) or not pin:
            raise ErroHttp(400, "Digite o PIN principal.")
        with self._trava:
            self._exigir_sessao(p.token)
        self._confere_pin_principal(pin, "O PIN principal não confere.")
        with self._trava:
            banco = self._exigir_sessao(p.token)
            banco.salvar_config(ia_pin=None, ia_principal=None, ia_reserva=None)
            self.fechar_area_ia()
            self._zerar_pin_ia()
            self._limpar_backups(lambda b: self._backup_sem(b, ia_pin=None, ia_principal=None, ia_reserva=None))
        return {}

    # ------------------------------------------------------------ IA: configuração (área do PIN da IA)

    def _ia_ver_config(self, p: Pedido, banco: Banco) -> dict:
        self._exigir_area_ia()
        prov = _modulo("provedores", "para_tela", "provedor", "e_local", "NAO_SUPORTADOS")
        ajustes = banco.config()
        return {"principal": _ia_publica(ajustes.get("ia_principal"), prov),
                "reserva": _ia_publica(ajustes.get("ia_reserva"), prov),
                "tempo_limite": ajustes.get("ia_tempo_limite"),
                "provedores": prov.para_tela(), "nao_suportados": dict(prov.NAO_SUPORTADOS),
                "ambiguos": dict(getattr(prov, "AMBIGUOS", {})),
                # IA salva que não dá para usar (motor antigo migrado sem chave, por exemplo).
                "problemas": _problemas_ia(ajustes)}

    def _ia_editar_config(self, p: Pedido, banco: Banco) -> dict:
        self._exigir_area_ia()
        outros = sorted(set(p.corpo) - {"tempo_limite"})
        if outros:
            raise ErroHttp(400, "Campos que não podem ser alterados: " + ", ".join(outros))
        if "tempo_limite" not in p.corpo:
            raise ErroHttp(400, "Informe o tempo limite, em segundos.")
        banco.salvar_config(ia_tempo_limite=p.corpo["tempo_limite"])      # null = volta ao padrão
        return {"tempo_limite": banco.config()["ia_tempo_limite"]}

    @staticmethod
    def _ia_destino(prov: Any, corpo: dict) -> tuple[Any, str, bool]:
        """(serviço, URL normalizada, exata) de um pedido {provedor, url?, url_exata?}; sem URL,
        a do serviço. `url_exata` só vale em "Outro": o endereço fica sem o /v1 automático."""
        servico = prov.provedor(corpo.get("provedor"))
        if servico is None:
            raise ErroHttp(400, "Escolha um serviço de IA da lista.")
        url = corpo.get("url")
        if url is not None and not isinstance(url, str):
            raise ErroHttp(400, "“url” precisa ser um texto.")
        exata = corpo.get("url_exata", False)
        if not isinstance(exata, bool):
            raise ErroHttp(400, "“url_exata” precisa ser verdadeiro ou falso.")
        exata = exata and servico.id == "outro"
        url = _nfc(url).strip() if url else ""
        if not url:
            url = servico.url
            if not url:
                raise ErroHttp(400, "Informe o endereço da IA.")
        url = prov.normalizar_url(url, servico.tipo, versao=not exata)
        partes = urllib.parse.urlsplit(url)
        if partes.scheme not in ("http", "https") or not partes.hostname:
            raise ErroHttp(400, "Endereço da IA inválido: use http:// ou https://.")
        return servico, url, exata

    @staticmethod
    def _ia_chave_nova(prov: Any, valor: Any) -> str | None:
        """A chave digitada (limpa e conferida), ou None para "sem chave"."""
        if valor is None:
            return None
        if not isinstance(valor, str):
            raise ErroHttp(400, "A chave precisa ser um texto.")
        chave = prov.limpar_chave(valor)
        if not chave:
            return None
        nao = prov.chave_nao_suportada(chave)
        if nao:
            raise ErroHttp(400, nao)
        if not prov.chave_valida(chave):
            raise ErroHttp(400, "A chave tem caracteres que não podem estar numa chave. Copie de novo.")
        return chave

    @staticmethod
    def _ia_chave_salva(salvo: Any, servico: Any, url: str) -> str | None:
        """A chave guardada, só se é do mesmo serviço e vai para o mesmo endereço.

        Outro serviço: a chave antiga não vale (e some). Mesmo serviço em outro endereço: 400,
        a chave nunca segue para um endereço novo sem ser digitada de novo.
        """
        if not isinstance(salvo, dict) or not salvo.get("chave") or salvo.get("provedor") != servico.id:
            return None
        if _origem(salvo.get("url", "")) != _origem(url):
            raise ErroHttp(400, CHAVE_DE_NOVO)
        return salvo["chave"]

    def _ia_salvar(self, p: Pedido, banco: Banco) -> dict:
        """PUT /api/ia/config/{principal|reserva} {provedor, url?, modelo, chave?, url_exata?}."""
        self._exigir_area_ia()
        qual = _qual_ia(p.caminho.rsplit("/", 1)[-1])
        prov = _modulo("provedores", "provedor", "normalizar_url", "limpar_chave", "chave_valida",
                       "chave_nao_suportada", "e_local")
        c = p.corpo
        outros = sorted(set(c) - {"provedor", "url", "modelo", "chave", "url_exata"})
        if outros:
            raise ErroHttp(400, "Campos desconhecidos: " + ", ".join(outros))
        servico, url, exata = self._ia_destino(prov, c)
        modelo = c.get("modelo")
        if not isinstance(modelo, str) or not modelo.strip():
            raise ErroHttp(400, "Informe o modelo da IA (use “Buscar modelos”).")
        if "chave" in c:
            chave = self._ia_chave_nova(prov, c["chave"])            # "" ou null = remove
        else:
            chave = self._ia_chave_salva(banco.config().get(f"ia_{qual}"), servico, url)
        if servico.precisa_chave and not chave:
            raise ErroHttp(400, f"O serviço {servico.nome} precisa de uma chave.")
        _conferir_envio_da_chave(url, chave)
        anterior = banco.config().get(f"ia_{qual}")
        chave_anterior = anterior.get("chave") if isinstance(anterior, dict) else None
        nova = {"provedor": servico.id, "url": url, "modelo": _nfc(modelo).strip(), "tipo": servico.tipo, "chave": chave}
        if exata:
            nova["url_exata"] = True
        banco.salvar_config(**{f"ia_{qual}": nova})
        if chave_anterior and chave_anterior != chave:        # chave apagada ou trocada: some das cópias
            self._limpar_backups(lambda b: self._backup_sem_chave_antiga(b, qual, chave))
        return _ia_publica(banco.config().get(f"ia_{qual}"), prov)

    def _ia_remover(self, p: Pedido, banco: Banco) -> dict:
        self._exigir_area_ia()
        qual = _qual_ia(p.caminho.rsplit('/', 1)[-1])
        banco.salvar_config(**{f"ia_{qual}": None})
        # "O serviço, o endereço, o modelo e a chave desta IA saem do Entre Nós": das cópias também.
        self._limpar_backups(lambda b: self._backup_sem(b, **{f"ia_{qual}": None}))
        return {}

    def _ia_modelos(self, p: Pedido) -> dict:
        """POST /api/ia/modelos {provedor, url?, chave?, qual?, url_exata?}: os modelos que o
        serviço oferece.

        Em "Outro", endereço sem versão ganha /v1; se ali o serviço não responder (404 ou
        resposta que não é de IA), tenta o endereço como foi digitado e, dando certo, devolve
        `url` e `url_exata: true` para a tela salvar assim.
        """
        self._exigir_ia_disponivel()
        ajustes = self._sessao_e_area_ia(p.token)
        prov = _modulo("provedores", "provedor", "normalizar_url", "limpar_chave", "chave_valida",
                       "chave_nao_suportada")
        c = p.corpo
        outros = sorted(set(c) - {"provedor", "url", "chave", "qual", "url_exata"})
        if outros:
            raise ErroHttp(400, "Campos desconhecidos: " + ", ".join(outros))
        servico, url, exata = self._ia_destino(prov, c)
        chave = self._ia_chave_nova(prov, c.get("chave"))
        if chave is None and c.get("qual") is not None:
            chave = self._ia_chave_salva(ajustes.get(f"ia_{_qual_ia(c.get('qual'))}"), servico, url)
        _conferir_envio_da_chave(url, chave)
        listar_modelos = _funcao("motor", "listar_modelos")
        como_digitada = None
        if servico.id == "outro" and not exata:
            como_digitada = self._ia_destino(prov, {**c, "url_exata": True})[1]
            if como_digitada == url:
                como_digitada = None
        try:
            modelos = listar_modelos(servico.id, url, servico.tipo, chave, url_exata=exata)
        except Exception as erro:  # noqa: BLE001 - rede, chave, formato: mensagem sem segredo
            if como_digitada is None or getattr(erro, "tipo", None) not in ("nao_encontrado", "incompativel"):
                raise _erro_da_ia(erro) from None
            try:        # o mesmo endereço (mesmo host: a chave pode ir), sem o /v1 que o Entre Nós pôs
                modelos = listar_modelos(servico.id, como_digitada, servico.tipo, chave, url_exata=True)
            except Exception:  # noqa: BLE001 - vale a mensagem da primeira tentativa
                raise _erro_da_ia(erro) from None
            return {"modelos": [m for m in modelos if isinstance(m, str)], "url": como_digitada, "url_exata": True}
        return {"modelos": [m for m in modelos if isinstance(m, str)]}

    def _ia_testar(self, p: Pedido) -> dict:
        """POST /api/ia/testar {qual}: manda uma frase inventada e diz se a IA respondeu."""
        self._exigir_ia_disponivel()
        ajustes = self._sessao_e_area_ia(p.token)
        qual = _qual_ia(p.corpo.get("qual"))
        cfg_ia = ajustes.get(f"ia_{qual}")
        if not isinstance(cfg_ia, dict):
            raise ErroHttp(409, "Essa IA ainda não foi configurada.")
        testar_ia = _funcao("motor", "testar_ia")
        try:
            resultado = testar_ia(cfg_ia, tempo=ajustes.get("ia_tempo_limite"))
        except Exception as erro:  # noqa: BLE001
            raise _erro_da_ia(erro) from None
        resultado = resultado if isinstance(resultado, dict) else {}
        return {"ok": bool(resultado.get("ok")), "segundos": resultado.get("segundos"),
                "formato": resultado.get("formato"), "erro": resultado.get("erro")}

    # ------------------------------------------------------------ IA: os botões ✨

    def _cadeia_ia(self, token: str | None) -> tuple[Any, Banco]:
        """A cadeia principal -> reserva (409 sem IA), com a sessão conferida. Chame com a trava."""
        banco = self._exigir_sessao(token)
        ajustes = banco.config()
        if not _ias_configuradas(ajustes):
            raise ErroHttp(409, SEM_IA)
        cadeia = _funcao("motor", "obter_cadeia_ia")(ajustes)
        if cadeia is None:
            # Há IA salva, mas nenhuma dá para usar: diz o porquê, não "nenhuma IA configurada".
            problemas = _problemas_ia(ajustes)
            if not problemas:
                raise ErroHttp(409, SEM_IA)
            rotulos = {"principal": "IA principal", "reserva": "IA de reserva"}
            causas = " ".join(f"{rotulos[q]}: {m}" for q, m in problemas.items() if q in rotulos)
            raise ErroHttp(409, f"{IA_INCOMPLETA} {causas} Confira em Ajustes → IA.")
        return cadeia, banco

    def _ia_analisar(self, p: Pedido) -> dict:
        """POST /api/ia/analisar {texto, data_registro, privado?}: regras primeiro, depois a IA.

        Texto em que um recurso acha algo que deixa o registro 🔒 (o ciclo, seção 20.8) não vai para
        a IA (403), mesmo com a caixinha desmarcada; sem saber disso com segurança (um recurso não
        abriu), 503. Os `extras` das regras nunca vão para a IA: voltam na proposta como estavam."""
        self._exigir_ia_disponivel()
        c = p.corpo
        texto = _texto(c, "texto", "Escreva alguma coisa antes de organizar.")
        referencia = _data(c.get("data_registro"), "data_registro") or self.hoje()
        if _privado(c):
            raise ErroHttp(403, REGISTRO_PRIVADO)
        with self._trava:
            cadeia, banco = self._cadeia_ia(p.token)
            motor_regras = _funcao("motor", "MotorRegras")
            conhecimento_do_banco = _funcao("motor", "conhecimento_do_banco")
            comparar_propostas = _funcao("motor", "comparar_propostas")
            conhecimento = conhecimento_do_banco(banco)
        if recursos.falhas():
            raise ErroHttp(503, FORA_DA_IA_INDISPONIVEL)
        base = motor_regras().analisar(texto, referencia, conhecimento)
        if any(e.get("_privado") for lista in base.extras.values() for e in lista):
            raise ErroHttp(403, TEXTO_FICA_FORA_DA_IA)
        extras, base.extras = base.extras, {}
        # Fora da trava: a IA pode demorar, e o resto do app não precisa esperar.
        proposta, ia_usada, avisos = cadeia.analisar(texto, referencia, conhecimento, copy.deepcopy(base))
        base.extras = extras
        proposta.extras = copy.deepcopy(extras)
        proposta.motor = _motor_neutro(ia_usada)
        mudancas = comparar_propostas(base, proposta) if ia_usada else []
        with self._trava:
            banco = self._exigir_sessao(p.token)
            proposta.relacoes = self._relacionar(proposta, banco, referencia)
        return {"proposta": proposta, "mudancas": mudancas, "ia_usada": ia_usada, "avisos": list(avisos or [])}

    def _ia_busca(self, p: Pedido) -> dict:
        """POST /api/ia/busca {q}: a busca das regras acha os trechos; a IA escreve a resposta.

        Registros "não enviar para a IA" ficam fora da busca que vai para a IA (nem contam como
        menção de uma pessoa), e cada trecho vai só com o que está escrito no próprio registro:
        nome ou relação que veio do cadastro (às vezes aprendido num registro 🔒) não sai daqui.
        """
        self._exigir_ia_disponivel()
        pergunta = self._pergunta(p.corpo.get("q"))
        buscar = _funcao("busca", "buscar")
        with self._trava:
            cadeia, banco = self._cadeia_ia(p.token)
            privados = fora_da_ia(banco)             # 🔒 e o que os recursos deixam fora (seção 20.0)
            todos = buscar(pergunta, banco, None, self.hoje())
            # E o que o próprio texto deixa fora (seção 20.10), lido só nos achados: a busca sem eles
            # pode achar outros, que também são lidos (poucas voltas; o filtro final garante).
            ids = [e.get("registro_id") for e in todos.encontrados or [] if isinstance(e, dict)]
            privados |= fora_da_ia_pelo_texto(banco, ids, privados)
            achados = buscar(pergunta, banco, None, self.hoje(), privados=privados) if privados else todos
            for _volta in range(3):
                ids = [e.get("registro_id") for e in achados.encontrados or [] if isinstance(e, dict)]
                novos = fora_da_ia_pelo_texto(banco, ids, privados)
                if not novos:
                    break
                privados |= novos
                achados = buscar(pergunta, banco, None, self.hoje(), privados=privados)
            ids = [e.get("registro_id") for e in achados.encontrados or [] if isinstance(e, dict)]
            privados |= fora_da_ia_pelo_texto(banco, ids, privados)
            encontrados = [e for e in (achados.encontrados or [])
                           if isinstance(e, dict) and e.get("registro_id") not in privados]

            trechos = so_o_escrito(banco, encontrados)
        algum_privado = any(isinstance(e, dict) and e.get("registro_id") in privados for e in todos.encontrados or [])
        avisos = [PRIVADOS_DE_FORA] if algum_privado else []
        if not trechos:
            # Nada que possa ir para a IA: nem a pergunta sai daqui.
            vazia = Resposta(pergunta=pergunta, texto=NAO_ENCONTREI)
            return {**para_json(vazia), "ia_usada": None, "avisos": avisos}
        resposta, ia_usada, avisos_ia = cadeia.responder(pergunta, trechos)
        # Na tela, os achados citados aparecem como a busca mostra (a IA recebeu a versão limpa).
        citados = {e.get("registro_id") for e in (resposta.encontrados or []) if isinstance(e, dict)}
        resposta.encontrados = [e for e in encontrados if e.get("registro_id") in citados]
        resposta.motor = _motor_neutro(ia_usada)
        with self._trava:
            self._exigir_sessao(p.token)         # bloqueou enquanto a IA pensava: não entrega
        return {**para_json(resposta), "ia_usada": ia_usada, "avisos": avisos + list(avisos_ia or [])}

    def _ia_sugestao(self, p: Pedido) -> dict:
        """POST /api/ia/sugestao {data?}: até 3 gestos pequenos, a partir do que está registrado."""
        self._exigir_ia_disponivel()
        referencia = _data(p.corpo.get("data"), "data") or self.hoje()
        with self._trava:
            cadeia, banco = self._cadeia_ia(p.token)
            contexto = contexto_sugestao(banco, referencia)      # já sem registros privados
        if not contexto:
            return {"sugestoes": [], "ia_usada": None, "avisos": []}
        sugestoes, ia_usada, avisos = cadeia.sugerir(contexto, referencia)
        with self._trava:
            self._exigir_sessao(p.token)
        return {"sugestoes": list(sugestoes or []), "ia_usada": ia_usada, "avisos": list(avisos or [])}


__all__ = [
    "ARQUIVO_ESPERA_PIN", "ATRASO_PIN", "ATRASO_PIN_MAXIMO", "AREA_IA_FECHADA", "BACKUP_DANIFICADO",
    "BACKUP_SO_BAIXANDO", "BLOQUEADO", "CHAVE_DE_NOVO", "COMPUTADOR", "DIARIO_MUDOU", "IA_SO_NO_COMPUTADOR",
    "LIMITE_CORPO_ARQUIVO_NAVEGADOR", "NAVEGADOR", "PASTA_SUMIU_NAVEGADOR", "PLATAFORMAS",
    "COMPARACOES_SEM_ESPERA", "DADO_INVALIDO", "DIARIO_DANIFICADO", "ESPERE_O_PIN", "ErroHttp", "FALHA_INTERNA",
    "FORA_DA_IA_INDISPONIVEL", "TEXTO_FICA_FORA_DA_IA", "fora_da_ia", "fora_da_ia_pelo_texto", "rotas_dos_recursos",
    "texto_fica_fora_da_ia", "todas_as_rotas",
    "INDISPONIVEL", "LIMITE_CORPO", "LIMITE_CORPO_ARQUIVO", "LIVRE", "MAX_CONTEXTO_SUGESTAO", "MINUTOS_AREA_IA",
    "NAO_E_BACKUP", "NAO_SALVOU_ANTES", "Nucleo", "PASTA_SUMIU", "PIN_DO_BACKUP", "PRIMEIRO_USO",
    "PRIVADOS_DE_FORA", "PUBLICA", "Pedido", "REGISTRO_PRIVADO", "ROTAS", "RespostaApi", "Rota", "SEM_IA",
    "SESSAO", "achar_rota", "atraso_do_pin", "configurar_log", "contexto_sugestao", "mensagem_de_limite",
    "registrar_erro", "resposta_de_erro", "segundos_de_espera", "so_o_escrito", "traduzir_erro",
]
