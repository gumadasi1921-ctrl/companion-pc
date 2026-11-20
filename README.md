# Companion PC

Painel de dados pessoais e atalhos do PC (Windows 11), acessado pelo navegador
de qualquer aparelho da rede local — celular, tablet ou outro computador.
Mostra o sistema (CPU, memória, GPU, discos, rede), o que está tocando, jogos e
tempo jogado, notificações, e controla o PC: botões estilo Stream Deck, mouse e
teclado, tela ao vivo, arquivos, área de transferência e energia.

Não precisa instalar nada no aparelho: abra no navegador e, no celular, use
"Adicionar à tela inicial" (é um PWA). Funciona em pé e deitado.

```
Aparelho (navegador/PWA) ──rede local (Wi-Fi/cabo)──  http://<IP do PC>:8770
Celular Android          ──cabo USB (adb reverse)───  http://localhost:8770
```

**Preparar o PC (uma vez):** `windows\setup-network.ps1`
(libera a porta 8770 no firewall em redes Privadas e instala o adb, usado só no modo cabo).

- **Rede local:** abra o painel (ícone na bandeja) e escaneie o QR code com a câmera
  do celular — o link já leva o token. Ou abra `http://<IP do PC>:8770` e cole o token.
- **Cabo (opcional, Android):** no celular ative *Opções do desenvolvedor → Depuração USB*, ligue no
  PC e aceite a permissão. O servidor faz o `adb reverse` sozinho; abra
  `http://localhost:8770` no Chrome (o botão "Abrir app no celular (cabo)" do
  painel abre já com o token). Pelo cabo dá para instalar como app de verdade;
  "manter a tela ligada", notificações e "Compartilhar com Companion PC" só
  funcionam assim.

### Abas

Visual de terminal de fósforo (verde, âmbar ou ciano, com linhas de varredura opcionais;
troque em **PC › Ajustes › Visual**). Cinco seções na barra; cada uma tem sub-abas no topo.
Em tablet deitado (≥ 1000 px de largura) as sub-abas de uma seção aparecem lado a lado.

| Seção | Sub-abas e o que tem |
|---|---|
| **Status** | *Sistema*: CPU, memória, GPU (consumo, ventoinha) e rede com gráfico do último minuto, processos, discos; temperatura da CPU e ventoinhas se o LibreHardwareMonitor estiver rodando. *Jogos*: jogo aberto, tempo jogado, recentes e Discord (microfone/áudio). |
| **Rádio** | *Tocando*: capa, faixa, progresso (arraste para pular), volume e saída de áudio (toque para alternar). *Mixer*: volume e mudo por aplicativo e escolha da saída (fone, caixa, monitor). |
| **Controle** | *Mouse e teclado*: touchpad (toque = clique; 2 dedos: toque = direito, arrastar = rolar; toque duplo e arraste = segurar), teclas especiais e o teclado do Android digitando no PC. *Tela do PC*: a tela ao vivo; toque = clique, segurar = botão direito, pinça = zoom, faixa da direita = rolar. |
| **Botões** | Páginas de botões estilo Stream Deck. **Editar** cria/edita botões (atalho de teclado, texto, ação pronta ou programa do config.json), com ícone, cor e confirmação. Ficam salvos em `deck.json` no PC. |
| **Tarefas** | Lista de tarefas: no celular só ver e marcar/desmarcar. Adicionar, editar, remover, reordenar e limpar feitas é no painel do PC (aba Tarefas). Ficam em `tasks.json` no PC. |
| **PC** | *Arquivos*: texto/link para a área de transferência do PC (ou já colar), fotos e arquivos para `Downloads\Companion PC`, e "Pegar o que está copiado no PC". *Energia*: bloquear, monitor, suspender, reiniciar, desligar. *Ajustes*: alertas (download concluído, GPU/CPU quente, memória cheia), cor do terminal e ajustes do aparelho. |

**Extras**

- **Botões que seguem o programa:** cada página pode ter uma lista de programas (`code.exe`,
  `spotify.exe`...; `@jogo` = qualquer jogo detectado). Quando um deles entra em foco no PC, a página
  abre sozinha. Já vêm páginas para navegador, VS Code, Terminal, Discord, Spotify, WhatsApp e jogos.
  Edite em **Botões › Editar** tocando no nome da página.
- **Microfone do PC:** o indicador `MIC` no topo coloca no mudo o microfone padrão e o de comunicação
  (vale para qualquer programa). Também existe como ação de botão.
- **Timer de energia:** em **PC › Energia**, desligar, suspender ou desligar o monitor daqui a
  15 min a 2 h. A contagem aparece no topo; toque para cancelar.
- **Modo base:** depois de alguns minutos sem tocar (ajuste em **PC › Ajustes**), o celular mostra
  um relógio grande com CPU/GPU, música e jogo. A posição muda um pouco a cada minuto para não marcar a tela.
- **Jogos:** em **Status › Jogos** ficam a biblioteca (Steam, Epic e GOG; toque para abrir o jogo),
  as horas jogadas por dia na semana e os jogos mais jogados.
- **Ping:** a latência da internet aparece no card Rede; o alerta "Internet caiu" avisa quando cai e volta.
- **Sons de terminal:** bipes curtos ao tocar, conectar e em avisos (**PC › Ajustes › Visual**).
- **Macros:** botão do tipo *Sequência*, um passo por linha: `teclas ctrl+t`, `texto olá`,
  `esperar 500` (ms) ou `esperar 2s`, `abrir vscode` (id ou nome do programa do config.json),
  `acao media.next`. No máximo 40 passos e 60 s de espera.
- **Notificações do PC:** o que aparece na Central de notificações do Windows (WhatsApp, Discord,
  e-mail...) chega no celular. Se estiver bloqueado: Configurações do Windows › Privacidade ›
  Notificações. Para ignorar apps, `"notif_ignore": ["Microsoft Edge"]` no config.json.
- **FPS do jogo:** com o PresentMon Console (`winget install Intel.PresentMon.Console`). O Windows só
  deixa medir FPS sem admin para quem está no grupo *Usuários de log de desempenho*; rode uma vez
  num PowerShell como Administrador e depois saia e entre na conta:
  `Add-LocalGroupMember -SID S-1-5-32-559 -Member $env:USERNAME`
- Programas do `config.json` aceitam `"appx": "<AppID>"` para apps da Microsoft Store
  (veja com `Get-StartApps` no PowerShell).

**Temperatura da CPU / ventoinhas (opcional):** o Windows não fornece isso sem
driver. Se o LibreHardwareMonitor estiver rodando com *Options → Remote Web Server → Run*,
o servidor lê de `http://127.0.0.1:8085` e mostra no Painel. Neste PC a versão
0.9.6 travou ao abrir (sem o driver PawnIO), então ficou sem — a GPU (temperatura,
ventoinha, consumo) vem do `nvidia-smi` e funciona sem nada extra.

Para aceitar só o cabo, use `"web": {"host": "127.0.0.1"}` no `config.json`;
para mudar a porta, `"web": {"port": 8770}`. Pastas: `"upload_dir"` (onde chegam
os arquivos do celular) e `"downloads_dir"` (a pasta vigiada para o alerta de download).

## Estrutura

| Caminho | O que é |
|---|---|
| `windows/server/companion_server.py` | Servidor (HTTP + WebSocket na porta 8770) |
| `windows/server/web/` | App web (HTML/JS, PWA) |
| `windows/server/tray.py` | Ícone na bandeja + painel de status com QR code |
| `windows/server/pc_info.py` | Mídia tocando (GSMTC), volume, mixer e saída de áudio (pycaw), GPU (nvidia-smi), sensores (LHM) |
| `windows/server/extras.py` | Deck, área de transferência, arquivos, tela do PC, vigia de downloads |
| `windows/server/games.py` | Jogo aberto, biblioteca (Steam/Epic/GOG) e histórico de horas |
| `windows/server/watchers.py` | Ping, notificações do Windows, FPS (PresentMon) |
| `windows/install-server.ps1` | Instala dependências e inicia o servidor com o Windows |
| `windows/setup-network.ps1` | Firewall da porta 8770 + adb (uma vez; pede admin só para o firewall) |

## Instalação

1. `powershell -ExecutionPolicy Bypass -File windows\install-server.ps1`
2. Uma vez: `windows\setup-network.ps1`
3. O token fica em `%USERPROFILE%\companion-pc\server\config.json`
   (quem tinha a instalação antiga em `r36s-companion` tem o token, deck e histórico migrados).

O servidor roda em segundo plano (pythonw), só com um ícone na bandeja:
verde = aparelho conectado, amarelo = aguardando conexão, cinza = porta indisponível.
Duplo clique no ícone (ou abrir "Companion PC" no Menu Iniciar) mostra o
painel com status, QR code, jogo atual e log; o menu tem Reiniciar e Sair. Só roda uma
instância — abrir de novo apenas mostra o painel. Para depurar no terminal:
`py -3 companion_server.py --console`.

## Segurança

- O servidor escuta na porta 8770 (rede local + cabo) e exige o token do `config.json`.
  O tráfego no Wi-Fi não é criptografado: use em rede doméstica de confiança
  (o firewall só libera redes Privadas). Texto digitado ou copiado pelo celular não vai para o log.
- Quem tem o token controla o PC (teclado, mouse, tela). Botões criados pelo
  celular só podem apertar teclas, digitar, usar ações prontas ou abrir programas
  já listados no `config.json` — caminhos novos de programas só pelo PC.
- Arquivos do PC só ficam acessíveis ao aparelho quando você os copia e toca em
  "Pegar o que está copiado", por um link aleatório que expira em 15 min.
- Só executa ações de uma lista fechada (`ACTIONS` no servidor); programas
  só abrem se estiverem em `"shortcuts"` do `config.json`. Não há execução
  de comandos arbitrários.
- Desligar/reiniciar usam atraso de 30 s (`shutdown_delay_s`) e podem ser
  cancelados em **PC › Energia**. `"allow_power": false` desativa a energia.
- Ao desconectar, o servidor solta Alt e o botão do mouse se estavam presos.

## Personalizar atalhos

Em `config.json` do servidor:
```json
{"id": "spotify", "name": "Spotify", "open": "spotify:"},
{"id": "proj", "name": "Projetos", "open": "D:\\Projetos"},
{"id": "obs", "name": "OBS", "open": "C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe"}
```
Reinicie o servidor após editar.
