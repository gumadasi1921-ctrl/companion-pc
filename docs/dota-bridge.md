# Ponte do Companion PC — contrato v1

Rota local **somente leitura** para o Companion PC (outro sistema no mesmo PC) ler o estado da
partida de Dota. Não muda visual, overlay nem análise pós-partida.

- **URL:** `GET http://127.0.0.1:32123/companion/v1/state`
- **Servidor:** o mesmo receptor do GSI (`gsi/receiver/mod.rs`, axum), que só faz bind em loopback
  (`127.0.0.1`; recusa qualquer outro endereço). Porta `32123` (`config.rs`, `DEFAULT_GSI_PORT`).
- **Resposta:** `200`, `application/json; charset=utf-8`, `Cache-Control: no-store`. Outros métodos
  na rota = `405`.
- **Garantias:** a requisição nunca grava (SQLite aberto com `SQLITE_OPEN_READ_ONLY`) nem espera a
  internet (só estado em memória, SQLite e caches já gravados); token do GSI, Steam ID e chaves de
  API nunca entram no JSON nem no log (o mapeamento só lê os campos listados abaixo). Dois efeitos
  acontecem **fora** da requisição, em tarefas separadas: a busca da build ausente e a extração de
  imagem ausente (ver abaixo).
- **Código:** `apps/desktop/src-tauri/src/companion_bridge.rs` (testes no mesmo arquivo e em
  `gsi/receiver/mod.rs`).
- **Mudanças:** só aditivas dentro de `v: 1` (campo novo = `null` quando sem dado).

## Contrato v1

Campos ausentes = `null`. Listas vazias quando não há dados.

```jsonc
{
  "v": 1,
  "updated_at": 1795240000,          // epoch s em que a resposta foi gerada
  "phase": "offline|menu|draft|pregame|game|postgame",
  "lobby": {
    "mmr": { "value": 3400, "estimated": true, "medal": "Lenda 3" },
    "today": { "wins": 2, "losses": 1 },
    "streak": 2,                     // +vitórias / −derrotas seguidas
    "recent": [                      // até 5, mais recente primeiro
      { "match_id": 9098969354, "hero_id": 12, "hero": "Phantom Lancer", "win": true,
        "k": 10, "d": 3, "a": 12, "duration_s": 2400, "ended_at": 1795239038 }
    ],
    "top_heroes": [                  // até 5, por partidas jogadas
      { "hero_id": 86, "hero": "Rubick", "matches": 120, "winrate": 0.53 }
    ]
  },
  "hero": {
    "hero_id": 74, "hero": "Invoker",
    "position": null,                // 1..5 | null
    "position_source": "historico",  // "gsi" | "historico" | null
    "mine": { "matches": 40, "winrate": 0.55, "last_played": 1795000000, "avg_kda": "7.1/4.2/9.8" },
    "build": {
      "source": "STRATZ", "updated_at": 1792007800,
      "items": [ { "stage": "start|early|core|late", "item": "Tango", "item_id": 44, "cost": 90 } ],
      "skills": ["q", "w", "q", "e", "q", "r", "q", "w", "w"]
    }
  },
  "live": {
    "clock_s": 400, "daytime": true, "gold": 900, "gpm": 410, "xpm": 520,
    "k": 3, "d": 1, "a": 7, "last_hits": 55, "denies": 4, "level": 9,
    "items": ["Tango", "Ramo de Ferro"],
    "items_detail": [                // mesmos itens, com id e slot
      { "item": "Tango", "item_id": 44, "slot": 0 },
      { "item": "Ramo de Ferro", "item_id": 16, "slot": 6 }
    ],
    "next_item": { "item": "Blink Dagger", "item_id": 1, "cost": 2250, "missing_gold": 1350 },
    "timers": { "patch": "7.41", "power_rune_in_s": 80, "bounty_rune_in_s": 80,
                "wisdom_rune_in_s": 20, "neutral_tier": 1, "neutral_next_in_s": 500 }
  },
  "result": { "hero_id": 74, "win": true, "k": 12, "d": 2, "a": 15, "match_id": 8976846659 }
}
```

## Imagens da ponte

Mesmo servidor, somente `GET`. Servem as imagens que o DotaCopilot já tem em disco (extraídas do VPK do
Dota); uma que ainda não existe é extraída em segundo plano e a rota responde `404` até lá.

| Rota | Arquivo | Tamanho real |
| --- | --- | --- |
| `/companion/v1/img/hero/{hero_id}?kind=icon` | ícone do herói | 32×32 |
| `/companion/v1/img/hero/{hero_id}?kind=portrait` (padrão sem `kind`) | retrato | 128×72 |
| `/companion/v1/img/hero/{hero_id}?kind=loadout` | render vertical | 448×678 |
| `/companion/v1/img/item/{item_id}` | ícone do item | 88×64 |

`Content-Type: image/png`, `Cache-Control: max-age=86400`. `400` = id que não seja só dígitos ou `kind` fora
da lista; `404` = id desconhecido ou imagem ainda não extraída.

## Busca da build ausente

Quando `hero.build` seria `null` por falta de cache para o herói + posição do histórico, a ponte busca a
build na STRATZ **em segundo plano** (mesma função do overlay do DotaCopilot). A resposta em curso não
espera (`build: null`); a build aparece nas leituras seguintes, em poucos segundos. No máximo 1 busca por
herói + posição, sem repetir a mesma em 10 min (erro incluído).

## Fontes de cada bloco

| Bloco | Fonte | Regra |
| --- | --- | --- |
| `phase` | último payload GSI aceito | sem payload ou mais velho que 35 s (heartbeat 30 s + timeout 5 s) = `offline`; sem `map` = `menu`; `map.game_state`: seleção/estratégia/carregamento = `draft`, `PRE_GAME` = `pregame`, `GAME_IN_PROGRESS` = `game`, `POST_GAME`/`DISCONNECT` = `postgame`; estado desconhecido = `menu` |
| `lobby` | SQLite (`matches`, `mmr_snapshots`/estimativa V2 via `history::current_mmr_with`) | presente sempre que o banco abre; `today` desde a meia-noite local (relógio do SQLite); `estimated` = valor estimado (não manual); `medal` pela tabela `USER_REFERENCE_V1` (`rank-progress.ts`; sem Imortal) |
| `hero.hero_id`, `hero.hero` | GSI `hero.id`; nome do cache `opendota.hero_grid.v1` | `null` fora de draft/jogo/pós-jogo ou sem herói escolhido |
| `hero.position` | — | o GSI não tem campo de posição: sempre `null` ao vivo, logo `position_source` nunca é `"gsi"` |
| `hero.position_source` | `matches.role` do herói | `"historico"` = posição mais jogada por você com o herói (empate: a mais recente); sem histórico de posição = `null` e `build` `null` |
| `hero.mine` | `matches` do herói (`hero_key` e `external_hero_<id>`) | `avg_kda` = médias com 1 casa |
| `hero.build.items` | `stratz_build_cache` (herói + posição do histórico) + `stratz.item_catalog.v1` (nomes) + `stratz.match_constants.v4` (custo) | mesma agregação do overlay (`item_build/aggregate.rs`, `starting.rs`); `start` = itens iniciais (repetidos pela quantidade); `early`/`core`/`late` pelo minuto alvo 0–15 / 15–35 / 35+ (mesmas faixas da página do herói, `hero_page.rs`) |
| `hero.build.skills` | `stratz.hero_guide_sequence.v2.<herói>.<posição>` (partida real, `item_build/sequence.rs`) + ordem das habilidades em `stratz.match_constants.v4` | só heróis com exatamente 4 habilidades aprendidas = Q, W, E, R na ordem do jogo; senão `null`. A fonte não guarda talentos, por isso só os níveis 1–9 (exatos) e nunca `"talento"` |
| `live` | GSI (`map`, `player`, `hero`, `items`) | só em `pregame`/`game`; `items` = inventário + mochila (nome PT do catálogo, senão nome interno) |
| `live.items_detail` | GSI `items.slot0`–`slot8`, `neutral0` + `stratz.item_catalog.v1` | `slot` 0–5 = inventário, 6–8 = mochila, **9 = item neutro** (`neutral0`; confirmado pelo dono do projeto); estoque fora; `item_id` = `null` se o catálogo em cache não conhecer o item; slot vazio não aparece |
| `live.next_item` | build acima + itens carregados (inventário, mochila, estoque) | 1º item da build após os iniciais que você não carrega; `missing_gold` = custo − ouro (mín. 0); `item_id` = id desse item na build |
| `result.hero_id` | GSI `hero.id` | `null` se o payload não trouxer herói |
| `live.timers` | ruleset `timer-rules/7.41/rules.json` | ver abaixo; todos `null` em Turbo (sem tempos de Turbo com fonte) |
| `result` | GSI no `postgame` | `win` = `map.win_team` igual ao seu `player.team_name` |

## Timers (tabela única)

Tudo em `apps/desktop/src-tauri/timer-rules/7.41/rules.json` (`"version": "7.41"` = `timers.patch`):
regras `power_rune`, `bounty_rune`, `wisdom_objective` de `periodic` e a lista `neutralTiers`. Fontes e
revisões no `README.md` da mesma pasta.

| Timer | 1º | Intervalo | Estado |
| --- | --- | --- | --- |
| Runa de poder | 6:00 | 2 min | conferir |
| Runa de recompensa | 0:00 | 4 min | conferir |
| Santuário da sabedoria (`wisdom_rune_in_s`) | 7:00 | 7 min | conferir (contagem pode reverter desde 7.41) |
| Neutros Tier 1 / 2 / 3 / 4 / 5 | 0:00 / 15:00 / 25:00 / 35:00 / 60:00 | — | conferir |

O 7.42 saiu em 2026-11-18 e não foi conferido: todas as entradas têm `"conferir": true`.

## Teste manual

1. Abra o app (atalho **Lastwise**).
2. Com o Dota fechado:
   `curl http://127.0.0.1:32123/companion/v1/state` → `"phase":"offline"`, `lobby` preenchido.
3. Com o Dota aberto no menu, o mesmo comando → `"phase":"menu"`.

## Lado do Companion PC (este repositório)

Cópia do contrato do DotaCopilot (fonte: `A:\Sistema dota\docs\companion-bridge.md`). O Companion só lê a
ponte (`windows/server/dota.py`, config `"dota": {"bridge", "auto_open"}`) e repassa o JSON aos aparelhos na
mensagem `{t: "dota", d, auto_open}`, sem calcular nada, com um bloco a mais em `d`:

```jsonc
"companion": { "dota_running": true, "dota_started": 1795280000, "menu_from_process": true }
```

### Única regra derivada: fase "menu" pelo processo do Dota

No menu principal o Dota **não envia GSI** (confirmado em 2026-11-21: com o jogo aberto no menu, a ponte
ficou em `offline`). Por isso, e só nesse caso:

| Processo do Dota (games.py) | Ponte | `phase` da ponte | O app mostra |
| --- | --- | --- | --- |
| qualquer | fora do ar | — (`offline`, `reason` "DotaCopilot fechado") | "Abra o DotaCopilot para ver a partida" |
| fechado | no ar | `offline` | tela offline com os blocos do lobby |
| **aberto** | **no ar** | **`offline`** | **`phase` = `menu`** ("No menu do Dota" + blocos do lobby), `menu_from_process: true` |
| qualquer | no ar | `menu`/`draft`/`pregame`/`game`/`postgame` | a fase da ponte, sem mudança |

Fatos usados: o processo aberto (o `games.py` reconhece o Dota 2 pela Steam, appid 570) e a resposta
`offline` da ponte, que está no ar. Nenhum outro campo é alterado ou criado (`with_process` em `dota.py`).

### Abrir sozinho

Uma vez por abertura do jogo: quando chega um `dota_started` novo (o Dota abriu), com `auto_open` no
`config.json` do PC e a chave "Abrir a tela Dota ao iniciar o jogo" em Ajustes do aparelho, o app vai para a
tela Dota (já na fase menu) com o aviso "Dota aberto" e o botão "Voltar". O último `dota_started` tratado fica
no aparelho (store `dotaSeen`), então recarregar o app não abre de novo.


### Imagens no app: proxy do servidor (`windows/server/dota_img.py`)

O celular nunca fala com a ponte: o servidor do Companion busca na ponte e guarda em disco.

- **Rotas:** `GET /dota/img/hero/<id>?kind=icon|portrait|loadout&k=<chave>` e `GET /dota/img/item/<id>?k=<chave>`
  (porta 8770, a mesma do app). Só ids numéricos (1–10 dígitos, > 0) e kinds da lista chegam à busca; nunca URL
  nem caminho livre. `kind` ausente = `portrait`; em item o `kind` é ignorado.
- **Autenticação:** o `<img>` não manda cabeçalho, então a URL leva `k` = `HMAC-SHA256(token do aparelho,
  "dota-img-v1")[:24]` (hex). O servidor entrega `k` ao app já autenticado no campo `img_key` da mensagem
  `{t:"dota", d, auto_open, img_key}` e da resposta de `dota.get`; o token nunca vai para a URL. Chave ausente, errada ou
  repetida = `403`. (Os arquivos do app em si continuam abertos, como antes: o token só vale no WebSocket.)
- **Busca:** `http://127.0.0.1:32123/companion/v1/img/...` (o endereço vem do mesmo `dota.bridge` do
  `config.json`; só `127.0.0.1`/`localhost`), urllib sem proxy do sistema, timeout 2 s, na fila de disco do servidor.
  Aceita só `image/png` (assinatura PNG, até 1 MB).
- **Cache:** `%USERPROFILE%\companion-pc\server\dota-img\` (`hero-<id>-<kind>.png`, `item-<id>.png`), limite 50 MB:
  passou, apaga as menos usadas recentemente. A instalação não toca nessa pasta.
- **Respostas:** `200` `image/png` com `Cache-Control: max-age=604800`; `404` da ponte = `404` (nada é guardado: a
  imagem pode ser extraída depois); ponte fora do ar, resposta inválida ou erro = `502` (e por 10 s nem tenta de novo);
  id/kind inválido = `400`; caminho fora do formato = `404`; sem chave = `403`. Erros: `Cache-Control: no-store`.
- **Log:** nada de imagem, id, nome de arquivo nem chave.
- **Testes:** `py -3 tests/test_dota_img.py` (unidade com ponte falsa + cópia do servidor em outra porta).

### Tela Dota com imagens (`windows/server/web/index.html`)

Só imagens vindas da ponte (pelo proxy acima); o Companion não busca nada na internet. Sem `img_key`, sem id ou
com a imagem falhando, o app mostra o nome em texto na mesma caixa (campo `null` = item escondido; nada inventado).

| Onde | Imagem (tamanho real → máximo na tela) |
| --- | --- |
| Últimas partidas e Mais jogados | retrato 128×72 → 64×36, `object-fit: cover`; borda verde/vermelha = vitória/derrota (só nas partidas) |
| Faixa do herói (draft/pregame, game, postgame) | loadout 448×678, `object-fit: cover` com `object-position` no topo (rosto), sem esticar: 72×96 (132×176 no fim da partida) |
| Itens (build, inventário, próximo item) | 88×64 → 44×32 (inventário: 44×32; mochila 32×23 a 60% de opacidade; neutro, `slot` 9, num círculo de 40 px ao lado) |

O ícone 32×32 do herói não é usado em nenhuma tela. No máximo ~40 imagens por tela (`DOTA_IMG_MAX`; o resto da build
vira só texto). Todas têm `loading="lazy"`, tamanho reservado (o layout não pula) e esqueleto até carregar.

**Atualização a cada segundo sem recriar `<img>`:** a tela é feita de seções com assinatura (`dotaPlan` → `renderDota`):
o HTML de uma seção só é refeito quando muda o que ela mostra (ids, estágios, slots...). O relógio, os tempos, o anel do ouro,
os números e o check da build (item comprado = check verde; os outros a 40%) mudam no lugar em `dotaTick` (`data-d`).

Teste visual sem o Dota: `py -3 tests/dota_preview.py` sobe uma ponte falsa (fixtures de cada fase, incluindo `game-full` com
slots 0–9 e `game-null` com campos `null`, e PNGs nos tamanhos reais) e uma cópia do servidor em portas de teste.

