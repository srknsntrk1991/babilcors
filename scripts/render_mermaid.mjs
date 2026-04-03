import { execFileSync } from "node:child_process";
import { mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

const inDir = join(process.cwd(), "docs", "diagrams");
const outDir = join(process.cwd(), "docs", "diagrams", "out");
mkdirSync(outDir, { recursive: true });

const files = readdirSync(inDir).filter((f) => f.endsWith(".mmd"));
for (const f of files) {
  const input = join(inDir, f);
  const base = f.replace(/\.mmd$/, "");
  const output = join(outDir, `${base}.png`);
  execFileSync("npx", ["-y", "@mermaid-js/mermaid-cli", "-i", input, "-o", output], {
    stdio: "inherit",
  });
}

