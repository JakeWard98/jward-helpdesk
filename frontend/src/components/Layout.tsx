import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

export default function Layout() {
  const { user, signOut } = useAuth();
  const isAdmin = user?.role === 'admin';

  return (
    <div className="app-shell">
      <header className="app-bar">
        <NavLink to="/tickets" className="brand">
          Helpdesk
        </NavLink>

        <nav className="app-nav">
          <NavLink to="/tickets" className={({ isActive }) => (isActive ? 'nav-active' : '')}>
            Tickets
          </NavLink>
          <NavLink to="/tickets/new" className={({ isActive }) => (isActive ? 'nav-active' : '')}>
            New ticket
          </NavLink>
          {isAdmin ? (
            <NavLink to="/admin" className={({ isActive }) => (isActive ? 'nav-active' : '')}>
              Admin
            </NavLink>
          ) : null}
        </nav>

        <div className="app-user">
          <NavLink to="/account" className="user-chip">
            {user?.display_name || user?.email}
          </NavLink>
          <button className="btn btn-link" type="button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
