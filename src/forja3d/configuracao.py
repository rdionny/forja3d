"""Configuração do Forja3D: padrões do projeto, preferências do usuário e ambiente."""
from __future__ import annotations

import json
import os
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EMPACOTADO = getattr(sys, "frozen", False)
# Instalado, RAIZ é a pasta do programa e os dados que vão com ele ficam em `_internal`.
RAIZ = Path(sys.executable).parent if EMPACOTADO else Path(__file__).resolve().parents[2]
PASTA_CONFIG = Path(getattr(sys, "_MEIPASS", RAIZ)) / "config"
ORCA_PADRAO = RAIZ / "ferramentas" / "OrcaSlicer_2.4.2" / ("orca-slicer.exe" if os.name == "nt" else "orca-slicer")
MODELO_IMAGEM_PADRAO = "gemini-3.1-flash-image"
PROVEDOR_PADRAO = "Padrão"


def _appdata_do_windows() -> str | None:
    """Quem abre o servidor MCP, como o Codex, pode passar um ambiente sem APPDATA e sem a pasta do usuário. O Windows ainda sabe onde ela fica."""
    if os.name != "nt":
        return None
    import ctypes

    caminho = ctypes.create_unicode_buffer(260)
    pasta_appdata = 0x1A
    return caminho.value if ctypes.windll.shell32.SHGetFolderPathW(None, pasta_appdata, None, 0, caminho) == 0 else None


def pasta_usuario() -> Path:
    base = os.environ.get("APPDATA") or _appdata_do_windows()
    return (Path(base) if base else Path.home()) / "Forja3D"


def pasta_projetos_padrao() -> Path:
    """Instalado, as peças ficam em Documentos: desinstalar não pode levar o trabalho de ninguém."""
    if not EMPACOTADO:
        return RAIZ / "projetos"
    from platformdirs import user_documents_dir

    return Path(user_documents_dir()) / "Forja3D"


@dataclass(frozen=True)
class Provedor:
    nome: str
    endpoint: str
    api_key: str = field(default="", repr=False)


@dataclass(frozen=True)
class ConfigIA:
    endpoint: str
    api_key: str = field(repr=False)
    modelo: str
    timeout_s: float = 240.0
    max_tentativas_rede: int = 2
    temperatura: float = 0.2
    esforco: dict[str, str] = field(default_factory=dict)
    modelo_imagem: str = MODELO_IMAGEM_PADRAO
    provedor: str = PROVEDOR_PADRAO
    provedor_imagem: str = PROVEDOR_PADRAO
    # Vazios: a imagem usa o mesmo provedor do chat.
    endpoint_imagem: str = ""
    api_key_imagem: str = field(default="", repr=False)


@dataclass(frozen=True)
class Configuracao:
    ia: ConfigIA
    max_chamadas_por_pedido: int = 25
    max_reparos_mesmo_erro: int = 3
    caminho_orca: Path = ORCA_PADRAO
    pasta_projetos: Path = field(default_factory=pasta_projetos_padrao)
    provedores: tuple[Provedor, ...] = ()
    # Desligado, o servidor MCP recusa toda ferramenta, mesmo que o Codex ou o Claude ainda o abram.
    mcp_ligado: bool = True
    # Endereço compatível com a OpenAI que transforma o áudio do microfone em texto. Vazio, o microfone só avisa.
    transcricao: str = ""
    # A página de downloads em que o app instalado procura versões novas. Vazio, não procura.
    atualizacao: str = ""

    @property
    def ia_configurada(self) -> bool:
        return bool(self.ia.endpoint and self.ia.modelo)

    @property
    def todos_os_provedores(self) -> tuple[Provedor, ...]:
        return self.provedores or (Provedor(self.ia.provedor, self.ia.endpoint, self.ia.api_key),)

    @property
    def chaves(self) -> tuple[str, ...]:
        """Todas as chaves em uso, para o filtro de logs."""
        todas = (self.ia.api_key, self.ia.api_key_imagem, *(p.api_key for p in self.provedores))
        return tuple(dict.fromkeys(c for c in todas if c))


def _ler_toml(caminho: Path) -> dict[str, Any]:
    if not caminho.exists():
        return {}
    return tomllib.loads(caminho.read_text(encoding="utf-8"))


def _mesclar(base: dict[str, Any], novo: dict[str, Any]) -> dict[str, Any]:
    resultado = dict(base)
    for chave, valor in novo.items():
        if isinstance(valor, dict) and isinstance(resultado.get(chave), dict):
            resultado[chave] = _mesclar(resultado[chave], valor)
        else:
            resultado[chave] = valor
    return resultado


def _padroes() -> dict[str, Any]:
    if EMPACOTADO:
        # Nem olha para ia_padrao.toml: ali fica a chave de quem desenvolve.
        return _ler_toml(PASTA_CONFIG / "ia_padrao.pacote.toml")
    return _mesclar(_ler_toml(PASTA_CONFIG / "ia_padrao.example.toml"), _ler_toml(PASTA_CONFIG / "ia_padrao.toml"))


def carregar() -> Configuracao:
    """Prioridade: variáveis de ambiente, preferências do usuário e padrões do projeto."""
    dados = _mesclar(_padroes(), _ler_toml(pasta_usuario() / "config.toml"))

    ia = dict(dados.get("ia", {}))
    for variavel, chave in (
        ("FORJA3D_IA_ENDPOINT", "endpoint"),
        ("FORJA3D_IA_API_KEY", "api_key"),
        ("FORJA3D_IA_MODELO", "modelo"),
        ("FORJA3D_IA_MODELO_IMAGEM", "modelo_imagem"),
    ):
        if os.environ.get(variavel):
            ia[chave] = os.environ[variavel]

    # `[ia]` guarda o provedor Padrão. Os demais ficam em `[[provedores]]`.
    padrao = Provedor(PROVEDOR_PADRAO, str(ia.get("endpoint", "")).rstrip("/"), str(ia.get("api_key", "")))
    provedores = [padrao]
    for item in dados.get("provedores", []):
        if item.get("nome") and item["nome"] != PROVEDOR_PADRAO:
            provedores.append(Provedor(str(item["nome"]), str(item.get("endpoint", "")).rstrip("/"), str(item.get("api_key", ""))))
    por_nome = {p.nome: p for p in provedores}
    chat = por_nome.get(str(ia.get("provedor")), padrao)
    imagem = por_nome.get(str(ia.get("provedor_imagem")), padrao)

    agente = dados.get("agente", {})
    app = dados.get("app", {})
    return Configuracao(
        ia=ConfigIA(
            endpoint=chat.endpoint,
            api_key=chat.api_key,
            modelo=str(ia.get("modelo", "")),
            timeout_s=float(ia.get("timeout_s", 240)),
            max_tentativas_rede=int(ia.get("max_tentativas_rede", 2)),
            temperatura=float(ia.get("temperatura", 0.2)),
            esforco=dict(ia.get("esforco", {})),
            modelo_imagem=str(ia.get("modelo_imagem") or MODELO_IMAGEM_PADRAO),
            provedor=chat.nome,
            provedor_imagem=imagem.nome,
            endpoint_imagem=imagem.endpoint,
            api_key_imagem=imagem.api_key,
        ),
        max_chamadas_por_pedido=int(agente.get("max_chamadas_por_pedido", 25)),
        max_reparos_mesmo_erro=int(agente.get("max_reparos_mesmo_erro", 3)),
        caminho_orca=Path(app["caminho_orca"]) if app.get("caminho_orca") else ORCA_PADRAO,
        pasta_projetos=Path(app["pasta_projetos"]) if app.get("pasta_projetos") else pasta_projetos_padrao(),
        provedores=tuple(provedores),
        mcp_ligado=dados.get("mcp", {}).get("ligado", True) is not False,
        transcricao=str(dados.get("transcricao", {}).get("endpoint", "")).rstrip("/"),
        atualizacao=str(dados.get("atualizacao", {}).get("endereco", "")).rstrip("/"),
    )


def salvar_preferencias(
    endpoint: str,
    api_key: str,
    modelo: str,
    caminho_orca: str,
    pasta_projetos: str,
    modelo_imagem: str = MODELO_IMAGEM_PADRAO,
    provedores: Sequence[Provedor] = (),
    provedor: str = PROVEDOR_PADRAO,
    provedor_imagem: str = PROVEDOR_PADRAO,
    mcp_ligado: bool = True,
    transcricao: str = "",
    atualizacao: str = "",
) -> Path:
    """Grava as preferências da tela de configurações. `json.dumps` gera strings válidas em TOML.

    `endpoint` e `api_key` são os do provedor Padrão. `provedores` traz os demais.
    """
    destino = pasta_usuario() / "config.toml"
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = [
        "[ia]",
        f"endpoint = {json.dumps(endpoint)}",
        f"api_key = {json.dumps(api_key)}",
        f"modelo = {json.dumps(modelo)}",
        f"modelo_imagem = {json.dumps(modelo_imagem)}",
        f"provedor = {json.dumps(provedor)}",
        f"provedor_imagem = {json.dumps(provedor_imagem)}",
        "",
        "[app]",
        f"caminho_orca = {json.dumps(caminho_orca)}",
        f"pasta_projetos = {json.dumps(pasta_projetos)}",
        "",
        "[mcp]",
        f"ligado = {'true' if mcp_ligado else 'false'}",
        "",
        "[transcricao]",
        f"endpoint = {json.dumps(transcricao)}",
        "",
        "[atualizacao]",
        f"endereco = {json.dumps(atualizacao)}",
        "",
    ]
    for item in provedores:
        linhas += [
            "[[provedores]]",
            f"nome = {json.dumps(item.nome)}",
            f"endpoint = {json.dumps(item.endpoint)}",
            f"api_key = {json.dumps(item.api_key)}",
            "",
        ]
    temporario = destino.with_suffix(".tmp")
    temporario.write_text("\n".join(linhas), encoding="utf-8")
    temporario.replace(destino)
    return destino
