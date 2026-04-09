import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App.jsx";

export function mountDashboardApp(target = document.getElementById("root")) {
  if (!target) {
    throw new Error("Dashboard root element was not found.");
  }

  createRoot(target).render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>,
  );
}
