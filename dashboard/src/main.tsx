import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { isLive, loadLive } from "./data/load";
import "./dashboard.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");
createRoot(root).render(<StrictMode>{isLive(window.location.search) ? <App load={loadLive} live /> : <App />}</StrictMode>);
