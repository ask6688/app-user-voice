// Credentials are optional: an existing session or manual login also works.
export function loadCredentials(env = process.env) {
  const qimai = {
    username: env.QIMAI_USERNAME || "",
    password: env.QIMAI_PASSWORD || "",
  };
  return { ok: Boolean(qimai.username && qimai.password), qimai };
}
