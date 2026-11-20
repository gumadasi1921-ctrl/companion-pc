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
- <style> 14–533; <nav id="nav"> 830; const SECTIONS 1057
- Telas (section.tab): dash 595, games 609, work 629, tasks 644, media 650, mix 670, mouse 678, screen 724, deck 739, send 746, power 772, sys 789
- JS: ajustes 896, formatação 911, conexão 953, seções/vistas 1054, abertura 1099, Painel 1112, Tocando 1183, Games 1244, Jogos semana 1266, Trabalho 1301, Tarefas 1355, Atalhos/Sistema 1373, Mouse 1398, Teclado 1484, Botões/deck 1541, foco 1715, microfone/energia 1730, celular parado 1767, Mixer 1829, Tela do PC 1879, Enviar/receber 1980, Alertas 2066, Ajustes celular 2124, loop de tela 2178
