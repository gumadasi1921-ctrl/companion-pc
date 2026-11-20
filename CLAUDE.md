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
- Instalação: windows/install-server.ps1 copia para %USERPROFILE%\companion-pc\server. Testar = rodar o install de novo.
- Redesign: ler só a seção relevante de docs/DESIGN.md antes de mexer no visual.
- Regra: consulte o grafo (graphify query "...") e este mapa ANTES de abrir arquivos; abra só os trechos indicados.
- Grafo só dos .py (.graphifyignore exclui web/, extras/, docs); regenerar: graphify update .
- Ao mudar o index.html, regenere este índice no mesmo commit.

### Índice index.html (linhas)
- <head> script do tema (window.pccTheme) 14–32; <style> 33–820 (tokens Fallout, tokens Moderno, nav nova, Aparência, Início/widgets e Widgets do Início no fim); <nav id="nav"> 1159; const SECTIONS 1397
- Tema: data-theme=fallout|modern, data-variant (só Fallout), data-mode/data-accent/data-scheme (só Moderno); prefs em store theme/variant/mode/accent; applyTheme no JS "Ajustes do celular"
- Início: widgets (catálogo HOME_W: now, tasks, pcquick, cpu, net, mem, work, today, procs, games, notif, quick); layout salvo em store "home" {v:1, phone[], tablet[]} (homeLayout/homeSave; padrão HOME_DEF; limites HOME_CAP 3/12); tela de edição = view "widgets" (wdShow/wdRender)
- Telas (section.tab): inicio 882, more 887, widgets 901, dash 912, games 926, work 946, tasks 961, media 967, mix 987, mouse 995, screen 1041, deck 1056, send 1063, power 1089, sys 1106
- JS: ajustes 1235, formatação 1250, conexão 1292, seções/vistas 1393, abertura 1456, Painel 1469, Tocando 1540, Games 1601, Jogos semana 1624, Trabalho 1659, Tarefas 1713, Início/widgets 1739 (gráficos SVG, HOME_W, homeBuild/homeUpd/homeFetch, Widgets do Início 2037), Atalhos/Sistema 2114, Mouse 2139, Teclado 2225, Botões/deck 2282, foco 2456, microfone/energia 2471, celular parado 2508, Mixer 2570, Tela do PC 2620, Enviar/receber 2721, Alertas 2807, Ajustes celular 2866, loop de tela 2920
