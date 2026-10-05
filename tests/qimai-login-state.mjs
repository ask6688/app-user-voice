import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { detectStateDetail, ensureLoggedIn } from '../src/utils/login.mjs';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const reviews = '<div class="user-avatar">fixture</div><div class="comment-details">' +
  '<div class="ivu-table-row"><div class="comment-txt">手机号注册验证码输入是对的，就是不让你登录</div></div></div>';
let checked = 0;
try {
  for (const text of ['验证码', '滑块', '二次验证', '当前网络或账号异常', '登录后查看']) {
    await page.setContent(reviews.replace('手机号注册验证码输入是对的，就是不让你登录', text + ' 用户评论里说登录失败'));
    assert.equal((await detectStateDetail(page, 'qimai')).state, 'logged_in', text);
    checked += 1;
  }
  assert.equal((await ensureLoggedIn(page, 'qimai', {})).ok, true, 'must reuse login without credentials');
  checked += 1;
  for (const html of [
    '<div role="dialog">请输入验证码</div>',
    '<div class="ivu-modal">请拖动滑块</div>',
    '<div role="alert">当前网络或账号异常，请半小时后重试</div>',
    '<p>请在手机上确认</p>',
  ]) {
    await page.setContent(reviews + html);
    assert.equal((await detectStateDetail(page, 'qimai')).state, 'human_needed', html);
    checked += 1;
  }
  await page.setContent(reviews + '<button>登录</button>');
  assert.equal((await detectStateDetail(page, 'qimai')).state, 'logged_out');
  checked += 1;
  await page.setContent(reviews + '<div hidden>请输入验证码</div>');
  assert.equal((await detectStateDetail(page, 'qimai')).state, 'logged_in');
  checked += 1;
  await page.setContent('<p>数据加载中</p>');
  assert.equal((await detectStateDetail(page, 'qimai')).state, 'unknown');
  checked += 1;
  await page.setContent('<div role="dialog">二次验证</div>');
  assert.equal((await detectStateDetail(page, 'qimai')).state, 'human_needed');
  checked += 1;
  console.log(`${checked} login state checks passed (local fixtures only)`);
} finally {
  await browser.close(); // Only the isolated headless fixture browser.
}
