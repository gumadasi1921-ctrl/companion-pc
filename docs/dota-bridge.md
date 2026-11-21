# Ponte do Companion PC — contrato v1

Rota local **somente leitura** para o Companion PC (outro sistema no mesmo PC) ler o estado da
partida de Dota. Não muda visual, overlay nem análise pós-partida.

- **URL:** `GET http://127.0.0.1:32123/companion/v1/state`
- **Servidor:** o mesmo receptor do GSI (`gsi/receiver/mod.rs`, axum), que só faz bind em loopback
  (`127.0.0.1`; recusa qualquer outro endereço). Porta `32123` (`config.rs`, `DEFAULT_GSI_PORT`).
- **Resposta:** `200`, `application/json; charset=utf-8`, `Cache-Control: no-store`. Outros métodos
  na rota = `405`.
- **Garantias:** nada é gravado (SQLite aberto com `SQLITE_OPEN_READ_ONLY`); nenhuma chamada à
  internet na requisição (só estado em memória, SQLite e caches já gravados); token do GSI, Steam ID
  e chaves de API nunca entram no JSON (o mapeamento só lê os campos listados abaixo).
- **Código:** `apps/desktop/src-tauri/src/companion_bridge.rs` (testes no mesmo arquivo).

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
    "next_item": { "item": "Blink Dagger", "cost": 2250, "missing_gold": 1350 },
    "timers": { "patch": "7.41", "power_rune_in_s": 80, "bounty_rune_in_s": 80,
                "wisdom_rune_in_s": 20, "neutral_tier": 1, "neutral_next_in_s": 500 }
  },
  "result": { "win": true, "k": 12, "d": 2, "a": 15, "match_id": 8976846659 }
}
```

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
| `live.next_item` | build acima + itens carregados (inventário, mochila, estoque) | 1º item da build após os iniciais que você não carrega; `missing_gold` = custo − ouro (mín. 0) |
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
