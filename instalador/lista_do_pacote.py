"""O que vai no pacote além do código, usado pelo spec e pela conferência.

É uma lista branca de propósito: a pasta config inteira levaria config/ia_padrao.toml, que tem a chave da API.
"""
from __future__ import annotations

from pathlib import Path

# Nada disto pode aparecer no pacote, em nenhuma pasta. Os dois documentos citam a chave em texto aberto.
NOMES_PROIBIDOS = frozenset(
    {
        "ia_padrao.toml",
        "ia_padrao.example.toml",
        "config.toml",
        "planejamento.md",
        "prompt_desenvolvimento.md",
        "instancia.lock",
        "forja3d.log",
        "projeto.json",
        "conversa.jsonl",
    }
)
# Sem .gcode aqui: o OrcaSlicer traz os dele em resources/printers. Um projeto já cai em projeto.json.
SUFIXOS_PROIBIDOS = (".forja3d", ".log")


def dados_do_app(raiz: Path) -> list[tuple[str, str]]:
    """Pares (arquivo de origem, pasta de destino dentro do pacote)."""
    fonte = raiz / "src" / "forja3d"
    config = raiz / "config"
    pares = [
        (config / "ia_padrao.pacote.toml", "config"),
        (config / "impressora_ender3_v3_se.json", "config"),
        (fonte / "agente" / "ferramentas.json", "forja3d/agente"),
    ]
    pares += [(a, "config/orca") for a in sorted((config / "orca").glob("*.json"))]
    pares += [(a, "forja3d/ia/prompts") for a in sorted((fonte / "ia" / "prompts").glob("*.md"))]
    pares += [(a, "forja3d/interface/icones") for a in sorted((fonte / "interface" / "icones").glob("*.svg"))]
    return [(str(origem), destino) for origem, destino in pares]
