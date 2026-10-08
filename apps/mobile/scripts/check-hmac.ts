// Checks the app's HMAC against Node's own, on random keys and messages:  node scripts/check-hmac.ts
// The server verifies a device proof with Python's hmac module; this shows the phone computes the same value.
import { createHash, createHmac, randomBytes, randomUUID } from "node:crypto";
import { hmacSha256Hex } from "../src/hmac.ts";

const sha256 = async (data: Uint8Array) => new Uint8Array(createHash("sha256").update(data).digest());

let bad = 0;
const cases: [string, string][] = [
  ["0b".repeat(20), "Hi There"],                                   // RFC 4231 test case 1
  ["aa".repeat(131), "Test Using Larger Than Block-Size Key - Hash Key First"],   // RFC 4231 test case 6
];
for (let i = 0; i < 200; i++) cases.push([randomBytes(32).toString("hex"), randomUUID()]);
cases.push([randomBytes(32).toString("hex"), "भुगतान ₹1,80,000"]);

for (const [key, msg] of cases) {
  const want = createHmac("sha256", Buffer.from(key, "hex")).update(msg, "utf8").digest("hex");
  const got = await hmacSha256Hex(key, msg, sha256);
  if (want !== got) {
    bad += 1;
    console.log("MISMATCH", key.slice(0, 16), JSON.stringify(msg), want, got);
  }
}
const rfc = await hmacSha256Hex("0b".repeat(20), "Hi There", sha256);
if (rfc !== "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7") {
  bad += 1;
  console.log("RFC 4231 vector failed:", rfc);
}
console.log(bad ? `${bad} mismatches` : `ok: ${cases.length} cases match Node's HMAC-SHA256, including the RFC 4231 vectors`);
process.exit(bad ? 1 : 0);
