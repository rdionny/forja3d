"""Ponto de entrada: aplicativo, processos auxiliares e comandos de terminal."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import traceback
from pathlib import Path


def _garantir_saidas() -> None:
    """Sem console, o programa empacotado não tem stdout nem stderr, e há biblioteca que escreve neles sem conferir."""
    for nome in ("stdout", "stderr"):
        if getattr(sys, nome) is None:
            setattr(sys, nome, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - vale até o fim do processo.


def _abrir_o_app(arquivo: Path | None = None) -> int:
    """Sem console, uma falha antes de a janela existir sumiria sem nenhum aviso."""
    try:
        from forja3d.interface.aplicativo import executar

        return executar(arquivo)
    except Exception:  # noqa: BLE001 - qualquer falha precisa chegar a quem abriu o programa.
        from forja3d.configuracao import pasta_usuario

        destino = pasta_usuario() / "logs" / "falha_ao_abrir.txt"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt":
            icone_de_erro = 0x10
            ctypes.windll.user32.MessageBoxW(None, f"O Forja3D não conseguiu abrir.\n\nOs detalhes estão em:\n{destino}", "Forja3D", icone_de_erro)
        return 1


def _comando_verificar(script: str, especificacao: str) -> int:
    import tempfile

    from forja3d.agente.especificacao import Especificacao
    from forja3d.cad.executor import executar_script
    from forja3d.impressora import carregar_perfil
    from forja3d.verificacao.verificador import verificar_peca

    with tempfile.TemporaryDirectory(prefix="forja3d_verificar_") as temporaria:
        pasta = Path(temporaria)
        resultado = executar_script(Path(script).read_text(encoding="utf-8"), pasta)
        if not resultado.ok:
            print(json.dumps({"ok": False, "tipo": resultado.tipo_erro, "erro": resultado.erro, "linha": resultado.linha, "trecho": resultado.trecho}, ensure_ascii=False, indent=1))
            return 2
        espec = Especificacao.model_validate_json(Path(especificacao).read_text(encoding="utf-8"))
        saida = verificar_peca(resultado.partes, espec, carregar_perfil(), pasta)
    print(json.dumps(saida, ensure_ascii=False, indent=1))
    return 0 if saida["aprovado"] else 1


def _comando_agente(pedido: str, material: str | None) -> int:
    from forja3d import configuracao
    from forja3d.agente.orquestrador import Orquestrador, Ouvinte
    from forja3d.fatiamento.orca import ParametrosFatiamento
    from forja3d.ia.cliente import ClienteIA
    from forja3d.impressora import carregar_perfil
    from forja3d.projetos.armazenamento import Projeto
    from forja3d.registro import configurar_logs

    class Terminal(Ouvinte):
        def etapa(self, texto: str) -> None:
            print(f"[{texto}]", flush=True)

        def versao(self, versao: str) -> None:
            print(f"  nova versão: {versao}", flush=True)

        def verificacao(self, versao: str, resultado: dict) -> None:
            print(f"  verificação de {versao}: aprovado={resultado['aprovado']}", flush=True)
            for item in resultado["bloqueios"] + resultado["alertas"]:
                print(f"    {item['severidade']} {item['codigo']}: {item['mensagem']}", flush=True)

    config = configuracao.carregar()
    configurar_logs(*config.chaves)
    projeto = Projeto.criar(config.pasta_projetos)
    agente = Orquestrador(config, carregar_perfil(), ClienteIA(config.ia), projeto, Terminal())
    print(agente.processar(pedido))
    print(f"\nProjeto: {projeto.pasta}")
    if material:
        agente.aprovar()
        resumo = agente.fatiar(ParametrosFatiamento(material=material))
        print(json.dumps(resumo["validacao"], ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    _garantir_saidas()
    argumentos = sys.argv[1:] if argv is None else argv
    if argumentos[:1] == ["--mcp"]:
        from forja3d.mcp.servidor import principal

        return principal()
    if argumentos[:1] == ["--worker-cad"]:
        from forja3d.cad.worker import principal

        return principal(argumentos[1])
    if argumentos[:1] == ["--worker-render"]:
        from forja3d.render.vistas import principal

        return principal(*argumentos[1:])
    if argumentos[:1] == ["--worker-importar"]:
        from forja3d.importacao.preparo import principal

        return principal(*argumentos[1:])
    # Dois cliques em um arquivo: o Windows passa só o caminho dele.
    if len(argumentos) == 1 and argumentos[0].lower().endswith(".forja3d"):
        return _abrir_o_app(Path(argumentos[0]).resolve())

    analisador = argparse.ArgumentParser(prog="forja3d")
    comandos = analisador.add_subparsers(dest="comando")
    verificar = comandos.add_parser("verificar", help="executa um script e compara com a especificação")
    verificar.add_argument("script")
    verificar.add_argument("especificacao")
    agente = comandos.add_parser("agente", help="atende a um pedido pelo terminal")
    agente.add_argument("pedido")
    agente.add_argument("--fatiar", metavar="MATERIAL", help="aprova e fatia no fim, com este material")
    dados = analisador.parse_args(argumentos)

    if dados.comando == "verificar":
        return _comando_verificar(dados.script, dados.especificacao)
    if dados.comando == "agente":
        return _comando_agente(dados.pedido, dados.fatiar)

    return _abrir_o_app()


if __name__ == "__main__":
    sys.exit(main())
