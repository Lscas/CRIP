// Render the top reviewer view of every readable workbook sheet for visual QA.
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [moduleRoot, inputPath, outputDir] = process.argv.slice(2);
if (!moduleRoot || !inputPath || !outputDir) {
  throw new Error("Usage: render_readable_export.mjs <node_modules> <input.xlsx> <output-dir>");
}
const moduleUrl = pathToFileURL(path.join(moduleRoot, "@oai", "artifact-tool", "dist", "artifact_tool.mjs")).href;
const { FileBlob, SpreadsheetFile } = await import(moduleUrl);
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
await fs.mkdir(outputDir, { recursive: true });

const views = [
  ["Summary", "A1:B17"],
  ["Materials & Equipment", "A1:O12"],
  ["Inspections & Tests", "A1:O12"],
];
for (const [sheetName, range] of views) {
  const region = await workbook.inspect({ kind: "region", sheetId: sheetName, range, maxChars: 1600 });
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  const fileName = sheetName.toLowerCase().replaceAll(" & ", "-").replaceAll(" ", "-") + ".png";
  await fs.writeFile(path.join(outputDir, fileName), new Uint8Array(await preview.arrayBuffer()));
  console.log(JSON.stringify({ sheetName, range, preview: fileName, inspection: region?.ndjson ?? region }));
}
