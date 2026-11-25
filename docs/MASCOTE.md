# Mascote do Companion — desenho (sem código)

Status: **proposta para revisão**. Nada aqui foi implementado. Base lida: `CLAUDE.md` (Mapa rápido), `graphify-out/GRAPH_REPORT.md` (só cabeçalhos), `docs/DESIGN.md` (só títulos), e no grafo `push_notif`, `FocusHold`, `pomo_state`, `focus_deliver`. Nomes de campo/trecho que **não** foram abertos estão marcados "confirmar na etapa N".

Princípios (valem para tudo abaixo):

1. **Companhia, não cobrança.** Nunca morre, nunca pune, nunca "fica com raiva". O pior estado é "dorminhoco e com saudade".
2. **Dois tipos de fala, nunca misturados.** (a) *Fatos do PC* — vêm de um evento real e mostram o número real ("Você está há 1 h 40 no Photoshop"); (b) *Vida do mascote* — sede, fome, sono: são **do personagem**, simuladas, e o app diz isso (selo "Lume" vs. selo do PC). Nenhum aviso afirma um fato que não veio de dado real.
3. **Silêncio é padrão seguro.** Em dúvida (sem dado, sem conexão, foco ligado), o mascote fica quieto.
4. **Original.** Personagem, nome, forma e comportamento próprios. Sem ovo, sem tela pixelada, sem ciclo de "evolução/morte" de bichinho virtual.

---

## 1. Personagem

### 1.1 Identidade

**Lume** — uma criaturinha-lanterna. Corpo de pedra de rio (cápsula larga e macia, mais larga que alta), dois pezinhos curtos, e uma antena fina com um **bulbo de luz** na ponta. A luz é o "indicador de vida": a cor segue o **acento do tema** do app (Fallout verde/âmbar/ciano, Moderno em qualquer acento), então o Lume combina com a interface sem asset novo por tema. Corpo: creme quente com leve gradiente; olhos grandes e ovais com brilho; boca mínima.

Por que não é cópia: não é ovo, não é pixel art, não tem "geração", fome que mata nem tela de ícones. A forma (cápsula + antena-luz) e o papel (colega de mesa que avisa) são do Companion.

### 1.2 Técnica (recomendação)

| Opção | Peso | Bateria | Veredito |
|---|---|---|---|
| **SVG inline em camadas + CSS (sombreamento por gradientes)** | ~8–12 KB SVG + ~3–4 KB CSS | animações só de `transform`/`opacity` (compositor); zero JS por quadro | **Escolhida** |
| Lottie (lottie-light) | ~50–150 KB de lib + JSON por estado | canvas/SVG renderizado em JS a 30–60 fps | Não: lib externa, mais pesado, difícil de tematizar |
| three.js pequeno | 600 KB+ | GPU contínua, aquece o aparelho | Não: desproporcional para um mascote de 56 px |
| Sprites WebP pré-renderizados | 40–120 KB por estado | barato, mas sem tema/acento | Só como plano B se o SVG ficar caro em algum aparelho |

**Efeito "3D leve" sem 3D:** corpo com `radialGradient` (luz vinda de cima à esquerda), brilho especular nos olhos e no bulbo, sombra de contato elíptica no chão (a bolha flutua, então a sombra é só visual), micro-inclinação (`rotate`/`translateY`) ao arrastar e "squash & stretch" no toque. Bulbo com halo por `filter: blur` **só no estado atual** (um único filtro; nunca blur animado).

**Orçamento (a conferir na medição, etapa 9):**
- Adicional total ≤ ~30 KB sem gzip no `index.html` (SVG + CSS + JS do mascote).
- No máximo 3 animações CSS simultâneas, nenhuma em `filter`/`box-shadow`/layout.
- Respiração = 1 keyframe de 4 s; piscar = 1 keyframe de 5–7 s com atraso aleatório fixado por CSS variable (sem timer JS).
- **Pausa total** quando a aba não está visível (`visibilitychange`) e quando o mascote está dormindo/oculto (`animation-play-state: paused`, ou o nó nem existe).
- `prefers-reduced-motion` e o interruptor **"Economia" (Ajustes › Mascote)** trocam tudo por poses estáticas (uma pose SVG por estado). Sem `getBattery` (não existe no iOS), então a economia é manual.
- Tamanho visual 56 px, alvo de toque ≥ 48 px (padrão do app é 44 px; igual à bolinha do mic).

**Fallback e legacy:** o teste de compatibilidade no topo do `index.html` já manda iPad 2/iOS 9 para `legacy.html`; o mascote **não existe no legacy** (nem código, nem aviso). Dentro do `index.html`, sem suporte a algo usado (ex.: `ResizeObserver`/Pointer Events já são pré-requisito do app e da bolinha do mic), o mascote simplesmente não liga — o toast atual continua.

### 1.3 Estados e animações

Cada estado = pose estática (fallback) + 1 animação curta em laço. Sempre dá para reconhecer o estado só pela pose + cor do bulbo, sem som e sem texto.

| Estado | Quando entra | Pose / laço | Bulbo |
|---|---|---|---|
| **Feliz** (base) | tudo em dia, ocioso | olhos abertos, leve sorriso; respira 4 s, pisca | acento, brilho médio |
| **Concentrado** | Pomodoro em fase Foco rodando | olhos semicerrados, sobrancelha reta; quase parado; **sem balões** | acento, brilho baixo e firme |
| **Cansado** | trabalho contínuo longo (fato do servidor) ou fim do dia | olhos caídos, bocejo ocasional, corpo "derrete" 4% | acento, mais fraco |
| **Com sede** | vida do mascote: faz tempo sem "dar água" (só se o lembrete de água está ligado) | língua para fora, olha para o copo (ícone) | acento pulsando devagar (2,4 s) |
| **Com fome** | vida do mascote: faz tempo sem petisco | barriga com onda, olhos grandes | idem |
| **Dormindo** | usuário o pôs para dormir | olhos fechados, "z" sobe e some; **bolha oculta** durante o sono | apagado |
| **Comemorando** | Pomodoro concluído, tarefa concluída, conquista | pulinho + brilhos curtos (≤ 1,2 s, 1 vez) | acento forte |
| **Avisando** | há balão para entregar | inclina para o lado do balão, acena; balão abre | acento, 2 piscadas |

Regras de transição: um estado por vez, prioridade **Dormindo > Concentrado > Avisando > Comemorando > Com sede/Com fome > Cansado > Feliz**. Comemorar e Avisar duram segundos e voltam ao estado de base. "Com sede/fome" nunca fica triste de verdade: é um convite, não um castigo.

---

## 2. Comportamento na tela

### 2.1 Bolha flutuante

Reaproveita as regras da bolinha do mic (`#mic-fab`, `micFabArea`, `micFabPaint`), **sem reinventar**:

- Botão fixo 56 px, `z-index` 38 (abaixo dos diálogos e do toast; a bolinha do mic é 39 e fica por cima se encostarem).
- Arrastar com Pointer Events, limiar 8 px; **arrastar nunca dispara toque**.
- Área útil = tela menos área segura (`env()`), cabeçalho e trilho/barra de baixo — a mesma função de área do mic (extraída para uso comum na etapa 2).
- Posição = fração {x,y} da área, guardada **por orientação** (`p` em pé/barra embaixo, `l` trilho lateral) em chave própria `pcc.mascot.pos`, **por aparelho**, fora do layout e sem `pk()` (como `pcc.micfab`).
- Reposiciona em `resize`/`orientationchange` e a cada 2 s.
- Diferença: ao soltar, **encosta na borda esquerda ou direita mais próxima** (ímã), y livre. Assim nunca fica no meio do conteúdo.
- **Não cobrir controles:** padrão = borda esquerda, y ≈ 0,85 (a bolinha do mic é canto inferior direito). Depois de 8 s sem interação a bolha vai a 60% de opacidade e fica meio metida na borda ("espiando"); toque ou balão a trazem de volta. Telas medidas como sensíveis (ex.: grade de Botões, controles do Mixer) usam a mesma regra: o usuário reposiciona; o app nunca move sozinho além do ímã de borda.
- Em telas cheias que já brigam por espaço (Tela do PC, Mouse): comportamento do mic = continua visível; **opção** "Esconder em Mouse e Tela do PC" (padrão ligado) — ver decisões.

### 2.2 Convivência com a bolinha do mic

- Distância mínima centro a centro: 72 px. Se o arraste/reposicionamento violar, o **mascote** é empurrado na vertical (o mic tem prioridade: é controle, o mascote é companhia).
- Balão do mascote abre para o lado oposto à borda e, se o mic estiver perto, para o lado oposto ao mic.
- Mic em "mute" (vermelho) não é tocado pelo mascote; o mascote não fala de microfone (fato de outro componente).
- Quem desliga um não afeta o outro (`prefs.micFab` e `prefs.mascot` independentes).

### 2.3 Toque e ajustes

- **Toque** abre a tela do mascote (nova vista `mascot`, item de Mais; não entra na barra por padrão, mas pode, pela barra configurável).
- Toque longo (500 ms) = menu rápido: *Silenciar hoje*, *Pôr para dormir*, *Esconder*.
- **Esconder:** Ajustes › Mascote › "Mostrar o Lume" (desliga bolha e balões, mantém a tela acessível por Mais; ou "Desligar por completo" que nem monta o SVG). Voltar a mostrar não perde nada: estado fica guardado.
- Teclado/leitor de tela: o botão tem `aria-label` com estado ("Lume, com sede. Abrir"), Enter/Espaço = toque.

---

## 3. Avisos — como o Lume entrega notificações do PC

Hoje: o servidor emite evento (`push_notif` → `FOCUS.should_hold` → push), e o app mostra toast/`homeNotif`, com `go` para abrir a vista. O Lume **não cria um segundo canal**; ele é uma **apresentação alternativa** do mesmo evento.

### 3.1 Roteador único de apresentação (no app)

Um ponto decide, para cada evento recebido, **um** destino:

1. Modo foco da pausa / servidor segurou? → nada de balão (o servidor já segura em memória; o app não reinterpreta).
2. Lume ligado, visível e acordado, e o tipo do evento é "elegível a balão" → **balão** (e o toast **não** é mostrado).
3. Caso contrário → **toast atual**, sem mudança.

A lista de notificações (widget Notificações, Alertas, histórico do `homeNotif`) continua recebendo tudo igual: o balão é só a *primeira apresentação*. Resultado: nenhum aviso aparece duas vezes e nenhum se perde.

### 3.2 Balão

- Curto: título ≤ 28 caracteres + 1 linha de corpo (≤ 70). Texto **vem do evento**, truncado; nunca reescrito com dado novo. Nome do app/origem sempre visível.
- Duração 5 s (8 s se tem ação); toque no balão executa o `go` do evento (mesmo comportamento do toast); deslizar para o lado dispensa.
- Fila: máximo 1 balão visível; os demais entram em "N avisos" (contador na bolha, toque abre a lista que já existe). Agrupa o mesmo app em < 10 s.
- Sem som novo. Vibração só se o app já vibra para o toast.

### 3.3 Foco do Pomodoro e lista de ignorados

- **Foco:** o estado "Concentrado" é o *rosto* de `focus.on`. Durante o foco o Lume não fala nada (inclusive lembretes dele). Na pausa, entrega **um** balão com o resumo que o servidor já gera (evento `focus.summary`: "Durante o foco: 3 do WhatsApp, 1 do Outlook"). Exceções do foco (`focus_except`) continuam as do servidor: se o servidor deixou passar, o Lume mostra balão normal.
- **Ignorados:** a lista de apps ignorados existente (Alertas) é aplicada **antes** do roteador — o que já é filtrado hoje continua filtrado, e o Lume não tem lista própria. (Nome exato do campo/ação: confirmar na etapa 5.)
- **Dormindo/oculto/desligado:** cai no toast atual, nunca em silêncio total para avisos do PC.

### 3.4 Quais tipos viram balão

Elegíveis por padrão: notificações do PC, `export` (arquivo pronto), `alert` (fora do lugar/setup), `pomo.done`, `trello.digest`, `steam.done`, `focus.summary`. Cada tipo tem interruptor em Ajustes › Mascote › Avisos. Alertas "sérios" (ex.: PC sem resposta, falha de backup se existirem) **ficam sempre no toast**, para não parecerem brincadeira.

---

## 4. Tela do mascote

Vista `mascot` (um scroll só, 3 blocos; deitado = 2 colunas como o Mixer v2).

1. **Palco:** o Lume maior (128–160 px), com o estado atual e uma frase de uma linha (da vida do mascote ou, se houver, o último fato do PC com selo da origem).
2. **Cuidar:**
   - **Alimentar** — botão "Petisco" (um único tipo no início; cooldown de 5 min só para não virar spam; resposta: mastiga, comemora curto).
   - **Dar água** — botão "Copo d'água". É também o **registro real** de que *você* bebeu água: o texto diz "Beba com ele". Conta no histórico e zera o lembrete de água.
   - **Pôr para dormir** — escolhe duração: 15 min · 30 min · 1 h · 2 h · "Até amanhã". Padrão configurável (30 min). Dormindo: a bolha **some** do pop-up, os lembretes do Lume pausam, avisos do PC caem no toast. Volta sozinho com animação de acordar (balão "Voltei", 2 s). Toque na bola de "z" na barra de status da tela ou "Acordar agora" interrompe.
3. **Status:** três medidores simples (Energia, Fome, Humor) como **barras/anéis com texto** ("Bem", "Com sede"…), nunca em vermelho de alerta; abaixo, 1 linha "Hoje: N copos, N pausas, N focos".
4. **Histórico do dia:** linha do tempo curta (hora + ícone + texto): petiscos, copos, pausas feitas, focos concluídos, tarefas concluídas, avisos entregues (contagem, sem repetir conteúdo de notificação — **conteúdo de notificação nunca entra no histórico do mascote**). Rolagem dos últimos 7 dias; guarda 14 dias.
5. **Conquistas** (rodapé): selos leves (seção 5.4).

Medidores (modelo): Energia, Fome, Humor ∈ 0–100, mas com **piso confortável de 30** (nunca abaixo → nunca "triste de verdade"). Derivam devagar para o piso se ninguém cuida (fome −1 por 15 min acordado; energia por tempo acordado e por fato de trabalho contínuo; humor = média simples). Ao reabrir o app depois de horas, os valores são **recalculados do relógio** (não há ticks em segundo plano). Dormir recupera energia.

---

## 5. Mecânicas de produtividade

Tudo ligável/desligável por item; frequência configurável; "silenciar hoje" corta tudo menos avisos do PC.

### 5.1 Regras sem culpa

- Nunca morre; nenhum estado persistente "ruim"; sem contagem de dias perdidos; sem sequência (streak) que quebra — só contadores acumulados.
- Mensagem sempre convite, sem comparar ("Que tal um copo d'água?", não "Você esqueceu").
- Um lembrete ignorado **não** sobe de tom. Depois de 2 ignorados seguidos do mesmo tipo no mesmo dia, o Lume **para de insistir naquele tipo até amanhã**.
- Máximo de 6 falas espontâneas por dia no total (configurável), fora avisos do PC.
- Nenhum lembrete em horário de silêncio, em foco, dormindo, ou com "Silenciar hoje".

### 5.2 Lembretes

| Lembrete | Fonte do dado | Regra padrão | Texto (sempre factual ou claramente do Lume) |
|---|---|---|---|
| **Água** | só do aparelho (relógio desde o último "Dar água") | a cada 90 min acordado, dentro do horário ativo | "Hora de um copo d'água?" (voz do Lume, sem número) |
| **Pausa por trabalho contínuo** | **fato do servidor**: minutos contínuos no PC ativo (derivado do tracker de trabalho; ver §6) | 90 min contínuos, 1 aviso e repete a cada +30 min até 2 vezes | "Você está há **1 h 40** seguidos no Photoshop." (número e app reais) |
| **Pomodoro** | evento `pomo.done`/mudança de fase (já existe) | fim de foco e fim de pausa | usa o texto do evento; Lume só "comemora" |
| **Fim do dia** | horário configurado + `work.get` do dia | 18:30, 1×/dia, só se houve trabalho registrado | "Hoje: **4 h 12** de trabalho, 3 tarefas feitas." — somente números lidos de work/tasks |
| **Tarefas** | `tasks.get` (já existe) | fim do dia: "Restam N tarefas." (N real); comemora ao concluir | sem prazo inventado |
| **Trello** | `trello.digest` / grupos overdue/today (já existe, somente leitura) | repete o digest das 9 h e "Tem N cards para hoje" (N real) | cards **nunca** são citados por nome no balão, só contagem + toque abre a lista (evita vazamento por tela visível) |

"Trabalho contínuo": o tracker hoje reconhece apps (Photoshop/Illustrator/Premiere/After Effects/CapCut) por foco. Definição proposta: contínuo = foco em app de trabalho sem lacuna maior que 5 min fora desses apps; **confirmar na etapa 7** se `WorkTracker.poll` tem sinal de ociosidade (teclado/mouse); se não tiver, a regra é só pelo foco de app e o texto diz "no [app]", sem afirmar "sem parar".

### 5.3 Fim do dia sem dados

Sem horas registradas, sem trabalho identificado, ou PC desconectado → **o Lume não faz resumo** (nada de "0 h"). No máximo despede-se sem números ("Bom descanso") se o usuário quiser (opção).

### 5.4 Conquistas leves

Selos cumulativos (sem prazo, sem perda): *Primeiro foco*, *10 focos*, *Primeira pausa feita*, *Dia de 5 copos*, *Dia de caixa zerada* (todas as tarefas feitas), *Mês com o Lume* (contagem de dias de uso). Aparecem na tela do mascote; ao ganhar, Lume comemora e há 1 balão "Novo selo". Sem ranking, sem compartilhamento.

---

## 6. Dados — onde mora cada coisa

### 6.1 Princípio

**O servidor fornece fatos do PC; o aparelho guarda a vida do mascote e as preferências.** Assim o servidor quase não muda e o Lume é um só por pessoa, não por PC.

| Dado | Onde | Por quê |
|---|---|---|
| Estado do Lume (medidores, último petisco/copo, sono até quando), histórico do dia (14 dias), conquistas | **aparelho**, `localStorage` `pcc.mascot` (v:1), atômico com try/catch como o resto do app | é do usuário, não do PC; funciona sem PC |
| Preferências (liga/desliga, horários, frequências, "Economia", posição por orientação) | **aparelho**, `pcc.mascot.cfg` / `pcc.mascot.pos`; **fora de `pk()`** | o hábito segue a pessoa, não o PC |
| Fatos de trabalho (minutos contínuos, app atual), Pomodoro, tarefas, Trello, notificações, `export` etc. | **servidor do PC ativo**, como hoje (sem mudança no contrato exceto o campo da pausa) | só o PC sabe |
| Minutos contínuos de trabalho | **novo campo mínimo no servidor**: algo como `work.stream = {minutes, app, since}` dentro do push/ação de trabalho que já existe, calculado no `work_loop` (etapa 7). O **limiar fica no aparelho** | evita config só pelo painel e evita recálculo no celular |

O mascote **não** é perfil de layout (`LAY`): trocar de layout não muda o Lume nem sua posição. (Decisão 6 abaixo.)

### 6.2 Vários PCs

- Um Lume só, no aparelho; ao trocar de PC (`PCL.setActive` + reload) o Lume continua o mesmo.
- Fatos e avisos vêm **só do PC ativo** (o app só fala com um por vez). Balões de aviso do PC levam **o selo do PC** (cor/ícone do monitor, nome curto), já existentes em `PCL`, para nunca parecer que o aviso é do PC errado.
- "Pausa por trabalho contínuo" só vale para o PC ativo; se a pessoa trabalha em outro PC e o app está no primeiro, o Lume não sabe — e **não finge saber** (limitação conhecida; ver riscos).
- PC desconectado ou sem recurso (`welcome.features` sem `work`/`notif`/`pomo`/`trello`): lembretes que dependem daquele dado ficam **desligados na tela de Ajustes com "indisponível neste PC"** (mesmo padrão do `HOME_NEEDS`); água/sono/petisco continuam, pois não dependem do PC.
- Backup: `backup.py` não toca no aparelho; exportar/importar o Lume é opcional e fica para depois (mesma ideia do código de layout `PCL1.`), nunca com token/PC.

### 6.3 Dado inventado — regras de bolso

- Nenhum número num balão que não esteja no evento/estado recebido.
- Sem relógio do servidor → sem avisos baseados nele.
- Texto do aviso do PC = texto do evento (truncado); o Lume nunca "interpreta" nem resume com palavras novas.
- Falas da vida do mascote são de uma lista fixa pequena (≈ 6 variações por situação, em pt-BR), marcadas com o selo do Lume.

---

## 7. Ajustes (Ajustes › Mascote)

Tudo no aparelho. Padrões em negrito.

- **Mostrar o Lume:** **ligado** · desligado · desligar por completo.
- **Economia (poses estáticas):** **desligado** (ligado automático com "reduzir movimento" do sistema).
- **Esconder em Mouse e Tela do PC:** **ligado**.
- **Avisos do PC pelo Lume:** **ligado**; por tipo: notificações · arquivo pronto · alerta · Pomodoro · Trello · Steam · resumo do foco.
- **Água:** ligado · a cada 60 / **90** / 120 / 180 min.
- **Pausa:** ligado · após 45 / 60 / **90** / 120 min contínuos · repetir a cada 15 / **30** / nunca.
- **Pomodoro:** ligado.
- **Fim do dia:** ligado · horário **18:30** · só se houve trabalho.
- **Tarefas e Trello:** ligado cada um.
- **Horário ativo / silêncio:** ativo **08:00–22:00**; fora disso nada fala (avisos do PC continuam em toast).
- **Máximo de falas espontâneas por dia:** 3 / **6** / 10.
- **Tempo de sono padrão:** 15 / **30** / 60 / 120 min.
- **Silenciar hoje:** interruptor que expira à meia-noite (também no toque longo); mostra "Silenciado até amanhã" na tela do Lume.
- **Zerar o Lume:** apaga estado/histórico/conquistas (confirmação + Desfazer 6 s, como Layouts).

---

## 8. Plano em etapas (commits pequenos)

Cada etapa é testável sozinha e não depende de servidor, exceto a 7.

1. **Arte e estados:** SVG do Lume + CSS dos 8 estados + página de galeria **só de desenvolvimento** (fora do app). Medição de bytes e de quadros.
2. **Bolha:** componente `#lume-fab` com arrasto, ímã de borda, área segura, posição por orientação, convivência com `#mic-fab`, `aria`, liga/desliga. Sem lógica de estado.
3. **Núcleo local:** `makeMascot(store, now)` entre marcadores (como `LAY:begin/end`): medidores, recálculo por relógio, sono, histórico, validação de `pcc.mascot`. Teste `node tests/test_mascot.js` (relógio injetável).
4. **Tela do mascote:** vista `mascot` (palco, cuidar, status, histórico), item em Mais, Ajustes mínimos (mostrar/economia).
5. **Roteador de avisos:** balão vs toast, fila, foco, ignorados, `focus.summary`, selo do PC. Teste do roteador (pura função).
6. **Lembretes locais:** água, Pomodoro, fim do dia, tarefas, Trello; horário ativo, limite diário, "ignorou 2 = para hoje", "Silenciar hoje"; tela completa de Ajustes.
7. **Servidor (única mudança de servidor):** campo `work.stream` (minutos contínuos) no `work_loop`/`work.get`, mais teste Python; lembrete de pausa no app.
8. **Conquistas** e frases (lista fixa), revisão do texto em pt-BR.
9. **Polimento:** medição em 844×390, 390×844 e 1024×768 nos 2 temas (sobreposição com mic, alvos ≥ 44/48 px, sem overflow), bateria/CPU em 10 min parado e com animação, `reduce motion`, atualização do Mapa rápido do `CLAUDE.md` e do índice do `index.html` no mesmo commit.

---

## 9. Riscos

| Risco | Mitigação |
|---|---|
| **Performance:** SVG com muitos nós + animação contínua | orçamento da §1.2, só `transform`/`opacity`, pausa fora de vista, medir CPU na etapa 9 |
| **Bateria** (celular parado com tela acesa) | animação só em primeiro plano, "Economia", opacidade reduzida e laço de 4 s; sem `requestAnimationFrame` contínuo |
| **Distração/irritação** | máximo diário, "ignorou 2 = para hoje", foco calado, horário ativo, "Silenciar hoje" e "Esconder" sempre a 2 toques |
| **Cobrir controles** | ímã de borda, 60% de opacidade ocioso, esconder em Mouse/Tela do PC, usuário no controle da posição |
| **Duplicar aviso** | roteador único (§3.1); lista de notificações inalterada |
| **Dado errado/inventado** | regras de bolso §6.3; sem dado = sem fala |
| **Privacidade** (balão visível por cima do ombro) | conteúdo de notificação não vai para o histórico; Trello só por contagem; opção "balão sem corpo" (só o app) |
| **Dois PCs, app em um só** | limitação assumida e explicada na tela de Ajustes; nunca "finge" |
| **Peso e manutenção do `index.html`** (arquivo único) | módulo com marcadores `/* LUME:begin/end */`, testes em node, índice atualizado a cada commit |
| **Estética "infantil" fora do tom Apple** | poucas cores (creme + acento), sombreamento sutil, movimento contido; revisão visual antes da etapa 4 |
| **Tema Fallout** (verde-terminal) | corpo creme continua legível; bulbo vira o acento; a avaliar na etapa 1 |

---

## 10. O que preciso que você decida

1. **Nome e figura:** "Lume" (criaturinha-lanterna em cápsula, bulbo com a cor do acento) serve? Ou prefere outro nome/forma?
2. **Vida do mascote:** manter fome/sede/sono **simulados** (rotulados como do Lume) ou **só lembretes reais** (sem barras de fome/humor)?
3. **Dormir ao arrastar:** "dormindo" some a bolha por completo (como pedido) — e **os avisos do PC voltam ao toast**. Aceita? Ou preferir silêncio total enquanto dorme?
4. **Posição inicial:** borda esquerda, embaixo (mic fica no canto direito). OK? Ímã de borda (sempre colado a esquerda/direita) vale?
5. **Onde fica a tela:** só em **Mais** (padrão) ou também na barra de baixo por padrão?
6. **Perfis de layout:** o Lume e a posição ficam **fora** do layout (sugerido) ou entram como parte do perfil?
7. **Pausa contínua precisa de um campo novo do servidor** (única mudança de servidor, etapa 7). Pode ou prefere começar sem pausa?
8. **Horários padrão:** ativo 08:00–22:00, fim do dia 18:30, água 90 min, pausa 90 min. Ajustar?
9. **Tipos de aviso no balão** (§3.4) — algum que **nunca** deve virar balão (ex.: Trello, Steam)?
10. **Esconder em Mouse e Tela do PC** por padrão: sim/não?
11. **Conquistas:** querer já na primeira versão ou deixar para depois?
12. **Exportar/importar o Lume** entre aparelhos: agora, depois ou nunca?
