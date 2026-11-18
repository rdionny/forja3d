"""Padrões, chave da API e coerência entre o perfil e o prompt."""
import logging
import re
import subprocess
from pathlib import Path

import pytest

from forja3d import configuracao
from forja3d.agente.orquestrador import PROMPT_SISTEMA
from forja3d.configuracao import RAIZ
from forja3d.registro import FiltroChave

# A chave fica em config/ia_padrao.toml, que nunca vai para o git: no GitHub Actions, ele não existe.
TEM_CHAVE = (RAIZ / "config" / "ia_padrao.toml").is_file()


def test_padroes_do_projeto(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for variavel in ("FORJA3D_IA_ENDPOINT", "FORJA3D_IA_API_KEY", "FORJA3D_IA_MODELO"):
        monkeypatch.delenv(variavel, raising=False)
    config = configuracao.carregar()
    assert config.ia.endpoint == "http://129.151.36.250:8317/v1"
    assert config.ia.modelo == "gemini-3.8-flash-high"
    assert bool(config.ia.api_key) == TEM_CHAVE
    assert config.ia.esforco["modelagem"] == "high"
    assert config.ia.esforco["especificacao"] == "low"


def test_prioridade_ambiente_usuario_padrao(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("FORJA3D_IA_MODELO", raising=False)
    configuracao.salvar_preferencias("http://u/v1", "chave-u", "modelo-u", "", "")
    assert configuracao.carregar().ia.modelo == "modelo-u"
    assert configuracao.carregar().ia.api_key == "chave-u"
    monkeypatch.setenv("FORJA3D_IA_MODELO", "modelo-env")
    assert configuracao.carregar().ia.modelo == "modelo-env"


def test_chave_nao_aparece_no_repr():
    config = configuracao.ConfigIA(endpoint="e", api_key="segredo", modelo="m")
    assert "segredo" not in repr(config)


def test_filtro_de_log_remove_a_chave():
    registro = logging.LogRecord("x", logging.INFO, "", 0, "cabecalho Bearer %s", ("segredo",), None)
    FiltroChave("segredo").filter(registro)
    assert "segredo" not in registro.getMessage()
    assert "***" in registro.getMessage()


def _versionaveis() -> list[Path]:
    saida = subprocess.run(
        ["git", "ls-files", "--others", "--cached", "--exclude-standard"],
        cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return [RAIZ / linha for linha in saida.splitlines() if linha.strip()]


@pytest.mark.skipif(not TEM_CHAVE, reason="config/ia_padrao.toml só existe na máquina de quem desenvolve")
def test_chave_fora_dos_arquivos_versionaveis():
    chave = configuracao.carregar().ia.api_key
    assert chave
    com_chave = []
    for arquivo in _versionaveis():
        if arquivo.is_file() and arquivo.stat().st_size < 2_000_000:
            try:
                if chave in arquivo.read_text(encoding="utf-8"):
                    com_chave.append(arquivo.name)
            except UnicodeDecodeError:
                pass
    assert not com_chave


def test_prompt_do_app_identico_ao_da_raiz():
    copia = (RAIZ / "src" / "forja3d" / "ia" / "prompts" / "agente_3d.md").read_text(encoding="utf-8")
    assert copia == (RAIZ / "PROMPT_AGENTE_3D.md").read_text(encoding="utf-8"), "rode scripts/sincronizar_prompt.py"
    assert "{{CALIBRACAO_USUARIO}}" in PROMPT_SISTEMA


def test_prompt_coerente_com_o_perfil(perfil):
    v = perfil["volume_mm"]
    f, s = v["fisico"], v["seguro"]
    assert f"{f['x']} × {f['y']} × {f['z']} mm" in PROMPT_SISTEMA
    assert f"{s['x']} × {s['y']} × {s['z']} mm" in PROMPT_SISTEMA
    assert f"{perfil['bico']['temp_max_c']} °C" in PROMPT_SISTEMA
    assert f"{perfil['mesa']['temp_max_c']} °C" in PROMPT_SISTEMA
    assert f"{v['maximo_fatiador_xy']} mm" in PROMPT_SISTEMA
    for nome, m in perfil["materiais"].items():
        faixa = f"{m['bico_c']} °C ({m['bico_min_c']}–{m['bico_max_c']})"
        assert faixa in PROMPT_SISTEMA, nome
        assert f"{m['mesa_c']} °C" in PROMPT_SISTEMA, nome


def test_presets_coerentes_com_o_perfil(perfil):
    import json

    pasta = RAIZ / "config" / "orca"
    for nome, m in perfil["materiais"].items():
        filamento = json.loads((pasta / f"filamento_{nome}.json").read_text(encoding="utf-8"))
        assert "inherits" not in filamento, "presets com herança caem em valores padrão"
        assert filamento["nozzle_temperature"][0] == str(m["bico_c"])
        assert filamento["hot_plate_temp"][0] == str(m["mesa_c"])
    for nome, q in perfil["qualidades"].items():
        processo = json.loads((pasta / f"processo_{nome}.json").read_text(encoding="utf-8"))
        assert "inherits" not in processo
        assert float(processo["layer_height"]) == q["camada_mm"]
    maquina = json.loads((pasta / "maquina.json").read_text(encoding="utf-8"))
    assert "inherits" not in maquina
    assert re.match(r"220x220", maquina["printable_area"][2])
