"use client";
import { useEffect, useState } from "react";
import { Download, WifiOff } from "lucide-react";
interface InstallEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}
export function PwaControls() {
  const [offline, setOffline] = useState(false);
  const [install, setInstall] = useState<InstallEvent | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const connection = () => setOffline(!navigator.onLine);
    const available = (event: Event) => { event.preventDefault(); setInstall(event as InstallEvent); };
    const installed = () => setInstall(null);
    connection();
    window.addEventListener("online", connection);
    window.addEventListener("offline", connection);
    window.addEventListener("beforeinstallprompt", available);
    window.addEventListener("appinstalled", installed);
    if ("serviceWorker" in navigator && window.isSecureContext) {
      void navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" })
        .catch(() => setError("Offline help is unavailable. You can still use CampusOS online."));
    }
    return () => {
      window.removeEventListener("online", connection);
      window.removeEventListener("offline", connection);
      window.removeEventListener("beforeinstallprompt", available);
      window.removeEventListener("appinstalled", installed);
    };
  }, []);
  return <>
    {offline && <div className="offline-status" role="status"><WifiOff size={16} />
      You’re offline. Reconnect for current campus information. Answers are not saved for offline use.
    </div>}
    {error && <div className="pwa-status" role="status">{error}</div>}
    {install && <button className="install-app" onClick={async () => {
      try { await install.prompt(); await install.userChoice; setInstall(null); }
      catch { setError("Use your browser menu to install CampusOS, if supported."); }
    }}><Download size={16} />Install app</button>}
  </>;
}
