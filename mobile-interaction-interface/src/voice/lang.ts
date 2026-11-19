// Portuguese or English, from the words themselves. Parakeet recognises either without being told which
// (it has no language setting), so this only decides which language Alfred answers and speaks in on Auto.

const PT = new Set(["o", "os", "as", "do", "da", "dos", "das", "no", "na", "nos", "nas", "um", "uma", "que", "não", "nao", "é", "está", "esta", "estão",
  "para", "por", "com", "se", "eu", "tu", "você", "voce", "meu", "minha", "sim", "obrigado", "obrigada", "olá", "ola", "bom", "boa", "dia", "noite",
  "liga", "ligar", "desliga", "desligar", "acende", "apaga", "luz", "candeeiro", "casa", "chaleira", "tomada", "aquecimento", "graus", "porta",
  "alguém", "alguem", "ninguém", "ninguem", "quem", "porquê", "porque", "quando", "hoje", "amanhã", "agora", "mais", "menos", "tudo", "favor"]);
const EN = new Set(["the", "is", "are", "was", "to", "on", "off", "in", "of", "and", "it", "you", "your", "my", "me", "i", "please", "thanks", "thank",
  "turn", "switch", "set", "dim", "light", "lamp", "home", "house", "heating", "degrees", "door", "anyone", "someone", "nobody", "what", "why",
  "how", "when", "who", "today", "tomorrow", "now", "skip", "undo", "everything", "plan", "kettle", "plug", "good", "morning", "evening"]);

export function detectLang(text: string): "pt" | "en" | null {
  const lower = text.toLowerCase();
  let pt = (lower.match(/[ãõçâêôáéíóúà]/g) ?? []).length;
  let en = 0;
  for (const w of lower.split(/[^a-zãõçâêôáéíóúà']+/).filter(Boolean)) {
    if (PT.has(w)) pt += 1;
    if (EN.has(w)) en += 1;
  }
  if (pt === en) return null;
  return pt > en ? "pt" : "en";
}
