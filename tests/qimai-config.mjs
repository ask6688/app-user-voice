import assert from "node:assert/strict";
import { loadCredentials } from "../src/utils/credentials.mjs";
import { resolveDateRangeFromArgs } from "../src/utils/dates.mjs";
import { validateQimaiConfig } from "../src/utils/qimai-config.mjs";

const ios = { commentUrl: "https://www.qimai.cn/app/comment/appid/1234567890/country/cn" };
const android = { baseCommentUrl: "https://www.qimai.cn/andapp/comment/appid/123456/market/100",
  channels: [{ key: "demo", name: "Demo", marketCode: 9 }, { key: "off", name: "Off", marketCode: 8, enabled: false }] };
assert.deepEqual(validateQimaiConfig({ app: { ios }, qimai: {} }).platforms, ["ios"]);
assert.deepEqual(validateQimaiConfig({ app: { android }, qimai: {} }).platforms, ["android"]);
assert.equal(validateQimaiConfig({ app: { android }, qimai: {} }).channels.length, 1);
assert.deepEqual(validateQimaiConfig({ app: { ios: { enabled: false }, android }, qimai: { platforms: ["ios", "android"] } }).platforms, ["android"]);
assert.deepEqual(validateQimaiConfig({ app: { ios, android: { enabled: false } }, qimai: { platforms: ["ios"] } }).platforms, ["ios"]);
assert.throws(() => validateQimaiConfig({ app: { ios }, qimai: { platforms: [] } }), /Enable at least/);
assert.throws(() => validateQimaiConfig({ app: { ios }, qimai: { platforms: ["invalid"] } }), /platforms/);
assert.throws(() => validateQimaiConfig({ app: { android: { ...android, channels: [] } }, qimai: {} }), /channel/);
assert.throws(() => validateQimaiConfig({ app: { android: { ...android, channels: [{ key: "../escape", name: "Demo", marketCode: 9 }] } }, qimai: {} }), /keys/);
assert.throws(() => validateQimaiConfig({ app: { ios: { commentUrl: "https://example.com/app/comment/appid/1" } }, qimai: {} }), /QiMai/);
assert.throws(() => resolveDateRangeFromArgs([]), /Python CLI/);
assert.throws(() => resolveDateRangeFromArgs(["--start-date", "2026-02-30", "--end-date", "2026-03-01"]), /valid/);
assert.throws(() => resolveDateRangeFromArgs(["--start-date", "2026-03-02", "--end-date", "2026-03-01"]), /after/);
assert.equal(resolveDateRangeFromArgs(["--start-date", "2026-02-27", "--end-date", "2026-03-01"]).days, 3);
assert.equal(loadCredentials({ QIMAI_USERNAME: "fixture", QIMAI_PASSWORD: "fixture-password" }).ok, true);
assert.equal(loadCredentials({}).ok, false);
assert.equal(loadCredentials({ QIMAI_USERNAME: "fixture" }).ok, false);
console.log("17 configuration, platform, date and independent-credential checks passed");
