{ pkgs }: {
  deps = [
    # Node.js and npm
    pkgs.nodejs_20
    pkgs.nodePackages.npm
    
    # Python 3.11 with pip
    pkgs.python311
    pkgs.python311Packages.pip
    
    # PDF processing (for pdf2image)
    pkgs.poppler_utils
    
    # OCR (for pytesseract)
    pkgs.tesseract4
    
    # Playwright browser dependencies
    pkgs.chromium
    pkgs.glib
    pkgs.nspr
    pkgs.nss
    pkgs.dbus
    pkgs.at-spi2-core
    pkgs.cups
    pkgs.gtk3
    pkgs.pango
    pkgs.cairo
    pkgs.libdrm
    pkgs.mesa
    pkgs.alsaLib
    pkgs.libxkbcommon
    
    # X11 libraries (required for headless browser)
    pkgs.xorg.libX11
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXdamage
    pkgs.xorg.libXext
    pkgs.xorg.libXfixes
    pkgs.xorg.libXrandr
    pkgs.xorg.libxcb
    pkgs.gbm
    pkgs.atk
  ];
}
