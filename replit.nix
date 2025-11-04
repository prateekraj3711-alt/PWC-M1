{ pkgs }: {
  deps = [
    pkgs.nodejs_20
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.tesseract4
    pkgs.poppler_utils
    pkgs.glib
    pkgs.nss
    pkgs.cairo
    pkgs.pango
    pkgs.gtk3
    pkgs.libX11
    pkgs.libXcomposite
    pkgs.libXdamage
    pkgs.libXext
    pkgs.libXfixes
    pkgs.libXrandr
    pkgs.gbm
    pkgs.atk
    pkgs.at_spi2_core
  ];
}


