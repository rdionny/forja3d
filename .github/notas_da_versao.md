## Novidades

- **Áudio no chat:** clique no microfone ao lado de **Enviar** e fale. As barras mostram o espectro da sua voz, e o texto aparece no campo enquanto você fala, para revisar antes de enviar. O servidor de transcrição, compatível com a OpenAI, fica em **Configurações > Transcrição de áudio**.
- **Linux:** o app roda no Linux, com pacote pronto. A voz usa o `espeak-ng` e a gravação usa o `arecord`. Veja a tabela do que funciona na seção "No Linux" do README.
- **Testes automáticos:** o GitHub Actions roda a suíte inteira no Windows e no Ubuntu a cada envio.

## Downloads

- **Windows 10 ou 11 (x64):** `Forja3D-0.2.0-instalador.exe`, com o OrcaSlicer 2.4.2 junto. Não pede administrador. O instalador não tem assinatura digital, então o SmartScreen pode avisar: clique em **Mais informações** e em **Executar assim mesmo**.
- **Linux (x64, Ubuntu 22.04 ou mais novo):** `Forja3D-0.2.0-linux-x64.tar.gz`. Extraia e rode `./Forja3D/Forja3D`. Antes, instale `libegl1 libxcb-cursor0 alsa-utils espeak-ng`. O OrcaSlicer não vem junto: baixe o AppImage da versão 2.4.2 e indique o arquivo em **Configurações**.

Nenhum dos dois leva chave de API. No primeiro uso, o app abre em **Configurações** para você informar o seu provedor.
