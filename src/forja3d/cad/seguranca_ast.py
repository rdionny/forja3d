"""Análise do código gerado pela IA antes de executar.

Reduz bastante o risco, mas não é uma sandbox completa: veja o README.
"""
from __future__ import annotations

import ast
import builtins

TAMANHO_MAXIMO = 50_000
IMPORTS_PERMITIDOS = {"cadquery", "math"}
NOMES_PROIBIDOS = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "getattr", "setattr", "delattr", "vars",
    "dir", "input", "breakpoint", "memoryview", "type", "object", "super", "classmethod", "staticmethod", "exit",
    "quit", "help",
}
ATRIBUTOS_PROIBIDOS = {"exporters", "importers", "save", "occ_impl", "OCP", "vis", "show", "show_object"}
ERROS_DE_NO = {
    ast.ClassDef: "definir classes",
    ast.Global: "usar global",
    ast.Nonlocal: "usar nonlocal",
    ast.AsyncFunctionDef: "usar async",
    ast.Await: "usar await",
    ast.AsyncFor: "usar async",
    ast.AsyncWith: "usar async",
}


class CodigoRecusado(Exception):
    """O script viola as regras de segurança."""

    def __init__(self, motivo: str, linha: int | None = None) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.linha = linha


def validar_codigo(codigo: str) -> None:
    if len(codigo.encode("utf-8")) > TAMANHO_MAXIMO:
        raise CodigoRecusado("O script passa de 50 KB.")
    try:
        arvore = ast.parse(codigo)
    except SyntaxError as erro:
        raise CodigoRecusado(f"Erro de sintaxe: {erro.msg}", erro.lineno) from erro

    for no in ast.walk(arvore):
        linha = getattr(no, "lineno", None)
        for tipo, descricao in ERROS_DE_NO.items():
            if isinstance(no, tipo):
                raise CodigoRecusado(f"Não é permitido {descricao}.", linha)
        if isinstance(no, ast.FunctionDef) and no.decorator_list:
            raise CodigoRecusado("Não é permitido usar decoradores.", linha)
        if isinstance(no, ast.Import):
            for apelido in no.names:
                if apelido.name not in IMPORTS_PERMITIDOS:
                    raise CodigoRecusado(f"Import não permitido: {apelido.name}. Use apenas cadquery e math.", linha)
        elif isinstance(no, ast.ImportFrom):
            if no.level or no.module not in IMPORTS_PERMITIDOS:
                raise CodigoRecusado(f"Import não permitido: {no.module}. Use apenas cadquery e math.", linha)
            for apelido in no.names:
                if apelido.name == "*" or apelido.name.startswith("_") or _atributo_proibido(apelido.name):
                    raise CodigoRecusado(f"Import não permitido: {apelido.name}.", linha)
        elif isinstance(no, ast.Name):
            if no.id in NOMES_PROIBIDOS or no.id.startswith("__"):
                raise CodigoRecusado(f"Nome não permitido: {no.id}.", linha)
        elif isinstance(no, ast.Attribute):
            if no.attr.startswith("_") or _atributo_proibido(no.attr):
                raise CodigoRecusado(f"Atributo não permitido: {no.attr}.", linha)


def _atributo_proibido(nome: str) -> bool:
    baixo = nome.lower()
    return nome in ATRIBUTOS_PROIBIDOS or baixo.startswith(("export", "import", "tosvg", "todxf"))


def _importar_restrito(nome, globais=None, locais=None, lista=(), nivel=0):
    if nome not in IMPORTS_PERMITIDOS or nivel:
        raise ImportError(f"Import não permitido: {nome}")
    return __import__(nome, globais, locais, lista, nivel)


BUILTINS_PERMITIDOS = {
    "__import__": _importar_restrito,
    **{
        nome: getattr(builtins, nome)
        for nome in (
            "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len", "list",
            "map", "max", "min", "pow", "print", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple",
            "zip", "Exception", "ValueError", "TypeError", "ZeroDivisionError", "IndexError", "KeyError",
        )
    },
}
