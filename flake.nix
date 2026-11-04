{
  description = "Arch Niri Dux ISO development tools";
  # QEMU 11.1.2 has an EGL cleanup regression; the stable tools avoid it.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { nixpkgs, ... }: {
    devShells.x86_64-linux.default = let
      pkgs = import nixpkgs { system = "x86_64-linux"; };
    in pkgs.mkShell {
      packages = with pkgs; [ actionlint curl fakeroot git gnumake gnupg libarchive pacman python3 qemu xorriso swtpm gptfdisk util-linux squashfsTools tesseract xdg-utils xdg-terminal-exec OVMF.fd ];
      OVMF_DIR = "${pkgs.OVMF.fd}/FV";
      GBM_BACKENDS_PATH = "${pkgs.mesa}/lib/gbm";
      LIBGL_DRIVERS_PATH = "${pkgs.mesa}/lib/dri";
      __EGL_VENDOR_LIBRARY_FILENAMES = "${pkgs.mesa}/share/glvnd/egl_vendor.d/50_mesa.json";
    };
  };
}
