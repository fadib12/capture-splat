// Run from the repository root after installing the exact versions in UPSTREAM.md.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const viewer = dirname(fileURLToPath(import.meta.url));
const root = resolve(viewer, "../../../../../..");
const install = join(root, "external/viewer-build");
const expected = { "@sparkjsdev/spark": "2.4.0", three: "0.180.0", esbuild: "0.25.12" };
for (const [name, version] of Object.entries(expected)) {
  const installed = JSON.parse(readFileSync(join(install, "node_modules", name, "package.json"), "utf8"));
  if (installed.version !== version) throw new Error(`Expected ${name} ${version}; found ${installed.version}`);
}
const require = createRequire(join(install, "package.json"));
require("esbuild").buildSync({
  entryPoints: [join(viewer, "main.source.js")],
  outfile: join(viewer, "main.js"),
  nodePaths: [join(install, "node_modules")],
  bundle: true,
  format: "iife",
  target: "safari17",
  minify: true,
  // Encode shader strings without literal multiline templates so generated
  // upstream shader indentation does not introduce trailing-whitespace diffs.
  supported: { "template-literal": false },
  legalComments: "eof",
  charset: "utf8"
});
