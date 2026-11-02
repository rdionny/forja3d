"""Configuração do Forja3D: padrões do projeto, preferências do usuário e ambiente."""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
PASTA_CONFIG = RAIZ / "config"
ORCA_PADRAO = RAIZ / "ferramentas" / "OrcaSlicer_2.4.2" / "orca-slicer.exe"


def pasta_usuario() -> Path:
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home()) / "Forja3D"


@dataclass(frozen=True)
class ConfigIA:
    endpoint: str
    api_key: str = field(repr=False)
    modelo: str
    timeout_s: float = 240.0
    max_tentativas_rede: int = 2
    temperatura: float = 0.2
    esforco: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Configuracao:
    ia: ConfigIA
    max_chamadas_por_pedido: int = 25
    max_reparos_mesmo_erro: int = 3
    caminho_orca: Path = ORCA_PADRAO
    pasta_projetos: Path = RAIZ / "projetos"


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


def carregar() -> Configuracao:
    """Prioridade: variáveis de ambiente, preferências do usuário e padrões do projeto."""
    dados = _ler_toml(PASTA_CONFIG / "ia_padrao.example.toml")
    dados = _mesclar(dados, _ler_toml(PASTA_CONFIG / "ia_padrao.toml"))
    dados = _mesclar(dados, _ler_toml(pasta_usuario() / "config.toml"))

    ia = dict(dados.get("ia", {}))
    for variavel, chave in (
        ("FORJA3D_IA_ENDPOINT", "endpoint"),
        ("FORJA3D_IA_API_KEY", "api_key"),
        ("FORJA3D_IA_MODELO", "modelo"),
    ):
        if os.environ.get(variavel):
            ia[chave] = os.environ[variavel]

    agente = dados.get("agente", {})
    app = dados.get("app", {})
    return Configuracao(
        ia=ConfigIA(
            endpoint=str(ia.get("endpoint", "")).rstrip("/"),
            api_key=str(ia.get("api_key", "")),
            modelo=str(ia.get("modelo", "")),
            timeout_s=float(ia.get("timeout_s", 240)),
            max_tentativas_rede=int(ia.get("max_tentativas_rede", 2)),
            temperatura=float(ia.get("temperatura", 0.2)),
            esforco=dict(ia.get("esforco", {})),
        ),
        max_chamadas_por_pedido=int(agente.get("max_chamadas_por_pedido", 25)),
        max_reparos_mesmo_erro=int(agente.get("max_reparos_mesmo_erro", 3)),
        caminho_orca=Path(app["caminho_orca"]) if app.get("caminho_orca") else ORCA_PADRAO,
        pasta_projetos=Path(app["pasta_projetos"]) if app.get("pasta_projetos") else RAIZ / "projetos",
    )


def salvar_preferencias(endpoint: str, api_key: str, modelo: str, caminho_orca: str, pasta_projetos: str) -> Path:
    """Grava as preferências da tela de configurações. `json.dumps` gera strings válidas em TOML."""
    destino = pasta_usuario() / "config.toml"
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = [
        "[ia]",
        f"endpoint = {json.dumps(endpoint)}",
        f"api_key = {json.dumps(api_key)}",
        f"modelo = {json.dumps(modelo)}",
        "",
        "[app]",
        f"caminho_orca = {json.dumps(caminho_orca)}",
        f"pasta_projetos = {json.dumps(pasta_projetos)}",
        "",
    ]
    temporario = destino.with_suffix(".tmp")
    temporario.write_text("\n".join(linhas), encoding="utf-8")
    temporario.replace(destino)
    return destino
