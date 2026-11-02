import { lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { adoptFragment } from "./access";
import { App } from "./App";
import { UncaughtErrors } from "./shell/UncaughtErrors";
import "./style.css";

// The kit gallery the screenshot harness opens; no link in the app leads to it.
const Gallery = lazy(() => import("./kit/Gallery").then(({ Gallery }) => ({ default: Gallery })));

const root = createRoot(document.getElementById("root")!);
if (window.location.hash === "#kit-gallery")
  root.render(
    <Suspense>
      <Gallery />
    </Suspense>,
  );
else {
  adoptFragment();
  root.render(<App />);
  // Apart from the app's own root, so an error that unmounts the app still shows its dialog.
  const errors = document.body.appendChild(document.createElement("div"));
  createRoot(errors).render(<UncaughtErrors />);
}
