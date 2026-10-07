"""Recursos do Entre Nós: o registro e os pontos de encaixe (DESIGN.md, seção 20.0).

Cada recurso mora em `entrenos/recursos/<nome>.py` (mais `web/telas/<nome>.js`, `web/telas/<nome>.css`
e `testes/test_<nome>.py`) e NÃO edita os arquivos centrais (banco, nucleo, motor, alertas, modelos,
app.js, index.html). O núcleo chama os recursos por aqui, sempre de forma tolerante: um recurso que
não importa, ou que levanta erro num gancho, não derruba o app (o erro vai para o `erros.log` só com
o tipo e os pontos do código, nunca com texto do usuário).

O que um módulo de recurso pode definir (tudo opcional, menos o NOME dos módulos da lista fixa):

- `NOME`: igual ao nome do arquivo (é o prefixo das rotas, `/api/<NOME>/...`, e a chave dos extras);
- `ROTULO`: nome para a tela nos avisos ("Ideias de presentes"); sem ele, vale o NOME;
- `ESQUEMA: list[str | Callable[[sqlite3.Connection], None]]` e `ESQUEMA_VERSAO: int` (= len(ESQUEMA)):
  `ESQUEMA[v]` leva as tabelas do recurso da versão v para v + 1 (DDL; vários comandos separados por
  ";"). O banco guarda a versão em `recursos_esquema(nome, versao)`. Só tabelas e índices do próprio
  recurso: mexer numa tabela central, criar gatilho ou visão é recusado (DESIGN 20.0);
- `TIPOS_EXTRAS`: os `tipo` que os extras do recurso podem ter (sem ele, só `tipo == NOME`);
- `extrair(texto, referencia, pessoas, datas, itens, conhecimento) -> list[dict]`: extras para a
  revisão, cada um `{tipo, resumo, trecho, ...}` com o `trecho` literal do texto;
- `gravar_extras(banco, registro_id, extras_confirmados, referencia)`: grava o que o usuário
  confirmou (dentro da mesma transação do registro; ValueError = 400 e nada é gravado);
- `marca_privado(extras) -> bool`: True = o registro vira 🔒 (fica fora da IA);
- `TIPOS_FORA_PELO_TEXTO`: os tipos de extra (que pedem 🔒) que deixam o TEXTO fora da IA mesmo sem
  confirmar na revisão: o ciclo (todos), o desentendimento, a resposta de "Nós dois". Sem ele, nenhum
  (só o confirmado conta). Ver `fica_fora_pelo_texto` e a seção 20.10.1;

- `rotas() -> list[RotaRecurso]` (use `rota(...)`): handlers `fn(nucleo, pedido, banco)`;
- `cartoes_hoje(banco, referencia) -> list[Alerta | dict]` (use `cartao(...)` para o dict);
- `cartoes_mostrados(banco, referencia, cartoes)`: quais cartões do recurso passaram pelo anti-excesso
  e aparecem no Hoje agora (`[{chave, nivel, tipo, dados}]`, a chave sem o prefixo "<NOME>:"). Quem
  grava algo por ter mostrado um cartão ("já sugeri esta pergunta") grava aqui, não no `cartoes_hoje`;
- `exportar(banco) -> dict`, `importar(banco, dados, mapa_registros) -> dict` (juntar sem duplicar;
  `mapa_registros` = id no arquivo -> id aqui; devolva `{"novos": n, "texto": "..."}` para a tela);
- `ao_apagar_registro(banco, registro_id)`: o que depende do registro some junto (depois disso, as
  linhas das tabelas do recurso com coluna `registro_id` igual ao apagado saem de qualquer jeito);
- `fora_da_ia(banco) -> set[int]`: registros que nunca vão para a IA (somados aos 🔒);
- `discretos(banco) -> set[int]`: registros cujo texto não aparece fora da tela do próprio recurso
  (Hoje → Últimos registros, menções em Pessoas, ligações "mesma pessoa"): o ciclo;
- `reservado(texto) -> bool`: o texto tem palavras que só aparecem na tela do recurso (o ciclo). Os
  itens de um registro discreto passam por aqui nas telas centrais (`texto_reservado`, seção 20.10).

`extrair` recebe em `conhecimento.consultar(sql, parametros)` a leitura do diário aberto (só leitura,
como `Banco.consultar`; None quando não há diário, nos testes que chamam o motor sozinho).

Tudo aqui roda no navegador também (Pyodide): nada de thread, sleep, rede ou Windows.
"""
from __future__ import annotations

import copy
import importlib
import json
import logging
import os
import re
import threading
import traceback
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from types import ModuleType
from typing import Any, Callable, Iterator

from ..modelos import para_json

# A lista fixa (seção 20): a ordem é a dos extras na revisão e a das chamadas aos ganchos.
NOMES: tuple[str, ...] = ("perguntas", "presentes", "promessas", "rapida", "lugares", "marcos", "semana",
                          "ciclo", "eu")
GANCHOS: tuple[str, ...] = ("extrair", "gravar_extras", "marca_privado", "rotas", "cartoes_hoje", "cartoes_mostrados",
                            "exportar", "importar", "ao_apagar_registro", "fora_da_ia", "discretos", "reservado")

SESSAO = "sessao"           # o padrão: token + diário aberto, com a trava do app (um pedido por vez)
LIVRE = "livre"             # token conferido; o handler cuida da trava (`with nucleo.trava:`)
ACESSOS = (SESSAO, LIVRE)
METODOS = ("GET", "POST", "PATCH", "PUT", "DELETE")
NIVEIS = ("importante", "lembrete", "sugestao")

MAX_EXTRAS = 200                # extras confirmados num registro, somando os recursos
MAX_EXTRAS_POR_RECURSO = 50     # o que um recurso pode propor para um texto

AVISO_EXTRAIR = "Uma parte do Entre Nós ({rotulo}) não conseguiu ler este texto; o resto foi organizado normalmente."
AVISO_PRIVADO = ("🔒 Este registro vai ficar fora da IA: ele tem informações que nunca vão para a IA. "
                 "Você pode remover essas informações na revisão.")
AVISO_PRIVADO_GRAVADO = "🔒 Este registro ficou fora da IA: ele tem informações que nunca vão para a IA."
AVISO_GRAVAR = "Não consegui guardar a parte de {rotulo} deste registro; o resto foi salvo."
AVISO_EXPORTAR = "Os dados de {rotulo} não puderam ser exportados."
AVISO_IMPORTAR = "Os dados de {rotulo} no arquivo não puderam ser juntados e ficaram de fora."
AVISO_IMPORTAR_DESCONHECIDOS = ("O arquivo tem dados de partes do Entre Nós que este aparelho não tem; "
                                "eles ficaram de fora.")
EXTRAS_FORMATO = "“extras” precisa ser um objeto com uma lista de informações por parte do Entre Nós."
EXTRA_DESCONHECIDO = "Há informações de um tipo que este Entre Nós não conhece."
EXTRA_SEM_TRECHO = "Cada informação precisa citar um trecho que está no texto do registro."
EXTRAS_DEMAIS = f"Informações demais num registro só (o limite é {MAX_EXTRAS})."

_RE_NOME = re.compile(r"[a-z][a-z0-9_]{0,30}")
_RE_TIPO = re.compile(r"[a-z][a-z0-9_]{0,39}")

_log = logging.getLogger("entrenos.recursos")


class RecursoIndisponivel(RuntimeError):
    """Algum recurso não abriu (ou falhou ao dizer o que fica fora da IA): na dúvida, nada vai."""


@dataclass(frozen=True)
class RotaRecurso:
    """Uma rota do recurso. `caminho` é relativo: "itens/{id}" vira /api/<NOME>/itens/{id}."""

    metodo: str
    caminho: str
    funcao: Callable[[Any, Any, Any], Any]
    acesso: str = SESSAO


def rota(metodo: str, caminho: str, funcao: Callable[[Any, Any, Any], Any], acesso: str = SESSAO) -> RotaRecurso:
    """`rota("GET", "itens", listar)` -> GET /api/<NOME>/itens. `{id}` no caminho vira `pedido.id`."""
    return RotaRecurso(str(metodo).upper(), str(caminho), funcao, acesso)


def cartao(chave: str, nivel: str, texto: str, porque: str = "", relevancia: int = 50,
           data: date | None = None, registro_ids: list[int] | tuple[int, ...] = (), tipo: str | None = None,
           dados: dict | None = None) -> dict:
    """Um cartão do Hoje. A `chave` ganha o prefixo "<NOME>:" no centro (é a que "dispensar" guarda).
    `tipo` escolhe o desenho da tela (`registrarCartaoHoje`); `dados` vai junto para ela (JSON)."""
    return {"chave": chave, "nivel": nivel, "texto": texto, "porque": porque, "relevancia": relevancia,
            "data": data, "registro_ids": list(registro_ids), "tipo": tipo, "dados": dict(dados or {})}


# ---------------------------------------------------------------- log (sem conteúdo)

def registrar_erro(erro: BaseException, recurso: str | None = None, onde: str | None = None) -> None:
    """Como `nucleo.registrar_erro`: só o tipo do erro e os pontos do código, mais qual recurso e
    qual gancho. A mensagem da exceção fica de fora (pode trazer texto do usuário)."""
    try:
        linhas = [f"recurso {recurso or '?'}" + (f" ({onde})" if onde else "")]
        atual: BaseException | None = erro
        vistos: set[int] = set()
        while atual is not None and id(atual) not in vistos:
            vistos.add(id(atual))
            linhas.append(type(atual).__qualname__ if atual is erro else f"causado por {type(atual).__qualname__}")
            for quadro in traceback.extract_tb(atual.__traceback__):
                linhas.append(f"  {os.path.basename(quadro.filename)}:{quadro.lineno} em {quadro.name}")
            atual = atual.__cause__ or atual.__context__
        _log.error("\n".join(linhas))
    except Exception:  # noqa: BLE001 - o log nunca derruba o app
        pass


# ---------------------------------------------------------------- o registro

_trava = threading.RLock()
_modulos: tuple[ModuleType, ...] | None = None
_falhas: dict[str, str] = {}             # nome -> tipo do erro (sem mensagem)
_geracao = 0                             # muda a cada troca da lista (o núcleo e o banco guardam caches)
_carregando = False
_parciais: list[ModuleType] = []


def _validar(modulo: ModuleType, nome_arquivo: str | None) -> None:
    """ValueError/TypeError se o módulo não cumpre o contrato (aí ele fica de fora, com log)."""
    nome = getattr(modulo, "NOME", None)
    if not isinstance(nome, str) or not _RE_NOME.fullmatch(nome):
        raise ValueError("NOME do recurso inválido")
    if nome_arquivo is not None and nome != nome_arquivo:
        raise ValueError("NOME do recurso diferente do nome do arquivo")
    rotulo_ = getattr(modulo, "ROTULO", None)
    if rotulo_ is not None and (not isinstance(rotulo_, str) or not rotulo_.strip()):
        raise ValueError("ROTULO inválido")
    passos = getattr(modulo, "ESQUEMA", None)
    versao = getattr(modulo, "ESQUEMA_VERSAO", None)
    if passos is None:
        if versao not in (None, 0):
            raise ValueError("ESQUEMA_VERSAO sem ESQUEMA")
    else:
        if not isinstance(passos, (list, tuple)) or not all(isinstance(p, str) or callable(p) for p in passos):
            raise TypeError("ESQUEMA precisa ser uma lista de DDL (texto) ou funções")
        if versao is not None and (isinstance(versao, bool) or not isinstance(versao, int) or versao != len(passos)):
            raise ValueError("ESQUEMA_VERSAO diferente do número de passos do ESQUEMA")
    tipos = getattr(modulo, "TIPOS_EXTRAS", None)
    if tipos is not None:
        if isinstance(tipos, str) or not all(isinstance(t, str) and _RE_TIPO.fullmatch(t) for t in tipos):
            raise ValueError("TIPOS_EXTRAS inválidos")
    pelo_texto = getattr(modulo, "TIPOS_FORA_PELO_TEXTO", None)
    if pelo_texto is not None and (isinstance(pelo_texto, str)
                                   or not all(isinstance(t, str) and _RE_TIPO.fullmatch(t) for t in pelo_texto)):
        raise ValueError("TIPOS_FORA_PELO_TEXTO inválidos")
    for gancho in GANCHOS:
        valor = getattr(modulo, gancho, None)
        if valor is not None and not callable(valor):
            raise TypeError(f"o gancho {gancho} não é uma função")


def _carregar() -> None:
    global _modulos, _carregando, _geracao
    _carregando = True
    _parciais.clear()
    falhas: dict[str, str] = {}
    try:
        for nome in NOMES:
            try:
                modulo = importlib.import_module(f"{__name__}.{nome}")
                _validar(modulo, nome)
            except Exception as erro:  # noqa: BLE001 - um recurso quebrado não derruba o app
                falhas[nome] = type(erro).__name__
                registrar_erro(erro, nome, "importar o módulo")
                continue
            _parciais.append(modulo)
        _modulos = tuple(_parciais)
        _falhas.clear()
        _falhas.update(falhas)
        _geracao += 1
    finally:
        _carregando = False
        _parciais.clear()


def carregados() -> tuple[ModuleType, ...]:
    """Os recursos que abriram e cumprem o contrato, na ordem de NOMES (carrega na primeira vez)."""
    with _trava:
        if _modulos is None:
            if _carregando:              # um recurso chamou o banco enquanto carregava
                return tuple(_parciais)
            _carregar()
        return _modulos or ()


def recarregar() -> None:
    """Esquece e carrega de novo a lista fixa (para os testes; o app carrega uma vez só)."""
    global _modulos
    with _trava:
        _modulos = None
        carregados()


def falhas() -> dict[str, str]:
    """{nome: tipo do erro} dos recursos que não abriram (o app segue sem eles)."""
    carregados()
    with _trava:
        return dict(_falhas)


def geracao() -> int:
    carregados()
    return _geracao


def nomes_carregados() -> list[str]:
    return [m.NOME for m in carregados()]


def recurso(nome: Any) -> ModuleType | None:
    if not isinstance(nome, str):
        return None
    return next((m for m in carregados() if m.NOME == nome), None)


@contextmanager
def usando(*modulos: ModuleType, com_os_fixos: bool = False) -> Iterator[None]:
    """Para os testes: troca a lista de recursos por `modulos` (com os da lista fixa também, se
    `com_os_fixos`). Módulo que não cumpre o contrato fica de fora e entra em `falhas()`."""
    global _modulos, _falhas, _geracao
    with _trava:
        antes = (carregados(), dict(_falhas))
        validos = list(antes[0]) if com_os_fixos else []
        novas_falhas = dict(antes[1]) if com_os_fixos else {}
        for modulo in modulos:
            try:
                _validar(modulo, None)
            except Exception as erro:  # noqa: BLE001
                novas_falhas[str(getattr(modulo, "NOME", modulo.__name__))] = type(erro).__name__
                registrar_erro(erro, str(getattr(modulo, "NOME", "?")), "validar")
                continue
            validos = [m for m in validos if m.NOME != modulo.NOME] + [modulo]
        _modulos, _falhas = tuple(validos), novas_falhas
        _geracao += 1
    try:
        yield
    finally:
        with _trava:
            _modulos, _falhas = antes[0], antes[1]
            _geracao += 1


def com_gancho(gancho: str) -> list[tuple[str, Callable]]:
    """[(NOME, função)] dos recursos que definem o gancho, na ordem da lista."""
    saida = []
    for modulo in carregados():
        funcao = getattr(modulo, gancho, None)
        if callable(funcao):
            saida.append((modulo.NOME, funcao))
    return saida


def esquema(modulo: ModuleType) -> list[str | Callable]:
    """Os passos do esquema do recurso (lista vazia: o recurso não tem tabelas)."""
    return list(getattr(modulo, "ESQUEMA", None) or [])


def tipos_extras(modulo: ModuleType) -> frozenset[str]:
    tipos = getattr(modulo, "TIPOS_EXTRAS", None)
    return frozenset(tipos) if tipos is not None else frozenset({modulo.NOME})


def rotulo(modulo: ModuleType | str | None) -> str:
    if isinstance(modulo, str):
        modulo = recurso(modulo) or modulo
    if isinstance(modulo, ModuleType):
        valor = getattr(modulo, "ROTULO", None)
        return valor.strip() if isinstance(valor, str) and valor.strip() else modulo.NOME
    return str(modulo or "?")


# ---------------------------------------------------------------- extras (revisão e gravação)

def _nfc(texto: str) -> str:
    return unicodedata.normalize("NFC", texto)


def _extra_limpo(bruto: Any, tipos: frozenset[str], texto: str) -> dict | None:
    """Um extra conferido: tipo conhecido, trecho literal do texto, resumo em texto e tudo em JSON.
    Chaves que começam com "_" são da fundação (a tela e o recurso não as mandam)."""
    if not isinstance(bruto, dict):
        return None
    tipo, trecho, resumo = bruto.get("tipo"), bruto.get("trecho"), bruto.get("resumo")
    if not isinstance(tipo, str) or tipo not in tipos or not isinstance(trecho, str):
        return None
    trecho = _nfc(trecho)
    if not trecho.strip() or trecho not in texto:
        return None
    if resumo is None:
        resumo = ""
    if not isinstance(resumo, str):
        return None
    limpo = {k: v for k, v in bruto.items() if isinstance(k, str) and not k.startswith("_")}
    limpo.update(tipo=tipo, trecho=trecho, resumo=_nfc(resumo).strip() or trecho)
    try:
        limpo = para_json(limpo)
        json.dumps(limpo, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return None
    return limpo


def extras_do_texto(texto: str, referencia: date, pessoas: list, datas: list, itens: list,
                    conhecimento: Any) -> tuple[dict[str, list[dict]], list[str]]:
    """Chama o `extrair` de cada recurso (com cópias: ninguém mexe na proposta das regras).

    Devolve ({NOME: [extras conferidos]}, avisos). Erro num recurso vira aviso sem conteúdo e não
    derruba a análise; extra que não cumpre o contrato (tipo, trecho literal) é descartado. Extra que
    faz o registro virar 🔒 ganha `"_privado": true` (a tela trava a caixinha e esconde o ✨)."""
    extras: dict[str, list[dict]] = {}
    avisos: list[str] = []
    algum_privado = False
    for modulo in carregados():
        funcao = getattr(modulo, "extrair", None)
        if not callable(funcao):
            continue
        nome = modulo.NOME
        try:
            brutos = funcao(texto, referencia, copy.deepcopy(pessoas), copy.deepcopy(datas),
                            copy.deepcopy(itens), copy.deepcopy(conhecimento))
            if brutos is None:
                brutos = []
            if not isinstance(brutos, (list, tuple)):
                raise TypeError("extrair precisa devolver uma lista")
        except Exception as erro:  # noqa: BLE001 - um recurso não derruba a análise
            registrar_erro(erro, nome, "extrair")
            avisos.append(AVISO_EXTRAIR.format(rotulo=rotulo(modulo)))
            continue
        tipos = tipos_extras(modulo)
        validos = [e for e in (_extra_limpo(b, tipos, texto) for b in brutos[:MAX_EXTRAS_POR_RECURSO]) if e]
        if len(validos) < min(len(brutos), MAX_EXTRAS_POR_RECURSO):
            _log.warning("recurso %s (extrair): %d extras fora do contrato descartados", nome,
                         min(len(brutos), MAX_EXTRAS_POR_RECURSO) - len(validos))
        if not validos:
            continue
        marcar = getattr(modulo, "marca_privado", None)
        for extra in validos:
            if callable(marcar) and _pede_privado(modulo, marcar, [extra]):
                extra["_privado"] = True
                algum_privado = True
        extras[nome] = validos
    if algum_privado:
        avisos.append(AVISO_PRIVADO)
    return extras, avisos


def _pede_privado(modulo: ModuleType, marcar: Callable, extras: list[dict]) -> bool:
    """O recurso quer o registro 🔒 por causa destes extras? Na dúvida (erro), sim."""
    try:
        return bool(marcar(copy.deepcopy(extras)))
    except Exception as erro:  # noqa: BLE001 - na dúvida, fica fora da IA
        registrar_erro(erro, modulo.NOME, "marca_privado")
        return True


def extras_confirmados(valor: Any, texto: str) -> dict[str, list[dict]]:
    """Os `extras` de POST /api/registros conferidos: recurso que grava extras, tipo conhecido,
    trecho literal do texto. ValueError (400) no primeiro problema; nada é gravado."""
    if valor is None:
        return {}
    if not isinstance(valor, dict):
        raise ValueError(EXTRAS_FORMATO)
    saida: dict[str, list[dict]] = {}
    total = 0
    for nome, lista in valor.items():
        modulo = recurso(nome)
        if modulo is None or not callable(getattr(modulo, "gravar_extras", None)):
            raise ValueError(EXTRA_DESCONHECIDO)
        if not isinstance(lista, list) or not all(isinstance(e, dict) for e in lista):
            raise ValueError(EXTRAS_FORMATO)
        total += len(lista)
        if total > MAX_EXTRAS:
            raise ValueError(EXTRAS_DEMAIS)
        tipos = tipos_extras(modulo)
        limpos = []
        for bruto in lista:
            if bruto.get("tipo") not in tipos:
                raise ValueError(EXTRA_DESCONHECIDO)
            limpo = _extra_limpo(bruto, tipos, texto)
            if limpo is None:
                raise ValueError(EXTRA_SEM_TRECHO)
            limpos.append(limpo)
        if limpos:
            saida[nome] = limpos
    return saida


def fica_fora_pelo_texto(extras: dict[str, list[dict]]) -> bool:
    """O texto fica fora da IA mesmo sem confirmar nada? (seção 20.10.1) Sim quando há um extra
    `_privado` de um tipo que o recurso lista em `TIPOS_FORA_PELO_TEXTO` (quem não lista, não tira: é
    escolha de cada recurso). O ciclo lista todos os dele; a área Eu, o desentendimento; as perguntas, a
    resposta de "Nós dois". Uma atitude sua não confirmada ("levei café pra ela") não tira o registro da IA."""
    for nome, lista in (extras or {}).items():
        modulo = recurso(nome)
        tipos = getattr(modulo, "TIPOS_FORA_PELO_TEXTO", None) if modulo is not None else None
        if not tipos:
            continue
        for e in lista or []:
            if isinstance(e, dict) and e.get("_privado") and e.get("tipo") in tipos:
                return True
    return False


def recursos_que_marcam_privado(extras: dict[str, list[dict]]) -> list[str]:
    """NOMEs dos recursos cujo `marca_privado` pede 🔒 para estes extras (erro conta como pedido)."""
    nomes = []
    for nome, lista in extras.items():
        modulo = recurso(nome)
        marcar = getattr(modulo, "marca_privado", None) if modulo is not None else None
        if lista and callable(marcar) and _pede_privado(modulo, marcar, lista):
            nomes.append(nome)
    return nomes


def gravar_extras(banco: Any, registro_id: int, extras: dict[str, list[dict]], referencia: date) -> list[str]:
    """Chama o `gravar_extras` de cada recurso dentro da transação do registro (um ponto de
    salvamento por recurso). ValueError sobe (400: nada é gravado); outro erro desfaz só a parte do
    recurso e vira aviso. Devolve os avisos."""
    avisos: list[str] = []
    for nome, lista in extras.items():
        modulo = recurso(nome)
        funcao = getattr(modulo, "gravar_extras", None) if modulo is not None else None
        if not callable(funcao):
            raise ValueError(EXTRA_DESCONHECIDO)
        try:
            with banco.transacao():
                funcao(banco, registro_id, copy.deepcopy(lista), referencia)
        except ValueError:
            raise
        except Exception as erro:  # noqa: BLE001 - o registro fica; só a parte do recurso sai
            registrar_erro(erro, nome, "gravar_extras")
            avisos.append(AVISO_GRAVAR.format(rotulo=rotulo(modulo)))
    return avisos


# ---------------------------------------------------------------- IA, exportar, importar, apagar

def fora_da_ia(banco: Any) -> set[int]:
    """Registros que os recursos mandam deixar fora da IA. RecursoIndisponivel quando não dá para
    saber com segurança: algum recurso da lista não abriu, ou o gancho falhou (aí nada vai)."""
    if falhas():
        raise RecursoIndisponivel("um recurso não abriu")
    fora: set[int] = set()
    for nome, funcao in com_gancho("fora_da_ia"):
        try:
            ids = funcao(banco)
            if ids is None:
                continue
            for i in ids:
                if isinstance(i, bool) or not isinstance(i, int):
                    raise TypeError("fora_da_ia precisa devolver ids de registro")
                fora.add(i)
        except Exception as erro:  # noqa: BLE001 - na dúvida, nada vai para a IA
            registrar_erro(erro, nome, "fora_da_ia")
            raise RecursoIndisponivel("fora_da_ia falhou") from None
    return fora


def discretos(banco: Any) -> set[int]:
    """Registros cujo texto fica só na tela do próprio recurso (o ciclo, seção 20.8): o Hoje mostra
    "🔒 Um registro reservado" no lugar do começo do texto, Pessoas não cita a frase e as ligações
    "mesma pessoa" não os usam. Na dúvida (um recurso não abriu ou o gancho falhou), todos os 🔒
    também ficam assim. Nunca levanta."""
    saida: set[int] = set()
    duvida = bool(falhas())
    for nome, funcao in com_gancho("discretos"):
        try:
            ids = funcao(banco)
            for i in ids or ():
                if isinstance(i, bool) or not isinstance(i, int):
                    raise TypeError("discretos precisa devolver ids de registro")
                saida.add(i)
        except Exception as erro:  # noqa: BLE001 - na dúvida, mais discreto
            registrar_erro(erro, nome, "discretos")
            duvida = True
    if duvida:
        try:
            saida |= set(banco.registros_privados())
        except Exception as erro:  # noqa: BLE001
            registrar_erro(erro, None, "discretos (registros privados)")
    return saida


def texto_reservado(texto: Any) -> bool:
    """Algum recurso diz que o texto tem palavras que só aparecem na tela dele (o ciclo)? As telas
    centrais (Hoje, Como ela está, Pessoas, e os recursos que leem os itens) não mostram um item de
    registro discreto com essas palavras no resumo e não citam o trecho que as tem. Na dúvida (um
    recurso não abriu ou o gancho falhou), sim. Nunca levanta."""
    if not isinstance(texto, str) or not texto.strip():
        return False
    if falhas():
        return True
    for nome, funcao in com_gancho("reservado"):
        try:
            if funcao(texto):
                return True
        except Exception as erro:  # noqa: BLE001 - na dúvida, reservado
            registrar_erro(erro, nome, "reservado")
            return True
    return False


# Campos com a frase literal de um registro (o que as telas citam).
_CHAVES_DE_FRASE = ("trecho", "reacao", "frase_literal")


def sem_frases_reservadas(valor: Any, discretos: set[int] | frozenset[int]) -> Any:
    """Cópia de `valor` (dicts e listas, como uma tela recebe) em que cada objeto com `registro_id` de
    um registro discreto perde as frases com palavras reservadas (`trecho`, `reacao`: viram "" e o
    objeto ganha `"reservado": true`; a tela mostra "frase reservada" e o link do registro). Para os
    recursos que citam frases do diário fora da tela do recurso dono (Presentes, Lugares; 20.10)."""
    if not discretos:
        return valor
    if isinstance(valor, list):
        return [sem_frases_reservadas(v, discretos) for v in valor]
    if isinstance(valor, tuple):
        return tuple(sem_frases_reservadas(v, discretos) for v in valor)
    if not isinstance(valor, dict):
        return valor
    saida = {k: sem_frases_reservadas(v, discretos) for k, v in valor.items()}
    rid = saida.get("registro_id")
    if isinstance(rid, int) and not isinstance(rid, bool) and rid in discretos:
        for chave in _CHAVES_DE_FRASE:
            frase = saida.get(chave)
            if isinstance(frase, str) and texto_reservado(frase):
                saida[chave] = ""
                saida["reservado"] = True
    return saida


def exportar_dados(banco: Any) -> tuple[dict[str, Any], list[str]]:
    """({NOME: dados em JSON}, avisos) de cada recurso que exporta."""
    saida: dict[str, Any] = {}
    avisos: list[str] = []
    for nome, funcao in com_gancho("exportar"):
        try:
            dados = para_json(funcao(banco))
            json.dumps(dados, ensure_ascii=False).encode("utf-8")
        except Exception as erro:  # noqa: BLE001
            registrar_erro(erro, nome, "exportar")
            avisos.append(AVISO_EXPORTAR.format(rotulo=rotulo(nome)))
            continue
        saida[nome] = dados
    return saida, avisos


def importar_dados(banco: Any, dados: dict[str, Any], mapa_registros: dict[int, int]) -> tuple[dict[str, Any], list[str]]:
    """Junta os dados de cada recurso que veio no arquivo (dentro da transação da importação, um
    ponto de salvamento por recurso). Erro desfaz só a parte do recurso e vira aviso."""
    resumos: dict[str, Any] = {}
    avisos: list[str] = []
    conhecidos = set()
    for nome, funcao in com_gancho("importar"):
        conhecidos.add(nome)
        if nome not in dados:
            continue
        try:
            with banco.transacao():
                resumo = funcao(banco, copy.deepcopy(dados[nome]), dict(mapa_registros))
            resumo = para_json(resumo) if isinstance(resumo, dict) else {}
        except Exception as erro:  # noqa: BLE001
            registrar_erro(erro, nome, "importar")
            avisos.append(AVISO_IMPORTAR.format(rotulo=rotulo(nome)))
            continue
        if _tem_dados(dados[nome]) or _conta_algo(resumo):
            resumos[nome] = resumo
    if set(dados) - conhecidos:
        avisos.append(AVISO_IMPORTAR_DESCONHECIDOS)
    return resumos, avisos


def _tem_dados(valor: Any) -> bool:
    """O arquivo trouxe alguma linha para o recurso? Todo recurso exporta a sua parte, mesmo vazia
    (`{"versao": 1, "lugares": [], "mencoes": []}`): só listas com conteúdo contam (as linhas das
    tabelas). O número da versão e os ajustes (que nenhum recurso importa) não."""
    if isinstance(valor, list):
        return bool(valor)
    if isinstance(valor, dict):
        return any(isinstance(v, list) and bool(v) for v in valor.values())
    return False


def _conta_algo(resumo: dict) -> bool:
    """O resumo do recurso tem algum número maior que zero (novos, ignorados...)."""
    return any(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in resumo.values())


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """Chama o `ao_apagar_registro` de cada recurso (um ponto de salvamento cada). Erro fica no
    log; o banco ainda apaga as linhas com `registro_id` das tabelas dos recursos."""
    for nome, funcao in com_gancho("ao_apagar_registro"):
        try:
            with banco.transacao():
                funcao(banco, registro_id)
        except Exception as erro:  # noqa: BLE001
            registrar_erro(erro, nome, "ao_apagar_registro")


def cartoes_mostrados(banco: Any, referencia: date, por_recurso: dict[str, list[dict]]) -> None:
    """Chama o `cartoes_mostrados` de cada recurso que teve cartão no Hoje (um ponto de salvamento
    cada; erro só no log: o Hoje aparece do mesmo jeito)."""
    for nome, funcao in com_gancho("cartoes_mostrados"):
        cartoes = por_recurso.get(nome)
        if not cartoes:
            continue
        try:
            with banco.transacao():
                funcao(banco, referencia, copy.deepcopy(cartoes))
        except Exception as erro:  # noqa: BLE001
            registrar_erro(erro, nome, "cartoes_mostrados")


def cartoes_brutos(banco: Any, referencia: date) -> list[tuple[str, list[Any]]]:
    """[(NOME, cartões como o recurso devolveu)]; quem falhou fica de fora (com log)."""
    saida = []
    for nome, funcao in com_gancho("cartoes_hoje"):
        try:
            brutos = funcao(banco, referencia)
            saida.append((nome, list(brutos or [])))
        except Exception as erro:  # noqa: BLE001 - o Hoje aparece sem os cartões dele
            registrar_erro(erro, nome, "cartoes_hoje")
    return saida


__all__ = [
    "ACESSOS", "AVISO_PRIVADO", "AVISO_PRIVADO_GRAVADO", "GANCHOS", "LIVRE", "MAX_EXTRAS", "METODOS", "NIVEIS", "NOMES",
    "RecursoIndisponivel", "RotaRecurso", "SESSAO", "ao_apagar_registro", "carregados", "cartao", "cartoes_brutos",
    "cartoes_mostrados",
    "com_gancho", "discretos", "esquema", "exportar_dados", "extras_confirmados", "extras_do_texto", "falhas",
    "fica_fora_pelo_texto",

    "fora_da_ia",
    "geracao", "gravar_extras", "importar_dados", "nomes_carregados", "recarregar", "recurso", "texto_reservado",
    "sem_frases_reservadas",

    "recursos_que_marcam_privado", "registrar_erro", "rota", "rotulo", "tipos_extras", "usando",
]
