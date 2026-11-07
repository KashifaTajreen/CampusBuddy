import type { Metadata, Viewport } from "next";
import { PwaControls } from "@/components/pwa-controls";
import "./globals.css";
export const metadata: Metadata = {
  title: "CampusOS · Your campus, connected",
  description:
    "Your classes, campus knowledge and opportunities in one secure workspace.",
  appleWebApp: { capable: true, title: "CampusOS", statusBarStyle: "default" },
  icons: { icon: "/icons/icon-192.png", apple: "/icons/apple-touch-icon.png" },
};
export const viewport: Viewport = { themeColor: "#4338ca", width: "device-width", initialScale: 1 };
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body><PwaControls />{children}</body>
    </html>
  );
}
