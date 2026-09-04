import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Overview from "./pages/Overview";
import LiveCase from "./pages/LiveCase";
import Optimizer from "./pages/Optimizer";
import Explainability from "./pages/Explainability";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="cases" element={<LiveCase />} />
          <Route path="optimizer" element={<Optimizer />} />
          <Route path="explainability" element={<Explainability />} />
          <Route path="approvals" element={<Approvals />} />
          <Route path="audit" element={<Audit />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
