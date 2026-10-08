// Spoken warnings (F-21). On-device text-to-speech reads the family headline and body in the payer's language.
import * as Speech from "expo-speech";

export function speak(text: string, locale: string) {
  try {
    Speech.stop();
    if (!text) return;
    Speech.speak(text, { language: locale === "hi" ? "hi-IN" : "en-IN", rate: 0.92, pitch: 1.0 });
  } catch {
    /* a device without a voice still shows the text */
  }
}

export function stopSpeaking() {
  try {
    Speech.stop();
  } catch {
    /* ignore */
  }
}
