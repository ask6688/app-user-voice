import fs from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline/promises";

// Detect the platform's login controls, excluding the reviews being analyzed.
// Captcha, account restrictions and 2FA always require a person.

const HUMAN_SIGNALS = [
  "验证码",
  "滑块",
  "滑动验证",
  "拖动滑块",
  "二次验证",
  "安全验证",
  "短信验证",
  "图形验证",
  "请在手机上确认",
  "行为验证",
  "风险验证",
  "请完成安全",
];

function normalize(text) {
  return (text || "").replace(/\s+/g, "");
}

const QIMAI_REVIEW_TEXT = '.comment-txt, .comment-details .ivu-table-row';

async function bodyText(page, excludeSelector = '') {
  if (excludeSelector) {
    return normalize(await page.locator('body').evaluate((body, excluded) => {
      // Review text describes another app's login, not QiMai's current state.
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      const parts = [];
      while (walker.nextNode()) {
        const parent = walker.currentNode.parentElement;
        if (!parent || parent.closest(excluded) || !parent.getClientRects().length) continue;
        const style = getComputedStyle(parent);
        if (style.visibility === 'hidden' || style.visibility === 'collapse') continue;
        parts.push(walker.currentNode.textContent);
      }
      return parts.join(' ');
    }, excludeSelector));
  }
  return normalize(await page.locator("body").innerText({ timeout: 6000 }).catch(() => ""));
}

function captchaSignalled(text) {
  const hit = HUMAN_SIGNALS.find((s) => text.includes(s));
  return hit || null;
}

async function isLoginButtonVisible(page, excludeSelector = '') {
  for (const label of ["登录", "登录/注册", "立即登录", "密码登录"]) {
    try {
      const matches = page.getByText(label, { exact: false });
      const count = await matches.count();
      for (let i = 0; i < (excludeSelector ? count : Math.min(count, 1)); i += 1) {
        const loc = matches.nth(i);
        if (excludeSelector && await loc.evaluate((el, selector) => !!el.closest(selector), excludeSelector)) continue;
        if (await loc.isVisible({ timeout: 1500 })) return true;
      }
    } catch {
      /* try next */
    }
  }
  return false;
}

// QiMai shows a logged-out banner like "登录/注册后可查看更多数据" / "注册即可获得SVIP特权".
// A logged-in session exposes a user avatar / nickname element instead.
async function detectQimai(page) {
  const text = await bodyText(page, QIMAI_REVIEW_TEXT);
  if (/当前网络或账号异常|半小时后重试/.test(text)) {
    return { state: "human_needed", signal: "qimai_network_or_account_restricted" };
  }
  const signal = captchaSignalled(text);
  if (signal) return { state: "human_needed", signal };
  if (text.includes("登录/注册后可查看更多") || text.includes("注册即可获得") || text.includes("登录后查看")) {
    return { state: "logged_out" };
  }
  // absence of a prominent login button AND presence of an avatar/nickname => logged in
  const hasAvatar = await page
    .locator(".user-avatar, .header-avatar, .user-info, .nickname, [class*='avatar']")
    .count()
    .catch(() => 0);
  const loginVisible = await isLoginButtonVisible(page, QIMAI_REVIEW_TEXT);
  if (!loginVisible && hasAvatar > 0) return { state: "logged_in" };
  if (loginVisible) return { state: "logged_out" };
  return { state: "unknown" };
}

export async function detectState(page, kind) {
  if (kind !== "qimai") throw new Error("unsupported login platform");
  const r = await detectQimai(page);
  return r.state;
}

// Detailed variant used for diagnostics (includes the matched signal + text snippet).
export async function detectStateDetail(page, kind) {
  if (kind !== "qimai") throw new Error("unsupported login platform");
  return detectQimai(page);
}

export async function waitForManualLogin(page, name, verify) {
  if (!process.stdin.isTTY) return false;
  const terminal = createInterface({ input: process.stdin, output: process.stdout });
  try {
    for (;;) {
      const answer = await terminal.question(`请在当前浏览器完成${name}登录。\n[1] 已登录，继续  [2] 取消本次运行\n> `);
      if (answer.trim() === "2") return false;
      if (answer.trim() !== "1") continue;
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline && !page.isClosed()) {
        if (await verify()) return true;
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      console.log("尚未验证到真实登录态，请完成登录后再选择继续。");
    }
  } finally {
    terminal.close();
  }
}

export async function ensureLoggedInWithCheckpoint(page, kind, creds, options = {}) {
  const result = await ensureLoggedIn(page, kind, creds, options);
  if (result.ok) return result;
  const resumed = await waitForManualLogin(page, "七麦",
    async () => await detectState(page, kind) === "logged_in");
  return resumed ? { ok: true, state: "logged_in", reason: "manual_login_completed" } : result;
}

async function findInputByHints(page, hints) {
  const inputs = page.locator("input");
  const count = await inputs.count().catch(() => 0);
  for (let i = 0; i < count; i += 1) {
    const el = inputs.nth(i);
    let hay = "";
    try {
      hay = normalize((await Promise.all(
        ["placeholder", "name", "id", "aria-label"].map(attribute => el.getAttribute(attribute))
      )).filter(Boolean).join(" "));
    } catch {
      hay = "";
    }
    if (hints.some((h) => hay.includes(h)) && await el.isVisible()) return el;
  }
  return null;
}

async function fillField(page, hints, value, label) {
  if (typeof value !== "string" || !value) return false;
  const el = await findInputByHints(page, hints);
  if (!el) return false;
  try {
    await el.fill(value, { timeout: 2000 });
    return await el.inputValue() === value;
  } catch {
    return false;
  }
}

async function clickSubmit(page) {
  // Press Enter on the password field first — it triggers native form submission and
  // avoids the ambiguity of multiple "登录" texts (nav link vs modal submit button).
  const pw = await findInputByHints(page, ["密码", "password"]);
  if (pw) {
    await pw.press("Enter").catch(() => {});
    await page.waitForTimeout(500);
    return true;
  }
  // Fallback: click a real <button> whose text is a submit action (avoid the nav link).
  const btn = page.locator("button").filter({ hasText: /登录|确认登录|登\s*录|立即登录|确定|提交/ });
  if (await btn.count()) {
    await btn.first().click({ timeout: 2500 }).catch(() => {});
    return true;
  }
  return false;
}

async function openLoginDialog(page) {
  for (const label of ["登录", "登录/注册", "立即登录", "密码登录"]) {
    try {
      const loc = page.getByText(label, { exact: false }).first();
      if ((await loc.count()) > 0 && (await loc.isVisible({ timeout: 1500 }))) {
        await loc.click({ timeout: 2500 });
        await page.waitForTimeout(1200);
        return true;
      }
    } catch {
      /* try next */
    }
  }
  return false;
}

/**
 * Ensure the session is logged in. Returns { ok, state, reason, screenshot }.
 * On captcha/2FA, ok=false and reason="human_needed" with a screenshot path.
 */
export async function ensureLoggedIn(page, kind, creds, options = {}) {
  const screenshotDir = options.screenshotDir || process.cwd();
  const waitMs = options.waitMs ?? 8000;

  let state = await detectState(page, kind);

  if (state === "logged_in") {
    return { ok: true, state, reason: "" };
  }
  if (state === "human_needed") {
    const shot = await saveShot(page, screenshotDir, `${kind}_human_needed.png`);
    return { ok: false, state, reason: "human_needed", screenshot: shot };
  }

  creds = creds[kind];
  if (!creds?.username || !creds?.password) {
    return { ok: false, state, reason: "平台登录凭据缺失；未尝试提交" };
  }

  // Try password login.
  await openLoginDialog(page);
  await page.waitForTimeout(800);
  // Switch to the password tab if the login dialog offers one (e.g. qimai defaults to 快捷登录).
  for (const tab of ["密码登录", "密码", "账号密码登录"]) {
    try {
      const loc = page.getByText(tab, { exact: false }).first();
      if ((await loc.count()) > 0 && (await loc.isVisible({ timeout: 1200 }))) {
        await loc.click({ timeout: 2000 });
        await page.waitForTimeout(500);
        break;
      }
    } catch {
      /* ignore */
    }
  }
  const filledUser = await fillField(page, ["手机", "账号", "邮箱", "用户", "username", "phone", "account"], creds.username, "username");
  const filledPass = await fillField(page, ["密码", "password"], creds.password, "password");
  console.error(`[login:${kind}] 账号框填充=${filledUser} 密码框填充=${filledPass}`);
  if (filledUser && filledPass) {
    const submitted = await clickSubmit(page);
    console.error(`[login:${kind}] 提交按钮点击=${submitted}`);
    await page.waitForTimeout(waitMs);
  } else {
    const shot = await saveShot(page, screenshotDir, `${kind}_login_form_unknown.png`);
    return { ok: false, state: "form_unknown", reason: "找不到账号/密码输入框", screenshot: shot };
  }

  state = await detectState(page, kind);
  if (state === "logged_in") return { ok: true, state, reason: "" };
  if (state === "human_needed") {
    const shot = await saveShot(page, screenshotDir, `${kind}_human_needed.png`);
    return { ok: false, state, reason: "human_needed", screenshot: shot };
  }
  const shot = await saveShot(page, screenshotDir, `${kind}_login_failed.png`);
  return { ok: false, state, reason: "登录后仍未检测到已登录状态", screenshot: shot };
}

async function saveShot(page, dir, name) {
  try {
    await fs.mkdir(dir, { recursive: true });
    const p = path.join(dir, name);
    await page.screenshot({ path: p, fullPage: true }).catch(() => {});
    return p;
  } catch {
    return "";
  }
}
