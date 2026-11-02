import { createRoot } from "react-dom/client";
import { adoptFragment } from "./access";
import { App } from "./App";
import { UncaughtErrors } from "./shell/UncaughtErrors";
import "./style.css";

adoptFragment();
createRoot(document.getElementById("root")!).render(<App />);
// Apart from the app's own root, so an error that unmounts the app still shows its dialog.
const errors = document.body.appendChild(document.createElement("div"));
createRoot(errors).render(<UncaughtErrors />);
