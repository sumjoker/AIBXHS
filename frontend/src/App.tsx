import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { AuthProvider } from './contexts/AuthContext'
import ProtectedRoute from './components/ProtectedRoute'
import MainLayout from './components/Layout/MainLayout'
import Home from './pages/Home'
import ChatBot from './pages/ChatBot'
import Dashboard from './pages/Dashboard'
import InventoryBot from './pages/InventoryBot'
import ReviewBot from './pages/ReviewBot'
import EmailBot from './pages/EmailBot'
import OrgManagement from './pages/OrgManagement'
import StoreManagement from './pages/StoreManagement'
import ProductManagement from './pages/ProductManagement'
import TenantManagement from './pages/TenantManagement'
import Login from './pages/Login'
import Register from './pages/Register'

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
        path="/inventory" 
        element={
          <ProtectedRoute>
            <MainLayout>
              <InventoryBot />
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
        path="/tenants"
        element={
          <ProtectedRoute>
            <MainLayout>
              <TenantManagement />
            </MainLayout>
          </ProtectedRoute>
        }
      />
    </Routes>
  )
}

function App() {
  return (
    <AuthProvider>
      <Router>
        <AppRoutes />
      </Router>
    </AuthProvider>
  )
}

export default App
