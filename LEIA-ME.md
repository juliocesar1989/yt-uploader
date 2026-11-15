# Live Setup — YouTube + Twitch

Programa para deixar a live pronta com um clique: título, descrição, thumbnail, aviso de IA, tags,
privacidade e playlist no YouTube; título, categoria e tags na Twitch. Perfis por jogo, com
numeração automática (`{n}`) e fila de títulos prontos.

## Abrir

Dê dois cliques em **`Abrir Live Setup.bat`**. Na primeira vez ele cria o ambiente Python e instala
as dependências (leva um minuto).

## Configuração (uma vez só)

### YouTube — arquivo `client_secret.json`

1. Acesse https://console.cloud.google.com/ e crie um projeto (ex.: "Live Setup").
2. Em **APIs e serviços → Biblioteca**, ative a **YouTube Data API v3**.
3. Em **APIs e serviços → Tela de consentimento OAuth** (ou "Google Auth Platform"):
   tipo **Externo**, preencha nome e e-mail, e adicione o seu e-mail como **usuário de teste**.
4. Em **Credenciais → Criar credenciais → ID do cliente OAuth**, tipo **App para computador**.
   Baixe o JSON e guarde na pasta do programa.
5. No programa: **⚙ Contas e configurações → Escolher…** (o JSON) → **Conectar YouTube**.
   Faça login com a conta do canal. O Google vai avisar que o app não é verificado:
   clique em *Avançado → Acessar* (o app é seu).
6. (Opcional) Ainda em Contas: **Carregar chaves** e escolha a **chave que está no OBS**. Só serve
   para escolher a live certa se houver mais de uma pendente no Studio.

> **Importante:** enquanto o app estiver em modo "Teste", o Google desconecta a cada **7 dias**.
> Para não acontecer, na tela de consentimento clique em **Publicar app** (para uso pessoal não
> precisa passar pela verificação; só aparece o aviso de app não verificado no login).

### Twitch — Client ID e Client Secret

1. Acesse https://dev.twitch.tv/console/apps → **Registrar seu aplicativo**.
2. Nome: qualquer um. **URL de redirecionamento OAuth:** `http://localhost:17563`
   Categoria: *Broadcaster Suite*. Tipo de cliente: **Confidencial**.
3. Abra o app criado, copie o **Client ID** e gere um **Novo segredo** (Client Secret).
4. No programa: cole os dois em **⚙ Contas e configurações** → **Conectar Twitch**.

## Uso

1. Crie um perfil por jogo (barra da esquerda).
2. **Geral:** modelo de título, os dois contadores e a descrição padrão. Variáveis:
   - `{n}` e `{s}`: dois contadores que sobem **+1** a cada START bem-sucedido;
   - `{n:2}`, `{s:3}`: o mesmo com zeros à esquerda (`05`, `087`);
   - `{jogo}` nome do perfil · `{data}` data de hoje.

   Exemplo: `RPG PATHFINDER 2E - Campanha Era das Cinzas - Livro 6 - Episódio #{n:2} - Sessão #{s:3}`
   com `{n}` = 5 e `{s}` = 87 → `... Episódio #05 - Sessão #087`.
3. **Fila de títulos:** títulos prontos (com descrição própria opcional) usados em ordem, um por live.
   Fila vazia → usa o modelo. "Adicionar vários…" aceita um título por linha.
4. **YouTube:** thumbnail (veja abaixo), playlist (clique em *Carregar playlists*), tags, privacidade e o aviso
   "conteúdo alterado ou sintético = Não".
5. **Twitch:** busque e escolha a categoria; tags.
6. Clique em **▶ START** **antes** de entrar ao vivo. O programa **não cria nem inicia live**,
   só altera informações:
   - **Twitch:** título, categoria e tags;
   - **YouTube:** encontra a **live pendente que o YouTube Studio já tem** (a da aba *Transmitir*,
     que o OBS vai usar) e aplica título, descrição, thumbnail, tags, privacidade, aviso de IA e
     playlist nela. Se não houver live pendente, avisa e não faz nada no YouTube.
7. Inicie a transmissão no OBS normalmente → a live já começa com os dados certos.
8. **Incremento:** assim que o START termina **sem nenhum erro**, `{n}` e `{s}` sobem +1 e o item
   da fila usado sai. Se algo falhar (Twitch, YouTube, thumbnail ou playlist), nada é incrementado:
   corrija e clique START de novo.
9. Atenção: cada START bem-sucedido incrementa. Para corrigir algo depois de um START que deu
   certo, volte os números em **Próximos contadores** antes de clicar START de novo.

O botão **Verificar live pendente** (aba YouTube) mostra no log quais lives pendentes existem e
qual delas o START vai atualizar (marcada com →).

## Thumbnail por episódio

**Jeito mais simples: escolha a pasta.** Na aba YouTube, clique em **Escolher pasta…** e selecione
a pasta onde ficam as thumbs. O programa pega sozinho o arquivo cujo nome **termina** com o número
da vez:

```
Livro 6 - Episódio 05.png   → usado quando {n} = 5
ep06.jpg                    → usado quando {n} = 6
```

- "Número no nome do arquivo segue" escolhe se o número é o de `{n}` (episódio) ou `{s}` (sessão).
- Vale o **último** número do nome (`Livro 6 - Episódio 05` → 5). Zeros à esquerda não importam.

Também dá para usar um arquivo com variáveis (`E:\Thumbs\ep{n:2}.jpg`) ou um arquivo fixo
(sempre a mesma imagem). Campo vazio = não mexe na thumbnail.

**Verificação em 2 lugares:**
1. **Na tela, antes do START:** a aba YouTube e a barra "Próxima live" mostram qual arquivo vai ser
   usado, com a prévia. Verde = ok; vermelho = problema (não encontrado, extensão diferente de
   JPG/PNG, maior que 50 MB, ou dois arquivos com o mesmo número).
2. **No START:** a mesma verificação é feita de novo; se houver erro, nada é atualizado.

**Botões −1 / +1** (barra "Próxima live"): mudam `{n}` e `{s}` juntos, só para conferir o título
e a thumbnail de outros números. Não mexem no YouTube nem na Twitch. Lembre de voltar ao número
certo antes do START.

## Limitações conhecidas

- A API do YouTube **não permite** definir o campo "Jogo" da live (só o título/categoria geral).
- Thumbnail personalizada exige canal com **telefone verificado**. Máx. 50 MB, JPG ou PNG (limite da API desde 14/09/2026).
- Cada START gasta ~200 unidades de cota da API (o limite diário é 10.000).
- Se houver várias lives pendentes, o START usa a ligada à chave do OBS (se configurada) ou a
  mais antiga. Confira com **Verificar live pendente**.

## Arquivos

- `data/profiles.json` — perfis · `data/settings.json` — configurações
- `data/youtube_token.json` / `data/twitch_token.json` — logins (não compartilhe esses arquivos)
