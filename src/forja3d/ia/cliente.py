"""Cliente do endpoint de IA, com erros em português e chamada cancelável."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import OpenAI

from forja3d.configuracao import ConfigIA

log = logging.getLogger(__name__)


class ErroIA(Exception):
    """Falha ao falar com a IA, com uma mensagem pronta para o usuário."""


class Cancelado(Exception):
    """O usuário cancelou a operação."""


@dataclass(frozen=True)
class ChamadaFerramenta:
    id: str
    nome: str
    argumentos: str


@dataclass(frozen=True)
class RespostaIA:
    conteudo: str
    chamadas: list[ChamadaFerramenta]
    mensagem: dict[str, Any] = field(repr=False)
    tokens: dict[str, Any] = field(default_factory=dict)


def traduzir_erro(erro: Exception) -> ErroIA:
    if isinstance(erro, openai.AuthenticationError):
        return ErroIA("A IA recusou a chave da API. Confira a chave em Configurações.")
    if isinstance(erro, openai.RateLimitError):
        return ErroIA("O endpoint de IA atingiu o limite de uso. Aguarde um pouco e tente de novo.")
    if isinstance(erro, openai.APITimeoutError):
        return ErroIA("A IA demorou demais para responder. Tente de novo.")
    if isinstance(erro, openai.APIConnectionError):
        return ErroIA("Não consegui conectar ao endpoint de IA. Confira a rede e o endereço em Configurações.")
    if isinstance(erro, openai.NotFoundError):
        return ErroIA("O endpoint não reconheceu o endereço ou o modelo. Confira em Configurações.")
    if isinstance(erro, openai.APIStatusError):
        return ErroIA(f"O endpoint de IA respondeu com erro {erro.status_code}. Tente de novo em instantes.")
    return ErroIA(f"Erro inesperado ao falar com a IA: {type(erro).__name__}.")


class ClienteIA:
    def __init__(self, config: ConfigIA) -> None:
        self._config = config
        self._cliente = OpenAI(
            base_url=config.endpoint,
            api_key=config.api_key or "sem-chave",
            timeout=config.timeout_s,
            max_retries=config.max_tentativas_rede,
        )

    @property
    def modelo(self) -> str:
        return self._config.modelo

    def listar_modelos(self) -> list[str]:
        try:
            return sorted(m.id for m in self._cliente.models.list().data)
        except Exception as erro:
            raise traduzir_erro(erro) from erro

    def verificar(self) -> None:
        """Confirma que o endpoint responde e que o modelo configurado existe."""
        modelos = self.listar_modelos()
        if self._config.modelo not in modelos:
            raise ErroIA(f"O modelo {self._config.modelo} não está disponível no endpoint. Escolha outro em Configurações.")

    def conversar(
        self,
        mensagens: list[dict[str, Any]],
        ferramentas: list[dict[str, Any]] | None,
        etapa: str,
        cancelar: threading.Event | None = None,
    ) -> RespostaIA:
        argumentos: dict[str, Any] = {
            "model": self._config.modelo,
            "messages": mensagens,
            "temperature": self._config.temperatura,
        }
        esforco = self._config.esforco.get(etapa)
        if esforco:
            argumentos["reasoning_effort"] = esforco
        if ferramentas:
            argumentos["tools"] = ferramentas

        # O SDK síncrono não é interrompível: a chamada corre em uma thread descartável.
        caixa: dict[str, Any] = {}

        def chamar() -> None:
            try:
                caixa["resposta"] = self._cliente.chat.completions.create(**argumentos)
            except Exception as erro:  # noqa: BLE001
                caixa["erro"] = erro

        inicio = time.monotonic()
        linha = threading.Thread(target=chamar, daemon=True)
        linha.start()
        while linha.is_alive():
            linha.join(0.1)
            if cancelar is not None and cancelar.is_set():
                raise Cancelado
        if "erro" in caixa:
            raise traduzir_erro(caixa["erro"]) from caixa["erro"]

        resposta = caixa["resposta"]
        if not resposta.choices:
            raise ErroIA("A IA devolveu uma resposta vazia. Tente de novo.")
        mensagem = resposta.choices[0].message
        chamadas = [
            ChamadaFerramenta(c.id, c.function.name, c.function.arguments or "{}") for c in (mensagem.tool_calls or [])
        ]
        tokens = resposta.usage.model_dump(exclude_none=True) if resposta.usage else {}
        log.info(
            "etapa=%s duracao=%.1fs chamadas=%s modelo=%s tokens=%s",
            etapa,
            time.monotonic() - inicio,
            [c.nome for c in chamadas],
            resposta.model,
            tokens,
        )
        return RespostaIA(mensagem.content or "", chamadas, mensagem.model_dump(exclude_none=True), tokens)
