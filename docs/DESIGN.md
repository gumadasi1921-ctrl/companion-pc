# Companion PC — Design (redesign 2026-11)
Mockups aprovados: canvas "Companion PC — visual moderno". Prioridade: celular DEITADO.

## 1. Temas
- data-theme="modern" (padrão) | "fallout" (visual atual de terminal, preservado; as opções verde/âmbar/ciano e linhas de varredura só aparecem com Fallout).
- Moderno: data-mode = auto (prefers-color-scheme) | light | dark; data-accent = blue | purple | green | orange | pink.
- Preferências por aparelho em store: theme, mode, accent.

## 2. Tokens — Moderno escuro
--bg #000000 | --surface #1C1C1E | --surface-2 #2C2C2E | --surface-3 #3A3A3C | --nav-bg #121214 | --separator #38383A
--text #F2F2F7 | --text-2 #A1A1A6 | --text-3 #98989D
--accent-fill #1F6FEB (texto branco) | --accent-text #4C9AFF | --accent-soft #1C2B45
--success #30D158 | --warning #FF9F0A | --danger-text #FF6961 | --danger-fill #D70015 | --danger-soft #3A1214
Gráficos: cpu #4C9AFF, gpu #BF5AF2, memória #FF9F0A, download #64D2FF, upload #FF9F0A; clientes em sequência #4C9AFF, #BF5AF2, #FF9F0A, #64D2FF, #30D158.
## 3. Tokens — Moderno claro
--bg #F2F2F7 | --surface #FFFFFF | --surface-2 #E5E5EA | --surface-3 #D1D1D6 | --nav-bg #F9F9FB | --separator #C6C6C8
--text #000000 | --text-2 #3C3C43 | --text-3 #636366 | --accent-text #0066CC | --accent-soft #E1ECFB | --success #1B7431 | --warning #9A4600 | --danger-text #D70015
Destaques (fill, texto branco): blue #1F6FEB, purple #8E44EC, green #187F3B, orange #B4530A, pink #D6336C.
Todo texto ≥ 4,5:1 de contraste nos dois modos.

## 4. Forma e tipo
Fonte: -apple-system, BlinkMacSystemFont, "Segoe UI Variable", "Segoe UI", system-ui, sans-serif (sem fonte baixada).
Títulos: 24px/700 (celular deitado), 34px/700 (em pé), 30px/700 (tablet/PC). Corpo 17/15px. Legenda 13px. Rótulo 11–12px/600 maiúsculas.
Raios: card 22 | grupo de lista 16–18 | botão 18 | pílula 999 | pequeno 10. Espaços: 4/8/12/16/20/24.
Alvo de toque ≥ 44px. Ícones: SEMPRE do sprite existente web/icons/lucide.svg (traço 1.9), nunca emoji.

## 5. Layout por tela (safe-area via env())
- Celular deitado (landscape, altura < 500): trilho à esquerda de 92px, 5 itens (ícone 26 + rótulo 11), item ativo com fundo --accent-soft e cor --accent-text; selo vermelho com as tarefas pendentes. Conteúdo com padding 14/20/18/16.
- Celular em pé (largura < 600): barra de abas embaixo, 84px, com fundo desfocado (backdrop-filter) e os mesmos 5 itens.
- Tablet/PC (≥ 1000×600): barra lateral de 220px, grupo "Principal" (Início, Controle, Botões, Tarefas) + grupo "Mais" (Rádio e mixer, Jogos, Trabalho, Arquivos, Energia, Ajustes) direto, sem tela "Mais".
Seções: inicio | ctrl | deck | tasks | mais (lista → radio, games, work, files, power, settings). Em telas largas, a seção Mais vira lista à esquerda (250px) + detalhe à direita.

## 6. Telas
- Início: widgets em 4 tamanhos — P pequeno (1×1, só o essencial), A alto (1×2), L largo (2×1) e G grande (2×2); cada widget diz quais suporta e tem um layout próprio para cada um (nunca esticado de outro tamanho). Celular deitado: grade 3 colunas × 2 linhas por página; tablet: 4×3 (A = 1×2, L = 2×1, G = 2×2). Os widgets entram na ordem da lista, cada um no primeiro espaço livre (esquerda→direita, cima→baixo); o que não cabe vai para a página seguinte (máx. 3). Páginas por rolagem horizontal com scroll-snap (sem biblioteca) e bolinhas tocáveis (44px) na linha do título; a página atual é lembrada por aparelho (store "homepg"). Em pé: os mesmos widgets empilhados com rolagem vertical (P lado a lado em 2 colunas; A, L e G na largura toda). Faixa do Foco: com o Pomodoro rodando ou pausado e o widget Foco fora do layout, uma faixa compacta (fase, tempo, Pausar/Continuar de 44px) aparece junto ao título do Início; tocar nela abre Tarefas › Foco; some ao voltar a idle e não aparece se o widget está no layout.
- Tarefas: à esquerda um anel de progresso 140px ("Faltam N" / "Só falta 1!"); quando todas estão feitas, tela "Tudo feito!" com check verde e vibração curta. À direita, as pendentes e depois "Feitas" (riscadas, --text-3). Tocar marca na hora; toast "Desfazer" por 4 s. Rodapé: "Adicionar e editar: painel do PC".
- Tarefas › Foco (Pomodoro, relógio no servidor): seletor segmentado "Lista | Foco" no topo. Anel grande (240 em pé, 180 deitado) com mm:ss em algarismos tabulares e o nome da fase; bolinhas do ciclo; Começar/Pausar/Continuar em --accent-fill (56px) + Pular e Zerar; "Focando em" (pendentes, opcional); "Hoje: N pomodoros"; "Ajustes do Pomodoro" abre durações, a cada N focos e começar a próxima fase sozinho. Pausas usam --success no anel. Sem animação do anel (salta por segundo). No fim da fase: toast + vibração curta + som curto.
- Botões: título + páginas em pílulas (a página ativa é clara/invertida; ponto verde = aberta automaticamente pelo programa em foco) + "Editar" em texto. Grade de 5×3 (deitado) / 3 colunas (em pé), cada botão com ÍCONE colorido (cor da categoria) + nome 15/600 + atalho 12/600 --text-3. Ao tocar: escala 0,95 por 120 ms. O editor mostra o básico; macros e opções avançadas ficam atrás de "Mais opções".
- Rádio: capa grande | título, artista, barra de posição, anterior / Play 72 (--accent-fill) / próxima, volume, pílula da saída de áudio. Seletor no topo: Tocando | Mixer.
- Mais › Energia: Bloquear, Desligar monitor, Suspender (neutros); Reiniciar… (--warning) e Desligar… (--danger-soft/--danger-text) abrem uma contagem com anel de 30 s e um botão "Cancelar" grande; pílulas de timer 15 min / 30 min / 1 h / 2 h.
- Ajustes › Aparência: 2 cartões de tema com prévia (Moderno / Fallout), seletor Automático/Claro/Escuro, 5 bolinhas de destaque.
- Mais › Tempo (previsão da Open-Meteo, buscada pelo servidor só para a cidade escolhida em Ajustes › Tempo; nunca localização automática): cartão "Agora" (cidade, ícone grande, temperatura 72px, condição, sensação, umidade, vento, nascer e pôr do sol); "Próximas 24 horas" em faixa rolável na horizontal (colunas de 64px: hora, ícone, temperatura, % de chuva); "Próximos 7 dias" em lista (dia, ícone, % de chuva, mín, barra mín→máx na mesma escala entre os dias — barra --accent-fill sobre trilho --surface-3 — e máx), estilo app Tempo do iPhone; rodapé "Atualizado às HH:MM" (ou "Desatualizado desde HH:MM" sem internet, mantendo a última previsão) e botão "Atualizar" de 44px (o servidor aceita 1 por minuto). Dado ausente fica escondido (nada inventado); sem cidade = cartão "Escolher a cidade em Ajustes". Ícones: Lucide (sun, moon, cloud, cloud-sun, cloud-moon, cloud-fog, cloud-drizzle, cloud-rain, cloud-snow, cloud-lightning, wind, droplets, sunrise, sunset); texto e ícone de cada código WMO (dia/noite) vêm da tabela única do servidor (weather.py).
- Widget Tempo (P, A, L, G; entra nos padrões só de quem nunca personalizou o Início): P = ícone + temperatura; L = ícone, temperatura, condição e mín/máx à esquerda e, à direita, a curva de temperatura das próximas 12 h (área --chart-up com escala Agora · mín–máx · hora final); A = ícone + temperatura + condição, as próximas 24 h em 6 colunas (hora, ícone, temperatura, de 4 em 4 h) e 3 dias com barra mín→máx; G = igual ao A com 4 dias (barra mín→máx) à esquerda e, à direita, a curva das 24 h + colunas de hora/ícone/temperatura; cidade no título; "Desatualizado desde HH:MM" sem internet; sem cidade = "Escolha sua cidade em Ajustes › Tempo" (tocar leva lá); aguardando a primeira busca = esqueleto. Dado ausente fica escondido; menos de 2 temperaturas = sem curva (nunca valores simulados). Tocar abre Mais › Tempo.
- Gráficos dos widgets do Início (SVG desenhado no app, sem biblioteca, cores da seção 2, variáveis do tema no Fallout): CPU e GPU = linhas com área, últimos 60 s, escala "−60 s · 100% · agora" (L compacto sem escala; A/G com temperatura, consumo, ventoinha e GHz em legenda); Rede = ↓ (área --chart-down) e ↑ (linha --chart-up) com pico na escala; Memória = anel --chart-mem (P pequeno; A/L com barras dos discos; G anel 120px + até 4 discos); Tarefas = anel de progresso (P/L/G) ou barra (A) + próximas pendentes; Trabalho e Jogos = barras por dia da semana (L compacto; A/G com barra empilhada por cliente / top jogos); Mais pesados = barra de CPU em cada processo (A/G); Tocando = capa grande (A e G) e barra da faixa (A/L/G); Foco = anel da fase (L/A/G). Sem histórico suficiente o gráfico mostra só os pontos que existem. Atualização 1×/s só com o Início visível: os SVG nascem uma vez e só mudam atributos (points, stroke-dashoffset, textos); nunca refeitos a cada segundo.
- Ajustes › Tempo: cidade atual + "Remover", campo de busca ("Buscar cidade") e até 5 resultados (nome, estado, país) de 52px; a escolhida fica salva no servidor (config.json weather.place) e vale para todos os aparelhos.
- Mais › Widgets do Início (também pelo botão "Editar" no topo do Início): seletor Celular | Tablet (listas separadas, salvas por aparelho em store "home", v2 = [{id,size}]). Cada linha: remover (círculo vermelho), subir/descer, a página em que o widget cai ("Página 1") e um seletor segmentado P·A·L·G só com os tamanhos que o widget suporta (desativa o que não cabe nas 3 páginas). Embaixo, pílulas "+" com os widgets de fora (entram no tamanho padrão do widget, ou num menor se não couber). O aviso "Lotado" só aparece quando as 3 páginas estão cheias (nem um P cabe). Remover tem Desfazer; "Restaurar" volta ao padrão. Migração do layout antigo (v1): widgets do celular viram A; no tablet Grande vira G e Pequeno vira P (se o widget não tem P, o tamanho mais parecido que ele suporta); layout inválido cai no padrão. Catálogo (tamanhos): now PALG, tasks PALG, pcquick PAG, cpu PALG, net PALG, mem PALG, work PALG, today PAL, procs PALG, games PALG, notif PALG, quick PAL, pomo PALG, dota PAG, weather PALG. Padrão celular: now A, tasks A, pcquick A. Padrão tablet: cpu L, net L, now G, tasks A, mem A, work L, games L, procs L, notif L, quick L.

## 7. Comportamento (regras de UX)
- Resposta < 400 ms: toques mudam a tela NA HORA (atualização otimista); se o PC recusar, volta ao estado anterior e mostra um toast de erro.
- Desfazer em vez de "tem certeza?"; confirmação só para desligar/reiniciar (contagem com cancelar).
- Carregando: esqueleto (blocos --surface-2 pulsando), nunca "Buscando…".
- Conexão caiu: faixa discreta "Reconectando…" no topo; nunca voltar à tela de token por queda de rede.
- Uma única ação em --accent-fill por tela. Ações principais na metade de baixo / perto do polegar.
- prefers-reduced-motion: sem escala, sem animação de anel, sem pulsar.
- Painel do PC: o MESMO app web, numa janela própria no PC, em "modo painel" (seção Gerenciar: tarefas, pastas de clientes, QR code, ajustes do PC).
