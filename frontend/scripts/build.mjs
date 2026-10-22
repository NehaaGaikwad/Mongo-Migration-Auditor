import { cp, mkdir, rm, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";

execFileSync(process.execPath, ["--check", "app.js"], { stdio: "inherit" });
await rm("dist", { recursive: true, force: true });
await mkdir("dist", { recursive: true });
await cp("index.html", "dist/index.html");
await cp("app.js", "dist/app.js");
await cp("styles.css", "dist/styles.css");
const apiUrl = process.env.AUDITOR_API_URL || "http://localhost:8000";
await writeFile("dist/config.js", `window.AUDITOR_API_URL=${JSON.stringify(apiUrl)};\n`);
