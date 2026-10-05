import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createQimaiBrowser } from "../src/qimai/browser-automation.mjs";
import { acquireRunLock } from "../src/utils/fs.mjs";

const root = await fs.mkdtemp(path.join(os.tmpdir(), "qimai-browser-reuse-test-"));
const config = { qimai: { userDataDir: ".browser/qimai-profile", headless: true } };
let session;
async function targets() {
  const client = await session.context.browser().newBrowserCDPSession();
  try {
    const { targetInfos } = await client.send("Target.getTargets");
    return targetInfos.filter(target => target.type === "page").map(target => target.targetId).sort();
  } finally { await client.detach(); }
}
try {
  session = await createQimaiBrowser(config, root);
  const page = await session.context.newPage();
  await page.setContent("<p>Synthetic local fixture</p>");
  const before = await targets();
  const endpoint = await fs.readFile(path.join(session.userDataDir, "DevToolsActivePort"), "utf8");
  await session.disconnect();
  session = await createQimaiBrowser(config, root);
  assert.equal(await fs.readFile(path.join(session.userDataDir, "DevToolsActivePort"), "utf8"), endpoint);
  assert.deepEqual(await targets(), before);
  const release = await acquireRunLock(path.join(root, "outputs/.fetch.lock"));
  try {
    await assert.rejects(acquireRunLock(path.join(root, "outputs/.fetch.lock")), /七麦抓取已在运行/);
    assert.deepEqual(await targets(), before);
  } finally { await release(); }
  await assert.rejects(createQimaiBrowser({ qimai: { userDataDir: "../outside" } }, root), /inside this workspace/);
  console.log("browser endpoint and tabs reused; concurrent run and outside profile rejected; no platform requests");
} finally {
  if (session) {
    // This browser was created only inside this test's fresh temporary directory.
    const client = await session.context.browser().newBrowserCDPSession().catch(() => null);
    await client?.send("Browser.close").catch(() => {});
    await session.disconnect().catch(() => {});
  }
  await fs.rm(root, { recursive: true, force: true });
}
