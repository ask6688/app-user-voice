import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createQimaiBrowser, downloadAndroidChannel, downloadIosComments } from "../src/qimai/browser-automation.mjs";
import { argValue, resolveDateRangeFromArgs } from "../src/utils/dates.mjs";
import { acquireRunLock, writeJson } from "../src/utils/fs.mjs";
import { validateQimaiConfig } from "../src/utils/qimai-config.mjs";

const rootDir = fileURLToPath(new URL("../", import.meta.url));
const configArg = argValue(process.argv, "--config");
if (!configArg) {
  console.error("请提供 --config JSON；日常使用请从 Python 主入口读取 YAML 配置。");
  process.exit(2);
}
const configPath = path.resolve(configArg);
const config = JSON.parse(await fs.readFile(configPath, "utf8"));
const { platforms, channels } = validateQimaiConfig(config);
const range = resolveDateRangeFromArgs();
const rawRoot = path.join(rootDir, "data/raw", range.label);
const reportPath = path.join(rootDir, "outputs/qimai", `qimai_fetch_${range.label}.json`);
const results = {
  app: config.app.name,
  dateRange: { start: range.startText, end: range.endText, label: range.label },
  generatedAt: new Date().toISOString(),
  platforms,
  browser: {},
  ios: null,
  android: [],
};
async function safe(fn) {
  try { return await fn(); }
  catch (error) { return { ok: false, reason: `异常: ${error.message}`, crashed: true }; }
}
const releaseLock = await acquireRunLock(path.join(rootDir, "outputs/qimai/.fetch.lock"));
let browserSession;
try {
  browserSession = await createQimaiBrowser(config, rootDir);
  const { context } = browserSession;
  results.browser.userDataDir = browserSession.userDataDir;
  if (platforms.includes("ios")) {
    const iosPage = context.pages().find(tab => tab.url() === config.app.ios.commentUrl) || await context.newPage();
    results.ios = await safe(() => downloadIosComments({ page: iosPage, url: config.app.ios.commentUrl,
      outputDir: path.join(rawRoot, "ios"), range, config }));
    await writeJson(reportPath, results);
  }
  // A failed enabled source stops later browser actions; Android-only is independent.
  if (platforms.includes("android") && (!platforms.includes("ios") || results.ios?.ok)) {
    for (const channel of channels) {
      if (!context.browser()?.isConnected()) break;
      const channelUrl = config.app.android.baseCommentUrl.replace(/\/market\/[^/?#]+/, `/market/${channel.marketCode}`);
      const channelPage = context.pages().find(tab => tab.url() === channelUrl) || await context.newPage();
      const result = await safe(() => downloadAndroidChannel({ page: channelPage,
        baseUrl: config.app.android.baseCommentUrl, outputDir: path.join(rawRoot, "android", channel.key), range, channel, config }));
      results.android.push(result);
      await writeJson(reportPath, results);
      if (!result?.ok) break;
    }
  }
} catch (error) {
  results.error = error.message;
} finally {
  await browserSession?.disconnect().catch(() => {});
  results.generatedAt = new Date().toISOString();
  await writeJson(reportPath, results);
  await releaseLock();
}
console.log(JSON.stringify({ reportPath, rawRoot, results }, null, 2));
const iosOk = !platforms.includes("ios") || Boolean(results.ios?.ok);
const androidOk = !platforms.includes("android") || (results.android.length === channels.length && results.android.every(item => item?.ok));
if (results.error || !iosOk || !androidOk) process.exitCode = 1;
