"""Projetos em disco: só arquivos, com escrita atômica."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ESTADO_INICIAL: dict[str, Any] = {"titulo": "Nova peça", "versao_atual": None, "versao_aprovada": None}
_VERSAO = re.compile(r"^v\d{3,}$")


def gravar_json(caminho: Path, dados: Any) -> None:
    temporario = caminho.with_name(caminho.name + ".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    temporario.replace(caminho)


def gravar_bytes(caminho: Path, dados: bytes) -> None:
    temporario = caminho.with_name(caminho.name + ".tmp")
    temporario.write_bytes(dados)
    temporario.replace(caminho)


def ler_json(caminho: Path, padrao: Any = None) -> Any:
    if not caminho.exists():
        return padrao
    return json.loads(caminho.read_text(encoding="utf-8"))


class Projeto:
    def __init__(self, pasta: Path) -> None:
        self.pasta = pasta
        self.estado: dict[str, Any] = {**ESTADO_INICIAL, **ler_json(pasta / "projeto.json", {})}

    @classmethod
    def criar(cls, pasta_projetos: Path, titulo: str = "Nova peça") -> "Projeto":
        pasta_projetos.mkdir(parents=True, exist_ok=True)
        base = f"peca_{datetime.now():%Y%m%d_%H%M%S}"
        pasta = pasta_projetos / base
        contador = 1
        while pasta.exists():
            contador += 1
            pasta = pasta_projetos / f"{base}_{contador}"
        pasta.mkdir()
        projeto = cls(pasta)
        projeto.estado["titulo"] = titulo
        projeto.salvar_estado()
        return projeto

    @staticmethod
    def listar(pasta_projetos: Path) -> list["Projeto"]:
        if not pasta_projetos.exists():
            return []
        pastas = [p for p in pasta_projetos.iterdir() if (p / "projeto.json").exists()]
        pastas.sort(key=lambda p: (p / "projeto.json").stat().st_mtime, reverse=True)
        return [Projeto(p) for p in pastas]

    @property
    def titulo(self) -> str:
        return self.estado["titulo"]

    def salvar_estado(self) -> None:
        gravar_json(self.pasta / "projeto.json", self.estado)

    def pasta_temporaria(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="_execucao_", dir=self.pasta))

    def versoes(self) -> list[str]:
        return sorted(p.name for p in self.pasta.iterdir() if p.is_dir() and _VERSAO.match(p.name))

    def pasta_versao(self, versao: str) -> Path:
        return self.pasta / versao

    def registrar_versao(self, pasta_execucao: Path, especificacao: dict[str, Any] | None, partes: list[dict[str, Any]], resumo: str) -> str:
        """Transforma uma execução bem-sucedida na próxima versão."""
        existentes = self.versoes()
        numero = int(existentes[-1][1:]) + 1 if existentes else 1
        versao = f"v{numero:03d}"
        (pasta_execucao / "script.py").replace(pasta_execucao / "modelo.py")
        for sobra in ("resultado.json", "worker_erros.txt"):
            (pasta_execucao / sobra).unlink(missing_ok=True)
        gravar_json(pasta_execucao / "medidas.json", partes)
        gravar_json(pasta_execucao / "especificacao.json", especificacao)
        gravar_json(pasta_execucao / "info.json", {"resumo": resumo, "criado_em": datetime.now().isoformat(timespec="seconds")})
        pasta_execucao.replace(self.pasta / versao)
        return versao

    def medidas(self, versao: str) -> list[dict[str, Any]]:
        return ler_json(self.pasta_versao(versao) / "medidas.json", [])

    def stls(self, versao: str) -> list[Path]:
        return [self.pasta_versao(versao) / p["stl"] for p in self.medidas(versao)]

    def verificacao(self, versao: str) -> dict[str, Any] | None:
        return ler_json(self.pasta_versao(versao) / "verificacao.json")

    def salvar_verificacao(self, versao: str, resultado: dict[str, Any]) -> None:
        gravar_json(self.pasta_versao(versao) / "verificacao.json", resultado)

    def info_versao(self, versao: str) -> dict[str, Any]:
        return ler_json(self.pasta_versao(versao) / "info.json", {})

    def especificacao(self, versao: str) -> dict[str, Any] | None:
        return ler_json(self.pasta_versao(versao) / "especificacao.json")

    def pasta_impressao(self, versao: str, material: str, qualidade: str) -> Path:
        return self.pasta_versao(versao) / "impressao" / f"{material}_{qualidade}"

    def ultimo_fatiamento(self, versao: str) -> tuple[Path, dict[str, Any]] | None:
        pasta = self.pasta_versao(versao) / "impressao"
        if not pasta.exists():
            return None
        resumos = sorted(pasta.glob("*/resumo.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return (resumos[0].parent, ler_json(resumos[0])) if resumos else None

    def guardar_referencia(self, dados: bytes) -> Path:
        pasta = self.pasta / "referencias"
        pasta.mkdir(exist_ok=True)
        caminho = pasta / f"referencia_{len(list(pasta.glob('referencia_*.png'))) + 1:02d}.png"
        gravar_bytes(caminho, dados)
        return caminho

    def nova_rodada_de_conceitos(self) -> Path:
        pasta = self.pasta / "conceitos"
        pasta.mkdir(exist_ok=True)
        rodada = pasta / f"rodada_{len(list(pasta.glob('rodada_*'))) + 1:02d}"
        rodada.mkdir()
        return rodada

    def salvar_conceito_escolhido(self, conceito: dict[str, Any]) -> None:
        gravar_json(self.pasta / "conceitos" / "conceito_escolhido.json", conceito)

    def conceito_escolhido(self) -> dict[str, Any] | None:
        return ler_json(self.pasta / "conceitos" / "conceito_escolhido.json")

    def registrar_conversa(self, papel: str, texto: str, imagem: str | None = None) -> None:
        registro = {"papel": papel, "texto": texto, "em": datetime.now().isoformat(timespec="seconds")}
        if imagem:
            registro["imagem"] = imagem
        with open(self.pasta / "conversa.jsonl", "a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")

    def ler_conversa(self) -> list[dict[str, Any]]:
        arquivo = self.pasta / "conversa.jsonl"
        if not arquivo.exists():
            return []
        return [json.loads(linha) for linha in arquivo.read_text(encoding="utf-8").splitlines() if linha.strip()]

    def salvar_historico(self, mensagens: list[dict[str, Any]]) -> None:
        gravar_json(self.pasta / "historico.json", mensagens)

    def ler_historico(self) -> list[dict[str, Any]]:
        return ler_json(self.pasta / "historico.json", [])

    def limpar_execucoes(self) -> None:
        for pasta in self.pasta.glob("_execucao_*"):
            shutil.rmtree(pasta, ignore_errors=True)
