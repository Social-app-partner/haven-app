import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { Outlet, useLocation } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { Toaster } from 'sonner';
import Sidebar from '@/components/Sidebar';
import Topbar from '@/components/Topbar';
import CrisisModal from '@/components/CrisisModal';
import CheckInModal from '@/components/CheckInModal';
import type { MoodValue } from '@/lib/moodStore';

interface HavenUi {
  openCrisis: (opts?: { fromCareCard?: boolean }) => void;
  openCheckIn: (opts?: { initialMood?: MoodValue; date?: string }) => void;
}

const HavenUiContext = createContext<HavenUi>({
  openCrisis: () => {},
  openCheckIn: () => {},
});

export function useHavenUi() {
  return useContext(HavenUiContext);
}

export default function Layout() {
  const location = useLocation();
  const [crisisOpen, setCrisisOpen] = useState(false);
  const [crisisFromCare, setCrisisFromCare] = useState(false);
  const [checkInOpen, setCheckInOpen] = useState(false);
  const [checkInOpts, setCheckInOpts] = useState<{ initialMood?: MoodValue; date?: string }>({});

  const openCrisis = useCallback((opts?: { fromCareCard?: boolean }) => {
    setCrisisFromCare(opts?.fromCareCard ?? false);
    setCrisisOpen(true);
  }, []);

  const openCheckIn = useCallback((opts?: { initialMood?: MoodValue; date?: string }) => {
    setCheckInOpts(opts ?? {});
    setCheckInOpen(true);
  }, []);

  const ui = useMemo(() => ({ openCrisis, openCheckIn }), [openCrisis, openCheckIn]);

  return (
    <HavenUiContext.Provider value={ui}>
      <div className="min-h-[100dvh] bg-haven-canvas">
        <Sidebar />
        <div className="ml-[248px] flex min-h-[100dvh] flex-col">
          <Topbar />
          <main className="flex-1 px-8 py-8">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={location.pathname}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
              >
                <Outlet />
              </motion.div>
            </AnimatePresence>
          </main>
        </div>

        <CrisisModal open={crisisOpen} onOpenChange={setCrisisOpen} fromCareCard={crisisFromCare} />
        <CheckInModal
          open={checkInOpen}
          onOpenChange={setCheckInOpen}
          initialMood={checkInOpts.initialMood}
          date={checkInOpts.date}
        />
        <Toaster position="bottom-center" toastOptions={{ style: { borderRadius: '999px' } }} />
      </div>
    </HavenUiContext.Provider>
  );
}
