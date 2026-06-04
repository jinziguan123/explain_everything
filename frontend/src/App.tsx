import { NavLink, Route, Routes } from 'react-router-dom'
import Workspace from './pages/Workspace'
import Knowledge from './pages/Knowledge'
import './App.css'

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
        <Route path="/knowledge" element={<Knowledge />} />
      </Routes>
    </div>
  )
}

export default App
