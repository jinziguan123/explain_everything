import { NavLink, Route, Routes } from 'react-router-dom'
import Workspace from './pages/Workspace'
import './App.css'

function KnowledgePage() {
  return <div className="page-placeholder">全局知识 (Phase C)</div>
}

function App() {
  return (
    <div className="app">
      <nav className="app-nav">
        <span className="app-brand">Explain Everything</span>
        <NavLink to="/" end className="app-nav-link">
          工作台
        </NavLink>
        <NavLink to="/knowledge" className="app-nav-link">
          全局知识
        </NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<Workspace />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
      </Routes>
    </div>
  )
}

export default App
