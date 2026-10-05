import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { spawnSync } from "node:child_process";
import { chromium } from "playwright";
import { triggerExportAndSave, setCustomDateRange } from "../src/qimai/browser-automation.mjs";
import { ensureLoggedIn } from "../src/utils/login.mjs";

const root = await fs.mkdtemp(path.join(os.tmpdir(), "qimai-download-test-"));
const python = process.env.PYTHON || "python3";
const made = spawnSync(python, ["-c", "import io,sys,openpyxl;w=openpyxl.Workbook();s=w.active;s.append(['发表时间','内容']);s.append(['2026-09-07','真实测试夹具']);b=io.BytesIO();w.save(b);sys.stdout.buffer.write(b.getvalue())"]);
assert.equal(made.status, 0);
const xlsx = made.stdout;
const csv = Buffer.from('发表时间,内容\r\n2026-09-07,complete first row\r\n2026-09-07,"first line\nsecond line"\r\n');
const cases = [
  { name: "custom-export-button", start: "2026-09-05", end: "2026-09-07", ext: "csv", customButton: true },
  { name: "single", start: "2026-09-07", end: "2026-09-07", ext: "xlsx" },
  { name: "three-day-slow", start: "2026-09-05", end: "2026-09-07", ext: "xlsx", delay: 3500 },
  { name: "cross-month", start: "2026-08-28", end: "2026-09-07", ext: "csv" },
  { name: "long-period", start: "2025-01-01", end: "2026-09-07", ext: "xlsx" },
  { name: "repeat-1", start: "2026-09-05", end: "2026-09-07", ext: "xlsx", suffix: " (1)", confirm: "dom" },
  { name: "repeat-17", start: "2026-09-05", end: "2026-09-07", ext: "csv", suffix: " (17)", confirm: "native", delay: 3500 },
  { name: "truncated-csv", start: "2026-09-05", end: "2026-09-07", ext: "csv", truncate: true },
  { name: "wrong-range", start: "2026-09-05", end: "2026-09-07", ext: "xlsx", wrongRange: true },
  { name: "corrupt-completed-xlsx", start: "2026-09-05", end: "2026-09-07", ext: "xlsx", corrupt: true },
  { name: "wrong-comment-date", start: "2026-09-06", end: "2026-09-06", ext: "xlsx", outside: true },
  { name: "continuous-slow-download", start: "2026-09-05", end: "2026-09-07", ext: "xlsx", stream: true },
  { name: "blob-xlsx", start: "2026-09-05", end: "2026-09-07", ext: "xlsx", blob: true },
  { name: "blob-csv", start: "2026-09-05", end: "2026-09-07", ext: "csv", blob: true },
  { name: "qimai-poptip-repeat", start: "2026-09-05", end: "2026-09-07", ext: "csv", confirm: "dom", poptip: true },
];
let current;
let requests = 0;
const server = http.createServer((req, res) => {
  if (req.url !== "/download") { res.end("<html><body></body></html>"); return; }
  requests += 1;
  const item = current;
  const data = item.corrupt ? xlsx.subarray(0, xlsx.length - 80) : item.ext === "xlsx" ? xlsx : csv;
  const name = `comments_${item.wrongRange ? '2026-08-01' : item.start}_${item.end}.${item.ext}${item.suffix || ''}`;
  res.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Disposition": `attachment; filename="${name}"`, "Content-Length": data.length });
  if (item.stream) {
    let offset = 0;
    const step = Math.ceil(data.length / 8);
    const timer = setInterval(() => {
      res.write(data.subarray(offset, offset + step));
      offset += step;
      if (offset >= data.length) { clearInterval(timer); res.end(); }
    }, 400);
    return;
  }
  const split = item.ext === "xlsx" ? data.length - 100 : data.indexOf(Buffer.from('2026-09-07,"'));
  res.write(data.subarray(0, split));
  setTimeout(() => item.truncate ? res.destroy() : res.end(data.subarray(split)), item.delay || 200);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const headed = process.argv.includes('--headed');
const context = await chromium.launchPersistentContext(path.join(root, "profile"), { headless: !headed, acceptDownloads: true });
const page = await context.newPage();
const results = [];
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.setContent('<button>登录</button><input placeholder="手机"><input placeholder="密码" type="password">');
  await page.evaluate(() => {
    document.querySelector('input[type=password]').onkeydown = (event) => {
      if (event.key !== 'Enter') return;
      window.submitted = [...document.querySelectorAll('input')].map((el) => el.value);
      document.body.innerHTML = '<div class="user-avatar">test user</div>';
    };
  });
  const login = await ensureLoggedIn(page, 'qimai', { qimai: { username: 'fixture-user', password: 'fixture-password' } }, { waitMs: 0, screenshotDir: root });
  assert.equal(login.ok, true);
  assert.deepEqual(await page.evaluate(() => window.submitted), ['fixture-user', 'fixture-password']);
  results.push({ name: 'platform-credentials-filled-and-verified', passed: true });
  await page.setContent('<button>登录</button>');
  const missing = await ensureLoggedIn(page, 'qimai', { qimai: {} }, { waitMs: 0, screenshotDir: root });
  assert.equal(missing.ok, false);
  assert.match(missing.reason, /凭据缺失/);
  results.push({ name: 'missing-credentials-no-submit', passed: true });
  // Keep the chart date input alongside the Android comment filter to detect
  // the original bug: selecting a chart date instead of the export range.
  for (const platform of ['ios', 'android']) {
    for (const [start, end] of [['2026-09-07', '2026-09-07'], ['2026-09-05', '2026-09-07'], ['2026-08-28', '2026-09-03'], ['2025-01-01', '2026-09-07']]) {
      await page.goto(`http://127.0.0.1:${server.address().port}/${platform === 'android' ? 'andapp' : 'app'}/comment`);
      const filter = '<div class="filter-container"><button id="custom">自定义</button><div class="ivu-date-picker"><input></div><span id="applied"></span></div>';
      const body = platform === 'ios' ? `<div class="comment-details">${filter}</div>` : `<div><div class="chart"><div class="ivu-date-picker"><input value="chart unchanged"></div></div>${filter}<div class="comment-details"></div></div>`;
      await page.setContent(body);
      await page.evaluate(() => {
        document.querySelector('.filter-container input').onkeydown = (event) => {
          if (event.key === 'Enter') document.querySelector('#applied').textContent = event.target.value.replace(/(\d{4})-(\d{2})-(\d{2})/g, '$1年$2月$3日');
        };
      });
      const checked = await setCustomDateRange(page, start, end);
      assert.equal(checked.ok, true, JSON.stringify(checked));
      if (platform === 'android') assert.equal(await page.locator('.chart input').inputValue(), 'chart unchanged');
      results.push({ name: `date-${platform}-${start}_${end}`, passed: true });
    }
  }
  for (const item of cases) {
    current = item;
    requests = 0;
    const out = path.join(root, item.name);
    await fs.mkdir(out);
    const stale = path.join(out, "old.xlsx (17).crdownload");
    await fs.writeFile(stale, xlsx);
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.setContent(`<button class="export-data">${item.customButton ? '导 出' : '导出数据'}</button><div id="confirm" ${item.poptip ? 'class="ivu-poptip-popper"' : 'role="dialog"'} hidden>您已经导出了此项数据，确定再次导出么？<button>取消</button><a href="/download">确定</a></div>`);
    await page.evaluate(({ mode, blob, filename }) => {
      document.querySelector('#confirm a').onclick = () => { document.querySelector('#confirm').hidden = true; };
      document.querySelector('.export-data').onclick = async () => {
        if (blob) {
          const data = await (await fetch('/download')).blob();
          const a = document.createElement('a');
          a.href = URL.createObjectURL(data);
          a.download = filename;
          a.click();
          return;
        }
        if (mode === 'dom') document.querySelector('#confirm').hidden = false;
        else if (mode !== 'native' || confirm('您已经导出了此项数据，确定再次导出么？')) location.href = '/download';
      };
    }, { mode: item.confirm, blob: item.blob, filename: `DemoApp_${item.start}_${item.end}.${item.ext}` });
    const started = Date.now();
    const result = await triggerExportAndSave(page, out, item.name, 5000, {
      ...(item.customButton ? { exportButton: page.getByRole('button', { name: /^导\s*出$/ }) } : {}),
      completionTimeoutMs: item.stream ? 1800 : 12000, range: { startText: item.start, endText: item.end }
    });
    assert.equal(result.ok, !item.truncate && !item.wrongRange && !item.corrupt && !item.outside, JSON.stringify(result));
    if (result.ok) {
      assert.equal(result.downloadTask.state, 'completed');
      assert.deepEqual(await fs.readFile(result.path), item.ext === 'xlsx' ? xlsx : csv);
      assert.equal(requests, 1, 'download must not be re-triggered during a pause');
      if (item.delay) assert.ok(Date.now() - started >= item.delay);
      if (item.confirm) assert.equal(result.repeatExportConfirmed, true);
    } else {
      assert.equal(await fs.stat(path.join(out, `${item.name}.${item.ext}`)).then(() => true, () => false), false);
    }
    assert.deepEqual(await fs.readFile(stale), xlsx);
    results.push({ name: item.name, passed: true, stage: result.stage || 'completed' });
    if (item.poptip) {
      const again = await triggerExportAndSave(page, out, 'repeat-same-tab', 5000, {
        completionTimeoutMs: 12000, range: { startText: item.start, endText: item.end }
      });
      assert.equal(again.ok, true, JSON.stringify(again));
      assert.equal(again.repeatExportConfirmed, true);
      assert.equal(requests, 2);
      results.push({ name: 'same-poptip-element-reused', passed: true });
    }
  }
  console.log(JSON.stringify(results));
} finally {
  await context.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
  await fs.rm(root, { recursive: true, force: true });
}
