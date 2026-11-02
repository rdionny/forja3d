"""Fase 0: resolve a herança dos presets do OrcaSlicer em JSONs completos."""
import json
import sys
from pathlib import Path

PERFIS = Path("ferramentas/OrcaSlicer_2.4.2/resources/profiles/Creality")
DESTINO = Path("config/orca")

ITENS = {
    "maquina": ("machine", "Creality Ender-3 V3 SE 0.4 nozzle"),
    "processo_fina": ("process", "0.12mm Fine @Creality Ender3V3SE 0.4"),
    "processo_otima": ("process", "0.16mm Optimal @Creality Ender3V3SE 0.4"),
    "processo_padrao": ("process", "0.20mm Standard @Creality Ender3V3SE 0.4"),
    "processo_rascunho": ("process", "0.24mm Draft @Creality Ender3V3SE 0.4"),
    "filamento_PLA": ("filament", "Creality Generic PLA @Ender-3V3-all"),
    "filamento_PETG": ("filament", "Creality Generic PETG @Ender-3V3-all"),
    "filamento_TPU": ("filament", "Creality Generic TPU @Ender-3V3-all"),
}


def carregar_indice(pasta):
    """Os arquivos nem sempre têm o nome igual ao campo `name`, então indexa pelo campo."""
    indice = {}
    for arquivo in pasta.glob("*.json"):
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        indice[dados["name"]] = dados
    return indice


def resolver(indice, nome):
    dados = indice[nome]
    pai = dados.get("inherits")
    base = resolver(indice, pai) if pai else {}
    return {**base, **dados}


def achatar(tipo, nome):
    completo = resolver(carregar_indice(PERFIS / tipo), nome)
    completo.pop("inherits", None)
    completo.pop("instantiation", None)
    completo["from"] = "system"
    completo["name"] = nome
    return completo


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    DESTINO.mkdir(parents=True, exist_ok=True)
    for chave, (tipo, nome) in ITENS.items():
        dados = achatar(tipo, nome)
        (DESTINO / f"{chave}.json").write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{chave}: {len(dados)} chaves")
