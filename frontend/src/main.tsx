import { createRoot } from "react-dom/client";
import { adoptFragment } from "./access";
import { App } from "./App";
import "./style.css";

adoptFragment();
createRoot(document.getElementById("root")!).render(<App />);
