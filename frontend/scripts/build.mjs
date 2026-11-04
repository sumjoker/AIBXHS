/**
 * 生产构建包装脚本
 *
 * 背景：在 Windows 上直接 `vite build` 会报
 *   [vite:esbuild-transpile] remove C:\Users\...\AppData\Local\Temp\esbuild-xxx: Access is denied
 * 原因是 esbuild 把转译产物写到系统 %TEMP%，随后删除该临时文件时被杀毒软件 /
 * 索引服务 / 目录权限锁住（本机 100% 复现，2026-11-04 实测）。
 *
 * 处理：把 TMP/TEMP/TMPDIR 指向仓库内的 node_modules/.cache/esbuild-tmp
 * （不进 git、可随时删除），esbuild 临时文件不再落到系统 Temp 里。
 *
 * 用法：npm run build  （等价于 vite build，可继续追加 vite 参数）
 */
import { mkdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const tmpDir = resolve(root, "node_modules", ".cache", "esbuild-tmp");
mkdirSync(tmpDir, { recursive: true });

const viteBin = resolve(root, "node_modules", "vite", "bin", "vite.js");
const args = [viteBin, "build", ...process.argv.slice(2)];

const result = spawnSync(process.execPath, args, {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, TMP: tmpDir, TEMP: tmpDir, TMPDIR: tmpDir },
});

if (result.error) {
  console.error("[build] 启动 vite 失败:", result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
