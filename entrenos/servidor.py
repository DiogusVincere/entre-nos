"""Servidor local do Entre Nós (DESIGN.md, seção 15).

HTTP só em 127.0.0.1 (nunca 0.0.0.0), a API JSON e os arquivos de `web/`.

Segurança:
- `Host` precisa ser 127.0.0.1:porta ou localhost:porta (contra DNS rebinding);
- `Origin`, quando vem, precisa ser a própria origem; `Sec-Fetch-Site` de outro site é recusado;
- toda rota /api/* exceto ping, estado, configurar e desbloquear exige `X-EntreNos-Token`
  (restaurar também dispensa, mas só enquanto não existe diário);
- POST/PATCH/DELETE só com `Content-Type: application/json` e corpo de até 1 MB (64 MB em
  restaurar e importar);
- CSP e cabeçalhos de proteção em toda resposta; `Cache-Control: no-store` na API;
- PIN errado: espera 1 s, 2 s, 4 s... até 30 s antes de responder;
- sem uso por `minutos_bloqueio`, salva, fecha o cofre e descarta banco e token;
- arquivos estáticos só de `web/`, sem `..`, com o tipo MIME certo.

Nada de conteúdo do usuário vai para log: `erros.log` guarda só o tipo do erro e onde ele
aconteceu no código. Os módulos motor, provedores, contexto, busca, alertas e perfil são
importados na hora do uso: se um deles faltar, só as rotas que dependem dele respondem 503.

IA sob demanda (DESIGN.md, seção 17): `/api/analisar` e `/api/busca` usam só as regras. A IA
roda apenas pelas rotas `/api/ia/analisar`, `/api/ia/busca` e `/api/ia/sugestao` (os botões ✨),
e nada de registro marcado "não enviar para a IA" segue para ela. A área de ajustes da IA pede
um segundo PIN (o PIN da IA), com atraso crescente e contador próprio; fica liberada por
`minutos_area_ia` e fecha no bloqueio e em `/api/ia/sair`. A chave de uma IA nunca volta para a
tela (só os 4 últimos caracteres) e nunca segue para outro endereço sem ser digitada de novo.

Restaurar backup e importar JSON (DESIGN.md, seção 18.2): `/api/restaurar` confere o arquivo
inteiro (cabeçalho, PIN, banco, integridade) antes de trocar qualquer coisa, guarda uma cópia do
diário atual e abre uma sessão nova com o PIN do backup; `/api/importar/previa` e
`/api/importar` juntam o JSON de `/api/exportar` sem duplicar. Só estas três rotas aceitam corpo
de até 64 MB, uma de cada vez, e o token delas é conferido antes de o corpo ser lido.

Arquivos que somem (DESIGN.md, seção 18.6): sem o entrenos.cofre, `/api/estado` conta as cópias
de backups/ e o primeiro uso as põe de lado antes de começar outro diário; diário danificado
(cortado) não gasta tentativa de PIN e pode ser trocado por um backup; pasta de dados apagada com
o diário aberto não é recriada; sem os arquivos da página, o programa avisa em vez de abrir em
branco.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hmac
import importlib
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import secrets
import socket
import socketserver
import sys
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import config
from .banco import DADOS_DE_VERSAO_NOVA, Banco, NaoEncontrado, ler_exportacao, validar_exportacao
from .cofre import (Cofre, CofreCorrompido, CofreFechado, CofreRecebido, PastaSumiu, PinErrado, abrir_conteudo,
                    conferir_verificador, criar_verificador, problema_do_pin, tamanho_decifrado)
from .modelos import Certeza, Item, Resposta, TipoItem, para_json

ENDERECO = "127.0.0.1"               # só este computador; nunca 0.0.0.0
PORTAS_EXTRAS = 12                   # 8788-8799 quando a 8787 está ocupada por outro programa
LIMITE_CORPO = 1024 * 1024
LIMITE_CORPO_ARQUIVO = 64 * 1024 * 1024   # só restaurar e importar (o arquivo vem no corpo)
PAGINA_SQLITE_MINIMA = 512           # um banco SQLite serializado tem tamanho múltiplo disto
ATRASO_PIN = 1.0                     # segundos depois do 1º PIN errado; dobra a cada erro
ATRASO_PIN_MAXIMO = 30.0
INTERVALO_VIGIA = 30.0               # de quanto em quanto tempo a thread confere a inatividade
MAX_PERGUNTA = 500
MINUTOS_AREA_IA = 10                 # a área de ajustes da IA fecha sozinha depois disso
COMPARACOES_SEM_ESPERA = 3           # "PIN da IA igual ao principal?" sem atraso (depois, como PIN errado)
QUAIS_IA = ("principal", "reserva")
DIAS_CONTEXTO_SUGESTAO = 30          # registros recentes que entram no pedido de sugestão
MAX_CONTEXTO_SUGESTAO = 40
# Mutex do programa instalado aberto; o mesmo nome está no AppMutex de instalador/EntreNos.iss.
MUTEX_ABERTO = "EntreNos-Aberto-A5F79238-49BF-4FE8-8496-7CD3D084F9B4"
_MUTEX: Any = None                   # guardado enquanto o processo vive

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
CABECALHOS_SEGURANCA = (
    ("Content-Security-Policy", CSP),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
TIPOS_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
}
BLOQUEADO = "O Entre Nós está bloqueado. Digite o PIN para continuar."
SESSAO_INVALIDA = "Sua sessão não vale mais. Digite o PIN de novo."
FALHA_INTERNA = "Algo deu errado aqui dentro. Tente de novo; se continuar, feche e abra o Entre Nós."
INDISPONIVEL = "Esta parte do Entre Nós ainda não está pronta. Tente de novo mais tarde."
DADO_INVALIDO = "Algum dado enviado não é válido."
AREA_IA_FECHADA = "Esta área pede o PIN da IA."
SEM_IA = "Nenhuma IA configurada. Configure uma em Ajustes → IA."
IA_INCOMPLETA = "A IA configurada não pode ser usada assim."
REGISTRO_PRIVADO = "Este registro está marcado para não ir para a IA."
CHAVE_DE_NOVO = "Informe a chave de novo para o novo endereço."
AJUSTES_DA_IA = "Os ajustes da IA ficam em Ajustes → IA, protegidos pelo PIN da IA."
PRIVADOS_DE_FORA = "Registros marcados “não enviar para a IA” ficaram de fora do que foi para a IA."
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
ARQUIVO_OCUPADO = "Outro arquivo grande está sendo recebido agora. Espere terminar e tente de novo."
NAO_SALVOU_ANTES = ("Não consegui gravar o diário de agora antes da troca, então nada foi trocado. Tente de novo; "
                    "se continuar, feche e abra o Entre Nós.")
PAGINA_SEM_ARQUIVOS = (
    "<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\"><title>Entre Nós</title></head><body>"
    "<h1>Faltam arquivos do Entre Nós</h1>"
    "<p>A página do Entre Nós não está na pasta do programa. O antivírus pode ter colocado algum arquivo "
    "em quarentena.</p>"
    "<p>Reinstale o Entre Nós (rode o instalador de novo por cima) ou peça à TI para restaurar o arquivo "
    "da quarentena. Não desligue o antivírus.</p>"
    "<p>Seu diário não foi afetado: ele fica em outra pasta (%LOCALAPPDATA%\\EntreNos).</p>"
    "</body></html>")
ARQUIVOS_DA_PAGINA = ("index.html", "app.js", "estilo.css")

_log = logging.getLogger("entrenos")
_log.addHandler(logging.NullHandler())
_log_servidor = logging.getLogger("entrenos.servidor")

# Mensagens de ValueError que vêm do Python (em inglês) e não servem para a tela.
_RE_MENSAGEM_INGLES = re.compile(
    r"^[a-z'\"(]|^Invalid\b|^Expecting\b| is not a valid |\bmust be\b|out of range|could not|codec can't")


# ---------------------------------------------------------------- erros e log

class ErroHttp(Exception):
    """Erro com status HTTP e mensagem em português para a tela.

    `extras` vão no JSON da resposta junto com "erro" (ex.: {"pin_ia": true}).
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
        _log_servidor.error("\n".join(linhas))
    except Exception:  # noqa: BLE001 - o log nunca derruba o app
        pass


def _mensagem_de_dado(erro: BaseException) -> str:
    mensagem = str(erro) if isinstance(erro, ValueError) and not isinstance(erro, UnicodeError) else ""
    if not mensagem or _RE_MENSAGEM_INGLES.search(mensagem):
        return DADO_INVALIDO
    return mensagem


def traduzir_erro(erro: BaseException) -> ErroHttp:
    """Exceção -> status HTTP + mensagem em português."""
    if isinstance(erro, ErroHttp):
        return erro
    if isinstance(erro, NaoEncontrado):
        return ErroHttp(404, str(erro))
    if isinstance(erro, PinErrado):
        return ErroHttp(401, "PIN incorreto.")
    if isinstance(erro, CofreFechado):
        return ErroHttp(423, BLOQUEADO)
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
    """Espera depois da n-ésima tentativa errada seguida: base, 2×base, 4×base... até `maximo`."""
    if falhas <= 0:
        return 0.0
    return min(maximo, base * 2 ** min(falhas - 1, 30))


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


def _chave_da_ia(chave: str) -> bool:
    """Ajuste que só a área da IA mexe: ia_* e os antigos motor*."""
    return chave.startswith(("ia_", "motor"))


def _config_publica(config_atual: dict) -> dict:
    """Ajustes para a tela e para a exportação: nada da IA (nem chave, nem PIN, nem qual IA)."""
    return {k: v for k, v in config_atual.items() if not _chave_da_ia(k)}


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


def _info_ia(ajustes: dict) -> dict:
    """O que qualquer tela aberta com o PIN principal pode saber da IA (sem qual, URL ou modelo).

    `utilizavel`: alguma IA configurada dá para usar (senão os botões ✨ não aparecem).
    `espera_maxima`: segundos que um ✨ pode levar no pior caso (tempo limite × IAs utilizáveis).
    """
    configuradas = [q for q in QUAIS_IA if isinstance(ajustes.get(f"ia_{q}"), dict)]
    problemas = _problemas_ia(ajustes)
    utilizaveis = [q for q in configuradas if q not in problemas]
    tempo = ajustes.get("ia_tempo_limite")
    tempo = tempo if isinstance(tempo, (int, float)) and not isinstance(tempo, bool) and tempo > 0 else 120
    return {"configurada": bool(configuradas), "utilizavel": bool(utilizaveis),
            "sai_do_computador": _ia_sai_do_computador(ajustes),
            "espera_maxima": int(round(tempo * len(utilizaveis))) if utilizaveis else None}


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


def contexto_sugestao(banco: Banco, referencia: date) -> list[dict]:
    """O que a IA recebe para pensar numa sugestão (seção 17.3): itens ativos recentes, datas
    próximas e o que ajuda ou é restrição. Registros "não enviar para a IA" ficam de fora.

    Prioridade: o que acontece agora (estado, pendência, evento próximo), depois o resto dos
    registros recentes, depois apoio e restrições. No máximo MAX_CONTEXTO_SUGESTAO itens.
    """
    privados = banco.registros_privados()
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
    if isinstance(erro, (TimeoutError, socket.timeout)):
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


@dataclass
class Pedido:
    metodo: str
    caminho: str
    consulta: dict[str, list[str]] = field(default_factory=dict)
    bruto: bytes = b""
    token: str | None = None
    grupos: tuple[str, ...] = ()
    depois: Callable[[], None] | None = None     # roda depois de a resposta sair (/api/sair)
    campos_grandes: tuple[str, ...] = ()         # ver _json_do_corpo
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
        valores = self.consulta.get(nome)
        return valores[-1] if valores else None

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


def _rota(metodo: str, caminho: str, funcao: str, acesso: str = SESSAO, limite_corpo: int = LIMITE_CORPO,
          campos_grandes: tuple[str, ...] = ()) -> Rota:
    padrao = re.escape("/api/" + caminho).replace(re.escape("{id}"), r"([0-9]{1,12})")
    return Rota(metodo, re.compile(padrao), funcao, acesso, limite_corpo, campos_grandes)


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
    _rota("GET", "exportar", "_exportar"),
    _rota("GET", "historico", "_historico"),
    _rota("POST", "sair", "_sair", LIVRE),
    # IA sob demanda (seção 17.6). As que conferem PIN ou falam com a IA são LIVRE: esperam
    # (atraso do PIN, resposta da IA) sem prender o resto do app.
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
)


def achar_rota(metodo: str, caminho: str) -> tuple[Rota, tuple[str, ...]]:
    permitidos: set[str] = set()
    for rota in ROTAS:
        achado = rota.padrao.fullmatch(caminho)
        if achado:
            if rota.metodo == metodo:
                return rota, achado.groups()
            permitidos.add(rota.metodo)
    if permitidos:
        raise ErroHttp(405, "Este endereço não aceita esse tipo de pedido.",
                       {"Allow": ", ".join(sorted(permitidos))})
    raise ErroHttp(404, "Não encontrei esse endereço.")


def arquivo_estatico(pasta_web: Path, caminho_url: str) -> Path | None:
    """Arquivo de `web/` para o caminho pedido, ou None. Nada fora de `web/`."""
    try:
        caminho = urllib.parse.unquote(caminho_url, errors="strict")
    except UnicodeError:
        return None
    if caminho in ("", "/"):
        caminho = "/index.html"
    if not caminho.startswith("/") or any(c in caminho for c in "\\:") or any(ord(c) < 32 for c in caminho):
        return None
    partes = caminho[1:].split("/")
    # Sem "..", sem arquivo oculto e sem ponto ou espaço no fim (o Windows ignoraria).
    if any(not p or p.startswith(".") or p.endswith((".", " ")) or p != p.strip() for p in partes):
        return None
    base = os.path.realpath(pasta_web)
    alvo = os.path.realpath(os.path.join(base, *partes))
    if not os.path.normcase(alvo).startswith(os.path.normcase(base) + os.sep):
        return None
    if os.path.splitext(alvo)[1].lower() not in TIPOS_MIME or not os.path.isfile(alvo):
        return None
    return Path(alvo)


# ---------------------------------------------------------------- o app

class EntreNos:
    """Estado do app: cofre, banco aberto, token e bloqueio. Não sabe nada de HTTP."""

    def __init__(self, pasta: Path | str, *, atraso_pin: float = ATRASO_PIN,
                 atraso_pin_maximo: float = ATRASO_PIN_MAXIMO, minutos_bloqueio: float | None = None,
                 intervalo_verificacao: float = INTERVALO_VIGIA, hoje: Callable[[], date] | None = None,
                 pasta_web: Path | str | None = None, minutos_area_ia: float = MINUTOS_AREA_IA):
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.pasta_web = Path(pasta_web) if pasta_web is not None else config.PASTA_WEB
        self.hoje = hoje or date.today
        self.cofre = Cofre(self.pasta)
        self.cofre.hoje = self.hoje
        self.atraso_pin = float(atraso_pin)
        self.atraso_pin_maximo = float(atraso_pin_maximo)
        self.minutos_bloqueio = minutos_bloqueio      # None = o que estiver nos ajustes
        self.intervalo_verificacao = float(intervalo_verificacao)
        self.porta: int | None = None
        self.servidor: ThreadingHTTPServer | None = None
        self._trava = threading.RLock()
        self._trava_pin = threading.Lock()
        self._banco: Banco | None = None
        self._token: str | None = None
        self._ultima_atividade = time.monotonic()
        self._falhas_pin = 0
        # Área de ajustes da IA: aberta até este instante (time.monotonic), ou fechada (None).
        self.minutos_area_ia = float(minutos_area_ia)
        self._area_ia_ate: float | None = None
        self._trava_pin_ia = threading.Lock()
        # Um pedido de corpo grande (até 64 MB) por vez: cada um custa centenas de MB de memória
        # antes de qualquer PIN, e no primeiro uso a rota de restaurar não pede token.
        self.vez_do_arquivo = threading.BoundedSemaphore(1)
        self._falhas_pin_ia = 0          # contador próprio: errar o PIN da IA não mexe no principal
        self._comparacoes_pin = 0        # "o PIN da IA é igual ao principal?" desde o último PIN certo
        self._encerrando = False
        self._parar = threading.Event()
        self._vigia: threading.Thread | None = None

    # ------------------------------------------------------------ ciclo de vida

    @property
    def desbloqueado(self) -> bool:
        with self._trava:           # espera um bloqueio em andamento terminar
            return self._banco is not None

    def iniciar_vigia(self) -> None:
        if self._vigia is None:
            self._vigia = threading.Thread(target=self._vigiar, name="entrenos-vigia", daemon=True)
            self._vigia.start()

    def _vigiar(self) -> None:
        while not self._parar.wait(self.intervalo_verificacao):
            try:
                self.verificar_inatividade()
            except Exception as erro:  # noqa: BLE001 - a vigia continua de pé
                registrar_erro(erro)

    def verificar_inatividade(self) -> bool:
        """Bloqueia se passou do tempo sem uso. True quando bloqueou agora."""
        with self._trava:
            if self._banco is None:
                return False
            if time.monotonic() - self._ultima_atividade < self._limite_inatividade(self._banco):
                return False
            self.bloquear()
            return True

    def _limite_inatividade(self, banco: Banco) -> float:
        minutos = self.minutos_bloqueio
        if minutos is None:
            try:
                minutos = float(banco.config().get("minutos_bloqueio") or config.MINUTOS_BLOQUEIO_PADRAO)
            except Exception:  # noqa: BLE001 - ajuste estranho: usa o padrão
                minutos = config.MINUTOS_BLOQUEIO_PADRAO
        return float(minutos) * 60

    def bloquear(self) -> None:
        """Salva, esquece a chave e descarta o banco em memória e o token."""
        with self._trava:
            banco, self._banco, self._token = self._banco, None, None
            self._area_ia_ate = None             # a área da IA fecha junto
            if banco is not None:
                try:
                    self.cofre.salvar(banco.para_bytes())
                except PastaSumiu:
                    pass        # a pasta de dados foi apagada: o diário não volta (seção 18.6)
                except Exception as erro:  # noqa: BLE001 - bloquear vem antes de tudo
                    registrar_erro(erro)
                banco.ao_salvar = None
                banco.fechar()
            self.cofre.fechar()

    def encerrar(self) -> None:
        """Salva, bloqueia e para a vigia (fim do programa)."""
        self._encerrando = True
        self._parar.set()
        self.bloquear()
        vigia = self._vigia
        if vigia is not None and vigia is not threading.current_thread():
            vigia.join(timeout=5)

    def _salvar(self, dados: bytes) -> None:
        """`ao_salvar` do banco aberto. Pasta de dados apagada com o diário aberto (o desinstalador
        com "apagar tudo"): bloqueia sem salvar, em vez de recriar o que foi apagado."""
        try:
            self.cofre.salvar(dados)
        except PastaSumiu:
            self._descartar_sem_salvar()
            raise ErroHttp(409, PASTA_SUMIU) from None

    def _descartar_sem_salvar(self) -> None:
        """Esquece o diário aberto, a chave e o token sem gravar nada."""
        with self._trava:
            banco, self._banco, self._token = self._banco, None, None
            self._area_ia_ate = None
            if banco is not None:
                banco.ao_salvar = None   # a conexão fecha sozinha quando o Banco é descartado
            self.cofre.fechar()

    def _marcar_atividade(self) -> None:
        self._ultima_atividade = time.monotonic()

    def _exigir_sessao(self, token: str | None) -> Banco:
        """O banco aberto, se o token confere. Bloqueado: 423; token errado ou ausente: 401."""
        with self._trava:
            self.verificar_inatividade()
            if self._banco is None or self._token is None:
                raise ErroHttp(423, BLOQUEADO)
            if not token or not hmac.compare_digest(token.encode("utf-8", "replace"),
                                                    self._token.encode("utf-8")):
                raise ErroHttp(401, SESSAO_INVALIDA)
            self._marcar_atividade()
            return self._banco

    def _abrir_sessao(self, banco: Banco) -> str:
        banco.ao_salvar = self._salvar
        self._banco = banco
        self._token = secrets.token_urlsafe(32)
        self._marcar_atividade()
        return self._token

    def _conferir_pin(self, acao: Callable[[], Any], mensagem: str = "PIN incorreto.") -> Any:
        """Roda `acao`, que confere o PIN. Errado: espera 1 s, 2 s, 4 s... e responde 401.

        A trava faz as tentativas passarem uma de cada vez, então tentar em paralelo não
        encurta a espera.
        """
        with self._trava_pin:
            try:
                resultado = acao()
            except PinErrado:
                self._falhas_pin += 1
                time.sleep(atraso_do_pin(self._falhas_pin, self.atraso_pin, self.atraso_pin_maximo))
                raise ErroHttp(401, mensagem) from None
            self._falhas_pin = 0
            self._comparacoes_pin = 0            # quem acertou o PIN principal não precisa esperar
            return resultado

    def _igual_ao_pin_principal(self, pin: str) -> bool:
        """O PIN (da IA) é igual ao principal, ou uma variação óbvia dele (maiúsculas, espaço
        no fim, espaços repetidos)? Cada resposta conta algo sobre o PIN principal: depois de
        COMPARACOES_SEM_ESPERA pedidos, espera como um PIN errado. Assim quem achar o app aberto
        não descobre o PIN principal criando ou trocando o PIN da IA muitas vezes."""
        with self._trava_pin:
            self._comparacoes_pin += 1
            time.sleep(atraso_do_pin(self._comparacoes_pin - COMPARACOES_SEM_ESPERA, self.atraso_pin,
                                     self.atraso_pin_maximo))
            return any(self.cofre.confere_pin(v) for v in _variacoes_do_pin(pin))

    def _conferir_pin_ia(self, verificador: dict, pin: Any, mensagem: str = "PIN da IA incorreto.") -> None:
        """Como `_conferir_pin`, para o PIN da IA, com trava e contador próprios."""
        with self._trava_pin_ia:
            if not conferir_verificador(pin, verificador):
                self._falhas_pin_ia += 1
                time.sleep(atraso_do_pin(self._falhas_pin_ia, self.atraso_pin, self.atraso_pin_maximo))
                raise ErroHttp(401, mensagem)
            self._falhas_pin_ia = 0

    def _confere_pin_principal(self, pin: str, mensagem: str) -> None:
        """PIN principal digitado de novo (com o cofre aberto), com o atraso do PIN principal."""
        def conferir() -> None:
            if not self.cofre.confere_pin(pin):
                raise PinErrado("PIN incorreto.")
        self._conferir_pin(conferir, mensagem)

    # ------------------------------------------------------------ área da IA

    def area_ia_aberta(self) -> bool:
        with self._trava:
            if self._area_ia_ate is not None and (self._banco is None or time.monotonic() >= self._area_ia_ate):
                self._area_ia_ate = None
            return self._area_ia_ate is not None

    def segundos_da_area_ia(self) -> int | None:
        """Segundos até a área da IA se fechar sozinha; None se está fechada."""
        with self._trava:
            if not self.area_ia_aberta() or self._area_ia_ate is None:
                return None
            return max(0, int(self._area_ia_ate - time.monotonic() + 0.999))

    def _abrir_area_ia(self) -> None:
        with self._trava:
            if self._banco is not None:
                self._area_ia_ate = time.monotonic() + self.minutos_area_ia * 60

    def fechar_area_ia(self) -> None:
        with self._trava:
            self._area_ia_ate = None

    def _exigir_area_ia(self) -> None:
        if not self.area_ia_aberta():
            raise ErroHttp(403, AREA_IA_FECHADA, extras={"pin_ia": True})

    def _sessao_e_area_ia(self, token: str | None) -> dict:
        """Para as rotas LIVRE da área da IA: confere sessão e área, devolve os ajustes."""
        with self._trava:
            banco = self._exigir_sessao(token)
            self._exigir_area_ia()
            return banco.config()

    # ------------------------------------------------------------ segurança do pedido

    def host_permitido(self, host: str | None) -> bool:
        if not host or self.porta is None:
            return False
        return host.strip().lower() in {f"127.0.0.1:{self.porta}", f"localhost:{self.porta}"}

    @staticmethod
    def origem_permitida(origem: str, host: str) -> bool:
        return origem.strip().lower() == f"http://{host.strip().lower()}"

    def primeiro_uso(self) -> bool:
        """Ainda não existe diário neste computador (nem aberto): hora de configurar ou restaurar."""
        with self._trava:
            return self._banco is None and not self.cofre.existe()

    def diario_danificado(self) -> bool:
        """O arquivo do diário existe, nada está aberto, e ele não tem como abrir com PIN nenhum
        (cortado, cabeçalho estranho): dá para restaurar um backup sem o PIN dele (seção 18.6)."""
        with self._trava:
            return self._banco is None and self.cofre.danificado()

    def conferir_antes_do_corpo(self, rota: Rota, token: str | None) -> None:
        """Rotas de corpo grande (até 64 MB): o token é conferido antes de o corpo ser lido."""
        if rota.acesso == PUBLICA or (rota.acesso == PRIMEIRO_USO and (self.primeiro_uso()
                                                                       or self.diario_danificado())):
            return
        self._exigir_sessao(token)

    def _sem_o_arquivo_do_diario(self) -> str:
        """Mensagem do desbloqueio quando o entrenos.cofre não está na pasta (seção 18.6)."""
        mensagem = (f"O arquivo do diário (entrenos.cofre) não está na pasta {self.pasta}. Se você não o "
                    "apagou, o antivírus pode tê-lo colocado em quarentena: peça para restaurá-lo (não "
                    "desligue o antivírus).")
        copias = self.cofre.resumo_das_copias()
        if copias:
            mensagem += (f" Há {copias['quantas']} {'cópia' if copias['quantas'] == 1 else 'cópias'} em "
                         f"{copias['pasta']}: para usar uma, recarregue a página e escolha “Já tenho um backup”.")
        else:
            mensagem += " Ou recarregue a página para criar um diário ou restaurar um backup."
        return mensagem

    def atender(self, rota: Rota, pedido: Pedido) -> Any:
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
            banco = self._exigir_sessao(pedido.token)
            try:
                return funcao(pedido, banco)
            finally:
                if self._banco is banco:
                    self._marcar_atividade()

    # ------------------------------------------------------------ rotas públicas

    def _ping(self, p: Pedido) -> dict:
        return {"app": "entre-nos", "versao": config.VERSAO}

    def _estado(self, p: Pedido) -> dict:
        with self._trava:
            self.verificar_inatividade()
            banco = self._banco
            # Bloqueado, os ajustes (e com eles a IA) estão dentro do cofre fechado.
            ia = (_info_ia(banco.config()) if banco is not None
                  else {"configurada": None, "utilizavel": None, "sai_do_computador": None, "espera_maxima": None})
            configurado = self.cofre.existe()
            danificado = banco is None and configurado and self.cofre.danificado()
            # Sem diário (ou com o arquivo danificado): quantas cópias de um diário que esteve aqui
            # existem em backups/ e onde, para a tela não deixar a pessoa começar outro às cegas.
            copias = self.cofre.resumo_das_copias() if not configurado or danificado else None
            return {"configurado": configurado, "desbloqueado": banco is not None,
                    "versao": config.VERSAO, "ia": ia, "danificado": danificado, "copias_anteriores": copias}

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
                raise ErroHttp(503, "O Entre Nós está sendo fechado.")
            if self.cofre.existe():
                raise ErroHttp(409, "Este computador já tem um Entre Nós configurado. Use o seu PIN.")
            # Cópias de um diário que sumiu ficam de lado, fora do rodízio do diário novo (18.6).
            self.cofre.separar_copias_anteriores()
            banco = Banco.novo()
            try:
                if valores:
                    banco.salvar_config(**valores)
                self.cofre.criar(pin, banco.para_bytes())
            except FileExistsError:
                banco.fechar()
                raise ErroHttp(409, "Este computador já tem um Entre Nós configurado. Use o seu PIN.") from None
            except BaseException:
                banco.fechar()
                raise
            self._falhas_pin = 0
            return {"token": self._abrir_sessao(banco)}

    def _desbloquear(self, p: Pedido) -> dict:
        pin = p.corpo.get("pin")
        if self._encerrando:
            raise ErroHttp(503, "O Entre Nós está sendo fechado.")
        if not self.cofre.existe():
            raise ErroHttp(409, self._sem_o_arquivo_do_diario(), extras={"sem_diario": True})
        if self.cofre.danificado():
            # Nenhum PIN abriria: não gasta tentativa nem faz esperar (seção 18.6).
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
        rid = banco.criar_registro(texto, data_registro, itens, _lista_de_objetos(c, "pessoas"),
                                   _lista_de_objetos(c, "relacoes"), _texto_opcional(c, "contexto"),
                                   _texto_opcional(c, "humor"), privado=_privado(c))
        return {"id": rid}

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
        privados = banco.registros_privados()
        encontrados = [e for e in (resposta.get("encontrados") or []) if isinstance(e, dict)]
        for e in encontrados:
            e["privado"] = e.get("registro_id") in privados
        para_ia = any(not e["privado"] for e in encontrados)
        if encontrados and not para_ia:
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
        # se o palpite é o PIN da IA, sem o atraso do PIN da IA.
        self._conferir_pin(lambda: self.cofre.trocar_pin(atual, novo), "O PIN atual não confere.")
        return {}

    def _backup(self, p: Pedido, banco: Banco) -> dict:
        pasta = p.corpo.get("pasta")
        if not isinstance(pasta, str) or not pasta.strip():
            raise ErroHttp(400, "Informe a pasta onde guardar a cópia.")
        destino = Path(os.path.expanduser(pasta.strip()))
        if not destino.is_absolute():
            raise ErroHttp(400, "Informe o caminho completo da pasta, como E:\\ ou D:\\Backups.")
        self._salvar(banco.para_bytes())        # a cópia sai com o que está na tela agora
        try:
            arquivo = self.cofre.copiar_backup(destino)
        except NotADirectoryError:
            raise ErroHttp(400, "Essa pasta não existe.") from None
        except PermissionError:
            raise ErroHttp(400, "Sem permissão para gravar nessa pasta.") from None
        except FileExistsError as erro:
            raise ErroHttp(400, str(erro)) from None
        except OSError:
            raise ErroHttp(400, "Não consegui gravar a cópia nessa pasta.") from None
        return {"arquivo": str(arquivo)}

    def _exportar(self, p: Pedido, banco: Banco) -> dict:
        dados = banco.exportar()          # já sem chave de IA e sem PIN da IA
        if isinstance(dados.get("config"), dict):
            dados["config"] = _config_publica(dados["config"])      # e sem qual IA (área do PIN da IA)
        return dados

    def _historico(self, p: Pedido, banco: Banco) -> list:
        return banco.historico(p.inteiro("limite", 100, 1, 1000))

    def _sair(self, p: Pedido) -> dict:
        self.encerrar()
        servidor = self.servidor
        if servidor is not None:
            # Desliga só depois de a resposta sair; shutdown() espera o serve_forever terminar.
            p.depois = lambda: threading.Thread(target=servidor.shutdown, name="entrenos-sair",
                                                daemon=True).start()
        return {}

    # ------------------------------------------------------------ restaurar e importar (seção 18.2)

    def _restaurar(self, p: Pedido) -> dict:
        """POST /api/restaurar {arquivo_b64, pin[, pin_atual]}: troca o diário por um backup (.cofre).

        Primeiro uso (sem diário): pública, como /api/configurar. Com diário: pede o token e o
        PIN principal de agora (`pin_atual`, com o atraso do PIN errado). O arquivo é conferido
        inteiro antes de qualquer mudança (cabeçalho, PIN, banco com migração, integridade); só
        então o diário atual vai para backups/entrenos-antes-da-restauracao-...cofre e o backup
        entra no lugar, de uma vez. O PIN passa a ser o do backup, e os ajustes e a IA também;
        a sessão antiga morre e a área da IA fica fechada. Devolve {token, resumo, copia}.
        """
        if self._encerrando:
            raise ErroHttp(503, "O Entre Nós está sendo fechado.")
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
        conteudo = _arquivo_do_corpo(c.get("arquivo_b64"))
        c["arquivo_b64"] = None                  # o texto em base64 (dezenas de MB) já virou bytes
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
        tentativa de PIN; PIN errado: 401 com o atraso do PIN (a mesma espera do desbloqueio)."""
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
                raise ErroHttp(503, "O Entre Nós está sendo fechado.")
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
            self._area_ia_ate = None
            self._falhas_pin_ia = 0
            self._comparacoes_pin = 0
            token = self._abrir_sessao(novo)
            if antigo is not None:
                antigo.ao_salvar = None          # nada mais do diário antigo vai para o arquivo
                try:
                    antigo.fechar()
                except Exception as erro:  # noqa: BLE001 - a troca já valeu
                    registrar_erro(erro)
        return {"token": token, "resumo": resumo, "copia": str(copia) if copia is not None else None,
                "danificado": modo == "danificado"}

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

    # ------------------------------------------------------------ IA: PIN e área

    def _ia_estado(self, p: Pedido, banco: Banco) -> dict:
        """Sem nome do serviço, URL nem modelo: isso fica na área do PIN da IA.

        `segundos_restantes`: quanto falta para a área se fechar (None fechada); a tela conta a
        partir daqui, não de quando a página abriu.
        """
        ajustes = banco.config()
        info = _info_ia(ajustes)
        return {"pin_definido": bool(ajustes.get("ia_pin")), "liberada": self.area_ia_aberta(),
                "configurada": info["configurada"], "utilizavel": info["utilizavel"],
                "reserva_configurada": isinstance(ajustes.get("ia_reserva"), dict),
                "sai_do_computador": info["sai_do_computador"], "espera_maxima": info["espera_maxima"],
                "segundos_restantes": self.segundos_da_area_ia()}

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
            self._falhas_pin_ia = 0
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
            self._falhas_pin_ia = 0
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
        """POST /api/ia/analisar {texto, data_registro, privado?}: regras primeiro, depois a IA."""
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
        base = motor_regras().analisar(texto, referencia, conhecimento)
        # Fora da trava: a IA pode demorar, e o resto do app não precisa esperar.
        proposta, ia_usada, avisos = cadeia.analisar(texto, referencia, conhecimento, copy.deepcopy(base))
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
        pergunta = self._pergunta(p.corpo.get("q"))
        buscar = _funcao("busca", "buscar")
        with self._trava:
            cadeia, banco = self._cadeia_ia(p.token)
            privados = banco.registros_privados()
            todos = buscar(pergunta, banco, None, self.hoje())
            achados = buscar(pergunta, banco, None, self.hoje(), privados=privados) if privados else todos
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


# ---------------------------------------------------------------- HTTP

class Manipulador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60                  # conexão parada não prende a thread para sempre
    server: ServidorHTTP

    def version_string(self) -> str:
        return "EntreNos"

    def log_message(self, formato: str, *args: Any) -> None:
        """Nada no log: o caminho pode trazer a pergunta da busca."""

    def end_headers(self) -> None:
        for nome, valor in CABECALHOS_SEGURANCA:
            self.send_header(nome, valor)
        super().end_headers()

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Erros do próprio http.server (pedido malformado, método que não existe), em JSON."""
        self.close_connection = True
        mensagens = {400: "Pedido malformado.", 408: "Tempo esgotado.", 414: "Endereço longo demais.",
                     431: "Cabeçalhos grandes demais.", 501: "Método não suportado.",
                     505: "Versão de HTTP não suportada."}
        try:
            if code == 501:       # método desconhecido: os cabeçalhos deste pedido já foram lidos
                self._corpo_lido = False
                self._descartar_corpo()
            self._json(code, {"erro": mensagens.get(code, "Pedido recusado.")})
        except OSError:
            pass

    def do_GET(self) -> None:
        self._tratar("GET")

    def do_POST(self) -> None:
        self._tratar("POST")

    def do_PATCH(self) -> None:
        self._tratar("PATCH")

    def do_PUT(self) -> None:
        self._tratar("PUT")

    def do_DELETE(self) -> None:
        self._tratar("DELETE")

    # ------------------------------------------------------------ respostas

    def _responder(self, status: int, corpo: bytes, tipo: str, cache: str,
                   extras: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", cache)
        for nome, valor in (extras or {}).items():
            self.send_header(nome, valor)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _json(self, status: int, dados: Any, extras: dict[str, str] | None = None) -> None:
        self._responder(status, _serializar(dados), "application/json; charset=utf-8", "no-store", extras)

    # ------------------------------------------------------------ pedido

    def _um_cabecalho(self, nome: str) -> str | None:
        valores = self.headers.get_all(nome) or []
        if len(valores) > 1:
            raise ErroHttp(403, "Pedido com cabeçalhos repetidos recusado.")
        return valores[0] if valores else None

    def _tem_corpo_pendente(self) -> bool:
        return (not self._corpo_lido and (self.headers.get("Transfer-Encoding") is not None
                                          or self.headers.get("Content-Length", "0").strip() not in ("", "0")))

    def _descartar_corpo(self, maximo: int = 8 * LIMITE_CORPO) -> None:
        """Joga fora o corpo não lido e fecha a conexão depois da resposta.

        Sem ler, o resto do corpo viraria o "próximo pedido"; e fechar com dados não lidos
        faz o Windows mandar RST, que pode sumir com a resposta antes de o navegador lê-la.
        Lê em pedaços de 64 KB (sem guardar), até `maximo` bytes.
        """
        self.close_connection = True
        if self._corpo_lido or self.headers.get("Transfer-Encoding") is not None:
            return
        try:
            restante = int(self.headers.get("Content-Length", "0"))
            if restante > maximo:
                return
            while restante > 0:
                bloco = self.rfile.read(min(65536, restante))
                if not bloco:
                    break
                restante -= len(bloco)
            self._corpo_lido = True
        except (ValueError, OSError):
            pass

    def _ler_corpo(self, metodo: str, limite: int = LIMITE_CORPO) -> bytes:
        if metodo not in ("POST", "PATCH", "PUT", "DELETE"):
            return b""
        tipo = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if tipo != "application/json":
            raise ErroHttp(415, "Envie os dados em JSON (Content-Type: application/json).")
        if self.headers.get("Transfer-Encoding") is not None:
            raise ErroHttp(411, "Informe o tamanho do corpo (Content-Length).")
        tamanhos = self.headers.get_all("Content-Length") or []
        if len(tamanhos) > 1:
            raise ErroHttp(400, "Pedido com tamanho repetido.")
        if not tamanhos:
            return b""
        try:
            tamanho = int(tamanhos[0].strip())
            if tamanho < 0:
                raise ValueError
        except ValueError:
            raise ErroHttp(400, "Tamanho do corpo inválido.") from None
        if tamanho > limite:
            raise ErroHttp(413, f"O pedido é grande demais (limite de {limite // (1024 * 1024)} MB).")
        corpo = self.rfile.read(tamanho)
        self._corpo_lido = True
        if len(corpo) != tamanho:
            self.close_connection = True
            raise ErroHttp(400, "O corpo do pedido chegou incompleto.")
        return corpo

    def _tratar(self, metodo: str) -> None:
        app = self.server.app
        self._corpo_lido = False
        pedido: Pedido | None = None
        vez_do_arquivo: threading.BoundedSemaphore | None = None
        try:
            caminho, _, consulta_bruta = self.path.split("#", 1)[0].partition("?")
            if not caminho.startswith("/") or caminho.startswith("//"):
                raise ErroHttp(404, "Não encontrado.")
            host = self._um_cabecalho("Host")
            if not app.host_permitido(host):
                raise ErroHttp(403, "Endereço não permitido. Abra o Entre Nós pelo atalho.")
            origem = self._um_cabecalho("Origin")
            if origem is not None and not app.origem_permitida(origem, host):
                raise ErroHttp(403, "Pedido vindo de outra página recusado.")
            if caminho != "/api" and not caminho.startswith("/api/"):
                if metodo != "GET":
                    raise ErroHttp(405, "Este endereço não aceita esse tipo de pedido.", {"Allow": "GET"})
                arquivo = arquivo_estatico(app.pasta_web, caminho)
                if arquivo is None and caminho in ("/", "/index.html"):
                    # A página sumiu (antivírus, instalação pela metade): um texto que diz o que
                    # fazer, em vez do JSON cru no navegador.
                    if self._tem_corpo_pendente():
                        self._descartar_corpo()
                    self._responder(404, PAGINA_SEM_ARQUIVOS.encode("utf-8"), TIPOS_MIME[".html"], "no-store")
                    return
                if arquivo is None:
                    raise ErroHttp(404, "Não encontrado.")
                conteudo = arquivo.read_bytes()
                if self._tem_corpo_pendente():
                    self._descartar_corpo()
                self._responder(200, conteudo, TIPOS_MIME[arquivo.suffix.lower()], "no-cache")
                return
            site = self.headers.get("Sec-Fetch-Site")
            if site is not None and site.strip().lower() not in ("same-origin", "none"):
                raise ErroHttp(403, "Pedido vindo de outra página recusado.")
            rota, grupos = achar_rota(metodo, caminho)
            if rota.limite_corpo > LIMITE_CORPO:
                # Até 64 MB: quem não tem sessão (ou não está no primeiro uso) nem chega a mandar.
                app.conferir_antes_do_corpo(rota, self.headers.get("X-EntreNos-Token"))
                # E um de cada vez: cada um custa centenas de MB antes de qualquer PIN. O corpo do
                # recusado é lido e jogado fora em pedaços (sem memória), para a resposta chegar.
                if not app.vez_do_arquivo.acquire(blocking=False):
                    self._descartar_corpo(rota.limite_corpo)
                    raise ErroHttp(503, ARQUIVO_OCUPADO)
                vez_do_arquivo = app.vez_do_arquivo
            bruto = self._ler_corpo(metodo, rota.limite_corpo)
            try:
                consulta = urllib.parse.parse_qs(consulta_bruta, keep_blank_values=True, max_num_fields=50)
            except ValueError:
                raise ErroHttp(400, "Parâmetros demais no endereço.") from None
            pedido = Pedido(metodo, caminho, consulta, bruto, self.headers.get("X-EntreNos-Token"), grupos,
                            campos_grandes=rota.campos_grandes)
            bruto = b""                  # só o pedido guarda os bytes (e os solta depois de ler o JSON)
            resposta = _serializar(app.atender(rota, pedido))
        except Exception as erro:  # noqa: BLE001 - todo erro vira resposta JSON
            if self._tem_corpo_pendente():
                self._descartar_corpo()
            falha = traduzir_erro(erro)
            self._json(falha.status, {**falha.extras, "erro": falha.mensagem}, falha.cabecalhos)
            return
        finally:
            if vez_do_arquivo is not None:
                vez_do_arquivo.release()
        if pedido.depois is not None:
            self.close_connection = True
        self._responder(200, resposta, "application/json; charset=utf-8", "no-store")
        if pedido.depois is not None:
            self.wfile.flush()
            pedido.depois()


def _serializar(dados: Any) -> bytes:
    return json.dumps(para_json(dados), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ServidorHTTP(ThreadingHTTPServer):
    """HTTP em 127.0.0.1. No Windows, porta exclusiva: ninguém mais escuta na mesma porta."""

    daemon_threads = True
    allow_reuse_address = os.name != "nt"
    allow_reuse_port = False

    def __init__(self, app: EntreNos, porta: int):
        self.app = app
        super().__init__((ENDERECO, porta), Manipulador)
        app.porta = self.server_address[1]
        app.servidor = self

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        # Sem socket.getfqdn(): a consulta de nome pode demorar segundos no Windows.
        self.server_name, self.server_port = ENDERECO, self.server_address[1]

    def handle_error(self, request: Any, client_address: Any) -> None:
        erro = sys.exc_info()[1]
        if erro is not None and not isinstance(erro, (ConnectionError, TimeoutError)):
            registrar_erro(erro)

    def server_close(self) -> None:
        super().server_close()
        self.app.encerrar()


def criar_servidor(pasta: Path | str | None = None, porta: int = config.PORTA_PADRAO,
                   **opcoes: Any) -> ServidorHTTP:
    """Servidor já escutando em 127.0.0.1:porta (0 = porta livre qualquer), com a vigia ligada.

    `opcoes` vão para EntreNos: atraso_pin, atraso_pin_maximo, minutos_bloqueio,
    intervalo_verificacao, hoje, pasta_web, minutos_area_ia (os testes usam valores pequenos).
    """
    app = EntreNos(pasta if pasta is not None else config.pasta_dados(), **opcoes)
    servidor = ServidorHTTP(app, porta)
    app.iniciar_vigia()
    return servidor


# ---------------------------------------------------------------- linha de comando

def configurar_log(pasta: Path) -> logging.Handler:
    """erros.log na pasta de dados (só criado quando houver erro)."""
    manipulador = logging.handlers.RotatingFileHandler(
        Path(pasta) / "erros.log", maxBytes=256 * 1024, backupCount=1, encoding="utf-8", delay=True)
    manipulador.setLevel(logging.WARNING)
    manipulador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _log.addHandler(manipulador)
    if _log.level == logging.NOTSET or _log.level > logging.WARNING:
        _log.setLevel(logging.WARNING)
    return manipulador


def e_entre_nos(porta: int, espera: float = 2.0) -> bool:
    """True se quem responde em 127.0.0.1:porta é um Entre Nós (GET /api/ping)."""
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with abridor.open(f"http://{ENDERECO}:{porta}/api/ping", timeout=espera) as resposta:
            dados = json.loads(resposta.read(4096).decode("utf-8"))
        return isinstance(dados, dict) and dados.get("app") == "entre-nos"
    except Exception:  # noqa: BLE001 - qualquer falha = não é o Entre Nós
        return False


def _avisar(mensagem: str) -> None:
    """Mensagem para a pessoa: no console, ou numa janela quando aberto pelo pythonw."""
    if sys.stderr is not None:
        print(mensagem, file=sys.stderr)
        return
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, mensagem, config.NOME_APP, 0x30)
        except Exception:  # noqa: BLE001
            pass


def _abrir_navegador(porta: int) -> None:
    try:
        webbrowser.open(f"http://{ENDERECO}:{porta}/")
    except Exception as erro:  # noqa: BLE001
        registrar_erro(erro)


def _marcar_aberto() -> Any:
    """Cria o mutex que o instalador procura (`AppMutex` e `CheckForMutexes` do EntreNos.iss).

    Assim o instalador e o desinstalador avisam "feche o Entre Nós" em vez de trocar arquivos
    de um programa aberto, e o desinstalador não apaga a pasta de dados com o diário aberto na
    memória. Vale também pelo iniciar.py (pythonw): ele usa a mesma pasta de dados, e o atalho
    do instalado, achando a porta ocupada por ele, só abre o navegador. O Windows desfaz o
    mutex sozinho quando o processo termina.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        return ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_ABERTO)
    except Exception:  # noqa: BLE001 - sem o mutex o app funciona; só o instalador não avisa
        return None


class _LeitorArgumentos(argparse.ArgumentParser):
    """Sem console (EntreNos.exe e pythonw), a ajuda e os erros de argumento aparecem numa janela."""

    def _print_message(self, message: str, file: Any = None) -> None:
        if message and (file or sys.stderr) is None:
            _avisar(message.strip())
            return
        super()._print_message(message, file)

    def error(self, message: str) -> None:  # type: ignore[override]
        if sys.stderr is None:
            _avisar(f"{self.format_usage().strip()}\n\n{self.prog}: {message}")
            raise SystemExit(2)
        super().error(message)


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    programa = "EntreNos.exe" if config.empacotado() else "iniciar.py"
    leitor = _LeitorArgumentos(prog=programa, description="Abre o Entre Nós neste computador.")
    leitor.add_argument("--sem-navegador", action="store_true", help="não abre o navegador")
    leitor.add_argument("--porta", type=int, default=None,
                        help=f"porta local (padrão {config.PORTA_PADRAO}, depois as seguintes)")
    leitor.add_argument("--dados", default=None, help="pasta dos dados (padrão %%LOCALAPPDATA%%\\EntreNos)")
    return leitor.parse_args(argv)


def faltam_arquivos_da_pagina(pasta_web: Path | None = None) -> list[str]:
    """Arquivos de web/ que deveriam estar lá e não estão (o antivírus pode tê-los levado)."""
    pasta_web = Path(pasta_web) if pasta_web is not None else config.PASTA_WEB
    return [nome for nome in ARQUIVOS_DA_PAGINA if not (pasta_web / nome).is_file()]


def main(argv: list[str] | None = None) -> int:
    global _MUTEX
    args = _argumentos(argv)
    faltam = faltam_arquivos_da_pagina()
    if faltam:
        # Sem a página não há o que abrir: avisa já, em vez de um navegador em branco (seção 18.6).
        _avisar(f"Faltam arquivos do Entre Nós em {config.PASTA_WEB}: {', '.join(faltam)}.\n\n"
                "O antivírus pode tê-los colocado em quarentena. Reinstale o Entre Nós (rode o instalador "
                "de novo por cima) ou peça à TI para restaurar os arquivos da quarentena. Não desligue o "
                "antivírus.\n\nSeu diário não foi afetado: ele fica em outra pasta (%LOCALAPPDATA%\\EntreNos).")
        return 1
    pasta = Path(os.path.abspath(args.dados)) if args.dados else config.pasta_dados()
    pasta.mkdir(parents=True, exist_ok=True)
    manipulador = configurar_log(pasta)
    gancho_threads, gancho_sys = threading.excepthook, sys.excepthook
    threading.excepthook = lambda a: registrar_erro(a.exc_value) if a.exc_value is not None else None
    sys.excepthook = lambda tipo, erro, tb: registrar_erro(erro)
    try:
        if args.porta is not None:
            portas = [args.porta]
        else:
            portas = list(range(config.PORTA_PADRAO, config.PORTA_PADRAO + PORTAS_EXTRAS + 1))
        servidor = None
        for porta in portas:
            try:
                servidor = criar_servidor(pasta, porta)
                break
            except OSError:
                if porta and e_entre_nos(porta):
                    # Já está aberto: só mostra.
                    if not args.sem_navegador:
                        _abrir_navegador(porta)
                    return 0
        if servidor is None:
            if len(portas) == 1:
                _avisar(f"Não consegui abrir o Entre Nós: a porta {portas[0]} está ocupada por outro programa.")
            else:
                _avisar(f"Não consegui abrir o Entre Nós: as portas {portas[0]} a {portas[-1]} estão "
                        "ocupadas por outros programas.")
            return 1
        porta = servidor.server_address[1]
        _MUTEX = _marcar_aberto()
        print(f"{config.NOME_APP} aberto em http://{ENDERECO}:{porta}/ (Ctrl+C para fechar)")
        if not args.sem_navegador:
            threading.Timer(0.3, _abrir_navegador, (porta,)).start()
        try:
            servidor.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            servidor.server_close()
        return 0
    except Exception as erro:
        if sys.stderr is not None:
            raise
        # Sem console (EntreNos.exe, pythonw): anota em erros.log, sem conteúdo, e avisa numa janela.
        registrar_erro(erro)
        _avisar("O Entre Nós fechou por causa de um erro. O tipo do erro ficou anotado no arquivo "
                f"erros.log, em {pasta}.")
        return 1
    finally:
        threading.excepthook, sys.excepthook = gancho_threads, gancho_sys
        _log.removeHandler(manipulador)
        manipulador.close()


__all__ = ["ENDERECO", "EntreNos", "ErroHttp", "MINUTOS_AREA_IA", "Manipulador", "ROTAS", "ServidorHTTP",
           "arquivo_estatico", "atraso_do_pin", "contexto_sugestao", "criar_servidor", "e_entre_nos",
           "faltam_arquivos_da_pagina", "main", "registrar_erro", "traduzir_erro"]
