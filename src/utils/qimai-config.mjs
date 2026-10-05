// The Python CLI supplies only the app and sources selected by the user.
export function validateQimaiConfig(config) {
  if (!config?.app || !config?.qimai) throw new Error("config requires app and qimai objects");
  const requested = config.qimai.platforms ?? ["ios", "android"].filter(platform => config.app[platform]);
  if (!Array.isArray(requested) || requested.some(platform => !["ios", "android"].includes(platform))) {
    throw new Error("qimai.platforms must contain ios and/or android");
  }
  const platforms = [...new Set(requested)].filter(platform => config.app[platform]?.enabled !== false);
  if (!platforms.length) throw new Error("Enable at least one QiMai platform");
  function checkUrl(value, platform) {
    let url;
    try { url = new URL(value); } catch { throw new Error(`${platform} comment URL is required`); }
    const prefix = platform === "ios" ? "/app/comment/appid/" : "/andapp/comment/appid/";
    if (url.protocol !== "https:" || !["qimai.cn", "www.qimai.cn"].includes(url.hostname) || !url.pathname.startsWith(prefix)) {
      throw new Error(`${platform} requires an HTTPS QiMai comment page URL`);
    }
  }
  if (platforms.includes("ios")) checkUrl(config.app.ios?.commentUrl, "ios");
  const channels = platforms.includes("android")
    ? (config.app.android?.channels || []).filter(channel => channel.enabled !== false) : [];
  if (platforms.includes("android")) {
    checkUrl(config.app.android?.baseCommentUrl, "android");
    if (!channels.length) throw new Error("Enable at least one Android channel");
    const keys = new Set();
    for (const channel of channels) {
      if (!/^[a-zA-Z0-9_-]+$/.test(channel.key || "") || keys.has(channel.key)) {
        throw new Error("Android channel keys must be unique, safe directory names");
      }
      keys.add(channel.key);
      if (!channel.name || /[\/\\\x00-\x1f]/.test(channel.name)) throw new Error("Android channel name is required and must be a safe filename");
      if (!Number.isInteger(channel.marketCode) || channel.marketCode <= 0) throw new Error("Android channel marketCode must be a positive integer");
    }
  }
  return { platforms, channels };
}
