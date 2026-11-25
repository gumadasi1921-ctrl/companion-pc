// Galeria do Lume (SO DESENVOLVIMENTO, fora do app): gera tests/lume_gallery.html com os 8 estados da skin, claro/escuro.
// Rodar: node tests/lume_gallery.js   (abre tests/lume_gallery.html no navegador). Também mede o peso da skin.
const fs = require("fs"), path = require("path"), vm = require("vm");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* LUMESKIN:begin \*\/([\s\S]*?)\/\* LUMESKIN:end \*\//);
if (!m) { console.log("FALHA bloco LUMESKIN não encontrado"); process.exit(1); }
const sb = {}; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.out={LUME_STATES,LUME_SKINS,lumeSkinCss,lumeSvg};", sb);
const { LUME_STATES, LUME_SKINS, lumeSkinCss, lumeSvg } = sb.out;
const out = [];
for (const sk of Object.values(LUME_SKINS)) {
  console.log(`skin ${sk.id}: svg+defs ${sk.art.length + sk.defs.length} B, css ${lumeSkinCss(sk).length} B`);
  out.push(`<style>${lumeSkinCss(sk)}</style><svg width="0" height="0" style="position:absolute"><defs>${sk.defs}</defs></svg>` +
    `<h2>${sk.name}</h2><div class="g">${LUME_STATES.map((s) => `<figure><div class="c">${lumeSvg(sk).replace('data-st="happy"', `data-st="${s}"`)}</div><figcaption>${s}</figcaption></figure>`).join("")}</div>`);
}
fs.writeFileSync(path.join(__dirname, "lume_gallery.html"), `<!doctype html><meta charset=utf-8><title>Galeria Lume</title><style>
body{font:14px system-ui;margin:16px;background:#101114;color:#eee}:root{--accent:#4da3ff}.g{display:grid;grid-template-columns:repeat(4,150px);gap:12px}figure{margin:0;text-align:center}
.c{width:150px;height:150px;background:#1b1d22;border-radius:16px}.light .c{background:#f2f2f4}.light{background:#fff;color:#111}</style>
<p><button onclick="document.body.classList.toggle('light')">claro/escuro</button> <button onclick="document.querySelectorAll('.lm').forEach(e=>e.classList.toggle('still'))">economia</button></p>${out.join("")}`);
