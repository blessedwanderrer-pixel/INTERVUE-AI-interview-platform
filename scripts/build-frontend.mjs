import { copyFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "dist");
const files = ["index.html", "app.js", "styles.css", "phase2.css"];
const apiUrl = (process.env.INTERVUE_API_URL || "").trim().replace(/\/$/, "");

if (apiUrl && !/^https:\/\/[a-z0-9.-]+(?::\d+)?\/api$/i.test(apiUrl)) {
  throw new Error("INTERVUE_API_URL must be an HTTPS URL ending in /api.");
}

await mkdir(output, { recursive: true });
await Promise.all(files.map((file) => copyFile(resolve(root, file), resolve(output, file))));
await writeFile(
  resolve(output, "runtime-config.js"),
  `window.INTERVUE_CONFIG=${JSON.stringify({ apiUrl })};\n`,
  "utf8",
);

console.log(`Built INTERVUE frontend (${apiUrl ? "production API configured" : "API configuration pending"}).`);

