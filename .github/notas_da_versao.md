## Novidades

- **Atualização pelo próprio app:** a partir desta versão, o Forja3D avisa ao abrir quando houver uma versão nova na página de downloads e atualiza pelo próprio aviso, conferindo o arquivo antes de instalar.
- **Temperaturas antes de preparar:** a barra de impressão ganhou bico, mesa, ventoinha e velocidade, já com os valores do material. O PLA passou a vir com bico em 240 °C e mesa em 65 °C, para a primeira camada segurar melhor.
- **Imprimir pela USB:** com a impressora no cabo, o botão **Imprimir pela USB** manda o G-code aprovado direto para ela, com andamento, pausa, troca de cor e cancelamento. O computador precisa ficar ligado até o fim.
- **Áudio no chat:** o microfone mostra o espectro da voz e transcreve enquanto você fala.

## Downloads

- **Windows 10 ou 11 (x64):** o instalador `.exe`, com o OrcaSlicer 2.4.2 junto. Não pede administrador. Ele não tem assinatura digital, então o SmartScreen pode avisar: clique em **Mais informações** e em **Executar assim mesmo**.
- **Linux (x64, Ubuntu 22.04 ou mais novo):** o pacote `.tar.gz`. Extraia e rode `./Forja3D/Forja3D`. Antes, instale `libegl1 libxcb-cursor0 alsa-utils espeak-ng`. O OrcaSlicer não vem junto: baixe o AppImage da versão 2.4.2 e indique o arquivo em **Configurações**.

Nenhum dos dois leva chave de API. No primeiro uso, o app abre em **Configurações** para você informar o seu provedor.
