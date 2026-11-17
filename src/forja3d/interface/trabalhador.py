"""Roda o agente fora da thread da interface. A interface nunca pode travar."""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from forja3d.agente.orquestrador import Orquestrador
from forja3d.configuracao import Configuracao
from forja3d.fatiamento.orca import ErroFatiamento, ParametrosFatiamento
from forja3d.fatiamento.simulacao import ler_percurso
from forja3d.ia.cliente import Cancelado, ClienteIA, ErroIA
from forja3d.importacao.malha import ModeloInvalido
from forja3d.importacao.preparo import ModeloLido, OpcoesDoModelo, ler_em_processo
from forja3d.importacao.separacao import ErroDeSeparacao, Regioes, ler_regioes, separar
from forja3d.projetos.armazenamento import Projeto
from forja3d.projetos.compartilhar import ArquivoInvalido, importar_projeto, salvar_projeto

log = logging.getLogger(__name__)


class OuvinteQt(QObject):
    """Recebe os eventos do agente na thread de trabalho e os entrega como sinais."""

    sinal_etapa = Signal(str)
    sinal_especificacao = Signal(dict)
    sinal_versao = Signal(str)
    sinal_verificacao = Signal(str, dict)
    sinal_fatiamento = Signal(str, dict)
    sinal_cores = Signal(str, int)

    def etapa(self, texto: str) -> None:
        self.sinal_etapa.emit(texto)

    def especificacao(self, especificacao: dict[str, Any]) -> None:
        self.sinal_especificacao.emit(especificacao)

    def versao(self, versao: str) -> None:
        self.sinal_versao.emit(versao)

    def verificacao(self, versao: str, resultado: dict[str, Any]) -> None:
        self.sinal_verificacao.emit(versao, resultado)

    def fatiamento(self, versao: str, resumo: dict[str, Any]) -> None:
        self.sinal_fatiamento.emit(versao, resumo)

    def cores(self, versao: str, arquivo: int) -> None:
        self.sinal_cores.emit(versao, arquivo)


class Trabalhador(QObject):
    resposta = Signal(str)
    erro = Signal(str)
    concluido = Signal()
    fatiado = Signal(dict)
    arquivo_salvo = Signal(object)
    projeto_importado = Signal(object)
    arquivo_falhou = Signal(str)
    modelo_lido = Signal(object)
    modelo_adicionado = Signal(dict)
    simulacao_lida = Signal(object)
    regioes_lidas = Signal(str, object)
    cores_separadas = Signal(dict)

    def __init__(self, config: Configuracao, perfil: dict[str, Any], fabrica_cliente: Callable[..., Any] = ClienteIA) -> None:
        super().__init__()
        self.config = config
        self.perfil = perfil
        self.fabrica_cliente = fabrica_cliente
        self.ouvinte = OuvinteQt()
        self.orquestrador: Orquestrador | None = None

    def abrir(self, projeto: Projeto) -> Orquestrador:
        self.orquestrador = Orquestrador(
            self.config, self.perfil, self.fabrica_cliente(self.config.ia), projeto, self.ouvinte
        )
        return self.orquestrador

    @Slot(str)
    def processar(self, texto: str) -> None:
        self._atender(lambda: self.orquestrador.processar(texto))

    @Slot(object)
    def processar_conceito(self, conceito: dict[str, Any]) -> None:
        self._atender(lambda: self.orquestrador.processar_conceito(conceito))

    @Slot(str, str)
    def processar_parte(self, texto: str, parte: str) -> None:
        self._atender(lambda: self.orquestrador.processar_parte(texto, parte))

    @Slot()
    def verificar(self) -> None:
        try:
            self.orquestrador.verificar_versao_atual()
        except Exception:
            log.exception("Falha ao verificar a versão")
            self.erro.emit("Não consegui verificar esta versão. Ela não pode ser preparada até a verificação passar.")
        finally:
            self.concluido.emit()

    @Slot(object, object)
    def salvar_arquivo(self, projeto: Projeto, destino: Path) -> None:
        try:
            self.arquivo_salvo.emit(salvar_projeto(projeto, destino))
        except OSError:
            log.exception("Falha ao salvar o arquivo")
            self.arquivo_falhou.emit(f"Não consegui salvar em:\n{destino}\n\nConfira se a pasta existe e se o arquivo não está aberto em outro programa.")
        finally:
            self.concluido.emit()

    @Slot(object, object)
    def importar_arquivo(self, arquivo: Path, pasta_projetos: Path) -> None:
        projeto = None
        try:
            projeto = importar_projeto(arquivo, pasta_projetos, self.perfil)
        except ArquivoInvalido as falha:
            self.arquivo_falhou.emit(str(falha))
        except OSError:
            log.exception("Falha ao abrir o arquivo")
            self.arquivo_falhou.emit(f"Não consegui abrir:\n{arquivo}")
        finally:
            self.concluido.emit()
        # Depois do concluido: abrir a peça pode iniciar a verificação, e ela não pode ser dada como terminada.
        if projeto is not None:
            self.projeto_importado.emit(projeto)

    @Slot(object)
    def ler_modelo(self, arquivos: Path | list[Path]) -> None:
        modelo = None
        try:
            modelo = ler_em_processo(arquivos)
        except ModeloInvalido as falha:
            self.arquivo_falhou.emit(str(falha))
        except OSError:
            log.exception("Falha ao ler o modelo")
            nomes = "\n".join(str(a) for a in ([arquivos] if isinstance(arquivos, Path) else arquivos))
            self.arquivo_falhou.emit(f"Não consegui abrir:\n{nomes}")
        finally:
            self.concluido.emit()
        # Depois do concluido: a janela de escolhas abre com o app livre.
        if modelo is not None:
            self.modelo_lido.emit(modelo)

    @Slot(object, object)
    def adicionar_modelo(self, modelo: ModeloLido, opcoes: OpcoesDoModelo) -> None:
        try:
            self.modelo_adicionado.emit(self.orquestrador.adicionar_modelo(modelo.stl, opcoes))
        except Exception:
            log.exception("Falha ao colocar o modelo na mesa")
            self.arquivo_falhou.emit("Não consegui colocar o modelo na mesa. Os detalhes foram para o log.")
        finally:
            shutil.rmtree(modelo.pasta, ignore_errors=True)
            self.orquestrador.projeto.limpar_execucoes()
            self.concluido.emit()

    @Slot(object, object, object)
    def ler_simulacao(self, gcode: Path, cor_inicial: str | None, tempo_s: int | None) -> None:
        percurso = None
        try:
            percurso = ler_percurso(gcode, cor_inicial, tempo_s)
        except (OSError, ValueError):
            log.exception("Falha ao ler o G-code para simular")
            self.arquivo_falhou.emit("Não consegui ler o G-code para simular. Clique em Preparar de novo.")
        finally:
            self.concluido.emit()
        # Depois do concluido: a simulação fecha quando o app fica ocupado.
        if percurso is not None:
            self.simulacao_lida.emit(percurso)

    @Slot(str, object)
    def ler_regioes(self, parte: str, stl: Path) -> None:
        regioes = None
        try:
            regioes = ler_regioes(stl)
        except (ErroDeSeparacao, ModeloInvalido) as falha:
            self.arquivo_falhou.emit(str(falha))
        except Exception:
            log.exception("Falha ao procurar as regiões da peça")
            self.arquivo_falhou.emit("Não consegui ler as regiões desta peça. Os detalhes foram para o log.")
        finally:
            self.concluido.emit()
        # Depois do concluido: a tela de pintura abre com o app livre.
        if regioes is not None:
            self.regioes_lidas.emit(parte, regioes)

    @Slot(str, object, object, bool)
    def separar_cores(self, parte: str, regioes: Regioes, pintura: dict[int, str], pino: bool) -> None:
        try:
            self.cores_separadas.emit(self.orquestrador.separar_cores(parte, separar(regioes, pintura, pino)))
        except (ErroDeSeparacao, ModeloInvalido) as falha:
            self.arquivo_falhou.emit(str(falha))
        except Exception:
            log.exception("Falha ao separar as cores")
            self.arquivo_falhou.emit("Não consegui separar as cores desta peça. Os detalhes foram para o log, e a peça continua como estava.")
        finally:
            self.orquestrador.projeto.limpar_execucoes()
            self.concluido.emit()

    def _atender(self, pedido: Callable[[], str]) -> None:
        try:
            self.resposta.emit(pedido())
        except ErroIA as falha:
            self.erro.emit(str(falha))
        except Exception:
            log.exception("Falha inesperada no agente")
            self.erro.emit("Algo deu errado no agente. Os detalhes foram para o log. Suas versões salvas continuam intactas.")
        finally:
            self.concluido.emit()

    @Slot(object)
    def aprovar_e_fatiar(self, parametros: ParametrosFatiamento) -> None:
        try:
            self.orquestrador.aprovar()
            self.fatiado.emit(self.orquestrador.fatiar(parametros))
        except Cancelado:
            self.erro.emit("Fatiamento cancelado.")
        except (ErroFatiamento, ValueError) as falha:
            self.erro.emit(str(falha))
        except Exception:
            log.exception("Falha inesperada ao fatiar")
            self.erro.emit("Algo deu errado ao fatiar. Os detalhes foram para o log.")
        finally:
            self.concluido.emit()
