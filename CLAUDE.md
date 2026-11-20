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
- <head> teste de compatibilidade (redireciona para legacy.html) 5; script do tema (window.pccTheme) 15–33; <style> 34–821 (tokens Fallout, tokens Moderno, nav nova, Aparência, Início/widgets e Widgets do Início no fim); <nav id="nav"> 1160; const SECTIONS 1398
- Tema: data-theme=fallout|modern, data-variant (só Fallout), data-mode/data-accent/data-scheme (só Moderno); prefs em store theme/variant/mode/accent; applyTheme no JS "Ajustes do celular"
- Início: widgets (catálogo HOME_W: now, tasks, pcquick, cpu, net, mem, work, today, procs, games, notif, quick); layout salvo em store "home" {v:1, phone[], tablet[]} (homeLayout/homeSave; padrão HOME_DEF; limites HOME_CAP 3/12); tela de edição = view "widgets" (wdShow/wdRender)
- Telas (section.tab): inicio 883, more 888, widgets 902, dash 913, games 927, work 947, tasks 962, media 968, mix 988, mouse 996, screen 1042, deck 1057, send 1064, power 1090, sys 1107
- JS: ajustes 1236, formatação 1251, conexão 1293, seções/vistas 1394, abertura 1457, Painel 1470, Tocando 1541, Games 1602, Jogos semana 1625, Trabalho 1660, Tarefas 1714, Início/widgets 1740 (gráficos SVG, HOME_W, homeBuild/homeUpd/homeFetch, Widgets do Início 2038), Atalhos/Sistema 2115, Mouse 2140, Teclado 2226, Botões/deck 2283, foco 2457, microfone/energia 2472, celular parado 2509, Mixer 2571, Tela do PC 2621, Enviar/receber 2722, Alertas 2808, Ajustes celular 2867, loop de tela 2921
- legacy.html (windows/server/web): painel leve ES5 para iOS 9/Safari 9 (abas Início, Rádio, Botões, PC), aberto pelo teste no topo do index.html; mesmas ações do app principal; sem editor. Não usar sintaxe moderna nele (validar com es-check es5).
