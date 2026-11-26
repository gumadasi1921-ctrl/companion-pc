// Ícone do Dota: o path inline do index.html é o de web/icons/dota.svg, com currentColor e sem restos do Illustrator.
// Rodar: node tests/test_dota_icon.js
const fs = require("fs"), path = require("path");
const web = path.join(__dirname, "..", "windows", "server", "web");
const svg = fs.readFileSync(path.join(web, "icons", "dota.svg"), "utf8"), html = fs.readFileSync(path.join(web, "index.html"), "utf8");
let bad = 0;
const ok = (n, c) => { console.log(c ? "OK   " : "FALHA", n); if (!c) bad++; };
const d = /<path d="([^"]+)"/.exec(svg)?.[1];
ok("arquivo tem 1 path, viewBox original e currentColor", !!d && (svg.match(/<path/g) || []).length === 1 && svg.includes('viewBox="0 0 4796 4793.7"') && svg.includes('fill="currentColor"'));
ok("sem comentário do Illustrator nem id Camada_1", !/Illustrator|Camada_1|<!--/.test(svg));
ok("index.html tem o símbolo ic-dota com o mesmo path e currentColor", html.includes('<symbol id="ic-dota"') && html.includes(`stroke="none" d="${d}"`) && html.includes('fill="currentColor" stroke="none"'));
ok("NAVM usa o ícone dota na aba Dota", /dota: \["Dota", "Dota", "dota", "dota"\]/.test(html));
ok("outros ícones intactos (Jogos continua gamepad-2)", /games: \["Jogos", "Jogos", "gamepad-2", "games"\]/.test(html));
process.exit(bad ? 1 : 0);
