import { promises as fsp } from 'node:fs'
import { readStatus } from '../hooks/register.js'

const [home, state, sid] = process.argv.slice(2)
const vars = { HOME: home, JUNO_STATE: state || undefined }
const $ = {
  env: { get: async (k) => vars[k] },
  session: { id: async () => sid || undefined },
  ui: { log() {} },
  fs: {
    stat: async (p) => fsp.stat(p).catch((e) => { throw new Error(`${e.code}: ${p}`) }),
    read: async (p) => fsp.readFile(p, 'utf8'),
  },
}
console.log(JSON.stringify(await readStatus($, Date.now())))
