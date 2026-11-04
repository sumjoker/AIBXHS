import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider } from "./contexts/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";
import MainLayout from "./components/Layout/MainLayout";
import ErrorBoundary from "./components/ErrorBoundary";
import Home from "./pages/Home";
import ChatBot from "./pages/ChatBot";
import Dashboard from "./pages/Dashboard";
import InventoryBot from "./pages/InventoryBot";
import BusinessSettings from "./pages/BusinessSettings";
import ReviewBot from "./pages/ReviewBot";
import EmailBot from "./pages/EmailBot";
import AdBotPage from "./pages/AdBot";
import DataAlertBot from "./pages/DataAlertBot";
import OrgManagement from "./pages/OrgManagement";
import StoreManagement from "./pages/StoreManagement";
import ProductManagement from "./pages/ProductManagement";
import InboundManagement from './pages/InboundManagement'
import OutboundManagement from './pages/OutboundManagement'
import PurchaseManagement from './pages/PurchaseManagement'
import ReplenishmentManagement from './pages/ReplenishmentManagement'
import OperationLogs from './pages/OperationLogs'
import StockTransferManagement from './pages/StockTransferManagement'
import ShipmentManagement from './pages/ShipmentManagement'
import SupplierManagement from './pages/SupplierManagement'
import WarehouseManagement from './pages/WarehouseManagement'
import BaseTableManagement from './pages/BaseTableManagement'
import TenantManagement from "./pages/TenantManagement";
import PermissionManagement from "./pages/PermissionManagement";
import AllocateShipmentTest from "./pages/Inventory/AllocateShipmentTest";
import ProductSelection from "./pages/ProductSelection";
import ProductDevelopmentBot from "./pages/ProductDevelopmentBot";
import ProductDevelopmentCreate from "./pages/ProductDevelopmentCreate";
import AICreationCenter from './pages/AICreationCenter';
import UpscaleCenter from './pages/UpscaleCenter';
import AIImageStudio from './pages/AIImageStudio';
import VideoBindingReview from './pages/VideoBindingReview';
import Login from "./pages/Login";
import Register from "./pages/Register";

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <MainLayout>
              <Dashboard />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/todo"
        element={
          <ProtectedRoute>
            <MainLayout>
              <Home />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/chat"
        element={
          <ProtectedRoute>
            <MainLayout>
              <ChatBot />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <MainLayout>
              <Dashboard />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/inventory"
        element={
          <ProtectedRoute>
            <MainLayout>
              <InventoryBot />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      {/* 标签管理页已下线（后端 /api/tags/* 逻辑保留）；旧链接回落到库存机器人 */}
      <Route path="/tags" element={<Navigate to="/inventory" replace />} />
      <Route
        path="/business-settings"
        element={
          <ProtectedRoute>
            <MainLayout>
              <BusinessSettings />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/review"
        element={
          <ProtectedRoute>
            <MainLayout>
              <ReviewBot />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/email"
        element={
          <ProtectedRoute>
            <MainLayout>
              <EmailBot />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/rating-optimization"
        element={
          <ProtectedRoute>
            <MainLayout>
              {/* 页面优化机器人由 KeepAliveHost 常驻缓存渲染，切换路由不卸载 */}
              {null}
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/data-alert"
        element={
          <ProtectedRoute>
            <MainLayout>
              <DataAlertBot />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/org"
        element={
          <ProtectedRoute>
            <MainLayout>
              <OrgManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/stores"
        element={
          <ProtectedRoute>
            <MainLayout>
              <StoreManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/products"
        element={
          <ProtectedRoute>
            <MainLayout>
              <ProductManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/inbound"
        element={
          <ProtectedRoute>
            <MainLayout>
              <InboundManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/outbound"
        element={
          <ProtectedRoute>
            <MainLayout>
              <OutboundManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/purchase"
        element={
          <ProtectedRoute>
            <MainLayout>
              <PurchaseManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/replenishment"
        element={
          <ProtectedRoute>
            <MainLayout>
              <ReplenishmentManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/operation-logs"
        element={
          <ProtectedRoute>
            <MainLayout>
              <OperationLogs />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/stock-transfer"
        element={
          <ProtectedRoute>
            <MainLayout>
              <StockTransferManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/shipment"
        element={
          <ProtectedRoute>
            <MainLayout>
              <ShipmentManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/suppliers"
        element={
          <ProtectedRoute>
            <MainLayout>
              <SupplierManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/warehouses"
        element={
          <ProtectedRoute>
            <MainLayout>
              <WarehouseManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/base-table"
        element={
          <ProtectedRoute>
            <MainLayout>
              <BaseTableManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/tenants"
        element={
          <ProtectedRoute>
            <MainLayout>
              <TenantManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/product-selection"
        element={
          <ProtectedRoute>
            <MainLayout>
              <ProductSelection />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/development/new"
        element={
          <ProtectedRoute>
            <MainLayout>
              <ProductDevelopmentCreate />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/development"
        element={
          <ProtectedRoute>
            <MainLayout>
              <ProductDevelopmentBot />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/permissions"
        element={
          <ProtectedRoute>
            <MainLayout>
              <PermissionManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/inventory/allocate-test"
        element={
          <ProtectedRoute>
            <MainLayout>
              <AllocateShipmentTest />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/ai-creation"
        element={
          <ProtectedRoute>
            <MainLayout>
              <AICreationCenter />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/upscale-center"
        element={
          <ProtectedRoute>
            <MainLayout>
              <UpscaleCenter />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/ai-image"
        element={
          <ProtectedRoute>
            <MainLayout>
              <AIImageStudio />
            </MainLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/video-binding-review"
        element={
          <ProtectedRoute>
            <MainLayout>
              <VideoBindingReview />
            </MainLayout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

/** 根级错误边界：捕获Content之外的渲染错误（如布局本身），路由变化时自动重置 */
function RootErrorBoundary({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  return <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>;
}

function App() {
  return (
    <AuthProvider>
      <Router>
        <RootErrorBoundary>
          <AppRoutes />
        </RootErrorBoundary>
      </Router>
    </AuthProvider>
  );
}

export default App;
