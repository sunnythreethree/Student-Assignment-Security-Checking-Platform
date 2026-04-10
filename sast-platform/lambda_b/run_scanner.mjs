/**
 * run_scanner.mjs
 * CLI wrapper around the teacher's scanner.js.
 * Called by scanner.py via subprocess:
 *   node run_scanner.mjs <code-file-path> <filename>
 * Outputs JSON array of vulnerability findings to stdout.
 */
import { scanCode } from './scanner.js';
import fs from 'fs';

const [, , codePath, filename] = process.argv;

if (!codePath) {
  process.stderr.write('Usage: node run_scanner.mjs <code-file-path> <filename>\n');
  process.exit(1);
}

try {
  const code = fs.readFileSync(codePath, 'utf-8');
  const findings = scanCode(code, filename || 'code.js');
  process.stdout.write(JSON.stringify(findings));
} catch (err) {
  process.stderr.write(`Scanner error: ${err.message}\n`);
  process.exit(1);
}
