import test from 'node:test'
import assert from 'node:assert/strict'

import { API_BASE_URL, apiUrl } from './url.js'

test('默认同域 API 地址保持 /api 且不重复前缀', () => {
  assert.equal(API_BASE_URL, '/api')
  assert.equal(apiUrl('/chat'), '/api/chat')
  assert.equal(apiUrl('/api/chat/stream'), '/api/chat/stream')
})

test('绝对地址和开发 mock 地址保持原样', () => {
  assert.equal(apiUrl('https://example.com/api/health'), 'https://example.com/api/health')
  assert.equal(apiUrl('/__mock__/chat/stream'), '/__mock__/chat/stream')
})
