# Forja3D

Descreva uma peça em português, e um agente de IA projeta, verifica e prepara o G-code para a **Creality Ender-3 V3 SE com bico de 0,4 mm**.

## Como abrir

Dê dois cliques em `Abrir Forja3D.bat`.

Em uma máquina nova:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
```

Copie `config/ia_padrao.example.toml` para `config/ia_padrao.toml` e preencha a chave. O OrcaSlicer portátil fica em `ferramentas/OrcaSlicer_2.4.2/`, ou indique outro caminho em **Configurações**.

## Como usar

1. Descreva a peça na conversa, com as medidas que tiver.
2. Acompanhe a peça no visualizador e a verificação abaixo da conversa. Peça ajustes pelo chat.
3. Escolha material, qualidade, paredes e preenchimento, e clique em **Aprovar e preparar**.
4. Clique em **Exportar G-code** e salve no **cartão SD**. A impressora não lê pen drive.

No visualizador, arraste para girar, use a roda para aproximar e Shift + arrastar para mover.

## O que protege a peça e a impressora

| Etapa | O que confere |
|---|---|
| Especificação | Tamanho e material, antes de modelar |
| Verificação geométrica | Medidas, furos, sólido válido, paredes finas, balanços e pontes |
| Crítica visual | O agente olha as vistas da peça |
| Validação do G-code | Movimentos na mesa, temperaturas, comandos permitidos |

A IA nunca escreve G-code e não tem como exportar arquivos: isso depende do seu clique.

## Limites conhecidos

- **Segurança do código da IA:** o script gerado passa por uma análise, roda em processo separado, com tempo e memória limitados e sem a chave da API. Isso reduz bastante o risco, **mas não é uma sandbox completa**.
- **Chave da API:** o endpoint padrão usa HTTP sem criptografia, então a chave e os pedidos trafegam em texto aberto. Ela fica em `config/ia_padrao.toml`, fora do Git. Para trocar, use **Configurações**.
- **Impressão:** enquanto a calibração não for feita, folgas e furos usam valores iniciais comuns em FDM. Encaixes podem precisar de ajuste.
- **Sem sensor de fim de filamento:** confira se o carretel tem o bastante antes de imprimir.

## Desenvolvimento

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pytest -m ao_vivo
.venv\Scripts\python.exe scripts\sincronizar_prompt.py
.venv\Scripts\python.exe scripts\testar_endpoint.py
```

O primeiro comando roda os testes sem usar a IA. O segundo usa o endpoint real. O terceiro é necessário depois de editar `PROMPT_AGENTE_3D.md`. O quarto confere se o endpoint responde a texto, ferramentas, leitura de imagem, streaming e geração de imagem.

Para atender a um pedido pelo terminal, sem interface:

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m forja3d agente "Cubo de 20 mm em PLA"
```

Veja `PLANEJAMENTO.md`, `PROMPT_DESENVOLVIMENTO.md`, `docs/decisoes-fase0.md` e `docs/imagem-de-referencia.md`.
