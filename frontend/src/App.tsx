import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { LayoutDashboard, List, BarChart3, Upload, PieChart, Trophy } from 'lucide-react';
import Dashboard from './pages/Dashboard';
import Trades from './pages/Trades';
import TradeDetail from './pages/TradeDetail';
import TopTrades from './pages/TopTrades';
import Statistics from './pages/Statistics';
import Import from './pages/Import';
import ErrorBoundary from './components/ErrorBoundary';

const navItems = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/trades', icon: List, label: 'Trades' },
  { to: '/top-trades', icon: Trophy, label: 'Top / Worst' },
  { to: '/statistics', icon: PieChart, label: 'Statistics' },
  { to: '/import', icon: Upload, label: 'Import' },
];

function App() {
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <nav className="w-56 shrink-0 bg-surface-2 border-r border-border flex flex-col">
        <div className="p-4 border-b border-border">
          <h1 className="text-lg font-semibold flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-accent" />
            Trading Journal
          </h1>
        </div>
        <div className="flex-1 p-2 space-y-0.5">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-accent/15 text-accent-2'
                    : 'text-text-2 hover:text-text hover:bg-surface-3'
                }`
              }
            >
              <item.icon className="w-4 h-4" />
              {item.label}
            </NavLink>
          ))}
        </div>
        <div className="p-3 border-t border-border text-xs text-text-3">
          Sierra Charts Journal v0.1
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/trades/:id" element={<TradeDetail />} />
            <Route path="/top-trades" element={<TopTrades />} />
            <Route path="/statistics" element={<Statistics />} />
            <Route path="/import" element={<Import />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default App;
