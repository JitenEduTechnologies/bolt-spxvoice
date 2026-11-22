// Real render regression test for ProviderSelect: bundles the component with esbuild,
// mounts it in headless Chrome, opens the dropdown and checks the DOM. A control bundle that
// reproduces the original defect (SelectLabel outside SelectGroup) must crash, proving
// this test would catch it. Run: `npm run test:provider-select-render` from ui/.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const uiRoot = path.resolve(here, "..");
const require = createRequire(path.join(uiRoot, "package.json"));
const esbuild = require("esbuild");
const { chromium } = require("playwright-core");

const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";

async function bundle(source: string): Promise<string> {
    const r = await esbuild.build({
        stdin: { contents: source, resolveDir: uiRoot, loader: "tsx", sourcefile: "harness.tsx" },
        bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
        alias: { "@": path.join(uiRoot, "src") },
        banner: { js: "var process={env:{NODE_ENV:\"production\"}};" },
        logLevel: "silent",
    });
    return r.outputFiles[0].text;
}

const mount = (jsx: string, imports: string) => `
import { createRoot } from "react-dom/client";
${imports}
(window as any).__changes = [];
const onChange = (v: string) => (window as any).__changes.push(v);
createRoot(document.getElementById("root")!).render(${jsx});
`;

const unavailable = `[
  { id: "groq", state: "unavailable", reason_code: "unsupported_by_voice_runtime", message: "m" },
  { id: "azure", state: "unavailable", reason_code: "unsupported_by_voice_runtime", message: "m" },
]`;

const good = mount(
    `<ProviderSelect service="llm" value="deepseek" onChange={onChange} available={["openai","google","deepseek"]} unavailable={${unavailable}} />`,
    `import { ProviderSelect } from "@/components/ProviderSelect";`,
);
// The original defect: SelectLabel placed directly inside SelectContent.
const broken = mount(
    `<Select value="deepseek" onValueChange={onChange}><SelectTrigger><SelectValue /></SelectTrigger>
       <SelectContent><SelectItem value="deepseek">deepseek</SelectItem><SelectSeparator /><SelectLabel>Not available here</SelectLabel>
       <SelectItem value="unavailable:groq" disabled>groq</SelectItem></SelectContent></Select>`,
    `import { Select, SelectContent, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";`,
);

const browser = await chromium.launch({ executablePath: fs.existsSync(CHROME) ? CHROME : undefined, channel: fs.existsSync(CHROME) ? undefined : "chrome", headless: true });
let passed = 0;
const ok = (name: string) => { passed += 1; console.log(`  ok  ${name}`); };

async function run(code: string) {
    const page = await browser.newPage({ viewport: { width: 390, height: 800 } });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e.message)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await page.setContent('<!doctype html><meta name="viewport" content="width=device-width"><div id="root"></div>');
    await page.addScriptTag({ content: code });
    await page.waitForTimeout(500);
    return { page, errors };
}

try {
    {
        const { page, errors } = await run(await bundle(good));
        const trigger = page.getByRole("combobox");
        await trigger.waitFor({ timeout: 5000 }).catch(async (e: Error) => {
            throw new Error(`trigger did not render; errors=[${errors.join(" | ")}] root=${(await page.innerHTML("#root")).slice(0, 300)}`);
        });
        assert.deepEqual(errors, [], "no render errors on mount: " + errors.join(" | "));
        ok("mounts without any page/console error (390px viewport)");
        assert.match((await trigger.textContent()) ?? "", /deepseek/);
        ok("trigger shows the current provider");

        await trigger.click();
        await page.waitForSelector('[role="option"]', { timeout: 5000 });
        const opts = await page.$$eval('[role="option"]', (els) => els.map((e) => ({
            text: (e.textContent || "").trim(),
            disabled: e.hasAttribute("data-disabled") || e.getAttribute("aria-disabled") === "true",
        })));
        assert.deepEqual(opts.filter((o) => !o.disabled).map((o) => o.text), ["openai", "google", "deepseek"]);
        ok("supported providers are selectable");
        assert.deepEqual(opts.filter((o) => o.disabled).map((o) => o.text).sort(), ["azure", "groq"]);
        ok("unavailable providers are listed disabled");
        const body = (await page.locator('[role="listbox"]').innerText()).replace(/\s+/g, " ");
        assert.match(body, /Not available here/);
        ok("'Not available here' label is rendered");
        assert.equal(await page.locator('[role="group"] >> text=Not available here').count(), 1);
        ok("label lives inside a select group");
        assert.ok((await page.locator('[data-testid="unavailable-option-llm-groq"]').count()) === 1);
        ok("unavailable options carry their test ids");

        await page.locator('[data-testid="unavailable-option-llm-groq"]').click({ force: true }).catch(() => {});
        assert.deepEqual(await page.evaluate(() => (window as any).__changes), []);
        ok("clicking an unavailable option never calls onChange");
        await page.getByRole("option", { name: "openai", exact: true }).click();
        assert.deepEqual(await page.evaluate(() => (window as any).__changes), ["openai"]);
        ok("selecting a supported provider calls onChange once");
        assert.deepEqual(errors, [], "no errors after interaction: " + errors.join(" | "));
        ok("no errors after interaction");
        await page.close();
    }
    {
        const { page, errors } = await run(await bundle(broken));
        const msg = errors.join(" | ");
        assert.match(msg, /SelectLabel|SelectGroup/i, "control must reproduce the original crash, got: " + msg);
        ok("control (SelectLabel outside SelectGroup) crashes - this test would catch the original defect");
        await page.close();
    }
} finally {
    await browser.close();
}
console.log(`${passed} passed`);
