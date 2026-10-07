/** Minimal ZIP writer for "upload a folder from my computer": files picked with <input webkitdirectory> are
 *  packed in the browser and sent through the same validated /uploads endpoint as a ZIP.
 *  Deflate via the native CompressionStream when available, otherwise stored. No ZIP64 (limits enforced below). */

export const SKIP_DIRS = new Set(['.git', 'target', 'node_modules', '.idea', '__MACOSX']);
export const MAX_ENTRIES = 20000;

export interface PickedFile { path: string; data: Blob }

/** Keeps project files only: drops VCS, build output and IDE folders anywhere in the tree. */
export function filterPicked(files: { webkitRelativePath: string; data: Blob }[]): PickedFile[] {
  return files
    .filter(f => !f.webkitRelativePath.split('/').some(p => SKIP_DIRS.has(p) || p === '.DS_Store'))
    .map(f => ({ path: f.webkitRelativePath, data: f.data }));
}

let table: Uint32Array | null = null;
export function crc32(bytes: Uint8Array): number {
  if (!table) {
    table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) crc = table[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

async function deflate(bytes: Uint8Array): Promise<Uint8Array | null> {
  if (typeof CompressionStream === 'undefined') return null;
  try {
    const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new CompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  } catch {
    return null;  // 'deflate-raw' unsupported: store
  }
}

export async function buildZip(files: PickedFile[], opts: { compress?: boolean; onProgress?: (done: number, total: number) => void } = {}): Promise<Blob> {
  if (files.length === 0) throw new Error('La carpeta no contiene archivos de proyecto');
  if (files.length > MAX_ENTRIES) throw new Error(`Demasiados archivos (${files.length} > ${MAX_ENTRIES})`);
  const enc = new TextEncoder();
  const parts: BlobPart[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  const DOS_DATE = (0 << 9) | (1 << 5) | 1;  // 1980-01-01, deterministic
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    if (f.path.startsWith('/') || f.path.split('/').includes('..')) throw new Error(`Ruta no permitida: ${f.path}`);
    const raw = new Uint8Array(await f.data.arrayBuffer());
    const crc = crc32(raw);
    const deflated = opts.compress === false ? null : await deflate(raw);
    const useDeflate = !!deflated && deflated.length < raw.length;
    const body = useDeflate ? deflated! : raw;
    const name = enc.encode(f.path);
    if (offset + body.length > 0xfffffff0) throw new Error('El proyecto supera 4 GB: suba un ZIP');
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
    local.setUint16(8, useDeflate ? 8 : 0, true); local.setUint16(10, 0, true); local.setUint16(12, DOS_DATE, true);
    local.setUint32(14, crc, true); local.setUint32(18, body.length, true); local.setUint32(22, raw.length, true);
    local.setUint16(26, name.length, true); local.setUint16(28, 0, true);
    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true); cd.setUint16(4, 20, true); cd.setUint16(6, 20, true); cd.setUint16(8, 0x0800, true);
    cd.setUint16(10, useDeflate ? 8 : 0, true); cd.setUint16(12, 0, true); cd.setUint16(14, DOS_DATE, true);
    cd.setUint32(16, crc, true); cd.setUint32(20, body.length, true); cd.setUint32(24, raw.length, true);
    cd.setUint16(28, name.length, true); cd.setUint32(42, offset, true);
    parts.push(new Uint8Array(local.buffer), name, body as BlobPart);
    const cdEntry = new Uint8Array(46 + name.length);
    cdEntry.set(new Uint8Array(cd.buffer), 0);
    cdEntry.set(name, 46);
    central.push(cdEntry);
    offset += 30 + name.length + body.length;
    opts.onProgress?.(i + 1, files.length);
  }
  const cdSize = central.reduce((n, c) => n + c.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central as BlobPart[], new Uint8Array(end.buffer)], { type: 'application/zip' });
}
