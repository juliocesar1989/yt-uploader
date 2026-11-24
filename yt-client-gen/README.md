# YouTube OAuth Client Generator (extensão Chrome)

Cria um cliente OAuth "App para computador" no Google Cloud Console e baixa
o `client_secret.json` automaticamente, no formato que o `youtube_auth.py` usa.

## Instalar
1. Descompacte o zip.
2. Abra `chrome://extensions` e ative o **Modo do desenvolvedor**.
3. Clique em **Carregar sem compactação** e escolha a pasta `yt-client-gen`.

## Usar
1. Clique no ícone da extensão e informe o **ID do projeto** (ou abra o Console no projeto e clique em **Aba**).
2. **1 · Ativar YouTube Data API v3**
3. **2 · Tela de consentimento**: só na primeira vez em cada projeto (nome do app, e-mail, público "Externo", adicione seu e-mail como usuário de teste).
4. **3 · Criar cliente e baixar JSON**: preenche tipo e nome, clica em Criar e salva `client_secret_<projeto>.json` em Downloads.

Se algum clique automático falhar (o Google muda a interface às vezes), termine
manualmente. A extensão continua observando a página e baixa o JSON assim que
o diálogo com o ID e o secret aparece.

## Usar pelo Live Setup
Em **⚙ Contas e configurações → Gerar automaticamente**, o `app.py` sobe um
servidor temporário em `127.0.0.1` e abre a página dele no Chrome, no perfil
onde esta extensão está instalada. O `bridge.js` lê o pedido dessa página e a
extensão executa as etapas 1 e 3 em sequência, mandando o progresso para o app.
Depois do download, ela envia o caminho do arquivo e o app move o JSON para a
pasta do programa. Depois de atualizar a extensão, clique em **Recarregar** em
`chrome://extensions`.

## Segurança
O secret só vai para o arquivo local em Downloads. A extensão só roda em
`console.cloud.google.com` e na página local do Live Setup (`127.0.0.1`), e só
conversa com o próprio app nessa máquina (cada pedido tem um token aleatório).
