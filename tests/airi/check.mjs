// Calls sc-core the way AIRI's core agent does (packages/core-agent/src/runtime/
// chat-completions.ts in moeru-ai/airi at 5824836): xsAI 0.5.1 streamText with
// includeUsage, a tool list, stopWhen, AIRI's own character system prompt and
// content-part arrays, after listModels. Exit 0 only if both turns get a real
// reply and the second sees the first.
//   SC_URL=http://127.0.0.1:8770 SC_TOKEN=... node tests/airi/check.mjs
import { listModels } from '@xsai/model'
import { streamText } from '@xsai/stream-text'
import { rawTool } from '@xsai/tool'

const base = process.env.SC_URL
const apiKey = process.env.SC_TOKEN
if (!base || !apiKey) {
  console.error('set SC_URL and SC_TOKEN')
  process.exit(2)
}
const baseURL = `${base.replace(/\/$/, '')}/v1/`

const models = await listModels({ baseURL, apiKey })
if (!models.length) {
  console.error('FAIL: /v1/models listed nothing')
  process.exit(1)
}
const model = models[0].id

const weather = rawTool({
  name: 'weather',
  description: 'Get the weather for a city',
  parameters: { type: 'object', properties: { city: { type: 'string' } }, required: ['city'] },
  execute: async () => 'sunny',
})
const stopWhen = ({ steps }) => !(steps.at(-1)?.toolCalls?.length > 0)

async function turn(messages) {
  const t0 = performance.now()
  const result = streamText({
    baseURL, apiKey, model, messages,
    streamOptions: { includeUsage: true }, tools: [weather], stopWhen,
  })
  let text = ''
  for await (const delta of result.textStream)
    text += delta
  return { text, ms: Math.round(performance.now() - t0) }
}

const system = { role: 'system', content: 'You are AIRI, a cheerful VTuber.' }
const first = { role: 'user', content: [{ type: 'text', text: 'My dog is called Biscuit. Please remember that.' }] }
const a = await turn([system, first])
const second = { role: 'user', content: [{ type: 'text', text: 'What is my dog called? Answer with the name.' }] }
const b = await turn([system, first, { role: 'assistant', content: a.text }, second])
console.log(JSON.stringify({ model, turn1: a, turn2: b }))
if (!a.text.trim() || !b.text.trim()) {
  console.error('FAIL: empty reply')
  process.exit(1)
}
if (!/biscuit/i.test(b.text)) {
  console.error('FAIL: the second turn did not see the first')
  process.exit(1)
}
console.log('PASS')
