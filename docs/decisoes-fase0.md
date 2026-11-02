# Fase 0: decisões e resultados

Situação em 02/11/2026: **falta só a parte física, que o usuário decidiu deixar para o fim do projeto**. Todo o resto foi testado de verdade neste PC, mas nenhum G-code rodou na impressora ainda.

## Correções ao planejamento original

| O que eu tinha escrito | O que é verdade | Como descobri |
|---|---|---|
| Enviar por **pen drive** | **Cartão SD.** O firmware tem `SDSUPPORT` e `USB_FLASH_DRIVE_SUPPORT` desligado. A impressora tem slot de cartão e USB-C | Firmware oficial e lojas |
| Limite físico de 220 mm em X e Y | O fatiador **recusa a partir de 218 mm** (217 passa) | Teste real |
| Nomes de filamento `Generic PLA @Creality Ender-3V3-all` | Na v2.4.2 estável: `Creality Generic PLA @Ender-3V3-all`. O nome antigo vinha do branch `main` | Arquivos da versão baixada |
| G-code inicial com `G90` e `M82` | A v2.4.2 **não tem** essas linhas. O próprio fatiador acrescenta `G90`, `G21` e `M83` depois da purga | G-code real |
| Lista branca sem `G21` | **`G21` aparece em todo arquivo.** Sem ele, tudo seria reprovado | G-code real |
| "Input shaping" como dado duvidoso da página da Creality | Está ativo no firmware (`HIGH_SPEED 1`) | Firmware oficial |
| Exportar presets pela interface do Orca | Um script resolve a herança e funciona sem abrir a interface | Teste real |
| Extrair o G-code do `.gcode.3mf` | O G-code já sai solto, como `plate_1.gcode` em `--outputdir` | Teste real |

## Ambiente

- Python 3.11.9, em `.venv`. Ocupa **1,6 GB**, bem mais do que os 300–500 MB estimados antes da instalação.
- Versões fixadas em `requirements.lock`.
- O `pip` resolveu `cadquery 2.8.0` com **`cadquery-ocp 7.9.3.1.1` e `vtk 9.6.2`**, e não as versões mais recentes desses pacotes (8.0.1.1.0 e 9.7.1). Não force atualização: a combinação instalada é a que foi testada.
- PySide6-Essentials basta para a janela com `pyvistaqt`.
- OrcaSlicer 2.4.2 portátil em `ferramentas/OrcaSlicer_2.4.2/`, 407 MB, fora do Git. O SHA-256 do ZIP confere com o publicado pelo projeto (`feba3009…75d6be`).

## Endpoint de IA

Testado com o SDK `openai` 3.42.1, lendo `config/ia_padrao.toml`. Script: `scripts/fase0/testar_endpoint.py`.

| Item | Resultado |
|---|---|
| Modelo configurado existe | OK, entre 43 modelos |
| Chamada de ferramenta | OK, 2,5 s, com `content=None` |
| Retorno da ferramenta, usando `model_dump(exclude_none=True)` na mensagem do assistente | OK, 2,4 s |
| Imagem em `image_url` | OK, 3,6 s |
| Campo `model` da resposta | `gemini-3.8-flash-n`, diferente do pedido |

## CAD: suporte em L

Script: `scripts/fase0/medir_cad.py`. **A hipótese do planejamento foi confirmada.**

| Arquivo | Executa | Volume (mm³) | Caixa em Y | Furos | Filete |
|---|---|---|---|---|---|
| `suporte_l_modelo_errado.py` | **Sim, sem exceção** | 8.334,33 | -30 a 0 | **0** (esperados 2) | **Aresta externa errada** |
| `suporte_l_corrigido.py` | Sim | 8.357,41 | -15 a 15 | 2 de Ø4 | Junta interna |
| `suporte_l_agente_valido.py` (gerado com o prompt) | Sim | 8.319,59 | -20 a 20 | 2 de Ø4,5 | Junta interna |
| `saida/suporte_gerado.py` (gerado com o prompt) | Sim | 8.319,59 | -20 a 20 | 2 de Ø4,5 | Junta interna |
| `suporte_l_agente_parede_longa.py` (gerado com o prompt) | Sim | 9.538,91 | -15 a 15 | 2 de Ø4,5 | Junta interna |
| `suporte_l_api_invalida.py` (gerado com o prompt) | **`TypeError`** em `Workplane.fillet()` | n/a | n/a | n/a | n/a |

Conclusões:

- O volume do suporte corrigido (8.357,41) bate com o cálculo manual (8.357,4).
- Como o script errado é válido, fechado e tem o tamanho certo, só a comparação com a especificação detecta o defeito.
- Dos 5 scripts gerados com o prompt, 4 ficaram corretos de primeira e 1 precisaria do loop de reparo. A amostra é pequena e serve só de indício.
- **O modelo interpreta o mesmo pedido de formas diferentes:** a parede ficou ora no lado de 30 mm, ora no de 40 mm. As duas leituras atendem ao pedido, mas o volume muda cerca de 15%. Por isso o prompt agora manda registrar a escolha em `premissas` e citá-la na resposta.

### Como detectar furos

A primeira tentativa falhou com `StdFail_NotDone`, porque usava `face.normalAt(face.Center())`. O centro de massa de um cilindro completo fica no eixo, fora da superfície.

Regra validada:

1. Pegue a superfície com `BRepAdaptor_Surface`, sem usar `Face.Center()`.
2. A face é côncava quando `cilindro.Direct() == (orientação da face == REVERSED)`.
3. Agrupe as faces por eixo e raio, porque o OCCT pode dividir um furo em várias faces.
4. **Só é furo o grupo côncavo que soma 360°.** Um filete côncavo também é um cilindro com a normal voltada para o eixo: nos testes, ele aparece com 90°.

A regra 2 foi conferida em todos os casos contra a normal real, calculada em um ponto sobre a superfície.

## Visualização

Script: `scripts/fase0/testar_visualizador.py`. Capturas em `saida/fase0/visualizador/`.

- A janela PySide6 com `pyvistaqt` abre, mostra a peça sobre a mesa de 220 × 220 mm e o volume de 250 mm, e fecha sem erro.
- Renderização fora da tela funciona: PNG de 1024 × 1024.
- **Uma única vista isométrica esconde detalhes:** no suporte em L, a parede cobre 1 dos 2 furos. Por isso a crítica visual passa a incluir duas isométricas de cantos opostos, além das vistas ortogonais.

## OrcaSlicer: respostas da CLI

Scripts: `testar_orca.py`, `testar_orca_parametros.py`, `testar_orca_limites.py` e `achatar_presets.py`.

| Dúvida | Resposta |
|---|---|
| Nome do executável | Só `orca-slicer.exe`. Não há variante de console, e `--help` não imprime nada |
| Ordem em `--load-settings` | **Indiferente.** Zero linhas executáveis diferentes. Só mudam IDs em comentários |
| Presets com herança | **Precisam ser achatados.** Com `inherits`, o arquivo carrega, mas cai em valores padrão. No teste, falhou com -51 por acaso, em outra validação |
| STL ou 3MF | Os dois funcionam. O projeto usa STL |
| Onde sai o G-code | `plate_1.gcode` em `--outputdir`. Também fica em `Metadata/plate_1.gcode` no `.gcode.3mf` |
| Códigos de saída | 0 = OK, -3 = arquivo ou parâmetro inválido, -50 = não cabe, -51 = configuração inválida. **Vêm como 32 bits sem sinal**: converta (4294967246 é -50) |
| Mensagens | Em falha, stdout traz `Slic3r::CLI::run found error, exit`. O stderr às vezes detalha, mas na peça grande fica vazio. Não houve `result.json` |
| Parâmetros | **JSON derivado funciona.** O conjunto de flags testado (`--wall-loops`, `--sparse-infill-density`, `--brim-type`, `--brim-width`, `--enable-support`) falhou com -3. Não investiguei qual flag causou isso, porque o JSON resolve |
| Posição | Peça única fica centrada na mesa, com ou sem `--arrange`, mesmo com o modelo na origem |
| Pasta de dados | Com `--datadir`, nada foi escrito em `%APPDATA%` |
| Pasta de trabalho | O Orca grava `00000.log` na pasta atual, com um aviso inofensivo de miniatura. Rode com `cwd` em pasta própria |
| Tempo | Cubo: cerca de 0,5 s |

### Limites testados

| Peça | Resultado |
|---|---|
| Placa de 210 mm com brim de 5 mm | Aceita, extrusão de 0,94 a 219,06 mm |
| Placa de 212 mm com brim de 5 mm | Aceita: o brim é cortado na borda da mesa (0,21 a 219,79 mm) |
| Placa de 217 mm sem brim | Aceita |
| Placa de 218 mm ou 219,8 mm | **Recusada (-50)**, mesmo centrada e sem `--arrange` |
| Coluna de 239 mm | Aceita, Z final de 249,2 mm |
| Coluna de 245 mm | **Aceita, com Z final de 255,2 mm**, acima do limite físico |
| Coluna de 251 mm | Recusada (-50) |

Peça de 245 mm: **o fatiador não pega esse caso**, por isso o limite de 239 mm fica no nosso verificador. Pelo código-fonte, o firmware limitaria o movimento a 250 mm, então isso provavelmente não causaria colisão. Mantive o bloqueio porque não confirmei que o seu firmware é igual ao código publicado. Ele pode virar alerta depois.

### Materiais e qualidades

| Caso | Bico | Mesa | Tempo do cubo | Filamento |
|---|---|---|---|---|
| PLA, 0,20 mm | 220 °C | 55 °C | 16m 33s | 3,69 g |
| PETG, 0,20 mm | 250 °C | 70 °C | 12m 47s | 3,67 g |
| TPU, 0,20 mm | 230 °C | 30 °C | 16m 50s | 3,76 g |
| PLA, 0,12 mm | 220 °C | 55 °C | 26m 50s | 3,78 g |
| PLA, 0,16 mm | 220 °C | 55 °C | 20m 27s | 3,77 g |
| PLA, 0,24 mm | 220 °C | 55 °C | 14m 14s | 3,74 g |

A primeira camada tem sempre 0,20 mm. O topo do cubo de 20 mm fica em 20,00 mm (0,12 e 0,20), 20,04 mm (0,16) e 20,12 mm (0,24).

Os valores numéricos da máquina, do processo e dos filamentos no perfil do projeto conferem com a v2.4.2.

## G-code real

Script: `scripts/fase0/analisar_gcode.py`.

- **Comandos vistos em todos os casos:** `G1 G21 G28 G90 G91 G92 M83 M84 M104 M106 M109 M140 M190 M201 M203 M204 M205 M220 M221`.
- **Modo de extrusão:** absoluto na purga e relativo (`M83`) depois dela, com `;LAYER_CHANGE` marcando cada camada.
- **Limites enviados à máquina:** `M201 X2500 Y2500 Z500 E5000`, `M203 X250 Y250 Z5 E40`, `M204 P2500 R500 T2500`. Por isso, velocidades acima do limite no `F` são só informativas: há `F5000` com Z e `F2700` só em E.
- **Elevação final confirmada:** de Z 20,0 para 30,2, exatamente +10,2 mm.
- **O validador deve checar o destino de cada movimento.** A primeira viagem da camada 1 parte de X=-2,4, e olhar a origem daria falso alarme.

## Firmware oficial

Fonte: [CrealityOfficial/Ender-3V3-SE](https://github.com/CrealityOfficial/Ender-3V3-SE), última atualização em **julho de 2024**. A página da Creality cita o firmware V1.1.0, de maio de 2025, então a sua impressora pode ter uma versão mais nova que este código.

| Item | No código | O que significa |
|---|---|---|
| `ENABLE_LEVELING_AFTER_G28` | Ativo | **A malha é reativada a cada `G28`.** O perfil não precisa de `M420` |
| `HEATER_0_MAXTEMP 275`, `HOTEND_OVERSHOOT 15` | 260 °C | O firmware limita o alvo do bico a 260 °C |
| `BED_MAXTEMP 110`, `BED_OVERSHOOT 10` | 100 °C | O firmware limita o alvo da mesa a 100 °C |
| `X_MIN_POS -13`, `Y_MIN_POS -15` | | A purga em X=-2,4 é um movimento válido |
| `X_MAX_POS 220`, `Y_MAX_POS 227`, `Z_MAX_POS 250` | Soft endstops ativos | Movimentos além disso são limitados |
| `EXTRUDE_MINTEMP 180` | | Não extruda abaixo de 180 °C |
| `SDSUPPORT` ativo, `USB_FLASH_DRIVE_SUPPORT` desligado | | **Cartão SD, sem pen drive** |
| `BAUDRATE 115200` | | Serial, para o futuro controle pela USB-C |
| `FILAMENT_RUNOUT_SENSOR` desligado | | **Sem sensor de fim de filamento** |
| `POWER_LOSS_RECOVERY` ativo | | Recupera após queda de energia |
| `ADVANCED_PAUSE_FEATURE` ativo | | `M600` disponível |
| `MAX_VFAT_ENTRIES` | 2 ou 5 | Nome longo de pelo menos 26 caracteres. O projeto limita a 26 |

## Chave da API no Git

`config/ia_padrao.toml` está no `.gitignore`. `PLANEJAMENTO.md` e `PROMPT_DESENVOLVIMENTO.md` também estão, porque citam a chave em texto aberto. Para versionar esses dois, tire a chave deles antes.

## Cubo de calibração

Modelo: `tests/dados/cubo_calibracao_20mm.py`, com X, Y e Z gravados nas faces. Gerado por `scripts/fase0/gerar_cubo.py`, que confere 11 itens antes de liberar. PLA e PETG foram aprovados.

Arquivos: `saida/impressao/cubo20_pla.gcode` e `saida/impressao/cubo20_petg.gcode`.

## Pendente: precisa da sua impressora

- [ ] **Imprimir o cubo de 20 mm pelo cartão SD e medir X, Y e Z**
- [ ] Confirmar que o visor lista o arquivo com o nome `cubo20_pla.gcode`
- [ ] Versão do firmware (no menu da impressora)
- [ ] Superfície da mesa e marca do filamento
