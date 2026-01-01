import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TürkAnime GUI",
  description: "Anime keşif ve takip uygulaması",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="tr">
      <body
        className="antialiased"
      >
        {children}
      </body>
    </html>
  );
}
