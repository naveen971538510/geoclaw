import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AuthGate } from "./components/AuthGate.jsx";
import { AppShell } from "./components/AppShell.jsx";
import { DashboardPage } from "./pages/DashboardPage.jsx";
import { PricesPage } from "./pages/PricesPage.jsx";
import { SignalsPage } from "./pages/SignalsPage.jsx";
import { SubscribePage } from "./pages/SubscribePage.jsx";

function RoutedDashboard() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/prices" element={<PricesPage />} />
        <Route path="/signals" element={<SignalsPage />} />
        <Route path="/subscribe" element={<SubscribePage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppShell>
  );
}

export default function App() {
  return (
    <AuthGate>
      <RoutedDashboard />
    </AuthGate>
  );
}
