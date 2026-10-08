// Where secrets live on the device, and the device's own key (F-35).
//
// On a phone the session token and the device key go in the system keystore (Android Keystore, iOS
// Keychain) through expo-secure-store: other apps cannot read them and they are not included in backups.
// In a browser there is no such place, so the token is kept in sessionStorage: it lasts as long as the tab
// and is never written to disk in the clear. The browser build is for the laptop demo only.
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import { bytesToHex, hmacSha256Hex } from "./hmac";

const web = Platform.OS === "web";
const clean = (key: string) => key.replace(/[^A-Za-z0-9._-]/g, "_");

export async function secureGet(key: string): Promise<string | null> {
  try {
    if (web) return typeof sessionStorage !== "undefined" ? sessionStorage.getItem(key) : null;
    return await SecureStore.getItemAsync(clean(key));
  } catch {
    return null;
  }
}

export async function secureSet(key: string, value: string | null): Promise<void> {
  try {
    if (web) {
      if (typeof sessionStorage === "undefined") return;
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } else if (value === null) await SecureStore.deleteItemAsync(clean(key));
    else await SecureStore.setItemAsync(clean(key), value, { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY });
  } catch {
    /* storage is a convenience: without it the person signs in again next time */
  }
}

// ------------------------------------------------------------------ the device key --
// A random secret made on this phone the first time someone signs in with a PIN, and registered with the
// server in that same request. A Step-Up passed by fingerprint or face must carry a proof made with it, so
// the server knows the request comes from this phone and not merely from someone holding a copied token.
export type DeviceKey = { key_id: string; secret: string };
const DEVICE_KEY = "upi-inspector.device.v1";

export const deviceKeySupported = !web;

export async function getDeviceKey(): Promise<DeviceKey | null> {
  if (web) return null;
  try {
    const raw = await SecureStore.getItemAsync(clean(DEVICE_KEY));
    const k = raw ? (JSON.parse(raw) as DeviceKey) : null;
    return k && /^[0-9a-f]{64}$/.test(k.secret) && k.key_id ? k : null;
  } catch {
    return null;
  }
}

/** The key for this phone, made now if there is none. Null where there is no secure place to keep one. */
export async function ensureDeviceKey(): Promise<DeviceKey | null> {
  if (web) return null;
  const have = await getDeviceKey();
  if (have) return have;
  try {
    const key: DeviceKey = {
      key_id: `dk-${bytesToHex(await Crypto.getRandomBytesAsync(10))}`,
      secret: bytesToHex(await Crypto.getRandomBytesAsync(32)),
    };
    await SecureStore.setItemAsync(clean(DEVICE_KEY), JSON.stringify(key), { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY });
    return (await getDeviceKey()) ? key : null;      // only use a key that was really stored
  } catch {
    return null;
  }
}

async function sha256(data: Uint8Array): Promise<Uint8Array> {
  return new Uint8Array(await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, data as any));
}

/** HMAC-SHA256(device secret, challenge id): what the server checks for a Step-Up passed with biometrics. */
export async function deviceProof(key: DeviceKey, challengeId: string): Promise<string> {
  return hmacSha256Hex(key.secret, challengeId, sha256);
}

export function deviceLabel(): string {
  return `${Platform.OS === "ios" ? "iPhone" : Platform.OS === "android" ? "Android phone" : "Device"}`;
}
