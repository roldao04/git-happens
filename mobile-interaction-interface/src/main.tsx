import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { HttpHomeApi } from "./api/http";
import { MockHomeApi } from "./api/mock/MockHomeApi";
import { homeKey, makeApi, modeFrom } from "./api/select";
import "./app.css";

const mode = modeFrom(import.meta.env as { VITE_ALFRED_MODE?: string });
const api = makeApi(mode);
const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

void (async () => {
  // The microphone is this laptop's voice model (npm run setup said yes): the loop's API serves it for your
  // home, and npm run demo starts a voice-only server for the demo house. No model: no microphone.
  const voice = api instanceof HttpHomeApi ? api : new HttpHomeApi("./api");
  await Promise.race([voice.checkVoice(), new Promise((r) => setTimeout(r, 2000))]);
  if (api instanceof MockHomeApi) api.transcribe = voice.transcribe;
  createRoot(root).render(
    <StrictMode>
      <App api={api} homeKey={homeKey(mode)} />
    </StrictMode>,
  );
})();
