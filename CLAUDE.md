## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. When the answer is under a Markdown heading, `graphify section "<heading>"` prints that section's text, so the .md need not be opened. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.

## Mapa rápido
- companion_server.py: HTTP+WebSocket porta 8770. ACTIONS = lista fechada de ações do celular (send("nome",{...}) no JS); QUIET_ACTIONS = não vão para o log; PANEL_OPS + io_call = operações do painel PC (tray) na fila de disco IO_POOL; push_now/push_* = estado para os celulares; config.json tem o token (nunca imprimir).
- tray.py: painel tkinter (ttk.Notebook: Status, Trabalho, Configurações; padrão _build_work / self.app.ops[...]).
- work.py: tempo no Photoshop/Premiere/After por cliente/job (WorkTracker; JSON via _load_json/_save_json).
- tasks.py: TaskList (tasks.json ao lado do config.json; por id; ops tasks_* em PANEL_OPS via io_call; celular só tasks.get/tasks.toggle, ambas em QUIET_ACTIONS). Painel: _build_tasks/_t_* no tray.py. Celular: seção `tasks` (t-tasks, loadTasks/renderTasks).
- extras.py: deck, área de transferência, arquivos, tela. games.py: jogos. pc_info.py: info do PC. watchers.py: observadores.
- web/index.html (windows/server/web): app do celular (PWA) em 1 arquivo; SECTIONS = seções da barra de baixo + sub-abas; store = localStorage prefixo "pcc.".
- pair.py: pareamento por código de 6 dígitos (Pairing: new/close/state/check; 120 s, uso único, 5 erros/IP/min → 60 s, 20 erros/código). Servidor: msg {t:pair,code} antes do hello em session() → paired{token}+welcome; erros 4002 (código) e 4003 (bloqueio). Painel: pair_* em PANEL_OPS, rótulo no Status (tray.py _pair_refresh), código só vale com o painel aberto. Testes: py -3 tests/test_pair.py.
- Instalação: windows/install-server.ps1 copia para %USERPROFILE%\companion-pc\server. Testar = rodar o install de novo.
- Redesign: ler só a seção relevante de docs/DESIGN.md antes de mexer no visual.
- Regra: consulte o grafo (graphify query "...") e este mapa ANTES de abrir arquivos; abra só os trechos indicados.
- Grafo só dos .py (.graphifyignore exclui web/, extras/, docs); regenerar: graphify update .
- Ao mudar o index.html, regenere este índice no mesmo commit.

### Índice index.html (linhas)
- <head> teste de compatibilidade (redireciona para legacy.html) 5; script do tema (window.pccTheme) 15–33; <style> 34–821 (tokens Fallout, tokens Moderno, nav nova, Aparência, Início/widgets e Widgets do Início no fim); <nav id="nav"> 1160; const SECTIONS 1398
- Tema: data-theme=fallout|modern, data-variant (só Fallout), data-mode/data-accent/data-scheme (só Moderno); prefs em store theme/variant/mode/accent; applyTheme no JS "Ajustes do celular"
- Início: widgets (catálogo HOME_W: now, tasks, pcquick, cpu, net, mem, work, today, procs, games, notif, quick); layout salvo em store "home" {v:1, phone[], tablet[]} (homeLayout/homeSave; padrão HOME_DEF; limites HOME_CAP 3/12); tela de edição = view "widgets" (wdShow/wdRender)
- Telas (section.tab): inicio 883, more 888, widgets 902, dash 913, games 927, work 947, tasks 962, media 968, mix 988, mouse 996, screen 1042, deck 1057, send 1064, power 1090, sys 1107
- JS: ajustes 1237, formatação 1252, conexão 1294 (connect(pairCode): hello ou pair; msg paired guarda o token), seções/vistas 1411, abertura 1474, Painel 1487, Tocando 1558, Games 1619, Jogos semana 1642, Trabalho 1677, Tarefas 1731, Início/widgets 1757 (gráficos SVG, HOME_W, homeBuild/homeUpd/homeFetch, Widgets do Início 2055), Atalhos/Sistema 2132, Mouse 2157, Teclado 2243, Botões/deck 2300, foco 2474, microfone/energia 2489, celular parado 2526, Mixer 2588, Tela do PC 2638, Enviar/receber 2739, Alertas 2825, Ajustes celular 2884, loop de tela 2938
- legacy.html (windows/server/web): painel leve ES5 para iOS 9/Safari 9 (~66 KB; limite 90), aberto pelo teste no topo do index.html. Barra lateral 200px (paisagem) / ícones embaixo (retrato) com Início, Rádio, Botões, Jogos, Trabalho, Sistema, Tarefas, PC (+ página `notif` aberta pelo Início). Início tem bloco de notificações (3) e Tarefas tem Desfazer 4 s; Modo base (store `ldock`, ajuste na aba PC); jogos: games.history/library/launch; trabalho: work.get/week; notificações: notif.sub + evento kind=notif. Mesmas ações do app principal; sem editor. Não usar sintaxe moderna nele (validar com es-check es5).
