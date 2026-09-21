import { Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { useAuth } from './auth/AuthContext';
import { Spinner } from './components/ui';
import Account from './pages/Account';
import Admin from './pages/Admin';
import ChangePassword from './pages/ChangePassword';
import MfaChallenge from './pages/MfaChallenge';
import MfaEnrol from './pages/MfaEnrol';
import NewTicket from './pages/NewTicket';
import SignIn from './pages/SignIn';
import TicketDetail from './pages/TicketDetail';
import TicketList from './pages/TicketList';

export default function App() {
  const { stage, user } = useAuth();

  // The sign-in stages are gates, not routes: until each is satisfied the rest
  // of the app is not rendered at all, and the API would refuse it anyway.
  if (stage === 'loading') return <Spinner label="Starting…" />;
  if (stage === 'signed-out') return <SignIn />;
  if (stage === 'mfa_required') return <MfaChallenge />;
  if (stage === 'mfa_enrolment_required') return <MfaEnrol />;
  if (user?.must_change_password) return <ChangePassword />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/tickets" element={<TicketList />} />
        <Route path="/tickets/new" element={<NewTicket />} />
        <Route path="/tickets/:ticketId" element={<TicketDetail />} />
        <Route path="/account" element={<Account />} />
        {user?.role === 'admin' ? <Route path="/admin" element={<Admin />} /> : null}
        <Route path="*" element={<Navigate to="/tickets" replace />} />
      </Route>
    </Routes>
  );
}
