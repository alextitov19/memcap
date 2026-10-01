'use strict';

// Managed descendants inherit this preload. Match executable identity, never
// command-line text: an ordinary program mentioning Jest must remain unchanged.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

function runner() {
  if (!process.argv[1]) return null;
  let file;
  try { file = fs.realpathSync(process.argv[1]); } catch { return null; }
  let directory = path.dirname(file);
  for (let depth = 0; depth < 3; depth += 1) {
    try {
      const manifest = path.join(directory, 'package.json');
      const info = fs.statSync(manifest);
      if (!info.isFile() || info.size > 65536) return null;
      const name = JSON.parse(fs.readFileSync(manifest, 'utf8')).name;
      const entry = path.relative(directory, file).split(path.sep).join('/');
      if ((name === 'jest' || name === 'jest-cli') && entry === 'bin/jest.js') return 'jest';
      if (name === 'vitest' && entry === 'vitest.mjs') return 'vitest';
      if (['playwright', '@playwright/test'].includes(name) && entry === 'cli.js') return 'playwright';
      return null; // Do not attribute a nested package's entrypoint to its parent.
    } catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'ENOTDIR') return null;
    }
    const parent = path.dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  return null;
}

function bounded(value, maximum) {
  if (/^\d+%$/.test(value)) {
    const percent = Number(value.slice(0, -1));
    if (percent <= 0 || percent > 100) return null;
    const cpus = os.availableParallelism ? os.availableParallelism() : os.cpus().length;
    return Math.max(1, Math.min(maximum, Math.floor(cpus * percent / 100)));
  }
  if (!/^\d+$/.test(value) || Number(value) < 1) return null;
  return Math.min(maximum, Number(value));
}

function cap(args, flags, output, maximum) {
  const result = [];
  let limit = maximum;
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === '--') return {args: [...result, `${output}=${limit}`, ...args.slice(i)], limit};
    let value;
    if (flags.includes(arg)) {
      if (i + 1 === args.length) return null; // Preserve invalid input for the CLI.
      value = args[++i];
    } else {
      const long = flags.find(flag => arg.startsWith(`${flag}=`));
      const short = flags.find(flag => flag.length === 2 && arg.startsWith(flag) && /^\d+%?$/.test(arg.slice(2)));
      if (long) value = arg.slice(long.length + 1);
      else if (short) value = arg.slice(2);
      else { result.push(arg); continue; }
    }
    const candidate = bounded(value, maximum);
    if (candidate === null) return null;
    limit = Math.min(limit, candidate);
  }
  return {args: [...result, `${output}=${limit}`], limit};
}

function apply() {
  const raw = process.env.MEMCAP_NODE_WORKERS || '';
  if (!/^[1-9]\d*$/.test(raw) || Number(raw) > 64) return;
  const kind = runner();
  if (!kind) return;
  const args = process.argv.slice(2);
  const end = args.indexOf('--');
  const options = end < 0 ? args : args.slice(0, end);
  if (kind === 'jest' && options.some(arg => ['--runInBand', '-i', '--runInBand=true'].includes(arg))) return;
  if (kind === 'playwright' && args[0] !== 'test') return;
  const flags = kind === 'playwright' ? ['--workers', '-j'] :
    kind === 'jest' ? ['--maxWorkers', '--max-workers', '-w'] : ['--maxWorkers', '--max-workers'];
  let result = cap(args, flags,
                   kind === 'playwright' ? '--workers' : '--maxWorkers', Number(raw));
  if (!result) return;
  if (kind === 'vitest' && options.some(arg => ['--minWorkers', '--min-workers'].some(flag => arg === flag || arg.startsWith(`${flag}=`)))) {
    result = cap(result.args, ['--minWorkers', '--min-workers'], '--minWorkers', result.limit);
    if (!result) return;
  }
  process.argv.splice(2, process.argv.length - 2, ...result.args);
}

apply();
