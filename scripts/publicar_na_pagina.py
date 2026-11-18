"""Põe uma versão do GitHub na página de downloads da rede de casa, com o manifesto que o app procura.

Uso: publicar_na_pagina.py v0.2.1 [--pasta <pasta da página>]

Baixa do release o instalador do Windows e o pacote do Linux (com o gh já logado), calcula o SHA-256,
refaz index.html e grava atualizacao.json. As versões antigas saem da pasta. O app instalado procura
<endereço>/atualizacao.json ao abrir: veja src/forja3d/atualizacao.py.
"""
import argparse
import json
import string
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gerar_instalador import RAIZ, sha256

sys.path.insert(0, str(RAIZ / "src"))
from forja3d.atualizacao import MANIFESTO  # noqa: E402

PASTA_PADRAO = Path.home() / "Downloads" / "Forja3D-downloads"
NOTAS = RAIZ / ".github" / "notas_da_versao.md"
PAGINA = string.Template("""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Baixar o Forja3D</title>
<style>
  :root { --fundo: #14161a; --painel: #1d2026; --texto: #e6e8ec; --suave: #9aa2b1; --acento: #f29a52; --borda: #2c3038; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--fundo); color: var(--texto); font: 16px/1.5 system-ui, "Segoe UI", Ubuntu, sans-serif; }
  main { max-width: 680px; margin: 0 auto; padding: 40px 16px; }
  h1 { margin: 0 0 4px; font-size: 28px; }
  h2 { margin: 0 0 12px; font-size: 20px; }
  .suave { color: var(--suave); }
  .cartao { background: var(--painel); border: 1px solid var(--borda); border-radius: 10px; padding: 20px; margin: 24px 0; }
  a.botao { display: inline-block; background: var(--acento); color: #14161a; font-weight: 700; text-decoration: none; padding: 14px 22px; border-radius: 8px; font-size: 18px; }
  a.botao:hover { filter: brightness(1.1); }
  a { color: var(--acento); }
  pre { background: var(--fundo); border: 1px solid var(--borda); border-radius: 8px; padding: 12px; overflow-x: auto; margin: 8px 0 0; }
  code { font-family: ui-monospace, "Cascadia Code", "DejaVu Sans Mono", monospace; font-size: 14px; }
  ol { padding-left: 20px; margin-bottom: 0; }
  li { margin-bottom: 12px; }
  .pequeno { font-size: 13px; word-break: break-all; }
</style>
</head>
<body>
<main>
  <h1>Baixar o Forja3D $versao</h1>
  <p class="suave">Escolha o sistema do computador. Nenhum dos dois leva chave de API: no primeiro uso, o app abre em Configurações.
  Depois de instalado, o app avisa sozinho quando houver versão nova nesta página e atualiza pelo próprio aviso.</p>

  <section class="cartao">
    <h2>Windows 10 ou 11</h2>
    <a class="botao" href="$exe" download>Baixar para Windows ($mb_exe MB)</a>
    <p class="suave pequeno">SHA-256: $sha_exe</p>
    <ol>
      <li>Rode o instalador. Ele não pede administrador e já leva o OrcaSlicer.</li>
      <li>O instalador não tem assinatura digital, então o Windows pode dizer que protegeu o computador. Clique em <b>Mais informações</b> e em <b>Executar assim mesmo</b>.</li>
      <li>Em <b>Configurações</b>, informe o endpoint e a chave do seu provedor de IA e, se tiver, o servidor de <b>Transcrição de áudio</b>.</li>
    </ol>
  </section>

  <section class="cartao">
    <h2>Linux (Ubuntu 22.04 ou mais novo, x64)</h2>
    <a class="botao" href="$tar" download>Baixar para Linux ($mb_tar MB)</a>
    <p class="suave pequeno">SHA-256: $sha_tar</p>
    <ol>
      <li>Instale o que a janela, o microfone e a voz usam:
        <pre><code>sudo apt install libegl1 libxcb-cursor0 alsa-utils espeak-ng</code></pre></li>
      <li>Na pasta em que o arquivo foi salvo, extraia e abra:
        <pre><code>tar -xzf $tar
./Forja3D/Forja3D</code></pre></li>
      <li>Em <b>Configurações</b>, informe o seu provedor de IA, como no Windows.</li>
      <li>Para fatiar, baixe o AppImage do <a href="https://github.com/SoftFever/OrcaSlicer/releases/tag/v2.4.2">OrcaSlicer 2.4.2</a>, dê permissão com <code>chmod +x</code> e indique o arquivo em <b>Configurações &gt; OrcaSlicer</b>.</li>
    </ol>
    <p class="suave">Se o app não abrir, a mensagem aparece no terminal, e os detalhes ficam em <code>~/Forja3D/logs/falha_ao_abrir.txt</code>.</p>
  </section>
</main>
</body>
</html>
""")


def novidades(texto: str) -> str:
    """Só a parte de novidades das notas do release: a de downloads não serve no aviso do app."""
    return texto.split("\n## Downloads")[0].replace("## Novidades", "", 1).strip()


def manifesto(versao: str, notas: str, exe: Path, tar: Path) -> dict:
    return {
        "versao": versao,
        "notas": notas,
        "windows": {"arquivo": exe.name, "sha256": sha256(exe)},
        "linux": {"arquivo": tar.name, "sha256": sha256(tar)},
    }


def pagina(dados: dict, exe: Path, tar: Path) -> str:
    return PAGINA.substitute(
        versao=dados["versao"],
        exe=exe.name, sha_exe=dados["windows"]["sha256"], mb_exe=round(exe.stat().st_size / 1e6),
        tar=tar.name, sha_tar=dados["linux"]["sha256"], mb_tar=round(tar.stat().st_size / 1e6),
    )


def publicar(tag: str, pasta: Path) -> None:
    versao = tag.removeprefix("v")
    exe = pasta / f"Forja3D-{versao}-instalador.exe"
    tar = pasta / f"Forja3D-{versao}-linux-x64.tar.gz"
    pasta.mkdir(parents=True, exist_ok=True)
    print(f"Baixando {exe.name} e {tar.name} do release {tag}", flush=True)
    comando = ["gh", "release", "download", tag, "--pattern", exe.name, "--pattern", tar.name, "--dir", str(pasta), "--clobber"]
    subprocess.run(comando, check=True, cwd=RAIZ)
    dados = manifesto(versao, novidades(NOTAS.read_text(encoding="utf-8")), exe, tar)
    (pasta / "index.html").write_text(pagina(dados, exe, tar), encoding="utf-8")
    # O manifesto por último e de uma vez: um app que procure no meio da cópia acha a versão anterior inteira.
    temporario = pasta / (MANIFESTO + ".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    temporario.replace(pasta / MANIFESTO)
    for antigo in pasta.glob("Forja3D-*"):
        if antigo not in (exe, tar):
            antigo.unlink()
    print(f"Página pronta em {pasta}: versão {versao}.")


if __name__ == "__main__":
    analisador = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    analisador.add_argument("tag", help="a tag do release, como v0.2.1")
    analisador.add_argument("--pasta", type=Path, default=PASTA_PADRAO, help=f"a pasta servida na rede (padrão: {PASTA_PADRAO})")
    argumentos = analisador.parse_args()
    publicar(argumentos.tag, argumentos.pasta)
