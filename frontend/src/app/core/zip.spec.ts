import { buildZip, crc32, filterPicked } from './zip';

async function readZip(blob: Blob) {
  const b = new Uint8Array(await blob.arrayBuffer());
  const v = new DataView(b.buffer);
  const eocd = b.length - 22;
  expect(v.getUint32(eocd, true)).toBe(0x06054b50);
  const count = v.getUint16(eocd + 10, true);
  let p = v.getUint32(eocd + 16, true);
  const out: Record<string, string> = {};
  for (let i = 0; i < count; i++) {
    expect(v.getUint32(p, true)).toBe(0x02014b50);
    const method = v.getUint16(p + 10, true), csize = v.getUint32(p + 20, true), crc = v.getUint32(p + 16, true);
    const nlen = v.getUint16(p + 28, true), off = v.getUint32(p + 42, true);
    const name = new TextDecoder().decode(b.slice(p + 46, p + 46 + nlen));
    const lnlen = v.getUint16(off + 26, true);
    const data = b.slice(off + 30 + lnlen, off + 30 + lnlen + csize);
    const raw = method === 8
      ? new Uint8Array(await new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).arrayBuffer())
      : data;
    expect(crc32(raw)).toBe(crc);
    out[name] = new TextDecoder().decode(raw);
    p += 46 + nlen;
  }
  return out;
}

describe('zip (carpeta del navegador → ZIP)', () => {
  it('crc32 estándar', () => {
    expect(crc32(new TextEncoder().encode('123456789'))).toBe(0xcbf43926);
  });

  it('omite .git, target y node_modules', () => {
    const blob = new Blob(['x']);
    const kept = filterPicked([
      { webkitRelativePath: 'orders/pom.xml', data: blob },
      { webkitRelativePath: 'orders/.git/config', data: blob },
      { webkitRelativePath: 'orders/mod/target/classes/A.class', data: blob },
      { webkitRelativePath: 'orders/ui/node_modules/x.js', data: blob },
    ]);
    expect(kept.map(k => k.path)).toEqual(['orders/pom.xml']);
  });

  for (const compress of [true, false]) {
    it(`genera un ZIP legible (${compress ? 'deflate' : 'store'})`, async () => {
      const pom = '<project>' + 'x'.repeat(2000) + '</project>';
      const zip = await buildZip([
        { path: 'orders/pom.xml', data: new Blob([pom]) },
        { path: 'orders/src/Á.java', data: new Blob(['class A {}']) },
      ], { compress });
      expect(await readZip(zip)).toEqual({ 'orders/pom.xml': pom, 'orders/src/Á.java': 'class A {}' });
    });
  }

  it('rechaza rutas con ..', async () => {
    await expect(buildZip([{ path: '../x', data: new Blob(['a']) }])).rejects.toThrow();
  });
});
