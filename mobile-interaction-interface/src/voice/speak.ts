// Alfred's replies read aloud by the browser (speechSynthesis), in the language of the reply.
// Piper (it has pt-PT voices) is the open alternative on the server side.

export function canSpeak(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function speak(text: string, lang: string | null): void {
  if (!canSpeak() || !text.trim()) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang === "pt" ? "pt-PT" : "en-GB";
  window.speechSynthesis.speak(u);
}

export function stopSpeaking(): void {
  if (canSpeak()) window.speechSynthesis.cancel();
}
