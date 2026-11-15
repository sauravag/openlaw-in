import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenLaw.in",
  description:
    "Free, cited answers to questions about Indian law, grounded in government sources.",
};

/**
 * `interactiveWidget: "resizes-content"`: when the soft keyboard opens, the
 * layout viewport shrinks with it instead of the browser panning the page.
 * The chat is an `h-dvh` column with the composer pinned at the bottom, so
 * this keeps the composer above the keyboard AND keeps the top of the
 * message list -- where a reply's crisis box is scrolled to -- on screen,
 * rather than panned away above the visible area (chat UI review, I1).
 * `width`/`initialScale` restate Next's defaults, which an explicit
 * viewport export would otherwise not carry.
 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  interactiveWidget: "resizes-content",
};

// No custom web font: the primary user is on a cheap Android phone, possibly
// on a slow connection (see the project brief). The system font stack
// (`globals.css`) renders instantly with zero extra bytes -- worth more here
// than the visual polish a downloaded font would add.
//
// `lang="en"` is only the starting value for the server-rendered shell;
// `app/page.tsx` sets `document.documentElement.lang` to the selected
// language so a screen reader pronounces Hindi content with a Hindi voice
// (Task 6 review, H3).
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col bg-white text-ink">{children}</body>
    </html>
  );
}
