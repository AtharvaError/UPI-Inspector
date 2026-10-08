// Generates both apps' themes from tokens.json.
//   node packages/design-tokens/build.mjs
// Outputs:
//   apps/dashboard/src/theme/tokens.css   CSS custom properties
//   apps/mobile/theme/tokens.ts           a typed object for React Native
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..");
const t = JSON.parse(readFileSync(resolve(here, "tokens.json"), "utf8"));
const kebab = (s) => s.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();

const lines = [":root {"];
for (const [k, v] of Object.entries(t.color)) lines.push(`  --c-${kebab(k)}: ${v};`);
for (const [k, v] of Object.entries(t.radius)) lines.push(`  --r-${k}: ${v}px;`);
t.space.forEach((v, i) => lines.push(`  --s-${i + 1}: ${v}px;`));
for (const [k, v] of Object.entries(t.shadow)) lines.push(`  --shadow-${k}: ${v};`);
for (const [k, v] of Object.entries(t.type)) {
  lines.push(`  --t-${kebab(k)}-size: ${v.size}px;`, `  --t-${kebab(k)}-line: ${v.line}px;`, `  --t-${kebab(k)}-weight: ${v.weight};`);
}
lines.push(`  --m-fast: ${t.motion.fast}ms;`, `  --m-base: ${t.motion.base}ms;`, `  --m-slow: ${t.motion.slow}ms;`);
lines.push(`  --m-ease: ${t.motion.easingStandard};`, `  --m-ease-emph: ${t.motion.easingEmphasised};`);
lines.push("}", "");
const css = "/* Generated from packages/design-tokens/tokens.json. Do not edit. */\n" + lines.join("\n");
const cssOut = resolve(root, "apps/dashboard/src/theme/tokens.css");
mkdirSync(dirname(cssOut), { recursive: true });
writeFileSync(cssOut, css);

const ts =
  "// Generated from packages/design-tokens/tokens.json. Do not edit.\n" +
  `export const tokens = ${JSON.stringify(t, (k, v) => (k === "$comment" ? undefined : v), 2)} as const;\n` +
  "export type Tokens = typeof tokens;\n";
const tsOut = resolve(root, "apps/mobile/theme/tokens.ts");
mkdirSync(dirname(tsOut), { recursive: true });
writeFileSync(tsOut, ts);
console.log("tokens ->", cssOut.replace(root, "."), "and", tsOut.replace(root, "."));
