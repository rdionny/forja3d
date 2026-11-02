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

### Na mesa

- **Mover:** clique em um item e arraste. A vista **Topo** é a melhor para posicionar. O que está na mesa é o que vai imprimir.
- **Câmera:** arraste o espaço vazio para girar, use a roda para aproximar e Shift + arrastar para mover a vista.
- **Copiar e cia.:** com o item selecionado, use os botões ou Ctrl+C, Ctrl+V, Ctrl+D (duplicar), R (girar 90°) e Delete. O botão direito abre o mesmo menu. Deletar tira só da mesa: o modelo continua igual, e o botão **Mesa** adiciona a parte de novo.
- **Desfazer e refazer:** use os ícones no topo, Ctrl+Z e Ctrl+Y. Valem para as alterações na mesa da versão aberta, até trocar de versão ou de peça.
- **Arquivos diferentes:** em **Mesa > Mover para o arquivo**, separe os itens para imprimir em outro momento. Cada arquivo vira um G-code. O que não couber em um arquivo vai sozinho para outro.
- **Pedir um ajuste em uma parte:** selecione o item e clique em **Enviar à IA**. A próxima mensagem do chat vai falar só daquela parte, e o agente recebe as vistas dela.
- **Tempo:** depois de preparar, o app mostra quanto a impressão demora e a que horas fica pronta se começar agora. É a estimativa do OrcaSlicer, que não conta aquecimento nem nivelamento.

Mudar a mesa invalida o G-code preparado: é preciso clicar em **Aprovar e preparar** de novo.

### Na conversa

Cada mensagem tem uma barra com quatro ações:

- **Copiar:** leva o texto para a área de transferência.
- **Editar:** abre a mensagem para você reescrever. Em uma resposta, abre a pergunta que a gerou.
- **Enviar de novo:** refaz a resposta. Em uma resposta, reenvia a pergunta que a gerou.
- **Ler:** lê em voz alta com a voz do Windows, sem mandar o texto para fora do computador. Clique de novo para parar.

Editar e enviar de novo voltam a conversa até aquela mensagem: o que veio depois sai da conversa, e a peça volta para a versão daquele momento. As versões mais novas continuam na lista, e uma cópia da conversa antiga fica em `conversas_anteriores/`, na pasta do projeto. Antes de tirar outros pedidos da conversa, o app pergunta.

### Na lista de peças

Clique com o botão direito em uma peça para **renomear**, **fixar no topo** ou **apagar**. Apagar manda a pasta do projeto para a Lixeira do Windows, de onde ela ainda pode voltar.

### Salvar e compartilhar

O app guarda tudo sozinho na pasta `projetos/`. Para levar um projeto para outro lugar ou passar para outra pessoa, use o botão **Arquivo**, ao lado do nome do programa:

- **Salvar** (Ctrl+S): grava no arquivo escolhido antes. Na primeira vez, pergunta onde.
- **Salvar como** (Ctrl+Shift+S): escolhe outro local ou nome. Também está no botão direito da lista de peças.
- **Abrir arquivo** (Ctrl+O): cria na sua lista uma cópia do projeto que está no arquivo `.forja3d`. Também há o botão **Abrir**, acima da lista.

O arquivo leva a conversa, as versões, a mesa e as imagens de referência. Veja se não há nada na conversa que você prefira não passar adiante.

Ele não leva o G-code, a verificação, a chave da API nem as conversas descartadas. Quem abre tem a peça verificada de novo no próprio computador e prepara o G-code lá. Só a versão atual é verificada ao abrir: as outras são verificadas quando a pessoa voltar para elas.

### A partir de uma imagem

Também dá para começar por uma imagem: clique em **Imagem de referência**, ou arraste e cole a imagem no chat. O app mostra pelo menos 2 opções de conceito, cada uma com a peça montada e a vista explodida. Depois da escolha, o agente pergunta o que faltar e modela a peça. As imagens são só ilustração e não têm medidas reais.

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
- **Arquivo de outra pessoa:** tudo é conferido ao abrir, nada do arquivo é executado, e o G-code é sempre gerado e validado no seu computador. Mesmo assim, o script e a conversa do arquivo vão para o agente como contexto, e um texto mal-intencionado ainda pode tentar influenciar o modelo. Abra só arquivos de quem você conhece.
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
