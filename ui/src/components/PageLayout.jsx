// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Menu, Moon, Sun, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import ConsentBanner from "@/components/ConsentBanner";
import { useTheme } from "@/hooks/useTheme";
import { CONSENT_RESET_EVENT, clearConsent } from "@/lib/consent";

function handleReopenConsent(e) {
  e.preventDefault();
  clearConsent();
  globalThis.dispatchEvent(new CustomEvent(CONSENT_RESET_EVENT));
}

export default function PageLayout({ children }) {
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="font-[system-ui,sans-serif] text-[var(--text)] flex flex-col min-h-screen">
      <header className="bg-navy text-white">
        <div className="max-w-[1100px] mx-auto px-4 md:px-8 h-14 flex items-center gap-3">
          <button
            className="flex items-center gap-2 cursor-pointer bg-transparent border-none p-0 text-inherit"
            onClick={() => navigate("/")}
          >
            <img src="/logo.svg" alt="Channel" className="h-7 w-auto" />
            <span className="font-bold text-xl tracking-[1px]">Channel</span>
          </button>

          <div className="flex-1" />

          <a
            href="/docs/"
            className="hidden md:block text-sm text-white/75 no-underline hover:text-white transition-colors"
          >
            Docs
          </a>

          {/* Sign in — visible at every breakpoint */}
          <Button
            variant="nav"
            size="sm"
            className="marketing-signin-btn"
            onClick={() => navigate("/app")}
          >
            Sign in
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={toggle}
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </Button>

          {/* Hamburger — mobile only */}
          <Button
            variant="ghost"
            size="sm"
            className="md:hidden text-white hover:bg-white/10"
            onClick={() => setMenuOpen((v) => !v)}
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </Button>
        </div>

        {/* Mobile drawer */}
        {menuOpen && (
          <div className="md:hidden bg-navy border-t border-white/10">
            <nav className="px-4 py-4 flex flex-col gap-1">
              <a
                href="/docs/"
                className="block px-3 py-3 text-white/85 text-base no-underline hover:text-white hover:bg-white/5 rounded"
                style={{ borderLeft: "2px solid transparent" }}
              >
                Docs
              </a>
            </nav>
          </div>
        )}
      </header>

      {/* Page content */}
      <main className="flex-1">
        {children}
      </main>

      {/* Footer */}
      <footer className="border-t border-[var(--border)]">
        <div className="max-w-[1100px] mx-auto px-4 md:px-8 py-8">
          <div className="flex flex-col gap-6 sm:flex-row sm:justify-between sm:items-start">
            <div className="flex items-center gap-2">
              <img src="/logo.svg" alt="Channel" className="h-5 w-auto opacity-60" />
              <span className="font-bold text-sm tracking-[1px] text-[var(--text-muted)]">Channel</span>
            </div>
            <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm text-[var(--text-muted)]">
              <a href="/docs/" className="no-underline hover:text-[var(--text)] transition-colors">Docs</a>
              <a
                href="#cookie-preferences"
                onClick={handleReopenConsent}
                className="no-underline hover:text-[var(--text)] transition-colors"
              >
                Cookie preferences
              </a>
            </div>
          </div>
          <p className="mt-6 text-[13px] text-[var(--text-muted)]">© 2026 Channel.</p>
        </div>
      </footer>
      <ConsentBanner />
    </div>
  );
}
