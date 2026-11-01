// packages/api-client/scripts/gen.mjs
//
// Generates src/schema.d.ts from contracts/openapi.json with openapi-typescript. With --check it writes nothing and
// exits 1 when the committed file differs from what the contract generates (line endings ignored, since a Windows
// checkout may hold the file with CRLF). `pnpm --filter @khadbot/api-client gen` and `... check` run it.

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = resolve(here, "..");
const contract = resolve(pkg, "../../contracts/openapi.json");
const target = resolve(pkg, "src/schema.d.ts");
const require = createRequire(import.meta.url);
const cli = join(dirname(require.resolve("openapi-typescript/package.json")), "bin/cli.js");

function generate(output) {
  execFileSync(process.execPath, [cli, contract, "--output", output], { stdio: ["ignore", "ignore", "inherit"] });
}

const normalize = (text) => text.replace(/\r\n/g, "\n");

if (process.argv.includes("--check")) {
  const dir = mkdtempSync(join(tmpdir(), "khadbot-api-client-"));
  try {
    const fresh = join(dir, "schema.d.ts");
    generate(fresh);
    let committed = "";
    try {
      committed = readFileSync(target, "utf8");
    } catch {
      committed = "";
    }
    if (normalize(committed) !== normalize(readFileSync(fresh, "utf8"))) {
      console.error(
        "packages/api-client/src/schema.d.ts is out of date with contracts/openapi.json. Run `mise run gen:types` " +
          "(after `mise run gen:contracts` if the API changed) and commit the result.",
      );
      process.exit(1);
    }
    console.log("api-client: generated types match contracts/openapi.json");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
} else {
  generate(target);
  console.log("api-client: wrote src/schema.d.ts");
}
