import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { ensureDir } from "../utils/fs.mjs";
import { loadCredentials } from "../utils/credentials.mjs";
import { ensureLoggedInWithCheckpoint } from "../utils/login.mjs";

const CREDS = loadCredentials();
const validatorPath = fileURLToPath(new URL("../../scripts/validate_download.py", import.meta.url));

function normalizeText(text) {
  return (text || "").replace(/\s+/g, "");
}

function commentDateScope(page) {
  const details = page.locator(".comment-details");
  return page.url().includes("/andapp/")
    ? details.locator("..").locator(":scope > .filter-container")
    : details;
}

export async function setCustomDateRange(page, startText, endText) {
  const scope = commentDateScope(page);
  const picker = scope.locator(".ivu-date-picker input");
  const steps = [];
  try {
    if (/当前网络或账号异常|半小时后重试/.test(await page.locator("body").innerText({ timeout: 3000 }))) {
      return { ok: false, steps, reason: "human_needed: 七麦提示网络或账号异常，请停止重试" };
    }
    if (await picker.count() !== 1) throw new Error("评论模块日期框不唯一或不存在");
    const custom = scope.getByText("自定义", { exact: true });
    if (await custom.count()) await custom.first().click({ timeout: 5000 });
    await picker.scrollIntoViewIfNeeded({ timeout: 3000 });
    await picker.fill(startText + " - " + endText, { timeout: 5000 });
    await picker.press("Enter");
    await picker.press("Tab");
    const expected = startText + " - " + endText;
    const value = await picker.inputValue();
    if (value !== expected) throw new Error("日期框未接受所请求范围：" + value);
    // Verify the rendered filter label too, not only the editable input value.
    const expectedLabel = expected.replace(/(\d{4})-(\d{2})-(\d{2})/g, "$1年$2月$3日");
    await scope.getByText(expectedLabel, { exact: true }).first().waitFor({ state: "visible", timeout: 10000 });
    steps.push("评论模块日期已提交并回读：" + expected);
    return { ok: true, confirmed: true, steps, start: startText, end: endText, value };
  } catch (error) {
    const text = await page.locator("body").innerText({ timeout: 2000 }).catch(() => "");
    if (/当前网络或账号异常|半小时后重试/.test(text)) {
      return { ok: false, steps, reason: "human_needed: 七麦提示网络或账号异常，请停止重试" };
    }
    return { ok: false, steps, reason: "date_range_not_applied: " + error.message };
  }
}
export async function triggerExportAndSave(page, outputDir, prefix, timeoutMs, options = {}) {
  const label = prefix.startsWith("IOS_") ? "iOS" : prefix.split("_")[0];
  const events = [];
  const log = (message) => {
    events.push({ at: new Date().toISOString(), message });
    console.log(`[${label}] ${message}`);
  };
  await ensureDir(outputDir);
  const attemptDir = await fs.mkdtemp(path.join(outputDir, ".export-"));
  const client = await page.context().newCDPSession(page);
  let task = null;
  let armed = false;
  let confirmClicked = false;
  let dialogError = null;
  let disconnected = false;
  const onClose = () => { disconnected = true; log("browser context closed"); };
  let result;
  let lastProgressAt = Date.now();
  const { frameTree } = await client.send("Page.getFrameTree");
  const onBegin = (event) => {
    if (!armed || task || event.frameId !== frameTree.frame.id) return;
    task = { guid: event.guid, suggestedFilename: event.suggestedFilename, state: "inProgress", receivedBytes: 0, totalBytes: 0 };
    lastProgressAt = Date.now();
    log(`download event detected: ${event.suggestedFilename}`);
  };
  const onProgress = (event) => {
    if (event.guid !== task?.guid) return;
    if (event.receivedBytes > task.receivedBytes || event.state !== task.state) lastProgressAt = Date.now();
    Object.assign(task, { state: event.state, receivedBytes: event.receivedBytes, totalBytes: event.totalBytes });
  };
  const onConsole = (message) => {
    if (message.text() === "__QIMAI_HUMAN_REQUIRED__") dialogError = "human_needed";
    if (message.text() === "__QIMAI_REPEAT_EXPORT_CONFIRMED__") {
      confirmClicked = true;
      log("repeat-export confirm dialog detected; clicked confirm");
    }
  };
  const onDialog = async (dialog) => {
    if (/已经导出.*再次导出|确定再次导出|重复导出/.test(normalizeText(dialog.message()))) {
      try { await dialog.accept(); confirmClicked = true; log("repeat-export confirm dialog detected; clicked confirm"); }
      catch { dialogError = "repeat_export_confirm_not_handled"; }
    } else {
      await dialog.dismiss().catch(() => {});
    }
  };
  client.on("Browser.downloadWillBegin", onBegin);
  client.on("Browser.downloadProgress", onProgress);
  page.on("console", onConsole);
  page.on("dialog", onDialog);
  page.context().on("close", onClose);
  try {
    // One transport owns this task. GUID filenames prevent stale/collision files
    // from being mistaken for this export, regardless of suggested filename.
    await client.send("Browser.setDownloadBehavior", {
      behavior: "allowAndName", downloadPath: path.resolve(attemptDir), eventsEnabled: true
    });
    armed = true;
    await installRepeatExportObserver(page);
    const inactivityTimeout = options.completionTimeoutMs ?? Math.max(timeoutMs, 180000);
    lastProgressAt = Date.now();
    let clicked = false;
    let sawButton = false;
    let sawBusy = false;
    while (Date.now() - lastProgressAt < inactivityTimeout) {
      if (disconnected && task?.state !== "completed") throw new Error("browser_disconnected");
      if (dialogError) throw new Error(dialogError);
      if (task?.state === "canceled") throw new Error("download_canceled");
      if (task?.state === "completed") {
        if (task.totalBytes > 0 && task.receivedBytes !== task.totalBytes) throw new Error("download_byte_count_mismatch");
        const ext = task.suggestedFilename.match(/\.(xlsx|csv)(?:\s*\(\d+\))?$/i)?.[1]?.toLowerCase();
        if (!ext) throw new Error("unsupported_download_format");
        if (options.range) {
          const dates = [...task.suggestedFilename.matchAll(/20\d{2}[-_]?\d{2}[-_]?\d{2}/g)]
            .map((m) => m[0].replace(/[-_]/g, ""));
          const expected = [options.range.startText, options.range.endText].map((d) => d.replaceAll("-", ""));
          if (dates.length !== 2 || dates.some((d, i) => d !== expected[i])) {
            throw new Error("export_range_mismatch: " + task.suggestedFilename);
          }
        }
        const source = path.join(attemptDir, task.guid);
        if ((await fs.stat(source)).size !== task.receivedBytes) throw new Error("download_byte_count_mismatch");
        const staging = path.join(attemptDir, "validated." + ext);
        await fs.copyFile(source, staging);
        const validation = await validateCompletedDownload(staging, options.range);
        if (!validation.ok) throw new Error("download_validation_failed: " + (validation.details?.error || validation.stderr));
        const target = path.join(outputDir, prefix + "." + ext);
        await fs.rename(staging, target);
        log("download completed; " + ext + " validation passed");
        result = { ok: true, path: target, suggestedFilename: task.suggestedFilename,
          repeatExportConfirmed: confirmClicked, validation: { ...validation.details, path: target }, transport: "browser_guid", downloadTask: task };
        break;
      }
      if (!clicked && !task) {
        const button = options.exportButton
          ? { locator: options.exportButton, text: await options.exportButton.innerText(),
              disabled: await options.exportButton.isDisabled(), inProgress: false }
          : await findExportButton(page);
        if (button) {
          if (!sawButton) log("export button found: " + button.text);
          sawButton = true;
          sawBusy = button.inProgress || button.disabled;
          armed = true;
          if (!sawBusy) {
            await button.locator.scrollIntoViewIfNeeded({ timeout: 3000 });
            await button.locator.click({ timeout: 5000 });
            clicked = true;
            log("clicked export; waiting for download completion");
          }
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!result) throw new Error(task ? "download_timeout" : confirmClicked ? "confirm_clicked_but_no_download" : !sawButton ? "export_button_not_found" : sawBusy ? "export_in_progress_timeout" : "download_timeout");
  } catch (error) {
    result = { ...exportFailure(error.message.split(":")[0], error.message), downloadTask: task };
    if (!disconnected && task?.state === "inProgress") await client.send("Browser.cancelDownload", { guid: task.guid }).catch(() => {});
    if (!page.isClosed()) await page.screenshot({ path: path.join(attemptDir, "failure.png"), timeout: 3000 }).catch(() => {});
  } finally {
    page.off("console", onConsole);
    page.off("dialog", onDialog);
    page.context().off("close", onClose);
    if (!page.isClosed()) await page.evaluate(() => window.__qimaiRepeatExportObserver?.disconnect()).catch(() => {});
    client.off("Browser.downloadWillBegin", onBegin);
    client.off("Browser.downloadProgress", onProgress);
    await client.detach().catch(() => {});
    await fs.writeFile(path.join(attemptDir, "export-state.json"), JSON.stringify({ result, task, events }, null, 2));
  }
  return { ...result, evidenceDir: attemptDir };
}
function exportFailure(stage, reason) {
  return { ok: false, stage, reason: reason.startsWith(stage) ? reason : `${stage}: ${reason}` };
}

async function installRepeatExportObserver(page) {
  await page.evaluate(() => {
    window.__qimaiRepeatExportObserver?.disconnect();
    const marker = "data-qimai-repeat-export-handled";
    document.querySelectorAll(`[${marker}]`).forEach((element) => element.removeAttribute(marker));
    const normalize = (value) => (value || "").replace(/\s+/g, "");
    const visible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const scan = () => {
      const roots = Array.from(document.querySelectorAll(".ivu-poptip-popper, .ivu-modal-confirm, .ivu-modal, .el-message-box, .ant-modal, [role='dialog']"));
      for (const root of roots) {
        if (root.hasAttribute(marker) || !visible(root)) continue;
        const text = normalize(root.textContent);
        if (/当前网络或账号异常|半小时后重试|滑块|验证码|二次验证/.test(text)) {
          console.log("__QIMAI_HUMAN_REQUIRED__");
          return;
        }
        if (!/已经导出.*再次导出|确定再次导出|重复导出/.test(text)) continue;
        const buttons = Array.from(root.querySelectorAll("button, a, [role='button']"));
        const confirm = buttons.find((button) => visible(button) && normalize(button.textContent) === "确定");
        if (!confirm) continue;
        root.setAttribute(marker, "true");
        console.log("__QIMAI_REPEAT_EXPORT_CONFIRMED__");
        confirm.click();
        return true;
      }
      return false;
    };
    scan();
    const observer = new MutationObserver(scan);
    observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
    window.__qimaiRepeatExportObserver = observer;
  });
}

async function findExportButton(page) {
  const selectors = [
    ".export-data",
    "button:has-text('导出数据')",
    "a:has-text('导出数据')",
    "button:has-text('导出中')",
    "a:has-text('导出中')"
  ];
  for (const selector of selectors) {
    const matches = page.locator(selector);
    const count = await matches.count().catch(() => 0);
    for (let index = 0; index < count; index += 1) {
      const locator = matches.nth(index);
      if (!await locator.isVisible({ timeout: 300 }).catch(() => false)) continue;
      const text = normalizeText(await locator.innerText().catch(() => ""));
      const disabled = await locator.isDisabled().catch(() => false);
      return { locator, text, disabled, inProgress: /导出中|生成中|处理中/.test(text) };
    }
  }
  return null;
}


export async function validateCompletedDownload(filePath, range) {
  const python = process.env.PYTHON || "python3";
  return new Promise((resolve) => {
    const args = [validatorPath, filePath];
    if (range) args.push("--start-date", range.startText, "--end-date", range.endText);
    const child = spawn(python, args, {
      cwd: process.cwd(),
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => resolve({ ok: false, stderr: error.message }));
    child.on("close", (code) => {
      let details = null;
      try { details = JSON.parse(stdout.trim()); } catch { /* preserve raw output below */ }
      resolve({ ok: code === 0 && Boolean(details?.ok), details, stderr: stderr.trim() });
    });
  });
}


async function currentDateRangeConfirmed(page, range) {
  const value = await commentDateScope(page).locator(".ivu-date-picker input")
    .first().inputValue().catch(() => "");
  const normalized = normalizeText(value);
  return normalized === `${range.startText}-${range.endText}`;
}

async function pageShowsNoCommentData(page) {
  const text = normalizeText(await page.locator(".comment-details")
    .first().innerText({ timeout: 3000 }).catch(() => ""));
  return text.includes("暂无数据") || /共0条/.test(text) || /0条结果/.test(text);
}

async function writeEmptyAndroidCsv(outputDir, channel, range) {
  const headers = channel.key === "meizu"
    ? ["评级", "评价", "发表时间"]
    : ["评论时间", "评论人", "星级", "内容"];
  const outputPath = path.join(outputDir, `${channel.name}_${range.label}.csv`);
  await fs.writeFile(outputPath, `${headers.join(",")}\n`, "utf8");
  return outputPath;
}

async function preparePersistentProfile(userDataDir) {
  const lockPath = path.join(userDataDir, "SingletonLock");
  const lockTarget = await fs.readlink(lockPath).catch(() => "");
  const pid = Number(lockTarget.match(/-(\d+)$/)?.[1] || 0);
  let ownerAlive = false;
  if (pid > 0) {
    try {
      process.kill(pid, 0);
      ownerAlive = true;
    } catch {
      ownerAlive = false;
    }
  }
  if (ownerAlive) {
    throw new Error(`七麦固定浏览器 Profile 正被 PID ${pid} 使用，拒绝并发启动`);
  }
  if (lockTarget) {
    for (const name of ["SingletonCookie", "SingletonLock", "SingletonSocket"]) {
      await fs.unlink(path.join(userDataDir, name)).catch(() => {});
    }
    console.log("[browser] removed stale persistent-profile locks");
  }

  const preferencesPath = path.join(userDataDir, "Default", "Preferences");
  try {
    const preferences = JSON.parse(await fs.readFile(preferencesPath, "utf8"));
    if (preferences.profile?.exit_type === "Crashed") {
      preferences.profile.exit_type = "Normal";
      preferences.profile.exited_cleanly = true;
      await fs.writeFile(preferencesPath, JSON.stringify(preferences));
      console.log("[browser] cleared stale crash-restore state");
    }
  } catch {
    // A new profile may not have Preferences yet.
  }
}

export async function createQimaiBrowser(config, rootDir) {
  rootDir = await fs.realpath(rootDir);
  const userDataDir = path.resolve(rootDir, config.qimai.userDataDir || ".browser/qimai-profile");
  const relativeProfile = path.relative(path.resolve(rootDir), userDataDir);
  if (!relativeProfile || relativeProfile.startsWith("..") || path.isAbsolute(relativeProfile)) {
    throw new Error("qimai.userDataDir must be a dedicated directory inside this workspace");
  }
  for (let directory = userDataDir; directory !== rootDir; directory = path.dirname(directory)) {
    const stat = await fs.lstat(directory).catch(error => {
      if (error.code === "ENOENT") return null;
      throw error;
    });
    if (stat?.isSymbolicLink()) throw new Error("qimai.userDataDir must not contain symlinks");
  }
  await ensureDir(userDataDir);
  const portFile = path.join(userDataDir, "DevToolsActivePort");
  async function connect() {
    const [port, endpoint] = (await fs.readFile(portFile, "utf8")).trim().split("\n");
    if (!/^\d+$/.test(port) || !endpoint.startsWith("/devtools/browser/")) throw new Error("Invalid browser endpoint");
    return chromium.connectOverCDP(`ws://127.0.0.1:${port}${endpoint}`, { timeout: 15000, noDefaults: true });
  }
  let browser = await connect().catch((error) => {
    if (error.code === 'ENOENT' || /ECONNREFUSED/.test(error.message)) return null;
    const stage = /setDownloadBehavior|Browser context management is not supported/.test(error.message)
      ? 'download_behavior_unsupported' : 'browser_attach_failed';
    throw new Error(`${stage}: ${error.message}`, { cause: error });
  });
  if (!browser) {
    await preparePersistentProfile(userDataDir);
    await fs.unlink(portFile).catch(() => {});
    const executable = config.qimai.executablePath || chromium.executablePath();
    await fs.access(executable);
    const browserLog = await fs.open(path.join(userDataDir, "browser.log"), "a", 0o600);
    const child = spawn(executable, [
      `--user-data-dir=${userDataDir}`, "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
      "--disable-blink-features=AutomationControlled", "--no-first-run", "--enable-logging=stderr",
      ...(String(config.qimai.headless ?? false) === "true" ? ["--headless=new"] : []),
      "about:blank"
    ], { detached: true, stdio: ["ignore", "ignore", browserLog.fd] });
    await browserLog.close();
    let launchError;
    child.on("error", (error) => { launchError = error; });
    child.unref();
    const deadline = Date.now() + 15000;
    while (!browser && Date.now() < deadline && !launchError) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      browser = await connect().catch(() => null);
    }
    if (!browser) throw launchError || new Error("浏览器连接超时；保留窗口，不自动重启");
  }
  const context = browser.contexts()[0];
  const page = context.pages()[0] || await context.newPage();
  // For connectOverCDP, browser.close disconnects the client, not Chrome.
  return { context, page, userDataDir, disconnect: () => browser.close() };
}

export async function downloadIosComments({ page, url, outputDir, range, config }) {
  await ensureDir(outputDir);
  if (page.url() !== url) await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});

  const login = await ensureLoggedInWithCheckpoint(page, "qimai", CREDS, { screenshotDir: outputDir });
  if (!login.ok) {
    if (login.reason === "human_needed") {
      return { ok: false, source: "ios", reason: "七麦登录需要人工完成验证码/滑块/二次验证", login };
    }
    await page.screenshot({ path: path.join(outputDir, "qimai_ios_login_blocked.png"), fullPage: true }).catch(() => {});
    return { ok: false, source: "ios", reason: login.reason || "七麦登录兜底失败，无法自动导出", login };
  }

  const dateResult = await setCustomDateRange(page, range.startText, range.endText);
  if (!dateResult.ok) {
    await page.screenshot({ path: path.join(outputDir, "qimai_ios_date_blocked.png"), fullPage: true }).catch(() => {});
    return { ok: false, source: "ios", reason: dateResult.reason, dateResult };
  }

  const download = await triggerExportAndSave(
    page,
    outputDir,
    `IOS_${range.label}`,
    Number(config.qimai.downloadTimeoutMs || 45000),
    { range }
  );
  if (!download.ok) {
    await page.screenshot({ path: path.join(outputDir, "qimai_ios_export_blocked.png"), fullPage: true }).catch(() => {});
    return { ok: false, source: "ios", reason: download.reason, dateResult, download };
  }

  return { ok: true, source: "ios", dateResult, download };
}

export async function downloadAndroidChannel({ page, baseUrl, outputDir, range, channel, config }) {
  await ensureDir(outputDir);
  let url = baseUrl;
  if (channel.marketCode) {
    url = baseUrl.replace(/\/market\/[^/?#]+/, `/market/${channel.marketCode}`);
  }

  if (page.url() !== url) await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});

  const login = await ensureLoggedInWithCheckpoint(page, "qimai", CREDS, { screenshotDir: outputDir });
  if (!login.ok) {
    if (login.reason === "human_needed") {
      return { ok: false, source: "android", channel: channel.name, reason: "七麦登录需要人工完成验证码/滑块/二次验证", login };
    }
    await page.screenshot({ path: path.join(outputDir, `qimai_${channel.key}_login_blocked.png`), fullPage: true }).catch(() => {});
    return { ok: false, source: "android", channel: channel.name, reason: login.reason || "七麦登录兜底失败，无法自动导出", login };
  }

  const marketResult = { ok: page.url().includes(`/market/${channel.marketCode}`), steps: [`渠道页面：${channel.name}`] };
  if (!marketResult.ok) return { ok: false, source: "android", channel: channel.name, reason: "channel_not_confirmed", marketResult };
  const dateResult = await setCustomDateRange(page, range.startText, range.endText);
  if (!dateResult.ok) {
    await page.screenshot({ path: path.join(outputDir, `qimai_${channel.key}_date_blocked.png`), fullPage: true }).catch(() => {});
    return { ok: false, source: "android", channel: channel.name, reason: dateResult.reason, marketResult, dateResult };
  }

  const channelConfirmed = page.url().includes(`/market/${channel.marketCode}`);
  const dateConfirmed = await currentDateRangeConfirmed(page, range);
  const settled = await page.waitForLoadState("networkidle", { timeout: 15000 }).then(() => true, () => false);
  const noData = await pageShowsNoCommentData(page);
  if (noData && !settled) return { ok: false, source: "android", channel: channel.name, reason: "empty_result_not_settled", dateResult, marketResult };
  if (channelConfirmed && dateConfirmed && noData) {
    const outputPath = await writeEmptyAndroidCsv(outputDir, channel, range);
    return {
      ok: true,
      source: "android",
      channel: channel.name,
      channelStatus: "empty",
      count: 0,
      marketResult,
      dateResult: { ...dateResult, confirmed: true },
      download: { ok: true, empty: true, path: outputPath }
    };
  }

  const download = await triggerExportAndSave(
    page,
    outputDir,
    `${channel.name}_${range.label}`,
    Number(config.qimai.downloadTimeoutMs || 45000),
    { range }
  );

  if (!download.ok) {
    await page.screenshot({ path: path.join(outputDir, `qimai_${channel.key}_export_blocked.png`), fullPage: true }).catch(() => {});
    return { ok: false, source: "android", channel: channel.name, reason: download.reason, marketResult, dateResult, download };
  }

  return { ok: true, source: "android", channel: channel.name, channelStatus: "success", marketResult, dateResult: { ...dateResult, confirmed: dateConfirmed }, download };
}

