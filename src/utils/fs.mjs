import { unlinkSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";

export async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true });
}

export async function writeJson(filePath, data) {
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

// One run owns a workspace browser's download target at a time.
export async function acquireRunLock(lockPath) {
  await ensureDir(path.dirname(lockPath));
  for (;;) {
    try {
      const lock = await fs.open(lockPath, "wx", 0o600);
      await lock.writeFile(String(process.pid));
      await lock.close();
      const removeOnExit = () => { try { unlinkSync(lockPath); } catch {} };
      process.once("exit", removeOnExit);
      return async () => {
        process.off("exit", removeOnExit);
        await fs.unlink(lockPath).catch(error => { if (error.code !== "ENOENT") throw error; });
      };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      const owner = Number(await fs.readFile(lockPath, "utf8"));
      if (!Number.isInteger(owner) || owner <= 0) throw new Error("七麦抓取锁正在初始化；请勿并发运行");
      try { process.kill(owner, 0); }
      catch (error) {
        if (error.code !== "ESRCH") throw error;
        await fs.unlink(lockPath);
        continue;
      }
      throw new Error(`七麦抓取已在运行（PID ${owner}），请等待完成；未操作浏览器`);
    }
  }
}
