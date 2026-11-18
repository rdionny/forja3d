"""Procura versões novas na página de downloads e instala sem sair do app.

A página publica `atualizacao.json` com a versão, as novidades e o SHA-256 de cada pacote
(veja scripts/publicar_na_pagina.py). O pacote baixado só é instalado se o SHA-256 conferir.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MANIFESTO = "atualizacao.json"
TEMPO_DA_CONSULTA_S = 5
TEMPO_DA_LEITURA_S = 60
BLOCO = 1024 * 1024
LIMITE_DO_MANIFESTO = 64 * 1024


class ErroDeAtualizacao(Exception):
    """A mensagem é para o usuário."""


@dataclass(frozen=True)
class Novidade:
    versao: str
    notas: str
    arquivo: str
    sha256: str


def numeros(versao: str) -> tuple[int, ...]:
    return tuple(int(parte) for parte in versao.split("."))


def sistema() -> str:
    return "windows" if os.name == "nt" else "linux"


def pasta_de_download() -> Path:
    return Path(tempfile.gettempdir()) / "forja3d_atualizacao"


def _nome_seguro(nome: str) -> bool:
    # O nome vem da página: só um nome de arquivo, nunca um caminho.
    return bool(nome) and nome not in (".", "..") and Path(nome).name == nome and "\\" not in nome and "/" not in nome


def procurar(endereco: str, versao_atual: str) -> Novidade | None:
    """None quando não há versão nova ou a página não responde: fora de casa, o app abre normalmente."""
    try:
        with urllib.request.urlopen(f"{endereco.rstrip('/')}/{MANIFESTO}", timeout=TEMPO_DA_CONSULTA_S) as resposta:  # noqa: S310 - endereço das configurações.
            dados = json.loads(resposta.read(LIMITE_DO_MANIFESTO))
        pacote = dados[sistema()]
        novidade = Novidade(str(dados["versao"]), str(dados.get("notas", "")), str(pacote["arquivo"]), str(pacote["sha256"]).lower())
        if numeros(novidade.versao) <= numeros(versao_atual) or not _nome_seguro(novidade.arquivo):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return novidade


def baixar(
    endereco: str,
    novidade: Novidade,
    pasta: Path,
    progresso: Callable[[int, int], None] | None = None,
    cancelar: threading.Event | None = None,
) -> Path:
    """Baixa o pacote e confere o SHA-256. Se não conferir, apaga o arquivo e nada é instalado."""
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / novidade.arquivo
    resumo = hashlib.sha256()
    endereco_do_pacote = f"{endereco.rstrip('/')}/{urllib.parse.quote(novidade.arquivo)}"
    try:
        with urllib.request.urlopen(endereco_do_pacote, timeout=TEMPO_DA_LEITURA_S) as resposta, open(destino, "wb") as saida:  # noqa: S310
            total = int(resposta.headers.get("Content-Length") or 0)
            feito = 0
            while bloco := resposta.read(BLOCO):
                if cancelar is not None and cancelar.is_set():
                    raise ErroDeAtualizacao("Atualização cancelada. Nada foi instalado.")
                saida.write(bloco)
                resumo.update(bloco)
                feito += len(bloco)
                if progresso is not None:
                    progresso(feito, total)
    except OSError as erro:
        destino.unlink(missing_ok=True)
        raise ErroDeAtualizacao("Não consegui baixar a atualização. Confira se este computador está na mesma rede da página de downloads.") from erro
    except ErroDeAtualizacao:
        destino.unlink(missing_ok=True)
        raise
    if resumo.hexdigest() != novidade.sha256:
        destino.unlink(missing_ok=True)
        raise ErroDeAtualizacao("O arquivo baixado não confere com o SHA-256 publicado na página. Nada foi instalado.")
    return destino


def comando_do_instalador(instalador: Path) -> list[str]:
    # Com /abrir=1, o instalador abre o app de novo no fim: a instalação silenciosa sozinha terminaria calada.
    return [str(instalador), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/abrir=1"]


def trocar_pasta(pacote: Path, pasta: Path) -> None:
    """Extrai a versão nova ao lado da pasta do app e troca uma pela outra. O Linux deixa trocar os arquivos de um programa aberto."""
    nova = pasta.with_name(pasta.name + ".nova")
    antiga = pasta.with_name(pasta.name + ".antiga")
    shutil.rmtree(nova, ignore_errors=True)
    shutil.rmtree(antiga, ignore_errors=True)
    nova.mkdir()
    try:
        with tarfile.open(pacote) as arquivo:
            arquivo.extractall(nova, filter="data")
    except (OSError, tarfile.TarError) as erro:
        shutil.rmtree(nova, ignore_errors=True)
        raise ErroDeAtualizacao("O pacote baixado não abriu. A versão atual continua.") from erro
    extraida = nova / "Forja3D"
    if not (extraida / "Forja3D").is_file():
        shutil.rmtree(nova, ignore_errors=True)
        raise ErroDeAtualizacao("O pacote baixado não tem o Forja3D. A versão atual continua.")
    pasta.rename(antiga)
    extraida.rename(pasta)
    shutil.rmtree(nova, ignore_errors=True)
    shutil.rmtree(antiga, ignore_errors=True)


def instalar(pacote: Path) -> None:
    """Começa a instalação. Quem chama fecha o app logo depois, para o instalador poder trocar os arquivos."""
    if os.name == "nt":
        subprocess.Popen(comando_do_instalador(pacote), close_fds=True)
        return
    pasta = Path(sys.executable).parent
    trocar_pasta(pacote, pasta)
    # shortcut: espera fixa para a janela antiga fechar; sem ela, a nova entregaria o pedido à antiga e sairia.
    subprocess.Popen(["sh", "-c", 'sleep 2; exec "$0"', str(pasta / "Forja3D")], start_new_session=True, close_fds=True)
