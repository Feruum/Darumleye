// Forward arguments without shell interpolation, using the existing Python project.
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const project = fileURLToPath(new URL('../services/agent/', import.meta.url))
const args = process.argv.slice(2)
const rootEnv = fileURLToPath(new URL('../../.env', import.meta.url))
const explicitEnv = args.some((arg) => arg === '--env-file' || arg.startsWith('--env-file='))
const envArgs = !explicitEnv && existsSync(rootEnv) ? ['--env-file', rootEnv] : []
const moduleArgs = ['-m', 'agent.spec_review_cli', ...envArgs, ...args]
const options = { stdio: 'inherit' }
let result = spawnSync('uv', ['run', '--project', project, '--frozen', 'python', ...moduleArgs], options)

// uv is preferred for a fresh checkout; an already provisioned environment can run offline.
if (result.error?.code === 'ENOENT') {
  const python = join(project, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  if (!existsSync(python)) {
    console.error('Установите uv (https://docs.astral.sh/uv/) для запуска Spec Review.')
    process.exit(1)
  }
  result = spawnSync(python, moduleArgs, options)
}
if (result.error) {
  console.error('Не удалось запустить CLI Spec Review. Проверьте установленный Python/uv.')
  process.exit(1)
}
process.exit(result.status ?? (result.signal === 'SIGINT' ? 130 : 1))
