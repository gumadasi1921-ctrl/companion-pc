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
- <head> teste de compatibilidade (redireciona para legacy.html) 5; script do tema (window.pccTheme) 15–33; <style> 34–875 (tokens Fallout, tokens Moderno, nav nova, Aparência, Início/widgets e Widgets do Início no fim); <nav id="nav"> 1216; const SECTIONS 1473
- Tema: data-theme=fallout|modern, data-variant (só Fallout), data-mode/data-accent/data-scheme (só Moderno); prefs em store theme/variant/mode/accent; applyTheme no JS "Ajustes do celular"
- Início: widgets (catálogo HOME_W: now, tasks, pcquick, cpu, net, mem, work, today, procs, games, notif, quick); layout salvo em store "home" {v:1, phone[], tablet[]} (homeLayout/homeSave; padrão HOME_DEF; limites HOME_CAP 3/12); tela de edição = view "widgets" (wdShow/wdRender)
- Telas (section.tab): inicio 937, more 942, widgets 956, dash 967, games 981, work 1001, tasks 1016, media 1024, mix 1044, mouse 1052, screen 1098, deck 1113, send 1120, power 1146, sys 1163
- JS: ajustes 1295, formatação 1310, conexão 1352 (connect(pairCode): hello ou pair; msg paired guarda o token), seções/vistas 1469, abertura 1532, Painel 1545, Tocando 1616, Games 1677, Jogos semana 1700, Trabalho 1735, Tarefas 1789, Início/widgets 1829 (gráficos SVG, HOME_W, homeBuild/homeUpd/homeFetch, Widgets do Início 2126), Atalhos/Sistema 2204, Mouse 2229, Teclado 2315, Botões/deck 2372, foco 2553, microfone/energia 2569, celular parado 2606, Mixer 2668, Tela do PC 2718, Enviar/receber 2819, Alertas 2905, Ajustes celular 2964, loop de tela 3018
- legacy.html (windows/server/web): painel leve ES5 para iOS 9/Safari 9 (~66 KB; limite 90), aberto pelo teste no topo do index.html. Barra lateral 200px (paisagem) / ícones embaixo (retrato) com Início, Rádio, Botões, Jogos, Trabalho, Sistema, Tarefas, PC (+ página `notif` aberta pelo Início). Início tem bloco de notificações (3) e Tarefas tem Desfazer 4 s; Modo base (store `ldock`, ajuste na aba PC); jogos: games.history/library/launch; trabalho: work.get/week; notificações: notif.sub + evento kind=notif. Mesmas ações do app principal; sem editor. Não usar sintaxe moderna nele (validar com es-check es5).
