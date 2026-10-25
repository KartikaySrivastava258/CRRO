// Bundles src/main.jsx -> dist/ with esbuild. NODE_PATH may point at a global
// node_modules (offline environments); normal installs resolve ./node_modules.
import * as esbuild from "esbuild";
import { cpSync, mkdirSync, rmSync, statSync, writeFileSync } from "node:fs";

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
mkdirSync("dist/assets", { recursive: true });
cpSync("index.html", "dist/index.html");
cpSync("public", "dist", { recursive: true });
const opts = {
  entryPoints: ["src/main.jsx"], bundle: true, outdir: "dist/assets", entryNames: "app",
  minify: !watch, sourcemap: watch, jsx: "automatic", target: ["es2020"], loader: { ".css": "css" },
  nodePaths: (process.env.NODE_PATH || "").split(":").filter(Boolean),
  define: { "process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production"),
            "__CRRO_API_BASE__": JSON.stringify(process.env.CRRO_API_BASE || "") },
  metafile: true, logLevel: "info",
};
if (watch) { const ctx = await esbuild.context(opts); await ctx.watch(); }
else {
  const r = await esbuild.build(opts);
  const sizes = Object.fromEntries(Object.entries(r.metafile.outputs).map(([k]) => [k, statSync(k).size]));
  writeFileSync("dist/build-report.json", JSON.stringify({ built_at: new Date().toISOString(), sizes }, null, 1));
  console.log("bundle sizes (bytes):", sizes);
}
