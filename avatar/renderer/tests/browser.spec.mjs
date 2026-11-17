import { spawnSync } from 'node:child_process';
import { test, expect } from '@playwright/test';

function corner(png) {
  const result = spawnSync('python3', ['-c', `
import json, struct, sys, zlib
data = sys.stdin.buffer.read()
assert data[:8] == b"\\x89PNG\\r\\n\\x1a\\n"
offset = 8
width = height = None
idat = b""
while offset + 8 <= len(data):
    length = struct.unpack(">I", data[offset:offset+4])[0]
    tag = data[offset+4:offset+8]
    chunk = data[offset+8:offset+8+length]
    if tag == b"IHDR":
        width, height = struct.unpack(">II", chunk[:8])
    elif tag == b"IDAT":
        idat += chunk
    elif tag == b"IEND":
        break
    offset += 12 + length
raw = zlib.decompress(idat)
row = width * 4
pixel = raw[1:5]
json.dump({"width": width, "height": height, "rgba": list(pixel)}, sys.stdout)
`], { input: png });
  if (result.status !== 0) {
    throw new Error(result.stderr.toString() || 'png decode failed');
  }
  return JSON.parse(result.stdout.toString());
}

test('timeline opens the mouth, cancel closes it, background stays black', async ({ page }) => {
  const errors = [];
  const external = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('request', (request) => {
    const url = request.url();
    const local = url.startsWith('http://127.0.0.1')
      || url.startsWith('http://localhost')
      || url.startsWith('blob:')
      || url.startsWith('data:');
    if (!local) external.push(url);
  });
  await page.goto('/index.html');
  await page.waitForFunction(() => window.__avatar && window.__avatar.loaded, null, { timeout: 120000 });
  const clear = await page.evaluate(() => window.__avatar.clear());
  expect(clear.hex).toBe('000000');
  expect(clear.alpha).toBe(1);
  expect(clear.background).toBe('000000');

  await page.evaluate(() => {
    const samples = 22050;
    const pcm = new ArrayBuffer(samples * 2);
    window.__avatar.playPackage({
      turn_id: 't-browser',
      sample_rate: 22050,
      pcm,
      visemes: ['aa'],
      vtimes: [0],
      vdurations: [900],
    });
  });
  await page.waitForFunction(() => window.__avatar.mouthOpen(), null, { timeout: 10000 });
  const cancelMs = await page.evaluate(() => window.__avatar.cancel('t-browser'));
  expect(cancelMs).toBeLessThan(150);
  const stillOpen = await page.evaluate(() => window.__avatar.mouthOpen());
  expect(stillOpen).toBe(false);

  const shot = await page.screenshot({ clip: { x: 0, y: 0, width: 8, height: 8 } });
  const pixel = corner(shot);
  expect(pixel.rgba[0]).toBe(0);
  expect(pixel.rgba[1]).toBe(0);
  expect(pixel.rgba[2]).toBe(0);
  expect(errors, errors.join('\n')).toEqual([]);
  expect(external, external.join('\n')).toEqual([]);
});
