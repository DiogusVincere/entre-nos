"""Monta o site do Entre Nós no celular (PWA, DESIGN.md seção 21) na pasta site/.

    python construir_web.py                # monta site/ (o comando de build do Cloudflare Pages)
    python construir_web.py --destino X    # monta em outra pasta (os testes usam uma temporária)
    python construir_web.py --licencas     # extrai de novo as licenças das rodas do Pyodide

O que faz, nesta ordem (qualquer problema para tudo, sem site pela metade):
1. confere o Pyodide guardado em web/pyodide/<versão>/: o SHA-256 de cada arquivo do núcleo (os do
   pacote npm, conferidos quando entraram no projeto) e o de cada roda contra o pyodide-lock.json; e
   que as licenças das rodas estão em licencas/, iguais às de dentro delas;
2. gera web/entrenos-py.zip: o pacote entrenos/ sem o servidor.py, o __main__.py e o __pycache__,
   com datas fixas (o mesmo código dá o mesmo zip, e a versão do site só muda quando algo muda);
3. copia web/ para site/, grava o sw.js com a versão e as listas do pré-cache, e o _headers do
   Cloudflare Pages com a mesma CSP do servidor do computador (lida do entrenos/servidor.py, sem
   importá-lo) e os tipos dos arquivos.

Só biblioteca padrão e nada de rede: roda no build do Cloudflare (Python 3.8 ou mais novo) e no PC.
Não gera .exe nem mexe no instalador. O site guarda só o código: nenhum dado do diário passa por aqui.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

RAIZ = Path(os.path.dirname(os.path.abspath(__file__)))
WEB = RAIZ / "web"
PACOTE = RAIZ / "entrenos"
SITE = RAIZ / "site"

VERSAO_PYODIDE = "314.0.7"
PASTA_PYODIDE = "pyodide/" + VERSAO_PYODIDE
PACOTES_PYTHON = ("cryptography",)          # o que o trabalhador carrega (as dependências vêm do lock)
NOME_ZIP = "entrenos-py.zip"
MARCA_SITE = ".entrenos-site"               # só apaga uma pasta de destino que este script criou
PASTA_LICENCAS = "licencas"

# O núcleo do Pyodide 314.0.7 (pacote npm pyodide@314.0.7, integridade npm conferida ao entrar no
# projeto, seção 21.1). Atualizar o Pyodide = pasta nova inteira e esta tabela nova.
SHA256_PYODIDE = {
    "pyodide.mjs": "6f1d60f7bf529beb300f0f47983c921d3982363640ba20af0e38efdddbc66109",
    "pyodide.asm.mjs": "f7cdc8ece80678ceb712f8e65ebe6d3a83203a180c399865f49612a051693635",
    "pyodide.asm.wasm": "cc36e3cab04fdfc9a63ff13eb52eae2b911bf46c025cc7b281f394bd3de1d5e6",
    "python_stdlib.zip": "fa1957e5777068fc4f7437f96d860ae2fbe9c19732ba06c84e004ec16dd7dd7a",
    "pyodide-lock.json": "5dc2fc119108bc148c7457dc86e7675b5c87e1cafd420b9c34c1eaef7b36c010",
    "LICENSE-pyodide.txt": "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5",
}
# O que o navegador baixa para abrir o Python (o service worker guarda no cache do Pyodide).
NUCLEO_PYODIDE = ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")

FORA_DO_ZIP = {"servidor.py", "__main__.py"}  # o transporte do computador não vai para o celular
DATA_FIXA = (2026, 1, 1, 0, 0, 0)

# Tipos que o Cloudflare Pages pode não acertar sozinho (o servidor do PC usa os mesmos).
TIPOS = {
    ".mjs": "text/javascript; charset=utf-8",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
    ".zip": "application/zip",
    ".whl": "application/zip",
}
# A CSP vai UMA vez, em "/*" (seção 22.2). Antes ela ia só em "/", "/index.html", "/sw.js" e
# "/trabalhador.mjs": um caminho qualquer (https://site/x) que o Cloudflare respondesse com o
# index.html abriria a página inteira SEM CSP. Numa regra só não há duas políticas juntadas com
# vírgula (o Cloudflare junta o mesmo cabeçalho de duas regras). O 404.html do site faz caminho
# desconhecido responder 404, e não a página.
# A política do site é a do computador mais 'wasm-unsafe-eval' (o Worker compila o Pyodide, e a CSP
# do script do Worker é a mesma de "/*"). No computador a página não roda WebAssembly.
CAMINHOS_SEM_CACHE = ("/", "/index.html", "/sw.js")
PAGINAS_FORA_DO_PRECACHE = (".html",)   # o Cloudflare redireciona /x.html para /x: redirect não vai para o cache
CABECALHOS_GERAIS = (
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    # same-origin-allow-popups: a janela de login do Google (sincronização, seção 22) precisa
    # conversar com a página que a abriu.
    ("Cross-Origin-Opener-Policy", "same-origin-allow-popups"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)

INICIO_BLOCO_SW = "// <construir_web>"
FIM_BLOCO_SW = "// </construir_web>"


class ErroDeConstrucao(Exception):
    """Algo impede montar o site; a mensagem diz o quê."""


# ---------------------------------------------------------------- utilidades

def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _gravar_texto(caminho: Path, texto: str) -> None:
    """Texto em UTF-8 com fim de linha LF em qualquer sistema (o mesmo site no Windows e no build)."""
    with open(caminho, "w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(texto)


def _relativo(caminho: Path, base: Path) -> str:
    return caminho.relative_to(base).as_posix()


def _ignorado(nome: str) -> bool:
    return nome.startswith(".") or nome == "__pycache__" or nome.endswith((".pyc", ".pyo"))


def ler_csp(servidor: Path | None = None) -> str:
    """A CSP do servidor do computador (`CSP = (...)` em entrenos/servidor.py), lida pela árvore do
    código: importar o servidor puxaria o cryptography, que o build do site não tem."""
    caminho = servidor or (PACOTE / "servidor.py")
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    for no in arvore.body:
        if isinstance(no, ast.Assign) and any(isinstance(a, ast.Name) and a.id == "CSP" for a in no.targets):
            valor = ast.literal_eval(no.value)
            if isinstance(valor, str) and valor:
                return valor
    raise ErroDeConstrucao(f"Não achei a CSP (CSP = \"...\") em {caminho}.")


# ---------------------------------------------------------------- Pyodide

def _lock(pasta: Path) -> dict:
    try:
        return json.loads((pasta / "pyodide-lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        raise ErroDeConstrucao(f"Não consegui ler {pasta / 'pyodide-lock.json'}: {erro}") from None


def rodas_necessarias(lock: dict, pacotes: tuple[str, ...] = PACOTES_PYTHON) -> list[dict]:
    """Os pacotes do lock que o trabalhador carrega, com as dependências, em ordem de nome."""
    todos = lock.get("packages") or {}
    vistos: dict[str, dict] = {}
    pendentes = list(pacotes)
    while pendentes:
        nome = pendentes.pop()
        if nome in vistos:
            continue
        info = todos.get(nome)
        if not isinstance(info, dict) or not info.get("file_name") or not info.get("sha256"):
            raise ErroDeConstrucao(f"O pyodide-lock.json não descreve o pacote {nome!r}.")
        vistos[nome] = info
        pendentes.extend(info.get("depends") or [])
    return [vistos[n] for n in sorted(vistos)]


def conferir_pyodide(web: Path = WEB) -> list[str]:
    """Confere o Pyodide guardado em web/pyodide/<versão>/. Devolve os nomes das rodas usadas."""
    raiz = web / "pyodide"
    outras = sorted(p.name for p in raiz.iterdir() if p.is_dir() and p.name != VERSAO_PYODIDE) if raiz.is_dir() else []
    if outras:
        raise ErroDeConstrucao(f"Há outra versão do Pyodide em {raiz} ({', '.join(outras)}). Uma versão só: "
                               "atualizar é trocar a pasta inteira (e a tabela SHA256_PYODIDE).")
    pasta = web / PASTA_PYODIDE
    if not pasta.is_dir():
        raise ErroDeConstrucao(f"Falta a pasta do Pyodide: {pasta}.")
    for nome, esperado in SHA256_PYODIDE.items():
        arquivo = pasta / nome
        if not arquivo.is_file():
            raise ErroDeConstrucao(f"Falta {arquivo}.")
        if sha256(arquivo) != esperado:
            raise ErroDeConstrucao(f"{arquivo} não é o arquivo conferido do Pyodide {VERSAO_PYODIDE} (SHA-256 diferente).")
    rodas = rodas_necessarias(_lock(pasta))
    nomes = []
    for info in rodas:
        arquivo = pasta / info["file_name"]
        if not arquivo.is_file():
            raise ErroDeConstrucao(f"Falta a roda {arquivo.name} em {pasta}.")
        if sha256(arquivo) != info["sha256"]:
            raise ErroDeConstrucao(f"A roda {arquivo.name} não confere com o SHA-256 do pyodide-lock.json.")
        nomes.append(arquivo.name)
    sobrando = sorted(p.name for p in pasta.glob("*.whl") if p.name not in nomes)
    if sobrando:
        raise ErroDeConstrucao(f"Rodas que o Entre Nós não usa em {pasta}: {', '.join(sobrando)}.")
    return nomes


# ---------------------------------------------------------------- licenças

_RE_LICENCA = re.compile(r"(?:^|/)(?:LICEN[CS]E|COPYING|NOTICE|AUTHORS)[^/]*$", re.IGNORECASE)


def licencas_das_rodas(pasta: Path) -> dict[str, bytes]:
    """{nome do arquivo em licencas/: conteúdo} tirado de dentro de cada roda (dist-info)."""
    saida: dict[str, bytes] = {}
    for roda in sorted(pasta.glob("*.whl")):
        partes = roda.name.split("-")
        projeto = f"{partes[0]}-{partes[1]}"
        with zipfile.ZipFile(roda) as z:
            for nome in sorted(z.namelist()):
                if ".dist-info/" not in nome or not _RE_LICENCA.search(nome):
                    continue
                base = nome.rsplit("/", 1)[-1]
                destino = f"{projeto}-{base}" if base.lower().endswith(".txt") else f"{projeto}-{base}.txt"
                saida[destino] = z.read(nome)
    return saida


def extrair_licencas(web: Path = WEB) -> list[Path]:
    """Grava em licencas/ as licenças de dentro das rodas (as que existem lá são trocadas)."""
    pasta = web / PASTA_PYODIDE
    destino = pasta / PASTA_LICENCAS
    destino.mkdir(exist_ok=True)
    gravados = []
    for nome, conteudo in licencas_das_rodas(pasta).items():
        (destino / nome).write_bytes(conteudo)
        gravados.append(destino / nome)
    return gravados


def conferir_licencas(web: Path = WEB) -> None:
    pasta = web / PASTA_PYODIDE
    esperadas = licencas_das_rodas(pasta)
    if not esperadas:
        raise ErroDeConstrucao("As rodas do Pyodide não trouxeram nenhum arquivo de licença.")
    for nome, conteudo in esperadas.items():
        arquivo = pasta / PASTA_LICENCAS / nome
        if not arquivo.is_file() or arquivo.read_bytes() != conteudo:
            raise ErroDeConstrucao(f"Falta (ou está diferente) a licença {arquivo}. Rode: python construir_web.py --licencas")
    if not (pasta / PASTA_LICENCAS / "LEIA-ME.txt").is_file():
        raise ErroDeConstrucao(f"Falta {pasta / PASTA_LICENCAS / 'LEIA-ME.txt'} (a lista dos componentes e licenças).")


# ---------------------------------------------------------------- o código Python do celular

def arquivos_do_pacote(pacote: Path = PACOTE) -> list[Path]:
    """Os .py de entrenos/ que vão para o celular (sem o transporte do computador)."""
    saida = []
    for pasta, subpastas, arquivos in os.walk(pacote):
        subpastas[:] = sorted(s for s in subpastas if not _ignorado(s))
        for nome in sorted(arquivos):
            caminho = Path(pasta) / nome
            if not nome.endswith(".py") or _ignorado(nome):
                continue
            if caminho.parent == pacote and nome in FORA_DO_ZIP:
                continue
            saida.append(caminho)
    return saida


def montar_zip_python(destino: Path, pacote: Path = PACOTE) -> int:
    """Grava `destino` com entrenos/ (caminhos "entrenos/..."), sempre igual para o mesmo código."""
    arquivos = arquivos_do_pacote(pacote)
    if not any(a.name == "ponte.py" and a.parent == pacote for a in arquivos):
        raise ErroDeConstrucao(f"Falta {pacote / 'ponte.py'}: sem ela o celular não conversa com o Python.")
    temporario = destino.with_name(destino.name + ".tmp")
    with zipfile.ZipFile(temporario, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for caminho in arquivos:
            info = zipfile.ZipInfo(f"{pacote.name}/{_relativo(caminho, pacote)}", date_time=DATA_FIXA)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, caminho.read_bytes())
    os.replace(temporario, destino)
    return len(arquivos)


# ---------------------------------------------------------------- o site

def _destino_seguro(destino: Path) -> Path:
    destino = Path(os.path.abspath(destino))
    proibidos = {Path(os.path.abspath(p)) for p in (RAIZ, WEB, PACOTE, Path(destino.anchor))}
    if destino in proibidos or WEB in destino.parents or destino in WEB.parents or destino in PACOTE.parents:
        raise ErroDeConstrucao(f"Não monto o site em {destino}: escolha uma pasta só para ele (como site/).")
    if destino.exists():
        if not destino.is_dir():
            raise ErroDeConstrucao(f"{destino} existe e não é uma pasta.")
        if any(destino.iterdir()) and not (destino / MARCA_SITE).is_file():
            raise ErroDeConstrucao(f"{destino} já tem arquivos que não são de um site montado por este script; "
                                   "não apago. Escolha outra pasta ou esvazie esta.")
    return destino


def arquivos_da_web(web: Path = WEB) -> list[str]:
    """Os caminhos (relativos, com /) do que vai de web/ para o site, menos o entrenos-py.zip, que é
    gerado de novo a cada build. Sem arquivos ocultos nem __pycache__."""
    saida = []
    for pasta, subpastas, arquivos in os.walk(web):
        subpastas[:] = sorted(s for s in subpastas if not _ignorado(s))
        for nome in sorted(arquivos):
            if _ignorado(nome):
                continue
            relativo = _relativo(Path(pasta) / nome, web)
            if relativo != NOME_ZIP:
                saida.append(relativo)
    return saida


def _copiar_web(web: Path, destino: Path) -> list[str]:
    copiados = arquivos_da_web(web)
    for relativo in copiados:
        alvo = destino / relativo
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(web / relativo, alvo)
    return copiados


def listas_do_precache(arquivos: list[str], rodas: list[str]) -> tuple[list[str], list[str]]:
    """(app, pyodide): o que o service worker guarda na instalação. A página entra como "./" (o
    Cloudflare Pages redireciona /index.html para /, e redirect não vai para o cache)."""
    app = ["./"]
    for relativo in sorted(arquivos):
        if relativo.startswith("pyodide/") or relativo in ("index.html", "sw.js", "_headers", MARCA_SITE) \
                or relativo.endswith(PAGINAS_FORA_DO_PRECACHE):
            continue
        app.append(relativo)
    pyodide = [f"{PASTA_PYODIDE}/{nome}" for nome in (*NUCLEO_PYODIDE, *sorted(rodas))]
    return app, pyodide


def versao_do_site(destino: Path, arquivos: list[str]) -> str:
    """12 hex do SHA-256 de todos os arquivos (menos o próprio sw.js e o _headers): muda o sw.js, e o
    navegador oferece "nova versão", sempre que qualquer arquivo do site muda."""
    h = hashlib.sha256()
    for relativo in sorted(arquivos):
        if relativo in ("sw.js", "_headers", MARCA_SITE):
            continue
        h.update(relativo.encode("utf-8") + b"\0" + sha256(destino / relativo).encode("ascii") + b"\n")
    return h.hexdigest()[:12]


def bloco_do_sw(versao: str, app: list[str], pyodide: list[str]) -> str:
    def lista(itens: list[str]) -> str:
        return "[\n" + "".join(f"  {json.dumps(i, ensure_ascii=False)},\n" for i in itens) + "]"
    return (f"{INICIO_BLOCO_SW}\n"
            f"const VERSAO = {json.dumps(versao)};\n"
            f"const VERSAO_PYODIDE = {json.dumps(VERSAO_PYODIDE)};\n"
            f"const ARQUIVOS_APP = {lista(app)};\n"
            f"const ARQUIVOS_PYODIDE = {lista(pyodide)};\n"
            f"{FIM_BLOCO_SW}")


def bloco_de_desenvolvimento(web: Path = WEB) -> str:
    """O bloco do web/sw.js guardado no projeto: versão "dev" e as listas do que web/ tem (com o
    entrenos-py.zip). Assim o web/ servido direto, sem build, também funciona; o teste confere que
    ele acompanha os arquivos (python construir_web.py --sw-dev o refaz)."""
    rodas = [info["file_name"] for info in rodas_necessarias(_lock(web / PASTA_PYODIDE))]
    app, pyodide = listas_do_precache([*arquivos_da_web(web), NOME_ZIP], rodas)
    return bloco_do_sw("dev", app, pyodide)


def trocar_bloco_do_sw(texto: str, bloco: str) -> str:
    inicio, fim = texto.find(INICIO_BLOCO_SW), texto.find(FIM_BLOCO_SW)
    if inicio < 0 or fim < inicio:
        raise ErroDeConstrucao(f"O sw.js não tem o bloco {INICIO_BLOCO_SW} ... {FIM_BLOCO_SW}.")
    return texto[:inicio] + bloco + texto[fim + len(FIM_BLOCO_SW):]


def csp_do_site(csp: str) -> str:
    """A CSP do computador com 'wasm-unsafe-eval' no script-src (o Pyodide do Worker)."""
    trocada = csp.replace("script-src 'self';", "script-src 'self' 'wasm-unsafe-eval';", 1)
    if trocada == csp:
        raise ErroDeConstrucao("A CSP do servidor.py não tem \"script-src 'self';\" para acrescentar o WebAssembly.")
    return trocada


def gerar_headers(csp: str, arquivos: list[str]) -> str:
    """O _headers do Cloudflare Pages: a CSP do site e os cabeçalhos gerais em "/*" (uma regra só para
    a CSP), sem cache para a página e o sw.js, Pyodide imutável e os tipos que ele pode errar."""
    linhas = ["# Gerado por construir_web.py (não edite à mão): cabeçalhos do Cloudflare Pages, DESIGN.md seções 21.3 e 22.2.",
              "/*", f"  Content-Security-Policy: {csp}"]
    linhas += [f"  {nome}: {valor}" for nome, valor in CABECALHOS_GERAIS]
    for caminho in CAMINHOS_SEM_CACHE:
        linhas += ["", caminho, "  Cache-Control: no-cache"]
        tipo = TIPOS.get(os.path.splitext(caminho)[1])
        if tipo:
            linhas.append(f"  Content-Type: {tipo}")
    linhas += ["", f"/{PASTA_PYODIDE}/*", "  Cache-Control: public, max-age=31536000, immutable"]
    for relativo in sorted(arquivos):
        caminho = "/" + relativo
        tipo = TIPOS.get(os.path.splitext(relativo)[1].lower())
        if tipo and caminho not in CAMINHOS_SEM_CACHE:
            linhas += ["", caminho, f"  Content-Type: {tipo}"]
    return "\n".join(linhas) + "\n"


def construir(destino: Path = SITE, web: Path = WEB, pacote: Path = PACOTE) -> dict:
    """Monta o site em `destino`. Devolve um resumo ({versao, arquivos, bytes, ...})."""
    rodas = conferir_pyodide(web)
    conferir_licencas(web)
    csp = csp_do_site(ler_csp(pacote / "servidor.py"))
    destino = _destino_seguro(destino)
    if not all((web / nome).is_file() for nome in ("sw.js", "index.html", "404.html", "oauth.html", "oauth.js")):
        raise ErroDeConstrucao(f"Faltam arquivos da página em {web} (index.html, 404.html, oauth.html, oauth.js, sw.js).")

    modulos = montar_zip_python(web / NOME_ZIP, pacote)
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    (destino / MARCA_SITE).write_text("Montado por construir_web.py; pode apagar.\n", encoding="utf-8")
    arquivos = _copiar_web(web, destino)
    shutil.copyfile(web / NOME_ZIP, destino / NOME_ZIP)
    arquivos.append(NOME_ZIP)

    app, pyodide = listas_do_precache(arquivos, rodas)
    faltando = [a for a in [*app[1:], *pyodide] if not (destino / a).is_file()]
    if faltando:
        raise ErroDeConstrucao(f"O pré-cache cita arquivos que não estão no site: {', '.join(faltando)}.")
    versao = versao_do_site(destino, arquivos)
    sw = (web / "sw.js").read_text(encoding="utf-8")
    _gravar_texto(destino / "sw.js", trocar_bloco_do_sw(sw, bloco_do_sw(versao, app, pyodide)))
    _gravar_texto(destino / "_headers", gerar_headers(csp, arquivos))
    total = sum((destino / a).stat().st_size for a in arquivos)
    return {"destino": str(destino), "versao": versao, "arquivos": len(arquivos), "bytes": total,
            "modulos_python": modulos, "rodas": rodas, "precache_app": len(app), "precache_pyodide": len(pyodide)}


def main(argv: list[str] | None = None) -> int:
    leitor = argparse.ArgumentParser(description="Monta o site do Entre Nós (PWA) em site/.")
    leitor.add_argument("--destino", default=str(SITE), help="pasta do site (padrão: site/ na raiz)")
    leitor.add_argument("--licencas", action="store_true",
                        help="extrai de novo as licenças das rodas do Pyodide para licencas/ e sai")
    leitor.add_argument("--sw-dev", action="store_true",
                        help="refaz as listas do web/sw.js (versão dev) a partir do que web/ tem e sai")
    args = leitor.parse_args(argv)
    try:
        if args.licencas:
            for caminho in extrair_licencas():
                print(caminho)
            return 0
        if args.sw_dev:
            sw = WEB / "sw.js"
            _gravar_texto(sw, trocar_bloco_do_sw(sw.read_text(encoding="utf-8"), bloco_de_desenvolvimento()))
            print(sw)
            return 0
        resumo = construir(Path(args.destino))
    except ErroDeConstrucao as erro:
        print(f"Não montei o site: {erro}", file=sys.stderr)
        return 1
    print(f"Site montado em {resumo['destino']}: versão {resumo['versao']}, {resumo['arquivos']} arquivos, "
          f"{resumo['bytes'] / (1024 * 1024):.1f} MB; Python: {resumo['modulos_python']} módulos; "
          f"Pyodide {VERSAO_PYODIDE} com {', '.join(resumo['rodas'])}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
