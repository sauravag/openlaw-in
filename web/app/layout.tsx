import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenLaw.in",
  description:
    "Free, cited answers to questions about Indian law, grounded in government sources.",
};

// No custom web font: the primary user is on a cheap Android phone, possibly
// on a slow connection (see the project brief). The system font stack
// (`globals.css`) renders instantly with zero extra bytes -- worth more here
// than the visual polish a downloaded font would add.
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
