{ pkgs }: {
  deps = [
    pkgs.nodejs-18_x
    pkgs.python311
    pkgs.tesseract
    pkgs.poppler_utils
  ];
}
