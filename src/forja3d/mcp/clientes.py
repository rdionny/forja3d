"""Liga e desliga o Forja3D no Codex, pela tela de configurações.

Mexe só no bloco [mcp_servers.forja3d] do config.toml do Codex. O resto do arquivo fica byte a byte
como estava, e nada é gravado se o arquivo lido de volta não for o de antes mais, ou menos, esse bloco.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from forja3d.configuracao import EMPACOTADO, RAIZ

NOME = "forja3d"
# O Codex abre o servidor com um ambiente reduzido. Sem estas, o Forja3D não acharia as configurações do usuário.
VARIAVEIS_PASSADAS = ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP")
_TABELA = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


class ErroNoCodex(Exception):
    """A mensagem é para o usuário."""


def config_do_codex() -> Path:
    base = os.environ.get("CODEX_HOME")
    return Path(base) / "config.toml" if base else Path.home() / ".codex" / "config.toml"


def codex_instalado() -> bool:
    return config_do_codex().parent.is_dir()


def comando_do_servidor() -> tuple[str, list[str]]:
    """Instalado, é o próprio Forja3D.exe. Pelo código, o python do venv com o script que acha o pacote sozinho."""
    if EMPACOTADO:
        return sys.executable, ["--mcp"]
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    return str(python), [str(RAIZ / "scripts" / "servidor_mcp.py")]


def comando_do_claude_code() -> str:
    comando, argumentos = comando_do_servidor()
    return f'claude mcp add {NOME} --scope user -- "{comando}" ' + " ".join(f'"{a}"' for a in argumentos)


def _texto_toml(valor: str) -> str:
    # Entre aspas simples, a barra invertida do Windows não precisa de escape.
    return f"'{valor}'" if "'" not in valor and "\n" not in valor else json.dumps(valor)


def bloco_do_codex(quebra: str = "\n") -> str:
    comando, argumentos = comando_do_servidor()
    linhas = [
        f"[mcp_servers.{NOME}]",
        f"command = {_texto_toml(comando)}",
        f"args = [{', '.join(_texto_toml(a) for a in argumentos)}]",
        f"env_vars = [{', '.join(json.dumps(v) for v in VARIAVEIS_PASSADAS)}]",
        "startup_timeout_sec = 60",
        "tool_timeout_sec = 900",
    ]
    return quebra.join(linhas) + quebra


def _e_do_forja3d(nome_da_tabela: str) -> bool:
    nome = nome_da_tabela.replace('"', "").replace("'", "").replace(" ", "")
    return nome == f"mcp_servers.{NOME}" or nome.startswith(f"mcp_servers.{NOME}.")


def _sem_o_bloco(texto: str) -> str:
    """Tira as tabelas do Forja3D, inclusive subtabelas como [mcp_servers.forja3d.env], e mais nada."""
    saida = []
    dentro = False
    for linha in texto.splitlines(keepends=True):
        tabela = _TABELA.match(linha)
        if tabela:
            dentro = _e_do_forja3d(tabela.group(1))
        if not dentro:
            saida.append(linha)
    return "".join(saida)


def _sem_servidores_vazios(dados: dict[str, Any]) -> dict[str, Any]:
    if dados.get("mcp_servers") == {}:
        dados = {k: v for k, v in dados.items() if k != "mcp_servers"}
    return dados


def estado_no_codex() -> str:
    """"ausente", "desligado", "ligado" ou "outro", quando o bloco existe mas aponta para outro Forja3D."""
    caminho = config_do_codex()
    if not caminho.parent.is_dir():
        return "ausente"
    try:
        servidor = tomllib.loads(caminho.read_text(encoding="utf-8")).get("mcp_servers", {}).get(NOME) if caminho.is_file() else None
    except (OSError, tomllib.TOMLDecodeError):
        return "desligado"
    if not servidor:
        return "desligado"
    return "ligado" if (servidor.get("command"), servidor.get("args")) == (comando_do_servidor()[0], comando_do_servidor()[1]) else "outro"


def ligar_no_codex(ligar: bool) -> str:
    """Põe ou tira o Forja3D do Codex e devolve o que fez, para mostrar na tela."""
    caminho = config_do_codex()
    if not caminho.parent.is_dir():
        return "O Codex não foi encontrado neste computador."
    try:
        original = caminho.read_bytes().decode("utf-8") if caminho.is_file() else ""
        antes = tomllib.loads(original)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as erro:
        raise ErroNoCodex(f"Não consegui ler {caminho}: {erro}. Nada foi mudado.") from erro

    quebra = "\r\n" if "\r\n" in original else "\n"
    novo = _sem_o_bloco(original)
    if ligar:
        bloco = bloco_do_codex(quebra)
        if novo.strip():
            novo = novo.rstrip("\r\n") + quebra + quebra
        novo += bloco

    esperado = copy.deepcopy(antes)
    servidores = dict(esperado.get("mcp_servers", {}))
    servidores.pop(NOME, None)
    if ligar:
        servidores[NOME] = tomllib.loads(bloco)["mcp_servers"][NOME]
    esperado["mcp_servers"] = servidores
    try:
        depois = tomllib.loads(novo)
    except tomllib.TOMLDecodeError:
        depois = None
    if depois is None or _sem_servidores_vazios(depois) != _sem_servidores_vazios(esperado):
        raise ErroNoCodex(f"O {caminho.name} do Codex tem um formato que eu não soube editar com segurança, então não mexi nele. Use o bloco do README.")
    if novo == original:
        return "O Forja3D já estava ligado no Codex." if ligar else "O Forja3D já estava fora do Codex."

    if caminho.is_file():
        (caminho.with_name(caminho.name + ".antes-do-forja3d")).write_bytes(original.encode("utf-8"))
    temporario = caminho.with_name(caminho.name + ".forja3d.tmp")
    temporario.write_bytes(novo.encode("utf-8"))
    temporario.replace(caminho)
    if ligar:
        return "Forja3D ligado no Codex. Abra uma conversa nova no Codex para ele carregar as ferramentas."
    return "Forja3D tirado do Codex. As conversas novas não vão mais ver as ferramentas."
