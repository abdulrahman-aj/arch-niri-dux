{
  description = "Arch Niri Dux ISO development tools";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { nixpkgs, ... }: {
    devShells.x86_64-linux.default = let
      pkgs = import nixpkgs { system = "x86_64-linux"; };
    in pkgs.mkShell {
      packages = with pkgs; [ curl gnumake gnupg libarchive python3 qemu util-linux squashfsTools OVMF.fd ];
      OVMF_DIR = "${pkgs.OVMF.fd}/FV";
    };
  };
}
