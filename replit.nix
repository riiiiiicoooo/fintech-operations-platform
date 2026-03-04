{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.postgresql
    pkgs.poetry
    pkgs.git
    pkgs.nodejs_20
  ];
  env = {
    PYTHONBIN = "${pkgs.python311}/bin/python3";
    LANG = "en_US.UTF-8";
  };
}
