"""O MCP pela entrada e saída padrão: JSON-RPC 2.0, uma mensagem por linha.

Só o que o Forja3D usa: initialize, ping, tools/list e tools/call. Feito à mão porque o SDK oficial
traria um servidor web inteiro para dentro do pacote, e aqui só se fala por stdio.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from typing import Any, BinaryIO

log = logging.getLogger(__name__)

# Da mais nova para a mais antiga. O cliente pede uma, e o servidor responde com ela se souber falar.
VERSOES = ("2025-06-18", "2025-03-26", "2024-11-05")
ERRO_DE_LEITURA, PEDIDO_INVALIDO, METODO_DESCONHECIDO, PARAMETROS_INVALIDOS, ERRO_INTERNO = -32700, -32600, -32601, -32602, -32603


class FerramentaDesconhecida(Exception):
    pass


class Servidor:
    """`chamar` recebe o nome e os argumentos e devolve o conteúdo do resultado e se ele é um erro."""

    def __init__(
        self,
        nome: str,
        versao: str,
        instrucoes: str,
        ferramentas: list[dict[str, Any]],
        chamar: Callable[[str, dict[str, Any]], tuple[list[dict[str, Any]], bool]],
    ) -> None:
        self.info = {"name": nome, "version": versao}
        self.instrucoes = instrucoes
        self.ferramentas = ferramentas
        self.chamar = chamar

    def responder(self, mensagem: Any) -> Any:
        """A resposta a uma mensagem já lida, ou None para uma notificação. Um lote vira uma lista de respostas."""
        if isinstance(mensagem, list):
            respostas = [r for r in (self.responder(m) for m in mensagem) if r is not None]
            return respostas or None
        if not isinstance(mensagem, dict) or mensagem.get("jsonrpc") != "2.0" or not isinstance(mensagem.get("method"), str):
            return _erro(mensagem.get("id") if isinstance(mensagem, dict) else None, PEDIDO_INVALIDO, "Pedido JSON-RPC inválido.")
        if "id" not in mensagem:
            return None
        identificador, metodo = mensagem["id"], mensagem["method"]
        parametros = mensagem.get("params") or {}
        try:
            if metodo == "initialize":
                pedida = parametros.get("protocolVersion")
                return _resultado(
                    identificador,
                    {
                        "protocolVersion": pedida if pedida in VERSOES else VERSOES[0],
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": self.info,
                        "instructions": self.instrucoes,
                    },
                )
            if metodo == "ping":
                return _resultado(identificador, {})
            if metodo == "tools/list":
                return _resultado(identificador, {"tools": self.ferramentas})
            if metodo == "tools/call":
                nome, argumentos = parametros.get("name"), parametros.get("arguments") or {}
                if not isinstance(nome, str) or not isinstance(argumentos, dict):
                    return _erro(identificador, PARAMETROS_INVALIDOS, "tools/call precisa de name e de arguments como objeto.")
                conteudo, erro = self.chamar(nome, argumentos)
                return _resultado(identificador, {"content": conteudo, "isError": erro})
            return _erro(identificador, METODO_DESCONHECIDO, f"Método desconhecido: {metodo}")
        except FerramentaDesconhecida as falha:
            return _erro(identificador, PARAMETROS_INVALIDOS, str(falha))
        except Exception:
            log.exception("Falha ao atender %s", metodo)
            return _erro(identificador, ERRO_INTERNO, "Erro interno no servidor do Forja3D. Os detalhes foram para o log.")

    def rodar(self, entrada: Iterable[bytes], saida: BinaryIO) -> None:
        """Atende uma mensagem por vez, até a entrada fechar: o cliente encerra o servidor fechando o stdin."""
        for linha in entrada:
            if not linha.strip():
                continue
            try:
                mensagem = json.loads(linha)
            except ValueError:
                resposta: Any = _erro(None, ERRO_DE_LEITURA, "A mensagem não é um JSON válido.")
            else:
                resposta = self.responder(mensagem)
            if resposta is not None:
                saida.write(json.dumps(resposta, ensure_ascii=False).encode("utf-8") + b"\n")
                saida.flush()


def _resultado(identificador: Any, resultado: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identificador, "result": resultado}


def _erro(identificador: Any, codigo: int, mensagem: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identificador, "error": {"code": codigo, "message": mensagem}}
