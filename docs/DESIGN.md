# Companion PC — Design (redesign 2026-11)
Mockups aprovados: canvas "Companion PC — visual moderno". Prioridade: celular DEITADO.

## 1. Temas
- data-theme="modern" (padrão) | "fallout" (visual atual de terminal, preservado; as opções verde/âmbar/ciano e linhas de varredura só aparecem com Fallout).
- Moderno: data-mode = auto (prefers-color-scheme) | light | dark; data-accent = blue | purple | green | orange | pink.
- Preferências por aparelho em store: theme, mode, accent.

## 2. Tokens — Moderno escuro
--bg #000000 | --surface #1C1C1E | --surface-2 #2C2C2E | --surface-3 #3A3A3C | --nav-bg #121214 | --separator #38383A
--text #F2F2F7 | --text-2 #A1A1A6 | --text-3 #8E8E93
--accent-fill #1F6FEB (texto branco) | --accent-text #4C9AFF | --accent-soft #1C2B45
--success #30D158 | --warning #FF9F0A | --danger-text #FF6961 | --danger-fill #D70015 | --danger-soft #3A1214
Gráficos: cpu #4C9AFF, gpu #BF5AF2, memória #FF9F0A, download #64D2FF, upload #FF9F0A; clientes em sequência #4C9AFF, #BF5AF2, #FF9F0A, #64D2FF, #30D158.
## 3. Tokens — Moderno claro
--bg #F2F2F7 | --surface #FFFFFF | --surface-2 #E5E5EA | --surface-3 #D1D1D6 | --nav-bg #F9F9FB | --separator #C6C6C8
--text #000000 | --text-2 #3C3C43 | --text-3 #6E6E73 | --accent-text #0066CC | --accent-soft #E1ECFB | --success #248A3D | --danger-text #D70015
Destaques (fill, texto branco): blue #1F6FEB, purple #8E44EC, green #1E9E4A, orange #D9650B, pink #D6336C.
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
- Início (deitado): 3 colunas — Tocando (capa 84, Play 64 em --accent-fill, anterior/próxima 48, barra de progresso) | Tarefas (x de y + barra + 3 próximas marcáveis) | PC (CPU/GPU/Memória em barras) + 4 atalhos 2×2. Em pé: o mesmo, empilhado. Tablet: grade 4×3 — CPU/GPU 60 s em linha (2 colunas, com temperaturas/ventoinha/consumo), Rede ↓↑, Memória (anel) + discos | Tocando, Tarefas (anel), Trabalho na semana em barras empilhadas por cliente (2 colunas) | Mais pesados agora (top 5), Jogos (horas/semana), Notificações (3 últimas), Atalhos.
- Tarefas: à esquerda um anel de progresso 140px ("Faltam N" / "Só falta 1!"); quando todas estão feitas, tela "Tudo feito!" com check verde e vibração curta. À direita, as pendentes e depois "Feitas" (riscadas, --text-3). Tocar marca na hora; toast "Desfazer" por 4 s. Rodapé: "Adicionar e editar: painel do PC".
- Botões: título + páginas em pílulas (a página ativa é clara/invertida; ponto verde = aberta automaticamente pelo programa em foco) + "Editar" em texto. Grade de 5×3 (deitado) / 3 colunas (em pé), cada botão com ÍCONE colorido (cor da categoria) + nome 15/600 + atalho 12/600 --text-3. Ao tocar: escala 0,95 por 120 ms. O editor mostra o básico; macros e opções avançadas ficam atrás de "Mais opções".
- Rádio: capa grande | título, artista, barra de posição, anterior / Play 72 (--accent-fill) / próxima, volume, pílula da saída de áudio. Seletor no topo: Tocando | Mixer.
- Mais › Energia: Bloquear, Desligar monitor, Suspender (neutros); Reiniciar… (--warning) e Desligar… (--danger-soft/--danger-text) abrem uma contagem com anel de 30 s e um botão "Cancelar" grande; pílulas de timer 15 min / 30 min / 1 h / 2 h.
- Ajustes › Aparência: 2 cartões de tema com prévia (Moderno / Fallout), seletor Automático/Claro/Escuro, 5 bolinhas de destaque.

## 7. Comportamento (regras de UX)
- Resposta < 400 ms: toques mudam a tela NA HORA (atualização otimista); se o PC recusar, volta ao estado anterior e mostra um toast de erro.
- Desfazer em vez de "tem certeza?"; confirmação só para desligar/reiniciar (contagem com cancelar).
- Carregando: esqueleto (blocos --surface-2 pulsando), nunca "Buscando…".
- Conexão caiu: faixa discreta "Reconectando…" no topo; nunca voltar à tela de token por queda de rede.
- Uma única ação em --accent-fill por tela. Ações principais na metade de baixo / perto do polegar.
- prefers-reduced-motion: sem escala, sem animação de anel, sem pulsar.
- Painel do PC: o MESMO app web, numa janela própria no PC, em "modo painel" (seção Gerenciar: tarefas, pastas de clientes, QR code, ajustes do PC).
