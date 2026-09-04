import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { Playground } from "./pages/Playground";
import { Requests } from "./pages/Requests";
import { Insights } from "./pages/Insights";
import { Admin } from "./pages/Admin";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/playground" element={<Playground />} />
        <Route path="/requests" element={<Requests />} />
        <Route path="/insights" element={<Insights />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Layout>
  );
}
