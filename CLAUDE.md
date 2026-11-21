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
- Painel web (substitui o tkinter, que fica de reserva): config.json tem panel_token (separado do token, nunca no QR/log). companion_server.py: session() trata hello{panel} (loopback E panel_token; senão 4001) → panel_session(); msg {t:pop,a,p,id}; PANEL_WS/PANEL_ASYNC = operações aceitas (tasks_*, work_*, config_*, pair_new/state, panel_status, pick_folder), todas em QUIET_ACTIONS; conexão normal mandando pop → recusada (o celular pelo cabo também é loopback: só o panel_token vale). Código de pareamento: PAIR.close() quando o último painel web desconecta. tray.py: open_web_panel (Edge --app, ops['panel_url']; sem Edge → tkinter). tasks.restore(id,text,done,pos). Testes: py -3 tests/test_panel_auth.py (sobe cópia em outra porta) e tests/test_tasks_restore.py.
- pair.py: pareamento por código de 6 dígitos (Pairing: new/close/state/check; 120 s, uso único, 5 erros/IP/min → 60 s, 20 erros/código). Servidor: msg {t:pair,code} antes do hello em session() → paired{token}+welcome; erros 4002 (código) e 4003 (bloqueio). Painel: pair_* em PANEL_OPS, rótulo no Status (tray.py _pair_refresh), código só vale com o painel aberto. Testes: py -3 tests/test_pair.py.
- Instalação: windows/install-server.ps1 copia para %USERPROFILE%\companion-pc\server. Testar = rodar o install de novo.
- Redesign: ler só a seção relevante de docs/DESIGN.md antes de mexer no visual.
- Regra: consulte o grafo (graphify query "...") e este mapa ANTES de abrir arquivos; abra só os trechos indicados.
- Grafo só dos .py (.graphifyignore exclui web/, extras/, docs); regenerar: graphify update .
- Ao mudar o index.html, regenere este índice no mesmo commit.

### Índice index.html (linhas)
- <head> teste de compatibilidade (redireciona para legacy.html) 5; script do tema (window.pccTheme) e flag PCC_PANEL 15–36; <style> 37–961 (tokens Fallout, tokens Moderno, nav nova, Aparência, Início/widgets e Widgets do Início no fim); <nav id="nav"> 1303; const SECTIONS 1560
- Tema: data-theme=fallout|modern, data-variant (só Fallout), data-mode/data-accent/data-scheme (só Moderno); prefs em store theme/variant/mode/accent; applyTheme no JS "Ajustes do celular"
- Início: widgets (catálogo HOME_W: now, tasks, pcquick, cpu, net, mem, work, today, procs, games, notif, quick); layout salvo em store "home" {v:1, phone[], tablet[]} (homeLayout/homeSave; padrão HOME_DEF; limites HOME_CAP 3/12); tela de edição = view "widgets" (wdShow/wdRender)
- Telas (section.tab): inicio 1023, more 1028, widgets 1042, dash 1053, games 1067, work 1087, tasks 1102, media 1110, mix 1130, mouse 1138, screen 1184, deck 1199, send 1206, power 1232, sys 1250
- JS: ajustes 1382, formatação 1397, conexão 1439 (connect(pairCode): hello ou pair; msg paired guarda o token), seções/vistas 1556, abertura 1619, Painel 1632, Tocando 1703, Games 1764, Jogos semana 1787, Trabalho 1822, Tarefas 1876, Início/widgets 1916 (gráficos SVG, HOME_W, homeBuild/homeUpd/homeFetch, Widgets do Início 2213), Atalhos/Sistema 2291, Mouse 2323, Teclado 2409, Botões/deck 2466, foco 2647, microfone/energia 2663, celular parado 2700, Mixer 2762, Tela do PC 2812, Enviar/receber 2913, Alertas 2999, Ajustes celular 3058, loop de tela 3112
- Modo painel (janela do PC, #panel=<panel_token>; html.panel-mode esconde o app do celular, que não conecta): bloco no fim do index.html, 3155–3590 (HTML #panel 3158, CSS #panel-css, script: conexão hello{panel}+pop 3300, call 3330, seções statusView 3364, tasksView 3382, workView 3444, phoneView 3494, settingsView 3527). Só 5 seções: Status, Tarefas, Trabalho, Celular (QR + código de pareamento), Ajustes.
- legacy.html (windows/server/web): painel leve ES5 para iOS 9/Safari 9 (~66 KB; limite 90), aberto pelo teste no topo do index.html. Barra lateral 200px (paisagem) / ícones embaixo (retrato) com Início, Rádio, Botões, Jogos, Trabalho, Sistema, Tarefas, PC (+ página `notif` aberta pelo Início). Início tem bloco de notificações (3) e Tarefas tem Desfazer 4 s; Modo base (store `ldock`, ajuste na aba PC); jogos: games.history/library/launch; trabalho: work.get/week; notificações: notif.sub + evento kind=notif. Mesmas ações do app principal; sem editor. Não usar sintaxe moderna nele (validar com es-check es5).
