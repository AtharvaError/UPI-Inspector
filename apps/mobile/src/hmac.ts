// HMAC-SHA256 built on a SHA-256 function, because the phone's crypto module offers digests but not HMAC.
// Pure and dependency-free so it can be checked against a reference implementation (scripts/check-hmac.ts).

export type Sha256 = (data: Uint8Array) => Promise<Uint8Array>;

export function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length >> 1);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export function bytesToHex(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += bytes[i].toString(16).padStart(2, "0");
  return s;
}

export function utf8(text: string): Uint8Array {
  if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text);
  const esc = unescape(encodeURIComponent(text));          // older JavaScript engines
  const out = new Uint8Array(esc.length);
  for (let i = 0; i < esc.length; i++) out[i] = esc.charCodeAt(i);
  return out;
}

function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/** HMAC-SHA256(key, message), hex. RFC 2104 with a 64-byte block. */
export async function hmacSha256Hex(keyHex: string, message: string, sha256: Sha256): Promise<string> {
  let key = hexToBytes(keyHex);
  if (key.length > 64) key = await sha256(key);
  const block = new Uint8Array(64);
  block.set(key);
  const ipad = new Uint8Array(64);
  const opad = new Uint8Array(64);
  for (let i = 0; i < 64; i++) {
    ipad[i] = block[i] ^ 0x36;
    opad[i] = block[i] ^ 0x5c;
  }
  const inner = await sha256(concat(ipad, utf8(message)));
  return bytesToHex(await sha256(concat(opad, inner)));
}
