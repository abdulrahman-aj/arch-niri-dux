{
  description = "Arch Niri Dux ISO development tools";
  # Python 3.14 matches the Arch builder and reads zstd repository databases.
  # QEMU 11.1.2 has an EGL cleanup regression; the stable tools avoid it.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { nixpkgs, ... }: {
    devShells.x86_64-linux.default = let
      pkgs = import nixpkgs { system = "x86_64-linux"; };
    in pkgs.mkShell {
      packages = with pkgs; [ actionlint github-cli jq curl fakeroot git gnumake gnupg gum libarchive pacman python314 ruff shfmt shellcheck qemu xorriso swtpm gptfdisk util-linux squashfsTools tesseract xdg-utils xdg-terminal-exec OVMF.fd ];
      OVMF_DIR = "${pkgs.OVMF.fd}/FV";
      GBM_BACKENDS_PATH = "${pkgs.mesa}/lib/gbm";
      LIBGL_DRIVERS_PATH = "${pkgs.mesa}/lib/dri";
      __EGL_VENDOR_LIBRARY_FILENAMES = "${pkgs.mesa}/share/glvnd/egl_vendor.d/50_mesa.json";
      # QEMU's GTK window links this fontconfig, which warns on a newer host's
      # /etc/fonts; read the host's fonts with nixpkgs' own rules instead.
      FONTCONFIG_FILE = pkgs.writeText "fonts.conf" ''
        <?xml version="1.0"?>
        <!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
        <fontconfig>
          <include>${pkgs.fontconfig.out}/etc/fonts/conf.d</include>
          <dir>/usr/share/fonts</dir>
          <dir>/usr/local/share/fonts</dir>
          <dir prefix="xdg">fonts</dir>
          <cachedir prefix="xdg">fontconfig</cachedir>
        </fontconfig>
      '';
    };
  };
}
